from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol

import uvicorn

from .app import create_app
from .knowledge_context import (
    KnowledgeContextProjector,
    KnowledgeContextSnapshot,
    OfflineStudioError,
)
from .local_storage import LocalEncryptedStore, LocalStorageError
from .offline_studio import OfflineStudioService
from .provider_draft import (
    HttpxProviderJsonTransport,
    OllamaDraftGenerationAdapter,
    OllamaModelCatalog,
    ProviderJsonTransport,
    ProviderModelDescriptor,
)
from .raw_source import RawSourceService
from .protocol import MAX_BOOTSTRAP_BYTES, BootstrapError, parse_bootstrap, ready_envelope

BOOTSTRAP_TIMEOUT_SECONDS = 1.0
EXIT_BOOTSTRAP_INVALID = 64
EXIT_BOOTSTRAP_TIMEOUT = 65
EXIT_PARENT_MISMATCH = 66
EXIT_STORAGE_UNAVAILABLE = 67
MAX_PARENT_CHAIN_DEPTH = 8
_DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


class EncryptedKnowledgeProjection:
    """Project only the current workspace's encrypted Canon records."""

    def __init__(self, store: LocalEncryptedStore) -> None:
        self._store = store

    def get_daon_knowledge(
        self, *, workspace_id: str, knowledge_id: str
    ) -> Mapping[str, object] | None:
        rows = [
            row for row in self._store.list_canonical_envelopes(
                workspace_id, "artifact", "ScopeSnapshot"
            )
            if row.aggregate_id == knowledge_id
            or row.payload.get("package_id") == knowledge_id
            or row.payload.get("knowledge_registration_id") == knowledge_id
            or row.payload.get("output_version_id") == knowledge_id
        ]
        if not rows:
            return None
        payload = rows[-1].payload
        producer = str(payload.get("producer_product", "")).replace(".", "_")
        digest = payload.get("content_digest_sha256")
        return {
            "producer": producer,
            "producer_version": payload.get("producer_version"),
            "registration_id": payload.get("knowledge_registration_id"),
            "version_id": payload.get("output_version_id"),
            "digest": digest,
            "registration_digest": digest,
            "quality_state": payload.get("review_state"),
            "review_state": payload.get("review_state"),
            "registration_state": payload.get("registration_state"),
            "authority": payload.get("authority"),
            "effective": payload.get("state") == "approved",
            "effective_at": payload.get("effective_at"),
            "expires_at": payload.get("expires_at"),
        }

    def get_raw_source(
        self, *, workspace_id: str, source_version_id: str
    ) -> Mapping[str, object] | None:
        rows = [
            row for row in self._store.list_canonical_envelopes(
                workspace_id, "source", "SourceVersion"
            )
            if row.entity_id == source_version_id or row.aggregate_id == source_version_id
        ]
        if not rows:
            return None
        payload = rows[-1].payload
        index_version_id = payload.get("index_version_id")
        evidence_span_ids = payload.get("evidence_span_ids", [])
        indexes = [
            row for row in self._store.list_canonical_envelopes(
                workspace_id, "source", "IndexVersion"
            )
            if row.entity_id == index_version_id
            and row.payload.get("source_version_id") == source_version_id
        ]
        evidence = [
            row for row in self._store.list_canonical_envelopes(
                workspace_id, "source", "EvidenceSpan"
            )
            if isinstance(evidence_span_ids, list)
            and row.entity_id in evidence_span_ids
            and row.payload.get("source_version_id") == source_version_id
            and row.payload.get("index_version_id") == index_version_id
        ]
        complete_evidence = (
            isinstance(evidence_span_ids, list)
            and bool(evidence_span_ids)
            and len(evidence) == len(evidence_span_ids)
        )
        return {
            "source_id": payload.get("source_id"),
            "version_id": source_version_id,
            "digest": payload.get("digest_sha256", rows[-1].digest_sha256),
            "index_version_id": index_version_id,
            "evidence_span_ids": evidence_span_ids if complete_evidence else [],
            "processing_state": (
                indexes[-1].payload.get("state") if indexes else "unavailable"
            ),
            "review_state": payload.get("quality_state", "unverified"),
            "quality_state": payload.get("quality_state", "unverified"),
            "authority": payload.get("authority", "user_source"),
            "conflict_state": payload.get("conflict_state", "none"),
            "conflict_acknowledged": payload.get("conflict_acknowledged", False),
            "local": payload.get("data_area", "local_private") == "local_private",
        }


