from __future__ import annotations

import importlib.util
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MODULE_PATH = Path(__file__).with_name("foundation-b4-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b4_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B8_BROWSER_FIXTURE_UNAVAILABLE")
B4 = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(B4)
B4.WORKSPACE_ID = "workspace-b8"; B4.TRACE_ID = "trace-b8-browser"
B4.STATE.clear(); B4.STATE.update(generated=False, version=1, status="draft", review=None, approval_request=None, approval=None, output_type="business_draft")


def draft_content() -> dict[str, object]:
    return {"template_id": "letter", "review_state": "draft", "sections": [
        {"title": "요약", "body": "Daon 승인 지식과 원본 PDF를 결합한 업무 요약", "evidence": ["citation-b4-daon page 1", "citation-b4-raw page 2"]},
        {"title": "조치", "body": "근거를 검토하고 업무 문서를 확정한다.", "evidence": ["citation-b4-raw page 2"]},
    ], "warnings": [], "lineage": {"request_id": "generation-b8"}}


def version_projection(version: int) -> dict[str, object]:
    is_second = version == 2
    return {"output_version_id": f"output-version-b8-{version}", "content_version": version, "previous_version_id": "output-version-b8-1" if is_second else None,
            "status": B4.STATE["status"], "content": draft_content(), "revision_type": "user_edit" if is_second else "initial",
            "change_reason": "문서 의견 반영" if is_second else "initial_generation", "settings_snapshot_id": "settings-b8", "citations": B4.citations(),
            "review_request_id": B4.STATE["review"], "approval_request_id": B4.STATE["approval_request"], "approval_id": B4.STATE["approval"],
            "delivery_id": None, "knowledge_registration_id": None, "output_format": "docx"}


def output_projection() -> dict[str, object]:
    version = int(B4.STATE["version"]); projection = version_projection(version)
    return {"studio_output_id": "output-b4", "output_version_id": projection["output_version_id"], "output_type": "business_draft",
            "title": "혼합 지식 업무 문서 초안", "status": B4.STATE["status"], "content": projection["content"], "content_version": version,
            "settings_snapshot_id": "settings-b8", "source_count": 2, "citations": B4.citations(), "output_format": "docx",
            "review_request_id": B4.STATE["review"], "approval_request_id": B4.STATE["approval_request"], "approval_id": B4.STATE["approval"]}


B4.version_projection = version_projection; B4.output_projection = output_projection


class Handler(B4.Handler):
    server_version = "DaonB8Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path.endswith("/exports/docx"):
            content = b"PK\x03\x04Daon-B8-business-draft"
            self.send_response(200); self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", 'attachment; filename="daon-b8-draft.docx"'); self.send_header("Content-Length", str(len(content)))
            self.end_headers(); self.wfile.write(content); return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18488
    print(f"READY {port}", flush=True); ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
