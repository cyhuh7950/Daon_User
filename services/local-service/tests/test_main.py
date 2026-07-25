import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from daon_user_local_service import main
from daon_user_local_service.protocol import MAX_BOOTSTRAP_BYTES


class BinaryInput:
    def __init__(self, payload: bytes) -> None:
        self.buffer = io.BytesIO(payload)


class FakeListener:
    def __init__(self) -> None:
        self.closed = False

    def setsockopt(self, *_args: Any) -> None:
        return None

    def bind(self, address: tuple[str, int]) -> None:
        assert address == ("127.0.0.1", 0)

    def listen(self, backlog: int) -> None:
        assert backlog == 128

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 48123)

    def close(self) -> None:
        self.closed = True


class FakeServer:
    last: "FakeServer | None" = None

    def __init__(self, _config: object) -> None:
        self.should_exit = False
        self.sockets: list[FakeListener] = []
        FakeServer.last = self

    def run(self, *, sockets: list[FakeListener]) -> None:
        self.sockets = sockets


def bootstrap() -> bytes:
    return (
        json.dumps(
            {
                "protocol_version": "1.0",
                "app_instance_id": "instance-123",
                "token": "x" * 48,
            }
        ).encode()
        + b"\n"
    )


def test_run_rejects_invalid_bootstrap_without_starting_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.sys, "stdin", BinaryInput(b"invalid\n"))
    assert main.run() == 64


@pytest.mark.parametrize(
    ("payload", "keep_pipe_open", "expected_code"),
    [
        (b"", True, 65),
        (b'{"protocol_version":"1.0"', True, 65),
        (bootstrap().removesuffix(b"\n"), False, 64),
        (b"x" * (MAX_BOOTSTRAP_BYTES + 1) + b"\n", False, 64),
        (b"not-json\n", False, 64),
    ],
)
def test_packaged_entrypoint_rejects_incomplete_bootstrap_with_deadline(
    payload: bytes,
    keep_pipe_open: bool,
    expected_code: int,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "daon_user_local_service"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    assert process.stdin is not None
    started = time.monotonic()
    if payload:
        process.stdin.write(payload)
        process.stdin.flush()
    if not keep_pipe_open:
        process.stdin.close()

    try:
        actual_code = process.wait(timeout=3)
        stderr = process.stderr.read().decode() if process.stderr else ""
        assert actual_code == expected_code, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
    assert time.monotonic() - started < 3


def test_bootstrap_reader_timeout_does_not_keep_process_alive() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    started = time.monotonic()
    try:
        with pytest.raises(main.BootstrapReadTimeout):
            main.read_bootstrap_line(stream, timeout_seconds=0.05)
    finally:
        os.close(write_fd)
        stream.close()
    assert time.monotonic() - started < 0.5


def test_run_binds_only_loopback_emits_safe_ready_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = FakeListener()
    output = io.StringIO()
    monkeypatch.setattr(main.sys, "stdin", BinaryInput(bootstrap()))
    monkeypatch.setattr(main.sys, "stdout", output)
    monkeypatch.setattr(main.socket, "socket", lambda *_args: listener)
    monkeypatch.setattr(main.uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main.uvicorn, "Server", FakeServer)

    assert main.run() == 0
    envelope = json.loads(output.getvalue())
    assert envelope == {
        "event": "ready",
        "protocol_version": "1.0",
        "app_instance_id": "instance-123",
        "port": 48123,
    }
    assert "x" * 48 not in output.getvalue()
    assert FakeServer.last is not None
    assert FakeServer.last.sockets == [listener]
    assert listener.closed is True
