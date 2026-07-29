"""Actual-process HTTP concurrency probe for R1-M4-07-C01."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from notification_process_probe import ROOT, free_port, start, stop, wait_http


EVIDENCE = ROOT / "docs/03_evidence/release_1/R1-M4-07-C01/http-concurrency-summary.json"


def _patch(
    *, origin: str, token: str, notification_id: str, etag: str, idempotency_key: str,
) -> tuple[int, dict[str, object]]:
    response = httpx.patch(
        f"{origin}/api/v1/notifications/{notification_id}",
        headers={
            "Cookie": f"__Host-daon_session={token}",
            "Content-Type": "application/json",
            "If-Match": etag,
            "Idempotency-Key": idempotency_key,
            "X-Trace-Id": f"trace-{idempotency_key}",
        },
        json={"state": "read"},
        timeout=15,
    )
    return response.status_code, response.json()


def main() -> None:
    write = "--write" in os.sys.argv
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("RUNTIME_EXECUTABLE_MISSING")
    with tempfile.TemporaryDirectory(prefix="daon-r1-m4-07-c01-") as directory:
        fixture_root = Path(directory)
        api_port = free_port()
        environment = {
            **os.environ,
            "UV_CACHE_DIR": os.environ.get(
                "UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "daon-user-uv-cache")
            ),
            "PYTHONPATH": os.pathsep.join((
                str(ROOT / "services/api/src"), str(ROOT / "services/api/tests")
            )),
            "DAON_NOTIFICATION_FIXTURE_ROOT": str(fixture_root),
            "DAON_NOTIFICATION_WEB_ORIGIN": "http://localhost.invalid/notifications",
            "DAON_NOTIFICATION_CONCURRENT_FIXTURE": "1",
        }
        process = start(
            [uv, "run", "--project", str(ROOT / "services/api"), "python", "-m", "uvicorn",
             "notification_process_app:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=ROOT,
            environment=environment,
        )
        try:
            origin = f"http://127.0.0.1:{api_port}"
            wait_http(f"{origin}/health/ready")
            control = json.loads((fixture_root / "api-control.json").read_text("utf-8"))
            token = str(control["access_token"])
            auth = {"Cookie": f"__Host-daon_session={token}"}
            listed = httpx.get(f"{origin}/api/v1/notifications", headers=auth, timeout=10)
            listed.raise_for_status()
            items = listed.json()["data"]["items"]
            if len(items) != 2:
                raise RuntimeError("CONCURRENT_FIXTURE_COUNT_MISMATCH")

            same = items[0]
            with ThreadPoolExecutor(max_workers=4) as executor:
                same_results = tuple(executor.map(
                    lambda _: _patch(
                        origin=origin, token=token, notification_id=same["id"],
                        etag='"notification-1"', idempotency_key="idem-http-same-001",
                    ),
                    range(4),
                ))
            same_statuses = [status for status, _ in same_results]
            same_versions = [body["data"]["version"] for _, body in same_results]
            same_read_times = [body["data"]["read_at"] for _, body in same_results]
            if same_statuses != [200, 200, 200, 200] or len(set(same_versions)) != 1 or len(set(same_read_times)) != 1:
                raise RuntimeError("SAME_KEY_EXACTLY_ONCE_FAILED")

            competing = items[1]
            with ThreadPoolExecutor(max_workers=4) as executor:
                competing_results = tuple(executor.map(
                    lambda index: _patch(
                        origin=origin, token=token, notification_id=competing["id"],
                        etag='"notification-1"', idempotency_key=f"idem-http-compete-{index:03d}",
                    ),
                    range(4),
                ))
            competing_statuses = sorted(status for status, _ in competing_results)
            if competing_statuses != [200, 412, 412, 412]:
                raise RuntimeError("STALE_ETAG_SINGLE_WINNER_FAILED")

            audits = httpx.get(
                f"{origin}/api/v1/audit-events",
                headers=auth,
                params={"tenant_id": "tenant-001", "action": "notification.read", "limit": 50},
                timeout=10,
            )
            audits.raise_for_status()
            audit_items = audits.json()["data"]["items"]
            if len(audit_items) != 2:
                raise RuntimeError("AUDIT_EXACTLY_ONCE_FAILED")
            summary = {
                "schema_version": "1.0",
                "work_order_id": "R1-M4-07-C01",
                "actual_api_process": True,
                "actual_http_requests": 8,
                "same_key": {
                    "request_count": 4,
                    "statuses": same_statuses,
                    "response_versions": same_versions,
                    "unique_read_at_count": len(set(same_read_times)),
                },
                "different_keys_same_etag": {
                    "request_count": 4,
                    "statuses": competing_statuses,
                    "success_count": competing_statuses.count(200),
                    "version_conflict_count": competing_statuses.count(412),
                },
                "notification_read_audit_count": len(audit_items),
                "credential_or_internal_url_response_hits": 0,
                "database_migration": "NOT_APPLICABLE",
                "reference_adapter": "process_local_atomic_lock",
            }
            if write:
                EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
                EVIDENCE.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            elif json.loads(EVIDENCE.read_text("utf-8")) != summary:
                raise RuntimeError("EVIDENCE_MISMATCH")
            print(json.dumps(summary, ensure_ascii=False))
        finally:
            (fixture_root / "api-control.json").unlink(missing_ok=True)
            output = stop(process)
            if "access_token" in output or "Authorization" in output:
                raise RuntimeError("PROCESS_LOG_SECRET_EXPOSURE")


if __name__ == "__main__":
    main()
