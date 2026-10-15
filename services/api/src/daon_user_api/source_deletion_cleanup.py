"""S16 Source-specific, resumable cleanup; durable repository owns authority."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib

from psycopg import Error as PostgresError
from psycopg.types.json import Jsonb

from .authorization import Action
from .cloud_storage import CloudDatabaseError
from .object_queue import ObjectKeyPolicy, ObjectStorageError
from .open_law_source_access import _current_auth_grant, OpenLawAccessError
from .retention import RetentionError, _fingerprint


class SourceDeletionCleanup:
    def __init__(self, repository, object_storage):
        self._repository = repository
        self._object_storage = object_storage

    def execute(self, context, **request):
        if self._object_storage is None:
            # Do not accept a destructive request with no real cleanup port.
            raise RetentionError("OBJECT_STORAGE_UNAVAILABLE", 503, retryable=True)
        view = self._repository.prepare(context, **request)
        if view.state == "purged":
            return view
        for key in self._repository.pending(context, view.request_id):
            if not self._repository.delete_one(context, view.request_id, key, self._object_storage):
                break
        return self._repository.finish(context, view.request_id)


class PostgresSourceCleanupRepository:
    """A per-request facade: no shared store/transaction monkeypatching.

    Each object attempt retains the same Source lock until its acknowledgment
    commits. A lost process rolls its DB transaction back, not its durable keys.
    """
    _SAFE_ERRORS = frozenset({
        "SOURCE_UNAVAILABLE", "DELETION_REQUEST_UNAVAILABLE", "SOURCE_SHARED_BINDING",
        "SOURCE_RETAINED_REFERENCE", "SOURCE_PROCESSING_ACTIVE", "LEGAL_HOLD_ACTIVE",
        "CURRENT_ACCESS_DENIED", "STEP_UP_REQUIRED", "RETENTION_IDEMPOTENCY_CONFLICT",
        "DELETION_INVENTORY_INVALID", "DELETION_CLEANUP_PENDING", "SOURCE_DELETION_ACTIVE",
    })

    def __init__(self, service, authorization_key):
        self._service = service
        self._authorization_key = authorization_key

    @contextmanager
    def _transaction(self, context):
        try:
            with self._service._store._transaction(self._service._access(context)) as connection:
                try:
                    yield connection
                except PostgresError as error:
                    code = error.diag.message_primary
                    if code in self._SAFE_ERRORS:
                        status = 403 if code in {"CURRENT_ACCESS_DENIED", "STEP_UP_REQUIRED"} else 404 if code in {"SOURCE_UNAVAILABLE", "DELETION_REQUEST_UNAVAILABLE"} else 409
                        raise RetentionError(code, status) from None
                    if error.sqlstate == "23503":
                        raise RetentionError("SOURCE_RETAINED_REFERENCE", 409) from None
                    raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=True) from None
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from None

    @staticmethod
    def _guard(connection, context, notebook_id, versions, *, action=Action.EDIT):
        # Lock the authoritative role/policy roots while evaluating the same
        # AuthorizationService read rules. No fake session/principal is made.
        for table, predicate, params in (
            ("identity_auth_workspaces", "tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)),
            ("identity_auth_tenant_roles", "tenant_id=%s AND user_id=%s", (context.tenant_id, context.actor_id)),
            ("identity_auth_memberships", "tenant_id=%s AND workspace_id=%s AND user_id=%s", (context.tenant_id, context.workspace_id, context.actor_id)),
            ("identity_auth_tenant_policies", "tenant_id=%s", (context.tenant_id,)),
            ("identity_auth_workspace_policies", "tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)),
        ):
            connection.execute(f"SELECT 1 FROM {table} WHERE {predicate} FOR SHARE", params).fetchall()
        try:
            _current_auth_grant(context, connection, action)
        except OpenLawAccessError:
            raise RetentionError("CURRENT_ACCESS_DENIED", 403) from None
        notebook = connection.execute(
            "SELECT 1 FROM notebooks n WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s AND created_by=%s "
            "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
            "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))",
            (context.tenant_id, context.workspace_id, notebook_id, context.actor_id),
        ).fetchone()
        if notebook is None:
            raise RetentionError("CURRENT_ACCESS_DENIED", 403)
        denied = connection.execute(
            "SELECT allowed FROM identity_auth_source_access WHERE tenant_id=%s AND workspace_id=%s "
            "AND user_id=%s AND source_version_id=ANY(%s) FOR SHARE",
            (context.tenant_id, context.workspace_id, context.actor_id, list(versions)),
        ).fetchall()
        if any(not row[0] for row in denied):
            raise RetentionError("CURRENT_ACCESS_DENIED", 403)
        connection.execute("SELECT set_config('app.notebook_id',%s,true)", (notebook_id,))

    def _scope(self, connection, context, request_id, *, action=Action.EDIT):
        scope = connection.execute(
            "SELECT source_id,notebook_id,source_version_ids FROM source_cleanup_requests "
            "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND request_id=%s",
            (context.tenant_id, context.workspace_id, context.actor_id, request_id),
        ).fetchone()
        if scope is None:
            raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
        self._guard(connection, context, scope[1], scope[2], action=action)
        return scope

    def prepare(self, context, *, source_id, notebook_id, idempotency_key):
        fingerprint = _fingerprint((source_id, notebook_id))
        request_id = "deletion-" + hashlib.sha256(
            f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}".encode()
        ).hexdigest()[:28]
        with self._transaction(context) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"notebook-delete|{context.tenant_id}|{context.workspace_id}|{notebook_id}",))
            self._service._lock_source(connection, context, source_id)
            prior = connection.execute(
                "SELECT notebook_id,source_version_ids FROM source_cleanup_requests "
                "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND source_id=%s",
                (context.tenant_id, context.workspace_id, context.actor_id, source_id),
            ).fetchone()
            versions = prior[1] if prior else [row[0] for row in connection.execute(
                "SELECT record_id FROM source_versions WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s",
                (context.tenant_id, context.workspace_id, source_id),
            ).fetchall()]
            self._guard(connection, context, notebook_id, versions)
            # A replay MUST NOT depend on now-deleted source/object inventory.
            inventory = None if prior else [asdict(item) for item in self._service._inventory_provider.read_in_transaction(connection, context, source_id)]
            row = connection.execute("SELECT stage_source_cleanup(%s,%s,%s,%s,%s,%s,%s,%s,%s)", (
                request_id, source_id, notebook_id, context.policy_version, idempotency_key, fingerprint,
                context.trace_id, self._authorization_key, Jsonb(inventory),
            )).fetchone()
            return self._service._view(connection, context, row[0])

    def get_request(self, context, request_id):
        with self._transaction(context) as connection:
            # 0062 remains readable during the approved rolling migration. It
            # cannot create new S16 cleanup requests without the new functions.
            ready = connection.execute("SELECT to_regprocedure('public.read_source_cleanup_scope(text)')").fetchone()[0]
            if ready is not None:
                scope = connection.execute("SELECT read_source_cleanup_scope(%s)", (request_id,)).fetchone()[0]
                if scope is not None:
                    self._guard(connection, context, scope['notebook_id'], scope['source_version_ids'], action=Action.VIEW)
            return self._service._view(connection, context, request_id)

    def pending(self, context, request_id):
        with self._transaction(context) as connection:
            self._scope(connection, context, request_id)
            rows = connection.execute(
                "SELECT object_key FROM source_cleanup_objects WHERE tenant_id=%s AND workspace_id=%s "
                "AND actor_id=%s AND request_id=%s AND state<>'completed' ORDER BY object_key LIMIT 2049",
                (context.tenant_id, context.workspace_id, context.actor_id, request_id),
            ).fetchall()
            if len(rows) > 2048:
                raise RetentionError("DELETION_INVENTORY_INVALID", 409)
            return tuple(row[0] for row in rows)

    def delete_one(self, context, request_id, key, storage):
        with self._transaction(context) as connection:
            self._scope(connection, context, request_id)
            connection.execute("SELECT lock_source_cleanup_request(%s,%s,%s)", (request_id, context.policy_version, self._authorization_key))
            row = connection.execute(
                "SELECT area,key_kind,state FROM source_cleanup_objects WHERE tenant_id=%s AND workspace_id=%s "
                "AND actor_id=%s AND request_id=%s AND object_key=%s",
                (context.tenant_id, context.workspace_id, context.actor_id, request_id, key),
            ).fetchone()
            if row is None:
                raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
            if row[2] == "completed":
                return True
            policy = ObjectKeyPolicy()
            validate = policy.validate_staging if row[1] == "staging" else policy.validate_final
            validate(self._service._access(context), row[0], key)
            error_code = None
            try:
                storage.delete(key)
            except ObjectStorageError as error:
                error_code = error.code if error.code in {"OBJECT_STORAGE_UNAVAILABLE", "OBJECT_STORAGE_ACCESS_DENIED", "OBJECT_KEY_INVALID"} else "OBJECT_STORAGE_UNAVAILABLE"
            connection.execute("SELECT progress_source_cleanup(%s,%s,%s,%s,%s)", (request_id, context.policy_version, self._authorization_key, key, error_code))
            return error_code is None

    def finish(self, context, request_id):
        with self._transaction(context) as connection:
            self._scope(connection, context, request_id)
            connection.execute("SELECT progress_source_cleanup(%s,%s,%s,NULL,NULL)", (request_id, context.policy_version, self._authorization_key))
            return self._service._view(connection, context, request_id)
