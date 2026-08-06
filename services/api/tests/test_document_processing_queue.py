from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from daon_user_api.document_processing_queue import (
    DocumentProcessingJob,
    DocumentProcessingQueueError,
    PostgresDocumentProcessingQueue,
)


class Cursor:
    def __init__(self, row=None) -> None:  # type: ignore[no-untyped-def]
        self.row = row

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.row


class FakeConnection:
    def __init__(self, current: DocumentProcessingJob) -> None:
        self.current = current
        self.mutations: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if sql.startswith("SELECT state,attempt,max_attempts"):
            job = self.current
            return Cursor((
                job.state, job.attempt, job.max_attempts, job.lease_owner,
                job.lease_until, job.version,
            ))
        self.mutations.append((sql, params))
        if sql.startswith("UPDATE document_processing_jobs"):
            return Cursor()
        if sql.startswith("INSERT INTO document_processing_job_attempts"):
            return Cursor()
        raise AssertionError(sql)


class FakeCloudStore:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.capabilities: list[str] = []

    @contextmanager
    def _transaction(self, context):  # type: ignore[no-untyped-def]
        self.capabilities.append(context.capability)
        yield self.connection


def leased_job(*, owner: str = "worker-a", version: int = 2) -> DocumentProcessingJob:
    now = datetime.now(timezone.utc)
    return DocumentProcessingJob(
        tenant_id="tenant-cp3", workspace_id="workspace-cp3", job_id="job-cp3",
        source_id="source-cp3", source_version_id="source-version-cp3",
        processing_run_id="run-cp3", state="leased", attempt=1, max_attempts=3,
        lease_owner=owner, lease_until=now + timedelta(minutes=2),
        trace_id="trace-cp3", policy_version="policy-v1", created_by="actor-cp3",
        created_at=now - timedelta(minutes=1), version=version,
    )


class PostgresDocumentProcessingQueueTests(unittest.TestCase):
    def test_different_worker_cannot_complete_an_active_lease(self) -> None:
        job = leased_job()
        connection = FakeConnection(job)
        queue = PostgresDocumentProcessingQueue.for_cloud_store(
            FakeCloudStore(connection),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(DocumentProcessingQueueError, "DOCUMENT_JOB_LEASE_LOST"):
            queue.complete(job, "worker-b", now=datetime.now(timezone.utc))

        self.assertEqual(connection.mutations, [])

    def test_matching_worker_completes_job_and_appends_immutable_attempt(self) -> None:
        job = leased_job()
        connection = FakeConnection(job)
        queue = PostgresDocumentProcessingQueue.for_cloud_store(
            FakeCloudStore(connection),  # type: ignore[arg-type]
        )

        queue.complete(job, "worker-a", now=datetime.now(timezone.utc))

        self.assertEqual(len(connection.mutations), 2)
        self.assertIn("state='completed'", connection.mutations[0][0])
        self.assertIn("'completed'", connection.mutations[1][0])

    def test_processing_failure_is_dead_lettered_not_retried_on_terminal_run(self) -> None:
        job = leased_job()
        connection = FakeConnection(job)
        queue = PostgresDocumentProcessingQueue.for_cloud_store(
            FakeCloudStore(connection),  # type: ignore[arg-type]
        )

        queue.fail_terminal(
            job, "worker-a", "NO_AVAILABLE_UNDERSTANDING_MODEL",
            now=datetime.now(timezone.utc),
        )

        self.assertEqual(len(connection.mutations), 2)
        self.assertIn("state='dead_letter'", connection.mutations[0][0])
        self.assertNotIn("retry_wait", connection.mutations[0][0])
        self.assertIn("'dead_letter'", connection.mutations[1][0])

    def test_expired_lease_rejects_completion_without_mutation(self) -> None:
        job = leased_job()
        expired = DocumentProcessingJob(
            **{
                field: getattr(job, field)
                for field in job.__dataclass_fields__
                if field != "lease_until"
            },
            lease_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        connection = FakeConnection(expired)
        queue = PostgresDocumentProcessingQueue.for_cloud_store(
            FakeCloudStore(connection),  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(DocumentProcessingQueueError, "DOCUMENT_JOB_LEASE_LOST"):
            queue.complete(expired, "worker-a", now=datetime.now(timezone.utc))

        self.assertEqual(connection.mutations, [])


if __name__ == "__main__":
    unittest.main()
