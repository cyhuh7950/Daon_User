"""국가법령 두 공식 HTTPS 경로만 읽는 bounded transport와 정규 계약."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from .safe_http_fetch import (MAX_BODY, MAX_TEXT, WebsiteFetchError, _WireReader,
                              checked_headers, remaining, resolve_public_ips, validate_public_ips)

HOST = 'www.law.go.kr'
VERSION = 'open-law-text-v1'
LAW_KEYS = {'mst','law_id','title','effective_date','promulgation_date','ministry','article_code'}


class OpenLawError(ValueError):
    """오류 문자열에는 외부 값/키/URL을 포함하지 않는다."""


def exact(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


def valid_date(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{8}',value): return False
    try: date(int(value[:4]),int(value[4:6]),int(value[6:])); return True
    except ValueError: return False


def valid_id(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9]{1,20}',value) is not None


def valid_string(value, maximum):
    return isinstance(value,str) and 0 < len(value) <= maximum and bool(value.strip()) and not any(ord(c)<32 for c in value)


def validate_search(query,page):
    if not valid_string(query,100) or type(page) is not int or not 1 <= page <= 100:
        raise OpenLawError('OPEN_LAW_INPUT_INVALID')


@dataclass(frozen=True)
class OpenLawSelection:
    mst: str
    effective_date: str
    article_code: str | None

    def __post_init__(self):
        if (not valid_id(self.mst) or not valid_date(self.effective_date)
                or self.article_code is not None and (not isinstance(self.article_code,str)
                    or re.fullmatch(r'[0-9]{6}',self.article_code) is None)):
            raise OpenLawError('OPEN_LAW_INPUT_INVALID')


@dataclass(frozen=True)
class OpenLawDocument:
    law: dict
    raw_content: bytes
    text_content: bytes
    raw_sha256: str
    text_sha256: str
    fetched_at: str
    extraction_version: str


def validate_law(law, article=True):
    keys=LAW_KEYS if article else LAW_KEYS-{'article_code'}
    if (not exact(law,keys) or not valid_id(law['mst']) or not valid_id(law['law_id'])
            or not valid_string(law['title'],300) or not valid_date(law['effective_date'])
            or law['promulgation_date'] is not None and not valid_date(law['promulgation_date'])
            or law['ministry'] is not None and not valid_string(law['ministry'],200)):
        raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
    if article: OpenLawSelection(law['mst'],law['effective_date'],law['article_code'])


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError
        result[key]=value
    return result


def parse_json(raw,*,maximum=MAX_BODY):
    if not isinstance(raw,bytes) or not raw or len(raw)>maximum:
        raise OpenLawError('OPEN_LAW_CONTENT_TOO_LARGE')
    try:
        # Reject nesting before json.loads allocates a deep recursive tree.
        depth=0; quoted=False; escaped=False
        for char in raw:
            if quoted:
                if escaped: escaped=False
                elif char==92: escaped=True
                elif char==34: quoted=False
            elif char==34: quoted=True
            elif char in (91,123):
                depth+=1
                if depth>40: raise ValueError
            elif char in (93,125): depth-=1
        return json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):
        raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID') from None


def validate_search_result(value, expected_page=None):
    if (not exact(value,{'items','page','total_count'}) or not isinstance(value['items'],list)
            or len(value['items'])>20 or type(value['page']) is not int or not 1<=value['page']<=100
            or type(value['total_count']) is not int or value['total_count']<len(value['items'])
            or expected_page is not None and value['page'] != expected_page):
        raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
    for law in value['items']: validate_law(law,False)
    return value


def encode_document(document):
    return {'law':document.law,'raw_base64':base64.b64encode(document.raw_content).decode('ascii'),
        'text':document.text_content.decode('utf-8'),'raw_sha256':document.raw_sha256,
        'text_sha256':document.text_sha256,'fetched_at':document.fetched_at,'extraction_version':document.extraction_version}


def decode_document(value):
    try:
        if not exact(value,{'law','raw_base64','text','raw_sha256','text_sha256','fetched_at','extraction_version'}): raise ValueError
        validate_law(value['law'])
        encoded=value['raw_base64'];text=value['text']
        if not isinstance(encoded,str) or len(encoded)>((MAX_BODY+2)//3)*4 or not isinstance(text,str) or not text.strip() or len(text)>MAX_TEXT: raise ValueError
        raw=base64.b64decode(encoded,validate=True); content=text.encode('utf-8')
        if (not raw or len(raw)>MAX_BODY or base64.b64encode(raw).decode('ascii')!=encoded
                or len(content)>MAX_TEXT or hashlib.sha256(raw).hexdigest()!=value['raw_sha256']
                or hashlib.sha256(content).hexdigest()!=value['text_sha256'] or value['extraction_version']!=VERSION): raise ValueError
        stamp=value['fetched_at']
        if not isinstance(stamp,str) or len(stamp)>40 or datetime.fromisoformat(stamp.replace('Z','+00:00')).utcoffset() is None: raise ValueError
        return OpenLawDocument(value['law'],raw,content,value['raw_sha256'],value['text_sha256'],stamp,VERSION)
    except (ValueError,TypeError,UnicodeError,KeyError):
        raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID') from None


def https_get(path,params,ips,deadline):
    """S13과 분리된 고정 query. DNS 재조회/proxy/redirect/fallback 없음."""
    raw=sock=None
    try:
        if path not in {'/DRF/lawSearch.do','/DRF/lawService.do'}: raise ValueError
        addresses=validate_public_ips(ips); address=ipaddress.ip_address(addresses[0])
        raw=socket.socket(socket.AF_INET6 if address.version==6 else socket.AF_INET,socket.SOCK_STREAM)
        raw.settimeout(remaining(deadline))
        raw.connect((str(address),443,0,0) if address.version==6 else (str(address),443))
        if ipaddress.ip_address(raw.getpeername()[0])!=address: raise ValueError
        tls=ssl.create_default_context()
        if not tls.check_hostname or tls.verify_mode!=ssl.CERT_REQUIRED: raise ValueError
        raw.settimeout(remaining(deadline));sock=tls.wrap_socket(raw,server_hostname=HOST)
        if ipaddress.ip_address(sock.getpeername()[0])!=address: raise ValueError
        sock.settimeout(remaining(deadline))
        target=path+'?'+urlencode(params)
        request=(f'GET {target} HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: Daon-User-Open-Law/1.0\r\n'
            'Accept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n')
        sock.sendall(request.encode('ascii')); reader=_WireReader(sock,deadline,MAX_BODY)
        if not re.fullmatch(rb'HTTP/1\.[01] 200(?: [\x20-\x7e]*)?\r\n',reader.line()): raise ValueError
        headers={}
        while True:
            line=reader.line()
            if line==b'\r\n': break
            if line[:1] in (b' ',b'\t') or b':' not in line: raise ValueError
            name,value=line[:-2].split(b':',1);name=name.decode('ascii').lower()
            if name in headers: raise ValueError
            headers[name]=value.decode('ascii').strip(' ')
        headers=checked_headers(headers)
        if headers.get('content-type','').split(';')[0].strip().lower() not in {'application/json','text/json'}: raise ValueError
        result=reader.body(headers);remaining(deadline);return result
    except (TimeoutError,socket.timeout): raise OpenLawError('OPEN_LAW_TIMEOUT') from None
    except WebsiteFetchError as error:
        code='OPEN_LAW_TIMEOUT' if str(error)=='WEBSITE_FETCH_TIMEOUT' else 'OPEN_LAW_CONTENT_TOO_LARGE' if str(error)=='WEBSITE_CONTENT_TOO_LARGE' else 'OPEN_LAW_UPSTREAM_FAILED'
        raise OpenLawError(code) from None
    except (OSError,ValueError,UnicodeError): raise OpenLawError('OPEN_LAW_UPSTREAM_FAILED') from None
    finally:
        if sock is not None:sock.close()
        if raw is not None and raw is not sock:raw.close()


def _list(value):
    if value is None:
        return []
    if isinstance(value,dict):
        return [value]
    if isinstance(value,list) and all(isinstance(item,dict) for item in value):
        return value
    raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')


def _text(value):
    if isinstance(value,str): return [value]
    if isinstance(value,list) and all(isinstance(v,str) for v in value):return value
    raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')


def _integer(value):
    if type(value) is not int and (not isinstance(value,str) or re.fullmatch(r'[0-9]+',value) is None):
        raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID')
    return int(value)


class OpenLawClient:
    def __init__(self,*,api_key,transport=None,resolver=None):
        self._key=api_key;self._transport=transport or https_get;self._resolver=resolver or resolve_public_ips

    def _get(self,path,params,deadline):
        if not isinstance(self._key,str) or not self._key or len(self._key)>4096 or any(ord(c)<33 for c in self._key):
            raise OpenLawError('OPEN_LAW_CREDENTIAL_REQUIRED')
        try:
            deadline=min(deadline,time.monotonic()+8)
            ips=validate_public_ips(self._resolver(HOST,deadline))
            raw=self._transport(path,{'OC':self._key,'target':'eflaw','type':'JSON',**params},ips,deadline)
            remaining(deadline)
            if not isinstance(raw,bytes) or self._key.encode() in raw: raise OpenLawError('OPEN_LAW_UPSTREAM_FAILED')
            parsed=parse_json(raw)
            def echo(value):
                if isinstance(value,str):return self._key in value
                if isinstance(value,dict):return any(self._key in k or echo(v) for k,v in value.items())
                if isinstance(value,list):return any(echo(v) for v in value)
                return False
            if echo(parsed): raise OpenLawError('OPEN_LAW_UPSTREAM_FAILED')
            return raw,parsed
        except OpenLawError:raise
        except WebsiteFetchError as error:
            raise OpenLawError('OPEN_LAW_TIMEOUT' if str(error)=='WEBSITE_FETCH_TIMEOUT' else 'OPEN_LAW_EGRESS_BLOCKED') from None
        except Exception:raise OpenLawError('OPEN_LAW_UPSTREAM_FAILED') from None

    def search(self,query,page,deadline):
        validate_search(query,page)
        _,data=self._get('/DRF/lawSearch.do',{'search':'1','nw':'3','display':'20','page':str(page),'query':query},deadline)
        try:
            root=data['LawSearch']; total=_integer(root['totalCnt']); actual=_integer(root['page'])
            rows=_list(root.get('law',[]))
            if total and not rows:raise ValueError
            items=[]
            for row in rows:
                if row.get('현행연혁코드','현행법령') not in {'현행법령','현행'}:raise ValueError
                items.append({'mst':str(row['법령일련번호']),'law_id':str(row['법령ID']),
                    'title':row['법령명한글'],'effective_date':str(row['시행일자']),
                    'promulgation_date':str(row['공포일자']) if row.get('공포일자') else None,'ministry':row.get('소관부처명') or None})
            return validate_search_result({'items':items,'page':actual,'total_count':total},page)
        except (ValueError,KeyError,TypeError):raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID') from None

    def read(self,selection,deadline):
        if not isinstance(selection,OpenLawSelection):raise OpenLawError('OPEN_LAW_INPUT_INVALID')
        params={'MST':selection.mst,'efYd':selection.effective_date}
        if selection.article_code is not None:params['JO']=selection.article_code
        raw,data=self._get('/DRF/lawService.do',params,deadline)
        try:
            root=data['법령']; info=root['기본정보']
            # 법령키는 opaque upstream 값. 고정 요청 MST와 응답 법령ID/시행일은 별도다.
            if str(info['시행일자'])!=selection.effective_date:raise ValueError
            ministry=info.get('소관부처')
            if isinstance(ministry,dict):ministry=ministry.get('content')
            law={'mst':selection.mst,'law_id':str(info['법령ID']),'title':info['법령명_한글'],
                'effective_date':selection.effective_date,'promulgation_date':str(info['공포일자']) if info.get('공포일자') else None,
                'ministry':ministry or None,'article_code':selection.article_code}
            validate_law(law);lines=[];size=0;selected=0
            def append(value):
                nonlocal size
                for text in _text(value):
                    text=text.replace('\r\n','\n').replace('\r','\n')
                    if not text.strip():continue
                    size+=len(text.encode('utf-8'))+(1 if lines else 0)
                    if size>MAX_TEXT:raise OpenLawError('OPEN_LAW_CONTENT_TOO_LARGE')
                    lines.append(text)
            def children(node):
                for key in ('항','호','목'):
                    for child in _list(node.get(key)):
                        if key+'내용' in child:append(child[key+'내용'])
                        children(child)
            for article in _list(root.get('조문',{}).get('조문단위')):
                if selection.article_code is not None:
                    code=f"{_integer(article['조문번호']):04}{_integer(article.get('조문가지번호',0)):02}"
                    if code!=selection.article_code:continue
                selected+=1
                if '조문내용' in article:append(article['조문내용'])
                children(article)
            if not selected or not lines:raise OpenLawError('OPEN_LAW_TEXT_UNAVAILABLE')
            text='\n'.join(lines).encode('utf-8')
            return OpenLawDocument(law,raw,text,hashlib.sha256(raw).hexdigest(),hashlib.sha256(text).hexdigest(),
                datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),VERSION)
        except OpenLawError:raise
        except (ValueError,KeyError,TypeError,AttributeError):raise OpenLawError('OPEN_LAW_PROTOCOL_INVALID') from None
