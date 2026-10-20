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
