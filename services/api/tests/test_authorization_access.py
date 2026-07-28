from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_authorization_support import FixedClock, POLICY_VERSION, TRACE_ID, SelectiveFailAuditStore, principal
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import (
    AccessAction,
    AccessState,
    AuthorizationError,
    AuthorizationService,
    EvidenceDependency,
    HistoricalResultDescriptor,
    Role,
    SqliteAuthorizationRepository,
)


class AuthorizationHistoricalAccessTests(unittest.TestCase):
    def make_service(self, path: Path, audit_store=None):
        owner = principal("owner")
        repository = SqliteAuthorizationRepository(path)
        repository.bootstrap_workspace(tenant_id=owner.tenant_id, workspace_id="workspace-001", owner_user_id=owner.user_id, owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=2500, now=FixedClock()())
        audit = audit_store or AuditEventStore()
        service = AuthorizationService(repository=repository, audit_store=audit, clock=FixedClock())
        viewer = principal("viewer")
        service.set_membership(principal=owner, workspace_id="workspace-001", user_id=viewer.user_id, role=Role.ORGANIZATION_ADMIN, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        return service, repository, audit, owner, viewer

    def descriptor(self, result_id: str = "output-001", *, unsafe=False, decisive=False):
        return HistoricalResultDescriptor(
            result_id=result_id, result_kind="output", tenant_id="tenant-001", workspace_id="workspace-001",
            source_version_ids=("source-version-allowed", "source-version-revoked"),
            evidence_reference_ids=("evidence-allowed", "evidence-revoked"),
            dependencies=(
                EvidenceDependency("evidence-allowed", "source-version-allowed", ("segment-allowed",), False, True),
                EvidenceDependency("evidence-revoked", "source-version-revoked", ("segment-revoked",), decisive, not unsafe),
            ),
            original_policy_version="historical-policy-v1",
            original_membership_version=1,
        )

    def test_available_partial_and_blocked_mask_matrix_uses_current_source_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, owner, viewer = self.make_service(Path(directory) / "auth.sqlite3")
            service.register_historical_result(principal=owner, descriptor=self.descriptor(), trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            available = service.evaluate_historical_access(principal=viewer, result_id="output-001", action=AccessAction.READ, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(available.state, AccessState.AVAILABLE)
            service.set_source_access(principal=owner, workspace_id="workspace-001", user_id=viewer.user_id, source_version_id="source-version-revoked", allowed=False, expected_version=0, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            partial = service.evaluate_historical_access(principal=viewer, result_id="output-001", action=AccessAction.CITATION, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(partial.state, AccessState.PARTIALLY_REDACTED)
            self.assertEqual(partial.masked_reference_ids, ("evidence-revoked",))
            self.assertEqual(partial.masked_segment_ids, ("segment-revoked",))

            service.register_historical_result(principal=owner, descriptor=self.descriptor("output-blocked", decisive=True), trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            blocked = service.evaluate_historical_access(principal=viewer, result_id="output-blocked", action=AccessAction.READ, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(blocked.state, AccessState.ACCESS_BLOCKED)
            with self.assertRaises(AuthorizationError) as denied:
                service.require_historical_access(principal=viewer, result_id="output-blocked", action=AccessAction.READ, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(denied.exception.code, "CURRENT_ACCESS_DENIED")

    def test_historical_descriptor_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, repository, _, owner, _ = self.make_service(Path(directory) / "auth.sqlite3")
            original = self.descriptor()
            service.register_historical_result(principal=owner, descriptor=original, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            with self.assertRaises(AuthorizationError) as duplicate:
                service.register_historical_result(principal=owner, descriptor=self.descriptor(), trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(duplicate.exception.code, "HISTORICAL_RESULT_IMMUTABLE")
            self.assertEqual(repository.read_historical_result("tenant-001", "workspace-001", "output-001"), original)

    def test_all_historical_actions_recheck_current_membership_and_acl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, owner, viewer = self.make_service(Path(directory) / "auth.sqlite3")
            service.register_historical_result(principal=owner, descriptor=self.descriptor(), trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            for action in AccessAction:
                decision = service.evaluate_historical_access(principal=viewer, result_id="output-001", action=action, trace_id=f"trace-{action.value}", policy_version=POLICY_VERSION)
                self.assertIn(decision.state, {AccessState.AVAILABLE, AccessState.PARTIALLY_REDACTED})
            service.set_membership(principal=owner, workspace_id="workspace-001", user_id=viewer.user_id, role=Role.VIEWER, expected_version=1, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            for action in (AccessAction.EXPORT, AccessAction.DELIVERY, AccessAction.KNOWLEDGE_REGISTRATION, AccessAction.RERUN):
                decision = service.evaluate_historical_access(principal=viewer, result_id="output-001", action=action, trace_id=f"trace-denied-{action.value}", policy_version=POLICY_VERSION)
                self.assertEqual(decision.state, AccessState.ACCESS_BLOCKED)

    def test_rerun_returns_new_current_snapshot_not_historical_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, _, _, owner, _ = self.make_service(Path(directory) / "auth.sqlite3")
            service.register_historical_result(principal=owner, descriptor=self.descriptor(), trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            first = service.authorize_rerun(principal=owner, result_id="output-001", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            second = service.authorize_rerun(principal=owner, result_id="output-001", trace_id="trace-rerun-002", policy_version=POLICY_VERSION)
            self.assertNotEqual(first.run_request_id, second.run_request_id)
            self.assertEqual(first.snapshot.data_area, "cloud_sync")
            self.assertEqual(first.snapshot.cost_limit_cents, 2500)
            self.assertNotEqual(first.snapshot.policy_version, "historical-policy-v1")

    def test_foreign_and_missing_result_are_safe_and_read_audit_failure_returns_no_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controlled = SelectiveFailAuditStore()
            service, repository, _, owner, viewer = self.make_service(Path(directory) / "auth.sqlite3", audit_store=controlled)
            foreign = HistoricalResultDescriptor(
                result_id="foreign-output", result_kind="output", tenant_id="tenant-002", workspace_id="workspace-foreign",
                source_version_ids=(), evidence_reference_ids=(), dependencies=(), original_policy_version="old", original_membership_version=1,
            )
            repository.bootstrap_workspace(tenant_id="tenant-002", workspace_id="workspace-foreign", owner_user_id="foreign", owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=1, now=FixedClock()())
            repository.insert_historical_result(foreign, FixedClock()())
            failures = []
            for result_id in (foreign.result_id, "missing-output"):
                with self.assertRaises(AuthorizationError) as error:
                    service.evaluate_historical_access(principal=viewer, result_id=result_id, action=AccessAction.READ, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
                failures.append((error.exception.code, error.exception.http_status, str(error.exception)))
            self.assertEqual(failures[0], failures[1])

            service.register_historical_result(principal=owner, descriptor=self.descriptor(), trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            before = repository.access_decision_count()
            controlled.fail_actions.add("authorization.access.decided")
            with self.assertRaises(AuthorizationError) as audit_failed:
                service.evaluate_historical_access(principal=viewer, result_id="output-001", action=AccessAction.READ, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            self.assertEqual(audit_failed.exception.code, "AUDIT_WRITE_FAILED")
            self.assertEqual(repository.access_decision_count(), before)


if __name__ == "__main__":
    unittest.main()
