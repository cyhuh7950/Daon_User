"""PostgreSQL counts for the safe operations status projection."""

from __future__ import annotations

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .operations_status import OperationsCounts, OperationsStatusContext


class OperationsStatusError(RuntimeError):
    def __init__(self, code: str = "OPERATIONS_STATUS_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


class PostgresOperationsStatusRepository:
    def __init__(self, cloud_store: PostgresCloudStore) -> None:
        self._cloud_store = cloud_store

    def read_counts(self, context: OperationsStatusContext) -> OperationsCounts:
        try:
            with self._cloud_store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "operations.read")) as connection:
                row = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM sync_operations WHERE tenant_id=%s AND workspace_id=%s AND state IN ('preview','awaiting_approval','approved','transferring','conflict')),"
                    "(SELECT count(*) FROM document_processing_jobs WHERE tenant_id=%s AND workspace_id=%s AND state IN ('pending','leased','retry_wait')),"
                    "(SELECT count(*) FROM document_processing_jobs WHERE tenant_id=%s AND workspace_id=%s AND state='dead_letter')",
                    (context.tenant_id, context.workspace_id) * 3,
                ).fetchone()
        except CloudDatabaseError as error:
            raise OperationsStatusError() from error
        if row is None:
            raise OperationsStatusError()
        return OperationsCounts(int(row[0]), int(row[1]), int(row[2]))
