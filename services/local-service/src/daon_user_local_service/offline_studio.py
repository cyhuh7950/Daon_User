from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Callable

from .knowledge_context import (
    KnowledgeContextItem,
    KnowledgeContextMode,
    KnowledgeContextProjector,
    KnowledgeContextSnapshot,
    OfflineStudioError,
    canonical_json_bytes,
    citation_lineage,
)
from .local_storage import LocalCanonicalEnvelope, LocalEncryptedStore, LocalStorageError
from .provider_draft import (
    DraftGenerationPort,
    ModelCatalogPort,
    ModelSelectionSnapshot,
    ProviderModelDescriptor,
)


@dataclass(frozen=True, slots=True)
class ConfirmSettingsInput:
    title: str
    purpose: str
    temperature: float
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class SectionInput:
    title: str
    body: str
    unverified: bool
    citation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfirmedSettingsView:
    request_id: str
    settings_snapshot_id: str
    context_snapshot_id: str
    model_selection: ModelSelectionSnapshot


@dataclass(frozen=True, slots=True)
class OfflineDraftView:
    draft_id: str
    output_version_id: str
    output_version: int
    sections: tuple[SectionInput, ...]
    context: KnowledgeContextSnapshot
    model_selection: ModelSelectionSnapshot
    egress: str = "none"


@dataclass(frozen=True, slots=True)
class SyncQueueDraftView:
    operation_id: str
    approval_state: str
    manifest_digest: str
    payload: dict[str, object]


def _context_item(value: object) -> KnowledgeContextItem:
    if not isinstance(value, dict):
        raise OfflineStudioError("KNOWLEDGE_CONTEXT_INVALID")
    names = (
        "item_id", "origin", "producer", "version_id", "digest", "authority",
        "quality_state",
    )
    if any(not isinstance(value.get(name), str) for name in names):
        raise OfflineStudioError("KNOWLEDGE_CONTEXT_INVALID")
    weight = value.get("weight")
    optional_text = (
        "producer_version", "review_state", "processing_state", "conflict_state",
        "selection_reason",
    )
    optional_nullable = (
        "registration_id", "registration_state", "source_id", "index_version_id",
        "effective_at", "expires_at",
    )
    evidence_span_ids = value.get("evidence_span_ids", [])
    if (
        not isinstance(weight, (int, float))
        or isinstance(weight, bool)
        or any(not isinstance(value.get(name, ""), str) for name in optional_text)
        or any(
            value.get(name) is not None and not isinstance(value.get(name), str)
            for name in optional_nullable
        )
        or not isinstance(evidence_span_ids, (list, tuple))
        or any(not isinstance(item, str) for item in evidence_span_ids)
        or not isinstance(value.get("conflict_acknowledged", False), bool)
        or not isinstance(value.get("unverified", False), bool)
    ):
        raise OfflineStudioError("KNOWLEDGE_CONTEXT_INVALID")
    return KnowledgeContextItem(
        str(value["item_id"]), str(value["origin"]), str(value["producer"]),
        str(value["version_id"]), str(value["digest"]), str(value["authority"]),
        str(value["quality_state"]), float(weight),
        producer_version=str(value.get("producer_version", "")),
        registration_id=value.get("registration_id"),
        registration_state=value.get("registration_state"),
        source_id=value.get("source_id"),
        index_version_id=value.get("index_version_id"),
        evidence_span_ids=tuple(evidence_span_ids),
        review_state=str(value.get("review_state", "")),
        processing_state=str(value.get("processing_state", "")),
        effective_at=value.get("effective_at"),
        expires_at=value.get("expires_at"),
        conflict_state=str(value.get("conflict_state", "none")),
        conflict_acknowledged=bool(value.get("conflict_acknowledged", False)),
        unverified=bool(value.get("unverified", False)),
        selection_reason=str(value.get("selection_reason", "")),
    )


def _context_item_payload(item: KnowledgeContextItem) -> dict[str, object]:
    payload: dict[str, object] = asdict(item)
    payload["evidence_span_ids"] = list(item.evidence_span_ids)
    return payload


