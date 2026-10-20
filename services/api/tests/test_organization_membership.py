from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
from unittest.mock import patch
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from daon_user_api.organization_membership import (
    InvitationState,
    MembershipState,
    OrganizationWorkflowError,
    RequestState,
    SqliteOrganizationRepository,
    _iso,
    _parse,
    _utc,
)


class OrganizationTimestampBoundaryTests(unittest.TestCase):
    def test_pg_aware_datetime_and_existing_iso_preserve_the_same_utc_instant(self) -> None:
        expected = datetime(2026, 10, 17, 3, 4, 5, 123456, tzinfo=timezone.utc)
        for value in (
            expected,
            datetime(2026, 10, 17, 12, 4, 5, 123456, tzinfo=timezone(timedelta(hours=9))),
            "2026-10-17T03:04:05.123456Z",
            "2026-10-17T12:04:05.123456+09:00",
            "2026-10-16T22:04:05.123456-05:00",
        ):
            with self.subTest(value=value):
                parsed = _parse(value)
                self.assertEqual(parsed, expected)
                self.assertIs(parsed.tzinfo, timezone.utc)
                self.assertEqual(_iso(parsed), "2026-10-17T03:04:05.123456+00:00")

    def test_naive_and_invalid_database_timestamps_are_safe_errors(self) -> None:
        class NoOffset(tzinfo):
            def utcoffset(self, _value):
                return None

        for value in (
            datetime(2026, 10, 17, 12),
            datetime(2026, 10, 17, 12, tzinfo=NoOffset()),
            "2026-10-17T12:00:00", "2026-10-17", "2026-02-30T12:00:00Z",
            "not-a-time", "", None, 1720000000, {},
        ):
            with self.subTest(value=value):
                with self.assertRaises(OrganizationWorkflowError) as error:
                    _parse(value)
                self.assertEqual(error.exception.code, "INVALID_TIME")
                self.assertEqual(error.exception.status, 400)

    def test_utc_input_boundary_rejects_tzinfo_without_an_offset(self) -> None:
        class NoOffset(tzinfo):
            def utcoffset(self, _value):
                return None

        for convert in (_utc, _iso):
            with self.subTest(convert=convert.__name__):
                with self.assertRaises(OrganizationWorkflowError) as error:
                    convert(datetime(2026, 10, 17, 12, tzinfo=NoOffset()))
                self.assertEqual(error.exception.code, "INVALID_TIME")


class OrganizationMembershipRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)

    def test_creation_request_is_pending_and_duplicate_pending_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SqliteOrganizationRepository(Path(directory) / "org.sqlite3")
            request = repository.create_organization_request(
                applicant_user_id="user-1", organization_name="Daon", organization_identifier="daon", now=self.now
            )
            self.assertEqual(request.state, RequestState.PENDING)
            with self.assertRaises(OrganizationWorkflowError) as error:
                repository.create_organization_request(
                    applicant_user_id="user-1", organization_name="Other", organization_identifier="other", now=self.now
                )
            self.assertEqual(error.exception.code, "PERSISTENCE_CONFLICT")
            approved = repository.decide_organization_request(
                request_id=request.request_id, actor_id="system-1", approved=True,
                expected_version=1, reason=None, now=self.now + timedelta(minutes=1)
            )
            self.assertEqual(approved.state, RequestState.APPROVED)

    def test_invitation_is_digest_only_and_single_use_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SqliteOrganizationRepository(Path(directory) / "org.sqlite3")
            invitation = repository.create_invitation(
                tenant_id="tenant-a", created_by="admin-a", code="invite-secret",
                expires_at=self.now + timedelta(days=1), max_uses=1, now=self.now
            )
            self.assertNotEqual(invitation.code_digest, "invite-secret")
            revoked = repository.revoke_invitation(
                tenant_id="tenant-a", invitation_id=invitation.invitation_id,
                actor_id="admin-a", expected_version=1, now=self.now
            )
            self.assertEqual(revoked.state, InvitationState.REVOKED)
            invitation = repository.create_invitation(
                tenant_id="tenant-a", created_by="admin-a", code="invite-secret-2",
                expires_at=self.now + timedelta(days=1), max_uses=1, now=self.now
            )
            request = repository.create_join_request(
                tenant_id="tenant-a", user_id="user-b", invitation_code="invite-secret-2", now=self.now
            )
            decided = repository.decide_join_request(
                request_id=request.request_id, actor_id="admin-a", approved=True,
                expected_version=1, reason=None, role="viewer", now=self.now
            )
            self.assertEqual(decided.state, RequestState.APPROVED)
            self.assertEqual(repository.list_members("tenant-a")[0].state, MembershipState.ACTIVE)
            self.assertEqual(repository.list_members("tenant-b"), ())
            with self.assertRaises(OrganizationWorkflowError) as error:
                repository.create_join_request(
                    tenant_id="tenant-a", user_id="user-c", invitation_code="invite-secret", now=self.now
                )
            self.assertEqual(error.exception.code, "INVITATION_INVALID")

    def test_decision_requires_expected_version_and_suspension_records_new_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SqliteOrganizationRepository(Path(directory) / "org.sqlite3")
            request = repository.create_join_request(tenant_id="tenant-a", user_id="user-b", now=self.now)
            with self.assertRaises(OrganizationWorkflowError) as error:
                repository.decide_join_request(
                    request_id=request.request_id, actor_id="admin-a", approved=True,
                    expected_version=2, reason=None, role="viewer", now=self.now
                )
            self.assertEqual(error.exception.code, "REQUEST_CONFLICT")
            approved = repository.decide_join_request(
                request_id=request.request_id, actor_id="admin-a", approved=True,
                expected_version=1, reason=None, role="viewer", now=self.now
            )
            member = repository.set_membership(
                tenant_id="tenant-a", user_id="user-b", actor_id="admin-a",
                state=MembershipState.SUSPENDED, expected_version=1,
                reason="관리자 중지", now=self.now + timedelta(minutes=1)
            )
            self.assertEqual(approved.state, RequestState.APPROVED)
            self.assertEqual(member.state, MembershipState.SUSPENDED)
            self.assertEqual(member.version, 2)

    def test_join_request_can_resolve_tenant_from_invitation_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SqliteOrganizationRepository(Path(directory) / "org.sqlite3")
            repository.create_invitation(
                tenant_id="tenant-code", created_by="admin-code", code="AAAAA-TEST-2026",
                expires_at=self.now + timedelta(days=1), max_uses=1, now=self.now,
            )
            request = repository.create_join_request_by_invitation(
                user_id="user-code", invitation_code="AAAAA-TEST-2026", now=self.now,
            )
            self.assertEqual(request.tenant_id, "tenant-code")
            self.assertEqual(request.invitation_id is not None, True)

    def test_state_and_role_changes_emit_audit_contracts(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as directory:
            repository = SqliteOrganizationRepository(Path(directory) / "org.sqlite3", audit_sink=events.append)
            request = repository.create_join_request(tenant_id="tenant-a", user_id="user-b", now=self.now)
            repository.decide_join_request(
                request_id=request.request_id, actor_id="admin-a", approved=True,
                expected_version=1, reason=None, role="viewer", now=self.now
            )
            repository.change_role(
                tenant_id="tenant-a", user_id="user-b", actor_id="admin-a", role="editor",
                expected_version=1, reason="업무 범위 변경", now=self.now
            )
            repository.set_membership(
                tenant_id="tenant-a", user_id="user-b", actor_id="admin-a",
                state=MembershipState.SUSPENDED, expected_version=2,
                reason="중지", now=self.now
            )
        self.assertEqual([event.action for event in events], [
            "organization.join_request.decided",
            "organization.membership.role_changed",
            "organization.membership.state_changed",
        ])


class OrganizationManagementOperationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "management.sqlite"
        self.repository = SqliteOrganizationRepository(self.path)
        self.now = datetime.now(timezone.utc)
        self.payload = {"code": "l2-synthetic-secret", "expires_at": self.now+timedelta(days=1), "max_uses": 1}

    def prepare_join_access(self):
        from daon_user_api.identity import SqliteIdentityRepository
        from daon_user_api.authorization import SqliteAuthorizationRepository
        identity = SqliteIdentityRepository(self.path)
        auth = SqliteAuthorizationRepository(self.path)
        with self.repository.transaction() as connection:
            connection.execute("INSERT INTO tenants VALUES ('tenant-a')")
            connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,email_verified_at,state) VALUES ('user-a','local','l2-j1-user','l2-j1-user','l2-j1@example.test',?,'active')", (self.now.isoformat(),))
            connection.execute("INSERT INTO auth_workspaces VALUES ('workspace-a','tenant-a','organization','cloud_sync',1000,1,1,?)", (self.now.isoformat(),))
        identity.close(); auth.close()
        self.payload["workspace_id"] = "workspace-a"

    def execute(self, *, key="l2-invitation-create-0001", payload=None):
        selected = self.payload if payload is None else payload
        fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True, default=str).encode()).hexdigest()
        return self.repository.execute_management_operation(operation="organization.invitation.create", actor_id="admin-a", tenant_id="tenant-a", target_id=None,
            payload=selected, idempotency_key=key, request_fingerprint=fingerprint, trace_id="l2-trace", policy_version="l2-policy")

    def test_l2_success_and_exact_replay_write_one_receipt_and_audit_intent(self):
        result = self.execute()
        replay = self.execute()
        self.assertFalse(result.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(result.value, replay.value)
        self.assertEqual(set(result.value), {"invitation_id", "tenant_id", "workspace_id", "created_by", "expires_at", "max_uses", "used_count", "state", "version", "created_at", "updated_at"})
        with self.repository.transaction() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM invitation_codes").fetchone()[0], 1)
            rows = connection.execute("SELECT * FROM organization_operation_outbox").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["delivered_at"])
            self.assertNotIn("l2-synthetic-secret", str(dict(rows[0])))
            self.assertNotIn("code_digest", rows[0]["response_json"])
        with self.assertRaises(OrganizationWorkflowError) as error:
            self.execute(payload=self.payload | {"max_uses": 2})
        self.assertEqual(error.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_l2_business_dto_and_receipt_failures_leave_no_partial_mutation(self):
        for boundary in ("_parse", "_management_view", "_record_management_operation"):
            with self.subTest(boundary=boundary):
                target = "daon_user_api.organization_membership."+boundary if boundary == "_parse" else self.repository
                context = patch(target, side_effect=OrganizationWorkflowError("INJECTED_FAILURE", 503)) if boundary == "_parse" else patch.object(target, boundary, side_effect=OrganizationWorkflowError("INJECTED_FAILURE", 503))
                with context, self.assertRaises(OrganizationWorkflowError): self.execute()
                with self.repository.transaction() as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM invitation_codes").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0], 0)

    def test_l2_legacy_exact_replay_is_projected_without_new_history_or_audit(self):
        from dataclasses import asdict
        invitation = self.repository.create_invitation(tenant_id="tenant-a", created_by="admin-a", now=self.now, **self.payload)
        value = {key: item.value if hasattr(item,"value") else item.isoformat() if isinstance(item,datetime) else item for key,item in asdict(invitation).items()}
        fingerprint = hashlib.sha256(json.dumps(self.payload, sort_keys=True, default=str).encode()).hexdigest()
        self.repository.idempotency_record(operation="organization.invitation.create", actor_id="admin-a", key="l2-invitation-create-0001", fingerprint=fingerprint, result=value, now=self.now)
        replay = self.execute()
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.value["invitation_id"], invitation.invitation_id)
        self.assertNotIn("code_digest", replay.value)
        with self.repository.transaction() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM invitation_codes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM organization_idempotency").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0], 0)

    def test_l2_audit_ack_failure_and_restart_do_not_duplicate_the_sink(self):
        from daon_user_api.audit import AuditEventStore
        store = AuditEventStore()
        self.repository._audit_store = store
        self.execute()
        with patch.object(self.repository, "_ack_management_audit", side_effect=OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)):
            self.assertEqual(self.repository.dispatch_pending_management_audits(), 0)
        with self.repository.transaction() as connection:
            row = connection.execute("SELECT event_id,delivered_at FROM organization_operation_outbox").fetchone()
            self.assertIsNone(row["delivered_at"])
            self.assertIsNotNone(store.read(row["event_id"]))
        restarted = SqliteOrganizationRepository(self.path, audit_store=store)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.dispatch_pending_management_audits(), 1)
        self.assertEqual(restarted.dispatch_pending_management_audits(), 0)
        self.assertEqual(len(store.list(tenant_id="tenant-a").items), 1)

    def test_l2_audit_outage_stop_and_mismatched_duplicate_remain_pending(self):
        from threading import Event
        from dataclasses import replace
        from daon_user_api.audit import AuditEventStore
        store = AuditEventStore()
        self.repository._audit_store = store
        self.execute()
        stop = Event(); stop.set()
        self.assertEqual(self.repository.dispatch_pending_management_audits(stop=stop), 0)
        with patch.object(store, "append", side_effect=OSError("synthetic outage")):
            self.assertEqual(self.repository.dispatch_pending_management_audits(), 0)
        with patch.object(self.repository, "_ack_management_audit", side_effect=OSError("synthetic ack outage")):
            self.assertEqual(self.repository.dispatch_pending_management_audits(), 0)
        with self.repository.transaction() as connection:
            row = connection.execute("SELECT event_id FROM organization_operation_outbox").fetchone()
        existing = store.read(row["event_id"])
        with patch.object(store, "read", return_value=replace(existing, tenant_id="tenant-other")):
            self.assertEqual(self.repository.dispatch_pending_management_audits(), 0)
        self.assertEqual(self.repository.dispatch_pending_management_audits(), 1)

    def test_l2_all_six_operations_replay_and_stale_changes_leave_history_unchanged(self):
        self.prepare_join_access()
        from daon_user_api.audit import AuditEventStore
        store = AuditEventStore()
        self.repository._audit_store = store
        def operation(name, target, payload, actor="admin-a"):
            return self.repository.execute_management_operation(
                operation="organization."+name, actor_id=actor, tenant_id="tenant-a", target_id=target,
                payload=payload, idempotency_key="s39-six-operations-"+name,
                request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                trace_id="six-trace", policy_version="six-policy")
        invitation = self.execute()
        joined = operation("join.create", None, {"invitation_code": self.payload["code"]}, actor="user-a")
        approved = operation("join.decide", joined.value["request_id"], {"approved": True, "expected_version": 1, "role": "member"})
        role_payload = {"role": "viewer", "workspace_id": "workspace-a", "expected_version": 1, "expected_auth_version": 1, "reason": "업무 검토"}
        with self.repository.transaction() as connection:
            history = [tuple(row) for row in connection.execute("SELECT * FROM tenant_membership_role_history")]
        role = operation("member.role", "user-a", role_payload)
        with self.repository.transaction() as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM tenant_membership_role_history")], history)
        state = operation("member.state", "user-a", {"active": False, "expected_version": 2, "reason": "중지"})
        revoked = operation("invitation.revoke", invitation.value["invitation_id"], {"expected_version": 2})
        for name, target, payload, result, actor in (
            ("join.create", None, {"invitation_code": self.payload["code"]}, joined, "user-a"),
            ("join.decide", joined.value["request_id"], {"approved": True, "expected_version": 1, "role": "member"}, approved, "admin-a"),
            ("member.role", "user-a", role_payload, role, "admin-a"),
            ("member.state", "user-a", {"active": False, "expected_version": 2, "reason": "중지"}, state, "admin-a"),
            ("invitation.revoke", invitation.value["invitation_id"], {"expected_version": 2}, revoked, "admin-a"),
        ):
            replay = operation(name, target, payload, actor)
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.value, result.value)
        with self.repository.transaction() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0], 6)
            self.assertEqual(connection.execute("SELECT used_count FROM invitation_codes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM tenant_membership_role_history").fetchone()[0], 2)
        self.assertEqual(self.repository.dispatch_pending_management_audits(), 6)
        self.assertEqual(len(store.list(tenant_id="tenant-a").items), 6)

    def test_l2_shared_connection_and_post_insert_failure_roll_back_every_stage(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic outer failure"):
            with self.repository.transaction() as connection:
                with patch.object(self.repository, "_connect", side_effect=AssertionError("중첩 연결 금지")):
                    self.repository.create_invitation(tenant_id="tenant-a", created_by="admin-a", now=self.now,
                                                     connection=connection, **self.payload)
                    self.assertEqual(connection.execute("SELECT count(*) FROM invitation_codes").fetchone()[0], 1)
                raise RuntimeError("synthetic outer failure")
        record = self.repository._record_management_operation
        def fail_after_insert(connection, **values):
            record(connection, **values)
            self.assertEqual(connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0], 1)
            raise OrganizationWorkflowError("AUDIT_INTENT_STORAGE_FAILED", 503)
        with patch.object(self.repository, "_record_management_operation", fail_after_insert), self.assertRaises(OrganizationWorkflowError):
            self.execute()
        with self.repository.transaction() as connection:
            for table in ("organization_operation_outbox", "invitation_codes", "tenant_membership_role_history"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_l2_legacy_key_conflict_is_not_silently_accepted(self):
        arguments = dict(operation="organization.invitation.create", actor_id="admin-a", key="l2-legacy-conflict-0001", fingerprint="a"*64, result={"value": 1}, now=self.now)
        self.repository.idempotency_record(**arguments)
        self.repository.idempotency_record(**arguments)
        for replacement in ({"fingerprint": "b"*64}, {"result": {"value": 2}}):
            with self.subTest(replacement=replacement), self.assertRaises(OrganizationWorkflowError) as error:
                self.repository.idempotency_record(**(arguments | replacement))
            self.assertEqual(error.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_l2_locked_target_snapshot_is_reloaded_before_business_and_audit(self):
        self.prepare_join_access()
        self.execute()
        request = self.repository.create_join_request(tenant_id="tenant-a", user_id="user-a", invitation_code=self.payload["code"], now=self.now)
        self.repository.execute_management_operation(operation="organization.join.decide", actor_id="admin-a", tenant_id="tenant-a", target_id=request.request_id,
            payload={"approved": True, "expected_version": 1}, idempotency_key="l2-locked-approval-0001", request_fingerprint="a"*64, trace_id="locked-trace", policy_version="locked-policy")
        def concurrent_winner(connection, *_args):
            connection.execute("UPDATE tenant_memberships SET version=2 WHERE tenant_id='tenant-a' AND user_id='user-a'")
            connection.execute("UPDATE auth_memberships SET role='reviewer',version=2 WHERE tenant_id='tenant-a' AND user_id='user-a'")
        with patch.object(self.repository, "_lock_management_target", concurrent_winner):
            self.repository.execute_management_operation(operation="organization.member.role", actor_id="admin-a", tenant_id="tenant-a", target_id="user-a",
                payload={"role": "editor", "workspace_id": "workspace-a", "expected_version": 2, "expected_auth_version": 2}, idempotency_key="l2-locked-snapshot-0001", request_fingerprint="c"*64,
                trace_id="locked-trace", policy_version="locked-policy")
        with self.repository.transaction() as connection:
            audit = json.loads(connection.execute("SELECT audit_json FROM organization_operation_outbox WHERE operation='organization.member.role'").fetchone()[0])
            self.assertEqual(audit["before"], "reviewer")

    def test_l2_central_audit_validation_failure_rolls_back_before_commit(self):
        cases = ({"trace_id": "t"*129}, {"policy_version": "p"*129}, {"actor_id": "a"*129},
                 {"tenant_id": "n"*129}, {"payload": self.payload | {"reason": "http://127.0.0.1"}})
        for index, invalid in enumerate(cases):
            with self.subTest(invalid_field=next(iter(invalid))):
                repository = SqliteOrganizationRepository(self.path.with_name(f"invalid-audit-{index}.sqlite"))
                arguments = dict(operation="organization.invitation.create", actor_id="admin-a", tenant_id="tenant-a", target_id=None,
                    payload=self.payload, idempotency_key="l2-invalid-audit-0001", request_fingerprint="d"*64,
                    trace_id="safe-trace", policy_version="safe-policy")
                with self.assertRaises(OrganizationWorkflowError) as error:
                    repository.execute_management_operation(**(arguments | invalid))
                self.assertEqual(error.exception.code, "AUDIT_INTENT_INVALID")
                with repository.transaction() as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM invitation_codes").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0], 0)

    def test_l2_stale_approved_request_fails_without_a_second_receipt_or_history(self):
        request = self.repository.create_join_request(tenant_id="tenant-a", user_id="user-a", now=self.now)
        self.repository.decide_join_request(request_id=request.request_id, actor_id="admin-a", approved=True,
            expected_version=1, reason=None, role="member", now=self.now)
        with self.assertRaises(OrganizationWorkflowError) as error:
            self.repository.execute_management_operation(operation="organization.join.decide", actor_id="admin-a", tenant_id="tenant-a", target_id=request.request_id,
                payload={"approved": True, "expected_version": 2}, idempotency_key="l2-stale-request-0001", request_fingerprint="f"*64,
                trace_id="stale-trace", policy_version="stale-policy")
        self.assertEqual(error.exception.code, "REQUEST_CONFLICT")
        with self.repository.transaction() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM organization_operation_outbox").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM tenant_membership_role_history").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
