from __future__ import annotations

import importlib.util
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b8-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b8_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B9_BROWSER_FIXTURE_UNAVAILABLE")
B8 = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(B8)
B8.B4.WORKSPACE_ID = "workspace-b9"; B8.B4.TRACE_ID = "trace-b9-browser"
B8.B4.STATE.clear(); B8.B4.STATE.update(generated=True, version=2, status="approved", review="review-b9", approval_request="approval-request-b9", approval="approval-b9", output_type="business_draft")


def library_outputs() -> list[dict[str, object]]:
    rows = [
        ("output-b4", "evidence_report", "근거 기반 통합 보고서", "pdf"),
        ("output-b9-check", "compliance_checklist", "제약·준수 점검표", "xlsx"),
        ("output-b9-table", "comparison_table", "비교·데이터 표", "xlsx"),
        ("output-b9-map", "knowledge_map", "지식 구조도", "json"),
        ("output-b9-draft", "business_draft", "업무 문서 초안", "docx"),
    ]
    return [{
        "studio_output_id": output_id, "output_version_id": f"version-{output_id}", "output_type": output_type,
        "title": title, "status": "approved", "content_version": 2, "source_count": 2,
        "output_format": output_format, "citations": B8.B4.citations(), "content": {"body": title},
        "review_request_id": "review-b9", "approval_request_id": "approval-request-b9", "approval_id": "approval-b9",
    } for output_id, output_type, title, output_format in rows]


def version_for(output_id: str) -> dict[str, object]:
    output = next(item for item in library_outputs() if item["studio_output_id"] == output_id)
    return {
        "output_version_id": output["output_version_id"], "content_version": 2,
        "previous_version_id": f"version-{output_id}-1", "status": output["status"], "content": output["content"],
        "revision_type": "user_edit", "change_reason": "검토 반영", "settings_snapshot_id": "settings-b9",
        "citations": output["citations"], "review_request_id": output["review_request_id"],
        "approval_request_id": output["approval_request_id"], "approval_id": output["approval_id"],
        "delivery_id": None, "knowledge_registration_id": None, "output_format": output["output_format"],
    }


class Handler(B8.Handler):
    server_version = "DaonB9Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/v1/studio-outputs":
            self._send_json(B8.B4.envelope({"outputs": library_outputs(), "studio_locks": []})); return
        if path.startswith("/api/v1/studio-outputs/") and path.endswith("/versions"):
            output_id = path.split("/")[4]
            self._send_json(B8.B4.envelope({"output_id": output_id, "versions": [version_for(output_id)]})); return
        if "/exports/" in path:
            format_name = path.rsplit("/", 1)[-1]
            content = b"%PDF-1.4\n%%EOF\n" if format_name == "pdf" else b"PK\x03\x04Daon-B9-library"
            self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="daon-b9.{format_name}"')
            self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content); return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18489
    print(f"READY {port}", flush=True); ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
