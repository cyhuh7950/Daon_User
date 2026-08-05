from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from daon_user_api.document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingRequest,
    ServerProviderCredentialResolver,
    UpstageDocumentUnderstandingAdapter,
    resolve_document_model_selection,
)
from daon_user_api.provider_settings import (
    ModelDeploymentView,
    ProviderProfileView,
    ProviderSettingsSnapshot,
)


PDF = b"%PDF-1.4\n% synthetic contract fixture\n%%EOF\n"


def provider_snapshot() -> ProviderSettingsSnapshot:
    return ProviderSettingsSnapshot(
        workspace_id="workspace-cp3",
        profiles=(ProviderProfileView(
            "provider-upstage", "UPSTAGE", "external_api",
            "https://api.upstage.ai/v1", True, True, 2,
        ),),
        deployments=(
            ModelDeploymentView(
                "deployment-understanding", "provider-upstage", "UPSTAGE",
                "information-extract", ("vision",), True, True, 3,
            ),
            ModelDeploymentView(
                "deployment-parser", "provider-upstage", "UPSTAGE",
                "document-parse", ("document_parser",), True, True, 4,
            ),
        ),
        role_bindings={
            "vision": "deployment-understanding",
            "document_parser": "deployment-parser",
        },
        binding_version=5,
    )


class RecordingTransport:
    def __init__(self, *, semantic_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.semantic_error = semantic_error

    def post_json(self, *, url: str, api_key: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        self.calls.append(("semantic", url, payload))
        if self.semantic_error is not None:
            raise self.semantic_error
        return {
            "model": "information-extract-260610",
            "choices": [{"message": {"content": (
                '{"title":"Daon CP3","summary":"Vision first. Parser validates later.",'
                '"key_facts":["Vision first","Parser validates later"]}'
            )}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
        }

    def post_multipart(self, *, url: str, api_key: str, fields: dict[str, str], filename: str, content: bytes, timeout_seconds: float) -> dict[str, object]:
        self.calls.append(("parser", url, {"fields": fields, "filename": filename, "content": content}))
        return {
            "model": "document-parse-260630",
            "content": {
                "text": "Daon CP3 contract test. Vision first. Parser validates later.",
                "markdown": "Daon CP3 contract test. Vision first. Parser validates later.",
                "html": "<p>Daon CP3 contract test. Vision first. Parser validates later.</p>",
            },
            "elements": [{"page": 1, "category": "paragraph", "content": {"text": "Daon CP3 contract test."}}],
            "usage": {"pages": 1},
        }


class DocumentModelSelectionTests(unittest.TestCase):
    def test_selected_vision_and_parser_deployments_are_frozen_from_snapshot(self) -> None:
        selection = resolve_document_model_selection(provider_snapshot())

        self.assertEqual(selection.provider_code, "UPSTAGE")
        self.assertEqual(selection.semantic_deployment_id, "deployment-understanding")
        self.assertEqual(selection.semantic_model_id, "information-extract")
        self.assertEqual(selection.parser_deployment_id, "deployment-parser")
        self.assertEqual(selection.parser_model_id, "document-parse")
        self.assertEqual(selection.binding_version, 5)

    def test_missing_parser_binding_fails_closed(self) -> None:
        snapshot = provider_snapshot()
        snapshot.role_bindings.pop("document_parser")

        with self.assertRaisesRegex(DocumentUnderstandingError, "DOCUMENT_PARSER_MODEL_NOT_SELECTED"):
            resolve_document_model_selection(snapshot)


class UpstageDocumentUnderstandingAdapterTests(unittest.TestCase):
    def test_original_pdf_semantic_call_precedes_separate_parser_validation(self) -> None:
        transport = RecordingTransport()
        adapter = UpstageDocumentUnderstandingAdapter(transport=transport, api_key="up_test_secret")

        result = adapter.understand(
            DocumentUnderstandingRequest(
                source_id="source-cp3", source_version_id="source-version-cp3",
                filename="contract.pdf", content=PDF, trace_id="trace-cp3",
                prompt_version="understanding-prompt-v1", policy_version="policy-v1",
            ),
            resolve_document_model_selection(provider_snapshot()),
        )

        self.assertEqual([call[0] for call in transport.calls], ["semantic", "parser"])
        semantic_payload = transport.calls[0][2]
        image_url = semantic_payload["messages"][0]["content"][0]["image_url"]["url"]  # type: ignore[index]
        self.assertTrue(image_url.startswith("data:application/pdf;base64,"))
        self.assertNotIn("document-parse", str(semantic_payload))
        self.assertEqual(transport.calls[1][2]["content"], PDF)  # type: ignore[index]
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.substates, (
            "vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation",
        ))
        self.assertEqual(result.semantic.summary, "Vision first. Parser validates later.")
        self.assertEqual(result.parser.role, "validation_only")
        self.assertEqual(result.parser.pages, (1,))
        self.assertEqual(result.lineage["semantic_model_revision"], "information-extract-260610")
        self.assertEqual(result.lineage["parser_model_revision"], "document-parse-260630")
        self.assertEqual(result.lineage["parser_role"], "validation_only")

    def test_parser_is_never_called_when_semantic_understanding_fails(self) -> None:
        transport = RecordingTransport(
            semantic_error=DocumentUnderstandingError(
                "UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True,
            )
        )
        adapter = UpstageDocumentUnderstandingAdapter(transport=transport, api_key="up_test_secret")

        with self.assertRaisesRegex(DocumentUnderstandingError, "UNDERSTANDING_PROVIDER_UNAVAILABLE") as caught:
            adapter.understand(
                DocumentUnderstandingRequest(
                    source_id="source-cp3", source_version_id="source-version-cp3",
                    filename="contract.pdf", content=PDF, trace_id="trace-cp3",
                    prompt_version="understanding-prompt-v1", policy_version="policy-v1",
                ),
                resolve_document_model_selection(provider_snapshot()),
            )

        self.assertTrue(caught.exception.retryable)
        self.assertEqual([call[0] for call in transport.calls], ["semantic"])

    def test_secret_is_read_server_side_and_never_appears_in_errors(self) -> None:
        with patch.dict(os.environ, {"UPSTAGE_API_KEY": "up_never_expose"}, clear=False):
            secret = ServerProviderCredentialResolver().resolve("UPSTAGE")
        self.assertEqual(secret, "up_never_expose")

        adapter = UpstageDocumentUnderstandingAdapter(
            transport=RecordingTransport(), api_key=secret,
        )
        with self.assertRaises(DocumentUnderstandingError) as caught:
            adapter.understand(
                DocumentUnderstandingRequest(
                    source_id="source-cp3", source_version_id="source-version-cp3",
                    filename="not-pdf.pdf", content=b"not a pdf", trace_id="trace-cp3",
                    prompt_version="understanding-prompt-v1", policy_version="policy-v1",
                ),
                resolve_document_model_selection(provider_snapshot()),
            )
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(adapter))


if __name__ == "__main__":
    unittest.main()
