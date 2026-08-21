"""Resumable Notebook deletion worker boundary.

The worker owns destructive cleanup; callers provide a scoped repository and
object deleter so the API never receives direct database/object-store access.
"""

from dataclasses import dataclass
from typing import Callable, Protocol

from .notebook import NotebookContext, NotebookDeletionView, NotebookError


class NotebookDeletionStore(Protocol):
    def load_pending(self, context: NotebookContext) -> tuple[NotebookDeletionView, ...]: ...
    def delete_scoped(self, context: NotebookContext, request_id: str) -> None: ...
    def mark_completed(self, context: NotebookContext, request_id: str) -> NotebookDeletionView: ...
    def mark_failed(self, context: NotebookContext, request_id: str, code: str) -> NotebookDeletionView: ...


@dataclass(frozen=True, slots=True)
class NotebookDeletionResult:
    request_id: str
    status: str
    safe_error_code: str | None = None


class NotebookDeletionWorker:
    def __init__(self, store: NotebookDeletionStore, *, object_delete: Callable[[str], None] | None = None) -> None:
        self._store = store
        self._object_delete = object_delete

    def process(self, context: NotebookContext, request_id: str) -> NotebookDeletionResult:
        try:
            self._store.delete_scoped(context, request_id)
            view = self._store.mark_completed(context, request_id)
            return NotebookDeletionResult(view.request_id, view.state, view.safe_error_code)
        except NotebookError as error:
            view = self._store.mark_failed(context, request_id, error.code)
            return NotebookDeletionResult(view.request_id, view.state, view.safe_error_code)

    def resume_pending(self, context: NotebookContext) -> int:
        count = 0
        for request in self._store.load_pending(context):
            self.process(context, request.request_id)
            count += 1
        return count
