from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


WORKSPACE_ID = "workspace-b3"
TRACE_ID = "trace-b3-browser"


def envelope(data: dict[str, object]) -> bytes:
    return json.dumps(
        {"data": data, "meta": {"trace_id": TRACE_ID, "workspace_id": WORKSPACE_ID}},
        ensure_ascii=False,
    ).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "DaonB3Evidence/1"

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
                "source_id": "source-raw", "source_version_id": "version-raw",
                "filename": "reference.pdf", "source_state": "ready",
                "processing_state": "completed", "job_state": "completed",
            }]}))
            return
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/knowledge-packages":
            self._send_json(envelope({"items": [{
                "package_id": "knowledge-package-daon3", "producer": "daon3",
                "producer_version": "3.0.0", "knowledge_registration_id": "registration-daon3",
                "output_version_id": "output-version-daon3", "authority": "approved",
                "registration_state": "registered", "review_state": "approved",
                "digest_sha256": "a" * 64, "byte_size": 4096,
                "content_type": "application/vnd.daon.knowledge+json",
                "effective_at": "2026-01-01T00:00:00Z", "expires_at": "2027-01-01T00:00:00Z",
            }]}))
            return
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/studio/outputs":
            self._send_json(envelope({"outputs": []}))
            return
        if path == "/api/v1/studio-outputs":
            self._send_json(envelope({"outputs": [], "studio_locks": []}))
            return
        if path.endswith("/citations/citation-daon/content"):
            content = "Daon이 생성하고 승인한 일반 텍스트 지식".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'inline; filename="approved-knowledge.txt"')
            self.send_header("X-Citation-Locator-Kind", "section")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self._send_json(json.dumps({"error": {"code": "RESOURCE_UNAVAILABLE"}}).encode(), 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if path == f"/api/v1/workspaces/{WORKSPACE_ID}/questions":
            request = json.loads(body)
            print("QUESTION_BODY=" + json.dumps(request, ensure_ascii=False, sort_keys=True), flush=True)
            self._send_json(envelope({
                "run_id": "run-b3", "run_result_id": "result-b3",
                "answer": "Daon 승인 지식과 Raw Source를 함께 사용한 답변입니다.",
                "insufficient": False,
                "citations": [
                    {
                        "citation_id": "citation-daon", "source_id": "source-daon",
                        "source_version_id": "version-daon", "evidence_span_id": "span-daon",
                        "page": 1, "origin": "daon_knowledge",
                        "context_item_id": "knowledge-package-daon3",
                        "locator": {"kind": "section", "value": "span-daon"},
                    },
                    {
                        "citation_id": "citation-raw", "source_id": "source-raw",
                        "source_version_id": "version-raw", "evidence_span_id": "span-raw",
                        "page": 2, "origin": "raw_source", "context_item_id": "source-raw",
                        "locator": {"kind": "page", "value": "2"},
                    },
                ],
            }))
            return
        self._send_json(json.dumps({"error": {"code": "RESOURCE_UNAVAILABLE"}}).encode(), 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18483
    print(f"READY {port} {datetime.now(timezone.utc).isoformat()}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
