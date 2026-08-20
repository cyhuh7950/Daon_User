from __future__ import annotations

import unittest

from daon_user_api.studio_report import (
    StudioCitation,
    StudioOutputProjection,
    StudioReportContext,
    StudioReportCreateRequest,
    StudioReportError,
    StudioReportService,
)


class FakeRepository:
    def __init__(self) -> None:
        self.calls = []

    def create_report(self, context, request, idempotency_key):  # type: ignore[no-untyped-def]
        self.calls.append((context, request, idempotency_key))
        return StudioOutputProjection(
            "output-1", "output-version-1", "evidence_report", request.title,
            request.purpose, "draft", "근거 답변", request.run_id,
            request.run_result_id,
            (StudioCitation("citation-1", request.source_id, request.source_version_id, "span-1", 2),),
        ), False

    def list_outputs(self, context):  # type: ignore[no-untyped-def]
        return ()


class StudioReportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.service = StudioReportService(self.repository)
        self.context = StudioReportContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")

    def test_exact_request_creates_evidence_report_only(self) -> None:
        request = StudioReportCreateRequest(
            "source-1", "source-version-1", "run-1", "result-1", "승인 검토 보고서", "근거 기반 요약",
        )
        output, replayed = self.service.create(self.context, request, "report-1")
        self.assertEqual(output.output_type, "evidence_report")
        self.assertFalse(replayed)
        self.assertEqual(self.repository.calls[0][2], "report-1")

    def test_invalid_title_and_idempotency_fail_closed(self) -> None:
        with self.assertRaisesRegex(StudioReportError, "STUDIO_INPUT_INVALID"):
            StudioReportCreateRequest("source-1", "version-1", "run-1", "result-1", "", "목적")
        request = StudioReportCreateRequest("source-1", "version-1", "run-1", "result-1", "제목", "목적")
        with self.assertRaisesRegex(StudioReportError, "STUDIO_INPUT_INVALID"):
            self.service.create(self.context, request, "bad/key")


if __name__ == "__main__":
    unittest.main()
