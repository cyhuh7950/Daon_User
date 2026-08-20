"""PostgreSQL Notebook repository with transaction-bound license enforcement."""

from __future__ import annotations

import hashlib
from typing import Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .notebook import (
    NotebookBinding,
    NotebookConversationCitation,
    NotebookConversationView,
    NotebookContext,
    NotebookError,
    NotebookHomeView,
    NotebookSelectedContext,
    _selected_context,
)


class PostgresNotebookRepository:
    _TARGET_QUERIES = {
        "knowledge_context": "SELECT 1 FROM scope_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
        "conversation_thread": "SELECT 1 FROM conversations WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
        "studio_output": "SELECT 1 FROM studio_outputs WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
        "output_version": "SELECT 1 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
        "generation_settings": "SELECT 1 FROM generation_settings_snapshots WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
    }
    def __init__(self, store: PostgresCloudStore, *, creation_enforcer=None) -> None:  # type: ignore[no-untyped-def]
        self._store = store
        self._creation_enforcer = creation_enforcer

    @property
    def creation_license_authoritative(self) -> bool:
        return self._creation_enforcer is not None

    @staticmethod
    def _context(context: NotebookContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "notebook.read_or_write",
        )

    @staticmethod
    def _view(row) -> NotebookHomeView:  # type: ignore[no-untyped-def]
        source_count, output_count = int(row[3]), int(row[4])
        return NotebookHomeView(
            notebook_id=str(row[0]), title=str(row[1]),
            source_count=source_count, output_count=output_count,
            updated_at=row[2].isoformat().replace("+00:00", "Z"),
            status="empty" if source_count == 0 and output_count == 0 else "active",
            etag=f'"notebook:{int(row[5])}"',
        )

    @staticmethod
    def _select(*, current_only: bool = True) -> str:
        return (
            "SELECT n.notebook_id,m.title,m.updated_at,"
            "(SELECT count(*) FROM notebook_bindings b WHERE b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='source'),"
            "(SELECT count(*) FROM notebook_bindings b WHERE b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='studio_output'),m.version "
            "FROM notebooks n JOIN notebook_metadata_versions m ON m.tenant_id=n.tenant_id AND m.workspace_id=n.workspace_id AND m.notebook_id=n.notebook_id "
            + ("AND m.is_current=true " if current_only else "")
        )

    def _replay(self, connection, context: NotebookContext, idempotency_key: str, request_fingerprint: str):  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT request_fingerprint,notebook_id,metadata_version FROM notebook_idempotency "
            "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND idempotency_key=%s",
            (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row[0]) != request_fingerprint:
            raise NotebookError("IDEMPOTENCY_KEY_REUSED", 409)
        result = connection.execute(
            self._select(current_only=False) + "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s AND m.version=%s",
            (context.tenant_id, context.workspace_id, str(row[1]), int(row[2])),
        ).fetchone()
        if result is None:
            raise NotebookError("NOTEBOOK_STATE_INVALID", 503)
        return self._view(result)

    def create(self, context: NotebookContext, *, title: str, description: str | None, idempotency_key: str, request_fingerprint: str, now) -> tuple[NotebookHomeView, bool]:  # type: ignore[no-untyped-def]
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}",),
                )
                replay = self._replay(connection, context, idempotency_key, request_fingerprint)
                if replay is not None:
                    return replay, True
                if self._creation_enforcer is None:
                    raise NotebookError("LICENSE_ENFORCEMENT_UNAVAILABLE", 503)
                self._creation_enforcer(connection, context.tenant_id, "notebook.create", {"notebooks": 1})
                notebook_id = "notebook-" + hashlib.sha256(
                    f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}".encode()
                ).hexdigest()[:32]
                connection.execute(
                    "INSERT INTO notebooks (tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES (%s,%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, notebook_id, context.actor_id, now),
                )
                connection.execute(
                    "INSERT INTO notebook_metadata_versions (tenant_id,workspace_id,notebook_id,version,title,description,is_current,updated_by,updated_at) VALUES (%s,%s,%s,1,%s,%s,true,%s,%s)",
                    (context.tenant_id, context.workspace_id, notebook_id, title, description, context.actor_id, now),
                )
                connection.execute(
                    "INSERT INTO notebook_activities (tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) VALUES (%s,%s,%s,1,'created',%s,%s)",
                    (context.tenant_id, context.workspace_id, notebook_id, context.actor_id, now),
                )
                connection.execute(
                    "INSERT INTO notebook_idempotency (tenant_id,workspace_id,actor_id,idempotency_key,action,request_fingerprint,notebook_id,metadata_version,created_at) VALUES (%s,%s,%s,%s,'create',%s,%s,1,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, request_fingerprint, notebook_id, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) VALUES (%s,%s,%s,%s,'notebook.created','notebook',%s,'succeeded',%s,%s,%s,%s)",
                    (
                        "notebook-audit-" + hashlib.sha256(f"create|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()).hexdigest()[:24],
                        context.tenant_id, context.workspace_id, context.actor_id, notebook_id,
                        context.trace_id, context.policy_version,
                        Jsonb({"status": "empty", "metadata_version": 1}),
                        Jsonb({"reason_code": "NOTEBOOK_CREATED"}),
                    ),
                )
                result = connection.execute(
                    self._select() + "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()
                return self._view(result), False
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def list(self, context: NotebookContext) -> tuple[NotebookHomeView, ...]:
        try:
            with self._store._transaction(self._context(context)) as connection:
                rows = connection.execute(
                    self._select() + "WHERE n.tenant_id=%s AND n.workspace_id=%s ORDER BY m.updated_at DESC,n.notebook_id",
                    (context.tenant_id, context.workspace_id),
                ).fetchall()
            return tuple(self._view(row) for row in rows)
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def get(self, context: NotebookContext, notebook_id: str) -> NotebookHomeView:
        try:
            with self._store._transaction(self._context(context)) as connection:
                row = connection.execute(
                    self._select() + "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()
            if row is None:
                raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
            return self._view(row)
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def bind_verified(self, context: NotebookContext, notebook_id: str, *, binding_kind: str, record_id: str, version_id: str | None, now) -> bool:  # type: ignore[no-untyped-def]
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"{context.tenant_id}|{context.workspace_id}|{notebook_id}|binding",),
                )
                notebook = connection.execute(
                    "SELECT 1 FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()
                if notebook is None:
                    raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
                if binding_kind == "source":
                    if version_id is None:
                        raise NotebookError("NOTEBOOK_BINDING_VERSION_ID_INVALID")
                    target = connection.execute(
                        "SELECT 1 FROM sources s JOIN source_versions sv ON sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id AND sv.source_id=s.record_id "
                        "WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.record_id=%s AND sv.record_id=%s",
                        (context.tenant_id, context.workspace_id, record_id, version_id),
                    ).fetchone()
                else:
                    query = self._TARGET_QUERIES.get(binding_kind)
                    if query is None or version_id is not None:
                        raise NotebookError("NOTEBOOK_BINDING_KIND_INVALID")
                    target = connection.execute(
                        query, (context.tenant_id, context.workspace_id, record_id),
                    ).fetchone()
                if target is None:
                    raise NotebookError("NOTEBOOK_BINDING_TARGET_NOT_FOUND", 404)
                inserted = connection.execute(
                    "INSERT INTO notebook_bindings (tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1",
                    (
                        context.tenant_id, context.workspace_id, notebook_id, binding_kind,
                        record_id, version_id, context.actor_id, now,
                    ),
                ).fetchone()
                if inserted is None:
                    return True
                sequence = int(connection.execute(
                    "SELECT coalesce(max(sequence),0)+1 FROM notebook_activities WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()[0])
                connection.execute(
                    "INSERT INTO notebook_activities (tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) VALUES (%s,%s,%s,%s,'context_bound',%s,%s)",
                    (context.tenant_id, context.workspace_id, notebook_id, sequence, context.actor_id, now),
                )
                return False
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def read_selected_context(self, context: NotebookContext, notebook_id: str) -> NotebookSelectedContext:
        try:
            with self._store._transaction(self._context(context)) as connection:
                notebook = connection.execute(
                    "SELECT 1 FROM notebooks WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()
                if notebook is None:
                    raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
                rows = connection.execute(
                    "SELECT binding_kind,record_id,version_id FROM notebook_bindings "
                    "WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s ORDER BY binding_kind,created_at,record_id",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchall()
                conversation_rows = connection.execute(
                    "SELECT nb.record_id,r.record_id,rr.record_id,rr.canonical_json,c.record_id,c.canonical_json "
                    "FROM notebook_bindings nb JOIN conversations cv ON cv.tenant_id=nb.tenant_id "
                    "AND cv.workspace_id=nb.workspace_id AND cv.record_id=nb.record_id "
                    "JOIN runs r ON r.tenant_id=cv.tenant_id AND r.workspace_id=cv.workspace_id "
                    "AND r.conversation_id=cv.record_id AND r.state='completed' "
                    "JOIN run_results rr ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id "
                    "AND rr.run_id=r.record_id LEFT JOIN citations c ON c.tenant_id=rr.tenant_id "
                    "AND c.workspace_id=rr.workspace_id AND c.run_result_id=rr.record_id "
                    "WHERE nb.tenant_id=%s AND nb.workspace_id=%s AND nb.notebook_id=%s "
                    "AND nb.binding_kind='conversation_thread' AND NOT EXISTS (SELECT 1 "
                    "FROM notebook_bindings newer WHERE newer.tenant_id=nb.tenant_id "
                    "AND newer.workspace_id=nb.workspace_id AND newer.notebook_id=nb.notebook_id "
                    "AND newer.binding_kind='conversation_thread' AND (newer.created_at>nb.created_at "
                    "OR (newer.created_at=nb.created_at AND newer.record_id>nb.record_id))) "
                    "ORDER BY c.record_id",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchall()
            bindings = tuple(NotebookBinding(
                context.tenant_id, context.workspace_id, notebook_id,
                str(row[0]), str(row[1]), None if row[2] is None else str(row[2]),
            ) for row in rows)
            conversation = None
            if conversation_rows:
                result_payload = cast(Mapping[str, object], conversation_rows[0][3])
                citations = tuple(
                    NotebookConversationCitation(
                        str(row[4]), str(payload["source_id"]),
                        str(payload["source_version_id"]), str(payload["evidence_span_id"]),
                        int(payload["page"]), str(payload.get("origin", "raw_source")),
                        str(payload.get("context_item_id", payload["source_version_id"])),
                        dict(cast(Mapping[str, str], payload.get(
                            "locator", {"kind": "page", "value": str(payload["page"])},
                        ))),
                    )
                    for row in conversation_rows if row[4] is not None
                    for payload in (cast(Mapping[str, object], row[5]),)
                )
                conversation = NotebookConversationView(
                    str(conversation_rows[0][0]), str(conversation_rows[0][1]),
                    str(conversation_rows[0][2]), str(result_payload["answer"]),
                    bool(result_payload["insufficient"]), citations,
                )
            return _selected_context(notebook_id, bindings, conversation)
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error

    def update_title(self, context: NotebookContext, notebook_id: str, *, title: str, expected_etag: str, idempotency_key: str, request_fingerprint: str, now) -> tuple[NotebookHomeView, bool]:  # type: ignore[no-untyped-def]
        try:
            with self._store._transaction(self._context(context)) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}",),
                )
                replay = self._replay(connection, context, idempotency_key, request_fingerprint)
                if replay is not None:
                    return replay, True
                row = connection.execute(
                    "SELECT version,description FROM notebook_metadata_versions WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s AND is_current=true FOR UPDATE",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()
                if row is None:
                    raise NotebookError("NOTEBOOK_NOT_FOUND", 404)
                version = int(row[0])
                if expected_etag != f'"notebook:{version}"':
                    raise NotebookError("NOTEBOOK_ETAG_MISMATCH", 412)
                connection.execute(
                    "UPDATE notebook_metadata_versions SET is_current=false WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s AND version=%s",
                    (context.tenant_id, context.workspace_id, notebook_id, version),
                )
                next_version = version + 1
                connection.execute(
                    "INSERT INTO notebook_metadata_versions (tenant_id,workspace_id,notebook_id,version,title,description,is_current,updated_by,updated_at) VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s)",
                    (context.tenant_id, context.workspace_id, notebook_id, next_version, title, row[1], context.actor_id, now),
                )
                sequence = int(connection.execute(
                    "SELECT coalesce(max(sequence),0)+1 FROM notebook_activities WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()[0])
                connection.execute(
                    "INSERT INTO notebook_activities (tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) VALUES (%s,%s,%s,%s,'title_updated',%s,%s)",
                    (context.tenant_id, context.workspace_id, notebook_id, sequence, context.actor_id, now),
                )
                connection.execute(
                    "INSERT INTO notebook_idempotency (tenant_id,workspace_id,actor_id,idempotency_key,action,request_fingerprint,notebook_id,metadata_version,created_at) VALUES (%s,%s,%s,%s,'update_title',%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, idempotency_key, request_fingerprint, notebook_id, next_version, now),
                )
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value,metadata) "
                    "VALUES (%s,%s,%s,%s,'notebook.title_updated','notebook',%s,'succeeded',%s,%s,%s,%s)",
                    (
                        "notebook-audit-" + hashlib.sha256(
                            f"title|{context.tenant_id}|{context.actor_id}|{idempotency_key}".encode()
                        ).hexdigest()[:24],
                        context.tenant_id, context.workspace_id, context.actor_id, notebook_id,
                        context.trace_id, context.policy_version,
                        Jsonb({"metadata_version": next_version}),
                        Jsonb({"reason_code": "NOTEBOOK_TITLE_UPDATED"}),
                    ),
                )
                result = connection.execute(
                    self._select() + "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s",
                    (context.tenant_id, context.workspace_id, notebook_id),
                ).fetchone()
                return self._view(result), False
        except NotebookError:
            raise
        except CloudDatabaseError as error:
            raise NotebookError("NOTEBOOK_UNAVAILABLE", 503) from error
