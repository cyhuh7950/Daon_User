from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from daon_user_api.retention import (
    DerivativeInput,
    ReferenceCleanupPort,
    ReferenceRetentionRepository,
    RetentionContext,
    RetentionError,
    RetentionService,
)


NOW = datetime(2026, 7, 31, 4, 0, tzinfo=UTC)


class RetentionDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.repository = ReferenceRetentionRepository()
        self.cleanup = ReferenceCleanupPort()
        self.service = RetentionService(
            self.repository, self.cleanup, clock=lambda: self.now,
            grace_period=timedelta(days=30), fixture_prefix="fixture-",
        )
        self.context = RetentionContext(
            "tenant-retention-a", "workspace-retention-a", "actor-retention-a",
            "trace-retention-a", "policy-retention-v1", organization_admin=True,
        )
        self.foreign = RetentionContext(
            "tenant-retention-b", "workspace-retention-b", "actor-retention-b",
            "trace-retention-b", "policy-retention-v1", organization_admin=True,
        )
        self.inventory = (
            DerivativeInput("original_content", "fixture-object-a"),
            DerivativeInput("index", "fixture-index-a"),
            DerivativeInput("preview", "fixture-preview-a"),
            DerivativeInput("cache", "fixture-cache-a"),
            DerivativeInput("known_local_copy", "fixture-local-a", acknowledgement_required=True),
            DerivativeInput("sync_reference", "fixture-sync-a"),
        )

    def _create(self):  # type: ignore[no-untyped-def]
        return self.service.create_request(
            self.context, source_id="fixture-source-a", inventory=self.inventory,
            idempotency_key="retention-create-a", if_match="*",
        )

    def test_request_is_idempotent_scoped_and_blocks_new_source_use(self) -> None:
        created = self._create()
        replay = self._create()
        self.assertEqual(created.request_id, replay.request_id)
        self.assertEqual(created.state, "grace_period")
        self.assertFalse(created.source_active)
        self.assertTrue(self.service.is_source_use_blocked(self.context, "fixture-source-a"))
        with self.assertRaisesRegex(RetentionError, "DELETION_REQUEST_UNAVAILABLE"):
            self.service.get_request(self.foreign, created.request_id)
        with self.assertRaisesRegex(RetentionError, "IDEMPOTENCY_KEY_REUSED"):
            self.service.create_request(
                self.context, source_id="fixture-source-other", inventory=self.inventory,
                idempotency_key="retention-create-a", if_match="*",
            )

    def test_hold_created_before_deletion_blocks_request_immediately(self) -> None:
        self.service.register_source(self.context, "fixture-source-preheld")
        hold = self.service.apply_legal_hold(
            self.context, source_id="fixture-source-preheld", expected_version=1,
            idempotency_key="hold-predelete", step_up_verified=True,
        )
        self.assertEqual(hold.state, "active")
        inventory = tuple(
            DerivativeInput(
                item.kind, f"fixture-preheld-{item.kind}",
                acknowledgement_required=item.acknowledgement_required,
            )
            for item in self.inventory
        )
        created = self.service.create_request(
            self.context, source_id="fixture-source-preheld", inventory=inventory,
            idempotency_key="create-preheld", if_match="*",
        )
        self.assertEqual(created.state, "blocked_by_hold")

    def test_grace_hold_release_cancel_and_lost_update_are_deterministic(self) -> None:
        created = self._create()
        with self.assertRaisesRegex(RetentionError, "DELETION_GRACE_PERIOD_ACTIVE"):
            self.service.purge(
                self.context, created.request_id, expected_version=created.version,
                idempotency_key="purge-too-early", step_up_verified=True,
            )
        hold = self.service.apply_legal_hold(
            self.context, source_id="fixture-source-a", expected_version=created.version,
            idempotency_key="hold-a", step_up_verified=True,
        )
        blocked = self.service.get_request(self.context, created.request_id)
        self.assertEqual(blocked.state, "blocked_by_hold")
        with self.assertRaisesRegex(RetentionError, "LEGAL_HOLD_ACTIVE"):
            self.service.purge(
                self.context, created.request_id, expected_version=blocked.version,
                idempotency_key="purge-held", step_up_verified=True,
            )
        released = self.service.release_legal_hold(
            self.context, hold.hold_id, expected_version=hold.version,
            idempotency_key="release-a", step_up_verified=True,
        )
        self.assertEqual(released.state, "released")
        restored = self.service.get_request(self.context, created.request_id)
        self.assertEqual(restored.state, "grace_period")
        with self.assertRaisesRegex(RetentionError, "RETENTION_VERSION_CONFLICT"):
            self.service.cancel(
                self.context, created.request_id, expected_version=created.version,
                idempotency_key="cancel-stale",
            )
        cancelled = self.service.cancel(
            self.context, created.request_id, expected_version=restored.version,
            idempotency_key="cancel-current",
        )
        self.assertEqual((cancelled.state, cancelled.source_active), ("cancelled", True))
        self.assertEqual(cancelled.source_version_mutations, 0)

    def test_step_up_binding_and_fixture_guard_fail_closed(self) -> None:
        created = self._create()
        self.now = created.grace_until + timedelta(seconds=1)
        for key in ("invalid", "expired", "reused", "binding"):
            with self.subTest(key=key), self.assertRaisesRegex(RetentionError, "STEP_UP_REQUIRED"):
                self.service.purge(
                    self.context, created.request_id, expected_version=created.version,
                    idempotency_key=f"purge-{key}", step_up_verified=False,
                )
        unsafe = self.service.create_request(
            self.context, source_id="fixture-source-unsafe",
            inventory=tuple(
                DerivativeInput(
                    item.kind,
                    "production-cache" if item.kind == "cache" else f"fixture-unsafe-{item.kind}",
                    acknowledgement_required=item.acknowledgement_required,
                )
                for item in self.inventory
            ),
            idempotency_key="unsafe-create", if_match="*",
        )
        self.now = unsafe.grace_until + timedelta(seconds=1)
        with self.assertRaisesRegex(RetentionError, "FIXTURE_ONLY_PURGE_REQUIRED"):
            self.service.purge(
                self.context, unsafe.request_id, expected_version=unsafe.version,
                idempotency_key="unsafe-purge", step_up_verified=True,
            )
        self.assertEqual(self.cleanup.attempt_count, 0)

    def test_partial_cleanup_retries_only_failed_items_and_local_ack_gates_purged(self) -> None:
        created = self._create()
        self.now = created.grace_until + timedelta(seconds=1)
        self.cleanup.fail_references.add("fixture-preview-a")
        partial = self.service.purge(
            self.context, created.request_id, expected_version=created.version,
            idempotency_key="purge-partial", step_up_verified=True,
        )
        self.assertEqual(partial.state, "cleanup_pending")
        completed_once = set(partial.completed_references)
        self.assertNotIn("fixture-preview-a", completed_once)
        self.assertNotIn("fixture-local-a", completed_once)
        self.cleanup.fail_references.clear()
        retry = self.service.purge(
            self.context, created.request_id, expected_version=partial.version,
            idempotency_key="purge-retry", step_up_verified=True,
        )
        self.assertEqual(retry.state, "cleanup_pending")
        for reference in completed_once:
            self.assertEqual(self.cleanup.attempts_by_reference[reference], 1)
        acknowledged = self.service.acknowledge_local_copy(
            self.context, created.request_id, reference_id="fixture-local-a",
            evidence="device_ack", expected_version=retry.version,
            idempotency_key="local-ack-a",
        )
        final = self.service.purge(
            self.context, created.request_id, expected_version=acknowledged.version,
            idempotency_key="purge-final", step_up_verified=True,
        )
        self.assertEqual(final.state, "purged")
        self.assertEqual(len(final.completed_references), len(self.inventory))
        self.assertEqual(self.service.minimal_lineage(created.request_id)["retention_years"], 1)
        self.assertNotIn("content_digest", self.service.minimal_lineage(created.request_id))


if __name__ == "__main__":
    unittest.main()
