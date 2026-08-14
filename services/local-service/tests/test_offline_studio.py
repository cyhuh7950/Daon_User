from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from daon_user_local_service.knowledge_context import KnowledgeContextProjector
from daon_user_local_service.local_storage import LocalEncryptedStore
from daon_user_local_service.knowledge_context import OfflineStudioError
from daon_user_local_service.offline_studio import ConfirmSettingsInput, OfflineStudioService, SectionInput
from daon_user_local_service.provider_draft import (
    OllamaDraftGenerationAdapter,
    OllamaModelCatalog,
    ProviderModelDescriptor,
)


WORKSPACE = "33333333-3333-4333-8333-333333333333"
NOW = "2026-08-14T03:00:00Z"


class ProjectionSource:
    def get_daon_knowledge(self, *, workspace_id: str, knowledge_id: str) -> dict[str, object] | None:
        return {
            "producer": "daon3", "producer_version": "3.0.0",
            "registration_id": "registration-v3", "version_id": "output-v3",
            "digest": "a" * 64,
            "registration_digest": "a" * 64, "quality_state": "approved",
            "authority": "approved", "review_state": "approved",
            "registration_state": "registered", "effective": True,
            "effective_at": "2026-08-13T03:00:00Z",
            "expires_at": "2026-08-15T03:00:00Z",
        }

    def get_raw_source(self, *, workspace_id: str, source_version_id: str) -> dict[str, object] | None:
        return {
            "source_id": "source-1", "version_id": source_version_id,
            "digest": "b" * 64,
            "index_version_id": "index-v1", "evidence_span_ids": ["span-v1"],
            "processing_state": "completed", "review_state": "unverified",
            "quality_state": "unverified", "authority": "user_source",
            "conflict_state": "none", "conflict_acknowledged": False,
            "local": True,
        }


class OllamaFixtureTransport:
    def get_json(
        self, *, url: str, timeout_seconds: float, max_response_bytes: int
    ) -> dict[str, object]:
        return {"models": [{"name": "fixture:latest", "digest": "sha256:" + "c" * 64}]}

    def post_json(
        self, *, url: str, payload: dict[str, object], timeout_seconds: float,
        max_response_bytes: int, api_key: str | None = None,
    ) -> dict[str, object]:
        assert api_key is None
        if url.endswith("/api/show"):
            return {"capabilities": ["completion"]}
        messages = payload["messages"]
        assert isinstance(messages, list)
        supplied = json.loads(messages[-1]["content"])
        context = supplied["context"]
        item_id = context["items"][0]["item_id"]
        content = json.dumps({
            "schema_version": 1,
            "sections": [{
                "title": "Summary", "body": "Offline deterministic draft",
                "citation_ids": [item_id], "unverified": context["mode"] == "raw_only",
            }],
        }, separators=(",", ":"))
        return {"message": {"content": content}}


def _service(root: Path) -> tuple[LocalEncryptedStore, OfflineStudioService]:
    store = LocalEncryptedStore.open(root, bytes(range(32)))
    transport = OllamaFixtureTransport()
    descriptor = ProviderModelDescriptor(
        "OLLAMA", "server_internal", "provider-ollama", "deployment-local-2",
        "fixture:latest", "sha256:" + "c" * 64, "d" * 64, 1,
    )
    catalog = OllamaModelCatalog(
        base_url="http://127.0.0.1:11434", transport=transport,
        descriptors={"deployment-local-2": descriptor},
    )
    generator = OllamaDraftGenerationAdapter(
        base_url="http://127.0.0.1:11434", transport=transport, catalog=catalog,
    )
    service = OfflineStudioService(
        store=store, context_projector=KnowledgeContextProjector(
            ProjectionSource(),
            clock=lambda: datetime(2026, 8, 14, 3, 0, tzinfo=UTC),
        ),
        model_catalog=catalog, generator=generator, clock=lambda: NOW,
        evidence_resolver=lambda context: [
            {"item_id": item.item_id, "text": "Offline deterministic draft evidence"}
            for item in context.items
        ],
    )
    return store, service


