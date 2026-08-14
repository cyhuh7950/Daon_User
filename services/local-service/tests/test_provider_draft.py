from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from daon_user_local_service.knowledge_context import (
    KnowledgeContextItem,
    KnowledgeContextMode,
    KnowledgeContextSnapshot,
    OfflineStudioError,
)


def test_product_model_boundary_has_no_arbitrary_executable_or_subprocess() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "daon_user_local_service"
        / "managed_local_draft.py"
    ).read_text(encoding="utf-8")

    assert "subprocess" not in source
    assert "executable_path" not in source
    assert "artifact_id" not in source
    assert '"local_runtime"' not in source


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object, str | None]] = []
        self.external_calls = 0

    def get_json(
        self, *, url: str, timeout_seconds: float, max_response_bytes: int
    ) -> dict[str, object]:
        self.calls.append(("GET", url, max_response_bytes, None))
        return {
            "models": [
                {"name": "qwen3:8b", "digest": "sha256:" + "a" * 64},
                {"name": "embed-only:latest", "digest": "sha256:" + "b" * 64},
                {"name": "qwen3:cloud", "digest": "sha256:" + "c" * 64},
                {
                    "name": "remote-named:latest",
                    "digest": "sha256:" + "d" * 64,
                    "remote_host": "https://ollama.example",
                },
            ]
        }

    def post_json(
        self,
        *,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
        api_key: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("POST", url, payload, api_key))
        if url.endswith("/api/show"):
            capabilities = [] if payload["model"] == "embed-only:latest" else ["completion"]
            return {"capabilities": capabilities}
        content = json.dumps(
            {
                "schema_version": 1,
                "sections": [
                    {
                        "title": "Summary",
                        "body": "Policy alpha is effective.",
                        "citation_ids": ["knowledge-1"],
                        "unverified": False,
                    }
                ],
            },
            separators=(",", ":"),
        )
        if "api.groq.com" in url or "api.upstage.ai" in url:
            self.external_calls += 1
            return {"choices": [{"message": {"content": content}}], "usage": {}}
        return {"message": {"content": content}}


def _context() -> KnowledgeContextSnapshot:
    return KnowledgeContextSnapshot(
        "scope-1",
        "33333333-3333-4333-8333-333333333333",
        KnowledgeContextMode.DAON_PRIORITY,
        (
            KnowledgeContextItem(
                "knowledge-1",
                "daon_knowledge",
                "daon3",
                "output-v1",
                "d" * 64,
                "registered",
                "approved",
                1.0,
                producer_version="3.0.0",
                registration_id="registration-1",
                review_state="approved",
                effective_at="2026-08-13T00:00:00Z",
                expires_at="2026-08-15T00:00:00Z",
                selection_reason="registered_knowledge_priority",
            ),
        ),
        "scope-v1",
        "weights-v1",
        (),
        "e" * 64,
    )


def _descriptor(
    *, deployment_id: str, model_id: str, model_digest: str
):
    from daon_user_local_service.provider_draft import ProviderModelDescriptor

    return ProviderModelDescriptor(
        provider_code="OLLAMA",
        provider_kind="server_internal",
        profile_id="provider-ollama",
        deployment_id=deployment_id,
        model_id=model_id,
        model_digest=model_digest,
        deployment_digest="f" * 64,
        binding_version=3,
    )


def test_ollama_catalog_lists_only_exact_installed_completion_models() -> None:
    from daon_user_local_service.provider_draft import OllamaModelCatalog

    transport = RecordingTransport()
    catalog = OllamaModelCatalog(
        base_url="http://127.0.0.1:11434",
        transport=transport,
        descriptors={
            "deployment-qwen": _descriptor(
                deployment_id="deployment-qwen",
                model_id="qwen3:8b",
                model_digest="sha256:" + "a" * 64,
            ),
            "deployment-embed": _descriptor(
                deployment_id="deployment-embed",
                model_id="embed-only:latest",
                model_digest="sha256:" + "b" * 64,
            ),
            "deployment-cloud": _descriptor(
                deployment_id="deployment-cloud",
                model_id="qwen3:cloud",
                model_digest="sha256:" + "c" * 64,
            ),
            "deployment-remote": _descriptor(
                deployment_id="deployment-remote",
                model_id="remote-named:latest",
                model_digest="sha256:" + "d" * 64,
            ),
        },
    )

    eligible = catalog.list_eligible(workspace_id=_context().workspace_id)

    assert tuple(item.deployment_id for item in eligible) == ("deployment-qwen",)
    selected = catalog.select(
        workspace_id=_context().workspace_id,
        deployment_id="deployment-qwen",
        temperature=0.1,
        max_output_tokens=256,
    )
    assert (selected.provider_code, selected.provider_kind) == ("OLLAMA", "server_internal")
    assert selected.model_digest == "sha256:" + "a" * 64
    assert [call[1] for call in transport.calls].count(
        "http://127.0.0.1:11434/api/tags"
    ) >= 2
    assert all("qwen3:cloud" not in item.model_id for item in eligible)
    assert all("remote-named" not in item.model_id for item in eligible)


