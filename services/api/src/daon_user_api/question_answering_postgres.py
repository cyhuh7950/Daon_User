"""Workspace-scoped source and Citation content access for question answering."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .object_queue import ObjectKeyPolicy, ObjectQueueError, ObjectStorageError, ObjectStoragePort
from .data_canon import canonical_json_bytes
from .document_index_postgres import IndexedEvidenceChunk
from .question_answering import GroundedTextResult, TextModelSelection


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class QuestionRepositoryError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class QuestionContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
            self.trace_id, self.policy_version,
        )):
            raise QuestionRepositoryError("QUESTION_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class ReadyQuestionSource:
    source_id: str
    source_version_id: str
    filename: str


@dataclass(frozen=True, slots=True)
class CitationPdfContent:
    source_id: str
    source_version_id: str
    filename: str
    content: bytes
    page_count_hint: int


@dataclass(frozen=True, slots=True)
class StoredCitation:
    citation_id: str
    source_id: str
    source_version_id: str
    evidence_span_id: str
    page: int


@dataclass(frozen=True, slots=True)
class StoredQuestionAnswer:
    run_id: str
    run_result_id: str
    answer: str
    insufficient: bool
    citations: tuple[StoredCitation, ...]


class PostgresQuestionAnsweringRepository:
    def __init__(self, cloud_store: PostgresCloudStore, object_storage: ObjectStoragePort) -> None:
        self._cloud_store = cloud_store
        self._object_storage = object_storage

    @staticmethod
    def _cloud_context(context: QuestionContext, capability: str) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, capability,
        )

    @staticmethod
    def _opaque_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:32]
        return f"{prefix}-{digest}"

    @staticmethod
    def _snapshot(payload: Mapping[str, object]) -> tuple[str, str]:
        text = canonical_json_bytes(payload).decode("utf-8")
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _insert_canon(
        connection, context: QuestionContext, table: str, record_id: str,
        payload: Mapping[str, object], *, extra_columns: tuple[str, ...] = (),
        extra_values: tuple[object, ...] = (), state: str | None = None,
    ) -> None:
        text, digest = PostgresQuestionAnsweringRepository._snapshot(payload)
        columns = (
            "tenant_id", "workspace_id", "record_id", "aggregate_id", "version",
            "schema_version", "canonical_json", "canonical_text", "digest_sha256",
            *(("state",) if state is not None else ()), "created_by", "trace_id",
            *extra_columns,
        )
        values: tuple[object, ...] = (
            context.tenant_id, context.workspace_id, record_id, record_id, 1, 1,
            Jsonb(dict(payload)), text, digest,
            *((state,) if state is not None else ()), context.actor_id, context.trace_id,
            *extra_values,
        )
        placeholders = ",".join(("%s",) * len(columns))
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
            values,
        )

    @staticmethod
    def _transition(
        connection, context: QuestionContext, run_id: str,
        version: int, target: str,
    ) -> int:
        transition_id = PostgresQuestionAnsweringRepository._opaque_id(
            "tr", run_id, target, str(version),
        )
        row = connection.execute(
            "SELECT state,version,outcome,error_code FROM transition_canon_state"
            "(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "Run", run_id, version, target, transition_id,
                f"QUESTION_{target.upper()}", context.trace_id, context.policy_version,
            ),
        ).fetchone()
        if row is None or str(row[2]) != "succeeded" or row[1] is None:
            raise QuestionRepositoryError("QUESTION_CANON_TRANSITION_INVALID", status=409)
        return int(row[1])

    def _row(
        self, context: QuestionContext, source_id: str, source_version_id: str,
        capability: str,
    ) -> tuple[object, ...]:
        if not _SAFE_ID.fullmatch(source_id) or not _SAFE_ID.fullmatch(source_version_id):
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, capability),
            ) as connection:
                row = connection.execute(
                    "SELECT s.record_id,sv.record_id,s.state,sv.canonical_json->>'filename',"
                    "o.object_id,o.object_key,o.digest_sha256,o.byte_size,o.content_type,o.status "
                    "FROM sources s JOIN source_versions sv ON "
                    "sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id "
                    "AND sv.source_id=s.record_id JOIN object_records o ON "
                    "o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id "
                    "AND o.object_id=sv.object_id WHERE s.record_id=%s AND sv.record_id=%s "
                    "AND s.state='ready' AND o.status='completed' "
                    "AND EXISTS (SELECT 1 FROM index_versions iv WHERE "
                    "iv.tenant_id=sv.tenant_id AND iv.workspace_id=sv.workspace_id "
                    "AND iv.source_version_id=sv.record_id) AND NOT EXISTS ("
                    "SELECT 1 FROM source_versions newer WHERE newer.tenant_id=sv.tenant_id "
                    "AND newer.workspace_id=sv.workspace_id AND newer.source_id=sv.source_id "
                    "AND newer.version>sv.version)",
                    (source_id, source_version_id),
                ).fetchone()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if row is None:
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        return cast(tuple[object, ...], row)

    def load_completed(
        self, context: QuestionContext, run_id: str,
    ) -> StoredQuestionAnswer | None:
        if not _SAFE_ID.fullmatch(run_id):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "question.read"),
            ) as connection:
                rows = connection.execute(
                    "SELECT rr.record_id,rr.canonical_json,c.record_id,c.canonical_json "
                    "FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id "
                    "AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id "
                    "LEFT JOIN citations c ON c.tenant_id=rr.tenant_id "
                    "AND c.workspace_id=rr.workspace_id AND c.run_result_id=rr.record_id "
                    "WHERE r.record_id=%s AND r.state='completed' ORDER BY c.record_id",
                    (run_id,),
                ).fetchall()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if not rows:
            return None
        try:
            result_payload = cast(Mapping[str, object], rows[0][1])
            citations = tuple(
                StoredCitation(
                    str(row[2]), str(cast(Mapping[str, object], row[3])["source_id"]),
                    str(cast(Mapping[str, object], row[3])["source_version_id"]),
                    str(cast(Mapping[str, object], row[3])["evidence_span_id"]),
                    int(cast(Mapping[str, object], row[3])["page"]),
                )
                for row in rows if row[2] is not None and row[3] is not None
            )
            return StoredQuestionAnswer(
                run_id, str(rows[0][0]), str(result_payload["answer"]),
                bool(result_payload["insufficient"]), citations,
            )
        except (KeyError, TypeError, ValueError):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID", status=500) from None

    def load_ready_source(
        self, context: QuestionContext, source_id: str, source_version_id: str,
    ) -> ReadyQuestionSource:
        row = self._row(context, source_id, source_version_id, "question.read")
        filename = str(row[3])
        if str(row[2]) != "ready" or not filename.lower().endswith(".pdf"):
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        return ReadyQuestionSource(str(row[0]), str(row[1]), filename)

    def read_current_pdf(
        self, context: QuestionContext, source_id: str, source_version_id: str,
    ) -> CitationPdfContent:
        row = self._row(context, source_id, source_version_id, "citation.read")
        filename, object_key = str(row[3]), str(row[5])
        if str(row[8]) != "application/pdf" or str(row[9]) != "completed":
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        scope = self._cloud_context(context, "object.read")
        try:
            ObjectKeyPolicy().validate_final(scope, "source", object_key)
            content = self._object_storage.get(object_key)
        except (ObjectQueueError, ObjectStorageError):
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=503) from None
        if (
            not content.startswith(b"%PDF-") or len(content) != int(row[7])
            or hashlib.sha256(content).hexdigest() != str(row[6])
        ):
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
        return CitationPdfContent(
            str(row[0]), str(row[1]), filename, content, content.count(b"\f") + 1,
        )

    def read_citation_pdf(
        self, context: QuestionContext, citation_id: str,
    ) -> tuple[CitationPdfContent, int]:
        if not _SAFE_ID.fullmatch(citation_id):
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "citation.read"),
            ) as connection:
                row = connection.execute(
                    "SELECT c.canonical_json->>'source_id',c.source_version_id,"
                    "c.canonical_json->>'page',es.canonical_json->>'page' "
                    "FROM citations c JOIN evidence_spans es ON "
                    "es.tenant_id=c.tenant_id AND es.workspace_id=c.workspace_id "
                    "AND es.record_id=c.evidence_span_id WHERE c.record_id=%s",
                    (citation_id,),
                ).fetchone()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if row is None:
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        try:
            source_id, source_version_id, page = str(row[0]), str(row[1]), int(row[2])
            evidence_page = int(row[3])
        except (TypeError, ValueError):
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404) from None
        content = self.read_current_pdf(context, source_id, source_version_id)
        if page < 1 or page != evidence_page:
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        return content, page

    def persist_completed(
        self, context: QuestionContext, *, run_id: str, source_id: str,
        source_version_id: str, question: str, selection: TextModelSelection,
        evidence: tuple[IndexedEvidenceChunk, ...], result: GroundedTextResult,
        provider_called: bool = True,
        egress_authorization: Mapping[str, object] | None = None,
    ) -> StoredQuestionAnswer:
        if not _SAFE_ID.fullmatch(run_id) or not question.strip():
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        evidence_by_id = {item.chunk_id: item for item in evidence}
        if any(chunk_id not in evidence_by_id for chunk_id in result.cited_chunk_ids):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")

        scope_id = self._opaque_id("scope", source_version_id)
        scope_snapshot_id = self._opaque_id("scope-snapshot", run_id)
        provider_id = self._opaque_id(
            "provider", selection.profile_id, str(selection.binding_version),
        )
        artifact_id = self._opaque_id("artifact", selection.provider_code, selection.model_id)
        deployment_id = self._opaque_id(
            "deployment", selection.deployment_id, str(selection.binding_version),
        )
        authorization = dict(egress_authorization or {})
        routing_policy_id = authorization.get("routing_policy_version_id") or self._opaque_id(
            "routing-policy", str(selection.binding_version), selection.deployment_id,
        )
        routing_decision_id = authorization.get("routing_decision_id") or self._opaque_id("routing", run_id)
        egress_decision_id = authorization.get("egress_decision_id")
        attempt_id = self._opaque_id("attempt", run_id, selection.deployment_id)
        run_snapshot_id = self._opaque_id("run-snapshot", run_id)
        run_result_id = self._opaque_id("run-result", run_id)

        citations = tuple(
            StoredCitation(
                self._opaque_id("citation", run_result_id, chunk_id), source_id,
                source_version_id, evidence_by_id[chunk_id].evidence_span_id,
                evidence_by_id[chunk_id].page,
            )
            for chunk_id in result.cited_chunk_ids
        )
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "question.execute"),
            ) as connection:
                self._insert_canon(connection, context, "knowledge_scopes", scope_id, {
                    "source_version_ids": [source_version_id], "mode": "single_source",
                })
                self._insert_canon(connection, context, "scope_snapshots", scope_snapshot_id, {
                    "knowledge_scope_id": scope_id, "source_version_ids": [source_version_id],
                }, extra_columns=("knowledge_scope_id",), extra_values=(scope_id,))
                self._insert_canon(connection, context, "provider_profiles", provider_id, {
                    "configured_profile_id": selection.profile_id,
                    "provider_code": selection.provider_code,
                    "binding_version": selection.binding_version,
                })
                self._insert_canon(connection, context, "model_artifacts", artifact_id, {
                    "provider_code": selection.provider_code, "model_id": selection.model_id,
                })
                self._insert_canon(connection, context, "model_deployments", deployment_id, {
                    "configured_deployment_id": selection.deployment_id,
                    "model_id": selection.model_id, "role": "text",
                    "binding_version": selection.binding_version,
                }, extra_columns=("provider_profile_id", "model_artifact_id"),
                    extra_values=(provider_id, artifact_id))
                self._insert_canon(connection, context, "routing_policy_versions", routing_policy_id, {
                    "binding_version": selection.binding_version,
                    "candidate_order": [deployment_id], "role": "text",
                })
                self._insert_canon(connection, context, "runs", run_id, {
                    "question": question, "source_id": source_id,
                    "source_version_id": source_version_id,
                }, state="accepted")
                version = self._transition(connection, context, run_id, 1, "planning")
                run_snapshot_payload: dict[str, object] = {
                    "source_version_ids": [source_version_id],
                    "knowledge_scope_id": scope_id, "authority": ["source"],
                    "weights_requested": {}, "weights_effective": {}, "weight_clamps": [],
                    "ruleset_snapshot_ids": [], "routing_policy_version_id": routing_policy_id,
                    "candidate_order": [deployment_id], "data_area": "workspace",
                    "data_classification": "workspace_private",
                    "egress_decision_id": egress_decision_id,
                    "egress_policy_fingerprint": authorization.get("policy_fingerprint"),
                    "frozen_routing_context": authorization.get("frozen_routing_context"),
                    "user_policy_version": context.policy_version,
                    "organization_policy_version": context.policy_version,
                    "cost_limit": 0, "currency": "USD",
                    "prompt_version": "cp3-grounded-question-v1",
                    "tool_version": "document-index-v1",
                }
                self._insert_canon(connection, context, "run_snapshots", run_snapshot_id,
                    run_snapshot_payload,
                    extra_columns=("run_id", "scope_snapshot_id", "routing_policy_version_id"),
                    extra_values=(run_id, scope_snapshot_id, routing_policy_id))
                version = self._transition(connection, context, run_id, version, "retrieving")
                version = self._transition(connection, context, run_id, version, "generating")
                if provider_called:
                    self._insert_canon(connection, context, "routing_decisions", routing_decision_id, {
                        "run_id": run_id, "selected_deployment_id": deployment_id,
                        "candidate_order": [deployment_id], "reason": "selected_text_role_binding",
                        "egress_decision_id": egress_decision_id,
                    }, extra_columns=("run_id", "routing_policy_version_id", "egress_decision_id"),
                        extra_values=(run_id, routing_policy_id, egress_decision_id))
                    self._insert_canon(connection, context, "model_attempts", attempt_id, {
                        "run_id": run_id, "deployment_id": deployment_id,
                        "model_artifact_id": artifact_id, "outcome": "succeeded",
                        "usage": dict(result.usage),
                    }, extra_columns=(
                        "routing_decision_id", "model_deployment_id", "model_artifact_id",
                        "candidate_order", "started_at", "finished_at", "usage_json",
                    ), extra_values=(
                        routing_decision_id, deployment_id, artifact_id, 1,
                        datetime.now(timezone.utc), datetime.now(timezone.utc),
                        Jsonb(dict(result.usage)),
                    ))
                version = self._transition(connection, context, run_id, version, "validating")
                self._insert_canon(connection, context, "run_results", run_result_id, {
                    "run_id": run_id, "answer": result.answer,
                    "insufficient": result.insufficient,
                    "citation_ids": [item.citation_id for item in citations],
                }, extra_columns=("run_id", "routing_decision_id", "selected_model_attempt_id"),
                    extra_values=(
                        run_id,
                        routing_decision_id if provider_called else None,
                        attempt_id if provider_called else None,
                    ))
                for citation in citations:
                    self._insert_canon(connection, context, "citations", citation.citation_id, {
                        "run_result_id": run_result_id, "source_id": source_id,
                        "source_version_id": source_version_id,
                        "evidence_span_id": citation.evidence_span_id, "page": citation.page,
                    }, extra_columns=("run_result_id", "source_version_id", "evidence_span_id"),
                        extra_values=(run_result_id, source_version_id, citation.evidence_span_id))
                self._transition(connection, context, run_id, version, "completed")
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,"
                    "target_type,target_id,outcome,trace_id,policy_version,metadata) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        self._opaque_id("audit", run_id), context.tenant_id,
                        context.workspace_id, context.actor_id, "question.answer",
                        "Run", run_id, "succeeded", context.trace_id,
                        context.policy_version, json.dumps({
                            "source_id": source_id, "source_version_id": source_version_id,
                            "citation_count": len(citations),
                        }),
                    ),
                )
        except QuestionRepositoryError:
            raise
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        return StoredQuestionAnswer(
            run_id, run_result_id, result.answer, result.insufficient, citations,
        )
