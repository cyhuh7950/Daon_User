from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import test_source_upload_runtime as source_fixture
from daon_user_api.runtime import RuntimeDependencies, WEB_SESSION_COOKIE, create_app
from daon_user_api.website_source_import import WebsiteImportResult, WebsiteSnapshotView, WebsiteImportError


class Imports:
    def __init__(self):
        self.calls=[]; self.replayed=False; self.failure=None
    def import_url(self,context,url,confirmed,key):
        self.calls.append((context,url,confirmed,key))
        if self.failure:
            raise WebsiteImportError(self.failure,503)
        return WebsiteImportResult('snapshot-1','source-1','version-1','ready',self.replayed)


class Repository:
    def get_snapshot(self,ctx,version):
        if version!='version-1' or ctx.notebook_id!='notebook-001':
            raise WebsiteImportError('WEBSITE_ACCESS_DENIED',403)
        return WebsiteSnapshotView('snapshot-1','source-1','version-1','https://example.com/','https://example.com/',
            'Example',None,'2026-10-15T10:00:00+00:00','unknown',None,'a'*64,'b'*64,'website-text-v1')


class WebsiteRuntimeTests(unittest.IsolatedAsyncioTestCase):
    _authenticated=source_fixture.SourceUploadRuntimeTests._authenticated
    asyncTearDown=source_fixture.SourceUploadRuntimeTests.asyncTearDown

    async def asyncSetUp(self):
        assert 'website_import_service' in RuntimeDependencies.__dataclass_fields__, '실제 runtime 연결 필요'
        # 테스트 소유 SQLite만 workspace 임시 경로에 만들고 기존 tearDown에서 회수한다.
        # sandbox OS 임시 root의 ACL 문제를 제품 실패로 계산하지 않는다.
        from pathlib import Path
        from functools import partial
        original = source_fixture.tempfile.TemporaryDirectory
        with patch.object(source_fixture.tempfile, 'TemporaryDirectory', partial(original,
                dir=Path(__file__).resolve().parents[3] / '.superpowers')):
            await source_fixture.SourceUploadRuntimeTests.asyncSetUp(self)
        await self.client.aclose()
        self.imports=Imports(); self.dependencies.website_import_service=self.imports
        self.dependencies.website_repository=Repository()
        from daon_user_api.notebook import NotebookError
        original_get=self.dependencies.notebook_service.get
        def get_notebook(ctx, notebook_id):
            if notebook_id!='notebook-001':
                raise NotebookError('NOTEBOOK_ACCESS_DENIED',403)
            return original_get(ctx,notebook_id)
        self.dependencies.notebook_service.get=get_notebook
        self.app=create_app(self.dependencies)
        import httpx
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1')
        self.body={'notebook_id':'notebook-001','url':'https://example.com/','public_page_confirmed':True}
        self.headers={'Cookie':f'{WEB_SESSION_COOKIE}=opaque-session','Idempotency-Key':'request-1'}

    async def test_exact_post_201_replay200_and_session_owned_context(self):
        with self._authenticated():
            response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json=self.body,headers=self.headers)
            self.assertEqual(response.status_code,201,response.text)
            value=response.json()['data']; self.assertEqual(value['kind'],'website_import')
            self.assertEqual(value['attributes']['source_state'],'ready')
            self.assertEqual(self.imports.calls[0][0].actor_id,'user-001')
            self.assertEqual(self.imports.calls[0][0].notebook_id,'notebook-001')
            self.imports.replayed=True
            replay=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json=self.body,headers=self.headers)
            self.assertEqual(replay.status_code,200)
            self.assertIn('no-store',replay.headers['cache-control'])

    async def test_runtime_openapi_declares_exact_import_body(self):
        operation=self.app.openapi()['paths']['/api/v1/workspaces/{id}/website-imports']['post']
        self.assertIn('requestBody',operation)
        schema=operation['requestBody']['content']['application/json']['schema']
        self.assertEqual(set(schema['required']),{'notebook_id','url','public_page_confirmed'})
        self.assertFalse(schema['additionalProperties'])
        self.assertIs(schema['properties']['public_page_confirmed']['const'],True)

    async def test_unknown_scope_false_confirmation_missing_key_and_url_fail_before_import(self):
        with self._authenticated():
            for changes in ({'actor_id':'other'},{'public_page_confirmed':False},{'public_page_confirmed':1},{'url':'http://example.com/'}, {'url':'https://127.0.0.1/'}):
                response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json={**self.body,**changes},headers=self.headers)
                self.assertIn(response.status_code,(400,422))
            response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json=self.body,headers={'Cookie':self.headers['Cookie']})
            self.assertIn(response.status_code,(400,422))
        self.assertEqual(self.imports.calls,[])

    async def test_duplicate_body_and_snapshot_query_fail_closed(self):
        with self._authenticated():
            raw='{"notebook_id":"other","notebook_id":"notebook-001","url":"https://example.com/","public_page_confirmed":true}'
            response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',content=raw,headers={**self.headers,'Content-Type':'application/json'})
            self.assertEqual(response.status_code,400)
            response=await self.client.get('/api/v1/workspaces/workspace-001/source-versions/version-1/website-snapshot?notebook_id=other&notebook_id=notebook-001',headers=self.headers)
            self.assertEqual(response.status_code,400)

    async def test_snapshot_exact13_fields_and_notebook_or_version_denial(self):
        with self._authenticated():
            url='/api/v1/workspaces/workspace-001/source-versions/version-1/website-snapshot?notebook_id=notebook-001'
            response=await self.client.get(url,headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['data']),13)
            self.assertNotIn('text',response.json()['data'])
            self.assertEqual((await self.client.get(url.replace('version-1','other'),headers=self.headers)).status_code,403)
            self.assertEqual((await self.client.get(url.replace('notebook-001','other'),headers=self.headers)).status_code,403)
            self.assertEqual((await self.client.get(url+'&actor_id=other',headers=self.headers)).status_code,400)

    async def test_snapshot_permission_revoked_during_read_returns_no_metadata(self):
        from daon_user_api.authorization import AuthorizationError
        def revoke(ctx,version):
            self.authorization.authorize_action=lambda **kwargs: (_ for _ in ()).throw(AuthorizationError('ACTION_DENIED',403))
            return Repository().get_snapshot(ctx,version)
        self.dependencies.website_repository.get_snapshot=revoke
        with self._authenticated():
            response=await self.client.get('/api/v1/workspaces/workspace-001/source-versions/version-1/website-snapshot?notebook_id=notebook-001',headers=self.headers)
        self.assertEqual(response.status_code,403)
        self.assertNotIn('requested_url',response.text)

    async def test_permission_denial_and_safe_error(self):
        from daon_user_api.authorization import AuthorizationError
        with self._authenticated(),patch.object(self.authorization,'authorize_action',side_effect=AuthorizationError('ACTION_DENIED',403)):
            response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json=self.body,headers=self.headers)
        self.assertEqual(response.status_code,403); self.assertEqual(self.imports.calls,[])
        self.imports.failure='WEBSITE_STORAGE_UNAVAILABLE'
        with self._authenticated():
            response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json=self.body,headers=self.headers)
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'WEBSITE_STORAGE_UNAVAILABLE')

    async def test_api_timeout_has_website_safe_error(self):
        import time
        original=self.imports.import_url
        def slow(*args):time.sleep(.05);return original(*args)
        self.imports.import_url=slow
        with self._authenticated(),patch('daon_user_api.runtime.request_timeout_for_path',return_value=.01) as timeout:
            response=await self.client.post('/api/v1/workspaces/workspace-001/website-imports',json=self.body,headers=self.headers)
            self.assertTrue(timeout.called,'현재 custom route timeout 호출 확인')
        self.assertEqual(response.status_code,504)
        self.assertEqual(response.json()['error']['code'],'WEBSITE_FETCH_TIMEOUT')

    async def test_production_factory_true_privacy_current_role_and_fingerprint_per_hop(self):
        from daon_user_api.safe_http_fetch import HttpFetchResponse, WebsiteFetchError
        from daon_user_api.website_source_import import WebsiteProcessStorage
        from daon_user_api.authorization import Role
        from contextlib import ExitStack
        import httpx
        for scenario in ('root','fingerprint','role','destination','deny','title255','title256','longFallback'):
            calls=[]
            policy=SimpleNamespace(mode='allow_approved_external',classification='public',allowed_provider_kinds=('external_api',),
                max_bytes=2097152,masking_required=True,redaction_required=True,required_approver='organization_admin',
                fingerprint='frozen',allowed_destinations=('example.com',))
            role=[Role.PERSONAL_OWNER]
            class Transport:
                def get(inner,url,ips,deadline):
                    calls.append((url,ips))
                    if scenario=='fingerprint':policy.fingerprint='changed'
                    if scenario=='role':role[0]=Role.VIEWER
                    if scenario in ('fingerprint','role'):
                        return HttpFetchResponse(302,{'Location':'/next'},b'')
                    if scenario in ('title255','title256'):
                        body=('<title>'+'가'*(255 if scenario=='title255' else 256)+'</title><p>public body</p>').encode()
                        return HttpFetchResponse(200,{'Content-Type':'text/html'},body)
                    return HttpFetchResponse(200,{'Content-Type':'text/plain'},b'public body')
            class Service:
                def __init__(inner,repo,promoter,**ports):inner.ports=ports
                def import_url(inner,ctx,url,confirmed,key):
                    inner.ports['authorize_context'](ctx)
                    try: inner.ports['fetch_page'](ctx,url)
                    except WebsiteFetchError as error: raise WebsiteImportError(str(error),403) from None
                    return WebsiteImportResult('snapshot-1','source-1','version-1','ready',False)
            self.dependencies.settings=replace(self.settings,cloud_database_dsn='postgresql://test-only.invalid/isolated')
            self.dependencies.object_queue_store=SimpleNamespace(close=lambda:None)
            self.dependencies.website_storage=WebsiteProcessStorage(endpoint='test-only.invalid:9000',bucket='isolated',access_key='fixture',secret_key='fixture',secure=True)
            self.dependencies.website_import_service=None
            self.dependencies.egress_policy_service=SimpleNamespace(get_effective=lambda ctx:policy)
            if scenario=='destination':policy.allowed_destinations=('other.example',)
            if scenario=='deny':policy.mode='deny_external'
            original_authorize=self.authorization.authorize_action
            def authorize(**kwargs):
                grant=original_authorize(**kwargs)
                return replace(grant,role=role[0])
            with ExitStack() as stack:
                stack.enter_context(self._authenticated())
                stack.enter_context(patch.object(self.authorization,'authorize_action',side_effect=authorize))
                stack.enter_context(patch('daon_user_api.runtime._WebsiteRequestRepository',return_value=SimpleNamespace(close=lambda:None)))
                stack.enter_context(patch('daon_user_api.runtime.PostgresDataCanonStore'))
                stack.enter_context(patch('daon_user_api.runtime.WebsiteSourceImportService',Service))
                stack.enter_context(patch('daon_user_api.runtime.SafeHttpTransport',Transport))
                stack.enter_context(patch('daon_user_api.safe_http_fetch.resolve_public_ips',return_value=('8.8.8.8',)))
                app=create_app(self.dependencies)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                    body={**self.body,'url':'https://example.com/'+'x'*240} if scenario=='longFallback' else self.body
                    response=await client.post('/api/v1/workspaces/workspace-001/website-imports',json=body,headers=self.headers)
            if scenario in ('root','title255'):self.assertEqual(response.status_code,201,response.text)
            elif scenario in ('title256','longFallback'):
                self.assertEqual(response.status_code,502,response.text)
                self.assertEqual(response.json()['error']['code'],'WEBSITE_FETCH_FAILED')
            else:self.assertEqual(response.status_code,403,response.text)
            self.assertEqual(len(calls),0 if scenario in ('destination','deny') else 1)
            if calls:self.assertEqual(calls[0],(body['url'],('8.8.8.8',)))
            self.assertTrue(policy.masking_required and policy.redaction_required)

    async def test_selected_original_only_and_default_six_fields(self):
        from daon_user_api.studio_report import WorkspaceSourceProjection
        raw=b'  original\n\n<script>public</script>  '
        source=WorkspaceSourceProjection('source-1','version-1','website.txt','ready','unavailable','unavailable')
        self.dependencies.studio_report_repository=SimpleNamespace(list_sources=lambda ctx:(source,))
        self.dependencies.notebook_service.read_selected_context=lambda ctx,nid:SimpleNamespace(sources=(('source-1','version-1'),),etag='"n1"')
        self.app=create_app(self.dependencies)
        import httpx
        await self.client.aclose()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1')
        url='/api/v1/workspaces/workspace-001/sources?notebook_id=notebook-001'
        with self._authenticated():
            default=await self.client.get(url,headers=self.headers)
            self.assertEqual(len(default.json()['data']['sources'][0]),6)
            with patch('daon_user_api.runtime._read_website_original',create=True,return_value=raw.decode()):
                response=await self.client.get(url+'&website_source_version_id=version-1',headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['data']['sources'][0]['website']['text'],raw.decode())
            self.assertEqual((await self.client.get(url+'&website_source_version_id=other',headers=self.headers)).status_code,403)


