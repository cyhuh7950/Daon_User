"""Standalone worker for durable asynchronous Studio generation jobs."""

from __future__ import annotations

import os
import logging
import signal
import threading
from dataclasses import dataclass
from pathlib import Path

from .cloud_storage import PostgresCloudStore
from .studio_generation_queue import PostgresStudioGenerationQueue
from .studio_workspace import StudioContext, StudioError, StudioGenerationRequest
from .studio_workspace_postgres import PostgresStudioWorkspaceRepository
from .studio_approval_lifecycle import StudioApprovalLifecycleRepository
from .operations_telemetry import observe_poll,safe_hook,start_worker_observation,worker_publisher


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StudioWorkerSettings:
    database_dsn: str
    worker_id: str
    lease_seconds: int
    poll_seconds: float
    notification_inbox_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")

    @classmethod
    def from_env(cls) -> "StudioWorkerSettings":
        dsn = os.environ.get("DAON_CLOUD_DATABASE_DSN", "").strip()
        if not dsn:
            raise ValueError("STUDIO_WORKER_DATABASE_REQUIRED")
        notification_flag = os.environ.get("DAON_NOTIFICATION_INBOX_ENABLED", "false")
        if notification_flag not in {"true", "false"}:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        return cls(
            dsn, os.environ.get("DAON_STUDIO_WORKER_ID", "studio-worker-1"),
            int(os.environ.get("DAON_STUDIO_WORKER_LEASE_SECONDS", "600")),
            float(os.environ.get("DAON_STUDIO_WORKER_POLL_SECONDS", "0.5")),
            notification_inbox_enabled=notification_flag == "true",
        )


class StudioGenerationWorker:
    def __init__(self, queue, repository, *, worker_id: str, lease_seconds: int = 600,
                 approval_repository=None, approval_sweep_seconds: float = 60.0, telemetry=None) -> None:  # type: ignore[no-untyped-def]
        if (isinstance(approval_sweep_seconds, bool) or not isinstance(approval_sweep_seconds, (int, float))
                or not 0 < approval_sweep_seconds <= 60):
            raise ValueError("STUDIO_WORKER_APPROVAL_POLL_INVALID")
        self._queue = queue
        self._repository = repository
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._approval_repository = approval_repository
        self._approval_sweep_seconds = approval_sweep_seconds
        self._telemetry = telemetry

    def run_once(self) -> bool:
        with observe_poll(self._telemetry):
            job = self._queue.claim(self._worker_id, lease_seconds=self._lease_seconds)
            if job is None:
                return False
            safe_hook(self._telemetry,"processing_started")
            try:
                start = getattr(self._queue, "start", None)
                if callable(start):
                    start(job)
                request_payload = dict(job.request_json)
                notebook_id = request_payload.pop("notebook_id", None)
                request_payload["source_version_ids"] = tuple(request_payload.get("source_version_ids", ()))
                request = StudioGenerationRequest(**request_payload)
                context = StudioContext(job.tenant_id, job.workspace_id, job.actor_id, job.trace_id, job.policy_version, notebook_id)
                revalidate=getattr(self._repository,'require_source_access',None)
                if callable(revalidate):revalidate(context,request.source_version_ids)
                result, _ = self._repository.create_generation(context, request, job.idempotency_key)
                self._queue.finish(job, state="completed", studio_output_id=result.get("studio_output_id"), output_version_id=result.get("output_version_id"))
            except StudioError as error:
                if error.code == "STUDIO_OUTPUT_UNAVAILABLE":
                    self._queue.finish(job, state="unavailable", error_code=error.code)
                elif error.code in {"STUDIO_DATABASE_UNAVAILABLE", "OBJECT_STORAGE_UNAVAILABLE", "STUDIO_QUEUE_UNAVAILABLE", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"}:
                    retry = getattr(self._queue, "retry", None)
                    if callable(retry) and retry(job, error_code=error.code):
                        return True
                    self._queue.finish(job, state="failed", error_code="STUDIO_DEAD_LETTER")
                else:
                    self._queue.finish(job, state="failed", error_code=error.code)
            except Exception:
                # 기존 bounded retry/dead-letter의 결과·횟수는 바꾸지 않는다.
                retry = getattr(self._queue, "retry", None)
                if callable(retry) and retry(job, error_code="STUDIO_JOB_PROCESSING_FAILED"):
                    return True
                self._queue.finish(job, state="failed", error_code="STUDIO_DEAD_LETTER")
            return True

    def _sweep_approvals(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self._approval_repository.sweep_due(self._worker_id, limit=100)
            except Exception:
                # No exception text, response data or actor/tenant payload in logs.
                _LOGGER.warning("STUDIO_APPROVAL_SWEEP_FAILED")
            if stop.wait(self._approval_sweep_seconds):
                break

    def run_forever(self, stop: threading.Event, *, poll_seconds: float = 0.5) -> None:
        if not 0 < poll_seconds <= 60:
            raise ValueError("STUDIO_WORKER_POLL_INVALID")
        approval_thread = None
        try:
            if not stop.is_set():start_worker_observation(self._telemetry,"studio",self._worker_id)
            if self._approval_repository is not None and not stop.is_set():
                approval_thread = threading.Thread(target=self._sweep_approvals, args=(stop,),
                    name="studio-approval-" + self._worker_id, daemon=False)
                approval_thread.start()
            while not stop.is_set():
                try:
                    worked = self.run_once()
                except Exception:
                    _LOGGER.warning("STUDIO_GENERATION_POLL_FAILED")
                    worked = False
                if not worked:
                    stop.wait(poll_seconds)
        finally:
            stop.set()
            if approval_thread is not None:
                approval_thread.join()
            safe_hook(self._telemetry,"stop")


def main() -> None:
    settings = StudioWorkerSettings.from_env()
    cloud_store = PostgresCloudStore(settings.database_dsn,
        notification_inbox_enabled=settings.notification_inbox_enabled)
    queue = PostgresStudioGenerationQueue(settings.database_dsn, cloud_store)
    repository = PostgresStudioWorkspaceRepository(cloud_store)
    worker = StudioGenerationWorker(queue, repository, worker_id=settings.worker_id, lease_seconds=settings.lease_seconds,
                                    approval_repository=StudioApprovalLifecycleRepository(cloud_store),
                                    telemetry=worker_publisher(settings.database_dsn,worker_kind="studio",worker_id=settings.worker_id))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    try:
        worker.run_forever(stop, poll_seconds=settings.poll_seconds)
    finally:
        try:
            queue.close()
        finally:
            cloud_store.close()


if __name__ == "__main__":
    main()
