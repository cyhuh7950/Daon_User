from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


WORKSPACE_ID = "workspace-b4"
TRACE_ID = "trace-b4-browser"
STATE = {"generated": False, "version": 1, "status": "draft", "review": None, "approval_request": None, "approval": None}


def envelope(data: dict[str, object], *, replayed: bool = False) -> bytes:
    meta: dict[str, object] = {"trace_id": TRACE_ID, "workspace_id": WORKSPACE_ID}
    if replayed:
        meta["replayed"] = False
    return json.dumps({"data": data, "meta": meta}, ensure_ascii=False).encode("utf-8")


def citations() -> list[dict[str, object]]:
    return [
        {
            "citation_id": "citation-b4-daon", "source_version_id": "version-daon-b4",
            "evidence_span_id": "span-daon-b4", "origin": "daon_knowledge",
            "locator": {"kind": "section", "value": "승인 지식 요약"},
        },
        {
            "citation_id": "citation-b4-raw", "source_version_id": "version-raw-b4",
            "evidence_span_id": "span-raw-b4", "origin": "raw_source",
            "locator": {"kind": "page", "value": "2"},
        },
    ]


def version_projection(version: int) -> dict[str, object]:
    is_second = version == 2
    return {
        "output_version_id": f"output-version-b4-{version}", "content_version": version,
        "previous_version_id": "output-version-b4-1" if is_second else None,
        "status": STATE["status"],
        "content": {"title": "혼합 지식 근거 보고서", "body": "검토 의견을 반영한 최종 보고서" if is_second else "Daon 승인 지식과 원본 PDF를 결합한 근거 보고서"},
        "revision_type": "user_edit" if is_second else "initial",
        "change_reason": "검토 의견 반영" if is_second else "최초 생성",
        "settings_snapshot_id": "settings-b4", "citations": citations(),
        "review_request_id": STATE["review"], "approval_request_id": STATE["approval_request"],
        "approval_id": STATE["approval"], "delivery_id": None, "knowledge_registration_id": None,
        "output_format": "pdf",
    }


def output_projection() -> dict[str, object]:
    version = int(STATE["version"])
    projection = version_projection(version)
    return {
        "studio_output_id": "output-b4", "output_version_id": projection["output_version_id"],
        "output_type": "evidence_report", "title": "혼합 지식 근거 보고서",
        "status": STATE["status"], "content": projection["content"],
        "content_version": version, "settings_snapshot_id": "settings-b4",
        "source_count": 2, "citations": citations(), "output_format": "pdf",
        "review_request_id": STATE["review"], "approval_request_id": STATE["approval_request"],
        "approval_id": STATE["approval"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DaonB4Evidence/1"

    def log_message(self, format: str, *args: object) -> None:
        print(format % args, flush=True)

    def _send_json(self, data: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/sources":
            self._send_json(envelope({"sources": [{
                "source_id": "source-raw-b4", "source_version_id": "version-raw-b4",
                "filename": "market-reference.pdf", "source_state": "ready",
                "processing_state": "completed", "job_state": "completed",
            }]}))
            return
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-packages":
            self._send_json(envelope({"items": [{
                "package_id": "knowledge-package-b4", "producer": "daon3",
                "producer_version": "3.0.0", "knowledge_registration_id": "registration-b4",
                "output_version_id": "approved-version-b4", "authority": "approved",
                "registration_state": "registered", "review_state": "approved",
                "digest_sha256": "b" * 64, "byte_size": 4096,
                "content_type": "application/vnd.daon.knowledge+json",
                "effective_at": "2026-01-01T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z",
            }]}))
            return
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/studio/outputs":
            self._send_json(envelope({"outputs": []}))
            return
        if path == "/api/v1/studio-outputs":
            self._send_json(envelope({"outputs": [output_projection()] if STATE["generated"] else [], "studio_locks": []}))
            return
        if path == "/api/v1/studio-outputs/output-b4/versions":
            versions = [version_projection(2), version_projection(1)] if STATE["version"] == 2 else [version_projection(1)]
            self._send_json(envelope({"output_id": "output-b4", "versions": versions}))
            return
        if path.endswith("/exports/pdf"):
            content = b"%PDF-1.4\n% Daon B4 grounded report\n%%EOF\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="daon-b4-report.pdf"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if path.endswith("/citations/citation-b4-daon/content"):
            content = "Daon 승인 지식 본문".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Citation-Locator-Kind", "section")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self._send_json(json.dumps({"error": {"code": "RESOURCE_UNAVAILABLE"}}).encode(), 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        request = json.loads(raw or b"{}")
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/questions":
            print("QUESTION_RESOURCES=" + json.dumps(request.get("resources", []), ensure_ascii=False, sort_keys=True), flush=True)
            self._send_json(envelope({
                "run_id": "run-b4", "run_result_id": "result-b4",
                "answer": "Daon 승인 지식과 원본 PDF를 함께 분석한 답변입니다.", "insufficient": False,
                "citations": [
                    {"citation_id": "citation-b4-daon", "source_id": "source-daon-b4", "source_version_id": "version-daon-b4", "evidence_span_id": "span-daon-b4", "page": 1, "origin": "daon_knowledge", "context_item_id": "knowledge-package-b4", "locator": {"kind": "section", "value": "승인 지식 요약"}},
                    {"citation_id": "citation-b4-raw", "source_id": "source-raw-b4", "source_version_id": "version-raw-b4", "evidence_span_id": "span-raw-b4", "page": 2, "origin": "raw_source", "context_item_id": "source-raw-b4", "locator": {"kind": "page", "value": "2"}},
                ],
            }))
            return
        if path == "/api/v1/studio-generation-requests":
            STATE.update(generated=True, version=1, status="draft", review=None, approval_request=None, approval=None)
            print("GENERATION_SOURCES=" + json.dumps(request.get("source_version_ids", []), sort_keys=True), flush=True)
            self._send_json(envelope(output_projection(), replayed=True), 201)
            return
        if path == "/api/v1/studio-outputs/output-b4/versions":
            STATE.update(version=2, status="draft", review=None, approval_request=None, approval=None)
            self._send_json(envelope(version_projection(2), replayed=True), 201)
            return
        if path == "/api/v1/reviews":
            STATE.update(review="review-b4", status="in_review")
            self._send_json(envelope({"record_id": "review-b4"}, replayed=True), 201)
            return
        if path == "/api/v1/approval-requests":
            STATE["approval_request"] = "approval-request-b4"
            self._send_json(envelope({"record_id": "approval-request-b4"}, replayed=True), 201)
            return
        if path == "/api/v1/session/step-up":
            self._send_json(envelope({"step_up_authorization": "step-up-b4-authorization"}, replayed=True), 201)
            return
        if path == "/api/v1/approvals":
            STATE.update(approval="approval-b4", status="approved")
            self._send_json(envelope({"record_id": "approval-b4"}, replayed=True), 201)
            return
        self._send_json(json.dumps({"error": {"code": "RESOURCE_UNAVAILABLE"}}).encode(), 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18484
    print(f"READY {port} {datetime.now(timezone.utc).isoformat()}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
