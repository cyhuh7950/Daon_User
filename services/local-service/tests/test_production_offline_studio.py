from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import cast

from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.main import build_production_offline_studio
from daon_user_local_service.offline_studio import ConfirmSettingsInput, OfflineStudioService
from daon_user_local_service.raw_source import RawSourceService


WORKSPACE = "33333333-3333-4333-8333-333333333333"


class CatalogTransport:
    def __init__(self) -> None:
        self.chat_evidence: list[dict[str, str]] | None = None

    def get_json(self, **_kwargs: object) -> dict[str, object]:
        return {"models": [{"name": "qwen3:8b", "digest": "sha256:" + "a" * 64}]}

    def post_json(self, *, url: str, **_kwargs: object) -> dict[str, object]:
        if url.endswith("/api/show"):
            return {"capabilities": ["completion"]}
        if url.endswith("/api/chat"):
            payload = cast(dict[str, object], _kwargs["payload"])
            messages = cast(list[dict[str, str]], payload["messages"])
            supplied = json.loads(messages[-1]["content"])
            self.chat_evidence = cast(list[dict[str, str]], supplied["evidence"])
            citation_id = self.chat_evidence[0]["item_id"]
            unverified = citation_id.startswith("raw-source:")
            grounded_body = self.chat_evidence[0]["text"]
            return {"message": {"content": json.dumps({
                "schema_version": 1,
                "sections": [{
                    "title": "Summary",
                    "body": grounded_body,
                    "citation_ids": [citation_id],
                    "unverified": unverified,
                }],
            }, separators=(",", ":"))}}
        raise AssertionError("catalog listing must not generate")


def _deployments() -> str:
    return json.dumps([{
        "provider_code": "OLLAMA",
        "provider_kind": "server_internal",
        "profile_id": "provider-ollama",
        "deployment_id": "deployment-qwen",
        "model_id": "qwen3:8b",
        "model_digest": "sha256:" + "a" * 64,
        "deployment_digest": "b" * 64,
        "binding_version": 3,
    }], separators=(",", ":"))


def _persist_provider_settings(service: OfflineStudioService) -> None:
    service.import_provider_settings(
        workspace_id=WORKSPACE,
        profiles=({
            "profile_id": "provider-ollama",
            "provider_code": "OLLAMA",
            "provider_kind": "server_internal",
            "base_url": "http://ollama:11434",
            "active": True,
            "version": 4,
        },),
        deployments=({
            "deployment_id": "deployment-qwen",
            "profile_id": "provider-ollama",
            "provider_code": "OLLAMA",
            "model_id": "qwen3:8b",
            "roles": ["text"],
            "active": True,
            "selected": True,
            "version": 7,
        },),
        policy_version="provider-settings-v7",
    )

def test_product_composition_builds_ollama_only_studio_without_fixture(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "production", bytes(range(32)))
    service = build_production_offline_studio(
        store,
        environment={
            "DAON_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            "DAON_OLLAMA_DEPLOYMENTS_JSON": _deployments(),
        },
        transport=CatalogTransport(),
    )
    _persist_provider_settings(service)

    assert [model.deployment_id for model in service.list_models(workspace_id=WORKSPACE)] == [
        "deployment-qwen"
    ]
    source = (Path(__file__).parents[1] / "src/daon_user_local_service/main.py").read_text(encoding="utf-8")
    assert "offline_studio=offline_studio" in source
    assert "GroqDraftGenerationAdapter" not in source
    assert "UpstageDraftGenerationAdapter" not in source
    store.close()


def test_product_composition_without_deployment_is_safe_empty_catalog(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "empty", bytes(range(32)))
    service = build_production_offline_studio(
        store,
        environment={"DAON_OLLAMA_BASE_URL": "http://127.0.0.1:11434"},
        transport=CatalogTransport(),
    )
    assert service.list_models(workspace_id=WORKSPACE) == ()
    store.close()


