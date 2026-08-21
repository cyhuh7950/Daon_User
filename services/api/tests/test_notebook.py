from __future__ import annotations

from datetime import datetime, timezone

import pytest

from daon_user_api.notebook import (
    NotebookContext,
    NotebookError,
    NotebookService,
    ReferenceNotebookRepository,
)
from daon_user_api.notebook_postgres import PostgresNotebookRepository


NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)


def _context(workspace_id: str = "workspace-001") -> NotebookContext:
    return NotebookContext("tenant-001", workspace_id, "user-001", "trace-001", "policy-v1")


def test_notebook_create_replay_metadata_versions_and_safe_projection() -> None:
    service = NotebookService(ReferenceNotebookRepository(), clock=lambda: NOW)
    created, replay = service.create(
        _context(), title="  전략\u3000노트  ", description=" 선택 설명 ",
        idempotency_key="notebook-create-0001",
    )
    same, second_replay = service.create(
        _context(), title="전략 노트", description="선택 설명",
        idempotency_key="notebook-create-0001",
    )
    assert created == same
    assert replay is False and second_replay is True
    assert created.title == "전략 노트"
    assert created.source_count == created.output_count == 0
    assert created.status == "empty"
    assert created.etag == '"notebook:1"'
    assert not hasattr(created, "description")

    updated, update_replay = service.update_title(
        _context(), created.notebook_id, title="전략 노트 2",
        expected_etag=created.etag, idempotency_key="notebook-title-0001",
    )
    same_update, repeated = service.update_title(
        _context(), created.notebook_id, title="전략 노트 2",
        expected_etag=created.etag, idempotency_key="notebook-title-0001",
    )
    assert updated == same_update
    assert update_replay is False and repeated is True
    assert updated.etag == '"notebook:2"'
    assert service.get(_context(), created.notebook_id) == updated
    assert service.list(_context()) == (updated,)


def test_notebook_scope_validation_conflict_and_no_default() -> None:
    service = NotebookService(ReferenceNotebookRepository(), clock=lambda: NOW)
    assert service.list(_context()) == ()
    created, _ = service.create(
        _context(), title="노트", description=None, idempotency_key="notebook-create-0002",
    )
    assert service.list(_context("workspace-002")) == ()
    with pytest.raises(NotebookError, match="NOTEBOOK_NOT_FOUND"):
        service.get(_context("workspace-002"), created.notebook_id)
    with pytest.raises(NotebookError, match="IDEMPOTENCY_KEY_REUSED"):
        service.create(
            _context(), title="다른 노트", description=None,
            idempotency_key="notebook-create-0002",
        )
    with pytest.raises(NotebookError, match="NOTEBOOK_ETAG_MISMATCH"):
        service.update_title(
            _context(), created.notebook_id, title="변경", expected_etag='"notebook:99"',
            idempotency_key="notebook-title-0002",
        )


@pytest.mark.parametrize("title", ["", "   ", "x" * 121, "제목\n삽입"])
def test_notebook_title_bounds_fail_close(title: str) -> None:
    service = NotebookService(ReferenceNotebookRepository(), clock=lambda: NOW)
    with pytest.raises(NotebookError, match="NOTEBOOK_TITLE_INVALID"):
        service.create(
            _context(), title=title, description=None,
            idempotency_key="notebook-create-invalid",
        )


def test_postgres_replay_selects_saved_metadata_version_not_only_current() -> None:
    sql = PostgresNotebookRepository._select(current_only=False)
    assert "m.is_current=true" not in sql


def test_notebook_verified_bindings_project_selected_context_and_new_notebook_is_empty() -> None:
    targets = {
        ("source", "source-1", "source-version-1"),
        ("knowledge_context", "scope-snapshot-1", None),
        ("conversation_thread", "conversation-1", None),
        ("studio_output", "studio-output-1", None),
        ("output_version", "output-version-1", None),
        ("generation_settings", "generation-settings-1", None),
    }
    service = NotebookService(ReferenceNotebookRepository(binding_targets=targets), clock=lambda: NOW)
    existing, _ = service.create(
        _context(), title="기존 Notebook", description=None,
        idempotency_key="notebook-context-create-0001",
    )
    empty, _ = service.create(
        _context(), title="새 Notebook", description=None,
        idempotency_key="notebook-context-create-0002",
    )
    for kind, record_id, version_id in sorted(targets):
        service.bind_verified(
            _context(), existing.notebook_id, binding_kind=kind,
            record_id=record_id, version_id=version_id,
        )
    selected = service.read_selected_context(_context(), existing.notebook_id)
    assert selected.notebook_id == existing.notebook_id
    assert selected.sources == (("source-1", "source-version-1"),)
    assert selected.knowledge_context_ids == ("scope-snapshot-1",)
    assert selected.conversation_thread_ids == ("conversation-1",)
    assert selected.studio_output_ids == ("studio-output-1",)
    assert selected.output_version_ids == ("output-version-1",)
    assert selected.generation_settings_ids == ("generation-settings-1",)
    assert service.read_selected_context(_context(), empty.notebook_id).is_empty is True
    with pytest.raises(NotebookError, match="NOTEBOOK_NOT_FOUND"):
        service.read_selected_context(_context("workspace-002"), existing.notebook_id)


