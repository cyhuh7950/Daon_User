from datetime import datetime, timezone

import pytest

from daon_user_api.notebook import NotebookContext, NotebookError, NotebookService, ReferenceNotebookRepository


def service():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return NotebookService(ReferenceNotebookRepository(), clock=lambda: now)


def test_deletion_requires_exact_title_and_etag():
    svc = service(); ctx = NotebookContext("tenant", "workspace", "actor", "trace", "policy")
    view, _ = svc.create(ctx, title="테스트", description=None, idempotency_key="notebook-create-123456")
    with pytest.raises(NotebookError) as mismatch:
        svc.request_deletion(ctx, view.notebook_id, title_confirmation="오타", expected_etag=view.etag, idempotency_key="notebook-delete-123456")
    assert mismatch.value.code == "NOTEBOOK_TITLE_CONFIRMATION_MISMATCH"
    with pytest.raises(NotebookError) as etag:
        svc.request_deletion(ctx, view.notebook_id, title_confirmation="테스트", expected_etag='"notebook:2"', idempotency_key="notebook-delete-123456")
    assert etag.value.code == "NOTEBOOK_ETAG_MISMATCH"


def test_deletion_request_is_idempotent():
    svc = service(); ctx = NotebookContext("tenant", "workspace", "actor", "trace", "policy")
    view, _ = svc.create(ctx, title="테스트", description=None, idempotency_key="notebook-create-654321")
    first, replay = svc.request_deletion(ctx, view.notebook_id, title_confirmation="테스트", expected_etag=view.etag, idempotency_key="notebook-delete-654321")
    second, replayed = svc.request_deletion(ctx, view.notebook_id, title_confirmation="테스트", expected_etag=view.etag, idempotency_key="notebook-delete-654321")
    assert not replay and replayed and first.request_id == second.request_id
