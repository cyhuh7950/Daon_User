"""Standalone worker for durable asynchronous Studio generation jobs."""

from __future__ import annotations

import os
import signal
import threading
from dataclasses import dataclass
from pathlib import Path

from .cloud_storage import PostgresCloudStore
from .studio_generation_queue import PostgresStudioGenerationQueue
from .studio_workspace import StudioContext, StudioError, StudioGenerationRequest
from .studio_workspace_postgres import PostgresStudioWorkspaceRepository


@dataclass(frozen=True, slots=True)
class StudioWorkerSettings:
    database_dsn: str
    worker_id: str
    lease_seconds: int
    poll_seconds: float

    @classmethod
    def from_env(cls) -> "StudioWorkerSettings":
        dsn = os.environ.get("DAON_CLOUD_DATABASE_DSN", "").strip()
        if not dsn:
            raise ValueError("STUDIO_WORKER_DATABASE_REQUIRED")
        return cls(
            dsn, os.environ.get("DAON_STUDIO_WORKER_ID", "studio-worker-1"),
            int(os.environ.get("DAON_STUDIO_WORKER_LEASE_SECONDS", "600")),
            float(os.environ.get("DAON_STUDIO_WORKER_POLL_SECONDS", "0.5")),
        )


class StudioGenerationWorker:
    def __init__(self, queue, repository, *, worker_id: str, lease_seconds: int = 600) -> None:  # type: ignore[no-untyped-def]
        self._queue = queue
        self._repository = repository
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    def run_once(self) -> bool:
        job = self._queue.claim(self._worker_id, lease_seconds=self._lease_seconds)
        if job is None:
            return False
        try:
            request_payload = dict(job.request_json)
            notebook_id = request_payload.pop("notebook_id", None)
            request_payload["source_version_ids"] = tuple(request_payload.get("source_version_ids", ()))
            request = StudioGenerationRequest(**request_payload)
            context = StudioContext(job.tenant_id, job.workspace_id, job.actor_id, job.trace_id, job.policy_version, notebook_id)
            result, _ = self._repository.create_generation(context, request, job.idempotency_key)
            self._queue.finish(job, state="completed", studio_output_id=result.get("studio_output_id"), output_version_id=result.get("output_version_id"))
        except StudioError as error:
            self._queue.finish(job, state="unavailable" if error.code == "STUDIO_OUTPUT_UNAVAILABLE" else "failed", error_code=error.code)
        except Exception:
            # Do not leave malformed/provider failures leased until expiry; expose only a safe code.
            self._queue.finish(job, state="failed", error_code="STUDIO_JOB_PROCESSING_FAILED")
        return True

    def run_forever(self, stop: threading.Event, *, poll_seconds: float = 0.5) -> None:
        if not 0 < poll_seconds <= 60:
            raise ValueError("STUDIO_WORKER_POLL_INVALID")
        while not stop.is_set():
            if not self.run_once():
                stop.wait(poll_seconds)


def main() -> None:
    settings = StudioWorkerSettings.from_env()
    cloud_store = PostgresCloudStore(settings.database_dsn)
    queue = PostgresStudioGenerationQueue(settings.database_dsn, cloud_store)
    repository = PostgresStudioWorkspaceRepository(cloud_store)
    worker = StudioGenerationWorker(queue, repository, worker_id=settings.worker_id, lease_seconds=settings.lease_seconds)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    try:
        worker.run_forever(stop, poll_seconds=settings.poll_seconds)
    finally:
        queue.close()
        cloud_store.close()


if __name__ == "__main__":
    main()