def _section(value: object) -> SectionInput:
    if not isinstance(value, dict):
        raise OfflineStudioError("OUTPUT_SECTION_INVALID")
    if set(value) not in (
        {"title", "body", "unverified"},
        {"title", "body", "unverified", "citation_ids"},
    ):
        raise OfflineStudioError("OUTPUT_SECTION_INVALID")
    raw_citations = value.get("citation_ids", [])
    if (
        not isinstance(value.get("title"), str)
        or not isinstance(value.get("body"), str)
        or not isinstance(value.get("unverified"), bool)
        or not isinstance(raw_citations, (list, tuple))
        or any(not isinstance(item, str) for item in raw_citations)
        or len(raw_citations) != len(set(raw_citations))
    ):
        raise OfflineStudioError("OUTPUT_SECTION_INVALID")
    return SectionInput(
        str(value["title"]), str(value["body"]), bool(value["unverified"]),
        tuple(raw_citations),
    )


class OfflineStudioService:
    def __init__(
        self, *, store: LocalEncryptedStore, context_projector: KnowledgeContextProjector,
        model_catalog: ModelCatalogPort, generator: DraftGenerationPort,
        clock: Callable[[], str],
        evidence_resolver: Callable[[KnowledgeContextSnapshot], list[dict[str, str]]] | None = None,
    ) -> None:
        self._store = store
        self._context_projector = context_projector
        self._model_catalog = model_catalog
        self._generator = generator
        self._clock = clock
        self._evidence_resolver = evidence_resolver or (lambda _context: [])

    @staticmethod
    def _id(prefix: str, *parts: object) -> str:
        digest = hashlib.sha256(canonical_json_bytes(parts)).hexdigest()
        return f"{prefix}-{digest[:24]}"

    def _append(
        self, workspace_id: str, entity_type: str, entity_id: str, aggregate_id: str,
        version: int, payload: Mapping[str, object], previous_version_id: str | None = None,
    ) -> None:
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        self._store.put_canonical_envelope(
            workspace_id, "artifact", entity_type=entity_type, entity_id=entity_id,
            aggregate_id=aggregate_id, version=version, schema_version=1,
            digest_sha256=digest, created_at=self._clock(),
            previous_version_id=previous_version_id, payload=dict(payload),
        )

    def _all(
        self, workspace_id: str, entity_type: str | None = None
    ) -> tuple[LocalCanonicalEnvelope, ...]:
        return self._store.list_canonical_envelopes(workspace_id, "artifact", entity_type)

    def import_provider_settings(
        self,
        *,
        workspace_id: str,
        profiles: tuple[dict[str, object], ...],
        deployments: tuple[dict[str, object], ...],
        policy_version: str,
    ) -> None:
        profile_fields = {
            "profile_id", "provider_code", "provider_kind", "base_url", "active", "version"
        }
        deployment_fields = {
            "deployment_id", "profile_id", "provider_code", "model_id", "roles",
            "active", "selected", "version",
        }
        if (
            not policy_version
            or len(profiles) > 128
            or len(deployments) > 512
            or any(
                set(profile) != profile_fields
                or any(not isinstance(profile.get(name), str) or not profile.get(name)
                       for name in ("profile_id", "provider_code", "provider_kind", "base_url"))
                or not isinstance(profile.get("active"), bool)
                or not isinstance(profile.get("version"), int)
                or isinstance(profile.get("version"), bool)
                or int(profile["version"]) < 1
                for profile in profiles
            )
            or any(
                set(deployment) != deployment_fields
                or any(not isinstance(deployment.get(name), str) or not deployment.get(name)
                       for name in ("deployment_id", "profile_id", "provider_code", "model_id"))
                or not isinstance(deployment.get("roles"), list)
                or any(not isinstance(role, str) for role in deployment.get("roles", []))
                or not isinstance(deployment.get("active"), bool)
                or not isinstance(deployment.get("selected"), bool)
                or not isinstance(deployment.get("version"), int)
                or isinstance(deployment.get("version"), bool)
                or int(deployment["version"]) < 1
                for deployment in deployments
            )
        ):
            raise OfflineStudioError("PROVIDER_SETTINGS_INVALID")
        payload: dict[str, object] = {
            "workspace_id": workspace_id,
            "profiles": sorted((dict(item) for item in profiles), key=lambda item: str(item["profile_id"])),
            "deployments": sorted(
                (dict(item) for item in deployments), key=lambda item: str(item["deployment_id"])
            ),
            "policy_version": policy_version,
        }
        fingerprint = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        existing = self._all(workspace_id, "ProviderSettingsSnapshot")
        if existing and existing[-1].digest_sha256 == fingerprint:
            return
        version = existing[-1].version + 1 if existing else 1
        entity_id = self._id("provider-settings", workspace_id, version, fingerprint)
        self._append(
            workspace_id,
            "ProviderSettingsSnapshot",
            entity_id,
            "provider-settings",
            version,
            payload,
            existing[-1].entity_id if existing else None,
        )
    def list_models(self, *, workspace_id: str) -> tuple[ProviderModelDescriptor, ...]:
        return self._model_catalog.list_eligible(workspace_id=workspace_id)

    def prepare_context(
        self, *, workspace_id: str, mode: str, daon_knowledge_ids: tuple[str, ...],
        raw_source_version_ids: tuple[str, ...], idempotency_key: str,
    ) -> KnowledgeContextSnapshot:
        key_digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        request_fingerprint = hashlib.sha256(canonical_json_bytes({
            "workspace_id": workspace_id,
            "mode": mode,
            "daon_knowledge_ids": list(daon_knowledge_ids),
            "raw_source_version_ids": list(raw_source_version_ids),
        })).hexdigest()
        replay = next(
            (
                row for row in self._all(workspace_id, "ScopeSnapshot")
                if row.payload.get("idempotency_fingerprint") == key_digest
            ),
            None,
        )
        if replay is not None:
            if replay.payload.get("request_fingerprint") != request_fingerprint:
                raise OfflineStudioError("IDEMPOTENCY_KEY_REUSED")
            return self._load_context(workspace_id, replay.entity_id)
        context = self._context_projector.project(
            workspace_id=workspace_id, mode=mode, daon_knowledge_ids=daon_knowledge_ids,
            raw_source_version_ids=raw_source_version_ids,
        )
        existing = next((row for row in self._all(workspace_id, "ScopeSnapshot")
                         if row.entity_id == context.snapshot_id), None)
        if existing is None:
            payload = {
                "snapshot_id": context.snapshot_id, "workspace_id": workspace_id,
                "schema_version": context.schema_version,
                "created_at": context.created_at,
                "mode": context.mode.value,
                "items": [_context_item_payload(item) for item in context.items],
                "knowledge_scope_id": context.knowledge_scope_id,
                "weight_profile_id": context.weight_profile_id,
                "warnings": list(context.warnings), "digest": context.digest,
                "idempotency_fingerprint": key_digest,
                "request_fingerprint": request_fingerprint,
            }
            self._append(
                workspace_id, "ScopeSnapshot", context.snapshot_id, context.snapshot_id, 1, payload
            )
        return context

    def confirm_settings(
        self, *, workspace_id: str, request: ConfirmSettingsInput,
        context_snapshot_id: str, model_deployment_id: str, idempotency_key: str,
        selection_actor_id: str = "local-session",
    ) -> ConfirmedSettingsView:
        context = self._load_context(workspace_id, context_snapshot_id)
        if not request.title or not request.purpose or not 0 <= request.temperature <= 2:
            raise OfflineStudioError("GENERATION_SETTINGS_INVALID")
        if request.max_output_tokens < 1 or request.max_output_tokens > 32768:
            raise OfflineStudioError("GENERATION_SETTINGS_INVALID")
        request_id = self._id("generation-request", workspace_id, idempotency_key)
        settings_id = self._id("generation-settings", workspace_id, idempotency_key)
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "request": asdict(request), "context_snapshot_id": context_snapshot_id,
            "model_deployment_id": model_deployment_id,
        })).hexdigest()
        existing = [row for row in self._all(workspace_id, "GenerationRequest")
                    if row.aggregate_id == request_id]
        if existing:
            latest = existing[-1].payload
            if latest.get("request_fingerprint") != fingerprint:
                raise OfflineStudioError("IDEMPOTENCY_KEY_REUSED")
            return ConfirmedSettingsView(
                request_id, str(latest["settings_snapshot_id"]), context_snapshot_id,
                self._selection(latest["model_selection"]),
            )
        selection = self._model_catalog.select(
            workspace_id=workspace_id, deployment_id=model_deployment_id,
            temperature=request.temperature, max_output_tokens=request.max_output_tokens,
            selection_actor_id=selection_actor_id, selected_at=self._clock(),
        )
        request_payload = {
            "request_id": request_id, "title": request.title, "purpose": request.purpose,
            "state": "configuring", "request_fingerprint": fingerprint,
        }
        self._append(workspace_id, "GenerationRequest", request_id, request_id, 1, request_payload)
        settings_payload = {
            "settings_snapshot_id": settings_id, "request_id": request_id,
            "temperature": request.temperature, "max_output_tokens": request.max_output_tokens,
            "context_snapshot_id": context.snapshot_id,
            "context_digest": context.digest,
            "model_selection": asdict(selection),
        }
        settings_digest = hashlib.sha256(canonical_json_bytes(settings_payload)).hexdigest()
        self._append(
            workspace_id, "GenerationSettingsSnapshot", settings_id, settings_id, 1,
            settings_payload,
        )
        confirmed_id = self._id("generation-request-confirmed", request_id)
        confirmed_payload = {
            **request_payload, "state": "submitted", "context_snapshot_id": context.snapshot_id,
            "context_digest": context.digest, "settings_snapshot_id": settings_id,
            "settings_snapshot_digest": settings_digest,
            "model_selection": asdict(selection),
        }
        self._append(
            workspace_id, "GenerationRequest", confirmed_id, request_id, 2,
            confirmed_payload, request_id,
        )
        return ConfirmedSettingsView(request_id, settings_id, context_snapshot_id, selection)

    def generate_draft(
        self, *, workspace_id: str, request_id: str, idempotency_key: str
    ) -> OfflineDraftView:
        requests = [row for row in self._all(workspace_id, "GenerationRequest")
                    if row.aggregate_id == request_id]
        if not requests or requests[-1].payload.get("state") != "submitted":
            raise OfflineStudioError("GENERATION_REQUEST_NOT_CONFIRMED")
        confirmed = requests[-1].payload
        context = self._load_context(workspace_id, str(confirmed["context_snapshot_id"]))
        selection = self._selection(confirmed["model_selection"])
        current = self._model_catalog.select(
            workspace_id=workspace_id, deployment_id=selection.deployment_id,
            temperature=selection.temperature, max_output_tokens=selection.max_output_tokens,
            selection_actor_id=selection.selection_actor_id,
            selected_at=selection.selected_at,
        )
        if current != selection:
            raise OfflineStudioError("MODEL_SELECTION_STALE")
        draft_id = self._id("studio-output", workspace_id, request_id)
        existing = [row for row in self._all(workspace_id, "OutputVersion")
                    if row.aggregate_id == draft_id]
        if existing:
            return self._draft_from_envelope(context, selection, existing[-1])
        evidence = self._evidence_resolver(context)
        expected_evidence_ids = {item.item_id for item in context.items}
        actual_evidence_ids = {
            item.get("item_id") for item in evidence if isinstance(item, dict)
        }
        if (
            not evidence
            or len(evidence) != len(context.items)
            or actual_evidence_ids != expected_evidence_ids
        ):
            raise OfflineStudioError("KNOWLEDGE_EVIDENCE_UNAVAILABLE")
        output = self._generator.generate(
            selection=selection, context=context,
            request={
                "request_id": request_id,
                "purpose": confirmed.get("purpose"),
                "evidence": evidence,
            },
        )
        raw_output_sections = output.get("sections")
        if not isinstance(raw_output_sections, list):
            raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
        citation_ids: set[str] = set()
        for section in raw_output_sections:
            if not isinstance(section, dict):
                raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
            raw_citations = section.get("citation_ids", [])
            if not isinstance(raw_citations, list):
                raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
            citation_ids.update(
                citation for citation in raw_citations if isinstance(citation, str)
            )
        context_by_id = {item.item_id: item for item in context.items}
        citation_payload = [
            citation_lineage(context_by_id[citation_id])
            for citation_id in sorted(citation_ids)
        ]
        egress = "none" if selection.provider_kind == "server_internal" else "approved_external"
        offline = selection.provider_kind == "server_internal"
        run_id = self._id("run", workspace_id, request_id, idempotency_key)
        self._append(
            workspace_id, "Run", run_id, run_id, 1,
            {"run_id": run_id, "request_id": request_id, "state": "completed"},
        )
        run_snapshot_id = self._id("run-snapshot", run_id)
        created_at = self._clock()
        trace_id = self._id("trace", run_id, created_at)
        self._append(
            workspace_id, "RunSnapshot", run_snapshot_id, run_id, 1,
            {"run_id": run_id, "request_id": request_id, "context_snapshot_id": context.snapshot_id,
             "context_digest": context.digest, "mode": context.mode.value,
             "items": [_context_item_payload(item) for item in context.items],
             "settings_snapshot_id": confirmed["settings_snapshot_id"],
             "settings_snapshot_digest": confirmed["settings_snapshot_digest"],
             "model_selection": asdict(selection), "egress": egress, "offline": offline,
             "citation_ids": sorted(citation_ids),
             "citation_lineage": citation_payload, "trace_id": trace_id,
             "created_at": created_at, "template_id": "offline-document-v1",
             "review_conditions": list(context.warnings)},
        )
        self._append(
            workspace_id, "StudioOutput", draft_id, draft_id, 1,
            {"draft_id": draft_id, "request_id": request_id, "title": confirmed.get("title")},
        )
        output_version_id = self._id("output-version", draft_id, 1)
        payload = {
            "draft_id": draft_id, "output_version_id": output_version_id, "output_version": 1,
            "sections": raw_output_sections, "context_snapshot_id": context.snapshot_id,
            "context_digest": context.digest,
            "settings_snapshot_id": confirmed["settings_snapshot_id"],
            "settings_snapshot_digest": confirmed["settings_snapshot_digest"],
            "model_selection": asdict(selection), "egress": egress,
            "citation_lineage": citation_payload,
        }
        self._append(
            workspace_id, "OutputVersion", output_version_id, draft_id, 1, payload
        )
        return self._draft_from_envelope(
            context, selection,
            self._store.get_canonical_envelope(
                workspace_id, "artifact", "OutputVersion", output_version_id
            ),
        )

    def append_edit(
        self, *, workspace_id: str, draft_id: str, previous_version_id: str,
        sections: tuple[SectionInput, ...], idempotency_key: str,
    ) -> OfflineDraftView:
        del idempotency_key
        versions = [row for row in self._all(workspace_id, "OutputVersion")
                    if row.aggregate_id == draft_id]
        if not versions or versions[-1].entity_id != previous_version_id:
            raise OfflineStudioError("OUTPUT_VERSION_CONFLICT")
        if not sections or any(not section.title or not section.body or not section.unverified
                               for section in sections):
            raise OfflineStudioError("OUTPUT_SECTION_INVALID")
        prior = versions[-1]
        context = self._load_context(workspace_id, str(prior.payload["context_snapshot_id"]))
        selection = self._selection(prior.payload["model_selection"])
        version = prior.version + 1
        output_version_id = self._id("output-version", draft_id, version)
        payload = {
            **prior.payload, "output_version_id": output_version_id, "output_version": version,
            "sections": [{
                "title": section.title,
                "body": section.body,
                "unverified": section.unverified,
                "citation_ids": list(section.citation_ids),
            } for section in sections],
        }
        self._append(
            workspace_id, "OutputVersion", output_version_id, draft_id, version, payload,
            previous_version_id,
        )
        return self._draft_from_envelope(
            context, selection,
            self._store.get_canonical_envelope(
                workspace_id, "artifact", "OutputVersion", output_version_id
            ),
        )

    def get_draft(self, *, workspace_id: str, draft_id: str) -> OfflineDraftView:
        versions = [row for row in self._all(workspace_id, "OutputVersion")
                    if row.aggregate_id == draft_id]
        if not versions:
            raise OfflineStudioError("OFFLINE_DRAFT_NOT_FOUND")
        latest = versions[-1]
        context = self._load_context(workspace_id, str(latest.payload["context_snapshot_id"]))
        return self._draft_from_envelope(
            context, self._selection(latest.payload["model_selection"]), latest
        )

    def get_draft_global(self, *, draft_id: str) -> OfflineDraftView:
        workspace_id = self._store.find_canonical_workspace(
            "artifact", "OutputVersion", draft_id
        )
        return self.get_draft(workspace_id=workspace_id, draft_id=draft_id)

    def queue_sync_preview(
        self, *, workspace_id: str, draft_id: str, output_version_id: str,
        source_dependency_ids: tuple[str, ...], idempotency_key: str,
    ) -> SyncQueueDraftView:
        draft = self.get_draft(workspace_id=workspace_id, draft_id=draft_id)
        if draft.output_version_id != output_version_id:
            raise OfflineStudioError("OUTPUT_VERSION_CONFLICT")
        payload: dict[str, object] = {
            "draft_id": draft_id, "output_version_id": output_version_id,
            "source_dependency_ids": list(source_dependency_ids), "state": "draft",
        }
        manifest_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        operation_id = self._id("sync-preview", workspace_id, idempotency_key)
        self._store.append_sync_queue_state(
            workspace_id, operation_id=operation_id, version=1, approval_state="draft",
            manifest_digest=manifest_digest, batch_cursor=None, conflict_id=None,
            queued_at=self._clock(), previous_version=None,
        )
        return SyncQueueDraftView(operation_id, "draft", manifest_digest, payload)

    def _load_context(self, workspace_id: str, snapshot_id: str) -> KnowledgeContextSnapshot:
        try:
            envelope = self._store.get_canonical_envelope(
                workspace_id, "artifact", "ScopeSnapshot", snapshot_id
            )
        except LocalStorageError as error:
            raise OfflineStudioError("KNOWLEDGE_CONTEXT_NOT_FOUND") from error
        payload = envelope.payload
        raw_items = payload.get("items")
        raw_warnings = payload.get("warnings")
        if not isinstance(raw_items, list):
            raise OfflineStudioError("KNOWLEDGE_CONTEXT_INVALID")
        if not isinstance(raw_warnings, list):
            raise OfflineStudioError("KNOWLEDGE_CONTEXT_INVALID")
        return KnowledgeContextSnapshot(
            snapshot_id, workspace_id, KnowledgeContextMode(str(payload["mode"])),
            tuple(_context_item(item) for item in raw_items),
            str(payload["knowledge_scope_id"]), str(payload["weight_profile_id"]),
            tuple(str(item) for item in raw_warnings), str(payload["digest"]),
            int(payload.get("schema_version", 1)),
            str(payload.get("created_at", envelope.created_at)),
        )

    @staticmethod
    def _selection(value: object) -> ModelSelectionSnapshot:
        if not isinstance(value, dict):
            raise OfflineStudioError("MODEL_SELECTION_STALE")
        try:
            return ModelSelectionSnapshot(**value)
        except TypeError:
            raise OfflineStudioError("MODEL_SELECTION_STALE") from None

    @staticmethod
    def _draft_from_envelope(
        context: KnowledgeContextSnapshot, selection: ModelSelectionSnapshot,
        envelope: LocalCanonicalEnvelope,
    ) -> OfflineDraftView:
        payload = envelope.payload
        raw_sections = payload.get("sections")
        if not isinstance(raw_sections, list):
            raise OfflineStudioError("OUTPUT_SECTION_INVALID")
        sections = tuple(_section(section) for section in raw_sections)
        return OfflineDraftView(
            str(payload["draft_id"]), str(payload["output_version_id"]),
            int(str(payload["output_version"])), sections, context, selection,
            str(payload.get("egress", "none")),
        )
