from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx

from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, native_login
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Role, SqliteAuthorizationRepository, AuthorizationService
from daon_user_api.identity import IdentityPrincipal
from daon_user_api.notification import (
    DeliveryState,
    InboxRequest,
    NotificationError,
    NotificationEvent,
    NotificationService,
    ReferenceNotificationRepository,
    inbox_json,
)
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, create_app


class BarrierNotificationRepository(ReferenceNotificationRepository):
    """Forces legacy split-lock reads to overlap and atomic calls to start together."""

    def __init__(self) -> None:
        super().__init__()
        self._legacy_idempotency_barrier: Barrier | None = None
        self._legacy_notification_barrier: Barrier | None = None
        self._atomic_start_barrier: Barrier | None = None

    def arm_race(self, parties: int) -> None:
        self._legacy_idempotency_barrier = Barrier(parties)
        self._legacy_notification_barrier = Barrier(parties)
        self._atomic_start_barrier = Barrier(parties)

    def disarm_race(self) -> None:
        self._legacy_idempotency_barrier = None
        self._legacy_notification_barrier = None
        self._atomic_start_barrier = None

    def idempotency_result(self, key: str):  # type: ignore[no-untyped-def]
        result = super().idempotency_result(key)
        barrier = self._legacy_idempotency_barrier
        if barrier is not None:
            barrier.wait(timeout=5)
        return result

    def notification_by_id(self, notification_id: str):  # type: ignore[no-untyped-def]
        result = super().notification_by_id(notification_id)
        barrier = self._legacy_notification_barrier
        if barrier is not None:
            barrier.wait(timeout=5)
        return result

    def transition_read(self, **kwargs):  # type: ignore[no-untyped-def]
        barrier = self._atomic_start_barrier
        if barrier is not None:
            barrier.wait(timeout=5)
        return super().transition_read(**kwargs)


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
        self.repository = BarrierNotificationRepository()
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

    async def test_approved_notification_target_links_survive_publish_list_and_detail(self) -> None:
        """승인된 원업무 경로를 홈 이동으로 바꾸거나 숨기지 않고 그대로 반환한다."""
        from dataclasses import replace
        links = (
            "/notebooks/notebook-001?output_version_id=version-001",
            "/notebooks/notebook-001?source_version_id=source-version-001",
            "/notebooks/notebook-001?run_id=run-001",
            "/notebooks/notebook-001?studio_job_id=job-001",
            "/settings/account",
        )
        for index, link in enumerate(links):
            with self.subTest(link=link):
                item, = self.service.publish(replace(self.event,
                    event_id=f"event-s42-target-{index}", deep_link=link), candidates=(self.principal,))
                listed = self.service.list_notifications(principal=self.principal, limit=20, cursor=None,
                    filters={}, search=None, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                self.assertEqual(next(value.deep_link for value in listed.items
                    if value.notification_id == item.notification_id), link)
                detail = self.service.get_notification(principal=self.principal,
                    notification_id=item.notification_id, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                self.assertEqual(detail.deep_link, link)

    async def test_notification_target_links_reject_ambiguous_or_unapproved_selection(self) -> None:
        """새 복귀 경로는 정확한 단일 대상만 허용하고 임의 redirect/오염을 거부한다."""
        from dataclasses import replace
        links = (
            "/notebooks/notebook-001",
            "/notebooks/notebook-001?run_id=run-001&run_id=run-002",
            "/notebooks/notebook-001?run_id=run-001&source_version_id=version-001",
            "/notebooks/notebook-001?run_id=run-001&redirect=https://evil.example",
            "/notebooks/notebook-001?unknown=run-001",
            "/notebooks/notebook-001?run_id=",
            "/notebooks/notebook-001?run_id=run-001#other",
            "/notebooks/notebook-001?run_id=run%0A001",
            "/notebooks/notebook-001?studio_job_id=job-001&studio_job_id=job-002",
            "/notebooks/notebook-001?studio_job_id=job-001&run_id=run-001",
            "/notebooks/notebook-001?studio_job_id=",
            "/notebooks/notebook-001?studio_job_id=job%0A001",
            "/notebooks/notebook-001%2Fother?run_id=run-001",
            "/notebooks/../settings?run_id=run-001",
            "/settings/account?redirect=https://evil.example",
            "/settings/account#other",
            "/settings/account/other",
        )
        for link in links:
            with self.subTest(link=link):
                with self.assertRaises(NotificationError) as rejected:
                    self.service.publish(replace(self.event, deep_link=link), candidates=(self.principal,))
                self.assertEqual(rejected.exception.code, "UNSAFE_DEEP_LINK")

    async def test_s43_authorization_database_failure_is_not_an_empty_inbox(self) -> None:
        from unittest.mock import patch
        from daon_user_api.authorization import AuthorizationError
        self.service.project_request(InboxRequest(
            request_id="s43-approval-request", request_kind="approval", status="pending",
            tenant_id=self.session.tenant_id, workspace_id="workspace-001",
            recipient_id=self.session.user_id, actor_id="actor-requester-001", due_at=None,
            resource_type="output_version", resource_id="s43-output-version",
            deep_link="/notebooks/s43-notebook?output_version_id=s43-output-version",
        ))
        with patch.object(self.authorization, "authorize_action",
                          side_effect=AuthorizationError("DATABASE_UNAVAILABLE", 503)):
            response = await self.client.get("/api/v1/inbox", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        # 기존 공개 오류 mapper는 내부 DB 원인을 RESOURCE_UNAVAILABLE로 숨긴다.
        # 새 공개 code를 만들지 않고 503/재시도 가능·빈 목록 아님을 검사한다.
        self.assertEqual(response.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
        self.assertIs(response.json()["error"]["retryable"], True)
        self.assertEqual(response.json()["error"]["details"], {})

    async def test_s42_list_authorization_database_outage_is_not_empty_success(self) -> None:
        from unittest.mock import patch
        from daon_user_api.authorization import AuthorizationError

        baseline = await self.client.get("/api/v1/notifications", headers=self.headers)
        self.assertEqual(baseline.status_code, 200)
        self.assertEqual(len(baseline.json()["data"]["items"]), 1)
        self.assertEqual(baseline.json()["data"]["unread_count"], 1)
        before = self.repository.all_notifications()
        authorize = self.authorization.authorize_action
        # 목록과 미읽음 개수는 각각 현재 접근을 확인한다. 두 번째 검사 실패도 부분 200이 아니다.
        for failure_at in (1, 2):
            with self.subTest(failure_at=failure_at):
                calls = 0

                def fail_current_access(**kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failure_at:
                        raise AuthorizationError("DATABASE_UNAVAILABLE", 503)
                    return authorize(**kwargs)

                with patch.object(self.authorization, "authorize_action", side_effect=fail_current_access):
                    response = await self.client.get("/api/v1/notifications", headers=self.headers)
                self.assertEqual(calls, failure_at)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
                self.assertIs(response.json()["error"]["retryable"], True)
                self.assertEqual(response.json()["error"]["details"], {})
                self.assertNotIn("data", response.json())
                self.assertEqual(self.repository.all_notifications(), before)
        recovered = await self.client.get("/api/v1/notifications", headers=self.headers)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["data"], baseline.json()["data"])

    async def test_s42_detail_read_and_read_replay_database_outage_preserve_state(self) -> None:
        from unittest.mock import patch
        from daon_user_api.authorization import AuthorizationError

        baseline = await self.client.get("/api/v1/notifications", headers=self.headers)
        self.assertEqual(baseline.status_code, 200)
        item = baseline.json()["data"]["items"][0]
        path = f"/api/v1/notifications/{item['id']}"
        detail = await self.client.get(path, headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        key = "idem-s42-db-outage-read-001"
        headers = {**self.headers, "If-Match": detail.headers["etag"], "Idempotency-Key": key,
                   "X-Trace-Id": "trace-s42-db-outage-read"}
        before = self.repository.all_notifications()
        for method in ("GET", "PATCH"):
            with self.subTest(method=method):
                with patch.object(self.authorization, "authorize_action",
                                  side_effect=AuthorizationError("DATABASE_UNAVAILABLE", 503)):
                    response = await self.client.request(method, path,
                        headers=headers, **({"json": {"state": "read"}} if method == "PATCH" else {}))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
                self.assertIs(response.json()["error"]["retryable"], True)
                self.assertEqual(response.json()["error"]["details"], {})
                self.assertNotIn("data", response.json())
                self.assertEqual(self.repository.all_notifications(), before)
                self.assertIsNone(self.repository.idempotency_result(key))
                self.assertEqual(len(self.audit.list(
                    tenant_id=self.session.tenant_id, action="notification.read").items), 0)

        first = await self.client.patch(path, headers=headers, json={"state": "read"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["data"]["read_state"], "read")
        read_before = self.repository.all_notifications()
        receipt_before = self.repository.idempotency_result(key)
        self.assertIsNotNone(receipt_before)
        with patch.object(self.authorization, "authorize_action",
                          side_effect=AuthorizationError("DATABASE_UNAVAILABLE", 503)):
            replay = await self.client.patch(path, headers=headers, json={"state": "read"})
        self.assertEqual(replay.status_code, 503)
        self.assertEqual(replay.json()["error"]["code"], "RESOURCE_UNAVAILABLE")
        self.assertIs(replay.json()["error"]["retryable"], True)
        self.assertEqual(replay.json()["error"]["details"], {})
        self.assertNotIn("data", replay.json())
        self.assertEqual(self.repository.all_notifications(), read_before)
        self.assertEqual(self.repository.idempotency_result(key), receipt_before)
        self.assertEqual(len(self.audit.list(
            tenant_id=self.session.tenant_id, action="notification.read").items), 1)
        recovered = await self.client.patch(path, headers=headers, json={"state": "read"})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json(), first.json())

    async def test_inbox_is_current_read_projection_without_write_route(self) -> None:
        self.service.project_request(InboxRequest(
            request_id="approval-request-001",
            request_kind="approval",
            status="pending",
            tenant_id=self.session.tenant_id,
            workspace_id="workspace-001",
            recipient_id=self.session.user_id,
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

    async def test_inbox_requires_recipient_and_request_action_authorization(self) -> None:
        reviewer = IdentityPrincipal("reviewer-001", "session-reviewer-001", "device-reviewer-001", self.session.tenant_id)
        approver = IdentityPrincipal("approver-001", "session-approver-001", "device-approver-001", self.session.tenant_id)
        viewer = IdentityPrincipal("viewer-001", "session-viewer-001", "device-viewer-001", self.session.tenant_id)
        foreign = IdentityPrincipal("reviewer-001", "session-reviewer-foreign", "device-reviewer-foreign", "tenant-foreign")
        for member, role in ((reviewer, Role.REVIEWER), (approver, Role.APPROVER), (viewer, Role.VIEWER)):
            self.authorization.set_membership(
                principal=self.principal, workspace_id="workspace-001", user_id=member.user_id,
                role=role, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
            )
        for kind in ("review", "approval", "delivery"):
            self.service.project_request(InboxRequest(
                request_id=f"{kind}-request-reviewer",
                request_kind=kind,
                status="pending",
                tenant_id=self.session.tenant_id,
                workspace_id="workspace-001",
                recipient_id=reviewer.user_id,
                actor_id="actor-requester-001",
                due_at=self.clock(),
                resource_type="output_version",
                resource_id=f"output-{kind}-001",
                deep_link=f"/inbox?request={kind}-request-reviewer",
            ))
        for kind in ("approval", "delivery"):
            self.service.project_request(InboxRequest(
                request_id=f"{kind}-request-approver",
                request_kind=kind,
                status="pending",
                tenant_id=self.session.tenant_id,
                workspace_id="workspace-001",
                recipient_id=approver.user_id,
                actor_id="actor-requester-001",
                due_at=self.clock(),
                resource_type="output_version",
                resource_id=f"output-{kind}-approver",
                deep_link=f"/inbox?request={kind}-request-approver",
            ))
        self.service.project_request(InboxRequest(
            request_id="approval-request-viewer",
            request_kind="approval",
            status="pending",
            tenant_id=self.session.tenant_id,
            workspace_id="workspace-001",
            recipient_id=viewer.user_id,
            actor_id="actor-requester-001",
            due_at=self.clock(),
            resource_type="output_version",
            resource_id="output-approval-viewer",
            deep_link="/inbox?request=approval-request-viewer",
        ))

        reviewer_page = self.service.list_inbox(
            principal=reviewer, limit=50, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        self.assertEqual([item.request_kind for item in reviewer_page.items], ["review"])
        approver_page = self.service.list_inbox(
            principal=approver, limit=50, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        self.assertEqual(
            {item.request_kind for item in approver_page.items}, {"approval", "delivery"}
        )
        self.assertEqual(self.service.list_inbox(
            principal=viewer, limit=50, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        ).items, ())
        self.assertEqual(self.service.list_inbox(
            principal=foreign, limit=50, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        ).items, ())
        self.assertNotIn("recipient_id", inbox_json(reviewer_page.items[0]))

        invalid = {field: getattr(reviewer_page.items[0], field) for field in reviewer_page.items[0].__dataclass_fields__}
        invalid["request_id"] = "review-request-invalid-recipient"
        invalid["recipient_id"] = "../other-user"
        with self.assertRaises(NotificationError):
            self.service.project_request(InboxRequest(**invalid))

        self.authorization.set_membership(
            principal=self.principal, workspace_id="workspace-001", user_id=reviewer.user_id,
            role=Role.VIEWER, expected_version=1, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        self.assertEqual(self.service.list_inbox(
            principal=reviewer, limit=50, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        ).items, ())

    async def test_concurrent_same_key_read_transitions_and_audits_exactly_once(self) -> None:
        item = self.service.list_notifications(
            principal=self.principal, limit=10, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        ).items[0]
        self.repository.arm_race(4)

        def mark() -> object:
            return self.service.mark_read(
                principal=self.principal, notification_id=item.notification_id,
                expected_etag=item.etag, idempotency_key="idem-concurrent-same-001",
                requested_state="read", trace_id="trace-concurrent-same",
                policy_version=POLICY_VERSION,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = tuple(executor.map(lambda _: mark(), range(4)))
        self.assertEqual({result.version for result in results}, {2})
        self.assertEqual({result.read_at for result in results}, {results[0].read_at})
        self.assertEqual(len(self.audit.list(
            tenant_id=self.session.tenant_id, action="notification.read"
        ).items), 1)

        self.repository.disarm_race()
        with self.assertRaises(NotificationError) as conflict:
            self.service.mark_read(
                principal=self.principal, notification_id=item.notification_id,
                expected_etag='"notification-2"', idempotency_key="idem-concurrent-same-001",
                requested_state="read", trace_id="trace-concurrent-same-conflict",
                policy_version=POLICY_VERSION,
            )
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")

    async def test_concurrent_different_keys_same_etag_has_one_winner(self) -> None:
        item = self.service.list_notifications(
            principal=self.principal, limit=10, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        ).items[0]
        self.repository.arm_race(4)

        def mark(index: int) -> tuple[str, object]:
            try:
                result = self.service.mark_read(
                    principal=self.principal, notification_id=item.notification_id,
                    expected_etag=item.etag, idempotency_key=f"idem-concurrent-different-{index:03d}",
                    requested_state="read", trace_id=f"trace-concurrent-different-{index}",
                    policy_version=POLICY_VERSION,
                )
                return "success", result
            except NotificationError as error:
                return "error", error

        with ThreadPoolExecutor(max_workers=4) as executor:
            outcomes = tuple(executor.map(mark, range(4)))
        successes = tuple(value for status, value in outcomes if status == "success")
        failures = tuple(value for status, value in outcomes if status == "error")
        self.assertEqual(len(successes), 1)
        self.assertEqual({error.code for error in failures}, {"VERSION_CONFLICT"})
        self.assertEqual(len(self.audit.list(
            tenant_id=self.session.tenant_id, action="notification.read"
        ).items), 1)

    async def test_audit_failure_does_not_commit_read_or_idempotency_state(self) -> None:
        item = self.service.list_notifications(
            principal=self.principal, limit=10, cursor=None, filters={}, search=None,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        ).items[0]
        original_append = self.audit.append

        def fail_read(draft):  # type: ignore[no-untyped-def]
            if draft.action == "notification.read":
                raise RuntimeError("forced audit failure")
            return original_append(draft)

        self.audit.append = fail_read  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "forced audit failure"):
            self.service.mark_read(
                principal=self.principal, notification_id=item.notification_id,
                expected_etag=item.etag, idempotency_key="idem-audit-failure-001",
                requested_state="read", trace_id="trace-audit-failure",
                policy_version=POLICY_VERSION,
            )
        self.audit.append = original_append  # type: ignore[method-assign]
        unchanged = self.service.get_notification(
            principal=self.principal, notification_id=item.notification_id,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        self.assertEqual((unchanged.read_at, unchanged.version), (None, 1))
        recovered = self.service.mark_read(
            principal=self.principal, notification_id=item.notification_id,
            expected_etag=item.etag, idempotency_key="idem-audit-failure-001",
            requested_state="read", trace_id="trace-audit-failure-retry",
            policy_version=POLICY_VERSION,
        )
        self.assertEqual(recovered.version, 2)

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
