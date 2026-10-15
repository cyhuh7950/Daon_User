"""고정 stdio MCP 2025-11-25 서버. stdout은 JSON-RPC 프레임 전용."""
from __future__ import annotations

import json
import os
import re
import sys
import time

from .open_law_client import (OpenLawClient,OpenLawError,OpenLawSelection,encode_document,exact)

PROTOCOL='2025-11-25'
MAX_FRAME=8388608
DESCRIPTION='Open law read result'


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


STRING={'type':'string'}
ID={'type':'string','pattern':'^[0-9]{1,20}$'}
DATE={'type':'string','pattern':'^[0-9]{8}$'}
LAW={'mst':ID,'law_id':ID,'title':{'type':'string','minLength':1,'maxLength':300},'effective_date':DATE,
    'promulgation_date':{'anyOf':[DATE,{'type':'null'}]},'ministry':{'type':['string','null'],'maxLength':200}}
SELECTION=obj({'mst':ID,'effective_date':DATE,'article_code':{'anyOf':[{'type':'string','pattern':'^[0-9]{6}$'},{'type':'null'}]}})
TOOLS=[{'name':'search_law','description':'Search current public law titles',
    'inputSchema':obj({'query':{'type':'string','minLength':1,'maxLength':100},'page':{'type':'integer','minimum':1,'maximum':100}}),
    'outputSchema':obj({'items':{'type':'array','maxItems':20,'items':obj(LAW)},'page':{'type':'integer','minimum':1,'maximum':100},'total_count':{'type':'integer','minimum':0}})},
    {'name':'get_law_text','description':'Read the selected law effective version',
    'inputSchema':SELECTION,'outputSchema':obj({'law':obj({**LAW,'article_code':SELECTION['properties']['article_code']}),
    'raw_base64':{'type':'string','maxLength':2796204},'text':{'type':'string','maxLength':524288},
    'raw_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'},'text_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'},
    'fetched_at':STRING,'extraction_version':{'type':'string','const':'open-law-text-v1'}})}]
SCHEMA_DIGEST=__import__('hashlib').sha256(json.dumps(TOOLS,sort_keys=True,separators=(',',':')).encode()).hexdigest()
SAFE_CODES=frozenset({'OPEN_LAW_INPUT_INVALID','OPEN_LAW_CREDENTIAL_REQUIRED','OPEN_LAW_PROTOCOL_INVALID',
    'OPEN_LAW_UPSTREAM_FAILED','OPEN_LAW_NOT_FOUND','OPEN_LAW_TEXT_UNAVAILABLE','OPEN_LAW_CONTENT_TOO_LARGE',
    'OPEN_LAW_TIMEOUT','OPEN_LAW_EGRESS_BLOCKED','OPEN_LAW_UNAVAILABLE'})


def wire_json(raw):
    from .open_law_client import parse_json
    return parse_json(raw,maximum=MAX_FRAME)


def dispatch(message,client,state):
    request_id=message.get('id') if isinstance(message,dict) else None
    def error():return {'jsonrpc':'2.0','id':request_id,'error':{'code':-32600,'message':'OPEN_LAW_PROTOCOL_INVALID'}}
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0':return error()
    method=message.get('method');params=message.get('params',{})
    if method=='notifications/initialized':
        if not exact(message,{'jsonrpc','method'}) or state.get('phase')!='initialize':return error()
        state['phase']='ready';return None
    if type(request_id) is not int or not exact(message,{'jsonrpc','id','method','params'}):return error()
    if method=='initialize':
        if state or not exact(params,{'protocolVersion','capabilities','clientInfo'}) or params['protocolVersion']!=PROTOCOL or params['capabilities']!={}:return error()
        if not exact(params['clientInfo'],{'name','version'}) or params['clientInfo']!={'name':'daon-open-law','version':'1'}:return error()
        state['phase']='initialize'
        result={'protocolVersion':PROTOCOL,'capabilities':{'tools':{}},'serverInfo':{'name':'daon-open-law','version':'1'}}
    elif state.get('phase')!='ready':return error()
    elif method=='tools/list':
        if params!={}:return error()
        state['listed']=True;result={'tools':TOOLS}
    elif method=='tools/call':
        if not state.get('listed') or not exact(params,{'name','arguments'}):return error()
        name=params['name'];arguments=params['arguments']
        try:
            deadline=float(os.environ.get('DAON_OPEN_LAW_DEADLINE',time.monotonic()+8))
            if name=='search_law' and exact(arguments,{'query','page'}):
                value=client.search(arguments['query'],arguments['page'],deadline)
            elif name=='get_law_text' and exact(arguments,{'mst','effective_date','article_code'}):
                value=encode_document(client.read(OpenLawSelection(**arguments),deadline))
            else:return error()
            result={'content':[{'type':'text','text':DESCRIPTION}],'structuredContent':value,'isError':False}
        except Exception as exc:
            code=str(exc) if isinstance(exc,OpenLawError) and str(exc) in SAFE_CODES else 'OPEN_LAW_UPSTREAM_FAILED'
            result={'content':[{'type':'text','text':code}],'isError':True}
    else:return error()
    return {'jsonrpc':'2.0','id':request_id,'result':result}


def serve(client=None):
    if client is None:
        # No DNS subprocess underneath MCP. Parent resolves/validates first;
        # absent/invalid pinned IPs never fall back to a child-side resolver.
        def approved_ips(_host,_deadline):
            from .safe_http_fetch import validate_public_ips
            try:
                values=json.loads(os.environ.get('DAON_OPEN_LAW_APPROVED_IPS','null'))
                if not isinstance(values,list) or len(values)>256:raise ValueError
                return validate_public_ips(tuple(values))
            except Exception:raise OpenLawError('OPEN_LAW_EGRESS_BLOCKED') from None
        raw_limit=os.environ.get('DAON_OPEN_LAW_MAX_RAW_BYTES','2097152')
        if not re.fullmatch('[1-9][0-9]{0,6}',raw_limit):return
        try:client=OpenLawClient(api_key=os.environ.get('DAON_OPEN_LAW_API_KEY'),resolver=approved_ips,max_raw_bytes=int(raw_limit))
        except OpenLawError:return
    state={}
    try:
        for _ in range(8):
            raw=sys.stdin.buffer.readline(MAX_FRAME+1)
            if not raw:return
            if len(raw)>MAX_FRAME or not raw.endswith(b'\n'):return
            result=dispatch(wire_json(raw),client,state)
            if result is not None:
                output=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\n'
                if len(output)>MAX_FRAME:return
                sys.stdout.buffer.write(output);sys.stdout.buffer.flush()
    except Exception:
        # No raw exceptions, search terms, HTTP bodies or credentials on stderr.
        return


if __name__=='__main__':serve()