@pytest.mark.parametrize(
    "base_url",
    (
        "http://192.0.2.10:11434",
        "https://example.invalid",
        "http://user@127.0.0.1:11434",
        "http://127.0.0.1:11434/path",
    ),
)
def test_ollama_remote_or_unsafe_endpoint_is_rejected(base_url: str) -> None:
    from daon_user_local_service.provider_draft import OllamaModelCatalog

    with pytest.raises(OfflineStudioError, match="OLLAMA_ENDPOINT_INVALID"):
        OllamaModelCatalog(base_url=base_url, transport=RecordingTransport(), descriptors={})


def test_ollama_chat_uses_schema_stream_false_and_never_calls_external_provider() -> None:
    from daon_user_local_service.provider_draft import (
        OllamaDraftGenerationAdapter,
        OllamaModelCatalog,
    )

    transport = RecordingTransport()
    descriptor = _descriptor(
        deployment_id="deployment-qwen",
        model_id="qwen3:8b",
        model_digest="sha256:" + "a" * 64,
    )
    catalog = OllamaModelCatalog(
        base_url="http://localhost:11434",
        transport=transport,
        descriptors={descriptor.deployment_id: descriptor},
    )
    selection = catalog.select(
        workspace_id=_context().workspace_id,
        deployment_id=descriptor.deployment_id,
        temperature=0,
        max_output_tokens=128,
    )

    result = OllamaDraftGenerationAdapter(
        base_url="http://localhost:11434",
        transport=transport,
        catalog=catalog,
    ).generate(
        selection=selection,
        context=_context(),
        request={
            "purpose": "draft",
            "evidence": [
                {"item_id": "knowledge-1", "text": "Policy alpha is effective."}
            ],
        },
    )

    chat = next(call for call in transport.calls if call[1].endswith("/api/chat"))
    payload = chat[2]
    assert isinstance(payload, dict)
    assert payload["stream"] is False
    assert payload["model"] == "qwen3:8b"
    assert isinstance(payload["format"], dict)
    messages = cast(list[dict[str, str]], payload["messages"])
    supplied = json.loads(messages[-1]["content"])
    context_item = supplied["context"]["items"][0]
    assert context_item == {
        "item_id": "knowledge-1",
        "origin": "daon_knowledge",
        "producer": "daon3",
        "producer_version": "3.0.0",
        "registration_id": "registration-1",
        "source_id": None,
        "version_id": "output-v1",
        "index_version_id": None,
        "evidence_span_ids": [],
        "digest": "d" * 64,
        "authority": "registered",
        "quality_state": "approved",
        "review_state": "approved",
        "conflict_state": "none",
        "unverified": False,
    }
    assert cast(Any, result["sections"])[0]["citation_ids"] == ["knowledge-1"]
    assert transport.external_calls == 0


