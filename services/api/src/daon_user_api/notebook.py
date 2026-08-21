"""Tenant/workspace-scoped Notebook domain and safe Home projection."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Protocol


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BINDING_KINDS = frozenset({
    "source", "knowledge_context", "conversation_thread",
    "studio_output", "output_version", "generation_settings",
})


class NotebookError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NotebookContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None for value in (
            self.tenant_id, self.workspace_id, self.actor_id, self.trace_id, self.policy_version,
        )):
            raise NotebookError("NOTEBOOK_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class Notebook:
    notebook_id: str
    tenant_id: str
    workspace_id: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NotebookBinding:
    tenant_id: str
    workspace_id: str
    notebook_id: str
    binding_kind: str
    record_id: str
    version_id: str | None


@dataclass(frozen=True, slots=True)
class NotebookBindingChangeView:
    notebook_id: str
    source_id: str
    source_version_id: str
    status: str
    etag: str


@dataclass(frozen=True, slots=True)
class NotebookActivity:
    tenant_id: str
    workspace_id: str
    notebook_id: str
    activity_kind: str
    occurred_at: datetime
    actor_id: str


@dataclass(frozen=True, slots=True)
class NotebookHomeView:
    notebook_id: str
    title: str
    source_count: int
    output_count: int
    updated_at: str
    status: str
    etag: str


@dataclass(frozen=True, slots=True)
class NotebookConversationCitation:
    citation_id: str
    source_id: str
    source_version_id: str
    evidence_span_id: str
    page: int
    origin: str
    context_item_id: str
    locator: dict[str, str]


@dataclass(frozen=True, slots=True)
class NotebookConversationView:
    conversation_thread_id: str
    run_id: str
    run_result_id: str
    answer: str
    insufficient: bool
    citations: tuple[NotebookConversationCitation, ...]


@dataclass(frozen=True, slots=True)
class NotebookSourceDeletionView:
    request_id: str
    source_id: str
    state: str
    version: int
    grace_until: datetime
    legal_hold_active: bool


@dataclass(frozen=True, slots=True)
class NotebookSelectedContext:
    notebook_id: str
    sources: tuple[tuple[str, str], ...]
    knowledge_context_ids: tuple[str, ...]
    conversation_thread_ids: tuple[str, ...]
    studio_output_ids: tuple[str, ...]
    output_version_ids: tuple[str, ...]
    generation_settings_ids: tuple[str, ...]
    source_deletion_requests: tuple[NotebookSourceDeletionView, ...] = ()
    conversation: NotebookConversationView | None = None
    etag: str = '"notebook-binding:1"'

    @property
    def is_empty(self) -> bool:
        return not any((
            self.sources, self.knowledge_context_ids, self.conversation_thread_ids,
            self.studio_output_ids, self.output_version_ids, self.generation_settings_ids,
        ))


@dataclass(frozen=True, slots=True)
class _Metadata:
    version: int
    title: str
    description: str | None
    updated_at: datetime
    updated_by: str


class NotebookRepository(Protocol):
    creation_license_authoritative: bool

    def create(
        self, context: NotebookContext, *, title: str, description: str | None,
        idempotency_key: str, request_fingerprint: str, now: datetime,
    ) -> tuple[NotebookHomeView, bool]: ...
    def list(self, context: NotebookContext) -> tuple[NotebookHomeView, ...]: ...
    def get(self, context: NotebookContext, notebook_id: str) -> NotebookHomeView: ...
    def update_title(
        self, context: NotebookContext, notebook_id: str, *, title: str,
        expected_etag: str, idempotency_key: str, request_fingerprint: str, now: datetime,
    ) -> tuple[NotebookHomeView, bool]: ...
    def bind_verified(
        self, context: NotebookContext, notebook_id: str, *, binding_kind: str,
        record_id: str, version_id: str | None, now: datetime,
    ) -> bool: ...
    def read_selected_context(
        self, context: NotebookContext, notebook_id: str,
    ) -> NotebookSelectedContext: ...
    def unbind_source(
        self, context: NotebookContext, notebook_id: str, *, source_id: str,
        source_version_id: str, expected_etag: str, idempotency_key: str,
        request_fingerprint: str, now: datetime,
    ) -> tuple[NotebookBindingChangeView, bool]: ...


def _canonical_text(value: object, *, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or _CONTROL.search(value):
        raise NotebookError(f"NOTEBOOK_{field}_INVALID")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not minimum <= len(normalized) <= maximum:
        raise NotebookError(f"NOTEBOOK_{field}_INVALID")
    return normalized


def _canonical_description(value: object) -> str | None:
    if value is None:
        return None
    normalized = _canonical_text(value, field="DESCRIPTION", minimum=0, maximum=500)
    return normalized or None


def _key(value: object) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise NotebookError("IDEMPOTENCY_KEY_INVALID")
    return value


def _fingerprint(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _binding(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise NotebookError(f"NOTEBOOK_BINDING_{field}_INVALID")
    return value


def _binding_values(
    binding_kind: object, record_id: object, version_id: object,
) -> tuple[str, str, str | None]:
    if not isinstance(binding_kind, str) or binding_kind not in _BINDING_KINDS:
        raise NotebookError("NOTEBOOK_BINDING_KIND_INVALID")
    record = _binding(record_id, field="RECORD_ID")
    if binding_kind == "source":
        version = _binding(version_id, field="VERSION_ID")
    elif version_id is not None:
        raise NotebookError("NOTEBOOK_BINDING_VERSION_ID_INVALID")
    else:
        version = None
    return binding_kind, record, version


def _selected_context(
    notebook_id: str, bindings: tuple[NotebookBinding, ...],
    conversation: NotebookConversationView | None = None,
    binding_version: int = 1,
) -> NotebookSelectedContext:
    def records(kind: str) -> tuple[str, ...]:
        return tuple(sorted(item.record_id for item in bindings if item.binding_kind == kind))
    return NotebookSelectedContext(
        notebook_id=notebook_id,
        sources=tuple(sorted(
            (item.record_id, item.version_id)
            for item in bindings if item.binding_kind == "source" and item.version_id is not None
        )),
        knowledge_context_ids=records("knowledge_context"),
        conversation_thread_ids=(
            (conversation.conversation_thread_id,) if conversation is not None
            else records("conversation_thread")[-1:]
        ),
        studio_output_ids=records("studio_output"),
        output_version_ids=records("output_version"),
        generation_settings_ids=records("generation_settings"),
        conversation=conversation, etag=f'"notebook-binding:{binding_version}"',
    )


class NotebookService:
    def __init__(
        self, repository: NotebookRepository, *, clock: Callable[[], datetime],
        require_create: Callable[[NotebookContext], None] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._require_create = require_create

    def create(
        self, context: NotebookContext, *, title: object, description: object,
        idempotency_key: object,
    ) -> tuple[NotebookHomeView, bool]:
        canonical_title = _canonical_text(title, field="TITLE", minimum=1, maximum=120)
        canonical_description = _canonical_description(description)
        key = _key(idempotency_key)
        request_fingerprint = _fingerprint({
            "tenant_id": context.tenant_id, "workspace_id": context.workspace_id,
            "actor_id": context.actor_id, "title": canonical_title,
            "description": canonical_description,
        })
        if self._require_create is not None and not self._repository.creation_license_authoritative:
            self._require_create(context)
        return self._repository.create(
            context, title=canonical_title, description=canonical_description,
            idempotency_key=key, request_fingerprint=request_fingerprint, now=self._clock(),
        )

    def list(self, context: NotebookContext) -> tuple[NotebookHomeView, ...]:
        return self._repository.list(context)

    def get(self, context: NotebookContext, notebook_id: str) -> NotebookHomeView:
        if not isinstance(notebook_id, str) or _SAFE_ID.fullmatch(notebook_id) is None:
            raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
        return self._repository.get(context, notebook_id)

    def update_title(
        self, context: NotebookContext, notebook_id: str, *, title: object,
        expected_etag: object, idempotency_key: object,
    ) -> tuple[NotebookHomeView, bool]:
        if not isinstance(notebook_id, str) or _SAFE_ID.fullmatch(notebook_id) is None:
            raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
        canonical_title = _canonical_text(title, field="TITLE", minimum=1, maximum=120)
        if not isinstance(expected_etag, str) or not re.fullmatch(r'"notebook:[1-9][0-9]*"', expected_etag):
            raise NotebookError("NOTEBOOK_ETAG_INVALID")
        key = _key(idempotency_key)
        fingerprint = _fingerprint({
            "tenant_id": context.tenant_id, "workspace_id": context.workspace_id,
            "actor_id": context.actor_id, "notebook_id": notebook_id,
            "title": canonical_title, "expected_etag": expected_etag,
        })
        return self._repository.update_title(
            context, notebook_id, title=canonical_title, expected_etag=expected_etag,
            idempotency_key=key, request_fingerprint=fingerprint, now=self._clock(),
        )

    def bind_verified(
        self, context: NotebookContext, notebook_id: object, *, binding_kind: object,
        record_id: object, version_id: object = None,
    ) -> bool:
        notebook = _binding(notebook_id, field="NOTEBOOK_ID")
        kind, record, version = _binding_values(binding_kind, record_id, version_id)
        return self._repository.bind_verified(
            context, notebook, binding_kind=kind, record_id=record,
            version_id=version, now=self._clock(),
        )

    def read_selected_context(
        self, context: NotebookContext, notebook_id: object,
    ) -> NotebookSelectedContext:
        notebook = _binding(notebook_id, field="NOTEBOOK_ID")
        return self._repository.read_selected_context(context, notebook)

    def unbind_source(
        self, context: NotebookContext, notebook_id: object, *, source_id: object,
        source_version_id: object, expected_etag: object, idempotency_key: object,
    ) -> tuple[NotebookBindingChangeView, bool]:
        notebook = _binding(notebook_id, field="NOTEBOOK_ID")
        source = _binding(source_id, field="RECORD_ID")
        version = _binding(source_version_id, field="VERSION_ID")
        if not isinstance(expected_etag, str) or re.fullmatch(r'"notebook-binding:[1-9][0-9]*"', expected_etag) is None:
            raise NotebookError("NOTEBOOK_BINDING_ETAG_INVALID")
        key = _key(idempotency_key)
        fingerprint = _fingerprint({
            "tenant_id": context.tenant_id, "workspace_id": context.workspace_id,
            "notebook_id": notebook, "source_id": source, "source_version_id": version,
            "expected_etag": expected_etag,
        })
        return self._repository.unbind_source(
            context, notebook, source_id=source, source_version_id=version,
            expected_etag=expected_etag, idempotency_key=key,
            request_fingerprint=fingerprint, now=self._clock(),
        )

    def require_selected_bindings(
        self, context: NotebookContext, notebook_id: object,
        required: tuple[tuple[object, object, object], ...],
    ) -> NotebookSelectedContext:
        notebook = _binding(notebook_id, field="NOTEBOOK_ID")
        selected = self._repository.read_selected_context(context, notebook)
        available = {
            *(("source", source_id, version_id) for source_id, version_id in selected.sources),
            *(("knowledge_context", value, None) for value in selected.knowledge_context_ids),
            *(("conversation_thread", value, None) for value in selected.conversation_thread_ids),
            *(("studio_output", value, None) for value in selected.studio_output_ids),
            *(("output_version", value, None) for value in selected.output_version_ids),
            *(("generation_settings", value, None) for value in selected.generation_settings_ids),
        }
        expected = {_binding_values(kind, record, version) for kind, record, version in required}
        if not expected.issubset(available):
            raise NotebookError("NOTEBOOK_SCOPE_MISMATCH", 409)
        return selected


class ReferenceNotebookRepository:
    """Process-local test adapter; production uses PostgreSQL."""

    creation_license_authoritative = False

    def __init__(
        self, *, binding_targets: set[tuple[str, str, str | None]] | None = None,
    ) -> None:
        self._lock = RLock()
        self._notebooks: dict[tuple[str, str, str], Notebook] = {}
        self._metadata: dict[tuple[str, str, str], list[_Metadata]] = {}
        self._idempotency: dict[tuple[str, str, str, str], tuple[str, NotebookHomeView]] = {}
        self._binding_targets = frozenset(binding_targets or set())
        self._bindings: dict[tuple[str, str, str], list[NotebookBinding]] = {}
        self._activities: dict[tuple[str, str, str], list[NotebookActivity]] = {}
        self._binding_terminations: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
        self._unbind_idempotency: dict[tuple[str, str, str, str], tuple[str, NotebookBindingChangeView]] = {}

    @staticmethod
    def _scope(context: NotebookContext, notebook_id: str) -> tuple[str, str, str]:
        return context.tenant_id, context.workspace_id, notebook_id

    @staticmethod
    def _view(notebook: Notebook, metadata: _Metadata) -> NotebookHomeView:
        return NotebookHomeView(
            notebook.notebook_id, metadata.title, 0, 0, _iso(metadata.updated_at), "empty",
            f'"notebook:{metadata.version}"',
        )

    def create(self, context: NotebookContext, *, title: str, description: str | None, idempotency_key: str, request_fingerprint: str, now: datetime) -> tuple[NotebookHomeView, bool]:
        idem_scope = (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        with self._lock:
            replay = self._idempotency.get(idem_scope)
            if replay is not None:
                if replay[0] != request_fingerprint:
                    raise NotebookError("IDEMPOTENCY_KEY_REUSED", 409)
                return replay[1], True
            notebook_id = "notebook-" + hashlib.sha256("|".join(idem_scope).encode()).hexdigest()[:32]
            notebook = Notebook(notebook_id, context.tenant_id, context.workspace_id, context.actor_id, now)
            metadata = _Metadata(1, title, description, now, context.actor_id)
            scope = self._scope(context, notebook_id)
            self._notebooks[scope] = notebook
            self._metadata[scope] = [metadata]
            self._bindings[scope] = []
            self._activities[scope] = [NotebookActivity(
                context.tenant_id, context.workspace_id, notebook_id,
                "created", now, context.actor_id,
            )]
            self._binding_terminations[scope] = set()
            view = self._view(notebook, metadata)
            self._idempotency[idem_scope] = (request_fingerprint, view)
            return view, False

    def list(self, context: NotebookContext) -> tuple[NotebookHomeView, ...]:
        with self._lock:
            values = [
                self._view(notebook, self._metadata[scope][-1])
                for scope, notebook in self._notebooks.items()
                if scope[:2] == (context.tenant_id, context.workspace_id)
            ]
        return tuple(sorted(values, key=lambda item: (item.updated_at, item.notebook_id), reverse=True))

    def get(self, context: NotebookContext, notebook_id: str) -> NotebookHomeView:
        scope = self._scope(context, notebook_id)
        with self._lock:
            notebook = self._notebooks.get(scope)
            if notebook is None:
                raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
            return self._view(notebook, self._metadata[scope][-1])

    def update_title(self, context: NotebookContext, notebook_id: str, *, title: str, expected_etag: str, idempotency_key: str, request_fingerprint: str, now: datetime) -> tuple[NotebookHomeView, bool]:
        idem_scope = (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        with self._lock:
            replay = self._idempotency.get(idem_scope)
            if replay is not None:
                if replay[0] != request_fingerprint:
                    raise NotebookError("IDEMPOTENCY_KEY_REUSED", 409)
                return replay[1], True
            scope = self._scope(context, notebook_id)
            notebook = self._notebooks.get(scope)
            if notebook is None:
                raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
            current = self._metadata[scope][-1]
            if expected_etag != f'"notebook:{current.version}"':
                raise NotebookError("NOTEBOOK_ETAG_MISMATCH", 412)
            metadata = _Metadata(current.version + 1, title, current.description, now, context.actor_id)
            self._metadata[scope].append(metadata)
            view = self._view(notebook, metadata)
            self._idempotency[idem_scope] = (request_fingerprint, view)
            return view, False

    def bind_verified(self, context: NotebookContext, notebook_id: str, *, binding_kind: str, record_id: str, version_id: str | None, now: datetime) -> bool:
        scope = self._scope(context, notebook_id)
        with self._lock:
            if scope not in self._notebooks:
                raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
            target = (binding_kind, record_id, version_id)
            if target not in self._binding_targets:
                raise NotebookError("NOTEBOOK_BINDING_TARGET_NOT_FOUND", 404)
            binding = NotebookBinding(
                context.tenant_id, context.workspace_id, notebook_id,
                binding_kind, record_id, version_id,
            )
            if binding in self._bindings[scope]:
                return True
            self._bindings[scope].append(binding)
            self._activities[scope].append(NotebookActivity(
                context.tenant_id, context.workspace_id, notebook_id,
                "context_bound", now, context.actor_id,
            ))
            return False

    def read_selected_context(self, context: NotebookContext, notebook_id: str) -> NotebookSelectedContext:
        scope = self._scope(context, notebook_id)
        with self._lock:
            if scope not in self._notebooks:
                raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
            ended = self._binding_terminations[scope]
            bindings = tuple(item for item in self._bindings[scope] if (item.record_id, item.version_id or "") not in ended)
        return _selected_context(notebook_id, bindings)

    def unbind_source(self, context: NotebookContext, notebook_id: str, *, source_id: str, source_version_id: str, expected_etag: str, idempotency_key: str, request_fingerprint: str, now: datetime) -> tuple[NotebookBindingChangeView, bool]:
        scope = self._scope(context, notebook_id)
        idem_scope = (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key)
        with self._lock:
            replay = self._unbind_idempotency.get(idem_scope)
            if replay is not None:
                if replay[0] != request_fingerprint:
                    raise NotebookError("IDEMPOTENCY_KEY_REUSED", 409)
                return replay[1], True
            if scope not in self._notebooks:
                raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
            target = (source_id, source_version_id)
            active = any(item.binding_kind == "source" and (item.record_id, item.version_id) == target for item in self._bindings[scope]) and target not in self._binding_terminations[scope]
            if not active:
                raise NotebookError("NOTEBOOK_BINDING_NOT_FOUND", 404)
            current_version = 1 + len(self._binding_terminations[scope])
            if expected_etag != f'"notebook-binding:{current_version}"':
                raise NotebookError("NOTEBOOK_BINDING_ETAG_MISMATCH", 412)
            self._binding_terminations[scope].add(target)
            view = NotebookBindingChangeView(notebook_id, source_id, source_version_id, "unbound", f'"notebook-binding:{current_version + 1}"')
            self._unbind_idempotency[idem_scope] = (request_fingerprint, view)
            self._activities[scope].append(NotebookActivity(context.tenant_id, context.workspace_id, notebook_id, "context_unbound", now, context.actor_id))
            return view, False