def test_generate_edit_restart_and_queue_are_append_only_and_encrypted(tmp_path: Path) -> None:
    root = tmp_path / "studio"
    store, service = _service(root)
    context = service.prepare_context(
        workspace_id=WORKSPACE, mode="daon_priority",
        daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=("source-v1",),
        idempotency_key="context-1",
    )
    confirmed = service.confirm_settings(
        workspace_id=WORKSPACE,
        request=ConfirmSettingsInput("Report", "Purpose", 0.1, 256),
        context_snapshot_id=context.snapshot_id,
        model_deployment_id="deployment-local-2", idempotency_key="confirm-1",
        selection_actor_id="user-1",
    )
    draft = service.generate_draft(
        workspace_id=WORKSPACE, request_id=confirmed.request_id, idempotency_key="generate-1"
    )
    assert (draft.output_version, draft.egress) == (1, "none")
    assert draft.context.items[0].origin == "daon_knowledge"
    assert draft.model_selection.deployment_id == "deployment-local-2"
    assert draft.model_selection.selection_actor_id == "user-1"
    assert store.list_canonical_types(WORKSPACE, "artifact") == (
        "ScopeSnapshot", "GenerationRequest", "GenerationSettingsSnapshot", "GenerationRequest",
        "Run", "RunSnapshot", "StudioOutput", "OutputVersion",
    )
    settings = store.list_canonical_envelopes(
        WORKSPACE, "artifact", "GenerationSettingsSnapshot"
    )[-1].payload
    run = store.list_canonical_envelopes(WORKSPACE, "artifact", "RunSnapshot")[-1].payload
    output = store.list_canonical_envelopes(WORKSPACE, "artifact", "OutputVersion")[-1].payload
    assert settings["context_snapshot_id"] == context.snapshot_id
    assert settings["context_digest"] == context.digest
    assert settings["model_selection"] == run["model_selection"] == output["model_selection"]
    assert run["settings_snapshot_id"] == output["settings_snapshot_id"] == confirmed.settings_snapshot_id
    assert run["context_snapshot_id"] == output["context_snapshot_id"] == context.snapshot_id
    assert {"trace_id", "created_at", "template_id", "review_conditions"} <= set(run)
    edited = service.append_edit(
        workspace_id=WORKSPACE, draft_id=draft.draft_id,
        previous_version_id=draft.output_version_id,
        sections=(SectionInput("Summary", "Edited", True),), idempotency_key="edit-1",
    )
    assert edited.output_version == 2
    queue = service.queue_sync_preview(
        workspace_id=WORKSPACE, draft_id=draft.draft_id,
        output_version_id=edited.output_version_id,
        source_dependency_ids=("source-v1",), idempotency_key="queue-1",
    )
    assert queue.approval_state == "draft"
    store.close()
    raw = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
    assert b"Offline deterministic draft" not in raw
    reopened, restarted = _service(root)
    assert restarted.get_draft(workspace_id=WORKSPACE, draft_id=draft.draft_id).output_version == 2
    reopened.close()


def test_generation_requires_confirmation_and_selection_remains_current(tmp_path: Path) -> None:
    store, service = _service(tmp_path / "fail")
    with pytest.raises(OfflineStudioError, match="GENERATION_REQUEST_NOT_CONFIRMED"):
        service.generate_draft(
            workspace_id=WORKSPACE, request_id="missing", idempotency_key="generate-missing"
        )
    store.lock()
    with pytest.raises(Exception, match="LOCAL_KEY_UNAVAILABLE"):
        service.get_draft(workspace_id=WORKSPACE, draft_id="missing")


def test_queue_canonical_bundle_contains_no_body_token_key_or_cloud_url(tmp_path: Path) -> None:
    store, service = _service(tmp_path / "queue")
    context = service.prepare_context(
        workspace_id=WORKSPACE, mode="raw_only", daon_knowledge_ids=(),
        raw_source_version_ids=("source-v1",), idempotency_key="context-raw",
    )
    confirmed = service.confirm_settings(
        workspace_id=WORKSPACE, request=ConfirmSettingsInput("Draft", "Raw", 0.1, 64),
        context_snapshot_id=context.snapshot_id, model_deployment_id="deployment-local-2",
        idempotency_key="confirm-raw",
    )
    draft = service.generate_draft(
        workspace_id=WORKSPACE, request_id=confirmed.request_id, idempotency_key="generate-raw"
    )
    queue = service.queue_sync_preview(
        workspace_id=WORKSPACE, draft_id=draft.draft_id,
        output_version_id=draft.output_version_id,
        source_dependency_ids=("source-v1",), idempotency_key="queue-raw",
    )
    encoded = json.dumps(queue.payload, sort_keys=True)
    assert all(forbidden not in encoded.lower() for forbidden in ("body", "token", "key", "http"))
    store.close()


