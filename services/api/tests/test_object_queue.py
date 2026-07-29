from __future__ import annotations

import hashlib
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.object_queue import (
    DurableObjectWorker,
    MinioObjectStorageAdapter,
    ObjectKeyPolicy,
    ObjectQueueCoordinator,
    ObjectQueueError,
    ObjectStorageError,
    PostgresObjectQueueStore,
    RetryPolicy,
    StagedObject,
    StoredObject,
)
from daon_user_api.object_worker import ObjectWorkerSettings


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0002_object_queue_worker.py"
IMPLEMENTATION = ROOT / "services" / "api" / "src" / "daon_user_api" / "object_queue.py"
UTC = timezone.utc


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str]] = {}
        self.available = True
        self.fail_promote: ObjectStorageError | None = None
        self.crash_after_promote = False
        self.promote_count = 0

    def health(self) -> bool:
        return self.available

    def put_staged(self, key: str, content: bytes, content_type: str, digest: str) -> StagedObject:
        if not self.available:
            raise ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)
        actual = hashlib.sha256(content).hexdigest()
        if actual != digest:
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH", retryable=False)
        self.objects[key] = (content, content_type, digest)
        return StagedObject(key, digest, len(content), content_type, f"etag-{digest[:12]}", "stage-v1")

    def promote(
        self, staged: StagedObject, final_key: str, *, expected_digest: str, expected_size: int,
        content_type: str,
    ) -> StoredObject:
        self.promote_count += 1
        if self.fail_promote is not None:
            raise self.fail_promote
        try:
            content, stored_type, digest = self.objects[staged.key]
        except KeyError:
            raise ObjectStorageError("OBJECT_NOT_FOUND", retryable=False) from None
        if digest != expected_digest or len(content) != expected_size or stored_type != content_type:
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH", retryable=False)
        self.objects[final_key] = (content, content_type, digest)
        if self.crash_after_promote:
            self.crash_after_promote = False
            raise SimulatedWorkerCrash("after-promote")
        return StoredObject(final_key, digest, len(content), content_type, f"etag-{digest[:12]}", "final-v1")

    def get(self, key: str) -> bytes:
        if not self.available:
            raise ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)
        try:
            return self.objects[key][0]
        except KeyError:
            raise ObjectStorageError("OBJECT_NOT_FOUND", retryable=False) from None


class SimulatedWorkerCrash(RuntimeError):
    pass


