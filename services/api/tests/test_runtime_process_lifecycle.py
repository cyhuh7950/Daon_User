from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_process_probe import (
    ProcessTreeShutdownError,
    force_cleanup_process_tree,
    next_exit_is_graceful,
    port_is_released,
    stop_process,
    start_next,
)


FIXTURE = Path(__file__).with_name("process_tree_fixture.py")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_listener(port: int, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError("fixture listener did not start")


def start_fixture(role: str, mode: str, port: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(FIXTURE), role, mode, str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


class NextLauncherContractTests(unittest.TestCase):
    def test_next_accepts_only_zero_or_platform_control_signal_exit(self) -> None:
        self.assertTrue(next_exit_is_graceful(0, "posix"))
        self.assertTrue(next_exit_is_graceful(-15, "posix"))
        self.assertTrue(next_exit_is_graceful(143, "posix"))
        self.assertFalse(next_exit_is_graceful(1, "posix"))
        self.assertTrue(next_exit_is_graceful(0xC000013A, "nt"))
        self.assertFalse(next_exit_is_graceful(1, "nt"))

    @patch("runtime_process_probe.subprocess.Popen")
    def test_next_uses_direct_node_launcher_without_npm_wrapper(self, popen: object) -> None:
        start_next(4010, 8010)
        command = popen.call_args.args[0]
        self.assertIn(Path(command[0]).name.lower(), {"node", "node.exe"})
        self.assertEqual(Path(command[1]).as_posix().split("/")[-4:], ["next", "dist", "bin", "next"])
        self.assertEqual(command[2:], ["start", "-p", "4010", "-H", "127.0.0.1"])
        self.assertEqual(popen.call_args.kwargs["cwd"].as_posix().split("/")[-2:], ["apps", "web"])


@unittest.skipIf(os.name == "nt", "POSIX process group contract")
class PosixProcessLifecycleTests(unittest.TestCase):
    def test_parent_exit_cannot_leave_pipe_and_listener_child_until_fixture_timeout(self) -> None:
        port = free_port()
        process = start_fixture("parent", "normal", port)
        try:
            wait_for_listener(port)
            started = time.monotonic()
            exit_code, output = stop_process(process)
            elapsed = time.monotonic() - started
            self.assertEqual(exit_code, 0)
            self.assertIn("PARENT_GRACEFUL", output)
            self.assertLess(elapsed, 2, "owned child kept stdout pipe open past bounded shutdown")
            self.assertTrue(port_is_released(port))
        finally:
            if process.poll() is None:
                force_cleanup_process_tree(process, timeout=1)

    def test_stubborn_child_is_force_cleaned_after_bounded_grace_period(self) -> None:
        port = free_port()
        process = start_fixture("parent", "stubborn", port)
        try:
            wait_for_listener(port)
            started = time.monotonic()
            with self.assertRaisesRegex(
                ProcessTreeShutdownError,
                "PROCESS_TREE_GRACEFUL_SHUTDOWN_TIMEOUT",
            ):
                stop_process(process, graceful_timeout=0.3, force_timeout=1)
            self.assertLess(time.monotonic() - started, 2)
            self.assertIsNotNone(process.poll())
            self.assertTrue(port_is_released(port))
        finally:
            if process.poll() is None:
                force_cleanup_process_tree(process, timeout=1)

    def test_finally_force_cleanup_is_bounded_and_releases_listener(self) -> None:
        port = free_port()
        process = start_fixture("parent", "stubborn", port)
        wait_for_listener(port)
        started = time.monotonic()
        force_cleanup_process_tree(process, timeout=1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNotNone(process.poll())
        self.assertTrue(port_is_released(port))

    def test_owned_group_shutdown_does_not_touch_unrelated_session(self) -> None:
        owned_port = free_port()
        unrelated_port = free_port()
        owned = start_fixture("parent", "normal", owned_port)
        unrelated = start_fixture("child", "normal", unrelated_port)
        try:
            wait_for_listener(owned_port)
            wait_for_listener(unrelated_port)
            exit_code, _output = stop_process(owned, graceful_timeout=1, force_timeout=1)
            self.assertEqual(exit_code, 0)
            self.assertIsNone(unrelated.poll())
            self.assertFalse(port_is_released(unrelated_port))
        finally:
            if owned.poll() is None:
                force_cleanup_process_tree(owned, timeout=1)
            if unrelated.poll() is None:
                force_cleanup_process_tree(unrelated, timeout=1)
        self.assertTrue(port_is_released(owned_port))
        self.assertTrue(port_is_released(unrelated_port))


if __name__ == "__main__":
    unittest.main()
