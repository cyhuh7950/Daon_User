from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Role, SqliteAuthorizationRepository, AuthorizationService
from daon_user_api.notification import (
    DeliveryState,
    InboxRequest,
    NotificationError,
    NotificationEvent,
    NotificationService,
    ReferenceNotificationRepository,
)
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, create_app


class NotificationContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "notification.sqlite3"
        self.audit = AuditEventStore()
        self.identity, self.identity_repository, _, self.clock = create_service(
            self.db_path, audit_store=self.audit
        )
        self.session = native_login(self.identity)
        self.principal = self.identity.describe_access(
            self.session.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION
        ).principal
        self.authorization_repository = SqliteAuthorizationRepository(self.db_path)
        self.authorization_repository.bootstrap_workspace(
            tenant_id=self.session.tenant_id,
            workspace_id="workspace-001",
            owner_user_id=self.session.user_id,
            owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization",
            data_area="cloud_sync",
            cost_limit_cents=1000,
            now=self.clock(),
        )
        self.authorization = AuthorizationService(
            repository=self.authorization_repository,
            audit_store=self.audit,
            clock=self.clock,
            identity_service=self.identity,
        )
        self.repository = ReferenceNotificationRepository()
        self.service = NotificationService(
            repository=self.repository,
            authorization_service=self.authorization,
            audit_store=self.audit,
            clock=self.clock,
            native_route_allowlist=frozenset({"operations", "inbox"}),
        )
        self.event = NotificationEvent(
            event_id="event-run-failed-001",
            tenant_id=self.session.tenant_id,
            workspace_id="workspace-001",
            kind="run_failed",
            severity="warning",
            title="실행 실패",
            summary="승인된 모델 실행을 확인하세요.",
            resource_type="run",
            resource_id="run-001",
            deep_link="/operations?run=run-001",
            trace_id="trace-notification-001",
            policy_version=POLICY_VERSION,
        )
        self.service.publish(self.event, candidates=(self.principal,))
        settings = RuntimeSettings.for_test(database_path=self.db_path, policy_version=POLICY_VERSION)
        self.dependencies = RuntimeDependencies(
            settings=settings,
            identity_service=self.identity,
            authorization_service=self.authorization,
            audit_store=self.audit,
            identity_repository=self.identity_repository,
            authorization_repository=self.authorization_repository,
            notification_service=self.service,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(self.dependencies)),
            base_url="https://api.test.invalid",
        )
        self.headers = {"Authorization": f"Bearer {self.session.access_token}"}

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    async def test_event_deduplication_recipient_and_audit_are_exactly_once(self) -> None:
        created = self.service.publish(self.event, candidates=(self.principal,))
        self.assertEqual(created, ())
        page = self.service.list_notifications(
            principal=self.principal, limit=50, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.unread_count, 1)
        events = self.audit.list(
            tenant_id=self.session.tenant_id, action="notification.created"
        ).items
        self.assertEqual(len(events), 1)

    async def test_http_list_detail_read_is_durable_in_adapter_and_idempotent(self) -> None:
        listed = await self.client.get("/api/v1/notifications?limit=20", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        item = listed.json()["data"]["items"][0]
        self.assertEqual(listed.json()["data"]["unread_count"], 1)
        detail = await self.client.get(f"/api/v1/notifications/{item['id']}", headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        write_headers = {
            **self.headers,
            "X-Trace-Id": "trace-notification-read-replay",
            "Content-Type": "application/json",
            "If-Match": detail.headers["etag"],
            "Idempotency-Key": "idem-notification-read-001",
        }
        first = await self.client.patch(
            f"/api/v1/notifications/{item['id']}", headers=write_headers, json={"state": "read"}
        )
        second = await self.client.patch(
            f"/api/v1/notifications/{item['id']}", headers=write_headers, json={"state": "read"}
        )
        refreshed = await self.client.get("/api/v1/notifications", headers=self.headers)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(refreshed.json()["data"]["unread_count"], 0)
        stale = await self.client.patch(
            f"/api/v1/notifications/{item['id']}",
            headers={**write_headers, "Idempotency-Key": "idem-notification-read-002"},
            json={"state": "read"},
        )
        self.assertEqual(stale.status_code, 412)
        conflict = await self.client.patch(
            f"/api/v1/notifications/{item['id']}",
            headers={**write_headers, "If-Match": first.headers["etag"]},
            json={"state": "unread"},
        )
        self.assertEqual(conflict.status_code, 409)

    async def test_current_acl_tenant_recipient_and_deep_link_fail_closed(self) -> None:
        foreign = await self.client.get(
            "/api/v1/notifications/notification-foreign", headers=self.headers
        )
        self.assertEqual(foreign.status_code, 404)
        with self.authorization_repository.transaction() as connection:
            connection.execute(
                "UPDATE auth_memberships SET state='revoked',version=version+1 WHERE tenant_id=? AND workspace_id=? AND user_id=?",
                (self.session.tenant_id, "workspace-001", self.session.user_id),
            )
            connection.execute(
                "UPDATE auth_tenant_roles SET state='revoked',version=version+1 WHERE tenant_id=? AND user_id=?",
                (self.session.tenant_id, self.session.user_id),
            )
        denied = await self.client.get("/api/v1/notifications", headers=self.headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "CURRENT_ACCESS_DENIED")

    async def test_input_plain_text_and_safe_deep_link_allowlists(self) -> None:
        for replacement in (
            {"title": "<script>alert(1)</script>"},
            {"deep_link": "https://evil.example/phish"},
            {"deep_link": "//evil.example/phish"},
            {"deep_link": "sinsan-daon://app/admin"},
        ):
            values = {field: getattr(self.event, field) for field in self.event.__dataclass_fields__}
            values.update(replacement)
            with self.assertRaises(NotificationError):
                self.service.publish(NotificationEvent(**values), candidates=(self.principal,))

    async def test_inbox_is_current_read_projection_without_write_route(self) -> None:
        self.service.project_request(InboxRequest(
            request_id="approval-request-001",
            request_kind="approval",
            status="pending",
            tenant_id=self.session.tenant_id,
            workspace_id="workspace-001",
            actor_id="actor-requester-001",
            due_at=self.clock(),
            resource_type="output_version",
            resource_id="output-version-001",
            deep_link="/inbox?request=approval-request-001",
        ))
        response = await self.client.get("/api/v1/inbox", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["items"][0]["status"], "pending")
        write = await self.client.post(
            "/api/v1/inbox", headers={**self.headers, "Content-Type": "application/json"}, json={}
        )
        self.assertEqual(write.status_code, 405)

    async def test_query_body_and_safe_error_boundaries(self) -> None:
        bad_queries = (
            "limit=201", "filter=unknown:value", "search=%3Cscript%3E", "cursor=forged",
            "recipient_id=other-user",
        )
        for query in bad_queries:
            response = await self.client.get(f"/api/v1/notifications?{query}", headers=self.headers)
            self.assertEqual(response.status_code, 400, query)
            self.assertNotIn(str(self.db_path), response.text)
            self.assertNotIn(self.session.access_token, response.text)


if __name__ == "__main__":
    unittest.main()
