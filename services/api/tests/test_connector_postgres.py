from __future__ import annotations

import unittest
from contextlib import contextmanager

from daon_user_api.connector_postgres import PostgresConnectorRepository
from daon_user_api.mcp_connector import ConnectorView


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=()):
        if sql.startswith("SELECT 1 FROM workspace_connectors"):
            return _Result([(1,)] if any(row[:2]==params[:2] for row in self.rows) else [])
        if sql.startswith("INSERT INTO workspace_connectors"):
            if not any(row[:3] == params[:3] for row in self.rows):
                self.rows.append((params[0], params[1], params[2], params[3], params[4], params[5], params[6], params[7], params[8], params[9]))
            return _Result(rowcount=1)
        if sql.startswith("SELECT connector_id,kind,name,status"):
            return _Result([(row[2], row[3], row[4], row[6], row[7], row[5], row[8], row[9]) for row in self.rows
                if row[:2] == params[:2] and (len(params)<3 or row[2]==params[2])])
        if sql.startswith("UPDATE workspace_connectors SET status"):
            for index,row in enumerate(self.rows):
                if row[:3] == params[4:7]:
                    row=(*row[:6],params[0],row[7],params[2],params[1]);self.rows[index]=row
                    return _Result([(row[2],row[3],row[4],row[6],row[7],row[5],row[8],row[9])],1)
            return _Result()
        if sql.startswith("SELECT status,sources_json FROM workspace_connectors"):
            return _Result([(row[6],[]) for row in self.rows if row[:3]==params])
        raise AssertionError(f"unexpected SQL: {sql}")


class _Store:
    def __init__(self):
        self.connection = _Connection()

    @contextmanager
    def _transaction(self, _context):
        yield self.connection


class ConnectorPersistenceTests(unittest.TestCase):
    def test_reconnect_lookup_requires_workspace_registration(self):
        repository=PostgresConnectorRepository(_Store())
        from daon_user_api.mcp_connector import ConnectorError
        with self.assertRaisesRegex(ConnectorError,'^CONNECTOR_NOT_FOUND$'):
            repository.get('tenant','other-workspace','actor','mcp-open-law-go-kr')

    def test_defaults_are_not_recreated_after_user_deletion(self):
        store = _Store()
        repository = PostgresConnectorRepository(store)
        mcp = ConnectorView("mcp-open-law-go-kr", "mcp", "국가법령정보센터", "unavailable", 0, "open.law.go.kr", None, "CONNECTOR_CREDENTIAL_REQUIRED")
        approved = ConnectorView("daon-approved-knowledge", "daon_approved_knowledge", "Daon 승인 지식", "unavailable", 0, "Daon", None, "CONNECTOR_CREDENTIAL_REQUIRED")

        first = repository.list("tenant", "workspace", "user", (mcp, approved))
        self.assertEqual([item.connector_id for item in first], [approved.connector_id])
        repository.register("tenant", "workspace", "user", mcp)
        first = repository.list("tenant", "workspace", "user", (mcp, approved))
        self.assertEqual({item.connector_id for item in first}, {mcp.connector_id, approved.connector_id})
        store.connection.rows = [row for row in store.connection.rows if row[2] != mcp.connector_id]

        second = repository.list("tenant", "workspace", "user", (mcp, approved))
        self.assertEqual([item.connector_id for item in second], [approved.connector_id])


class OpenLawRuntimeScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_runtime_http import RuntimeHttpTests
        await RuntimeHttpTests.asyncSetUp(self)

    async def asyncTearDown(self):
        from test_runtime_http import RuntimeHttpTests
        await RuntimeHttpTests.asyncTearDown(self)

    async def test_open_law_runtime_registration_and_reconnect_never_mutate_global_or_other_workspace(self):
        import os
        import httpx
        from unittest.mock import patch
        from daon_user_api.runtime import create_app,WEB_SESSION_COOKIE
        from daon_user_api.authorization import Role
        from daon_user_api.mcp_connector import ConnectorRegistry,create_open_law_connector
        self.authorization_repository.bootstrap_workspace(tenant_id=self.web.tenant_id,workspace_id='workspace-second',
            owner_user_id=self.web.user_id,owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',
            data_area='cloud_sync',cost_limit_cents=1000,now=self.clock())
        auth={'cookie':f'{WEB_SESSION_COOKIE}={self.web.access_token}','Content-Type':'application/json'}
        for persisted in (False,True):
            registry=ConnectorRegistry((create_open_law_connector(),));before=registry.list()
            self.dependencies.connector_registry=registry
            self.dependencies.connector_repository=PostgresConnectorRepository(_Store()) if persisted else None
            app=create_app(self.dependencies)
            with patch.dict(os.environ,{'DAON_OPEN_LAW_API_KEY':'fixture-oc'}),patch('daon_user_api.open_law_mcp_client.subprocess.Popen') as spawn:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                    registered=await client.post('/api/v1/workspaces/workspace-001/connectors',headers=auth,json={'kind':'mcp','name':'국가법령정보센터'})
                    self.assertEqual(registered.status_code,201)
                    self.assertEqual(registered.json()['data']['status'],'unavailable')
                    self.assertEqual(registered.json()['data']['error_code'],'CONNECTOR_NOT_VERIFIED')
                    reconnect=await client.post('/api/v1/workspaces/workspace-001/connectors/mcp-open-law-go-kr/reconnect',headers=auth)
                    self.assertEqual(reconnect.status_code,200)
                    self.assertEqual(reconnect.json()['data']['error_code'],'CONNECTOR_NOT_VERIFIED')
                    self.assertEqual(registry.list(),before)
                    other=await client.post('/api/v1/workspaces/workspace-second/connectors/mcp-open-law-go-kr/reconnect',headers=auth)
                    self.assertEqual(other.status_code,404)
                    foreign=await client.post('/api/v1/workspaces/workspace-foreign/connectors/mcp-open-law-go-kr/reconnect',headers=auth)
                    self.assertEqual(foreign.status_code,404)  # existing cross-tenant resource hiding
                    disconnected=await client.post('/api/v1/workspaces/workspace-001/connectors/mcp-open-law-go-kr/disconnect',headers=auth)
                    self.assertEqual(disconnected.json()['data']['status'],'disconnected')
                    self.assertEqual(registry.list(),before)
                    listed=await client.get('/api/v1/workspaces/workspace-second/connectors',headers=auth)
                    self.assertEqual(listed.status_code,200)
                    if persisted:
                        self.assertEqual(listed.json()['data']['connectors'],[])
                    else:
                        self.assertTrue(listed.json()['data']['connectors'])
                    self.assertTrue(all(item['status']!='disconnected' for item in listed.json()['data']['connectors']))
                    self.assertEqual(registry.list(),before)
                    own_sources=await client.get('/api/v1/workspaces/workspace-001/connectors/mcp-open-law-go-kr/sources',headers=auth)
                    self.assertEqual(own_sources.status_code,200)
                    self.assertEqual(own_sources.json()['data']['sources'],[])
                    if not persisted:
                        removed=await client.delete('/api/v1/workspaces/workspace-001/connectors/mcp-open-law-go-kr',headers=auth)
                        self.assertEqual(removed.status_code,204)
                        after=await client.get('/api/v1/workspaces/workspace-001/connectors',headers=auth)
                        self.assertEqual(after.json()['data']['connectors'],[])
                        again=await client.post('/api/v1/workspaces/workspace-001/connectors/mcp-open-law-go-kr/reconnect',headers=auth)
                        self.assertEqual(again.status_code,404)
                        self.assertEqual(registry.list(),before)
                spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