def test_confirm_idempotency_replay_is_exact_and_changed_payload_is_rejected(tmp_path: Path) -> None:
    store, service = _service(tmp_path / "idempotency")
    context = service.prepare_context(
        workspace_id=WORKSPACE, mode="daon_priority",
        daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=(),
        idempotency_key="context-idempotent",
    )
    request = ConfirmSettingsInput("Report", "Original", 0.1, 256)
    first = service.confirm_settings(
        workspace_id=WORKSPACE, request=request, context_snapshot_id=context.snapshot_id,
        model_deployment_id="deployment-local-2", idempotency_key="confirm-idempotent",
    )
    replay = service.confirm_settings(
        workspace_id=WORKSPACE, request=request, context_snapshot_id=context.snapshot_id,
        model_deployment_id="deployment-local-2", idempotency_key="confirm-idempotent",
    )
    assert replay == first
    with pytest.raises(OfflineStudioError, match="IDEMPOTENCY_KEY_REUSED"):
        service.confirm_settings(
            workspace_id=WORKSPACE,
            request=ConfirmSettingsInput("Report", "Changed", 0.1, 256),
            context_snapshot_id=context.snapshot_id,
            model_deployment_id="deployment-local-2", idempotency_key="confirm-idempotent",
        )
    store.close()


def test_context_idempotency_replays_exact_snapshot_and_rejects_changed_selection(
    tmp_path: Path,
) -> None:
    store, service = _service(tmp_path / "context-idempotency")
    first = service.prepare_context(
        workspace_id=WORKSPACE,
        mode="mixed",
        daon_knowledge_ids=("knowledge-daon3",),
        raw_source_version_ids=("source-v1",),
        idempotency_key="context-exact-replay",
    )
    replay = service.prepare_context(
        workspace_id=WORKSPACE,
        mode="mixed",
        daon_knowledge_ids=("knowledge-daon3",),
        raw_source_version_ids=("source-v1",),
        idempotency_key="context-exact-replay",
    )
    assert replay == first

    with pytest.raises(OfflineStudioError, match="IDEMPOTENCY_KEY_REUSED"):
        service.prepare_context(
            workspace_id=WORKSPACE,
            mode="daon_priority",
            daon_knowledge_ids=("knowledge-daon3",),
            raw_source_version_ids=("source-v1",),
            idempotency_key="context-exact-replay",
        )
    assert len(store.list_canonical_envelopes(
        WORKSPACE, "artifact", "ScopeSnapshot"
    )) == 1
    store.close()


def test_generate_rejects_stale_selected_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, service = _service(tmp_path / "stale")
    context = service.prepare_context(
        workspace_id=WORKSPACE, mode="daon_priority",
        daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=(),
        idempotency_key="context-stale",
    )
    confirmed = service.confirm_settings(
        workspace_id=WORKSPACE, request=ConfirmSettingsInput("Report", "Stale", 0.1, 256),
        context_snapshot_id=context.snapshot_id, model_deployment_id="deployment-local-2",
        idempotency_key="confirm-stale",
    )
    catalog = service._model_catalog
    original_select = cast(Any, catalog.select)

    def stale_select(**kwargs: object):
        return replace(original_select(**kwargs), deployment_digest="f" * 64)

    monkeypatch.setattr(catalog, "select", stale_select)
    with pytest.raises(OfflineStudioError, match="MODEL_SELECTION_STALE"):
        service.generate_draft(
            workspace_id=WORKSPACE, request_id=confirmed.request_id,
            idempotency_key="generate-stale",
        )
    store.close()
