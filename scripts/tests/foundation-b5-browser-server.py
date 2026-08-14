from __future__ import annotations

import importlib.util
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


MODULE_PATH = Path(__file__).with_name("foundation-b4-browser-server.py")
SPEC = importlib.util.spec_from_file_location("foundation_b4_browser_server", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("B5_BROWSER_FIXTURE_UNAVAILABLE")
B4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B4)

B4.WORKSPACE_ID = "workspace-b5"
B4.TRACE_ID = "trace-b5-browser"
B4.STATE.clear()
B4.STATE.update(generated=False, version=1, status="draft", review=None, approval_request=None, approval=None, output_type="compliance_checklist")


def checklist_content() -> dict[str, object]:
    return {
        "items": [
            {"item_id": "check-source-authority", "judgement": "needs_review", "evidence": "citation-b4-daon page 1", "ruleset_id": "ruleset-v3", "action": "권위 검토"},
            {"item_id": "check-original-source", "judgement": "needs_review", "evidence": "citation-b4-raw page 2", "ruleset_id": "ruleset-v3", "action": "원문 대조"},
        ],
        "warnings": [],
        "lineage": {"ruleset_id": "ruleset-v3", "ruleset_version": "ruleset-v3", "request_id": "generation-b5", "model_id": "run-b4"},
    }


def version_projection(version: int) -> dict[str, object]:
    is_second = version == 2
    return {
        "output_version_id": f"output-version-b5-{version}", "content_version": version,
        "previous_version_id": "output-version-b5-1" if is_second else None,
        "status": B4.STATE["status"], "content": checklist_content(),
        "revision_type": "user_edit" if is_second else "initial",
        "change_reason": "점검 의견 반영" if is_second else "initial_generation",
        "settings_snapshot_id": "settings-b5", "citations": B4.citations(),
        "review_request_id": B4.STATE["review"], "approval_request_id": B4.STATE["approval_request"],
        "approval_id": B4.STATE["approval"], "delivery_id": None, "knowledge_registration_id": None,
        "output_format": "xlsx",
    }


def output_projection() -> dict[str, object]:
    version = int(B4.STATE["version"])
    projection = version_projection(version)
    return {
        "studio_output_id": "output-b4", "output_version_id": projection["output_version_id"],
        "output_type": "compliance_checklist", "title": "혼합 지식 제약·준수 점검표",
        "status": B4.STATE["status"], "content": projection["content"],
        "content_version": version, "settings_snapshot_id": "settings-b5",
        "source_count": 2, "citations": B4.citations(), "output_format": "xlsx",
        "review_request_id": B4.STATE["review"], "approval_request_id": B4.STATE["approval_request"],
        "approval_id": B4.STATE["approval"],
    }


B4.version_projection = version_projection
B4.output_projection = output_projection


class Handler(B4.Handler):
    server_version = "DaonB5Evidence/1"

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path.endswith("/exports/xlsx"):
            content = b"PK\x03\x04Daon-B5-compliance-checklist"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="daon-b5-checklist.xlsx"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18485
    print(f"READY {port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