class ObjectQueueContractTests(unittest.TestCase):
    def test_migration_declares_outbox_jobs_attempts_and_forced_rls(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            "CREATE TABLE object_records",
            "CREATE TABLE object_outbox_events",
            "CREATE TABLE durable_jobs",
            "CREATE TABLE job_attempts",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "pending','leased','retry_wait','completed','dead_letter",
            "reject_audit_mutation",
        ):
            self.assertIn(token, source)
        self.assertIn("FOR UPDATE SKIP LOCKED", IMPLEMENTATION.read_text(encoding="utf-8"))

    def test_key_policy_uses_only_safe_server_scope_and_opaque_id(self) -> None:
        scope = CloudAccessContext("tenant-a", "workspace-a", "actor-a", "object.write")
        policy = ObjectKeyPolicy()
        final_key = policy.final_key(scope, "source", "0123456789abcdef0123456789abcdef")
        staged_key = policy.staging_key(scope, "source", "0123456789abcdef0123456789abcdef")
        self.assertEqual(final_key, "tenant-a/workspace-a/source/0123456789abcdef0123456789abcdef")
        self.assertTrue(staged_key.startswith("_staging/tenant-a/workspace-a/source/"))
        for attack in (
            "../escape", "/absolute", "tenant-a\\workspace-a", "https://object.example/key",
            "tenant-a/workspace-a/source/%2e%2e", "tenant-a/workspace-a/source/obj\x00x",
            "tenant-a/workspace-b/source/0123456789abcdef0123456789abcdef",
            "tenant-a/workspace-a/source/ｅｖｉｌ",
        ):
            with self.assertRaises(ObjectQueueError):
                policy.validate_final(scope, "source", attack)

    def test_retry_policy_is_bounded_and_deterministic_with_jitter(self) -> None:
        policy = RetryPolicy(max_attempts=4, base_seconds=2, max_seconds=10, jitter_ratio=0.25)
        self.assertEqual(policy.delay_seconds(1, jitter_unit=0.0), 1.5)
        self.assertEqual(policy.delay_seconds(2, jitter_unit=1.0), 5.0)
        self.assertEqual(policy.delay_seconds(4, jitter_unit=1.0), 10.0)
        self.assertFalse(policy.exhausted(3))
        self.assertTrue(policy.exhausted(4))

    def test_job_payload_rejects_secret_binary_and_unknown_schema(self) -> None:
        scope = CloudAccessContext("tenant-a", "workspace-a", "actor-a", "object.write")
        store = object.__new__(PostgresObjectQueueStore)
        for payload in (
            {"schema_version": 2, "object_id": "obj"},
            {"schema_version": 1, "object_id": "obj", "token": "secret"},
            {"schema_version": 1, "object_id": "obj", "binary": "AA=="},
        ):
            with self.assertRaises(ObjectQueueError):
                store.validate_payload(scope, "object.promote", payload)

    def test_incomplete_object_dependency_does_not_change_api_contract(self) -> None:
        adapter = FakeObjectStorage()
        adapter.available = False
        self.assertFalse(adapter.health())
        error = ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)
        self.assertEqual(str(error), "OBJECT_STORAGE_UNAVAILABLE")
        self.assertNotIn("endpoint", str(error).lower())

    def test_worker_settings_require_secret_references_and_valid_scope(self) -> None:
        with self.assertRaises(ValueError):
            ObjectWorkerSettings(
                database_dsn="postgresql://example",
                object_storage_endpoint="object:9000",
                object_storage_bucket="daon-objects",
                access_key_file=Path("access"),
                secret_key_file=Path("secret"),
                tenant_id="../tenant",
                workspace_id="workspace-a",
                actor_id="worker-a",
            )

    def test_worker_survives_retryable_database_outage_until_shutdown(self) -> None:
        class RecoveringStore:
            def __init__(self) -> None:
                self.calls = 0

            def claim(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                if self.calls == 1:
                    raise ObjectQueueError("DATABASE_UNAVAILABLE", retryable=True)
                return None

        store = RecoveringStore()
        worker = DurableObjectWorker(store, FakeObjectStorage(), "worker-recovery")  # type: ignore[arg-type]
        scope = CloudAccessContext("tenant-a", "workspace-a", "worker-a", "object.write")
        worker.start((scope,), poll_seconds=0.01)
        deadline = time.monotonic() + 1
        while store.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        worker.shutdown(timeout=1)
        self.assertGreaterEqual(store.calls, 2)
        self.assertFalse(worker.running)


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class PostgresObjectQueueIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PostgresObjectQueueStore(os.environ["DAON_TEST_POSTGRES_DSN"])
        suffix = self._testMethodName
        self.scope = CloudAccessContext(
            f"tenant-oq-{suffix}", f"workspace-oq-{suffix}", f"actor-oq-{suffix}", "object.write"
        )
        self.other_workspace = CloudAccessContext(
            self.scope.tenant_id, f"workspace-other-{suffix}", self.scope.actor_id, "object.write"
        )
        self.storage = FakeObjectStorage()
        self.ids = iter(
            f"{index:032x}" for index in range(1, 100)
        )
        self.coordinator = ObjectQueueCoordinator(self.store, self.storage, id_factory=lambda: next(self.ids))
        self.store.seed_scope(self.scope)
        self.store.seed_scope(self.other_workspace)

    def tearDown(self) -> None:
        self.store.close()

    def _submit(self, *, key: str = "idem-object-1", content: bytes = b"object-content"):
        return self.coordinator.submit(
            self.scope, area="source", content=content, content_type="application/pdf",
            idempotency_key=key, trace_id="trace-object-1",
        )

    def _request_audit_count(self, object_id: str) -> int:
        with self.store._transaction(self.scope) as connection:
            row = connection.execute(
                "SELECT count(*) FROM audit_events "
                "WHERE action='object.store.requested' AND target_type='object' AND target_id=%s",
                (object_id,),
            ).fetchone()
        assert row is not None
        return int(row[0])

    def test_domain_object_outbox_job_are_atomic_and_replay_safe(self) -> None:
        submission = self._submit()
        replay = self._submit()
        self.assertEqual(
            (submission.object_id, submission.job_id, submission.event_id),
            (replay.object_id, replay.job_id, replay.event_id),
        )
        self.assertFalse(submission.replayed)
        self.assertTrue(replay.replayed)
        counts = self.store.entity_counts(self.scope, submission.object_id)
        self.assertEqual(counts, {"objects": 1, "outbox": 1, "jobs": 1, "attempts": 0})
        self.assertEqual(self._request_audit_count(submission.object_id), 1)
        with self.assertRaises(ObjectQueueError):
            self.coordinator.submit(
                self.scope, area="source", content=b"different", content_type="application/pdf",
                idempotency_key="idem-object-1", trace_id="trace-object-1",
            )
        self.assertEqual(self.store.entity_counts(self.scope, submission.object_id), counts)

    def test_forced_transaction_failure_leaves_no_domain_or_queue_rows(self) -> None:
        content = b"transaction-content"
        digest = hashlib.sha256(content).hexdigest()
        object_id = next(self.ids)
        staged_key = ObjectKeyPolicy().staging_key(self.scope, "source", object_id)
        staged = self.storage.put_staged(staged_key, content, "application/pdf", digest)
        with self.assertRaises(ObjectQueueError):
            self.store.register_object_job(
                self.scope, object_id=object_id, area="source", staged=staged,
                final_key=ObjectKeyPolicy().final_key(self.scope, "source", object_id),
                idempotency_key="idem-force-failure", trace_id="trace-force-failure",
                force_rollback=True,
            )
        self.assertEqual(
            self.store.entity_counts(self.scope, object_id),
            {"objects": 0, "outbox": 0, "jobs": 0, "attempts": 0},
        )
        self.assertTrue(self.store.context_is_clear())

    def test_concurrent_workers_claim_only_once_and_complete_verified_object(self) -> None:
        submission = self._submit()
        now = self.store.get_job(self.scope, submission.job_id).next_attempt_at
        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = list(executor.map(
                lambda worker: self.store.claim(self.scope, worker, now=now, lease_seconds=30),
                ("worker-a", "worker-b"),
            ))
        self.assertEqual(sum(job is not None for job in claimed), 1)
        job = next(job for job in claimed if job is not None)
        assert job.lease_owner is not None
        worker = DurableObjectWorker(self.store, self.storage, job.lease_owner)
        result = worker.process_claimed(self.scope, job, now=now + timedelta(seconds=1))
        self.assertEqual(result.state, "completed")
        record = self.store.get_object(self.scope, submission.object_id)
        self.assertEqual(record.status, "completed")
        self.assertEqual(self.storage.get(record.object_key), b"object-content")
        self.assertEqual(self.store.entity_counts(self.scope, submission.object_id)["attempts"], 1)

    def test_crash_after_put_loses_lease_and_recovery_is_idempotent(self) -> None:
        submission = self._submit(key="idem-crash")
        start = self.store.get_job(self.scope, submission.job_id).next_attempt_at
        self.storage.crash_after_promote = True
        worker = DurableObjectWorker(self.store, self.storage, "worker-crash", lease_seconds=5)
        with self.assertRaises(SimulatedWorkerCrash):
            worker.run_once(self.scope, now=start)
        self.assertEqual(self.store.get_job(self.scope, submission.job_id).state, "leased")
        recovered = DurableObjectWorker(self.store, self.storage, "worker-recovery", lease_seconds=5)
        result = recovered.run_once(self.scope, now=start + timedelta(seconds=6))
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "completed")
        self.assertEqual(self.storage.promote_count, 2)
        self.assertEqual(self.store.entity_counts(self.scope, submission.object_id)["attempts"], 1)

    def test_retry_dead_letter_and_authorized_reprocess_preserve_history(self) -> None:
        submission = self._submit(key="idem-retry")
        self.storage.fail_promote = ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)
        policy = RetryPolicy(max_attempts=2, base_seconds=1, max_seconds=2, jitter_ratio=0)
        worker = DurableObjectWorker(self.store, self.storage, "worker-retry", retry_policy=policy)
        start = self.store.get_job(self.scope, submission.job_id).next_attempt_at
        first = worker.run_once(self.scope, now=start, jitter_unit=0.5)
        self.assertEqual(first.state, "retry_wait")
        retry_at = self.store.get_job(self.scope, submission.job_id).next_attempt_at
        self.assertGreater(retry_at, start)
        second = worker.run_once(self.scope, now=retry_at, jitter_unit=0.5)
        self.assertEqual(second.state, "dead_letter")
        with self.assertRaises(ObjectQueueError):
            self.store.reprocess(self.scope, submission.job_id, "reprocess-denied")
        operator = CloudAccessContext(
            self.scope.tenant_id, self.scope.workspace_id, self.scope.actor_id, "queue.reprocess"
        )
        new_job = self.store.reprocess(operator, submission.job_id, "reprocess-authorized")
        self.assertNotEqual(new_job.job_id, submission.job_id)
        self.assertEqual(new_job.retry_of_job_id, submission.job_id)
        self.assertEqual(self.store.get_job(self.scope, submission.job_id).state, "dead_letter")
        self.assertEqual(self.store.entity_counts(self.scope, submission.object_id)["attempts"], 2)

    def test_workspace_rls_blocks_job_object_and_metrics(self) -> None:
        submission = self._submit(key="idem-workspace")
        for operation in (
            lambda: self.store.get_object(self.other_workspace, submission.object_id),
            lambda: self.store.get_job(self.other_workspace, submission.job_id),
        ):
            with self.assertRaises(ObjectQueueError):
                operation()
        metrics = self.store.metrics(self.other_workspace, now=datetime.now(UTC))
        self.assertEqual(metrics.pending, 0)
        self.assertTrue(self.store.context_is_clear())

    def test_object_outage_reports_safe_metrics_and_worker_shutdown_stops_claims(self) -> None:
        self._submit(key="idem-health")
        self.storage.available = False
        status = self.coordinator.health(self.scope, now=datetime.now(UTC))
        self.assertFalse(status.object_storage_ready)
        self.assertEqual(status.queue.pending, 1)
        worker = DurableObjectWorker(self.store, self.storage, "worker-stop")
        worker.start((self.scope,), poll_seconds=0.01)
        worker.shutdown(timeout=2)
        self.assertFalse(worker.running)
        leased = self.store.metrics(self.scope, now=datetime.now(UTC)).leased
        self.assertEqual(leased, 0)


