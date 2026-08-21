"""Resumable Notebook deletion worker boundary.

The worker owns destructive cleanup; callers provide a scoped repository and
object deleter so the API never receives direct database/object-store access.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .notebook import NotebookContext, NotebookDeletionView, NotebookError
from .object_queue import ObjectStorageError, ObjectStoragePort


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

    def resume_startup(self) -> int:
        """Optional lifecycle hook; deployment workers may inject a scoped resumer."""
        resume = getattr(self._store, "resume_startup", None)
        if not callable(resume):
            return 0
        return int(resume())


class PostgresNotebookDeletionStore:
    """Durable deletion store using the migration-owned scoped SQL function."""

    def __init__(self, store: PostgresCloudStore, object_storage: ObjectStoragePort | None = None) -> None:
        self._store = store
        self._object_storage = object_storage

    @staticmethod
    def _access(context: NotebookContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "notebook.delete",
        )

    @staticmethod
    def _view(row) -> NotebookDeletionView:  # type: ignore[no-untyped-def]
        return NotebookDeletionView(
            str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]),
            None if row[5] is None else str(row[5]), row[6], row[7],
        )

    def load_pending(self, context: NotebookContext) -> tuple[NotebookDeletionView, ...]:
        try:
            with self._store._transaction(self._access(context)) as connection:
                rows = connection.execute(
                    "SELECT request_id,notebook_id,state,current_step,attempts,safe_error_code,requested_at,completed_at "
                    "FROM notebook_deletion_requests WHERE tenant_id=%s AND workspace_id=%s "
                    "AND state IN ('accepted','deleting') ORDER BY requested_at,request_id",
                    (context.tenant_id, context.workspace_id),
                ).fetchall()
            return tuple(self._view(row) for row in rows)
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def delete_scoped(self, context: NotebookContext, request_id: str) -> None:
        object_keys: list[str] = []
        try:
            with self._store._transaction(self._access(context)) as connection:
                request = connection.execute(
                    "SELECT notebook_id,state FROM notebook_deletion_requests "
                    "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s FOR UPDATE",
                    (context.tenant_id, context.workspace_id, request_id),
                ).fetchone()
                if request is None:
                    raise NotebookError("NOTEBOOK_DELETION_NOT_FOUND", 404)
                if str(request[1]) == "completed":
                    return
                connection.execute(
                    "UPDATE notebook_deletion_requests SET state='deleting',current_step='database',attempts=attempts+1 "
                    "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                    (context.tenant_id, context.workspace_id, request_id),
                )
                rows = connection.execute(
                    "SELECT object_key FROM delete_notebook_scope(%s,%s,%s) WHERE object_key IS NOT NULL",
                    (context.tenant_id, context.workspace_id, str(request[0])),
                ).fetchall()
                object_keys = [str(row[0]) for row in rows]
                connection.execute(
                    "UPDATE notebook_deletion_requests SET current_step='objects' "
                    "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                    (context.tenant_id, context.workspace_id, request_id),
                )
            if self._object_storage is not None:
                for key in object_keys:
                    self._object_storage.delete(key)
        except NotebookError:
            raise
        except ObjectStorageError as error:
            raise NotebookError(error.code, 503 if error.retryable else 409) from error
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def mark_completed(self, context: NotebookContext, request_id: str) -> NotebookDeletionView:
        now = datetime.now(timezone.utc)
        try:
            with self._store._transaction(self._access(context)) as connection:
                row = connection.execute(
                    "UPDATE notebook_deletion_requests SET state='completed',current_step='completed',completed_at=%s "
                    "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s "
                    "RETURNING request_id,notebook_id,state,current_step,attempts,safe_error_code,requested_at,completed_at",
                    (now, context.tenant_id, context.workspace_id, request_id),
                ).fetchone()
                if row is None:
                    raise NotebookError("NOTEBOOK_DELETION_NOT_FOUND", 404)
                return self._view(row)
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def mark_failed(self, context: NotebookContext, request_id: str, code: str) -> NotebookDeletionView:
        try:
            with self._store._transaction(self._access(context)) as connection:
                row = connection.execute(
                    "UPDATE notebook_deletion_requests SET state='failed',current_step='failed',safe_error_code=%s "
                    "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s "
                    "RETURNING request_id,notebook_id,state,current_step,attempts,safe_error_code,requested_at,completed_at",
                    (code, context.tenant_id, context.workspace_id, request_id),
                ).fetchone()
                if row is None:
                    raise NotebookError("NOTEBOOK_DELETION_NOT_FOUND", 404)
                return self._view(row)
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error
