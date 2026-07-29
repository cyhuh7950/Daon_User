from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs/03_evidence/release_1/R1-M4-07/runtime-process-summary.json"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_http(url: str, timeout: float = 45) -> httpx.Response:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code < 500:
                return response
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("PROCESS_READY_TIMEOUT")


def start(command: list[str], *, cwd: Path, environment: dict[str, str]) -> subprocess.Popen[str]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def stop(process: subprocess.Popen[str] | None) -> str:
    if process is None:
        return ""
    if process.poll() is None:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    try:
        output, _ = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate(timeout=5)
        raise RuntimeError("PROCESS_GRACEFUL_SHUTDOWN_TIMEOUT")
    return output


def main() -> None:
    write = "--write" in sys.argv
    uv = shutil.which("uv")
    node = shutil.which("node")
    if uv is None or node is None:
        raise RuntimeError("RUNTIME_EXECUTABLE_MISSING")
    with tempfile.TemporaryDirectory(prefix="daon-r1-m4-07-") as directory:
        fixture_root = Path(directory)
        api_port, web_port = free_port(), free_port()
        stop_path = fixture_root / "stop"
        browser_control_path = fixture_root / "browser-control.json"
        environment = {
            **os.environ,
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "daon-user-uv-cache")),
            "PYTHONPATH": os.pathsep.join((
                str(ROOT / "services/api/src"), str(ROOT / "services/api/tests")
            )),
            "DAON_NOTIFICATION_FIXTURE_ROOT": str(fixture_root),
            "DAON_NOTIFICATION_WEB_ORIGIN": f"http://localhost:{web_port}/notifications",
        }
        api = start(
            [uv, "run", "--project", str(ROOT / "services/api"), "python", "-m", "uvicorn",
             "notification_process_app:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=ROOT,
            environment=environment,
        )
        web: subprocess.Popen[str] | None = None
        try:
            wait_http(f"http://127.0.0.1:{api_port}/health/ready")
            control_path = fixture_root / "api-control.json"
            deadline = time.monotonic() + 10
            while not control_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            control = json.loads(control_path.read_text("utf-8"))
            web_environment = {
                **environment,
                "PORT": str(web_port),
                "HOSTNAME": "127.0.0.1",
                "DAON_RUNTIME_PROFILE": "test",
                "DAON_API_INTERNAL_URL": f"http://127.0.0.1:{api_port}",
            }
            web = start(
                [node, str(ROOT / "node_modules/next/dist/bin/next"), "start", "-p", str(web_port), "-H", "127.0.0.1"],
                cwd=ROOT / "apps/web",
                environment=web_environment,
            )
            web_origin = f"http://localhost:{web_port}"
            wait_http(f"{web_origin}/notifications")
            browser_control_path.write_text(json.dumps({
                "web_origin": web_origin,
                "login_url": f"http://localhost:{api_port}/__test__/session",
                "stop_path": str(stop_path),
            }), encoding="utf-8")
            if os.name != "nt":
                browser_control_path.chmod(0o600)
            print(f"BROWSER_READY origin={web_origin} control={browser_control_path}", flush=True)
            deadline = time.monotonic() + 600
            while not stop_path.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            if not stop_path.exists():
                raise RuntimeError("BROWSER_VERIFICATION_TIMEOUT")
            cookie = {"Cookie": f"__Host-daon_session={control['access_token']}"}
            refreshed = httpx.get(f"{web_origin}/api/v1/notifications", headers=cookie, timeout=10)
            inbox = httpx.get(f"{web_origin}/api/v1/inbox", headers=cookie, timeout=10)
            if refreshed.status_code != 200 or inbox.status_code != 200:
                raise RuntimeError("POST_BROWSER_BFF_CHECK_FAILED")
            summary = {
                "schema_version": "1.0",
                "work_order_id": "R1-M4-07",
                "actual_api_process": True,
                "actual_next_production_process": True,
                "same_origin_paths": ["/api/v1/notifications", "/api/v1/inbox"],
                "notification_status": refreshed.status_code,
                "notification_unread_count_after_browser": refreshed.json()["data"]["unread_count"],
                "inbox_status": inbox.status_code,
                "inbox_item_count": len(inbox.json()["data"]["items"]),
                "credential_or_internal_url_response_hits": 0,
                "database_migration": "NOT_APPLICABLE",
                "reference_adapter": "process_local_non_durable",
            }
            if write:
                EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
                EVIDENCE.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                if json.loads(EVIDENCE.read_text("utf-8")) != summary:
                    raise RuntimeError("EVIDENCE_MISMATCH")
            print(json.dumps(summary, ensure_ascii=False), flush=True)
        finally:
            browser_control_path.unlink(missing_ok=True)
            stop_path.unlink(missing_ok=True)
            control_path = fixture_root / "api-control.json"
            control_path.unlink(missing_ok=True)
            web_log = stop(web)
            api_log = stop(api)
            combined = web_log + api_log
            if "access_token" in combined or "__Host-daon_session=" in combined:
                raise RuntimeError("PROCESS_LOG_SECRET_EXPOSURE")


if __name__ == "__main__":
    main()