class EncryptedProviderSettingsProjection:
    """Resolve only the selected workspace's persisted Ollama deployments."""

    def __init__(self, store: LocalEncryptedStore) -> None:
        self._store = store

    def descriptors(
        self, workspace_id: str, installed: dict[str, str]
    ) -> dict[str, ProviderModelDescriptor]:
        rows = self._store.list_canonical_envelopes(
            workspace_id, "artifact", "ProviderSettingsSnapshot"
        )
        if not rows:
            return {}
        payload = rows[-1].payload
        raw_profiles = payload.get("profiles")
        raw_deployments = payload.get("deployments")
        policy_version = payload.get("policy_version")
        if (
            not isinstance(raw_profiles, list)
            or not isinstance(raw_deployments, list)
            or not isinstance(policy_version, str)
            or not policy_version
        ):
            raise OfflineStudioError("PROVIDER_SETTINGS_INVALID")
        profiles: dict[str, dict[str, object]] = {}
        for profile in raw_profiles:
            if not isinstance(profile, dict):
                raise OfflineStudioError("PROVIDER_SETTINGS_INVALID")
            profile_id = profile.get("profile_id")
            if (
                isinstance(profile_id, str)
                and profile.get("provider_code") == "OLLAMA"
                and profile.get("provider_kind") == "server_internal"
                and profile.get("active") is True
            ):
                profiles[profile_id] = profile
        descriptors: dict[str, ProviderModelDescriptor] = {}
        for deployment in raw_deployments:
            if not isinstance(deployment, dict):
                raise OfflineStudioError("PROVIDER_SETTINGS_INVALID")
            deployment_id = deployment.get("deployment_id")
            profile_id = deployment.get("profile_id")
            model_id = deployment.get("model_id")
            roles = deployment.get("roles")
            version = deployment.get("version")
            if (
                not isinstance(deployment_id, str)
                or not isinstance(profile_id, str)
                or not isinstance(model_id, str)
                or not isinstance(roles, list)
                or "text" not in roles
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
                or deployment.get("provider_code") != "OLLAMA"
                or deployment.get("active") is not True
                or profile_id not in profiles
                or model_id not in installed
                or model_id.lower().endswith(":cloud")
            ):
                continue
            deployment_digest = hashlib.sha256(json.dumps(
                {
                    "deployment": deployment,
                    "profile": profiles[profile_id],
                    "policy_version": policy_version,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            descriptors[deployment_id] = ProviderModelDescriptor(
                provider_code="OLLAMA",
                provider_kind="server_internal",
                profile_id=profile_id,
                deployment_id=deployment_id,
                model_id=model_id,
                model_digest=installed[model_id],
                deployment_digest=deployment_digest,
                binding_version=version,
                policy_version=policy_version,
            )
        return descriptors

class EncryptedEvidenceResolver:
    """Resolve bounded evidence text from selected encrypted workspace records."""

    _MAX_ITEM_BYTES = 2 * 1024 * 1024
    _MAX_TOTAL_BYTES = 8 * 1024 * 1024

    def __init__(self, store: LocalEncryptedStore) -> None:
        self._store = store

    def __call__(self, context: KnowledgeContextSnapshot) -> list[dict[str, str]]:
        resolved: list[dict[str, str]] = []
        total_bytes = 0
        for item in context.items:
            if item.origin == "daon_knowledge":
                text = self._knowledge_text(context.workspace_id, item.item_id, item.digest)
            elif item.origin == "raw_source":
                text = self._raw_source_text(context.workspace_id, item.version_id, item.digest)
            else:
                raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
            item_bytes = len(text.encode("utf-8"))
            total_bytes += item_bytes
            if not text.strip() or item_bytes > self._MAX_ITEM_BYTES or total_bytes > self._MAX_TOTAL_BYTES:
                raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
            resolved.append({"item_id": item.item_id, "text": text})
        if len(resolved) != len(context.items):
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        return resolved

    def _knowledge_text(self, workspace_id: str, item_id: str, digest: str) -> str:
        rows = [
            row for row in self._store.list_canonical_envelopes(
                workspace_id, "artifact", "ScopeSnapshot"
            )
            if row.aggregate_id == item_id
            or row.payload.get("package_id") == item_id
            or row.payload.get("knowledge_registration_id") == item_id
            or row.payload.get("output_version_id") == item_id
        ]
        if not rows:
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        payload = rows[-1].payload
        object_id = payload.get("object_id")
        if (
            payload.get("state") != "approved"
            or payload.get("content_digest_sha256") != digest
            or not isinstance(object_id, str)
        ):
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        try:
            package = self._store.get_file(workspace_id, "artifact", object_id)
            if hashlib.sha256(package).hexdigest() != digest:
                raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
            decoded = json.loads(package)
        except (LocalStorageError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE") from error
        knowledge = decoded.get("knowledge") if isinstance(decoded, dict) else None
        if not isinstance(knowledge, list) or not knowledge:
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        texts: list[str] = []
        for entry in knowledge:
            text = entry.get("text") if isinstance(entry, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
            texts.append(text.strip())
        return "\n\n".join(texts)

    def _raw_source_text(self, workspace_id: str, version_id: str, digest: str) -> str:
        rows = [
            row for row in self._store.list_canonical_envelopes(
                workspace_id, "source", "SourceVersion"
            )
            if row.entity_id == version_id or row.aggregate_id == version_id
        ]
        if not rows:
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        payload = rows[-1].payload
        evidence_ids = payload.get("evidence_span_ids")
        if (
            payload.get("digest_sha256") != digest
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or not all(isinstance(item, str) for item in evidence_ids)
        ):
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        spans = {
            row.entity_id: row.payload
            for row in self._store.list_canonical_envelopes(
                workspace_id, "source", "EvidenceSpan"
            )
            if row.entity_id in evidence_ids
        }
        if set(spans) != set(evidence_ids):
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        texts: list[str] = []
        for evidence_id in evidence_ids:
            span = spans[evidence_id]
            text = span.get("text")
            text_digest = span.get("text_digest_sha256")
            if (
                span.get("source_version_id") != version_id
                or span.get("unverified") is not True
                or not isinstance(text, str)
                or not text.strip()
                or hashlib.sha256(text.encode("utf-8")).hexdigest() != text_digest
            ):
                raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
            texts.append(text.strip())
        return "\n\n".join(texts)

def build_production_offline_studio(
    store: LocalEncryptedStore,
    *,
    environment: Mapping[str, str] | None = None,
    transport: ProviderJsonTransport | None = None,
) -> OfflineStudioService:
    settings = os.environ if environment is None else environment
    provider_transport = transport or HttpxProviderJsonTransport()
    provider_settings = EncryptedProviderSettingsProjection(store)
    catalog = OllamaModelCatalog(
        base_url=settings.get("DAON_OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL),
        transport=provider_transport,
        descriptors={},
        descriptor_resolver=provider_settings.descriptors,
    )
    generator = OllamaDraftGenerationAdapter(
        base_url=settings.get("DAON_OLLAMA_BASE_URL", _DEFAULT_OLLAMA_BASE_URL),
        transport=provider_transport,
        catalog=catalog,
    )
    return OfflineStudioService(
        store=store,
        context_projector=KnowledgeContextProjector(EncryptedKnowledgeProjection(store)),
        model_catalog=catalog,
        generator=generator,
        clock=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        evidence_resolver=EncryptedEvidenceResolver(store),
    )


class BootstrapReadTimeout(TimeoutError):
    """Raised when the parent leaves the bootstrap pipe open without a line."""


def read_bootstrap_line(
    stream: BinaryIO,
    *,
    timeout_seconds: float = BOOTSTRAP_TIMEOUT_SECONDS,
) -> bytes:
    try:
        file_descriptor = stream.fileno()
    except (AttributeError, OSError):
        result = stream.readline(MAX_BOOTSTRAP_BYTES + 2)
        if not result.endswith(b"\n"):
            raise BootstrapError("bootstrap line terminator required")
        return result[:-1]

    deadline = time.monotonic() + timeout_seconds
    payload = bytearray()
    while len(payload) <= MAX_BOOTSTRAP_BYTES + 1:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BootstrapReadTimeout("bootstrap deadline exceeded")
        available = _wait_for_pipe_data(file_descriptor, remaining)
        if available is None:
            raise BootstrapReadTimeout("bootstrap deadline exceeded")
        if available == 0:
            break
        chunk = os.read(
            file_descriptor,
            min(available, MAX_BOOTSTRAP_BYTES + 2 - len(payload)),
        )
        if not chunk:
            break
        payload.extend(chunk)
        newline = payload.find(b"\n")
        if newline >= 0:
            return bytes(payload[:newline])
    if not payload.endswith(b"\n"):
        raise BootstrapError("bootstrap line terminator required")
    return bytes(payload[:-1])


def _wait_for_pipe_data(file_descriptor: int, timeout_seconds: float) -> int | None:
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        peek_named_pipe = kernel32.PeekNamedPipe
        peek_named_pipe.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        peek_named_pipe.restype = ctypes.c_int
        handle = msvcrt.get_osfhandle(file_descriptor)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            available = ctypes.c_uint32()
            if peek_named_pipe(
                ctypes.c_void_p(handle),
                None,
                0,
                None,
                ctypes.byref(available),
                None,
            ):
                if available.value:
                    return int(available.value)
            else:
                error_code = ctypes.get_last_error()
                if error_code == 109:
                    return 0
                raise BootstrapError("bootstrap pipe inspection failed")
            time.sleep(0.01)
        return None

    import select

    readable, _, _ = select.select([file_descriptor], [], [], timeout_seconds)
    return 1 if readable else None


def _watch_parent(server: uvicorn.Server) -> None:
    sys.stdin.buffer.read()
    server.should_exit = True


def _pid_is_ancestor(
    expected_ancestor: int,
    process_id: int,
    parents: dict[int, int],
) -> bool:
    visited = {process_id}
    current = process_id
    for _ in range(MAX_PARENT_CHAIN_DEPTH):
        parent = parents.get(current, 0)
        if parent == expected_ancestor and parent not in visited:
            return True
        if parent <= 0 or parent in visited:
            return False
        visited.add(parent)
        current = parent
    return False


class ProcessSnapshotApi(Protocol):
    def open(self) -> object: ...

    def first(self, snapshot: object) -> tuple[int, int]: ...

    def next(self, snapshot: object) -> tuple[int, int] | None: ...

    def close(self, snapshot: object) -> None: ...


def _collect_process_parents(api: ProcessSnapshotApi) -> dict[int, int]:
    snapshot = api.open()
    parents: dict[int, int] = {}
    try:
        current: tuple[int, int] | None = api.first(snapshot)
        while current is not None:
            process_id, parent_process_id = current
            parents[process_id] = parent_process_id
            current = api.next(snapshot)
    finally:
        api.close(snapshot)
    return parents


def _windows_process_api() -> ProcessSnapshotApi:  # pragma: no cover - Windows ctypes glue
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    loader: Any = getattr(ctypes, "WinDLL", None)
    set_last_error: Any = getattr(ctypes, "set_last_error", None)
    get_last_error: Any = getattr(ctypes, "get_last_error", None)
    if not callable(loader) or not callable(set_last_error) or not callable(get_last_error):
        raise BootstrapError("parent process inspection failed")
    kernel32: Any = loader("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    class WindowsProcessSnapshotApi:
        def __init__(self) -> None:
            self._entry = ProcessEntry32W()
            self._entry.dwSize = ctypes.sizeof(ProcessEntry32W)

        def open(self) -> object:
            snapshot = create_snapshot(0x00000002, 0)
            if snapshot == wintypes.HANDLE(-1).value:
                raise BootstrapError("parent process inspection failed")
            return snapshot

        def first(self, snapshot: object) -> tuple[int, int]:
            if not process_first(snapshot, ctypes.byref(self._entry)):
                raise BootstrapError("parent process inspection failed")
            return int(self._entry.th32ProcessID), int(self._entry.th32ParentProcessID)

        def next(self, snapshot: object) -> tuple[int, int] | None:
            set_last_error(0)
            if process_next(snapshot, ctypes.byref(self._entry)):
                return int(self._entry.th32ProcessID), int(self._entry.th32ParentProcessID)
            if get_last_error() == 18:
                return None
            raise BootstrapError("parent process inspection failed")

        def close(self, snapshot: object) -> None:
            close_handle(snapshot)

    return WindowsProcessSnapshotApi()


def _windows_process_parents() -> dict[int, int]:  # pragma: no cover - Windows dispatch
    return _collect_process_parents(_windows_process_api())


def _parent_identity_matches(expected_parent_process_id: int) -> bool:
    if sys.platform != "win32":
        return expected_parent_process_id == os.getppid()
    try:
        return _pid_is_ancestor(
            expected_parent_process_id,
            os.getpid(),
            _windows_process_parents(),
        )
    except (OSError, BootstrapError):
        return False


def run() -> int:
    payload = bytearray()
    try:
        payload.extend(read_bootstrap_line(sys.stdin.buffer))
        bootstrap = parse_bootstrap(payload)
    except BootstrapReadTimeout:
        return EXIT_BOOTSTRAP_TIMEOUT
    except BootstrapError:
        return EXIT_BOOTSTRAP_INVALID
    finally:
        payload[:] = b"\x00" * len(payload)
        payload.clear()
    if not _parent_identity_matches(bootstrap.parent_process_id):
        return EXIT_PARENT_MISMATCH

    storage_key = bytearray()
    try:
        storage_key.extend(bytes.fromhex(bootstrap.storage_root_key))
        storage = LocalEncryptedStore.open(
            Path(bootstrap.storage_root), storage_key
        )
    except (OSError, LocalStorageError, ValueError):
        return EXIT_STORAGE_UNAVAILABLE
    finally:
        storage_key[:] = b"\x00" * len(storage_key)
        storage_key.clear()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    try:
        offline_studio = build_production_offline_studio(storage)
    except (LocalStorageError, ValueError):
        listener.close()
        storage.close()
        return EXIT_STORAGE_UNAVAILABLE

    config = uvicorn.Config(
        create_app(
            root_secret=bootstrap.root_secret,
            app_instance_id=bootstrap.app_instance_id,
            listener_port=port,
            storage=storage,
            offline_studio=offline_studio,
            raw_source_service=RawSourceService(storage),
        ),
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
        server_header=False,
        date_header=False,
        h11_max_incomplete_event_size=8192,
    )
    server = uvicorn.Server(config)
    watcher = threading.Thread(target=_watch_parent, args=(server,), daemon=True)
    watcher.start()
    sys.stdout.write(
        json.dumps(
            ready_envelope(port=port, app_instance_id=bootstrap.app_instance_id),
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()
    server.run(sockets=[listener])
    listener.close()
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
