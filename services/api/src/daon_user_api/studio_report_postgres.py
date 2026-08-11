"""PostgreSQL Canon transaction for grounded Studio reports."""

from __future__ import annotations

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
    ) -> None:
        self._cloud_store = cloud_store
        self._generation_provider = generation_provider or (
            lambda request, answer: compose_grounded_report_content(request.title, answer)
        )

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
        try:
            with self._cloud_store._transaction(self._cloud(context, "source.read")) as connection:
                rows = connection.execute(
                    "SELECT s.record_id,sv.record_id,sv.canonical_json->>'filename',s.state,"
                    "COALESCE(pr.state,'accepted'),COALESCE(oq.state,'pending') FROM sources s "
                    "JOIN source_versions sv ON sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id "
                    "AND sv.source_id=s.record_id LEFT JOIN LATERAL (SELECT state,record_id FROM processing_runs "
                    "WHERE tenant_id=sv.tenant_id AND workspace_id=sv.workspace_id "
                    "AND source_version_id=sv.record_id ORDER BY created_at DESC,record_id DESC LIMIT 1) pr ON true "
                    "LEFT JOIN LATERAL (SELECT state FROM document_processing_jobs "
                    "WHERE tenant_id=sv.tenant_id AND workspace_id=sv.workspace_id AND source_version_id=sv.record_id "
                    "AND processing_run_id=pr.record_id "
                    "ORDER BY created_at DESC LIMIT 1) oq ON true WHERE NOT EXISTS (SELECT 1 FROM source_versions newer "
                    "WHERE newer.tenant_id=sv.tenant_id AND newer.workspace_id=sv.workspace_id "
                    "AND newer.source_id=sv.source_id AND newer.version>sv.version) ORDER BY s.created_at, s.record_id"
                ).fetchall()
        except CloudDatabaseError as error:
            raise StudioReportError("STUDIO_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable) from None
        return tuple(WorkspaceSourceProjection(*(str(item) for item in row)) for row in rows)

    def create_report(self, context: StudioReportContext, request: StudioReportCreateRequest,
                      idempotency_key: str) -> tuple[StudioOutputProjection, bool]:
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "source_id": request.source_id, "source_version_id": request.source_version_id,
            "run_id": request.run_id, "run_result_id": request.run_result_id,
            "title": request.title, "purpose": request.purpose,
        })).hexdigest()
        operation = "studio.report.create"
        id_scope = self._id_scope(context, operation, idempotency_key)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.create")) as connection:
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
                lineage = connection.execute(
                    "SELECT r.canonical_json,rr.canonical_json,s.state FROM runs r JOIN run_results rr ON "
                    "rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id "
                    "JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id "
                    "AND sv.record_id=%s JOIN sources s ON s.tenant_id=sv.tenant_id "
                    "AND s.workspace_id=sv.workspace_id AND s.record_id=%s AND sv.source_id=s.record_id "
                    "WHERE r.record_id=%s AND rr.record_id=%s "
                    "AND r.canonical_json->>'source_id'=%s AND r.canonical_json->>'source_version_id'=%s",
                    (request.source_version_id, request.source_id, request.run_id, request.run_result_id,
                     request.source_id, request.source_version_id),
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
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.read")) as connection:
                rows = connection.execute(
                    "SELECT ov.canonical_json,ov.state,so.record_id,ov.record_id,er.canonical_json FROM output_versions ov "
                    "JOIN studio_outputs so ON so.tenant_id=ov.tenant_id AND so.workspace_id=ov.workspace_id "
                    "AND so.record_id=ov.studio_output_id LEFT JOIN evidence_references er ON "
                    "er.tenant_id=ov.tenant_id AND er.workspace_id=ov.workspace_id "
                    "AND er.output_version_id=ov.record_id WHERE so.canonical_json->>'output_type'='evidence_report' "
                    "ORDER BY so.created_at,so.record_id,er.record_id"
                ).fetchall()
        except CloudDatabaseError as error:
            raise StudioReportError("STUDIO_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable) from None
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            payload = dict(cast(Mapping[str, object], row[0]))
            item = grouped.setdefault(str(row[2]), {
                "studio_output_id": str(row[2]), "output_version_id": str(row[3]),
                "output_type": payload["output_type"], "title": payload["title"], "purpose": payload["purpose"],
                "status": str(row[1]), "content": payload["content"], "run_id": payload["run_id"],
                "run_result_id": payload["run_result_id"], "citations": [],
            })
            if row[4] is not None:
                cast(list, item["citations"]).append(dict(cast(Mapping[str, object], row[4])))
        return tuple(self._projection(value) for value in grouped.values())
