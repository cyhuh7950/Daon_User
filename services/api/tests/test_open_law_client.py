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
    def test_search_discards_only_direct_detail_links_and_never_returns_search_raw(self):
        expected={'items':[{'mst':'123','law_id':'456','title':'시험법','effective_date':'20260101',
            'promulgation_date':'20251201','ministry':'시험부'}],'page':1,'total_count':1}
        for single in (False,True):
            with self.subTest(single=single):
                data=json.loads(search_bytes());row=data['LawSearch']['law'][0]
                row['법령상세링크']='https://www.law.go.kr/DRF/lawService.do?OC=fixture-oc&MST=123'
                if single:data['LawSearch']['law']=row
                raw=json.dumps(data,ensure_ascii=False).encode()
                api=self.api(raw)
                self.assertEqual(api.search('민법',1,time.monotonic()+8),expected)
                returned,parsed=api._get('/DRF/lawSearch.do',{},time.monotonic()+8)
                self.assertIsNone(returned)
                rows=parsed['LawSearch']['law']
                for item in rows if isinstance(rows,list) else [rows]:
                    self.assertNotIn('법령상세링크',item)
                self.assertNotIn('fixture-oc',json.dumps(parsed,ensure_ascii=False))
                self.assertEqual(parsed['LawSearch']['page'],'1')

    def test_search_credential_in_any_other_key_value_or_nested_metadata_still_rejected(self):
        for location in ('title','ministry','row-key','row-value','row-nested','root-link','root-key','top-link','unexpected-law'):
            with self.subTest(location=location):
                data=json.loads(search_bytes());root=data['LawSearch'];row=root['law'][0]
                row['법령상세링크']='https://www.law.go.kr/?OC=fixture-oc'
                if location=='title':row['법령명한글']='시험법 fixture-oc'
                elif location=='ministry':row['소관부처명']='fixture-oc'
                elif location=='row-key':row['fixture-oc']='metadata'
                elif location=='row-value':row['추가정보']='fixture-oc'
                elif location=='row-nested':row['metadata']={'법령상세링크':['fixture-oc']}
                elif location=='root-link':root['법령상세링크']='fixture-oc'
                elif location=='root-key':root['fixture-oc']='metadata'
                elif location=='top-link':data['법령상세링크']='fixture-oc'
                else:data['metadata']={'LawSearch':{'law':[{'법령상세링크':'fixture-oc'}]}}
                # Decoded recursive checks must also catch escaped credentials, not only raw bytes.
                raw=json.dumps(data,ensure_ascii=True).replace('fixture-oc','fixture-\\u006fc').encode()
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_UPSTREAM_FAILED$'):
                    self.api(raw).search('민법',1,time.monotonic()+8)

    def test_search_scalar_credential_echo_rejected_before_public_normalization(self):
        from daon_user_api.open_law_client import OpenLawClient
        for key,value,location in [('987654321',987654321,'mst'),('987654321',987654321,'law_id'),
            ('987654321',987654321,'metadata'),('true',True,'metadata'),('false',False,'metadata'),
            ('null',None,'metadata'),('1e+20',1e20,'metadata'),('-0.0',-0.0,'metadata'),
            ('987654321.0',987654321.0,'exponent-wire')]:
            with self.subTest(location=location,key=key):
                data=json.loads(search_bytes());row=data['LawSearch']['law'][0]
                row['법령상세링크']='https://www.law.go.kr/?OC='+key
                if location=='mst':row['법령일련번호']=value
                elif location=='law_id':row['법령ID']=value
                else:row['metadata']={'scalar':[value]}
                raw=json.dumps(data,ensure_ascii=False)
                if location=='exponent-wire':raw=raw.replace('987654321.0','9.87654321e8')
                client=OpenLawClient(api_key=key,transport=lambda *_:raw.encode(),resolver=lambda *_:('8.8.8.8',))
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_UPSTREAM_FAILED$'):
                    client.search('민법',1,time.monotonic()+8)

    def test_search_unrelated_scalars_keep_public_schema_and_discarded_links(self):
        data=json.loads(search_bytes());row=data['LawSearch']['law'][0]
        row['법령상세링크']='https://www.law.go.kr/?OC=fixture-oc'
        row['metadata']=[987654321,1e20,-0.0,True,False,None]
        result=self.api(json.dumps(data,ensure_ascii=False).encode()).search('민법',1,time.monotonic()+8)
        self.assertEqual(result,{'items':[{'mst':'123','law_id':'456','title':'시험법','effective_date':'20260101',
            'promulgation_date':'20251201','ministry':'시험부'}],'page':1,'total_count':1})
        self.assertNotIn('fixture-oc',json.dumps(result))

    def test_search_final_result_rechecks_empty_ministry_normalized_to_json_null(self):
        from daon_user_api.open_law_client import OpenLawClient
        data=json.loads(search_bytes());row=data['LawSearch']['law'][0]
        row['소관부처명']='';row['법령상세링크']='https://www.law.go.kr/?OC=null'
        raw=json.dumps(data,ensure_ascii=False).encode()
        client=OpenLawClient(api_key='null',transport=lambda *_:raw,resolver=lambda *_:('8.8.8.8',))
        with self.assertRaisesRegex(ValueError,'^OPEN_LAW_UPSTREAM_FAILED$'):
            client.search('민법',1,time.monotonic()+8)

    def test_search_link_exception_keeps_json_structure_limits_and_law_validation(self):
        cases=[(b'{"LawSearch":{"law":[],"law":[]}}','OPEN_LAW_PROTOCOL_INVALID'),
            (b'['*41+b']'*41,'OPEN_LAW_PROTOCOL_INVALID'),
            (b' '*2097153,'OPEN_LAW_CONTENT_TOO_LARGE')]
        for value in ('invalid',7,True,[None],['invalid']):
            cases.append((json.dumps({'LawSearch':{'page':'1','totalCnt':'1','law':value}}).encode(),'OPEN_LAW_PROTOCOL_INVALID'))
        for field,value in [('시행일자','20260230'),('법령ID','not-numeric'),('현행연혁코드','연혁법령')]:
            data=json.loads(search_bytes());data['LawSearch']['law'][0].update({field:value,'법령상세링크':'fixture-oc'})
            cases.append((json.dumps(data,ensure_ascii=False).encode(),'OPEN_LAW_PROTOCOL_INVALID'))
        for index,(raw,code) in enumerate(cases):
            with self.subTest(case=index),self.assertRaisesRegex(ValueError,'^'+code+'$'):
                self.api(raw).search('민법',1,time.monotonic()+8)

    def test_body_detail_link_credential_echo_still_rejected_and_raw_hash_unchanged(self):
        from daon_user_api.open_law_client import OpenLawSelection
        for escaped in (False,True):
            with self.subTest(escaped=escaped):
                data=json.loads(body_bytes());data['법령']['법령상세링크']='https://www.law.go.kr/?OC=fixture-oc'
                raw=json.dumps(data,ensure_ascii=False)
                if escaped:raw=raw.replace('fixture-oc','fixture-\\u006fc')
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_UPSTREAM_FAILED$'):
                    self.api(raw.encode()).read(OpenLawSelection('123','20260101',None),time.monotonic()+8)
        raw=body_bytes();document=self.api(raw).read(OpenLawSelection('123','20260101',None),time.monotonic()+8)
        self.assertEqual(document.raw_content,raw)
        self.assertEqual(document.raw_sha256,hashlib.sha256(raw).hexdigest())
        self.assertEqual(document.text_content,'제1조 원문\n① 내용\n1. 순서\n가. 끝'.encode())
        self.assertEqual(document.text_sha256,hashlib.sha256(document.text_content).hexdigest())

    def test_small_raw_policy_bounds_real_wire_before_body_read_in_all_framings(self):
        from daon_user_api.open_law_client import https_get
        class Wire:
            def __init__(self,data):self.data=data;self.body_bytes=0;self.headers_done=False;self.seen=b''
            def settimeout(self,value):pass
            def connect(self,address):pass
            def getpeername(self):return ('8.8.8.8',443)
            def sendall(self,data):pass
            def recv(self,size):
                part=self.data[:size];self.data=self.data[size:]
                if self.headers_done:self.body_bytes+=len(part)
                else:
                    self.seen+=part
                    if self.seen.endswith(b'\r\n\r\n'):self.headers_done=True
                return part
            def close(self):pass
        class TLS:
            check_hostname=True;verify_mode=__import__('ssl').CERT_REQUIRED
            def wrap_socket(self,raw,**kwargs):return raw
        for framing,allowed_read in [(b'Content-Length: 5\r\n',0),(b'Transfer-Encoding: chunked\r\n',3),(b'',5)]:
            with self.subTest(framing=framing):
                body=b'5\r\nabcde\r\n0\r\n\r\n' if framing.startswith(b'Transfer') else b'abcde'
                wire=Wire(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'+framing+b'\r\n'+body)
                with patch('daon_user_api.open_law_client.socket.socket',return_value=wire),patch('daon_user_api.open_law_client.ssl.create_default_context',return_value=TLS()):
                    with self.assertRaisesRegex(ValueError,'^OPEN_LAW_CONTENT_TOO_LARGE$'):
                        https_get('/DRF/lawSearch.do',{'OC':'fixture-oc'},('8.8.8.8',),time.monotonic()+8,max_raw_bytes=4)
                self.assertEqual(wire.body_bytes,allowed_read)

    def test_raw_limit_strict_integer_and_custom_transport_bound(self):
        from daon_user_api.open_law_client import OpenLawClient
        for invalid in (True,False,1.0,0,-1,2097153,'1024'):
            with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,'^OPEN_LAW_INPUT_INVALID$'):
                OpenLawClient(api_key='fixture-oc',max_raw_bytes=invalid)
        raw=search_bytes()
        client=OpenLawClient(api_key='fixture-oc',max_raw_bytes=len(raw)-1,transport=lambda *_:raw,resolver=lambda *_:('8.8.8.8',))
        with self.assertRaisesRegex(ValueError,'^OPEN_LAW_CONTENT_TOO_LARGE$'):client.search('민법',1,time.monotonic()+8)
    def test_official_example_opaque_key_keeps_requested_mst_date_article_and_original_hashes(self):
        from daon_user_api.open_law_client import OpenLawSelection
        # 공식 정적 예시의 식별자 관계만 재현. 본문은 합성 fixture이며 실 API 결과가 아니다.
        data=json.loads(body_bytes());root=data['법령']
        root['법령키']='0112682010083000277'
        root['기본정보'].update({'법령ID':'011268','시행일자':'20100830'})
        raw=json.dumps(data,ensure_ascii=False,indent=2).encode()
        api=self.api(raw)
        result=api.read(OpenLawSelection('107433','20100830','000100'),time.monotonic()+8)
        self.assertEqual(self.calls[0][0],'/DRF/lawService.do')
        self.assertEqual(self.calls[0][1],{'OC':'fixture-oc','target':'eflaw','type':'JSON',
            'MST':'107433','efYd':'20100830','JO':'000100'})
        self.assertEqual(result.law['mst'],'107433')
        self.assertEqual(result.law['law_id'],'011268')
        self.assertEqual(result.raw_content,raw)
        self.assertEqual(result.raw_sha256,hashlib.sha256(raw).hexdigest())
        self.assertEqual(result.text_content,'제1조 원문\n① 내용\n1. 순서\n가. 끝'.encode())
        self.assertEqual(result.text_sha256,hashlib.sha256(result.text_content).hexdigest())

    def test_opaque_key_does_not_relax_response_date_law_id_or_article_binding(self):
        from daon_user_api.open_law_client import OpenLawSelection
        for field,value in [('시행일자','20260102'),('법령ID','not-numeric'),('법령ID',True),('법령ID',456.0)]:
            with self.subTest(field=field,value=value):
                data=json.loads(body_bytes());data['법령']['기본정보'][field]=value
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'):
                    self.api(json.dumps(data,ensure_ascii=False).encode()).read(
                        OpenLawSelection('123','20260101','000100'),time.monotonic()+8)
        with self.assertRaisesRegex(ValueError,'^OPEN_LAW_TEXT_UNAVAILABLE$'):
            self.api(body_bytes()).read(OpenLawSelection('123','20260101','000200'),time.monotonic()+8)
        for mst in ('107433&ID=011268',True,107433.0):
            with self.subTest(mst=mst):
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_INPUT_INVALID$'):
                    OpenLawSelection(mst,'20100830',None)

    def test_upstream_numeric_bool_and_float_aliases_are_protocol_errors(self):
        from daon_user_api.open_law_client import OpenLawSelection
        for field in ('page','totalCnt'):
            for value in (True,1.0,1.5):
                with self.subTest(field=field,value=value):
                    data=json.loads(search_bytes());data['LawSearch'][field]=value
                    with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'):
                        self.api(json.dumps(data).encode()).search('민법',1,time.monotonic()+8)
        for field,value in [('조문번호',True),('조문번호',1.0),('조문번호',1.5),('조문가지번호',False),('조문가지번호',0.0),('조문가지번호',0.5)]:
            with self.subTest(field=field,value=value):
                data=json.loads(body_bytes());data['법령']['조문']['조문단위'][0][field]=value
                with self.assertRaisesRegex(ValueError,'^OPEN_LAW_PROTOCOL_INVALID$'):
                    self.api(json.dumps(data,ensure_ascii=False).encode()).read(
                        OpenLawSelection('123','20260101','000100'),time.monotonic()+8)

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
