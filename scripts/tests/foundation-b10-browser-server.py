from __future__ import annotations

import importlib.util
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b9-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b9_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B10_BROWSER_FIXTURE_UNAVAILABLE")
B9 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B9)
B9.B8.B4.WORKSPACE_ID = "workspace-b10"
B9.B8.B4.TRACE_ID = "trace-b10-browser"


class Handler(B9.Handler):
    server_version = "DaonB10Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/v1/workspaces/workspace-b10/operations/status":
            self._send_json(B9.B8.B4.envelope({
                "workspace_id": "workspace-b10",
                "overall_status": "warning",
                "checked_at": "2026-08-15T06:30:00Z",
                "components": [
                    {"component_id": "provider", "status": "ready", "safe_code": "PROVIDER_READY", "pending_count": 0, "recovery_action": "none"},
                    {"component_id": "api", "status": "ready", "safe_code": "API_READY", "pending_count": 0, "recovery_action": "none"},
                    {"component_id": "storage", "status": "ready", "safe_code": "STORAGE_READY", "pending_count": 0, "recovery_action": "none"},
                    {"component_id": "sync", "status": "warning", "safe_code": "SYNC_PENDING", "pending_count": 2, "recovery_action": "open_sync_settings"},
                    {"component_id": "queue", "status": "warning", "safe_code": "QUEUE_ATTENTION_REQUIRED", "pending_count": 3, "recovery_action": "refresh_status"},
                ],
            }))
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18490
    print(f"READY {port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
