from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from test_identity_support import (
    FakeVerifiedOidcProvider,
    MutableClock,
    POLICY_VERSION,
    TRACE_ID,
    create_service,
)
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, DevicePlatform


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs/03_evidence/release_1/R1-M4-05"
NATIVE_TEST_LOGIN = "runtime-native-probe"
NATIVE_TEST_PASSWORD = "probe-only-not-a-secret-value"


class DiscardTestEmailSender:
    def send(self, **_message: str) -> None:
        return None


class NativeFramingUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _respond(self, status: int, body: bytes, media_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) <= len("Bearer "):
            self._respond(401, b'{"error":{"code":"AUTHENTICATION_REQUIRED"}}')
        elif self.path == "/api/v1/backups?workspace_id=workspace-probe":
            body = json.dumps({"data": "x" * 200_000, "meta": {}}).encode()
            self._respond(200, body, "application/json; charset=utf-8")
        elif self.path == "/api/v1/workspaces/workspace-probe/citations/citation-probe/content?notebook_id=notebook-probe":
            body = b"%PDF-1.4\n% runtime framing probe\n"
            self._respond(200, body, "application/pdf; version=1.4")
        else:
            self._respond(404, b'{"error":{"code":"RESOURCE_UNAVAILABLE"}}')

    def do_POST(self) -> None:  # noqa: N802
        self._respond(405, b'{"error":{"code":"METHOD_NOT_ALLOWED"}}')

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def port_is_released(port: int) -> bool:
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


class ProcessTreeShutdownError(RuntimeError):
    pass


def _signal_owned_process_tree(process: subprocess.Popen[str], *, force: bool) -> None:
    if os.name == "nt":
        if force:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        elif process.poll() is None:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        return
    if process.poll() is None:
        try:
            if os.getpgid(process.pid) != process.pid:
                raise ProcessTreeShutdownError("PROCESS_TREE_NOT_ISOLATED")
        except ProcessLookupError:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass


def _wait_posix_process_group_gone(group_id: int, timeout: float) -> bool:
    if os.name == "nt":
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def force_cleanup_process_tree(process: subprocess.Popen[str], timeout: float = 5) -> str:
    if os.name == "nt" and process.poll() is not None:
        output, _ = process.communicate(timeout=timeout)
        return output
    _signal_owned_process_tree(process, force=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        try:
            output, _ = process.communicate(timeout=1)
        except subprocess.TimeoutExpired as final_error:
            if process.stdout is not None:
                process.stdout.close()
            raise ProcessTreeShutdownError("PROCESS_TREE_FORCE_CLEANUP_TIMEOUT") from final_error
        raise ProcessTreeShutdownError("PROCESS_TREE_FORCE_CLEANUP_TIMEOUT") from error
    if not _wait_posix_process_group_gone(process.pid, timeout):
        raise ProcessTreeShutdownError("PROCESS_GROUP_REMAINED_AFTER_FORCE_CLEANUP")
    return output


def wait_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 30) -> httpx.Response:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, headers=headers, timeout=2)
            if response.status_code < 500:
                return response
        except (httpx.HTTPError, OSError) as error:
            last_error = error
        time.sleep(0.1)
    raise RuntimeError("PROCESS_READY_TIMEOUT") from last_error


def stop_process(
    process: subprocess.Popen[str],
    *,
    graceful_timeout: float = 15,
    force_timeout: float = 5,
) -> tuple[int, str]:
    _signal_owned_process_tree(process, force=False)
    try:
        output, _ = process.communicate(timeout=graceful_timeout)
    except subprocess.TimeoutExpired as error:
        force_cleanup_process_tree(process, timeout=force_timeout)
        raise ProcessTreeShutdownError("PROCESS_TREE_GRACEFUL_SHUTDOWN_TIMEOUT") from error
    if not _wait_posix_process_group_gone(process.pid, force_timeout):
        force_cleanup_process_tree(process, timeout=force_timeout)
        raise ProcessTreeShutdownError("PROCESS_GROUP_REMAINED_AFTER_GRACEFUL_SHUTDOWN")
    return int(process.returncode or 0), output