def test_product_generation_uses_encrypted_knowledge_package_text(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "grounded", bytes(range(32)))
    canonical_package = json.dumps(
        {
            "knowledge": [{
                "citation_id": "citation-1",
                "text": "Policy alpha is effective.",
            }],
            "schema_version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest = {
        "workspace_id": WORKSPACE,
        "copy_id": "copy-1",
        "package_id": "package-1",
        "producer_product": "daon3",
        "producer_version": "3.0.0",
        "knowledge_registration_id": "registration-1",
        "output_version_id": "output-version-1",
        "authority": "approved",
        "registration_state": "registered",
        "review_state": "approved",
        "effective_at": "2026-08-13T03:33:20Z",
        "expires_at": "2033-05-18T03:33:20Z",
        "schema_version": 1,
        "content_digest_sha256": hashlib.sha256(canonical_package).hexdigest(),
    }
    manifest_digest = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    store.import_knowledge_copy(
        manifest=manifest,
        manifest_digest_sha256=manifest_digest,
        canonical_package=canonical_package,
        idempotency_key="knowledge-copy-import-1",
    )
    transport = CatalogTransport()
    service = build_production_offline_studio(
        store,
        environment={
            "DAON_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            "DAON_OLLAMA_DEPLOYMENTS_JSON": _deployments(),
        },
        transport=transport,
    )
    _persist_provider_settings(service)
    context = service.prepare_context(
        workspace_id=WORKSPACE,
        mode="daon_priority",
        daon_knowledge_ids=("copy-1",),
        raw_source_version_ids=(),
        idempotency_key="context-1",
    )
    assert context.items[0].producer_version == "3.0.0"
    assert context.items[0].registration_id == "registration-1"
    assert context.items[0].effective_at == "2026-08-13T03:33:20Z"
    assert context.items[0].expires_at == "2033-05-18T03:33:20Z"
    confirmed = service.confirm_settings(
        workspace_id=WORKSPACE,
        request=ConfirmSettingsInput("Policy", "draft", 0, 128),
        context_snapshot_id=context.snapshot_id,
        model_deployment_id="deployment-qwen",
        idempotency_key="settings-1",
    )

    draft = service.generate_draft(
        workspace_id=WORKSPACE,
        request_id=confirmed.request_id,
        idempotency_key="generate-1",
    )

    assert transport.chat_evidence == [{
        "item_id": "copy-1",
        "text": "Policy alpha is effective.",
    }]
    output = store.get_canonical_envelope(
        WORKSPACE, "artifact", "OutputVersion", draft.output_version_id
    )
    assert output.payload["citation_lineage"] == [{
        "citation_id": "copy-1",
        "origin": "daon_knowledge",
        "producer": "daon3",
        "producer_version": "3.0.0",
        "registration_id": "registration-1",
        "registration_state": "registered",
        "source_id": None,
        "version_id": "output-version-1",
        "index_version_id": None,
        "evidence_span_ids": [],
        "digest": manifest["content_digest_sha256"],
        "authority": "approved",
        "quality_state": "approved",
        "review_state": "approved",
        "conflict_state": "none",
        "unverified": False,
    }]
    store.close()


def test_product_generation_uses_indexed_raw_source_evidence(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "raw-grounded", bytes(range(32)))
    content = "첫 번째 로컬 근거입니다.\n\n두 번째 로컬 근거입니다.".encode()
    source = RawSourceService(store).import_source(
        workspace_id=WORKSPACE,
        filename="local-evidence.txt",
        content_type="text/plain",
        content=content,
        content_digest_sha256=hashlib.sha256(content).hexdigest(),
        idempotency_key="raw-source-production-1",
    )
    transport = CatalogTransport()
    service = build_production_offline_studio(
        store,
        environment={"DAON_OLLAMA_BASE_URL": "http://127.0.0.1:11434"},
        transport=transport,
    )
    _persist_provider_settings(service)
    context = service.prepare_context(
        workspace_id=WORKSPACE,
        mode="raw_only",
        daon_knowledge_ids=(),
        raw_source_version_ids=(source.source_version_id,),
        idempotency_key="raw-context-1",
    )
    assert context.items[0].source_id == source.source_id
    assert context.items[0].index_version_id == source.index_version_id
    assert context.items[0].evidence_span_ids == source.evidence_span_ids
    assert context.items[0].processing_state == "completed"
    assert context.items[0].review_state == "unverified"
    confirmed = service.confirm_settings(
        workspace_id=WORKSPACE,
        request=ConfirmSettingsInput("Raw evidence", "draft", 0, 128),
        context_snapshot_id=context.snapshot_id,
        model_deployment_id="deployment-qwen",
        idempotency_key="raw-settings-1",
    )

    draft = service.generate_draft(
        workspace_id=WORKSPACE,
        request_id=confirmed.request_id,
        idempotency_key="raw-generate-1",
    )

    assert transport.chat_evidence == [{
        "item_id": source.source_version_id,
        "text": "첫 번째 로컬 근거입니다.\n\n두 번째 로컬 근거입니다.",
    }]
    output = store.get_canonical_envelope(
        WORKSPACE, "artifact", "OutputVersion", draft.output_version_id
    )
    citation = cast(dict[str, object], cast(list[object], output.payload["citation_lineage"])[0])
    assert citation["origin"] == "raw_source"
    assert citation["source_id"] == source.source_id
    assert citation["index_version_id"] == source.index_version_id
    assert citation["evidence_span_ids"] == list(source.evidence_span_ids)
    assert citation["conflict_state"] == "none"
    assert citation["unverified"] is True
    store.close()


def test_product_provider_settings_are_encrypted_and_workspace_scoped(tmp_path: Path) -> None:
    store = LocalEncryptedStore.open(tmp_path / "provider-settings", bytes(range(32)))
    transport = CatalogTransport()
    service = build_production_offline_studio(
        store,
        environment={
            "DAON_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            "DAON_OLLAMA_DEPLOYMENTS_JSON": _deployments(),
        },
        transport=transport,
    )
    service.import_provider_settings(
        workspace_id=WORKSPACE,
        profiles=({
            "profile_id": "provider-ollama",
            "provider_code": "OLLAMA",
            "provider_kind": "server_internal",
            "base_url": "http://ollama:11434",
            "active": True,
            "version": 4,
        },),
        deployments=({
            "deployment_id": "deployment-qwen",
            "profile_id": "provider-ollama",
            "provider_code": "OLLAMA",
            "model_id": "qwen3:8b",
            "roles": ["text"],
            "active": True,
            "selected": True,
            "version": 7,
        },),
        policy_version="provider-settings-v7",
    )

    own = service.list_models(workspace_id=WORKSPACE)
    other = service.list_models(workspace_id="44444444-4444-4444-8444-444444444444")

    assert [item.deployment_id for item in own] == ["deployment-qwen"]
    assert other == ()
    assert own[0].binding_version == 7
    assert "DAON_OLLAMA_DEPLOYMENTS_JSON" not in (
        Path(__file__).parents[1] / "src/daon_user_local_service/main.py"
    ).read_text(encoding="utf-8")
    assert b"provider-settings-v7" not in b"".join(
        item.read_bytes() for item in (tmp_path / "provider-settings").rglob("*") if item.is_file()
    )
    store.close()
