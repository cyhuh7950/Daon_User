from __future__ import annotations

import importlib.util
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b4-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b4_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B7_BROWSER_FIXTURE_UNAVAILABLE")
B4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B4)
B4.WORKSPACE_ID = "workspace-b7"
B4.TRACE_ID = "trace-b7-browser"
B4.STATE.clear()
B4.STATE.update(generated=False, version=1, status="draft", review=None, approval_request=None, approval=None, output_type="knowledge_map")


def map_content() -> dict[str, object]:
    return {
        "nodes": [
            {"id": "node-daon", "label": "Daon 승인 지식", "confidence": "verified", "evidence": "citation-b4-daon section"},
            {"id": "node-raw", "label": "원본 PDF", "confidence": "verified", "evidence": "citation-b4-raw page 2"},
        ],
        "edges": [{"id": "edge-grounding", "source": "node-daon", "target": "node-raw", "condition": "근거 순서"}],
    }


def version_projection(version: int) -> dict[str, object]:
    is_second = version == 2
    return {
        "output_version_id": f"output-version-b7-{version}", "content_version": version,
        "previous_version_id": "output-version-b7-1" if is_second else None,
        "status": B4.STATE["status"], "content": map_content(),
        "revision_type": "user_edit" if is_second else "initial",
        "change_reason": "구조 의견 반영" if is_second else "initial_generation",
        "settings_snapshot_id": "settings-b7", "citations": B4.citations(),
        "review_request_id": B4.STATE["review"], "approval_request_id": B4.STATE["approval_request"],
        "approval_id": B4.STATE["approval"], "delivery_id": None, "knowledge_registration_id": None,
        "output_format": "json",
    }


def output_projection() -> dict[str, object]:
    version = int(B4.STATE["version"]); projection = version_projection(version)
    return {
        "studio_output_id": "output-b4", "output_version_id": projection["output_version_id"],
        "output_type": "knowledge_map", "title": "혼합 지식 구조도",
        "status": B4.STATE["status"], "content": projection["content"], "content_version": version,
        "settings_snapshot_id": "settings-b7", "source_count": 2, "citations": B4.citations(), "output_format": "json",
        "review_request_id": B4.STATE["review"], "approval_request_id": B4.STATE["approval_request"], "approval_id": B4.STATE["approval"],
    }


B4.version_projection = version_projection
B4.output_projection = output_projection


class Handler(B4.Handler):
    server_version = "DaonB7Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path.endswith("/exports/json"):
            content = b'{"title":"Daon B7 knowledge map","nodes":2,"edges":1}'
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", 'attachment; filename="daon-b7-map.json"')
            self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content); return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18487
    print(f"READY {port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
