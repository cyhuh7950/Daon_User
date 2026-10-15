"""호출별 고정 child·정확 wire·현재 권한 callback·종료 회수."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime,timezone

from .open_law_client import (OpenLawError,OpenLawSelection,decode_document,exact,
                              validate_search,validate_search_result)
from .open_law_mcp_server import DESCRIPTION,MAX_FRAME,PROTOCOL,SAFE_CODES,SCHEMA_DIGEST,TOOLS,wire_json
from .safe_http_fetch import resolve_public_ips, validate_public_ips, WebsiteFetchError

_SLOTS=threading.BoundedSemaphore(2)


class OpenLawMcpClient:
    def __init__(self,*,api_key=None,enabled=False,qa_workspace_id='',authorize_context=None,timeout_seconds=10,resolver=None):
        if not isinstance(timeout_seconds,(int,float)) or isinstance(timeout_seconds,bool) or not 0<timeout_seconds<=10:
            raise OpenLawError('OPEN_LAW_INPUT_INVALID')
        self._key=api_key;self._enabled=enabled;self._qa=qa_workspace_id
        self._authorize=authorize_context;self._timeout=timeout_seconds
        self._resolver=resolver or resolve_public_ips

    def _check(self,context,fingerprint=None):
        if self._enabled is not True or not self._qa or context.workspace_id!=self._qa:
            raise OpenLawError('OPEN_LAW_ACCESS_DENIED')
        if not all(isinstance(getattr(context,k,None),str) and getattr(context,k) for k in
                   ('tenant_id','workspace_id','notebook_id','actor_id','trace_id','policy_version')):
            raise OpenLawError('OPEN_LAW_ACCESS_DENIED')
        if not isinstance(self._key,str) or not self._key:raise OpenLawError('OPEN_LAW_CREDENTIAL_REQUIRED')
        try:value=self._authorize(context) if callable(self._authorize) else None
        except Exception:raise OpenLawError('OPEN_LAW_EGRESS_BLOCKED') from None
        if not isinstance(value,str) or not value or fingerprint is not None and fingerprint!=value:
            raise OpenLawError('OPEN_LAW_EGRESS_BLOCKED')
        return value

    @staticmethod
    def _reap(child):
        if child.stdin:
            try:child.stdin.close()
            except OSError:pass
        try:child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:child.wait(timeout=1)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        for pipe in (child.stdout,child.stderr):
            if pipe:pipe.close()

    def _call(self,context,tool,arguments):
        frozen=self._check(context)
        if not _SLOTS.acquire(blocking=False):raise OpenLawError('OPEN_LAW_BUSY')
        child=None; reader=None
        try:
            deadline=time.monotonic()+self._timeout
            # DNS runs under its own bounded, reaped parent-owned subprocess
            # before the MCP child exists. MCP kill can never orphan a resolver.
            try:ips=validate_public_ips(self._resolver('www.law.go.kr',deadline))
            except WebsiteFetchError as error:
                raise OpenLawError('OPEN_LAW_TIMEOUT' if str(error)=='WEBSITE_FETCH_TIMEOUT' else 'OPEN_LAW_EGRESS_BLOCKED') from None
            self._check(context,frozen)
            # No inherited Provider/DB/credential environment. Python module
            # location is server-owned; OC is never an argv/log/file value.
            env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','PATH') if k in os.environ}
            env.update({'PYTHONPATH':str(Path(__file__).resolve().parent.parent),
                'PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8',
                'DAON_OPEN_LAW_API_KEY':self._key,'DAON_OPEN_LAW_DEADLINE':str(deadline)})
            env['DAON_OPEN_LAW_APPROVED_IPS']=json.dumps(ips)
            child=subprocess.Popen([sys.executable,'-B','-m','daon_user_api.open_law_mcp_server'],shell=False,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),bufsize=0)
            messages=queue.Queue(maxsize=1)
            def receive():
                try:
                    # One bounded frame per response. No notification stream
                    # or unbounded read/communicate allocation is permitted.
                    for _ in range(4):
                        raw=bytearray()
                        while len(raw)<=MAX_FRAME:
                            part=child.stdout.read(min(4096,MAX_FRAME+1-len(raw)))
                            if not part:break
                            raw.extend(part)
                            if b'\n' in raw:break
                        if len(raw)>MAX_FRAME or not raw.endswith(b'\n') or raw.count(b'\n')!=1:
                            raise ValueError
                        messages.put_nowait(bytes(raw))
                except Exception:
                    try:messages.put_nowait(None)
                    except queue.Full:pass
            reader=threading.Thread(target=receive,daemon=True);reader.start()
            def send(message):
                raw=json.dumps(message,separators=(',',':'),ensure_ascii=False).encode()+b'\n'
                if len(raw)>16384:raise OpenLawError('OPEN_LAW_INPUT_INVALID')
                child.stdin.write(raw);child.stdin.flush()
            def exchange(request_id,method,params,limit=None):
                send({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
                try:raw=messages.get(timeout=max(0,min(deadline-time.monotonic(),limit or 10)))
                except queue.Empty:raise OpenLawError('OPEN_LAW_TIMEOUT') from None
                if time.monotonic()>deadline:raise OpenLawError('OPEN_LAW_TIMEOUT')
                try:
                    if raw is None:raise ValueError
                    value=wire_json(raw)
                    if not exact(value,{'jsonrpc','id','result'}) or value['jsonrpc']!='2.0' or type(value['id']) is not int or value['id']!=request_id:raise ValueError
                    return value['result']
                except ValueError:raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID') from None
            initialized=exchange(1,'initialize',{'protocolVersion':PROTOCOL,'capabilities':{},'clientInfo':{'name':'daon-open-law','version':'1'}},2)
            if initialized!={'protocolVersion':PROTOCOL,'capabilities':{'tools':{}},'serverInfo':{'name':'daon-open-law','version':'1'}}:
                raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
            send({'jsonrpc':'2.0','method':'notifications/initialized'})
            if exchange(2,'tools/list',{})!={'tools':TOOLS}:raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
            self._check(context,frozen)
            result=exchange(3,'tools/call',{'name':tool,'arguments':arguments})
            if exact(result,{'content','isError'}) and result['isError'] is True:
                content=result['content'];code=content[0].get('text') if isinstance(content,list) and len(content)==1 and isinstance(content[0],dict) else None
                raise OpenLawError(code if code in SAFE_CODES else 'OPEN_LAW_PROTOCOL_INVALID')
            if (not exact(result,{'content','structuredContent','isError'}) or result['isError'] is not False
                    or result['content']!=[{'type':'text','text':DESCRIPTION}]):raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
            self._check(context,frozen)
            return result['structuredContent']
        except OpenLawError:raise
        except Exception:raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID') from None
        finally:
            try:
                if child is not None:self._reap(child)
                if reader is not None:reader.join(timeout=1)
            finally:_SLOTS.release()

    def search(self,context,query,page):
        validate_search(query,page)
        return validate_search_result(self._call(context,'search_law',{'query':query,'page':page}),page)

    def read(self,context,selection):
        if not isinstance(selection,OpenLawSelection):raise OpenLawError('OPEN_LAW_INPUT_INVALID')
        document=decode_document(self._call(context,'get_law_text',{'mst':selection.mst,
            'effective_date':selection.effective_date,'article_code':selection.article_code}))
        if any(document.law[k]!=getattr(selection,k) for k in ('mst','effective_date','article_code')):
            raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
        return document

    def probe(self,context):
        self.search(context,'민법',1)
        return {'protocol_version':PROTOCOL,'tool_schema_digest':SCHEMA_DIGEST,
            'checked_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
