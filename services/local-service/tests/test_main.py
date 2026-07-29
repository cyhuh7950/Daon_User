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
                "root_secret": "ab" * 32,
                "storage_root_key": "cd" * 32,
                "storage_root": str(Path(os.environ.get("TEMP", ".")) / "daon-main-test-storage"),
                "parent_process_id": os.getppid(),
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


def test_bootstrap_reader_reads_complete_line_from_parent_pipe() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    try:
        os.write(write_fd, bootstrap())
        os.close(write_fd)
        write_fd = -1
        assert main.read_bootstrap_line(stream, timeout_seconds=0.5) == bootstrap().removesuffix(
            b"\n"
        )
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        stream.close()


def test_bootstrap_reader_rejects_parent_pipe_eof_without_line_terminator() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    os.close(write_fd)
    try:
        with pytest.raises(main.BootstrapError):
            main.read_bootstrap_line(stream, timeout_seconds=0.5)
    finally:
        stream.close()


def test_run_returns_timeout_code_when_bootstrap_deadline_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(_stream: object) -> bytes:
        raise main.BootstrapReadTimeout("fixture timeout")

    monkeypatch.setattr(main, "read_bootstrap_line", timeout)
    assert main.run() == main.EXIT_BOOTSTRAP_TIMEOUT


def test_parent_identity_accepts_only_a_bounded_real_ancestor_chain() -> None:
    parents = {900: 800, 800: 700, 700: 600}
    assert main._pid_is_ancestor(700, 900, parents)
    assert not main._pid_is_ancestor(500, 900, parents)
    assert not main._pid_is_ancestor(900, 900, parents)
    assert not main._pid_is_ancestor(700, 900, {900: 800, 800: 900})


class FakeProcessSnapshotApi:
    def __init__(
        self,
        *,
        first: tuple[int, int] | Exception,
        remaining: list[tuple[int, int] | None | Exception],
        open_error: Exception | None = None,
    ) -> None:
        self.first_result = first
        self.remaining = iter(remaining)
        self.open_error = open_error
        self.snapshot = object()
        self.closed: list[object] = []

    def open(self) -> object:
        if self.open_error is not None:
            raise self.open_error
        return self.snapshot

    def first(self, snapshot: object) -> tuple[int, int]:
        assert snapshot is self.snapshot
        if isinstance(self.first_result, Exception):
            raise self.first_result
        return self.first_result

    def next(self, snapshot: object) -> tuple[int, int] | None:
        assert snapshot is self.snapshot
        result = next(self.remaining)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self, snapshot: object) -> None:
        self.closed.append(snapshot)


def test_process_snapshot_collection_closes_handle_after_complete_enumeration() -> None:
    api = FakeProcessSnapshotApi(first=(900, 800), remaining=[(800, 700), None])
    assert main._collect_process_parents(api) == {900: 800, 800: 700}
    assert api.closed == [api.snapshot]


@pytest.mark.parametrize("failure_stage", ["open", "first", "next"])
def test_process_snapshot_collection_fails_closed_and_closes_handle(
    failure_stage: str,
) -> None:
    error = main.BootstrapError("parent process inspection failed")
    api = FakeProcessSnapshotApi(
        first=error if failure_stage == "first" else (900, 800),
        remaining=[error] if failure_stage == "next" else [],
        open_error=error if failure_stage == "open" else None,
    )
    with pytest.raises(main.BootstrapError, match="parent process inspection failed"):
        main._collect_process_parents(api)
    assert api.closed == ([] if failure_stage == "open" else [api.snapshot])


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