def test_notebook_binding_rejects_unverified_target_and_cross_notebook_scope() -> None:
    service = NotebookService(
        ReferenceNotebookRepository(binding_targets={("source", "source-1", "source-version-1")}),
        clock=lambda: NOW,
    )
    notebook, _ = service.create(
        _context(), title="Notebook", description=None,
        idempotency_key="notebook-context-create-0003",
    )
    with pytest.raises(NotebookError, match="NOTEBOOK_BINDING_TARGET_NOT_FOUND"):
        service.bind_verified(
            _context(), notebook.notebook_id, binding_kind="source",
            record_id="source-1", version_id="source-version-other",
        )


def test_notebook_source_unbind_is_append_only_idempotent_and_immediately_unselected() -> None:
    service = NotebookService(
        ReferenceNotebookRepository(binding_targets={("source", "source-1", "source-version-1")}),
        clock=lambda: NOW,
    )
    notebook, _ = service.create(
        _context(), title="Notebook", description=None,
        idempotency_key="notebook-unbind-create-0001",
    )
    service.bind_verified(
        _context(), notebook.notebook_id, binding_kind="source",
        record_id="source-1", version_id="source-version-1",
    )
    view, replayed = service.unbind_source(
        _context(), notebook.notebook_id, source_id="source-1",
        source_version_id="source-version-1", expected_etag='"notebook-binding:1"',
        idempotency_key="notebook-unbind-source-0001",
    )
    assert replayed is False
    assert view.status == "unbound"
    assert view.etag == '"notebook-binding:2"'
    assert service.read_selected_context(_context(), notebook.notebook_id).sources == ()
    replay, replayed = service.unbind_source(
        _context(), notebook.notebook_id, source_id="source-1",
        source_version_id="source-version-1", expected_etag='"notebook-binding:1"',
        idempotency_key="notebook-unbind-source-0001",
    )
    assert replayed is True
    assert replay == view
    with pytest.raises(NotebookError, match="IDEMPOTENCY_KEY_REUSED"):
        service.unbind_source(
            _context(), notebook.notebook_id, source_id="source-other",
            source_version_id="source-version-1", expected_etag='"notebook-binding:1"',
            idempotency_key="notebook-unbind-source-0001",
        )
    with pytest.raises(NotebookError, match="NOTEBOOK_NOT_FOUND"):
        service.bind_verified(
            _context("workspace-002"), notebook.notebook_id, binding_kind="source",
            record_id="source-1", version_id="source-version-1",
        )


def test_notebook_scope_requires_every_selected_binding_and_fails_closed() -> None:
    service = NotebookService(
        ReferenceNotebookRepository(binding_targets={
            ("source", "source-1", "source-version-1"),
            ("studio_output", "output-1", None),
        }),
        clock=lambda: NOW,
    )
    notebook, _ = service.create(
        _context(), title="Scoped", description=None,
        idempotency_key="notebook-scope-create-0001",
    )
    service.bind_verified(
        _context(), notebook.notebook_id, binding_kind="source",
        record_id="source-1", version_id="source-version-1",
    )
    service.require_selected_bindings(
        _context(), notebook.notebook_id,
        (("source", "source-1", "source-version-1"),),
    )
    with pytest.raises(NotebookError, match="NOTEBOOK_SCOPE_MISMATCH"):
        service.require_selected_bindings(
            _context(), notebook.notebook_id,
            (("studio_output", "output-1", None),),
        )
    with pytest.raises(NotebookError, match="NOTEBOOK_NOT_FOUND"):
        service.require_selected_bindings(
            _context("workspace-002"), notebook.notebook_id,
            (("source", "source-1", "source-version-1"),),
        )
