"""PostgreSQL Canon transaction for grounded Studio reports."""

from __future__ import annotations

from .open_law_source_access import OpenLawAccessError,require_open_law_source_access
from .authorization import Action

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .studio_report import (
    StudioCitation, StudioOutputProjection, StudioReportContext, StudioReportCreateRequest,
    StudioReportError, WorkspaceSourceProjection, compose_grounded_report_content,
)


class PostgresStudioReportRepository:
    def __init__(
        self, cloud_store: PostgresCloudStore,
        generation_provider: Callable[[StudioReportCreateRequest, str], str] | None = None,
        creation_enforcer=None,
    ) -> None:
        self._cloud_store = cloud_store
        self._generation_provider = generation_provider or (
            lambda request, answer: compose_grounded_report_content(request.title, answer)
        )
        self._creation_enforcer = creation_enforcer

    @property
    def creation_license_authoritative(self) -> bool:
        return self._creation_enforcer is not None

    @staticmethod
    def _cloud(context: StudioReportContext, capability: str) -> CloudAccessContext:
        return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, capability)

    @staticmethod
    def _opaque(prefix: str, *values: str) -> str:
        return f"{prefix}-{hashlib.sha256('|'.join(values).encode()).hexdigest()[:32]}"

    @staticmethod
    def _id_scope(
        context: StudioReportContext, operation: str, idempotency_key: str,
    ) -> tuple[str, str, str, str, str]:
        return (
            context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key,
        )

    @staticmethod
    def _insert(connection, context: StudioReportContext, table: str, record_id: str,
                payload: Mapping[str, object], *, state: str | None = None,
                extra_columns: tuple[str, ...] = (), extra_values: tuple[object, ...] = ()) -> None:
        text = canonical_json_bytes(payload).decode("utf-8")
        columns = (
            "tenant_id", "workspace_id", "record_id", "aggregate_id", "version", "schema_version",
            "canonical_json", "canonical_text", "digest_sha256", *(("state",) if state else ()),
            "created_by", "trace_id", *extra_columns,
        )
        values = (
            context.tenant_id, context.workspace_id, record_id, record_id, 1, 1, Jsonb(dict(payload)),
            text, hashlib.sha256(text.encode()).hexdigest(), *((state,) if state else ()),
            context.actor_id, context.trace_id, *extra_values,
        )
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(('%s',) * len(columns))})",
            values,
        )

    @staticmethod
    def _transition(connection, context: StudioReportContext, entity: str, record_id: str,
                    version: int, target: str, suffix: str, id_scope: tuple[str, ...]) -> int:
        row = connection.execute(
            "SELECT state,version,outcome,error_code FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
            (entity, record_id, version, target, PostgresStudioReportRepository._opaque(
                "tr", *id_scope, record_id, suffix,
            ),
             "STUDIO_REPORT", context.trace_id, context.policy_version),
        ).fetchone()
        if row is None or str(row[2]) != "succeeded" or str(row[0]) != target:
            raise StudioReportError("STUDIO_CANON_TRANSITION_INVALID", status=409)
        return int(row[1])

    @staticmethod
    def _projection(value: Mapping[str, object]) -> StudioOutputProjection:
        try:
            citations = tuple(StudioCitation(**cast(dict, item)) for item in cast(list, value["citations"]))
            return StudioOutputProjection(
                str(value["studio_output_id"]), str(value["output_version_id"]),
                str(value["output_type"]), str(value["title"]), str(value["purpose"]),
                str(value["status"]), str(value["content"]), str(value["run_id"]),
                str(value["run_result_id"]), citations,
            )
        except (KeyError, TypeError, ValueError):
            raise StudioReportError("STUDIO_RESULT_INVALID", status=500) from None

    def list_sources(self, context: StudioReportContext) -> tuple[WorkspaceSourceProjection, ...]:
        if context.notebook_id is None:
            raise StudioReportError("NOTEBOOK_SCOPE_REQUIRED", status=400)
        try:
            with self._cloud_store._transaction(self._cloud(context, "source.read")) as connection:
                rows = connection.execute(
                    "SELECT s.record_id,sv.record_id,sv.canonical_json->>'filename',s.state,"
                    "COALESCE(pr.state,'accepted'),COALESCE(oq.state,'pending'), "
                    "(s.state='ready' AND s.canonical_json->>'kind'='studio_output' "
                    "AND pr.record_id IS NULL AND oq.state IS NULL AND EXISTS ("
                    "SELECT 1 FROM knowledge_registrations kr JOIN output_versions ov "
                    "ON ov.tenant_id=kr.tenant_id AND ov.workspace_id=kr.workspace_id AND ov.record_id=kr.output_version_id "
                    "JOIN notebook_bindings bound ON bound.tenant_id=ov.tenant_id AND bound.workspace_id=ov.workspace_id "
                    "AND bound.notebook_id=nb.notebook_id AND bound.binding_kind='output_version' AND bound.record_id=ov.record_id "
                    "WHERE kr.tenant_id=sv.tenant_id AND kr.workspace_id=sv.workspace_id "
                    "AND kr.registered_source_version_id=sv.record_id AND kr.state='registered' AND ov.state IN ('approved','delivered') "
                    "AND EXISTS (SELECT 1 FROM approvals approval WHERE approval.tenant_id=ov.tenant_id "
                    "AND approval.workspace_id=ov.workspace_id AND approval.output_version_id=ov.record_id AND approval.decision='approved') "
                    "AND kr.canonical_json->>'explicit'='true' "
                    "AND s.canonical_json->>'derived_output_version_id'=ov.record_id "
                    "AND sv.canonical_json->>'derived_output_version_id'=ov.record_id "
                    "AND EXISTS (SELECT 1 FROM index_versions iv JOIN evidence_spans es "
                    "ON es.tenant_id=iv.tenant_id AND es.workspace_id=iv.workspace_id AND es.source_version_id=iv.source_version_id "
                    "WHERE iv.tenant_id=sv.tenant_id AND iv.workspace_id=sv.workspace_id AND iv.source_version_id=sv.record_id "
                    "AND iv.canonical_json->>'strategy'='approved_knowledge_snapshot' "
                    "AND iv.canonical_json->'lineage'->>'knowledge_registration_id'=kr.record_id "
                    "AND iv.canonical_json->'lineage'->>'output_version_id'=ov.record_id "
                    "AND es.canonical_json->>'kind'='approved_knowledge_snapshot' "
                    "AND es.canonical_json->>'source_id'=s.record_id "
                    "AND es.canonical_json->>'output_version_id'=ov.record_id "
                    "AND EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(iv.canonical_json->'chunks')='array' "
                    "THEN iv.canonical_json->'chunks' ELSE '[]'::jsonb END) chunk "
                    "WHERE chunk->>'evidence_span_id'=es.record_id AND chunk->>'source_id'=s.record_id "
                    "AND chunk->>'source_version_id'=sv.record_id "
                    "AND length(chunk->>'text')>0 AND chunk->>'text'=es.canonical_json->>'text')))) AS approved_knowledge "
                    "FROM sources s "
                    "JOIN source_versions sv ON sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id "
                    "AND sv.source_id=s.record_id JOIN notebook_bindings nb ON nb.tenant_id=sv.tenant_id "
                    "AND nb.workspace_id=sv.workspace_id AND nb.notebook_id=%s AND nb.binding_kind='source' "
                    "AND nb.record_id=s.record_id AND nb.version_id=sv.record_id "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=nb.tenant_id "
                    "AND u.workspace_id=nb.workspace_id AND u.notebook_id=nb.notebook_id "
                    "AND u.source_id=nb.record_id AND u.source_version_id=nb.version_id) "
                    "AND NOT EXISTS (SELECT 1 FROM deletion_requests dr WHERE dr.tenant_id=s.tenant_id "
                    "AND dr.workspace_id=s.workspace_id AND dr.source_id=s.record_id AND dr.source_active=false) "
                    "LEFT JOIN LATERAL (SELECT state,record_id FROM processing_runs "
                    "WHERE tenant_id=sv.tenant_id AND workspace_id=sv.workspace_id "
                    "AND source_version_id=sv.record_id ORDER BY created_at DESC,record_id DESC LIMIT 1) pr ON true "
                    "LEFT JOIN LATERAL (SELECT state FROM document_processing_jobs "
                    "WHERE tenant_id=sv.tenant_id AND workspace_id=sv.workspace_id AND source_version_id=sv.record_id "
                    "AND processing_run_id=pr.record_id "
                    "ORDER BY created_at DESC LIMIT 1) oq ON true WHERE NOT EXISTS (SELECT 1 FROM source_versions newer "
                    "WHERE newer.tenant_id=sv.tenant_id AND newer.workspace_id=sv.workspace_id "
                    "AND newer.source_id=sv.source_id AND newer.version>sv.version) ORDER BY s.created_at, s.record_id",
                    (context.notebook_id,),
                ).fetchall()
                accessible=[]
                for row in rows:
                    try:require_open_law_source_access(context,str(row[1]),connection)
                    except OpenLawAccessError:continue
                    accessible.append(row)
                rows=accessible
        except CloudDatabaseError as error:
            raise StudioReportError("STUDIO_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable) from None
        # Approved knowledge is a directly registered/indexed OutputVersion
        # snapshot, not a document-processing job. Reuse the existing public
        # completion projection only when the catalog proves that exact path;
        # never create a fake ProcessingRun/job or upgrade an unready upload.
        return tuple(WorkspaceSourceProjection(
            *(str(item) for item in row[:4]),
            *(('completed', 'completed') if len(row) == 7 and row[6] is True and row[3] == 'ready'
              else (str(row[4]), str(row[5]))),
        ) for row in rows)

    def create_report(self, context: StudioReportContext, request: StudioReportCreateRequest,
                      idempotency_key: str) -> tuple[StudioOutputProjection, bool]:
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "notebook_id": context.notebook_id,
            "source_id": request.source_id, "source_version_id": request.source_version_id,
            "run_id": request.run_id, "run_result_id": request.run_result_id,
            "title": request.title, "purpose": request.purpose,
        })).hexdigest()
        operation = "studio.report.create"
        id_scope = self._id_scope(context, operation, idempotency_key)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.create")) as connection:
                try:require_open_law_source_access(context,request.source_version_id,connection,action=Action.GENERATE)
                except OpenLawAccessError:raise StudioReportError('RESOURCE_UNAVAILABLE',status=404) from None
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (
                    f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{operation}|{idempotency_key}",
                ))
                replay = connection.execute(
                    "SELECT request_fingerprint,result FROM idempotency_records "
                    "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s "
                    "AND operation=%s AND idempotency_key=%s",
                    id_scope,
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise StudioReportError("IDEMPOTENCY_CONFLICT", status=409)
                    return self._projection(cast(Mapping[str, object], replay[1])), True
                if context.notebook_id is None:
                    raise StudioReportError("NOTEBOOK_SCOPE_REQUIRED", status=400)
                if self._creation_enforcer is not None:
                    self._creation_enforcer(
                        connection, context.tenant_id, "studio.generate",
                        {"generation_runs": 1, "studio_outputs": 1},
                    )
                lineage = connection.execute(
                    "SELECT r.canonical_json,rr.canonical_json,s.state FROM runs r JOIN run_results rr ON "
                    "rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id "
                    "JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id "
                    "AND sv.record_id=%s JOIN sources s ON s.tenant_id=sv.tenant_id "
                    "AND s.workspace_id=sv.workspace_id AND s.record_id=%s AND sv.source_id=s.record_id "
                    "WHERE r.record_id=%s AND rr.record_id=%s "
                    "AND r.canonical_json->>'source_id'=%s AND r.canonical_json->>'source_version_id'=%s "
                    "AND EXISTS (SELECT 1 FROM notebook_bindings source_binding WHERE "
                    "source_binding.tenant_id=r.tenant_id AND source_binding.workspace_id=r.workspace_id "
                    "AND source_binding.notebook_id=%s AND source_binding.binding_kind='source' "
                    "AND source_binding.record_id=%s AND source_binding.version_id=%s) "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=r.tenant_id "
                    "AND u.workspace_id=r.workspace_id AND u.notebook_id=%s AND u.source_id=%s AND u.source_version_id=%s) "
                    "AND NOT EXISTS (SELECT 1 FROM deletion_requests dr WHERE dr.tenant_id=s.tenant_id "
                    "AND dr.workspace_id=s.workspace_id AND dr.source_id=s.record_id AND dr.source_active=false) "
                    "AND EXISTS (SELECT 1 FROM notebook_bindings thread_binding WHERE "
                    "thread_binding.tenant_id=r.tenant_id AND thread_binding.workspace_id=r.workspace_id "
                    "AND thread_binding.notebook_id=%s AND thread_binding.binding_kind='conversation_thread' "
                    "AND thread_binding.record_id=r.conversation_id)",
                    (request.source_version_id, request.source_id, request.run_id, request.run_result_id,
                     request.source_id, request.source_version_id,
                     context.notebook_id, request.source_id, request.source_version_id,
                     context.notebook_id, request.source_id, request.source_version_id,
                     context.notebook_id),
                ).fetchone()
                if lineage is None or str(lineage[2]) != "ready":
                    raise StudioReportError("RESOURCE_UNAVAILABLE", status=404)
                result = cast(Mapping[str, object], lineage[1])
                citation_rows = connection.execute(
                    "SELECT c.record_id,c.source_version_id,c.evidence_span_id,c.canonical_json FROM citations c "
                    "WHERE c.tenant_id=%s AND c.workspace_id=%s AND c.run_result_id=%s "
                    "AND c.source_version_id=%s ORDER BY c.record_id",
                    (context.tenant_id, context.workspace_id, request.run_result_id, request.source_version_id),
                ).fetchall()
                if bool(result.get("insufficient")) or not citation_rows:
                    raise StudioReportError("EVIDENCE_REQUIRED", status=409)
                citations = tuple(StudioCitation(
                    str(row[0]), request.source_id, str(row[1]), str(row[2]),
                    int(cast(Mapping[str, object], row[3])["page"]),
                ) for row in citation_rows)
                answer = str(result.get("answer", "")).strip()
                if not answer:
                    raise StudioReportError("EVIDENCE_REQUIRED", status=409)
                content = self._generation_provider(request, answer)
                if not isinstance(content, str) or not content.strip() or len(content) > 20_000:
                    raise StudioReportError("STUDIO_RESULT_INVALID", status=500)
                settings_id = self._opaque("settings", *id_scope)
                generation_id = self._opaque("generation", *id_scope)
                output_id = self._opaque("output", *id_scope)
                version_id = self._opaque("output-version", *id_scope)
                self._insert(connection, context, "generation_settings_snapshots", settings_id, {
                    "provider_code": "upstage", "model_id": "solar-pro4", "output_type": "evidence_report",
                    "source_version_id": request.source_version_id, "run_result_id": request.run_result_id,
                })
                self._insert(connection, context, "generation_requests", generation_id, {
                    "output_type": "evidence_report", "source_id": request.source_id,
                    "source_version_id": request.source_version_id, "run_id": request.run_id,
                    "run_result_id": request.run_result_id, "title": request.title, "purpose": request.purpose,
                }, state="configuring", extra_columns=("generation_settings_snapshot_id",), extra_values=(settings_id,))
                generation_version = self._transition(
                    connection, context, "GenerationRequest", generation_id, 1,
                    "confirmed", "confirmed", id_scope,
                )
                self._transition(
                    connection, context, "GenerationRequest", generation_id, generation_version,
                    "submitted", "submitted", id_scope,
                )
                self._insert(connection, context, "studio_outputs", output_id, {
                    "output_type": "evidence_report", "title": request.title, "purpose": request.purpose,
                    "run_id": request.run_id, "run_result_id": request.run_result_id,
                }, extra_columns=("generation_request_id",), extra_values=(generation_id,))
                self._insert(connection, context, "output_versions", version_id, {
                    "output_type": "evidence_report", "title": request.title, "purpose": request.purpose,
                    "content": content, "run_id": request.run_id, "run_result_id": request.run_result_id,
                }, state="generating", extra_columns=("studio_output_id", "generation_settings_snapshot_id"),
                    extra_values=(output_id, settings_id))
                self._transition(
                    connection, context, "OutputVersion", version_id, 1, "draft", "draft", id_scope,
                )
                for citation in citations:
                    reference_id = self._opaque(
                        "evidence-reference", *id_scope, version_id, citation.citation_id,
                    )
                    self._insert(connection, context, "evidence_references", reference_id, {
                        "citation_id": citation.citation_id, "source_id": citation.source_id,
                        "source_version_id": citation.source_version_id,
                        "evidence_span_id": citation.evidence_span_id, "page": citation.page,
                    }, extra_columns=("output_version_id", "source_version_id", "evidence_span_id"),
                        extra_values=(version_id, citation.source_version_id, citation.evidence_span_id))
                connection.execute(
                    "INSERT INTO notebook_bindings "
                    "(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES "
                    "(%s,%s,%s,'generation_settings',%s,NULL,%s,now()),"
                    "(%s,%s,%s,'studio_output',%s,NULL,%s,now()),"
                    "(%s,%s,%s,'output_version',%s,NULL,%s,now())",
                    (
                        context.tenant_id, context.workspace_id, context.notebook_id,
                        settings_id, context.actor_id,
                        context.tenant_id, context.workspace_id, context.notebook_id,
                        output_id, context.actor_id,
                        context.tenant_id, context.workspace_id, context.notebook_id,
                        version_id, context.actor_id,
                    ),
                )
                output = StudioOutputProjection(
                    output_id, version_id, "evidence_report", request.title, request.purpose,
                    "draft", content, request.run_id, request.run_result_id, citations,
                )
                result_payload = {
                    "studio_output_id": output.studio_output_id, "output_version_id": output.output_version_id,
                    "output_type": output.output_type, "title": output.title, "purpose": output.purpose,
                    "status": output.status, "content": output.content, "run_id": output.run_id,
                    "run_result_id": output.run_result_id,
                    "citations": [asdict(item) for item in citations],
                }
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (self._opaque("audit", *id_scope, output_id), context.tenant_id, context.workspace_id,
                     context.actor_id, operation, "StudioOutput", output_id, "succeeded",
                     context.trace_id, context.policy_version, json.dumps({"citation_count": len(citations)})),
                )
                connection.execute(
                    "INSERT INTO idempotency_records (tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key,
                     fingerprint, json.dumps(result_payload), datetime.now(timezone.utc) + timedelta(hours=24)),
                )
                return output, False
        except StudioReportError:
            raise
        except CloudDatabaseError as error:
            raise StudioReportError("STUDIO_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable) from None

    def list_outputs(self, context: StudioReportContext) -> tuple[StudioOutputProjection, ...]:
        if context.notebook_id is None:
            raise StudioReportError("NOTEBOOK_SCOPE_REQUIRED", status=400)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.read")) as connection:
                rows = connection.execute(
                    "SELECT ov.canonical_json,ov.state,so.record_id,ov.record_id,er.canonical_json,so.canonical_json "
                    "FROM studio_outputs so JOIN LATERAL (SELECT v.canonical_json,v.state,v.record_id "
                    "FROM output_versions v WHERE v.tenant_id=so.tenant_id AND v.workspace_id=so.workspace_id "
                    "AND v.studio_output_id=so.record_id ORDER BY v.content_version DESC,v.record_id DESC LIMIT 1) ov ON true "
                    "LEFT JOIN evidence_references er ON "
                    "er.tenant_id=so.tenant_id AND er.workspace_id=so.workspace_id "
                    "AND er.output_version_id=ov.record_id WHERE so.canonical_json->>'output_type'='evidence_report' "
                    "AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=so.tenant_id "
                    "AND nb.workspace_id=so.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='studio_output' AND nb.record_id=so.record_id) "
                    "AND so.tenant_id=%s AND so.workspace_id=%s "
                    "ORDER BY so.created_at,so.record_id,er.record_id"
                    , (context.notebook_id, context.tenant_id, context.workspace_id),
                ).fetchall()
                blocked=set()
                for row in rows:
                    citation=row[4]
                    if isinstance(citation,Mapping) and citation.get('source_version_id'):
                        try:require_open_law_source_access(context,str(citation['source_version_id']),connection)
                        except OpenLawAccessError:blocked.add(str(row[3]))
                rows=[row for row in rows if str(row[3]) not in blocked]
        except CloudDatabaseError as error:
            raise StudioReportError("STUDIO_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable) from None
        try:
            grouped: dict[str, dict[str, object]] = {}
            for row in rows:
                output_id = str(row[2])
                if output_id not in grouped:
                    payload = dict(cast(Mapping[str, object], row[0]))
                    parent = dict(cast(Mapping[str, object], row[5]))
                    content = payload["content"]
                    if isinstance(content, Mapping):
                        # The legacy DTO is text. Preserve every structured
                        # value as JSON, never a regenerated/abridged report.
                        content = json.dumps(dict(content), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    elif not isinstance(content, str):
                        raise ValueError("STUDIO_RESULT_INVALID")
                    grouped[output_id] = {
                        "studio_output_id": output_id, "output_version_id": str(row[3]),
                        "output_type": parent["output_type"], "title": parent["title"], "purpose": parent["purpose"],
                        "status": str(row[1]), "content": content, "run_id": payload["run_id"],
                        "run_result_id": payload["run_result_id"], "citations": [],
                    }
                if row[4] is not None:
                    citation = cast(Mapping[str, object], row[4])
                    cast(list, grouped[output_id]["citations"]).append({
                        key: citation[key] for key in (
                            "citation_id", "source_id", "source_version_id", "evidence_span_id", "page",
                        )
                    })
            return tuple(self._projection(value) for value in grouped.values())
        except (KeyError, TypeError, ValueError):
            raise StudioReportError("STUDIO_RESULT_INVALID", status=500) from None