def start_api(port: int, database_path: Path) -> subprocess.Popen[str]:
    environment = {
        **os.environ,
        "DAON_RUNTIME_PROFILE": "test",
        "DAON_API_BIND_HOST": "127.0.0.1",
        "DAON_API_PORT": str(port),
        "DAON_API_DATABASE_PATH": str(database_path),
        "DAON_POLICY_VERSION": POLICY_VERSION,
        "PYTHONPATH": str(ROOT / "services/api/src"),
    }
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "daon_user_api"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def start_next(port: int, api_port: int) -> subprocess.Popen[str]:
    environment = {
        **os.environ,
        "PORT": str(port),
        "HOSTNAME": "127.0.0.1",
        "DAON_RUNTIME_PROFILE": "test",
        "DAON_API_INTERNAL_URL": f"http://127.0.0.1:{api_port}",
        "DAON_PUBLIC_GATEWAY_URL": f"https://localhost:{port}",
    }
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("NEXT_NODE_EXECUTABLE_NOT_FOUND")
    next_cli = ROOT / "node_modules/next/dist/bin/next"
    if not next_cli.is_file():
        raise RuntimeError("NEXT_CLI_NOT_FOUND")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [executable, str(next_cli), "start", "-p", str(port), "-H", "127.0.0.1"],
        cwd=ROOT / "apps/web",
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def next_exit_is_graceful(exit_code: int, platform_name: str = os.name) -> bool:
    if platform_name == "nt":
        return exit_code in {0, 0xC000013A}
    return exit_code in {0, -signal.SIGTERM, 128 + signal.SIGTERM}


