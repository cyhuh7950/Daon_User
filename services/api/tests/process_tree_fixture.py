from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time


def child(mode: str, port: int) -> None:
    if mode == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()
        print(f"CHILD_READY pid={os.getpid()}", flush=True)
        time.sleep(4)


def parent(mode: str, port: int) -> None:
    subprocess.Popen(
        [sys.executable, __file__, "child", mode, str(port)],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    def stop(_signum: int, _frame: object) -> None:
        print("PARENT_GRACEFUL", flush=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    print(f"PARENT_READY pid={os.getpid()}", flush=True)
    time.sleep(10)


if __name__ == "__main__":
    role, fixture_mode, fixture_port = sys.argv[1:]
    if role == "child":
        child(fixture_mode, int(fixture_port))
    else:
        parent(fixture_mode, int(fixture_port))
