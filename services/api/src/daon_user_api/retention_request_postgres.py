"""Durable request/get/cancel boundary for the approved retention routes."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .object_queue import ObjectStorageError, ObjectStoragePort
from .retention import CleanupItemView, DeletionRequestView, LegalHoldView, RetentionContext, RetentionError, _fingerprint


class PostgresRetentionRequestService:
    def __init__(self, store: PostgresCloudStore, inventory_provider, *, clock, fixture_purge: bool = False, object_storage: ObjectStoragePort | None = None) -> None:  # type: ignore[no-untyped-def]
        self._store = store
        self._inventory_provider = inventory_provider
        self._clock = clock
        self._fixture_purge = fixture_purge
        self._object_storage = object_storage

    @staticmethod
    def _access(context: RetentionContext) -> CloudAccessContext:
        return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "retention.request")

    @staticmethod
    def _lock_source(connection, context: RetentionContext, source_id: str) -> None:  # type: ignore[no-untyped-def]
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"retention-source|{context.tenant_id}|{context.workspace_id}|{source_id}",),
        )

    @staticmethod
    def _request_source(connection, context: RetentionContext, request_id: str) -> str:  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT source_id FROM deletion_request_locator WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
            (context.tenant_id, context.workspace_id, request_id),
        ).fetchone()
        if row is None:
            raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
        return str(row[0])

    @staticmethod
    def _hold_source(connection, context: RetentionContext, hold_id: str) -> str:  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT source_id FROM legal_hold_locator WHERE tenant_id=%s AND workspace_id=%s AND hold_id=%s",
            (context.tenant_id, context.workspace_id, hold_id),
        ).fetchone()
        if row is None:
            raise RetentionError("LEGAL_HOLD_UNAVAILABLE", 404)
        return str(row[0])

    def locate_source_workspace(self, tenant_id: str, source_id: str) -> str:
        try:
            access = CloudAccessContext(tenant_id, tenant_id, tenant_id, "retention.locate")
            with self._store._transaction(access) as connection:
                rows = connection.execute(
                    "SELECT workspace_id FROM source_retention_locator WHERE tenant_id=%s AND source_id=%s ORDER BY workspace_id LIMIT 2",
                    (tenant_id, source_id),
                ).fetchall()
            if len(rows) != 1:
                raise RetentionError("SOURCE_UNAVAILABLE", 404)
            return str(rows[0][0])
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def locate_workspace(self, tenant_id: str, request_id: str) -> str:
        try:
            access = CloudAccessContext(tenant_id, tenant_id, tenant_id, "retention.locate")
            with self._store._transaction(access) as connection:
                row = connection.execute(
                    "SELECT workspace_id FROM deletion_request_locator WHERE tenant_id=%s AND request_id=%s",
                    (tenant_id, request_id),
                ).fetchone()
            if row is None:
                raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
            return str(row[0])
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def locate_hold_workspace(self, tenant_id: str, hold_id: str) -> str:
        try:
            access = CloudAccessContext(tenant_id, tenant_id, tenant_id, "retention.locate")
            with self._store._transaction(access) as connection:
                row = connection.execute(
                    "SELECT workspace_id FROM legal_hold_locator WHERE tenant_id=%s AND hold_id=%s",
                    (tenant_id, hold_id),
                ).fetchone()
            if row is None:
                raise RetentionError("LEGAL_HOLD_UNAVAILABLE", 404)
            return str(row[0])
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def source_etag(self, tenant_id: str, source_id: str) -> tuple[str, int]:
        workspace_id = self.locate_source_workspace(tenant_id, source_id)
        context = RetentionContext(tenant_id, workspace_id, tenant_id, tenant_id, "retention-policy")
        try:
            with self._store._transaction(self._access(context)) as connection:
                row = connection.execute(
                    "SELECT version FROM deletion_requests WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s",
                    (tenant_id, workspace_id, source_id),
                ).fetchone()
            version = 1 if row is None else int(row[0])
            return f'"source:{source_id}:{version}"', version
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    @staticmethod
    def _require_sensitive(context: RetentionContext, verified: bool) -> None:
        if not context.organization_admin:
            raise RetentionError("CURRENT_ACCESS_DENIED", 403)
        if not verified:
            raise RetentionError("STEP_UP_REQUIRED", 403)

    @staticmethod
    def _hold_view(connection, context: RetentionContext, hold_id: str) -> LegalHoldView:  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT source_id,state,version FROM legal_holds WHERE tenant_id=%s AND workspace_id=%s AND hold_id=%s",
            (context.tenant_id, context.workspace_id, hold_id),
        ).fetchone()
        if row is None:
            raise RetentionError("LEGAL_HOLD_UNAVAILABLE", 404)
        return LegalHoldView(hold_id, context.tenant_id, context.workspace_id, str(row[0]), str(row[1]), int(row[2]))

    @staticmethod
    def _sensitive_replay(connection, context: RetentionContext, action: str, key: str, fingerprint: str):  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT request_fingerprint,result_id FROM retention_sensitive_idempotency WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND action=%s AND idempotency_key=%s",
            (context.tenant_id, context.workspace_id, context.actor_id, action, key),
        ).fetchone()
        if row is None:
            return None
        if str(row[0]) != fingerprint:
            raise RetentionError("RETENTION_IDEMPOTENCY_CONFLICT")
        return str(row[1])

    @staticmethod
    def _view(connection, context: RetentionContext, request_id: str) -> DeletionRequestView:  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT source_id,state,version,source_active,grace_until,created_at FROM deletion_requests "
            "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
            (context.tenant_id, context.workspace_id, request_id),
        ).fetchone()
        if row is None:
            raise RetentionError("DELETION_REQUEST_UNAVAILABLE", 404)
        items = connection.execute(
            "SELECT derivative_kind,reference_id,state,attempt_count,inventory_disposition FROM deletion_cleanup_items "
            "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s ORDER BY derivative_kind,reference_id",
            (context.tenant_id, context.workspace_id, request_id),
        ).fetchall()
        cleanup = tuple(CleanupItemView(str(item[0]), str(item[1]), str(item[2]), int(item[3]), str(item[4])) for item in items)
        return DeletionRequestView(
            request_id, context.tenant_id, context.workspace_id, str(row[0]), str(row[1]), int(row[2]),
            row[5], row[4], bool(row[3]), cleanup,
            tuple(item.reference_id for item in cleanup if item.state == "completed"),
        )

    def create_request(self, context: RetentionContext, *, source_id: str, inventory, idempotency_key: str, if_match: str):  # type: ignore[no-untyped-def]
        if inventory is not None or if_match != "*":
            raise RetentionError("DELETION_REQUEST_INVALID", 400)
        now = self._clock()
        request_id = "deletion-" + hashlib.sha256(f"{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:28]
        object_keys: list[str] = []
        try:
            with self._store._transaction(self._access(context)) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"retention-create|{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}",))
                self._lock_source(connection, context, source_id)
                derived = self._inventory_provider.read_in_transaction(connection, context, source_id)
                if len(derived) != 6 or len({item.kind for item in derived}) != 6:
                    raise RetentionError("DELETION_INVENTORY_INVALID", 400)
                fingerprint = _fingerprint((source_id, derived))
                replay = connection.execute(
                    "SELECT request_fingerprint,request_id FROM retention_request_idempotency WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND action='create' AND idempotency_key=%s",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise RetentionError("RETENTION_IDEMPOTENCY_CONFLICT")
                    return self._view(connection, context, str(replay[1]))
                active_request = connection.execute(
                    "SELECT 1 FROM deletion_requests WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s "
                    "AND state NOT IN ('cancelled','purged') LIMIT 1",
                    (context.tenant_id, context.workspace_id, source_id),
                ).fetchone()
                if active_request is not None:
                    raise RetentionError("DELETION_REQUEST_ACTIVE")
                # User-confirmed Source deletion is immediate. Legal-hold/grace
                # transitions belong to the retired retention workflow and are
                # intentionally not part of this Notebook Source contract.
                object_keys = [str(row[0]) for row in connection.execute(
                    "SELECT object_key FROM delete_source_scope(%s,%s,%s) WHERE object_key IS NOT NULL",
                    (context.tenant_id, context.workspace_id, source_id),
                ).fetchall()]
                connection.execute(
                    "INSERT INTO deletion_request_locator (tenant_id,request_id,workspace_id,source_id,created_at) VALUES (%s,%s,%s,%s,%s)",
                    (context.tenant_id, request_id, context.workspace_id, source_id, now),
                )
                connection.execute(
                    "INSERT INTO deletion_requests (tenant_id,workspace_id,request_id,source_id,actor_id,state,version,source_active,purge_started,grace_until,policy_version,idempotency_key,request_fingerprint,trace_id,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,'purged',1,false,true,%s,%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, request_id, source_id, context.actor_id, now, context.policy_version, idempotency_key, fingerprint, context.trace_id, now, now),
                )
                for item in derived:
                    state = "completed"
                    connection.execute(
                        "INSERT INTO deletion_cleanup_items (tenant_id,workspace_id,request_id,reference_id,derivative_kind,state,acknowledgement_required,attempt_count,inventory_disposition,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s)",
                        (context.tenant_id, context.workspace_id, request_id, item.reference_id, item.kind, state, item.acknowledgement_required, item.disposition, now),
                    )
                connection.execute(
                    "INSERT INTO retention_request_idempotency (tenant_id,workspace_id,actor_id,action,idempotency_key,request_fingerprint,request_id,result_version,created_at) VALUES (%s,%s,%s,'create',%s,%s,%s,1,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, fingerprint, request_id, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) VALUES (%s,%s,%s,%s,'deletion.requested','source',%s,'succeeded',%s,%s,%s,%s)",
                    ("retention-audit-" + hashlib.sha256(f"delete|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24], context.tenant_id, context.workspace_id, context.actor_id, source_id, context.trace_id, context.policy_version, Jsonb({"request_id": request_id, "state": "purged"}), Jsonb({"inventory_count": 6, "immediate": True})),
                )
                view = self._view(connection, context, request_id)
            if self._object_storage is not None:
                for key in object_keys:
                    self._object_storage.delete(key)
            return view
        except ObjectStorageError as error:
            raise RetentionError(error.code, 503 if error.retryable else 409) from error
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def get_request(self, context: RetentionContext, request_id: str) -> DeletionRequestView:
        try:
            with self._store._transaction(self._access(context)) as connection:
                return self._view(connection, context, request_id)
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def cancel(self, context: RetentionContext, request_id: str, *, expected_version: int, idempotency_key: str) -> DeletionRequestView:
        fingerprint = _fingerprint((request_id, expected_version))
        now = self._clock()
        try:
            with self._store._transaction(self._access(context)) as connection:
                source_id = self._request_source(connection, context, request_id)
                self._lock_source(connection, context, source_id)
                replay = connection.execute(
                    "SELECT request_fingerprint,request_id FROM retention_request_idempotency WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND action='cancel' AND idempotency_key=%s",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise RetentionError("RETENTION_IDEMPOTENCY_CONFLICT")
                    return self._view(connection, context, str(replay[1]))
                current = self._view(connection, context, request_id)
                if current.version != expected_version:
                    raise RetentionError("RETENTION_VERSION_CONFLICT")
                if current.state not in {"grace_period", "blocked_by_hold"}:
                    raise RetentionError("DELETION_CLEANUP_PENDING")
                connection.execute("SELECT set_config('app.retention_transition','allowed',true)")
                connection.execute(
                    "UPDATE deletion_requests SET state='cancelled',source_active=true,version=version+1,updated_at=%s WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                    (now, context.tenant_id, context.workspace_id, request_id),
                )
                connection.execute(
                    "INSERT INTO retention_request_idempotency (tenant_id,workspace_id,actor_id,action,idempotency_key,request_fingerprint,request_id,result_version,created_at) VALUES (%s,%s,%s,'cancel',%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, fingerprint, request_id, expected_version + 1, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) VALUES (%s,%s,%s,%s,'deletion.cancelled','source',%s,'succeeded',%s,%s,%s,%s)",
                    ("retention-audit-" + hashlib.sha256(f"cancel|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24], context.tenant_id, context.workspace_id, context.actor_id, current.source_id, context.trace_id, context.policy_version, Jsonb({"request_id": request_id, "state": "cancelled"}), Jsonb({"version": expected_version + 1})),
                )
                return self._view(connection, context, request_id)
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def apply_legal_hold(self, context: RetentionContext, *, source_id: str, expected_version: int, idempotency_key: str, step_up_verified: bool) -> LegalHoldView:
        self._require_sensitive(context, step_up_verified)
        fingerprint = _fingerprint((source_id, expected_version, context.policy_version))
        now = self._clock()
        hold_id = "legal-hold-" + hashlib.sha256(f"{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24]
        try:
            with self._store._transaction(self._access(context)) as connection:
                self._lock_source(connection, context, source_id)
                replay = self._sensitive_replay(connection, context, "hold", idempotency_key, fingerprint)
                if replay is not None:
                    return self._hold_view(connection, context, replay)
                source = connection.execute(
                    "SELECT 1 FROM source_retention_locator WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s",
                    (context.tenant_id, context.workspace_id, source_id),
                ).fetchone()
                if source is None:
                    raise RetentionError("SOURCE_UNAVAILABLE", 404)
                terminal = connection.execute(
                    "SELECT state FROM deletion_requests WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s "
                    "ORDER BY created_at DESC,request_id DESC LIMIT 1",
                    (context.tenant_id, context.workspace_id, source_id),
                ).fetchone()
                if terminal is not None and str(terminal[0]) == "purged":
                    raise RetentionError("SOURCE_UNAVAILABLE", 404)
                request = connection.execute(
                    "SELECT request_id,version,state FROM deletion_requests WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s AND state NOT IN ('cancelled','purged')",
                    (context.tenant_id, context.workspace_id, source_id),
                ).fetchone()
                current_version = 1 if request is None else int(request[1])
                if current_version != expected_version:
                    raise RetentionError("RETENTION_VERSION_CONFLICT")
                connection.execute(
                    "INSERT INTO legal_hold_locator (tenant_id,hold_id,workspace_id,source_id,created_at) VALUES (%s,%s,%s,%s,%s)",
                    (context.tenant_id, hold_id, context.workspace_id, source_id, now),
                )
                connection.execute(
                    "INSERT INTO legal_holds (tenant_id,workspace_id,hold_id,source_id,actor_id,state,version,policy_version,idempotency_key,request_fingerprint,trace_id,created_at) VALUES (%s,%s,%s,%s,%s,'active',1,%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, hold_id, source_id, context.actor_id, context.policy_version, idempotency_key, fingerprint, context.trace_id, now),
                )
                if request is not None:
                    connection.execute(
                        "INSERT INTO legal_hold_targets (tenant_id,workspace_id,hold_id,request_id,source_id,attached_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (context.tenant_id, context.workspace_id, hold_id, str(request[0]), source_id, now),
                    )
                    connection.execute("SELECT set_config('app.retention_transition','allowed',true)")
                    connection.execute(
                        "UPDATE deletion_requests SET state='blocked_by_hold',version=version+1,updated_at=%s WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                        (now, context.tenant_id, context.workspace_id, str(request[0])),
                    )
                connection.execute(
                    "INSERT INTO retention_sensitive_idempotency (tenant_id,workspace_id,actor_id,action,idempotency_key,request_fingerprint,target_id,result_id,result_version,created_at) VALUES (%s,%s,%s,'hold',%s,%s,%s,%s,1,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, fingerprint, source_id, hold_id, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) VALUES (%s,%s,%s,%s,'legal_hold.applied','source',%s,'succeeded',%s,%s,%s,%s)",
                    ("retention-audit-" + hashlib.sha256(f"hold|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24], context.tenant_id, context.workspace_id, context.actor_id, source_id, context.trace_id, context.policy_version, Jsonb({"hold_id": hold_id, "state": "active"}), Jsonb({"reason_code": "LEGAL_HOLD_ACTIVE"})),
                )
                return self._hold_view(connection, context, hold_id)
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def release_legal_hold(self, context: RetentionContext, hold_id: str, *, expected_version: int, idempotency_key: str, step_up_verified: bool) -> LegalHoldView:
        self._require_sensitive(context, step_up_verified)
        fingerprint = _fingerprint((hold_id, expected_version, context.policy_version))
        now = self._clock()
        try:
            with self._store._transaction(self._access(context)) as connection:
                source_id = self._hold_source(connection, context, hold_id)
                self._lock_source(connection, context, source_id)
                replay = self._sensitive_replay(connection, context, "release", idempotency_key, fingerprint)
                if replay is not None:
                    return self._hold_view(connection, context, replay)
                hold = self._hold_view(connection, context, hold_id)
                if hold.version != expected_version or hold.state != "active":
                    raise RetentionError("RETENTION_VERSION_CONFLICT")
                connection.execute("SELECT set_config('app.retention_transition','allowed',true)")
                connection.execute(
                    "UPDATE legal_holds SET state='released',version=version+1,released_at=%s WHERE tenant_id=%s AND workspace_id=%s AND hold_id=%s",
                    (now, context.tenant_id, context.workspace_id, hold_id),
                )
                request = connection.execute(
                    "SELECT request_id,grace_until FROM deletion_requests WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s AND state='blocked_by_hold'",
                    (context.tenant_id, context.workspace_id, hold.source_id),
                ).fetchone()
                if request is not None:
                    next_state = "grace_period" if now < request[1] else "cleanup_pending"
                    connection.execute(
                        "UPDATE deletion_requests SET state=%s,version=version+1,updated_at=%s WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                        (next_state, now, context.tenant_id, context.workspace_id, str(request[0])),
                    )
                connection.execute(
                    "INSERT INTO retention_sensitive_idempotency (tenant_id,workspace_id,actor_id,action,idempotency_key,request_fingerprint,target_id,result_id,result_version,created_at) VALUES (%s,%s,%s,'release',%s,%s,%s,%s,2,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, fingerprint, hold_id, hold_id, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) VALUES (%s,%s,%s,%s,'legal_hold.released','source',%s,'succeeded',%s,%s,%s,%s)",
                    ("retention-audit-" + hashlib.sha256(f"release|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24], context.tenant_id, context.workspace_id, context.actor_id, hold.source_id, context.trace_id, context.policy_version, Jsonb({"hold_id": hold_id, "state": "released"}), Jsonb({"reason_code": "LEGAL_HOLD_RELEASED"})),
                )
                return self._hold_view(connection, context, hold_id)
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error

    def purge(self, context: RetentionContext, request_id: str, *, expected_version: int, idempotency_key: str, step_up_verified: bool) -> DeletionRequestView:
        self._require_sensitive(context, step_up_verified)
        fingerprint = _fingerprint((request_id, expected_version, context.policy_version))
        now = self._clock()
        try:
            with self._store._transaction(self._access(context)) as connection:
                source_id = self._request_source(connection, context, request_id)
                self._lock_source(connection, context, source_id)
                replay = self._sensitive_replay(connection, context, "purge", idempotency_key, fingerprint)
                if replay is not None:
                    return self._view(connection, context, replay)
                current = self._view(connection, context, request_id)
                if current.version != expected_version:
                    raise RetentionError("RETENTION_VERSION_CONFLICT")
                if connection.execute(
                    "SELECT 1 FROM legal_holds WHERE tenant_id=%s AND workspace_id=%s AND source_id=%s AND state='active' LIMIT 1",
                    (context.tenant_id, context.workspace_id, current.source_id),
                ).fetchone() is not None:
                    raise RetentionError("LEGAL_HOLD_ACTIVE")
                if now < current.grace_until:
                    raise RetentionError("DELETION_GRACE_PERIOD_ACTIVE")
                if not self._fixture_purge or any(not item.reference_id.startswith("fixture-") for item in current.cleanup_items):
                    raise RetentionError("FIXTURE_ONLY_PURGE_REQUIRED", 403)
                if any(item.state != "completed" for item in current.cleanup_items):
                    raise RetentionError("DELETION_CLEANUP_PENDING")
                connection.execute("SELECT set_config('app.retention_transition','allowed',true)")
                connection.execute(
                    "UPDATE deletion_requests SET state='cleanup_pending',purge_started=true,version=version+1,updated_at=%s WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                    (now, context.tenant_id, context.workspace_id, request_id),
                )
                connection.execute(
                    "UPDATE deletion_requests SET state='purged',version=version+1,updated_at=%s WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                    (now, context.tenant_id, context.workspace_id, request_id),
                )
                connection.execute(
                    "INSERT INTO retention_sensitive_idempotency (tenant_id,workspace_id,actor_id,action,idempotency_key,request_fingerprint,target_id,result_id,result_version,created_at) VALUES (%s,%s,%s,'purge',%s,%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, fingerprint, request_id, request_id, expected_version + 2, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) VALUES (%s,%s,%s,%s,'deletion.purged','source',%s,'succeeded',%s,%s,%s,%s)",
                    ("retention-audit-" + hashlib.sha256(f"purge|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24], context.tenant_id, context.workspace_id, context.actor_id, current.source_id, context.trace_id, context.policy_version, Jsonb({"request_id": request_id, "state": "purged"}), Jsonb({"fixture_only": True})),
                )
                return self._view(connection, context, request_id)
        except RetentionError:
            raise
        except CloudDatabaseError as error:
            raise RetentionError("RETENTION_UNAVAILABLE", 503, retryable=error.retryable) from error
