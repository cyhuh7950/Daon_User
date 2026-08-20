"""Authenticated single-PDF registration over durable object and canon stores."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .cloud_storage import CloudAccessContext
from .data_canon import CanonError, CanonicalContext, PostgresDataCanonStore
from .object_queue import (
    DurableObjectWorker,
    ObjectQueueCoordinator,
    ObjectQueueError,
    ObjectStoragePort,
    PostgresObjectQueueStore,
)


class SourceUploadError(RuntimeError):
    def __init__(self, code: str, status: int = 400, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SourceUploadResult:
    source_id: str
    source_version_id: str
    object_id: str
    digest_sha256: str
    byte_size: int
    status: str
    replayed: bool


class SourceUploadPort(Protocol):
    def register_pdf(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        notebook_id: str,
        actor_id: str,
        filename: str,
        content: bytes,
        idempotency_key: str,
        trace_id: str,
    ) -> SourceUploadResult: ...


class PostgresSourceUploadService:
    """Promote one validated PDF and atomically reconcile its canonical records."""

    def __init__(
        self,
        *,
        queue_store: PostgresObjectQueueStore,
        object_storage: ObjectStoragePort,
        canon_store: PostgresDataCanonStore,
    ) -> None:
        self._queue_store = queue_store
        self._canon_store = canon_store
        self._coordinator = ObjectQueueCoordinator(
            queue_store, object_storage, id_factory=lambda: "0" * 32
        )
        self._worker = DurableObjectWorker(
            queue_store, object_storage, "api-source-upload-worker"
        )

    @staticmethod
    def _opaque_id(scope: str, idempotency_key: str) -> str:
        return hashlib.sha256(f"{scope}|{idempotency_key}".encode("utf-8")).hexdigest()[:32]

    def close(self) -> None:
        self._canon_store.close()

    def register_pdf(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        notebook_id: str,
        actor_id: str,
        filename: str,
        content: bytes,
        idempotency_key: str,
        trace_id: str,
    ) -> SourceUploadResult:
        scope_text = f"{tenant_id}|{workspace_id}|{actor_id}"
        object_id = self._opaque_id(scope_text, idempotency_key)
        source_id = f"src-{object_id}"
        source_version_id = f"sv-{object_id}"
        digest = hashlib.sha256(content).hexdigest()
        cloud_context = CloudAccessContext(
            tenant_id, workspace_id, actor_id, "object.write"
        )
        canon_context = CanonicalContext(
            tenant_id, workspace_id, actor_id, "source.write", trace_id
        )
        try:
            self._queue_store.seed_scope(cloud_context)
            submission = self._coordinator.submit(
                cloud_context,
                area="source",
                content=content,
                content_type="application/pdf",
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                object_id=object_id,
            )
            record = self._queue_store.get_object(cloud_context, submission.object_id)
            for _ in range(64):
                if record.status in {"completed", "failed"}:
                    break
                self._worker.run_once(cloud_context)
                record = self._queue_store.get_object(cloud_context, submission.object_id)
            if record.status != "completed":
                code = "SOURCE_STORAGE_FAILED" if record.status == "failed" else "SOURCE_STORAGE_PENDING"
                raise SourceUploadError(code, 503, retryable=record.status != "failed")
            self._canon_store.register_uploaded_source(
                canon_context,
                notebook_id=notebook_id,
                source_id=source_id,
                source_version_id=source_version_id,
                object_id=submission.object_id,
                filename=filename,
                digest_sha256=digest,
                byte_size=len(content),
                created_at=datetime.now(timezone.utc),
            )
        except SourceUploadError:
            raise
        except ObjectQueueError as error:
            raise SourceUploadError(
                "SOURCE_STORAGE_UNAVAILABLE" if error.retryable else error.code,
                503 if error.retryable else 400,
                retryable=error.retryable,
            ) from None
        except CanonError as error:
            conflict = str(error) == "CANON_SNAPSHOT_INVALID"
            raise SourceUploadError(
                "SOURCE_CANON_CONFLICT" if conflict else "SOURCE_CANON_UNAVAILABLE",
                409 if conflict else 503,
                retryable=not conflict,
            ) from None
        return SourceUploadResult(
            source_id=source_id,
            source_version_id=source_version_id,
            object_id=object_id,
            digest_sha256=digest,
            byte_size=len(content),
            status="accepted",
            replayed=submission.replayed,
        )
