from __future__ import annotations

import importlib.util
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b11-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b11_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B12_BROWSER_FIXTURE_UNAVAILABLE")
B11 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B11)
B11.B10.B9.B8.B4.WORKSPACE_ID = "workspace-b12"
B11.B10.B9.B8.B4.TRACE_ID = "trace-b12-browser"

STATE = {"status": "awaiting_approval", "version": 1, "approved": []}


def operation() -> dict[str, object]:
    return {
        "operation_id": "sync-operation-b12", "tenant_id": "tenant-b12", "workspace_id": "workspace-b12",
        "actor_id": "actor-b12", "target_area": "cloud_sync", "state": STATE["status"], "version": STATE["version"],
        "manifest_digest": "a" * 64, "item_ids": ["item-source-b12", "item-output-b12"],
        "approved_item_ids": STATE["approved"], "completed_item_ids": [], "batches": [], "conflicts": [],
        "target_versions": [], "reindex_state": None, "source_mutations": 0, "overwrite_count": 0,
    }


class Handler(B11.Handler):
    server_version = "DaonB12Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/v1/workspaces/workspace-b12/sync-operations":
            self._send_json(B11.B10.B9.B8.B4.envelope({"operations": [operation()]}))
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/v1/sync-operations/sync-operation-b12/approve":
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.headers.get("If-Match") != f'"sync:sync-operation-b12:{STATE["version"]}"':
                self.send_error(409)
                return
            if body.get("step_up_authorization_id") != "step-up-b4-authorization":
                self.send_error(403)
                return
            STATE["approved"] = body.get("approved_item_ids", [])
            STATE["status"] = "approved"
            STATE["version"] = 2
            self._send_json(B11.B10.B9.B8.B4.envelope(operation()))
            print("SYNC_APPROVED=" + json.dumps(STATE["approved"], sort_keys=True), flush=True)
            return
        super().do_POST()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18492
    print(f"READY {port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