class WebsiteReadBoundaryTests(unittest.TestCase):
    def test_title_255_256_and_empty_long_url_fallback_before_store(self):
        import daon_user_api.runtime as runtime
        self.assertTrue(hasattr(runtime,'_website_public_page'),'저장 전 공개 DTO 제목 한도 필요')
        from daon_user_api.internet_connector import FetchedWebsite
        import hashlib
        def page(title,url='https://example.com/'):
            return FetchedWebsite(url,url,(),title,None,'unknown',None,'2026-10-15T10:00:00Z',b'x',b'x',hashlib.sha256(b'x').hexdigest(),hashlib.sha256(b'x').hexdigest(),'website-text-v1')
        valid=page('가'*255)
        self.assertIs(runtime._website_public_page(valid),valid)
        for value in (page('가'*256),page('', 'https://example.com/'+'x'*240),page('https://example.com/'+'x'*240)):
            with self.assertRaises(WebsiteImportError):runtime._website_public_page(value)
        fallback=page('https://example.com/')
        self.assertIs(runtime._website_public_page(fallback),fallback)

    def test_website_json_total_response_is_bounded(self):
        import daon_user_api.runtime as runtime
        self.assertTrue(hasattr(runtime,'_website_json_response'),'전체 응답 bounded serializer 필요')
        response=runtime._website_json_response({'text':'가'*10},maximum=100)
        self.assertLess(len(response.body),100)
        with self.assertRaises(WebsiteImportError):runtime._website_json_response({'text':'가'*40},maximum=100)
    def test_api_deadline_participates_in_web_promotion_and_commit(self):
        import daon_user_api.runtime as runtime
        import time
        from contextlib import contextmanager
        self.assertTrue(hasattr(runtime,'_WebsiteRequestPromoter'),'API 전체deadline 전달 필요')
        calls=[];end=time.monotonic()+.1
        promoter=object.__new__(runtime._WebsiteRequestPromoter);promoter._request_deadline=end
        with patch('daon_user_api.website_source_import.WebsiteObjectPromoter.promote',side_effect=lambda *args:calls.append(args[-1])):
            promoter.promote(None,None,'raw',b'x',end+12)
        self.assertEqual(calls,[end])
        repo=object.__new__(runtime._WebsiteRequestRepository);repo._request_deadline=time.monotonic()-1
        @contextmanager
        def transaction(self,ctx,**kwargs):
            yield SimpleNamespace(execute=lambda *args:None)
        with patch('daon_user_api.website_source_postgres.PostgresWebsiteSourceRepository._transaction',transaction):
            with self.assertRaises(WebsiteImportError):
                with repo._transaction(None):pass

    def test_original_read_child_timeout_is_killed_and_reaped(self):
        import subprocess,sys,time
        from daon_user_api.runtime import _website_read_exchange
        with subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(10)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as child:
            started=time.monotonic()
            with self.assertRaises(WebsiteImportError) as failure:
                _website_read_exchange(child,b'{}',started+.1)
            self.assertEqual(failure.exception.code,'WEBSITE_FETCH_TIMEOUT')
            self.assertIsNotNone(child.poll())
            self.assertLess(time.monotonic()-started,1)

    def test_raw_object_size_utf8_and_full_hash_not_chunks(self):
        import daon_user_api.runtime as runtime
        self.assertTrue(hasattr(runtime,'_checked_website_text'),'원문 전체 hash/UTF8 경계 필요')
        raw='  공백\n<script>data</script> '.encode()
        import hashlib
        self.assertEqual(runtime._checked_website_text(raw,hashlib.sha256(raw).hexdigest(),len(raw)),raw.decode())
        for value,digest,size in [(raw,'a'*64,len(raw)),(b'\xff','a'*64,1),(b'x'*524289,'a'*64,524289)]:
            with self.assertRaises(WebsiteImportError): runtime._checked_website_text(value,digest,size)

    def test_privacy_required_accepts_only_public_get_no_private_fields(self):
        import daon_user_api.runtime as runtime
        self.assertTrue(hasattr(runtime,'_website_outbound'),'웹 privacy 조건의 별도 검증 필요')
        value=runtime._website_outbound('https://example.com/')
        self.assertEqual(set(value),{'url','method','headers'})
        self.assertEqual(value['method'],'GET')
        self.assertEqual(set(value['headers']),{'Host','User-Agent','Accept','Accept-Encoding'})
        for url in ['https://user@example.com/','https://example.com/?secret=1','https://127.0.0.1/']:
            with self.assertRaises(WebsiteImportError): runtime._website_outbound(url)
