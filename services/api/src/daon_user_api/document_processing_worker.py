"""Independent durable document-processing worker process."""

from __future__ import annotations

import os
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .cloud_storage import PostgresCloudStore
from .document_index_postgres import PostgresDocumentIndex
from .document_processing import (
    DefaultDocumentAdapterFactory,
    DocumentProcessingContext,
    DocumentProcessingService,
    OriginalDocumentEgressGuard,
)
from .document_processing_postgres import PostgresDocumentProcessingRepository
from .document_processing_queue import PostgresDocumentProcessingQueue
from .document_understanding_adapter import (
    DocumentUnderstandingError,
)
from .object_queue import MinioObjectStorageAdapter
from .provider_credentials import ProviderCredentialCipher
from .workspace_model_defaults import PostgresWorkspaceModelResolver
from .egress_policy import EgressPolicyService
from .egress_policy_postgres import PostgresEgressPolicyRepository
from .operations_telemetry import observe_poll,safe_hook,start_worker_observation,worker_publisher
from .document_readiness import ReadinessDispatcherSettings


class QueuePort(Protocol):
    def claim(self, worker_id: str, *, lease_seconds: int = 120): ...  # type: ignore[no-untyped-def]
    def complete(self, job, worker_id: str, *, now: datetime) -> None: ...  # type: ignore[no-untyped-def]
    def fail_terminal(
        self, job, worker_id: str, safe_error_code: str, *, now: datetime,
    ) -> None: ...  # type: ignore[no-untyped-def]


class ProcessingPort(Protocol):
    def process_existing(
        self, context: DocumentProcessingContext, *, source_id: str,
        processing_run_id: str,
    ): ...  # type: ignore[no-untyped-def]


class IndexPort(Protocol):
    def index_result(self, context: DocumentProcessingContext, result): ...  # type: ignore[no-untyped-def]


class DocumentProcessingWorker:
    def __init__(
        self, worker_id: str, queue: QueuePort, processing: ProcessingPort, index: IndexPort,
        *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lease_seconds: int = 600,
        telemetry=None,
    ) -> None:
        self._worker_id = worker_id
        self._queue = queue
        self._processing = processing
        self._index = index
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._telemetry = telemetry

    def run_once(self) -> bool:
        with observe_poll(self._telemetry):
            job = self._queue.claim(self._worker_id, lease_seconds=self._lease_seconds)
            if job is None:
                return False
            safe_hook(self._telemetry,"processing_started")
            context = DocumentProcessingContext(
                job.tenant_id, job.workspace_id, job.created_by,
                job.trace_id, job.policy_version,
            )
            try:
                result = self._processing.process_existing(
                    context, source_id=job.source_id,
                    processing_run_id=job.processing_run_id,
                )
                if result.status == "ready":
                    self._index.index_result(context, result)
                self._queue.complete(job, self._worker_id, now=self._clock())
            except DocumentUnderstandingError as error:
                self._queue.fail_terminal(
                    job, self._worker_id, error.code, now=self._clock(),
                )
            return True

    def run_forever(self, stop: threading.Event, *, poll_seconds: float = 0.5) -> None:
        if poll_seconds <= 0 or poll_seconds > 60:
            raise ValueError("DOCUMENT_WORKER_POLL_INVALID")
        try:
            if not stop.is_set():start_worker_observation(self._telemetry,"document",self._worker_id)
            while not stop.is_set():
                if not self.run_once():
                    stop.wait(poll_seconds)
        finally:safe_hook(self._telemetry,"stop")