@pytest.mark.parametrize(
    ("adapter_name", "provider_code", "base_url", "expected_url"),
    (
        (
            "GroqDraftGenerationAdapter",
            "GROQ",
            "https://api.groq.com/openai/v1",
            "https://api.groq.com/openai/v1/chat/completions",
        ),
        (
            "UpstageDraftGenerationAdapter",
            "UPSTAGE",
            "https://api.upstage.ai/v1",
            "https://api.upstage.ai/v1/chat/completions",
        ),
    ),
)
def test_external_adapters_share_schema_and_grounding_validator(
    adapter_name: str, provider_code: str, base_url: str, expected_url: str
) -> None:
    import daon_user_local_service.provider_draft as provider

    transport = RecordingTransport()
    selection = provider.ModelSelectionSnapshot(
        provider_code=provider_code,
        provider_kind="external_api",
        profile_id=f"provider-{provider_code.lower()}",
        deployment_id=f"deployment-{provider_code.lower()}",
        model_id="approved-structured-model",
        model_digest="sha256:" + "a" * 64,
        binding_version=2,
        deployment_digest="b" * 64,
        temperature=0,
        max_output_tokens=128,
        output_schema_digest=provider.DRAFT_OUTPUT_SCHEMA_DIGEST,
    )
    adapter = getattr(provider, adapter_name)(
        base_url=base_url,
        api_key="server-memory-only-secret",
        transport=transport,
    )

    result = adapter.generate(
        selection=selection,
        context=_context(),
        request={
            "purpose": "draft",
            "evidence": [
                {"item_id": "knowledge-1", "text": "Policy alpha is effective."}
            ],
        },
    )

    call = next(item for item in transport.calls if item[1] == expected_url)
    assert call[3] == "server-memory-only-secret"
    assert cast(Any, result["sections"])[0]["citation_ids"] == ["knowledge-1"]
    assert "server-memory-only-secret" not in repr(adapter)


def test_grounding_rejects_unknown_citation_without_fallback() -> None:
    from daon_user_local_service.provider_draft import validate_draft_output

    with pytest.raises(OfflineStudioError, match="PROVIDER_GROUNDING_INVALID"):
        validate_draft_output(
            {
                "schema_version": 1,
                "sections": [
                    {
                        "title": "Summary",
                        "body": "Unsupported",
                        "citation_ids": ["unknown"],
                        "unverified": False,
                    }
                ],
            },
            context=_context(),
            request={"evidence": []},
        )


def test_grounding_rejects_valid_context_citation_when_evidence_is_empty() -> None:
    from daon_user_local_service.provider_draft import validate_draft_output

    with pytest.raises(OfflineStudioError, match="PROVIDER_GROUNDING_INVALID"):
        validate_draft_output(
            {
                "schema_version": 1,
                "sections": [
                    {
                        "title": "Summary",
                        "body": "Policy alpha is effective.",
                        "citation_ids": ["knowledge-1"],
                        "unverified": False,
                    }
                ],
            },
            context=_context(),
            request={"evidence": []},
        )


@pytest.mark.skipif(
    os.environ.get("DAON_ACTUAL_OLLAMA") != "1",
    reason="explicit actual Ollama gate only",
)
def test_actual_installed_ollama_model_generates_grounded_draft_once() -> None:
    from daon_user_local_service.provider_draft import (
        HttpxProviderJsonTransport,
        OllamaDraftGenerationAdapter,
        OllamaModelCatalog,
    )

    model_id = os.environ.get("DAON_ACTUAL_OLLAMA_MODEL", "")
    model_digest = os.environ.get("DAON_ACTUAL_OLLAMA_DIGEST", "")
    if not model_id or not model_digest:
        pytest.fail("ACTUAL_OLLAMA_SELECTION_MISSING")
    transport = HttpxProviderJsonTransport()
    descriptor = _descriptor(
        deployment_id="deployment-actual-ollama",
        model_id=model_id,
        model_digest=model_digest,
    )
    catalog = OllamaModelCatalog(
        base_url="http://127.0.0.1:11434",
        transport=transport,
        descriptors={descriptor.deployment_id: descriptor},
    )
    selection = catalog.select(
        workspace_id=_context().workspace_id,
        deployment_id=descriptor.deployment_id,
        temperature=0,
        max_output_tokens=64,
    )

    result = OllamaDraftGenerationAdapter(
        base_url="http://127.0.0.1:11434",
        transport=transport,
        catalog=catalog,
    ).generate(
        selection=selection,
        context=_context(),
        request={
            "purpose": "Write one concise policy sentence.",
            "evidence": [{
                "item_id": "knowledge-1",
                "text": "Policy alpha is effective.",
            }],
        },
        timeout_seconds=90,
    )

    assert cast(Any, result["sections"])[0]["citation_ids"] == ["knowledge-1"]
    assert selection.model_id == model_id
    assert selection.model_digest == model_digest
