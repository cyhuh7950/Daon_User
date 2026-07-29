"""S3-compatible object storage and PostgreSQL durable object-job foundation."""

from __future__ import annotations

import hashlib
import io
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, cast

from minio import Minio
from minio.commonconfig import CopySource, REPLACE
from minio.error import S3Error
from psycopg import Connection, Error
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from daon_user_api.cloud_storage import CloudAccessContext, classify_database_error


UTC = timezone.utc
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OPAQUE_ID = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]{0,254}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_ALLOWED_AREAS = frozenset(("source", "output"))
_ALLOWED_JOB_KINDS = frozenset(("object.promote",))
_ALLOWED_PAYLOAD_KEYS = frozenset(("schema_version", "object_id"))


class ObjectQueueError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class ObjectStorageError(ObjectQueueError):
    pass


@dataclass(frozen=True, slots=True)
class StagedObject:
    key: str
    digest_sha256: str
    byte_size: int
    content_type: str
    etag: str
    version_id: str | None


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    digest_sha256: str
    byte_size: int
    content_type: str
    etag: str
    version_id: str | None


class ObjectStoragePort(Protocol):
    def health(self) -> bool: ...

    def put_staged(self, key: str, content: bytes, content_type: str, digest: str) -> StagedObject: ...

    def promote(
        self,
        staged: StagedObject,
        final_key: str,
        *,
        expected_digest: str,
        expected_size: int,
        content_type: str,
    ) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    object_id: str
    area: str
    staging_key: str
    object_key: str
    digest_sha256: str
    byte_size: int
    content_type: str
    status: str
    storage_etag: str | None
    storage_version_id: str | None
    version: int


@dataclass(frozen=True, slots=True)
class ObjectSubmission:
    object_id: str
    job_id: str
    event_id: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ObjectJob:
    job_id: str
    event_id: str
    object_id: str
    state: str
    attempt: int
    max_attempts: int
    next_attempt_at: datetime
    lease_owner: str | None
    lease_until: datetime | None
    last_safe_error_code: str | None
    retry_of_job_id: str | None
    trace_id: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    pending: int
    retry_wait: int
    leased: int
    dead_letter: int
    oldest_pending_seconds: float


@dataclass(frozen=True, slots=True)
class ObjectQueueHealth:
    object_storage_ready: bool
    queue: QueueMetrics


class ObjectKeyPolicy:
    """Build and validate server-owned ASCII prefixes and opaque object IDs."""

    @staticmethod
    def _validate(scope: CloudAccessContext, area: str, object_id: str) -> None:
        if area not in _ALLOWED_AREAS or not _OPAQUE_ID.fullmatch(object_id):
            raise ObjectQueueError("OBJECT_KEY_INVALID")
        for value in (scope.tenant_id, scope.workspace_id):
            if not _SAFE_ID.fullmatch(value):
                raise ObjectQueueError("OBJECT_KEY_INVALID")

    def final_key(self, scope: CloudAccessContext, area: str, object_id: str) -> str:
        self._validate(scope, area, object_id)
        return f"{scope.tenant_id}/{scope.workspace_id}/{area}/{object_id}"

    def staging_key(self, scope: CloudAccessContext, area: str, object_id: str) -> str:
        self._validate(scope, area, object_id)
        return f"_staging/{scope.tenant_id}/{scope.workspace_id}/{area}/{object_id}"

    def validate_final(self, scope: CloudAccessContext, area: str, key: str) -> None:
        if not isinstance(key, str) or not key.isascii() or any(ord(char) < 32 for char in key):
            raise ObjectQueueError("OBJECT_KEY_INVALID")
        object_id = key.rsplit("/", 1)[-1]
        if key != self.final_key(scope, area, object_id):
            raise ObjectQueueError("OBJECT_KEY_INVALID")

    def validate_staging(self, scope: CloudAccessContext, area: str, key: str) -> None:
        if not isinstance(key, str) or not key.isascii() or any(ord(char) < 32 for char in key):
            raise ObjectQueueError("OBJECT_KEY_INVALID")
        object_id = key.rsplit("/", 1)[-1]
        if key != self.staging_key(scope, area, object_id):
            raise ObjectQueueError("OBJECT_KEY_INVALID")


