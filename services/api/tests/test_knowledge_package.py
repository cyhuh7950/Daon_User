from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta

from daon_user_api.knowledge_package import (
    KnowledgePackageContext,
    KnowledgePackageError,
    KnowledgePackageRecord,
    KnowledgePackageService,
    ReferenceKnowledgePackageRepository,
)


CONTENT = b"approved Daon knowledge"
DIGEST = hashlib.sha256(CONTENT).hexdigest()
NOW = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)


class KnowledgePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ReferenceKnowledgePackageRepository()
        self.repository.add(KnowledgePackageRecord(
            package_id="knowledge-package-1",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            producer="daon3",
            producer_version="3.0",
            knowledge_registration_id="registration-1",
            output_version_id="output-version-1",
            authority="approved",
            review_state="approved",
            registration_state="registered",
            digest_sha256=DIGEST,
            byte_size=len(CONTENT),
            content_type="application/json",
            content=CONTENT,
            effective_at=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
        ))
        self.service = KnowledgePackageService(self.repository, clock=lambda: NOW)
        self.context = KnowledgePackageContext(
            "tenant-1", "workspace-1", "actor-1", "trace-1", "policy-v1", "device-1"
        )

    def test_only_registered_approved_daon_knowledge_can_be_provisioned_offline(self) -> None:
        packages = self.service.list_packages(self.context)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].producer, "daon3")
        self.assertEqual(packages[0].registration_state, "registered")
        self.assertEqual(packages[0].review_state, "approved")
        self.assertEqual(packages[0].effective_at, NOW - timedelta(days=1))
        grant = self.service.create_offline_copy(
            self.context,
            package_id="knowledge-package-1",
            device_id="device-1",
            step_up_authorization_id="step-up-1",
            idempotency_key="knowledge-copy-0001",
            approval_verified=True,
        )
        self.assertEqual(grant.state, "approved")
        self.assertEqual(self.service.read_content(self.context, copy_id=grant.copy_id), CONTENT)

    def test_cross_workspace_expired_and_unverified_copy_fail_without_grant(self) -> None:
        foreign = KnowledgePackageContext(
            "tenant-1", "workspace-2", "actor-1", "trace-2", "policy-v1", "device-1"
        )
        self.assertEqual(self.service.list_packages(foreign), ())
        with self.assertRaisesRegex(KnowledgePackageError, "STEP_UP_REQUIRED"):
            self.service.create_offline_copy(
                self.context,
                package_id="knowledge-package-1",
                device_id="device-1",
                step_up_authorization_id="invalid",
                idempotency_key="knowledge-copy-denied",
                approval_verified=False,
            )
        self.assertEqual(self.repository.grant_count, 0)

    def test_idempotency_replay_converges_and_reuse_is_denied(self) -> None:
        first = self.service.create_offline_copy(
            self.context,
            package_id="knowledge-package-1",
            device_id="device-1",
            step_up_authorization_id="step-up-1",
            idempotency_key="knowledge-copy-0001",
            approval_verified=True,
        )
        replay = self.service.create_offline_copy(
            self.context,
            package_id="knowledge-package-1",
            device_id="device-1",
            step_up_authorization_id="step-up-1",
            idempotency_key="knowledge-copy-0001",
            approval_verified=True,
        )
        self.assertEqual(first.copy_id, replay.copy_id)
        with self.assertRaisesRegex(KnowledgePackageError, "IDEMPOTENCY_KEY_REUSED"):
            self.service.create_offline_copy(
                self.context,
                package_id="knowledge-package-1",
                device_id="device-2",
                step_up_authorization_id="step-up-1",
                idempotency_key="knowledge-copy-0001",
                approval_verified=True,
            )


if __name__ == "__main__":
    unittest.main()