def seed_database(database_path: Path) -> tuple[str, str]:
    clock = MutableClock(datetime.now(timezone.utc))
    audit = AuditEventStore()
    identity, identity_repository, _, _ = create_service(
        database_path, clock=clock, audit_store=audit
    )
    identity._email_sender = DiscardTestEmailSender()
    identity.signup(
        login_id=NATIVE_TEST_LOGIN,
        email="runtime-native-probe@example.invalid",
        password=NATIVE_TEST_PASSWORD,
        trace_id="trace-native-probe-signup",
        policy_version=POLICY_VERSION,
    )
    with identity_repository.transaction() as connection:
        connection.execute(
            "UPDATE users SET state='active', email_verified_at=? WHERE login_id=?",
            (datetime.now(timezone.utc).isoformat(), NATIVE_TEST_LOGIN),
        )
    start = identity.begin_oidc_login(
        issuer="https://login.example.com",
        client_id="daon-web",
        audience="daon-user-api",
        redirect_uri="https://app.example.com/auth/callback",
        client_kind=ClientKind.WEB,
        tenant_id="tenant-001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    provider = FakeVerifiedOidcProvider()
    provider.expected_nonce = start.nonce
    session = identity.complete_oidc_login(
        state=start.state,
        authorization_code=provider.authorization_code,
        code_verifier=start.code_verifier,
        client_id="daon-web",
        redirect_uri="https://app.example.com/auth/callback",
        provider=provider,
        platform=DevicePlatform.WEB,
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    authorization_repository = SqliteAuthorizationRepository(database_path)
    authorization_repository.bootstrap_workspace(
        tenant_id=session.tenant_id,
        workspace_id="workspace-001",
        owner_user_id=session.user_id,
        owner_role=Role.ORGANIZATION_ADMIN,
        workspace_kind="organization",
        data_area="cloud_sync",
        cost_limit_cents=1000,
        now=clock(),
    )
    authorization_repository.close()
    identity_repository.close()
    return session.access_token, session.user_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--with-next", action="store_true")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="daon-r1-m4-05-") as directory:
        database_path = Path(directory) / "runtime.sqlite3"
        token, expected_user = seed_database(database_path)
        api_port = free_port()
        api_origin = f"http://127.0.0.1:{api_port}"
        cookie = {"Cookie": f"__Host-daon_session={token}", "X-Trace-Id": "trace-process-001"}

        first = start_api(api_port, database_path)
        first_log = ""
        second = None
        next_process = None
        framing_next_process = None
        framing_upstream = None
        framing_thread = None
        next_log = ""
        next_exit_code = None
        next_graceful_shutdown_observed = None
        web_port: int | None = None
        try:
            live = wait_json(f"{api_origin}/health/live")
            ready = wait_json(f"{api_origin}/health/ready")
            session = wait_json(f"{api_origin}/api/v1/session", headers=cookie)
            authorization = httpx.post(
                f"{api_origin}/api/v1/workspaces/workspace-001/authorization/evaluations",
                headers={
                    **cookie,
                    "Content-Type": "application/json",
                    "Idempotency-Key": "idem-process-001",
                    "X-Tenant-Id": "tenant-foreign",
                    "X-Role": "viewer",
                },
                json={"action": "view", "requested_permissions": []},
                timeout=5,
            )
            if [live.status_code, ready.status_code, session.status_code, authorization.status_code] != [200, 200, 200, 200]:
                raise RuntimeError("API_RAW_HTTP_CONTRACT_FAILED")
            if session.json()["data"]["user_id"] != expected_user:
                raise RuntimeError("API_IDENTITY_PROJECTION_MISMATCH")
            _first_code, first_log = stop_process(first)
            graceful_markers = (
                "Shutting down",
                "Application shutdown complete",
                "Finished server process",
            )
            if not all(marker in first_log for marker in graceful_markers):
                raise RuntimeError("API_GRACEFUL_LIFECYCLE_NOT_OBSERVED")
            if _first_code != 0:
                raise RuntimeError(f"API_GRACEFUL_EXIT_NONZERO:{_first_code}")

            second = start_api(api_port, database_path)
            restarted_live = wait_json(f"{api_origin}/health/live")
            restarted_ready = wait_json(f"{api_origin}/health/ready")
            restarted_session = wait_json(f"{api_origin}/api/v1/session", headers=cookie)
            if [restarted_live.status_code, restarted_ready.status_code, restarted_session.status_code] != [200, 200, 200]:
                raise RuntimeError("API_SAME_PORT_RESTART_FAILED")

            bff_status = None
            bff_trace = None
            same_meaning = None
            bff_write_status = None
            bff_csrf_rejected_status = None
            bff_csrf_rejected_upstream_events = None
            client_bundle_forbidden_hits = None
            native_access = None
            if arguments.with_next:
                web_port = free_port()
                next_process = start_next(web_port, api_port)
                web_origin = f"http://localhost:{web_port}"
                public_web_origin = f"https://localhost:{web_port}"
                wait_json(f"{web_origin}/")
                direct = httpx.get(f"{api_origin}/api/v1/session", headers=cookie, timeout=5)
                decoy_cookie = "private-other-cookie"
                browser_cookie = {
                    **cookie,
                    "Cookie": f"analytics={decoy_cookie}; __Host-daon_session={token}",
                }
                bff = httpx.get(f"{web_origin}/bff/api/session", headers=browser_cookie, timeout=10)
                bff_status = bff.status_code
                bff_trace = bff.headers.get("x-trace-id")
                same_meaning = direct.json()["data"] == bff.json()["data"]
                response_text = bff.text
                if (
                    bff.status_code != 200
                    or not same_meaning
                    or api_origin in response_text
                    or token in response_text
                    or decoy_cookie in response_text
                ):
                    raise RuntimeError("NEXT_BFF_PROCESS_CONTRACT_FAILED")
                native_empty_login = httpx.post(
                    f"{web_origin}/api/v1/auth/native/login",
                    headers={"Content-Type": "application/json", "X-Trace-Id": "trace-native-login-empty-001"},
                    json={}, timeout=10,
                )
                native_login = httpx.post(
                    f"{web_origin}/api/v1/auth/native/login",
                    headers={"Content-Type": "application/json", "X-Trace-Id": "trace-native-login-001"},
                    json={"login_id": NATIVE_TEST_LOGIN, "password": NATIVE_TEST_PASSWORD}, timeout=10,
                )
                if native_login.status_code != 200:
                    raise RuntimeError(f"NEXT_NATIVE_LOGIN_FAILED:{native_login.status_code}")
                native_data = native_login.json()["data"]
                native_access = native_data["access_credential"]
                native_workspace = native_data["workspace_id"]
                native_unauthorized = httpx.get(f"{web_origin}/api/v1/session", timeout=10)
                native_authorized = httpx.get(
                    f"{web_origin}/api/v1/session",
                    headers={"Authorization": f"Bearer {native_access}", "X-Trace-Id": "trace-native-session-001"},
                    timeout=10,
                )
                native_cookie_trace = "trace-native-cookie-reject-001"
                native_cookie = httpx.get(
                    f"{web_origin}/api/v1/session",
                    headers={
                        "Authorization": f"Bearer {native_access}",
                        "Cookie": "blocked=value",
                        "X-Trace-Id": native_cookie_trace,
                    }, timeout=10,
                )
                native_unknown_trace = "trace-native-unknown-reject-001"
                native_unknown = httpx.get(
                    f"{web_origin}/api/v1/unknown",
                    headers={"Authorization": f"Bearer {native_access}", "X-Trace-Id": native_unknown_trace},
                    timeout=10,
                )
                recovery = httpx.get(
                    f"{web_origin}/api/v1/backups",
                    params={"workspace_id": native_workspace},
                    headers={"Authorization": f"Bearer {native_access}", "X-Trace-Id": "trace-native-recovery-001"},
                    timeout=10,
                )
                notifications = httpx.get(f"{web_origin}/bff/api/notifications", params={"limit": 50}, headers=browser_cookie, timeout=10)
                inbox = httpx.get(f"{web_origin}/bff/api/inbox", params={"limit": 50}, headers=browser_cookie, timeout=10)
                for response_with_framing in (native_authorized, recovery):
                    media_type = response_with_framing.headers.get("content-type", "").split(";", 1)[0].lower()
                    if (
                        media_type != "application/json"
                        or response_with_framing.headers.get("content-length") != str(len(response_with_framing.content))
                        or "transfer-encoding" in response_with_framing.headers
                    ):
                        raise RuntimeError("NEXT_NATIVE_JSON_FRAMING_FAILED")
                if (
                    native_empty_login.status_code == 404
                    or native_unauthorized.status_code != 401
                    or native_authorized.status_code != 200
                    or native_cookie.status_code != 400
                    or native_unknown.status_code != 404
                    or recovery.status_code not in {200, 503}
                    or notifications.status_code != 200
                    or inbox.status_code != 200
                ):
                    raise RuntimeError(
                        "NEXT_NATIVE_AND_WEB_ADAPTER_CONTRACT_FAILED:"
                        f"empty_login={native_empty_login.status_code}:login={native_login.status_code}:unauthorized={native_unauthorized.status_code}:"
                        f"authorized={native_authorized.status_code}:cookie={native_cookie.status_code}:"
                        f"unknown={native_unknown.status_code}:recovery={recovery.status_code}:"
                        f"notifications={notifications.status_code}:inbox={inbox.status_code}"
                    )
                write = httpx.post(
                    f"{web_origin}/bff/api/workspaces/workspace-001/authorization/evaluations",
                    headers={
                        **browser_cookie,
                        "Origin": public_web_origin,
                        "Sec-Fetch-Site": "same-origin",
                        "Content-Type": "application/json",
                        "Idempotency-Key": "idem-bff-process-001",
                        "X-Trace-Id": "trace-bff-write-001",
                    },
                    json={"action": "view", "requested_permissions": []},
                    timeout=10,
                )
                bff_write_status = write.status_code
                cross_trace = "trace-bff-cross-origin-001"
                csrf_rejected = httpx.post(
                    f"{web_origin}/bff/api/workspaces/workspace-001/authorization/evaluations",
                    headers={
                        **browser_cookie,
                        "Origin": "https://cross-origin.invalid",
                        "Sec-Fetch-Site": "cross-site",
                        "Content-Type": "application/json",
                        "X-Trace-Id": cross_trace,
                    },
                    json={"action": "view", "requested_permissions": []},
                    timeout=10,
                )
                bff_csrf_rejected_status = csrf_rejected.status_code
                rejected_audit = httpx.get(
                    f"{web_origin}/bff/api/audit-events",
                    headers={**browser_cookie, "X-Trace-Id": "trace-bff-audit-check-001"},
                    params={"tenant_id": "tenant-001", "trace_id": cross_trace, "limit": 50},
                    timeout=10,
                )
                if rejected_audit.status_code != 200:
                    raise RuntimeError("NEXT_BFF_CSRF_AUDIT_CHECK_FAILED")
                bff_csrf_rejected_upstream_events = len(rejected_audit.json()["data"]["items"])
                native_rejected_upstream_events = 0
                for rejected_trace in (native_cookie_trace, native_unknown_trace):
                    native_rejected_audit = httpx.get(
                        f"{web_origin}/bff/api/audit-events",
                        headers={**browser_cookie, "X-Trace-Id": f"audit-{rejected_trace}"},
                        params={"tenant_id": "tenant-001", "trace_id": rejected_trace, "limit": 50},
                        timeout=10,
                    )
                    if native_rejected_audit.status_code != 200:
                        raise RuntimeError("NEXT_NATIVE_REJECT_AUDIT_CHECK_FAILED")
                    native_rejected_upstream_events += len(native_rejected_audit.json()["data"]["items"])
                combined_bff_text = response_text + write.text + csrf_rejected.text + rejected_audit.text
                if (
                    bff_write_status != 200
                    or bff_csrf_rejected_status != 403
                    or bff_csrf_rejected_upstream_events != 0
                    or native_rejected_upstream_events != 0
                    or token in combined_bff_text
                    or decoy_cookie in combined_bff_text
                    or api_origin in combined_bff_text
                ):
                    raise RuntimeError(
                        "NEXT_BFF_WRITE_CSRF_CONTRACT_FAILED:"
                        f"write={bff_write_status}:write_code={write.json().get('error', {}).get('code')}:"
                        f"csrf={bff_csrf_rejected_status}:"
                        f"upstream_events={bff_csrf_rejected_upstream_events}:"
                        f"credential_hit={token in combined_bff_text or decoy_cookie in combined_bff_text}:"
                        f"internal_url_hit={api_origin in combined_bff_text}"
                    )
                static_root = ROOT / "apps/web/.next/static"
                forbidden = ("NEXT" + "_PUBLIC_API_BASE_URL", api_origin)
                client_bundle_forbidden_hits = sum(
                    text.count(item)
                    for path in static_root.rglob("*") if path.is_file()
                    for text in [path.read_text("utf-8", errors="ignore")]
                    for item in forbidden
                )
                if client_bundle_forbidden_hits:
                    raise RuntimeError("NEXT_CLIENT_BUNDLE_INTERNAL_ADDRESS_EXPOSED")
                raw_next_exit_code, next_log = stop_process(next_process)
                next_process = None
                if not next_exit_is_graceful(raw_next_exit_code):
                    raise RuntimeError(f"NEXT_GRACEFUL_EXIT_NONZERO:{raw_next_exit_code}")
                next_exit_code = 0
                if not port_is_released(web_port):
                    raise RuntimeError("NEXT_LISTENER_REMAINED_AFTER_SHUTDOWN")
                next_graceful_shutdown_observed = True

                framing_api_port = free_port()
                framing_web_port = free_port()
                framing_upstream = ThreadingHTTPServer(("127.0.0.1", framing_api_port), NativeFramingUpstream)
                framing_thread = threading.Thread(target=framing_upstream.serve_forever, daemon=True)
                framing_thread.start()
                framing_next_process = start_next(framing_web_port, framing_api_port)
                framing_web_origin = f"http://localhost:{framing_web_port}"
                wait_json(f"{framing_web_origin}/")
                framing_headers = {"Authorization": "Bearer runtime-probe-credential"}
                framing_recovery = httpx.get(
                    f"{framing_web_origin}/api/v1/backups",
                    params={"workspace_id": "workspace-probe"}, headers=framing_headers, timeout=10,
                )
                framing_pdf = httpx.get(
                    f"{framing_web_origin}/api/v1/workspaces/workspace-probe/citations/citation-probe/content",
                    params={"notebook_id": "notebook-probe"}, headers=framing_headers, timeout=10,
                )
                for framed, expected_type in (
                    (framing_recovery, "application/json"), (framing_pdf, "application/pdf"),
                ):
                    if (
                        framed.status_code != 200
                        or framed.headers.get("content-type", "").split(";", 1)[0].lower() != expected_type
                        or framed.headers.get("content-length") != str(len(framed.content))
                        or "transfer-encoding" in framed.headers
                    ):
                        raise RuntimeError("NEXT_NATIVE_FAKE_UPSTREAM_FRAMING_FAILED")
                if len(framing_recovery.content) <= 128 * 1024 or not framing_pdf.content.startswith(b"%PDF-"):
                    raise RuntimeError("NEXT_NATIVE_FAKE_UPSTREAM_BODY_FAILED")
                framing_next_exit, framing_next_log = stop_process(framing_next_process)
                framing_next_process = None
                next_log += framing_next_log
                if not next_exit_is_graceful(framing_next_exit) or not port_is_released(framing_web_port):
                    raise RuntimeError("NEXT_NATIVE_FRAMING_PROCESS_CLEANUP_FAILED")
                framing_upstream.shutdown()
                framing_upstream.server_close()
                framing_upstream = None
                framing_thread.join(timeout=5)
                if framing_thread.is_alive() or not port_is_released(framing_api_port):
                    raise RuntimeError("NEXT_NATIVE_FRAMING_UPSTREAM_CLEANUP_FAILED")
                framing_thread = None

            _second_code, second_log = stop_process(second)
            second = None
            if not all(marker in second_log for marker in graceful_markers):
                raise RuntimeError("API_RESTART_GRACEFUL_LIFECYCLE_NOT_OBSERVED")
            if _second_code != 0:
                raise RuntimeError(f"API_RESTART_GRACEFUL_EXIT_NONZERO:{_second_code}")
            if not port_is_released(api_port):
                raise RuntimeError("API_LISTENER_REMAINED_AFTER_SHUTDOWN")
            combined_log = first_log + second_log + next_log
            if (
                token in combined_log
                or NATIVE_TEST_PASSWORD in combined_log
                or (native_access is not None and native_access in combined_log)
                or "private-other-cookie" in combined_log
                or str(database_path) in combined_log
                or api_origin in next_log
            ):
                raise RuntimeError("PROCESS_LOG_SECRET_OR_INTERNAL_ADDRESS_EXPOSED")
            runtime_summary = {
                "schema_version": "1.0",
                "work_order_id": "R1-M4-05",
                "actual_api_process": True,
                "raw_http": {
                    "live": live.status_code,
                    "ready": ready.status_code,
                    "session": session.status_code,
                    "authorization": authorization.status_code,
                    "trace_id": authorization.headers.get("x-trace-id"),
                },
                "graceful_shutdown_observed": True,
                "graceful_exit_mode": "signal_handled",
                "graceful_exit_code": 0,
                "same_port_restart": True,
                "restart_statuses": [restarted_live.status_code, restarted_ready.status_code, restarted_session.status_code],
                "restart_graceful_shutdown_observed": True,
                "restart_exit_code": 0,
                "credential_or_database_path_log_hits": 0,
                "owned_processes_remaining": 0,
                "listeners_remaining": 0,
            }
            bff_summary = {
                "schema_version": "1.0",
                "work_order_id": "R1-M4-05",
                "actual_next_production_process": bool(arguments.with_next),
                "browser_request_path": "/bff/api/session",
                "same_origin": bool(arguments.with_next),
                "status": bff_status,
                "trace_id": bff_trace,
                "direct_and_bff_session_meaning_equal": same_meaning,
                "graceful_shutdown_observed": next_graceful_shutdown_observed,
                "graceful_exit_code": next_exit_code,
                "same_origin_write_status": bff_write_status,
                "cross_origin_write_status": bff_csrf_rejected_status,
                "cross_origin_write_upstream_audit_events": bff_csrf_rejected_upstream_events,
                "forwarded_cookie_names": ["__Host-daon_session"] if arguments.with_next else None,
                "client_bundle_forbidden_hits": client_bundle_forbidden_hits,
                "response_internal_url_or_credential_hits": 0,
                "gui_browser_claimed": False,
                "owned_processes_remaining": 0,
                "listeners_remaining": 0,
            }
            if arguments.write:
                EVIDENCE.mkdir(parents=True, exist_ok=True)
                (EVIDENCE / "runtime-process-summary.json").write_text(
                    json.dumps(runtime_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                (EVIDENCE / "bff-network-summary.json").write_text(
                    json.dumps(bff_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            else:
                for name, expected in (
                    ("runtime-process-summary.json", runtime_summary),
                    ("bff-network-summary.json", bff_summary),
                ):
                    actual = json.loads((EVIDENCE / name).read_text("utf-8"))
                    if actual != expected:
                        raise RuntimeError(f"EVIDENCE_MISMATCH:{name}")
            print(json.dumps({"runtime": runtime_summary, "bff": bff_summary}, ensure_ascii=False))
        finally:
            for process in (framing_next_process, next_process, second, first):
                if process is not None:
                    force_cleanup_process_tree(process)
            if framing_upstream is not None:
                framing_upstream.shutdown()
                framing_upstream.server_close()
            if framing_thread is not None:
                framing_thread.join(timeout=5)


if __name__ == "__main__":
    main()
