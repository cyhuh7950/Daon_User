import base64
import hashlib
import json
import time
import unittest
from unittest.mock import patch


def search_bytes():
    return json.dumps({'LawSearch': {'page':'1','totalCnt':'1','law':[
        {'법령일련번호':'123','법령ID':'456','법령명한글':'시험법','시행일자':'20260101',
         '공포일자':'20251201','소관부처명':'시험부'}]}},ensure_ascii=False).encode()


def body_bytes():
    return json.dumps({'법령': {'법령키':'123','기본정보':{'법령ID':'456','법령명_한글':'시험법',
        '시행일자':'20260101','공포일자':'20251201','소관부처':{'content':'시험부'}},
        '조문':{'조문단위':[{'조문번호':'1','조문가지번호':'0','조문내용':'제1조 원문',
            '항':[{'항내용':'① 내용','호':[{'호내용':'1. 순서','목':{'목내용':'가. 끝'}}]}]}]}}},
        ensure_ascii=False,indent=2).encode()


class OpenLawClientTests(unittest.TestCase):
    def test_malformed_article_children_and_law_arrays_never_silently_drop(self):
        from daon_user_api.open_law_client import OpenLawSelection
        for key in ('항','호','목'):
            for invalid in ('invalid-nonempty-structure','',7,True,['invalid'],[None],[{} ,'invalid']):
                with self.subTest(key=key,invalid=invalid):
                    document=json.loads(body_bytes())
                    document['법령']['조문']['조문단위'][0][key]=invalid
                    api=self.api(json.dumps(document,ensure_ascii=False).encode())
                    with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'):
                        api.read(OpenLawSelection('123','20260101',None),time.monotonic()+8)
        for invalid in ('invalid-nonempty-structure','',7,True,['invalid'],[None]):
            with self.subTest(law=invalid):
                api=self.api(json.dumps({'LawSearch':{'totalCnt':'0','page':'1','law':invalid}}).encode())
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'):
                    api.search('민법',1,time.monotonic()+8)

    def test_absent_and_normal_article_children_keep_original_text_order(self):
        from daon_user_api.open_law_client import OpenLawSelection
        for absent in (None,[],{}):
            document=json.loads(body_bytes())
            document['법령']['조문']['조문단위'][0]['항']=absent
            api=self.api(json.dumps(document,ensure_ascii=False).encode())
            self.assertEqual(api.read(OpenLawSelection('123','20260101',None),time.monotonic()+8).text_content,'제1조 원문'.encode())

    def api(self, raw):
        from daon_user_api.open_law_client import OpenLawClient
        self.calls=[]
        def transport(path, params, ips, deadline):
            self.calls.append((path,params,ips,deadline)); return raw
        return OpenLawClient(api_key='fixture-oc',transport=transport,resolver=lambda *_:('8.8.8.8',))

    def test_raw_base64_decodes_original_bytes_and_hash(self):
        from daon_user_api.open_law_client import OpenLawSelection, decode_document, encode_document
        raw=body_bytes(); document=self.api(raw).read(OpenLawSelection('123','20260101',None),time.monotonic()+8)
        self.assertEqual(document.raw_content,raw)
        self.assertEqual(document.text_content,'제1조 원문\n① 내용\n1. 순서\n가. 끝'.encode())
        self.assertEqual(document.raw_sha256,hashlib.sha256(raw).hexdigest())
        wire=encode_document(document)
        self.assertEqual(base64.b64decode(wire['raw_base64'],validate=True),raw)
        self.assertEqual(decode_document(wire),document)
        for key,value in [('raw_sha256','a'*64),('raw_base64',wire['raw_base64']+'!'),('text','modified')]:
            bad={**wire,key:value}
            with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'): decode_document(bad)

    def test_query_and_version_validation(self):
        from daon_user_api.open_law_client import OpenLawSelection
        api=self.api(search_bytes())
        self.assertEqual(api.search('민법',1,time.monotonic()+8)['items'][0]['mst'],'123')
        self.assertEqual(self.calls[0][0],'/DRF/lawSearch.do')
        self.assertEqual(self.calls[0][1],{'OC':'fixture-oc','target':'eflaw','type':'JSON','search':'1','nw':'3','display':'20','page':'1','query':'민법'})
        for query,page in [('',1),('x'*101,1),('x',True),('x',101),('x\n',1)]:
            with self.assertRaisesRegex(ValueError,'^OPEN_LAW_INPUT_INVALID$'):api.search(query,page,time.monotonic()+8)
        for values in [('1','20260230',None),('x','20260101',None),('1','20260101','1')]:
            with self.assertRaisesRegex(ValueError,'^OPEN_LAW_INPUT_INVALID$'):OpenLawSelection(*values)

    def test_credential_redacted_on_errors(self):
        from daon_user_api.open_law_client import OpenLawClient
        api=OpenLawClient(api_key='fixture-oc',resolver=lambda *_:('8.8.8.8',),
            transport=lambda *_: b'{"echo":"fixture-oc"}')
        with self.assertRaisesRegex(ValueError,'^OPEN_LAW_UPSTREAM_FAILED$'):api.search('민법',1,time.monotonic()+8)

    def test_https_dns_and_no_redirect(self):
        from daon_user_api.open_law_client import OpenLawClient
        api=OpenLawClient(api_key='fixture-oc',resolver=lambda *_:('127.0.0.1',))
        with patch('daon_user_api.open_law_client.socket.socket') as create:
            with self.assertRaisesRegex(ValueError,'^OPEN_LAW_EGRESS_BLOCKED$'):api.search('민법',1,time.monotonic()+8)
            create.assert_not_called()

    def test_pinned_https_wire_no_proxy_cookie_redirect_and_header_bound(self):
        from daon_user_api.open_law_client import https_get
        from daon_user_api.safe_http_fetch import WebsiteFetchError
        class Wire:
            def __init__(self,data):self.data=data;self.sent=b'';self.connected=None
            def settimeout(self,value):self.timeout=value
            def connect(self,address):self.connected=address
            def getpeername(self):return ('8.8.8.8',443)
            def sendall(self,data):self.sent=data
            def recv(self,size):result=self.data[:size];self.data=self.data[size:];return result
            def close(self):pass
        class TLS:
            check_hostname=True
            verify_mode=__import__('ssl').CERT_REQUIRED
            def wrap_socket(self,raw,*,server_hostname):
                self.hostname=server_hostname;return raw
        for response,code in [(b'HTTP/1.1 302 Found\r\nLocation: https://evil.example\r\n\r\n','OPEN_LAW_UPSTREAM_FAILED'),
            (b'HTTP/1.1 200 OK\r\nX-Huge: '+b'a'*16385+b'\r\n\r\n','OPEN_LAW_CONTENT_TOO_LARGE'),
            (b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}',None)]:
            wire=Wire(response);tls=TLS()
            with patch('daon_user_api.open_law_client.socket.socket',return_value=wire),patch('daon_user_api.open_law_client.ssl.create_default_context',return_value=tls):
                if code:
                    with self.assertRaisesRegex(ValueError,'^'+code+'$'):https_get('/DRF/lawSearch.do',{'OC':'fixture-oc'},('8.8.8.8',),time.monotonic()+8)
                else:self.assertEqual(https_get('/DRF/lawSearch.do',{'OC':'fixture-oc'},('8.8.8.8',),time.monotonic()+8),b'{}')
            self.assertEqual(wire.connected,('8.8.8.8',443));self.assertEqual(tls.hostname,'www.law.go.kr')
            self.assertIn(b'GET /DRF/lawSearch.do?OC=fixture-oc HTTP/1.1',wire.sent)
            self.assertNotIn(b'Cookie:',wire.sent);self.assertNotIn(b'Authorization:',wire.sent)

    def test_version_mismatch_empty_html_nested_and_oversize_rejected(self):
        from daon_user_api.open_law_client import OpenLawSelection
        for raw in [b'<html>error</html>',b'[]',b'['*65+b']'*65,b' '*2097153,
                    body_bytes().replace(b'20260101',b'20260102')]:
            with self.subTest(raw_size=len(raw)):
                with self.assertRaises(ValueError): self.api(raw).read(OpenLawSelection('123','20260101',None),time.monotonic()+8)