class MinioObjectStorageAdapter:
    """Server-only MinIO/S3-compatible adapter. Raw provider failures never escape."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        secure: bool = True,
    ) -> None:
        if (
            not isinstance(endpoint, str)
            or not endpoint
            or "://" in endpoint
            or "/" in endpoint
            or "@" in endpoint
            or any(ord(char) < 33 for char in endpoint)
        ):
            raise ValueError("OBJECT_ENDPOINT_INVALID")
        if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket):
            raise ValueError("OBJECT_BUCKET_INVALID")
        if not access_key or not secret_key:
            raise ValueError("OBJECT_CREDENTIAL_REQUIRED")
        self._bucket = bucket
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    @staticmethod
    def _safe_error(error: Exception) -> ObjectStorageError:
        if isinstance(error, S3Error) and error.code in {
            "AccessDenied", "InvalidAccessKeyId", "InvalidArgument", "InvalidBucketName", "NoSuchKey",
        }:
            code = "OBJECT_NOT_FOUND" if error.code == "NoSuchKey" else "OBJECT_STORAGE_ACCESS_DENIED"
            return ObjectStorageError(code, retryable=False)
        return ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)

    @staticmethod
    def _metadata(digest: str, size: int, content_type: str) -> dict[str, str]:
        return {"sha256": digest, "byte-size": str(size), "verified-content-type": content_type}

    @staticmethod
    def _read_metadata(metadata: Mapping[str, str] | None, name: str) -> str | None:
        wanted = name.lower()
        for key, value in (metadata or {}).items():
            normalized = key.lower().removeprefix("x-amz-meta-")
            if normalized == wanted:
                return str(value)
        return None

    def _stat_verified(
        self, key: str, *, expected_digest: str, expected_size: int, content_type: str,
    ) -> StoredObject:
        try:
            item = self._client.stat_object(self._bucket, key)
        except Exception as error:
            raise self._safe_error(error) from None
        digest = self._read_metadata(item.metadata, "sha256")
        size = int(item.size)
        verified_type = self._read_metadata(item.metadata, "verified-content-type")
        if digest != expected_digest or size != expected_size or verified_type != content_type:
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH", retryable=False)
        return StoredObject(key, digest, size, content_type, str(item.etag or ""), item.version_id)

    def health(self) -> bool:
        try:
            return bool(self._client.bucket_exists(self._bucket))
        except Exception:
            return False

    def put_staged(self, key: str, content: bytes, content_type: str, digest: str) -> StagedObject:
        if not isinstance(content, bytes) or not _DIGEST.fullmatch(digest):
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH", retryable=False)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH", retryable=False)
        try:
            result = self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(content),
                len(content),
                content_type=content_type,
                metadata=self._metadata(digest, len(content), content_type),
            )
        except Exception as error:
            raise self._safe_error(error) from None
        verified = self._stat_verified(
            key, expected_digest=digest, expected_size=len(content), content_type=content_type
        )
        return StagedObject(
            verified.key, verified.digest_sha256, verified.byte_size, verified.content_type,
            str(result.etag or verified.etag), result.version_id or verified.version_id,
        )

    def promote(
        self,
        staged: StagedObject,
        final_key: str,
        *,
        expected_digest: str,
        expected_size: int,
        content_type: str,
    ) -> StoredObject:
        if (
            staged.digest_sha256 != expected_digest
            or staged.byte_size != expected_size
            or staged.content_type != content_type
        ):
            raise ObjectStorageError("OBJECT_CHECKSUM_MISMATCH", retryable=False)
        metadata = self._metadata(expected_digest, expected_size, content_type)
        try:
            self._client.copy_object(
                self._bucket,
                final_key,
                CopySource(self._bucket, staged.key),
                metadata=metadata,
                metadata_directive=REPLACE,
            )
        except Exception as error:
            raise self._safe_error(error) from None
        return self._stat_verified(
            final_key,
            expected_digest=expected_digest,
            expected_size=expected_size,
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        response = None
        try:
            response = self._client.get_object(self._bucket, key)
            return cast(bytes, response.read())
        except Exception as error:
            raise self._safe_error(error) from None
        finally:
            if response is not None:
                response.close()
                response.release_conn()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 2.0
    max_seconds: float = 300.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if (
            not 1 <= self.max_attempts <= 100
            or self.base_seconds <= 0
            or self.max_seconds < self.base_seconds
            or not 0 <= self.jitter_ratio <= 1
        ):
            raise ValueError("RETRY_POLICY_INVALID")

    def delay_seconds(self, attempt: int, *, jitter_unit: float) -> float:
        if attempt < 1 or not 0 <= jitter_unit <= 1:
            raise ValueError("RETRY_POLICY_INPUT_INVALID")
        bounded = min(self.max_seconds, self.base_seconds * (2 ** (attempt - 1)))
        factor = 1 - self.jitter_ratio + (2 * self.jitter_ratio * jitter_unit)
        return float(min(self.max_seconds, bounded * factor))

    def exhausted(self, attempt: int) -> bool:
        return attempt >= self.max_attempts


class PostgresObjectQueueStore:
    """Workspace-scoped durable queue. Callers provide a server-verified scope."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 6) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
        self._pool = ConnectionPool[tuple[Any, ...]](
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": False},
            timeout=2.0,
            reconnect_timeout=5.0,
            open=False,
        )
        self._open_lock = threading.Lock()

    def _ensure_open(self) -> None:
        if not self._pool.closed:
            return
        with self._open_lock:
            if self._pool.closed:
                self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _transaction(self, context: CloudAccessContext) -> Iterator[Connection[tuple[Any, ...]]]:
        try:
            self._ensure_open()
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (context.tenant_id,))
                    connection.execute("SELECT set_config('app.workspace_id', %s, true)", (context.workspace_id,))
                    connection.execute("SELECT set_config('app.actor_id', %s, true)", (context.actor_id,))
                    connection.execute("SELECT set_config('app.capability', %s, true)", (context.capability,))
                    yield connection
        except ObjectQueueError:
            raise
        except PoolTimeout:
            raise ObjectQueueError("DATABASE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            classified = classify_database_error(error.sqlstate)
            raise ObjectQueueError(classified.code, retryable=classified.retryable) from None

    @staticmethod
    def _safe_id(value: str, code: str) -> str:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise ObjectQueueError(code)
        return value

    @staticmethod
    def validate_payload(
        context: CloudAccessContext, job_kind: str, payload: Mapping[str, object],
    ) -> dict[str, object]:
        del context
        if job_kind not in _ALLOWED_JOB_KINDS or set(payload) != _ALLOWED_PAYLOAD_KEYS:
            raise ObjectQueueError("JOB_PAYLOAD_INVALID")
        if payload.get("schema_version") != 1:
            raise ObjectQueueError("JOB_PAYLOAD_INVALID")
        object_id = payload.get("object_id")
        if not isinstance(object_id, str) or not _OPAQUE_ID.fullmatch(object_id):
            raise ObjectQueueError("JOB_PAYLOAD_INVALID")
        return {"schema_version": 1, "object_id": object_id}

    @staticmethod
    def _job_from_row(row: Sequence[object]) -> ObjectJob:
        payload = cast(dict[str, object], row[2])
        return ObjectJob(
            job_id=str(row[0]),
            event_id=str(row[1]),
            object_id=str(payload["object_id"]),
            state=str(row[3]),
            attempt=cast(int, row[4]),
            max_attempts=cast(int, row[5]),
            next_attempt_at=cast(datetime, row[6]),
            lease_owner=None if row[7] is None else str(row[7]),
            lease_until=cast(datetime | None, row[8]),
            last_safe_error_code=None if row[9] is None else str(row[9]),
            retry_of_job_id=None if row[10] is None else str(row[10]),
            trace_id=str(row[11]),
            version=cast(int, row[12]),
            created_at=cast(datetime, row[13]),
        )

    @staticmethod
    def _object_from_row(row: Sequence[object]) -> ObjectRecord:
        return ObjectRecord(
            object_id=str(row[0]), area=str(row[1]), staging_key=str(row[2]), object_key=str(row[3]),
            digest_sha256=str(row[4]), byte_size=cast(int, row[5]), content_type=str(row[6]), status=str(row[7]),
            storage_etag=None if row[8] is None else str(row[8]),
            storage_version_id=None if row[9] is None else str(row[9]), version=cast(int, row[10]),
        )

    def seed_scope(self, context: CloudAccessContext) -> None:
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.tenant_id),
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id, workspace_id, display_name) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.workspace_id),
            )

    def register_object_job(
        self,
        context: CloudAccessContext,
        *,
        object_id: str,
        area: str,
        staged: StagedObject,
        final_key: str,
        idempotency_key: str,
        trace_id: str,
        max_attempts: int = 5,
        now: datetime | None = None,
        force_rollback: bool = False,
    ) -> ObjectSubmission:
        if context.capability != "object.write":
            raise ObjectQueueError("OBJECT_ACCESS_DENIED")
        ObjectKeyPolicy().validate_staging(context, area, staged.key)
        ObjectKeyPolicy().validate_final(context, area, final_key)
        self._safe_id(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
        self._safe_id(trace_id, "TRACE_ID_INVALID")
        if not _DIGEST.fullmatch(staged.digest_sha256) or not _CONTENT_TYPE.fullmatch(staged.content_type):
            raise ObjectQueueError("OBJECT_METADATA_INVALID")
        if staged.byte_size < 0 or not 1 <= max_attempts <= 100:
            raise ObjectQueueError("OBJECT_METADATA_INVALID")
        created_at = now or datetime.now(UTC)
        fingerprint = hashlib.sha256(
            f"{area}|{staged.digest_sha256}|{staged.byte_size}|{staged.content_type}".encode()
        ).hexdigest()
        event_id = f"outbox-{object_id}"
        job_id = f"job-{object_id}"
        payload = {"schema_version": 1, "object_id": object_id}
        with self._transaction(context) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}",),
            )
            replay = connection.execute(
                "SELECT object_id, request_fingerprint FROM object_records "
                "WHERE created_by = %s AND idempotency_key = %s",
                (context.actor_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if str(replay[1]) != fingerprint:
                    raise ObjectQueueError("IDEMPOTENCY_KEY_REUSED")
                replay_object_id = str(replay[0])
                return ObjectSubmission(
                    replay_object_id, f"job-{replay_object_id}", f"outbox-{replay_object_id}", True
                )
            connection.execute(
                "INSERT INTO object_records "
                "(tenant_id, workspace_id, object_id, area, staging_key, object_key, digest_sha256, byte_size, "
                "content_type, status, created_by, trace_id, idempotency_key, request_fingerprint, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)",
                (context.tenant_id, context.workspace_id, object_id, area, staged.key, final_key,
                 staged.digest_sha256, staged.byte_size, staged.content_type, context.actor_id, trace_id,
                 idempotency_key, fingerprint, created_at),
            )
            connection.execute(
                "INSERT INTO object_outbox_events "
                "(tenant_id, workspace_id, event_id, object_id, event_kind, payload_reference, schema_version, "
                "status, trace_id, created_at) VALUES (%s,%s,%s,%s,'object.promote',%s,1,'pending',%s,%s)",
                (context.tenant_id, context.workspace_id, event_id, object_id, Jsonb(payload), trace_id, created_at),
            )
            connection.execute(
                "INSERT INTO durable_jobs "
                "(tenant_id, workspace_id, job_id, event_id, job_kind, payload_reference, payload_schema_version, "
                "deduplication_key, state, max_attempts, next_attempt_at, created_by, trace_id, created_at) "
                "VALUES (%s,%s,%s,%s,'object.promote',%s,1,%s,'pending',%s,%s,%s,%s,%s)",
                (context.tenant_id, context.workspace_id, job_id, event_id, Jsonb(payload), idempotency_key,
                 max_attempts, created_at, context.actor_id, trace_id, created_at),
            )
            connection.execute(
                "INSERT INTO audit_events "
                "(event_id, tenant_id, workspace_id, actor_id, action, target_type, target_id, outcome, "
                "trace_id, policy_version, metadata) VALUES (%s,%s,%s,%s,'object.store.requested',"
                "'object',%s,'succeeded',%s,'object-queue-v1',%s)",
                (f"audit-request-{object_id}", context.tenant_id, context.workspace_id, context.actor_id,
                 object_id, trace_id, Jsonb({"area": area, "job_id": job_id})),
            )
            if force_rollback:
                raise ObjectQueueError("FORCED_TRANSACTION_ROLLBACK")
        return ObjectSubmission(object_id, job_id, event_id, False)

    def claim(
        self,
        context: CloudAccessContext,
        worker_id: str,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> ObjectJob | None:
        self._safe_id(worker_id, "WORKER_ID_INVALID")
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ObjectQueueError("LEASE_INVALID")
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._transaction(context) as connection:
            row = connection.execute(
                "WITH candidate AS ("
                " SELECT tenant_id, workspace_id, job_id FROM durable_jobs"
                " WHERE ((state IN ('pending','retry_wait') AND next_attempt_at <= %s)"
                "    OR (state = 'leased' AND lease_until <= %s))"
                " ORDER BY next_attempt_at, created_at, job_id"
                " FOR UPDATE SKIP LOCKED LIMIT 1"
                ") UPDATE durable_jobs AS job SET state='leased', attempt=job.attempt+1, lease_owner=%s,"
                " lease_until=%s, last_safe_error_code=NULL, version=job.version+1"
                " FROM candidate WHERE job.tenant_id=candidate.tenant_id"
                " AND job.workspace_id=candidate.workspace_id AND job.job_id=candidate.job_id"
                " RETURNING job.job_id, job.event_id, job.payload_reference, job.state, job.attempt,"
                " job.max_attempts, job.next_attempt_at, job.lease_owner, job.lease_until,"
                " job.last_safe_error_code, job.retry_of_job_id, job.trace_id, job.version, job.created_at",
                (now, now, worker_id, lease_until),
            ).fetchone()
            if row is None:
                return None
            job = self._job_from_row(row)
            self.validate_payload(context, "object.promote", {"schema_version": 1, "object_id": job.object_id})
            connection.execute(
                "UPDATE object_outbox_events SET status='dispatched' WHERE event_id=%s",
                (job.event_id,),
            )
        return job

    def get_object(self, context: CloudAccessContext, object_id: str) -> ObjectRecord:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT object_id, area, staging_key, object_key, digest_sha256, byte_size, content_type, "
                "status, storage_etag, storage_version_id, version FROM object_records WHERE object_id=%s",
                (object_id,),
            ).fetchone()
        if row is None:
            raise ObjectQueueError("OBJECT_NOT_FOUND")
        return self._object_from_row(row)

    def get_job(self, context: CloudAccessContext, job_id: str) -> ObjectJob:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at "
                "FROM durable_jobs WHERE job_id=%s",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ObjectQueueError("JOB_NOT_FOUND")
        return self._job_from_row(row)

    @staticmethod
    def _assert_lease(job: ObjectJob, current: ObjectJob, worker_id: str, now: datetime) -> None:
        if (
            current.state != "leased"
            or current.version != job.version
            or current.lease_owner != worker_id
            or current.lease_until is None
            or current.lease_until <= now
        ):
            raise ObjectQueueError("JOB_LEASE_LOST", retryable=True)

    def complete(
        self,
        context: CloudAccessContext,
        job: ObjectJob,
        worker_id: str,
        stored: StoredObject,
        *,
        now: datetime,
    ) -> ObjectJob:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at "
                "FROM durable_jobs WHERE job_id=%s FOR UPDATE",
                (job.job_id,),
            ).fetchone()
            if row is None:
                raise ObjectQueueError("JOB_NOT_FOUND")
            current = self._job_from_row(row)
            self._assert_lease(job, current, worker_id, now)
            object_row = connection.execute(
                "SELECT object_id, area, staging_key, object_key, digest_sha256, byte_size, content_type, "
                "status, storage_etag, storage_version_id, version FROM object_records WHERE object_id=%s FOR UPDATE",
                (job.object_id,),
            ).fetchone()
            if object_row is None:
                raise ObjectQueueError("OBJECT_NOT_FOUND")
            record = self._object_from_row(object_row)
            if (
                stored.key != record.object_key
                or stored.digest_sha256 != record.digest_sha256
                or stored.byte_size != record.byte_size
                or stored.content_type != record.content_type
            ):
                raise ObjectQueueError("OBJECT_CHECKSUM_MISMATCH")
            connection.execute(
                "UPDATE object_records SET status='completed', storage_etag=%s, storage_version_id=%s, "
                "completed_at=%s, version=version+1 WHERE object_id=%s",
                (stored.etag, stored.version_id, now, job.object_id),
            )
            row = connection.execute(
                "UPDATE durable_jobs SET state='completed', lease_owner=NULL, lease_until=NULL, completed_at=%s, "
                "version=version+1 WHERE job_id=%s RETURNING job_id, event_id, payload_reference, state, attempt, "
                "max_attempts, next_attempt_at, lease_owner, lease_until, last_safe_error_code, retry_of_job_id, "
                "trace_id, version, created_at",
                (now, job.job_id),
            ).fetchone()
            connection.execute(
                "UPDATE object_outbox_events SET status='completed', completed_at=%s WHERE event_id=%s",
                (now, job.event_id),
            )
            connection.execute(
                "INSERT INTO job_attempts (tenant_id, workspace_id, job_id, attempt_number, worker_id, outcome, "
                "trace_id, started_at, finished_at) VALUES (%s,%s,%s,%s,%s,'completed',%s,%s,%s)",
                (context.tenant_id, context.workspace_id, job.job_id, job.attempt, worker_id, job.trace_id,
                 job.created_at, now),
            )
            connection.execute(
                "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, target_type, "
                "target_id, outcome, trace_id, policy_version, metadata) VALUES (%s,%s,%s,%s,"
                "'object.store.completed','object',%s,'succeeded',%s,'object-queue-v1',%s)",
                (f"audit-complete-{job.job_id}-{job.attempt}", context.tenant_id, context.workspace_id,
                 context.actor_id, job.object_id, job.trace_id, Jsonb({"job_id": job.job_id})),
            )
        assert row is not None
        return self._job_from_row(row)

    def fail(
        self,
        context: CloudAccessContext,
        job: ObjectJob,
        worker_id: str,
        error: ObjectQueueError,
        *,
        now: datetime,
        retry_at: datetime,
    ) -> ObjectJob:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at "
                "FROM durable_jobs WHERE job_id=%s FOR UPDATE",
                (job.job_id,),
            ).fetchone()
            if row is None:
                raise ObjectQueueError("JOB_NOT_FOUND")
            current = self._job_from_row(row)
            self._assert_lease(job, current, worker_id, now)
            retry = error.retryable and job.attempt < job.max_attempts
            state = "retry_wait" if retry else "dead_letter"
            outbox_state = "dispatched" if retry else "failed"
            row = connection.execute(
                "UPDATE durable_jobs SET state=%s, next_attempt_at=%s, lease_owner=NULL, lease_until=NULL, "
                "last_safe_error_code=%s, completed_at=%s, version=version+1 WHERE job_id=%s "
                "RETURNING job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at",
                (state, retry_at, error.code, None if retry else now, job.job_id),
            ).fetchone()
            connection.execute(
                "UPDATE object_outbox_events SET status=%s, completed_at=%s WHERE event_id=%s",
                (outbox_state, None if retry else now, job.event_id),
            )
            if not retry:
                connection.execute(
                    "UPDATE object_records SET status='failed', version=version+1 WHERE object_id=%s",
                    (job.object_id,),
                )
            connection.execute(
                "INSERT INTO job_attempts (tenant_id, workspace_id, job_id, attempt_number, worker_id, outcome, "
                "safe_error_code, trace_id, started_at, finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (context.tenant_id, context.workspace_id, job.job_id, job.attempt, worker_id, state, error.code,
                 job.trace_id, job.created_at, now),
            )
            connection.execute(
                "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, target_type, "
                "target_id, outcome, trace_id, policy_version, metadata) VALUES (%s,%s,%s,%s,"
                "'object.store.attempt','job',%s,'failed',%s,'object-queue-v1',%s)",
                (f"audit-attempt-{job.job_id}-{job.attempt}", context.tenant_id, context.workspace_id,
                 context.actor_id, job.job_id, job.trace_id,
                 Jsonb({"state": state, "safe_error_code": error.code})),
            )
        assert row is not None
        return self._job_from_row(row)

    def reprocess(self, context: CloudAccessContext, job_id: str, idempotency_key: str) -> ObjectJob:
        if context.capability != "queue.reprocess":
            raise ObjectQueueError("QUEUE_REPROCESS_DENIED")
        self._safe_id(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
        created_at = datetime.now(UTC)
        digest = hashlib.sha256(
            f"{context.tenant_id}|{context.workspace_id}|{job_id}|{idempotency_key}".encode()
        ).hexdigest()[:32]
        new_job_id = f"job-{digest}"
        new_event_id = f"outbox-{digest}"
        dedup = f"reprocess:{job_id}:{idempotency_key}"
        with self._transaction(context) as connection:
            existing = connection.execute(
                "SELECT job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at "
                "FROM durable_jobs WHERE job_kind='object.promote' AND deduplication_key=%s",
                (dedup,),
            ).fetchone()
            if existing is not None:
                return self._job_from_row(existing)
            old_row = connection.execute(
                "SELECT job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at "
                "FROM durable_jobs WHERE job_id=%s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if old_row is None:
                raise ObjectQueueError("JOB_NOT_FOUND")
            old = self._job_from_row(old_row)
            if old.state != "dead_letter":
                raise ObjectQueueError("JOB_NOT_DEAD_LETTER")
            payload = {"schema_version": 1, "object_id": old.object_id}
            connection.execute(
                "INSERT INTO object_outbox_events (tenant_id, workspace_id, event_id, object_id, event_kind, "
                "payload_reference, schema_version, status, trace_id, created_at) "
                "VALUES (%s,%s,%s,%s,'object.promote',%s,1,'pending',%s,%s)",
                (context.tenant_id, context.workspace_id, new_event_id, old.object_id, Jsonb(payload),
                 old.trace_id, created_at),
            )
            row = connection.execute(
                "INSERT INTO durable_jobs (tenant_id, workspace_id, job_id, event_id, job_kind, "
                "payload_reference, payload_schema_version, deduplication_key, state, max_attempts, "
                "next_attempt_at, retry_of_job_id, created_by, trace_id, created_at) "
                "VALUES (%s,%s,%s,%s,'object.promote',%s,1,%s,'pending',%s,%s,%s,%s,%s,%s) "
                "RETURNING job_id, event_id, payload_reference, state, attempt, max_attempts, next_attempt_at, "
                "lease_owner, lease_until, last_safe_error_code, retry_of_job_id, trace_id, version, created_at",
                (context.tenant_id, context.workspace_id, new_job_id, new_event_id, Jsonb(payload), dedup,
                 old.max_attempts, created_at, old.job_id, context.actor_id, old.trace_id, created_at),
            ).fetchone()
            connection.execute(
                "UPDATE object_records SET status='pending', version=version+1 WHERE object_id=%s",
                (old.object_id,),
            )
            connection.execute(
                "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, target_type, "
                "target_id, outcome, trace_id, policy_version, metadata) VALUES (%s,%s,%s,%s,"
                "'object.store.reprocessed','job',%s,'succeeded',%s,'object-queue-v1',%s)",
                (f"audit-reprocess-{digest}", context.tenant_id, context.workspace_id, context.actor_id,
                 new_job_id, old.trace_id, Jsonb({"retry_of_job_id": old.job_id})),
            )
        assert row is not None
        return self._job_from_row(row)

    def metrics(self, context: CloudAccessContext, *, now: datetime) -> QueueMetrics:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT count(*) FILTER (WHERE state='pending'), "
                "count(*) FILTER (WHERE state='retry_wait'), count(*) FILTER (WHERE state='leased'), "
                "count(*) FILTER (WHERE state='dead_letter'), "
                "coalesce(extract(epoch FROM (%s - min(created_at) FILTER "
                "(WHERE state IN ('pending','retry_wait')))), 0) FROM durable_jobs",
                (now,),
            ).fetchone()
        assert row is not None
        return QueueMetrics(int(row[0]), int(row[1]), int(row[2]), int(row[3]), max(0.0, float(row[4])))

    def entity_counts(self, context: CloudAccessContext, object_id: str) -> dict[str, int]:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT (SELECT count(*) FROM object_records WHERE object_id=%s), "
                "(SELECT count(*) FROM object_outbox_events WHERE object_id=%s), "
                "(SELECT count(*) FROM durable_jobs WHERE payload_reference->>'object_id'=%s), "
                "(SELECT count(*) FROM job_attempts WHERE job_id IN "
                " (SELECT job_id FROM durable_jobs WHERE payload_reference->>'object_id'=%s))",
                (object_id, object_id, object_id, object_id),
            ).fetchone()
        assert row is not None
        return {"objects": int(row[0]), "outbox": int(row[1]), "jobs": int(row[2]), "attempts": int(row[3])}

    def context_is_clear(self) -> bool:
        try:
            self._ensure_open()
            with self._pool.connection(timeout=2.0) as connection:
                row = connection.execute(
                    "SELECT nullif(current_setting('app.tenant_id', true), ''), "
                    "nullif(current_setting('app.workspace_id', true), '')"
                ).fetchone()
            return row is not None and row[0] is None and row[1] is None
        except (Error, PoolTimeout):
            raise ObjectQueueError("DATABASE_UNAVAILABLE", retryable=True) from None


