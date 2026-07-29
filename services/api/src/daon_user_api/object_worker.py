"""Standalone durable object worker runtime with bounded graceful shutdown."""

from __future__ import annotations

import os
import signal
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.object_queue import (
    DurableObjectWorker,
    MinioObjectStorageAdapter,
    PostgresObjectQueueStore,
)


@dataclass(frozen=True, slots=True)
class ObjectWorkerSettings:
    database_dsn: str
    object_storage_endpoint: str
    object_storage_bucket: str
    access_key_file: Path
    secret_key_file: Path
    tenant_id: str
    workspace_id: str
    actor_id: str
    secure: bool = True
    poll_seconds: float = 0.2
    lease_seconds: float = 30.0
    drain_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not all(
            (
                self.database_dsn,
                self.object_storage_endpoint,
                self.object_storage_bucket,
                self.tenant_id,
                self.workspace_id,
                self.actor_id,
            )
        ):
            raise ValueError("OBJECT_WORKER_CONFIGURATION_INCOMPLETE")
        if self.poll_seconds <= 0 or self.lease_seconds <= 0 or self.drain_timeout_seconds <= 0:
            raise ValueError("OBJECT_WORKER_LIMIT_INVALID")
        CloudAccessContext(self.tenant_id, self.workspace_id, self.actor_id, "object.write")

    @classmethod
    def from_env(cls) -> "ObjectWorkerSettings":
        required = {
            "database_dsn": "DAON_CLOUD_DATABASE_DSN",
            "object_storage_endpoint": "DAON_OBJECT_STORAGE_ENDPOINT",
            "object_storage_bucket": "DAON_OBJECT_STORAGE_BUCKET",
            "access_key_file": "DAON_OBJECT_ACCESS_KEY_FILE",
            "secret_key_file": "DAON_OBJECT_SECRET_KEY_FILE",
            "tenant_id": "DAON_WORKER_TENANT_ID",
            "workspace_id": "DAON_WORKER_WORKSPACE_ID",
            "actor_id": "DAON_WORKER_ACTOR_ID",
        }
        values: dict[str, str] = {}
        for field, variable in required.items():
            value = os.environ.get(variable)
            if value is None or not value.strip():
                raise ValueError("OBJECT_WORKER_CONFIGURATION_INCOMPLETE")
            values[field] = value.strip()
        return cls(
            database_dsn=values["database_dsn"],
            object_storage_endpoint=values["object_storage_endpoint"],
            object_storage_bucket=values["object_storage_bucket"],
            access_key_file=Path(values["access_key_file"]),
            secret_key_file=Path(values["secret_key_file"]),
            tenant_id=values["tenant_id"],
            workspace_id=values["workspace_id"],
            actor_id=values["actor_id"],
            secure=os.environ.get("DAON_OBJECT_STORAGE_SECURE", "true").lower() == "true",
            poll_seconds=float(os.environ.get("DAON_WORKER_POLL_SECONDS", "0.2")),
            lease_seconds=float(os.environ.get("DAON_WORKER_LEASE_SECONDS", "30")),
            drain_timeout_seconds=float(os.environ.get("DAON_WORKER_DRAIN_TIMEOUT_SECONDS", "10")),
        )


def _read_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise ValueError("OBJECT_SECRET_REFERENCE_UNAVAILABLE") from None
    if not value:
        raise ValueError("OBJECT_SECRET_REFERENCE_UNAVAILABLE")
    return value


def run(settings: ObjectWorkerSettings, *, stop_event: threading.Event | None = None) -> int:
    access_key = _read_secret(settings.access_key_file)
    secret_key = _read_secret(settings.secret_key_file)
    store = PostgresObjectQueueStore(settings.database_dsn)
    storage = MinioObjectStorageAdapter(
        endpoint=settings.object_storage_endpoint,
        bucket=settings.object_storage_bucket,
        access_key=access_key,
        secret_key=secret_key,
        secure=settings.secure,
    )
    context = CloudAccessContext(
        settings.tenant_id,
        settings.workspace_id,
        settings.actor_id,
        "object.write",
    )
    worker = DurableObjectWorker(
        store,
        storage,
        f"{socket.gethostname()}-{os.getpid()}",
        lease_seconds=settings.lease_seconds,
    )
    stopped = stop_event or threading.Event()
    previous_handlers: dict[signal.Signals, Any] = {}

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stopped.set()

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
    try:
        worker.start((context,), poll_seconds=settings.poll_seconds)
        stopped.wait()
        worker.shutdown(timeout=settings.drain_timeout_seconds)
        return 0
    finally:
        store.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    return run(ObjectWorkerSettings.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
