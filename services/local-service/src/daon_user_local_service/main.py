from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from typing import BinaryIO

import uvicorn

from .app import create_app
from .protocol import MAX_BOOTSTRAP_BYTES, BootstrapError, parse_bootstrap, ready_envelope

BOOTSTRAP_TIMEOUT_SECONDS = 1.0
EXIT_BOOTSTRAP_INVALID = 64
EXIT_BOOTSTRAP_TIMEOUT = 65


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


def run() -> int:
    try:
        payload = read_bootstrap_line(sys.stdin.buffer)
        bootstrap = parse_bootstrap(payload)
    except BootstrapReadTimeout:
        return EXIT_BOOTSTRAP_TIMEOUT
    except BootstrapError:
        return EXIT_BOOTSTRAP_INVALID

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])

    config = uvicorn.Config(
        create_app(token=bootstrap.token, app_instance_id=bootstrap.app_instance_id),
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