class ObjectQueueCoordinator:
    def __init__(
        self,
        store: PostgresObjectQueueStore,
        storage: ObjectStoragePort,
        *,
        id_factory: Callable[[], str],
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._store = store
        self._storage = storage
        self._id_factory = id_factory
        self._retry_policy = retry_policy or RetryPolicy()
        self._key_policy = ObjectKeyPolicy()

    def submit(
        self,
        context: CloudAccessContext,
        *,
        area: str,
        content: bytes,
        content_type: str,
        idempotency_key: str,
        trace_id: str,
    ) -> ObjectSubmission:
        if not isinstance(content, bytes) or not _CONTENT_TYPE.fullmatch(content_type):
            raise ObjectQueueError("OBJECT_METADATA_INVALID")
        object_id = self._id_factory()
        if not _OPAQUE_ID.fullmatch(object_id):
            raise ObjectQueueError("OBJECT_ID_INVALID")
        digest = hashlib.sha256(content).hexdigest()
        staging_key = self._key_policy.staging_key(context, area, object_id)
        final_key = self._key_policy.final_key(context, area, object_id)
        staged = self._storage.put_staged(staging_key, content, content_type, digest)
        return self._store.register_object_job(
            context,
            object_id=object_id,
            area=area,
            staged=staged,
            final_key=final_key,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            max_attempts=self._retry_policy.max_attempts,
        )

    def health(self, context: CloudAccessContext, *, now: datetime) -> ObjectQueueHealth:
        return ObjectQueueHealth(self._storage.health(), self._store.metrics(context, now=now))


class DurableObjectWorker:
    def __init__(
        self,
        store: PostgresObjectQueueStore,
        storage: ObjectStoragePort,
        worker_id: str,
        *,
        lease_seconds: float = 30.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._store = store
        self._storage = storage
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._retry_policy = retry_policy or RetryPolicy()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def process_claimed(
        self,
        context: CloudAccessContext,
        job: ObjectJob,
        *,
        now: datetime,
        jitter_unit: float = 0.5,
    ) -> ObjectJob:
        record = self._store.get_object(context, job.object_id)
        staged = StagedObject(
            record.staging_key, record.digest_sha256, record.byte_size, record.content_type, "", None
        )
        try:
            stored = self._storage.promote(
                staged,
                record.object_key,
                expected_digest=record.digest_sha256,
                expected_size=record.byte_size,
                content_type=record.content_type,
            )
            return self._store.complete(context, job, self._worker_id, stored, now=now)
        except ObjectQueueError as error:
            delay = self._retry_policy.delay_seconds(job.attempt, jitter_unit=jitter_unit)
            return self._store.fail(
                context,
                job,
                self._worker_id,
                error,
                now=now,
                retry_at=now + timedelta(seconds=delay),
            )

    def run_once(
        self,
        context: CloudAccessContext,
        *,
        now: datetime | None = None,
        jitter_unit: float = 0.5,
    ) -> ObjectJob | None:
        if self._stop.is_set():
            return None
        current = now or datetime.now(UTC)
        job = self._store.claim(
            context, self._worker_id, now=current, lease_seconds=self._lease_seconds
        )
        if job is None:
            return None
        return self.process_claimed(context, job, now=current, jitter_unit=jitter_unit)

    def start(self, scopes: Sequence[CloudAccessContext], *, poll_seconds: float = 0.2) -> None:
        if self.running or poll_seconds <= 0:
            raise ObjectQueueError("WORKER_ALREADY_RUNNING")
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                processed = False
                for scope in scopes:
                    if self._stop.is_set():
                        break
                    try:
                        processed = self.run_once(scope) is not None or processed
                    except ObjectQueueError as error:
                        if not error.retryable:
                            raise
                        self._stop.wait(poll_seconds)
                if not processed:
                    self._stop.wait(poll_seconds)

        self._thread = threading.Thread(target=run, name=f"object-worker-{self._worker_id}", daemon=True)
        self._thread.start()

    def shutdown(self, *, timeout: float) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise ObjectQueueError("WORKER_SHUTDOWN_TIMEOUT", retryable=True)
