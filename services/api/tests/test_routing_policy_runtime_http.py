import unittest
import httpx
from dataclasses import replace
import test_provider_settings_runtime_http as provider_tests
from test_routing_policy import policy_body
from daon_user_api.runtime import create_app, WEB_SESSION_COOKIE


class RoutingPolicyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = provider_tests.ProviderSettingsRuntimeHttpTests.asyncSetUp
    asyncTearDown = provider_tests.ProviderSettingsRuntimeHttpTests.asyncTearDown

    async def test_policy_endpoint_authenticates_before_reporting_missing_store(self):
        self.client.cookies.clear()
        r = await self.client.get(f'/api/v1/workspaces/{self.workspace_id}/routing-policy')
        self.assertEqual(r.status_code, 401)
        self.client.cookies.set(WEB_SESSION_COOKIE, self.credentials.access_token)
        r = await self.client.get(f'/api/v1/workspaces/{self.workspace_id}/routing-policy')
        self.assertEqual(r.status_code, 503)
        self.assertIn('ROUTING_POLICY_UNAVAILABLE', r.text)

    async def test_policy_rejects_body_scope_and_precondition_before_store_writes(self):
        body = policy_body()
        r = await self.client.put(f'/api/v1/workspaces/{self.workspace_id}/routing-policy',
            headers={'Idempotency-Key': 'qa-routing'}, json={**body, 'tenant_id': 'other'})
        self.assertEqual(r.status_code, 422)

    async def test_policy_http_uses_real_service_and_repository_without_dispatch(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from test_routing_policy_postgres import Store
        from daon_user_api.routing_policy import RoutingPolicyService
        from daon_user_api.routing_policy_postgres import PostgresRoutingPolicyRepository, PostgresRoutingMetadata
        from daon_user_api import routing_policy_postgres as pg
        store=Store()
        service=RoutingPolicyService(PostgresRoutingPolicyRepository(store),PostgresRoutingMetadata(store))
        self.dependencies.routing_policy_service=service
        await self.client.aclose()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url='https://app.example.com',cookies={WEB_SESSION_COOKIE:self.credentials.access_token})
        url=f'/api/v1/workspaces/{self.workspace_id}/routing-policy'
        body={**policy_body(),'enabled':False}
        with (patch.object(pg,'_authorize',return_value=SimpleNamespace(role='organization_admin')),
             patch.object(pg,'_CurrentAuthorizationRules',return_value=SimpleNamespace(
                 _effective_permission=lambda *args,**kwargs:SimpleNamespace(effective=True)))):
            absent=await self.client.get(url)
            self.assertEqual(absent.status_code,200);self.assertEqual(absent.json()['data']['version'],0)
            missing=await self.client.put(url,headers={'Idempotency-Key':'create'},json=body)
            self.assertEqual(missing.status_code,428)
            headers={'Idempotency-Key':'create','If-None-Match':'*'}
            first=await self.client.put(url,headers=headers,json=body)
            self.assertEqual(first.status_code,200)
            self.assertFalse(first.json()['data']['execution_enabled'])
            replay=await self.client.put(url,headers=headers,json=body)
            self.assertTrue(replay.json()['meta']['replayed'])
            self.assertEqual(len(store.db.versions),1)
            clash=await self.client.put(url,headers=headers,json={**body,'enabled':True})
            self.assertEqual(clash.status_code,409)
            read=await self.client.get(url)
            self.assertEqual(read.json()['data'],first.json()['data'])
            self.assertEqual(read.headers['etag'],first.headers['etag'])
            denied=await self.client.get('/api/v1/workspaces/not-mine/routing-policy')
            self.assertIn(denied.status_code,(403,404))
            self.assertTrue(all(c.tenant_id==self.credentials.tenant_id and c.workspace_id==self.workspace_id
                for c in store.contexts))


    async def _install_capability_policy_store(self):
        from types import SimpleNamespace
        self.policy_writes=[]
        class PolicyStore:
            def get(inner,context):
                return dict(version=0,policy_version_id=None,digest_sha256=None,body=None,
                    execution_enabled=False,readiness='policy_absent',etag='"routing-empty"')
            def put(inner,*args,**kwargs):
                self.policy_writes.append(args)
                raise AssertionError('unexpected policy write')
        self.dependencies.routing_policy_service=PolicyStore()
        await self.client.aclose()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url='https://app.example.com',cookies={WEB_SESSION_COOKIE:self.credentials.access_token})
        return f'/api/v1/workspaces/{self.workspace_id}/routing-policy'

    def _revoke_policy_management_in_synthetic_workspace(self):
        with self.authorization_repository.transaction() as connection:
            connection.execute("INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) VALUES(?,?,?,'viewer','active',1,?)",
                (self.credentials.tenant_id,self.workspace_id,self.credentials.user_id,self.clock().isoformat()))
            connection.execute('DELETE FROM auth_tenant_roles WHERE tenant_id=? AND user_id=?',
                (self.credentials.tenant_id,self.credentials.user_id))

    async def test_policy_capability_opt_in_current_grant_and_legacy_response_unchanged(self):
        url=await self._install_capability_policy_store()
        default=await self.client.get(url)
        opted=await self.client.get(url+'?include_permissions=1')
        self.assertEqual(opted.status_code,200)
        self.assertEqual(opted.json()['meta']['permissions'],{'policy_manage':True})
        self.assertNotIn('permissions',default.json()['meta'])
        self.assertEqual(default.json()['data'],opted.json()['data'])
        self.assertEqual(default.headers['etag'],opted.headers['etag'])
        self.assertEqual(opted.headers['cache-control'],'no-store')
        self.assertEqual(self.policy_writes,[])

    async def test_policy_capability_revoked_grant_is_false_and_put_rechecks_current_authority(self):
        url=await self._install_capability_policy_store()
        before=await self.client.get(url+'?include_permissions=1')
        self.assertEqual(before.status_code,200)
        self._revoke_policy_management_in_synthetic_workspace()
        after=await self.client.get(url+'?include_permissions=1')
        self.assertEqual(after.status_code,200)
        self.assertEqual(after.json()['meta']['permissions'],{'policy_manage':False})
        denied=await self.client.put(url,headers={'Idempotency-Key':'revoked','If-None-Match':'*'},json=policy_body())
        self.assertEqual(denied.status_code,403)
        self.assertEqual(denied.json()['error']['code'],'FORBIDDEN')
        self.assertEqual(self.policy_writes,[])

    async def test_policy_capability_only_exact_literal_and_scope_are_accepted(self):
        url=await self._install_capability_policy_store()
        for query in ['include_permissions=0','include_permissions=true','include_permissions=',
                      'include_permissions=1&include_permissions=1','unknown=1']:
            with self.subTest(query=query):
                result=await self.client.get(url+'?'+query)
                self.assertEqual(result.status_code,400)
        wrong=await self.client.get('/api/v1/workspaces/other-scope/routing-policy?include_permissions=1')
        self.assertIn(wrong.status_code,(403,404))
        result=await self.client.put(url+'?include_permissions=1',headers={'Idempotency-Key':'query'},json=policy_body())
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.policy_writes,[])

    async def test_policy_capability_does_not_swallow_non_denial_authorization_failure(self):
        from unittest.mock import patch
        from daon_user_api.authorization import Action,AuthorizationError
        url=await self._install_capability_policy_store()
        original=self.authorization.authorize_action
        def authorize(**kwargs):
            if kwargs['action'] is Action.POLICY_MANAGE:raise AuthorizationError('AUDIT_WRITE_FAILED',503)
            return original(**kwargs)
        with patch.object(self.authorization,'authorize_action',side_effect=authorize):
            result=await self.client.get(url+'?include_permissions=1')
        self.assertEqual(result.status_code,503)
        self.assertNotIn('permissions',result.json())
        self.assertEqual(self.policy_writes,[])
