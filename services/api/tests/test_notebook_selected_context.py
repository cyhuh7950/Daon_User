"""S18 repository projection tests; SQL/authorization are not real PostgreSQL here."""

from contextlib import contextmanager
from copy import deepcopy

import pytest

from daon_user_api.notebook import NotebookContext, NotebookError
from daon_user_api.notebook_postgres import PostgresNotebookRepository


def test_s42_exact_selector_source_validation_error_is_safe_not_found(monkeypatch):
    """오류 변환 단위 계약이며 실제 DB 접근 시험이 아니다."""
    from contextlib import nullcontext
    from daon_user_api.notification import NotificationError
    import daon_user_api.notification_events as events
    store = _ReadStore([], [])
    store.responses = [[(1,)], [('completed','conversation-s42',{},'trace-s42')]]
    monkeypatch.setattr(events, '_snapshot_read_scope', lambda connection, context:nullcontext(connection))
    def reject(*args, **kwargs):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 400)
    monkeypatch.setattr(events, '_completed_event_access', reject)
    with pytest.raises(NotebookError) as failure:
        PostgresNotebookRepository(store).read_selected_context(
            NotebookContext('tenant-s42','workspace-s42','actor-s42','trace-s42','policy-s42'),
            'notebook-s42', run_id='run-s42')
    assert (failure.value.code,failure.value.status)==('NOTEBOOK_NOT_FOUND',404)


class _ReadRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _ReadStore:
    """Only substitutes the DB boundary; runs the real repository projection."""

    def __init__(self, sources, citations, *, insufficient=False):
        bindings = [("source", source, version) for source, version in sources] + [
            ("conversation_thread", "conversation-s18", None),
            ("studio_output", "output-s18", None),
            ("output_version", "output-version-s18", None),
            ("generation_settings", "settings-s18", None),
        ]
        result = {"answer": "과거 원문 답변", "insufficient": insufficient}
        conversation = [("conversation-s18", "run-s18", "result-s18", result,
                         f"citation-{index}", item) for index, item in enumerate(citations)]
        if not conversation:
            conversation = [("conversation-s18", "run-s18", "result-s18", result, None, None)]
        # The production SQL already returns only this Notebook's active bindings.
        self.responses = [[(1,)], bindings, conversation, [(2,)], [(None,)], []]
        self.calls = []
        self.scopes = []

    @contextmanager
    def _transaction(self, context):
        self.scopes.append(context)
        yield self

    def execute(self, query, params=()):
        assert query.lstrip().split()[0] == "SELECT", "projection must never mutate history"
        self.calls.append(tuple(params))
        return _ReadRows(self.responses[len(self.calls) - 1])


def _citation(source="source-s18", version="version-s18", **extra):
    return {"source_id": source, "source_version_id": version,
            "evidence_span_id": "span-s18", "page": 2, "origin": "raw_source",
            "context_item_id": source, "locator": {"kind": "page", "value": "2"}, **extra}


@pytest.mark.parametrize("sources,citations,restore", [
    ([], [_citation()], False),
    ([("source-s18", "version-new")], [_citation()], False),
    ([("other-source", "version-s18")], [_citation()], False),
    ([("source-s18", "version-s18")], [_citation(), _citation("source-unbound", "version-unbound")], False),
    ([("source-s18", "version-s18")], [_citation()], True),
    ([], [], True),
    ([], [_citation(origin="daon_knowledge", context_item_id="package-s18")], True),
    ([("source-s18", "version-s18")], [_citation(), _citation("daon-source", "daon-version", origin="daon_knowledge", context_item_id="package-s18")], True),
], ids=["unbound-pdf", "different-version", "different-source", "one-of-many-unbound",
        "bound-pdf", "source-free", "daon-only", "bound-raw-plus-daon"])
def test_s18_selected_context_current_raw_binding(sources, citations, restore):
    store = _ReadStore(sources, citations)
    before = deepcopy(store.responses)
    context = NotebookContext("tenant-s18", "workspace-s18", "actor-s18", "trace-s18", "policy-s18")
    selected = PostgresNotebookRepository(store).read_selected_context(context, "notebook-s18")
    assert (selected.conversation is not None) is restore
    assert selected.sources == tuple(sorted(sources))
    assert selected.studio_output_ids == ("output-s18",)
    assert selected.output_version_ids == ("output-version-s18",)
    assert selected.generation_settings_ids == ("settings-s18",)
    assert selected.conversation_thread_ids == ("conversation-s18",)
    assert selected.etag == '"notebook-binding:3"'
    if restore:
        assert selected.conversation.answer == "과거 원문 답변"
        assert len(selected.conversation.citations) == len(citations)
    assert store.responses == before, "stored result/citations/bindings must remain unchanged"
    assert store.calls[:3] == [("tenant-s18", "workspace-s18", "notebook-s18")] * 3
    assert len(store.calls) == 6
    assert (store.scopes[0].tenant_id, store.scopes[0].workspace_id, store.scopes[0].actor_id) == (
        "tenant-s18", "workspace-s18", "actor-s18")


def test_s18_selected_context_legacy_raw_origin_and_insufficient_general():
    legacy = _citation()
    del legacy["origin"]
    context = NotebookContext("tenant-s18", "workspace-s18", "actor-s18", "trace-s18", "policy-s18")
    assert PostgresNotebookRepository(_ReadStore([], [legacy])).read_selected_context(context, "notebook-s18").conversation is None
    selected = PostgresNotebookRepository(_ReadStore([], [], insufficient=True)).read_selected_context(context, "notebook-s18")
    assert selected.conversation.insufficient is True
    assert selected.conversation.citations == ()
