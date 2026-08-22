"""Durable queue and status projection for asynchronous Studio generation."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Mapping, Sequence, cast

from psycopg import Connection, Error
from psycopg_pool import ConnectionPool, PoolTimeout

from .cloud_storage import CloudAccessContext, PostgresCloudStore, classify_database_error


class StudioGenerationQueueError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class StudioGenerationJob:
    tenant_id: str
    workspace_id: str
    job_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    idempotency_key: str
    request_json: Mapping[str, object]
    state: str
    attempt: int
    version: int
    safe_error_code: str | None = None
    studio_output_id: str | None = None
    output_version_id: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class PostgresStudioGenerationQueue:
    def __init__(self, dsn: str, cloud_store: PostgresCloudStore) -> None:
        self._cloud_store = cloud_store
        self._pool: ConnectionPool[tuple[Any, ...]] = ConnectionPool(
            conninfo=dsn, min_size=1, max_size=2, kwargs={"autocommit": False},
            timeout=2.0, reconnect_timeout=5.0, open=False,
        )
        self._lock = threading.Lock()

    @contextmanager
    def _claim_transaction(self) -> Iterator[Connection[tuple[Any, ...]]]:
        try:
            if self._pool.closed:
                with self._lock:
                    if self._pool.closed:
                        self._pool.open(wait=False)
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    yield connection
        except PoolTimeout:
            raise StudioGenerationQueueError("STUDIO_QUEUE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            classified = classify_database_error(error.sqlstate)
            raise StudioGenerationQueueError(classified.code, retryable=classified.retryable) from None

    @staticmethod
    def _from_row(row: Sequence[object], *, state: str = "leased") -> StudioGenerationJob:
        return StudioGenerationJob(
            tenant_id=str(row[0]), workspace_id=str(row[1]), job_id=str(row[2]), actor_id=str(row[3]),
            trace_id=str(row[4]), policy_version=str(row[5]), idempotency_key=str(row[6]),
            request_json=cast(Mapping[str, object], row[7]), state=state,
            attempt=int(row[8]), version=int(row[9]),
        )

    def claim(self, worker_id: str, *, lease_seconds: int = 600) -> StudioGenerationJob | None:
        with self._claim_transaction() as connection:
            row = connection.execute(
                "SELECT * FROM claim_studio_generation_job(%s,%s)", (worker_id, lease_seconds),
            ).fetchone()
        return None if row is None else self._from_row(row)

    def _scope(self, job: StudioGenerationJob) -> CloudAccessContext:
        return CloudAccessContext(job.tenant_id, job.workspace_id, job.actor_id, "studio.create")

    def finish(self, job: StudioGenerationJob, *, state: str, error_code: str | None = None,
               studio_output_id: str | None = None, output_version_id: str | None = None) -> None:
        if state not in {"completed", "failed", "unavailable"}:
            raise StudioGenerationQueueError("STUDIO_JOB_STATE_INVALID")
        with self._cloud_store._transaction(self._scope(job)) as connection:
            result = connection.execute(
                "UPDATE studio_generation_jobs SET state=%s,lease_owner=NULL,lease_until=NULL,"
                "safe_error_code=%s,studio_output_id=%s,output_version_id=%s,completed_at=now(),version=version+1 "
                "WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s AND state='leased' AND version=%s "
                "RETURNING job_id",
                (state, error_code, studio_output_id, output_version_id, job.tenant_id, job.workspace_id, job.job_id, job.version),
            ).fetchone()
            if result is None:
                raise StudioGenerationQueueError("STUDIO_JOB_LEASE_LOST", retryable=True)

    def get(self, context: CloudAccessContext, job_id: str) -> StudioGenerationJob:
        with self._cloud_store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "studio.read")) as connection:
            row = connection.execute(
                "SELECT tenant_id,workspace_id,job_id,actor_id,trace_id,policy_version,idempotency_key,request_json,state,attempt,version,safe_error_code,studio_output_id,output_version_id,created_at,completed_at "
                "FROM studio_generation_jobs WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s",
                (context.tenant_id, context.workspace_id, job_id),
            ).fetchone()
        if row is None:
            raise StudioGenerationQueueError("STUDIO_JOB_NOT_FOUND")
        return StudioGenerationJob(
            tenant_id=str(row[0]), workspace_id=str(row[1]), job_id=str(row[2]), actor_id=str(row[3]),
            trace_id=str(row[4]), policy_version=str(row[5]), idempotency_key=str(row[6]),
            request_json=cast(Mapping[str, object], row[7]), state=str(row[8]), attempt=int(row[9]), version=int(row[10]),
            safe_error_code=None if row[11] is None else str(row[11]),
            studio_output_id=None if row[12] is None else str(row[12]),
            output_version_id=None if row[13] is None else str(row[13]),
            created_at=cast(datetime | None, row[14]),
            completed_at=cast(datetime | None, row[15]),
        )

    def close(self) -> None:
        self._pool.close()
