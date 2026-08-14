from __future__ import annotations

import importlib.util
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b10-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b10_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B11_BROWSER_FIXTURE_UNAVAILABLE")
B10 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B10)
B10.B9.B8.B4.WORKSPACE_ID = "workspace-b11"
B10.B9.B8.B4.TRACE_ID = "trace-b11-browser"

STATE = {
    "formats": {
        "evidence_report": "pdf", "compliance_checklist": "xlsx",
        "comparison_table": "xlsx", "knowledge_graph": "json", "business_draft": "docx",
    },
    "version": 0,
}


class Handler(B10.Handler):
    server_version = "DaonB11Evidence/1"

    def _settings(self) -> None:
        content = B10.B9.B8.B4.envelope({
            "workspace_id": "workspace-b11", "default_formats": STATE["formats"],
            "version_save_mode": "append_only", "version": STATE["version"],
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("ETag", f'"output-version-settings:workspace-b11:{STATE["version"]}"')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/api/v1/workspaces/workspace-b11/output-version-settings":
            self._settings()
            return
        super().do_GET()

    def do_PATCH(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/v1/workspaces/workspace-b11/output-version-settings":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if self.headers.get("If-Match") != f'"output-version-settings:workspace-b11:{STATE["version"]}"':
            self.send_error(409)
            return
        STATE["formats"] = body["default_formats"]
        STATE["version"] += 1
        self._settings()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18491
    print(f"READY {port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
