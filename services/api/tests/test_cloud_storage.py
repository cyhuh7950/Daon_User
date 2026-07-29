from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from daon_user_api.cloud_storage import (
    CloudAccessContext,
    CloudDatabaseError,
    PostgresCloudStore,
    classify_database_error,
)
from daon_user_api.cloud_admin import server_version_supported


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0001_cloud_foundation.py"


class CloudStorageContractTests(unittest.TestCase):
    def test_postgres_patch_version_accepts_packaging_suffix_only(self) -> None:
        self.assertTrue(server_version_supported("18.4"))
        self.assertTrue(server_version_supported("18.4 (Debian 18.4-1.pgdg13+1)"))
        self.assertFalse(server_version_supported("18.3"))
        self.assertFalse(server_version_supported("18.40"))

    def test_access_context_rejects_untrusted_or_empty_scope(self) -> None:
        with self.assertRaises(ValueError):
            CloudAccessContext(tenant_id="", workspace_id="workspace-a", actor_id="user-a", capability="view")
        with self.assertRaises(ValueError):
            CloudAccessContext(tenant_id="tenant-a", workspace_id="workspace-a", actor_id="user-a", capability="")

    def test_database_errors_are_safe_and_bounded(self) -> None:
        for sqlstate, code, retryable in (
            ("57014", "DATABASE_TIMEOUT", True),
            ("40P01", "DATABASE_RETRYABLE_CONFLICT", True),
            ("40001", "DATABASE_RETRYABLE_CONFLICT", True),
            ("23505", "DATABASE_CONSTRAINT_VIOLATION", False),
            ("42501", "DATABASE_ACCESS_DENIED", False),
            (None, "DATABASE_UNAVAILABLE", True),
        ):
            error = classify_database_error(sqlstate)
            self.assertIsInstance(error, CloudDatabaseError)
            self.assertEqual(error.code, code)
            self.assertEqual(error.retryable, retryable)
            self.assertNotIn("postgres", str(error).lower())

    def test_migration_declares_rls_atomicity_and_vector_contract(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            "CREATE EXTENSION IF NOT EXISTS vector",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "current_setting('app.tenant_id', true)",
            "current_setting('app.workspace_id', true)",
            "CREATE TRIGGER audit_events_immutable",
            "vector(3)",
            "idempotency_records",
        ):
            self.assertIn(token, source)


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class PostgresCloudIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PostgresCloudStore(os.environ["DAON_TEST_POSTGRES_DSN"])
        suffix = self._testMethodName
        self.tenant_a = CloudAccessContext(
            f"tenant-a-{suffix}", f"workspace-a-{suffix}", f"user-a-{suffix}", "notification.read"
        )
        self.tenant_b = CloudAccessContext(
            f"tenant-b-{suffix}", f"workspace-b-{suffix}", f"user-b-{suffix}", "notification.read"
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_readiness_requires_migration_and_vector(self) -> None:
        status = self.store.readiness()
        self.assertTrue(status.ready)
        self.assertEqual(status.schema_revision, "0001")
        self.assertEqual(status.vector_version, "0.8.2")

    def test_rls_blocks_cross_tenant_and_context_does_not_leak(self) -> None:
        self.store.seed_scope(self.tenant_a)
        self.store.seed_scope(self.tenant_b)
        self.store.put_vector(self.tenant_a, "vector-a", [1.0, 0.0, 0.0])
        self.assertEqual(self.store.get_vector(self.tenant_a, "vector-a"), (1.0, 0.0, 0.0))
        self.assertIsNone(self.store.get_vector(self.tenant_b, "vector-a"))
        self.assertTrue(self.store.context_is_clear())

    def test_notification_audit_and_idempotency_are_atomic(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-a", "event-a")
        first = self.store.mark_notification_read(self.tenant_a, created.notification_id, 1, "idem-cloud-read-0001")
        replay = self.store.mark_notification_read(self.tenant_a, created.notification_id, 1, "idem-cloud-read-0001")
        self.assertEqual(first, replay)
        self.assertEqual(self.store.audit_count(self.tenant_a, "notification.read"), 1)
        self.assertEqual(self.store.idempotency_count(self.tenant_a, "idem-cloud-read-0001"), 1)

    def test_same_key_concurrency_replays_one_result_and_one_audit(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-c", "event-c")
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = tuple(executor.map(
                lambda _: self.store.mark_notification_read(
                    self.tenant_a, created.notification_id, 1, "idem-cloud-read-0003"
                ),
                range(8),
            ))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.store.audit_count(self.tenant_a, "notification.read"), 1)

    def test_different_key_same_version_allows_one_winner(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-d", "event-d")

        def attempt(index: int) -> str:
            try:
                self.store.mark_notification_read(
                    self.tenant_a, created.notification_id, 1, f"idem-cloud-compete-{index:04d}"
                )
                return "success"
            except CloudDatabaseError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = tuple(executor.map(attempt, range(8)))
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("VERSION_CONFLICT"), 7)
        self.assertEqual(self.store.audit_count(self.tenant_a, "notification.read"), 1)

    def test_audit_failure_rolls_back_state_and_idempotency(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-b", "event-b")
        with self.assertRaises(CloudDatabaseError):
            self.store.mark_notification_read(
                self.tenant_a,
                created.notification_id,
                1,
                "idem-cloud-read-0002",
                force_audit_failure=True,
            )
        current = self.store.get_notification(self.tenant_a, created.notification_id)
        self.assertEqual(current.version, 1)
        self.assertIsNone(current.read_at)
        self.assertEqual(self.store.idempotency_count(self.tenant_a, "idem-cloud-read-0002"), 0)


if __name__ == "__main__":
    unittest.main()
