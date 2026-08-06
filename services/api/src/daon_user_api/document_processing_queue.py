"""Durable cross-workspace queue for document-understanding workers."""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Sequence, cast

from psycopg import Connection, Error
from psycopg_pool import ConnectionPool, PoolTimeout

from .cloud_storage import CloudAccessContext, PostgresCloudStore, classify_database_error


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class DocumentProcessingQueueError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class DocumentProcessingJob:
    tenant_id: str
    workspace_id: str
    job_id: str
    source_id: str
    source_version_id: str
    processing_run_id: str
    state: str
    attempt: int
    max_attempts: int
    lease_owner: str | None
    lease_until: datetime | None
    trace_id: str
    policy_version: str
    created_by: str
    created_at: datetime
    version: int


class PostgresDocumentProcessingQueue:
    """Claim globally through a security-definer function; finish inside RLS scope."""

    def __init__(
        self, dsn: str, cloud_store: PostgresCloudStore, *, min_size: int = 1, max_size: int = 2,
    ) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
        self._cloud_store = cloud_store
        self._pool: ConnectionPool[tuple[Any, ...]] | None = ConnectionPool(
            conninfo=dsn, min_size=min_size, max_size=max_size,
            kwargs={"autocommit": False}, timeout=2.0, reconnect_timeout=5.0,
            open=False,
        )
        self._open_lock = threading.Lock()

    @classmethod
    def for_cloud_store(cls, cloud_store: PostgresCloudStore) -> PostgresDocumentProcessingQueue:
        instance = cls.__new__(cls)
        instance._cloud_store = cloud_store
        instance._pool = None
        instance._open_lock = threading.Lock()
        return instance

    @staticmethod
    def _safe_id(value: str, code: str) -> str:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise DocumentProcessingQueueError(code)
        return value

    @staticmethod
    def _job_from_row(row: Sequence[object]) -> DocumentProcessingJob:
        return DocumentProcessingJob(
            tenant_id=str(row[0]), workspace_id=str(row[1]), job_id=str(row[2]),
            source_id=str(row[3]), source_version_id=str(row[4]),
            processing_run_id=str(row[5]), state=str(row[6]), attempt=cast(int, row[7]),
            max_attempts=cast(int, row[8]), lease_owner=None if row[9] is None else str(row[9]),
            lease_until=cast(datetime | None, row[10]), trace_id=str(row[11]),
            policy_version=str(row[12]), created_by=str(row[13]),
            created_at=cast(datetime, row[14]), version=cast(int, row[15]),
        )

    @contextmanager
    def _claim_transaction(self) -> Iterator[Connection[tuple[Any, ...]]]:
        if self._pool is None:
            raise DocumentProcessingQueueError("DOCUMENT_QUEUE_CLAIM_NOT_CONFIGURED")
        try:
            if self._pool.closed:
                with self._open_lock:
                    if self._pool.closed:
                        self._pool.open(wait=False)
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    yield connection
        except DocumentProcessingQueueError:
            raise
        except PoolTimeout:
            raise DocumentProcessingQueueError("DATABASE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            classified = classify_database_error(error.sqlstate)
            raise DocumentProcessingQueueError(
                classified.code, retryable=classified.retryable,
            ) from None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def claim(self, worker_id: str, *, lease_seconds: int = 600) -> DocumentProcessingJob | None:
        self._safe_id(worker_id, "DOCUMENT_WORKER_ID_INVALID")
        if not isinstance(lease_seconds, int) or not 10 <= lease_seconds <= 600:
            raise DocumentProcessingQueueError("DOCUMENT_WORKER_LEASE_INVALID")
        with self._claim_transaction() as connection:
            row = connection.execute(
                "SELECT tenant_id,workspace_id,job_id,source_id,source_version_id,"
                "processing_run_id,state,attempt,max_attempts,lease_owner,lease_until,"
                "trace_id,policy_version,created_by,created_at,version "
                "FROM claim_document_processing_job(%s,%s)",
                (worker_id, lease_seconds),
            ).fetchone()
        return None if row is None else self._job_from_row(row)

    @staticmethod
    def _scope(job: DocumentProcessingJob) -> CloudAccessContext:
        return CloudAccessContext(
            job.tenant_id, job.workspace_id, job.created_by, "source.process",
        )

    @staticmethod
    def _current_from_row(job: DocumentProcessingJob, row: Sequence[object]) -> DocumentProcessingJob:
        return DocumentProcessingJob(
            tenant_id=job.tenant_id, workspace_id=job.workspace_id, job_id=job.job_id,
            source_id=job.source_id, source_version_id=job.source_version_id,
            processing_run_id=job.processing_run_id, state=str(row[0]),
            attempt=cast(int, row[1]), max_attempts=cast(int, row[2]),
            lease_owner=None if row[3] is None else str(row[3]),
            lease_until=cast(datetime | None, row[4]), trace_id=job.trace_id,
            policy_version=job.policy_version, created_by=job.created_by,
            created_at=job.created_at, version=cast(int, row[5]),
        )

    @staticmethod
    def _assert_lease(
        claimed: DocumentProcessingJob, current: DocumentProcessingJob,
        worker_id: str, now: datetime,
    ) -> None:
        if (
            current.state != "leased" or current.version != claimed.version
            or current.attempt != claimed.attempt or current.lease_owner != worker_id
            or current.lease_until is None or current.lease_until <= now
        ):
            raise DocumentProcessingQueueError("DOCUMENT_JOB_LEASE_LOST", retryable=True)

    def _locked_current(self, connection, job: DocumentProcessingJob) -> DocumentProcessingJob:  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT state,attempt,max_attempts,lease_owner,lease_until,version "
            "FROM document_processing_jobs WHERE job_id=%s FOR UPDATE",
            (job.job_id,),
        ).fetchone()
        if row is None:
            raise DocumentProcessingQueueError("DOCUMENT_JOB_NOT_FOUND")
        return self._current_from_row(job, row)

    def complete(
        self, job: DocumentProcessingJob, worker_id: str, *, now: datetime,
    ) -> None:
        self._safe_id(worker_id, "DOCUMENT_WORKER_ID_INVALID")
        with self._cloud_store._transaction(self._scope(job)) as connection:
            current = self._locked_current(connection, job)
            self._assert_lease(job, current, worker_id, now)
            connection.execute(
                "UPDATE document_processing_jobs SET state='completed',lease_owner=NULL,"
                "lease_until=NULL,completed_at=%s,version=version+1 WHERE job_id=%s",
                (now, job.job_id),
            )
            connection.execute(
                "INSERT INTO document_processing_job_attempts "
                "(tenant_id,workspace_id,job_id,attempt_number,worker_id,outcome,trace_id,"
                "started_at,finished_at) VALUES (%s,%s,%s,%s,%s,'completed',%s,%s,%s)",
                (
                    job.tenant_id, job.workspace_id, job.job_id, job.attempt, worker_id,
                    job.trace_id, job.created_at, now,
                ),
            )

    def fail_terminal(
        self, job: DocumentProcessingJob, worker_id: str, safe_error_code: str,
        *, now: datetime,
    ) -> None:
        self._safe_id(worker_id, "DOCUMENT_WORKER_ID_INVALID")
        self._safe_id(safe_error_code, "DOCUMENT_JOB_ERROR_CODE_INVALID")
        with self._cloud_store._transaction(self._scope(job)) as connection:
            current = self._locked_current(connection, job)
            self._assert_lease(job, current, worker_id, now)
            connection.execute(
                "UPDATE document_processing_jobs SET state='dead_letter',lease_owner=NULL,"
                "lease_until=NULL,last_safe_error_code=%s,completed_at=%s,version=version+1 "
                "WHERE job_id=%s",
                (safe_error_code, now, job.job_id),
            )
            connection.execute(
                "INSERT INTO document_processing_job_attempts "
                "(tenant_id,workspace_id,job_id,attempt_number,worker_id,outcome,"
                "safe_error_code,trace_id,started_at,finished_at) "
                "VALUES (%s,%s,%s,%s,%s,'dead_letter',%s,%s,%s,%s)",
                (
                    job.tenant_id, job.workspace_id, job.job_id, job.attempt, worker_id,
                    safe_error_code, job.trace_id, job.created_at, now,
                ),
            )