@unittest.skipUnless(
    all(os.environ.get(name) for name in (
        "DAON_TEST_S3_ENDPOINT", "DAON_TEST_S3_ACCESS_KEY", "DAON_TEST_S3_SECRET_KEY", "DAON_TEST_S3_BUCKET",
    )),
    "isolated S3-compatible endpoint required",
)
class MinioObjectStorageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MinioObjectStorageAdapter(
            endpoint=os.environ["DAON_TEST_S3_ENDPOINT"],
            bucket=os.environ["DAON_TEST_S3_BUCKET"],
            access_key=os.environ["DAON_TEST_S3_ACCESS_KEY"],
            secret_key=os.environ["DAON_TEST_S3_SECRET_KEY"],
            secure=os.environ.get("DAON_TEST_S3_SECURE") == "true",
        )
        self.scope = CloudAccessContext("tenant-minio", "workspace-minio", "actor-minio", "object.write")

    def test_put_promote_get_and_metadata_integrity(self) -> None:
        content = b"%PDF-1.7 isolated object"
        digest = hashlib.sha256(content).hexdigest()
        object_id = "abcdef0123456789abcdef0123456789"
        policy = ObjectKeyPolicy()
        staged = self.adapter.put_staged(
            policy.staging_key(self.scope, "source", object_id), content, "application/pdf", digest
        )
        stored = self.adapter.promote(
            staged, policy.final_key(self.scope, "source", object_id),
            expected_digest=digest, expected_size=len(content), content_type="application/pdf",
        )
        self.assertEqual((stored.digest_sha256, stored.byte_size, stored.content_type),
                         (digest, len(content), "application/pdf"))
        self.assertEqual(self.adapter.get(stored.key), content)
        self.assertTrue(self.adapter.health())

    def test_checksum_mismatch_and_internal_values_are_safe(self) -> None:
        with self.assertRaises(ObjectStorageError) as raised:
            self.adapter.put_staged(
                "_staging/tenant-minio/workspace-minio/source/abcdef0123456789abcdef0123456789",
                b"content", "application/pdf", "0" * 64,
            )
        self.assertEqual(raised.exception.code, "OBJECT_CHECKSUM_MISMATCH")
        self.assertNotIn(os.environ["DAON_TEST_S3_ENDPOINT"], str(raised.exception))


if __name__ == "__main__":
    unittest.main()
