from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import BinaryIO

from .protocol import MAX_BOOTSTRAP_BYTES, Bootstrap, BootstrapError, parse_bootstrap


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


def read_bootstrap(
    stream: BinaryIO,
    *,
    read_line: Callable[[BinaryIO], bytes] | None = None,
) -> Bootstrap:
    payload = bytearray()
    try:
        payload.extend((read_line or read_bootstrap_line)(stream))
        return parse_bootstrap(payload)
    finally:
        payload[:] = b"\x00" * len(payload)
        payload.clear()


def run_entrypoint() -> int:
    try:
        bootstrap = read_bootstrap(sys.stdin.buffer)
    except BootstrapReadTimeout:
        return EXIT_BOOTSTRAP_TIMEOUT
    except BootstrapError:
        return EXIT_BOOTSTRAP_INVALID

    from .main import run

    return run(bootstrap)


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
