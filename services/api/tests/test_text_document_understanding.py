import json

import pytest

from daon_user_api.document_processing import DocumentProcessingContext, StoredSourceDocument
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.text_document_understanding import OllamaTextDocumentAdapter
from daon_user_api.workspace_model_defaults import ResolvedModel


class Transport:
    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    def post_json_no_auth(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps({
            "title": "Test source", "summary": "The source describes ORANGE-COMPASS-42.",
            "key_facts": self.facts,
        })}, "model": "llama3.2:3b"}


def _selection():
    return ResolvedModel(
        connection_id="ollama-public", provider_code="OLLAMA", model_id="llama3.2:3b",
        capability="text_generation", base_url="http://ollama.internal:11434",
        credential_version=0, default_version=1, catalog_version=1,
        provider_kind="local", routing_owner="provider", daon_fallback_allowed=True,
        _credential=None,
    )


def _document():
    return StoredSourceDocument(
        "source-txt", "source-version-txt", "report.txt",
        "ORANGE-COMPASS-42 is the current source marker.\n".encode(), "text/plain",
    )


def _context():
    return DocumentProcessingContext("tenant-t", "workspace-t", "actor-t", "trace-t", "policy-v1")


def test_txt_calls_selected_ollama_and_produces_verbatim_grounded_result():
    transport = Transport(["ORANGE-COMPASS-42 is the current source marker."])
    result = OllamaTextDocumentAdapter(transport).understand(_document(), _selection(), _context())
    assert result.status == "ready"
    assert result.semantic.key_facts == ("ORANGE-COMPASS-42 is the current source marker.",)
    assert result.parser.page_texts == ((1, "ORANGE-COMPASS-42 is the current source marker."),)
    assert result.lineage["provider_code"] == "OLLAMA"
    assert result.lineage["source_format"] == "txt"
    assert transport.calls[0]["url"] == "http://ollama.internal:11434/api/chat"
    assert "ORANGE-COMPASS-42" in json.dumps(transport.calls[0]["payload"])


@pytest.mark.parametrize("filename,mime,source_format", [
    ("report.md", "text/markdown", "md"),
    ("report.csv", "text/csv", "csv"),
])
def test_registered_utf8_text_formats_use_same_semantic_and_verbatim_contract(filename, mime, source_format):
    transport = Transport(["ORANGE-COMPASS-42 is the current source marker."])
    document = StoredSourceDocument(
        "source-text", "source-version-text", filename,
        b"ORANGE-COMPASS-42 is the current source marker.\n", mime,
    )
    result = OllamaTextDocumentAdapter(transport).understand(document, _selection(), _context())
    assert result.status == "ready"
    assert result.semantic.key_facts == ("ORANGE-COMPASS-42 is the current source marker.",)
    assert result.lineage["source_format"] == source_format
    assert result.parser.page_texts == ((1, "ORANGE-COMPASS-42 is the current source marker."),)
    assert len(transport.calls) == 1


def test_txt_hallucinated_fact_fails_before_ready():
    transport = Transport(["UNSUPPORTED-999 is approved."])
    with pytest.raises(DocumentUnderstandingError, match="TEXT_UNDERSTANDING_UNGROUNDED"):
        OllamaTextDocumentAdapter(transport).understand(_document(), _selection(), _context())


def test_txt_invalid_encoding_or_oversize_is_not_sent_to_model():
    transport = Transport(["safe"])
    for content in (b"\xff", b"a" * (32 * 1024 + 1)):
        document = StoredSourceDocument("source-txt", "source-version-txt", "report.txt", content, "text/plain")
        with pytest.raises(DocumentUnderstandingError):
            OllamaTextDocumentAdapter(transport).understand(document, _selection(), _context())
    assert transport.calls == []