@dataclass(frozen=True, slots=True)
class DocumentWorkerSettings:
    worker_id: str
    database_dsn: str
    object_endpoint: str
    object_bucket: str
    object_access_key_file: Path
    object_secret_key_file: Path
    provider_credential_key_file: Path
    object_secure: bool
    lease_seconds: int
    poll_seconds: float
    notification_inbox_enabled: bool = False
    readiness_settings: ReadinessDispatcherSettings = field(default_factory=ReadinessDispatcherSettings)

    def __post_init__(self) -> None:
        if type(self.notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        if type(self.readiness_settings) is not ReadinessDispatcherSettings:
            raise ValueError("DOCUMENT_PROCESSING_CONTEXT_INVALID")

    @classmethod
    def from_env(cls) -> DocumentWorkerSettings:
        required = {
            "database_dsn": os.environ.get("DAON_CLOUD_DATABASE_DSN", "").strip(),
            "object_endpoint": os.environ.get("DAON_OBJECT_STORAGE_ENDPOINT", "").strip(),
            "object_bucket": os.environ.get("DAON_OBJECT_STORAGE_BUCKET", "").strip(),
            "access": os.environ.get("DAON_OBJECT_ACCESS_KEY_FILE", "").strip(),
            "secret": os.environ.get("DAON_OBJECT_SECRET_KEY_FILE", "").strip(),
            "provider_key": os.environ.get("DAON_PROVIDER_CREDENTIAL_KEY_FILE", "").strip(),
        }
        if any(not value for value in required.values()):
            raise ValueError("DOCUMENT_WORKER_ENV_REQUIRED")
        notification_flag = os.environ.get("DAON_NOTIFICATION_INBOX_ENABLED", "false")
        if notification_flag not in {"true", "false"}:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        try:
            readiness_settings = ReadinessDispatcherSettings.from_env()
        except DocumentUnderstandingError:
            raise ValueError("DOCUMENT_PROCESSING_CONTEXT_INVALID") from None
        return cls(
            worker_id=os.environ.get("DAON_DOCUMENT_WORKER_ID", "document-worker-1"),
            database_dsn=required["database_dsn"],
            object_endpoint=required["object_endpoint"],
            object_bucket=required["object_bucket"],
            object_access_key_file=Path(required["access"]),
            object_secret_key_file=Path(required["secret"]),
            provider_credential_key_file=Path(required["provider_key"]),
            object_secure=os.environ.get("DAON_OBJECT_STORAGE_SECURE", "true").lower() == "true",
            lease_seconds=int(os.environ.get("DAON_DOCUMENT_WORKER_LEASE_SECONDS", "600")),
            poll_seconds=float(os.environ.get("DAON_DOCUMENT_WORKER_POLL_SECONDS", "0.5")),
            notification_inbox_enabled=notification_flag == "true",
            readiness_settings=readiness_settings,
        )


def build_worker(settings: DocumentWorkerSettings) -> tuple[DocumentProcessingWorker, PostgresDocumentProcessingQueue, PostgresCloudStore]:
    try:
        access_key = settings.object_access_key_file.read_text(encoding="utf-8").strip()
        secret_key = settings.object_secret_key_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise ValueError("OBJECT_SECRET_REFERENCE_UNAVAILABLE") from None
    cloud_store = PostgresCloudStore(settings.database_dsn,
        notification_inbox_enabled=settings.notification_inbox_enabled)
    try:
        provider_key = settings.provider_credential_key_file.read_bytes()
    except OSError:
        cloud_store.close()
        raise ValueError("PROVIDER_CREDENTIAL_KEY_REFERENCE_UNAVAILABLE") from None
    storage = MinioObjectStorageAdapter(
        endpoint=settings.object_endpoint, bucket=settings.object_bucket,
        access_key=access_key, secret_key=secret_key, secure=settings.object_secure,
    )
    readiness = None
    if settings.readiness_settings.scopes:
        from .document_readiness_postgres import PostgresDocumentReadinessRepository
        readiness = PostgresDocumentReadinessRepository(cloud_store, settings=settings.readiness_settings)
    repository = PostgresDocumentProcessingRepository(cloud_store, storage,
        readiness_port=readiness, readiness_settings=settings.readiness_settings)
    processing = DocumentProcessingService(
        repository,
        PostgresWorkspaceModelResolver(
            cloud_store, ProviderCredentialCipher(provider_key, encryption_key_version=1),
        ),
        DefaultDocumentAdapterFactory(),
        egress_guard=OriginalDocumentEgressGuard(
            EgressPolicyService(PostgresEgressPolicyRepository(cloud_store)),
        ),
    )
    queue = PostgresDocumentProcessingQueue(settings.database_dsn, cloud_store)
    worker = DocumentProcessingWorker(
        settings.worker_id, queue, processing, PostgresDocumentIndex(cloud_store),
        lease_seconds=settings.lease_seconds,
        telemetry=worker_publisher(settings.database_dsn,worker_kind="document",worker_id=settings.worker_id),
    )
    return worker, queue, cloud_store


def main() -> None:
    settings = DocumentWorkerSettings.from_env()
    worker, queue, cloud_store = build_worker(settings)
    stop = threading.Event()

    def request_stop(signum, frame) -> None:  # type: ignore[no-untyped-def]
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        worker.run_forever(stop, poll_seconds=settings.poll_seconds)
    finally:
        queue.close()
        cloud_store.close()


if __name__ == "__main__":
    main()
