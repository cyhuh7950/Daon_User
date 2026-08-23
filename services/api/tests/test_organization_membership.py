from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daon_user_api.organization_membership import (
    InvitationState,
    MembershipState,
    OrganizationWorkflowError,
    RequestState,
    SqliteOrganizationRepository,
)


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


if __name__ == "__main__":
    unittest.main()
