from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from typing import Any, BinaryIO, Protocol

import uvicorn

from .app import create_app
from .protocol import MAX_BOOTSTRAP_BYTES, BootstrapError, parse_bootstrap, ready_envelope

BOOTSTRAP_TIMEOUT_SECONDS = 1.0
EXIT_BOOTSTRAP_INVALID = 64
EXIT_BOOTSTRAP_TIMEOUT = 65
EXIT_PARENT_MISMATCH = 66
MAX_PARENT_CHAIN_DEPTH = 8


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
    try:
        payload = read_bootstrap_line(sys.stdin.buffer)
        bootstrap = parse_bootstrap(payload)
    except BootstrapReadTimeout:
        return EXIT_BOOTSTRAP_TIMEOUT
    except BootstrapError:
        return EXIT_BOOTSTRAP_INVALID
    if not _parent_identity_matches(bootstrap.parent_process_id):
        return EXIT_PARENT_MISMATCH

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])

    config = uvicorn.Config(
        create_app(
            root_secret=bootstrap.root_secret,
            app_instance_id=bootstrap.app_instance_id,
            listener_port=port,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
