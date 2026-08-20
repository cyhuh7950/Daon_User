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
    notebook_id: str | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
            self.trace_id, self.policy_version,
        )) or (self.notebook_id is not None and not _SAFE_ID.fullmatch(self.notebook_id)):
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
class CitationContent:
    source_id: str
    source_version_id: str
    filename: str
    content: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class StoredCitation:
    citation_id: str
    source_id: str
    source_version_id: str
    evidence_span_id: str
    page: int
    origin: str = "raw_source"
    context_item_id: str = ""
    locator: Mapping[str, str] | None = None


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
                notebook_clause = (
                    " AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE "
                    "nb.tenant_id=r.tenant_id AND nb.workspace_id=r.workspace_id "
                    "AND nb.notebook_id=%s AND nb.binding_kind='conversation_thread' "
                    "AND nb.record_id=r.conversation_id)"
                    if context.notebook_id is not None else ""
                )
                params: tuple[object, ...] = (
                    (run_id, context.notebook_id)
                    if context.notebook_id is not None else (run_id,)
                )
                rows = connection.execute(
                    "SELECT rr.record_id,rr.canonical_json,c.record_id,c.canonical_json "
                    "FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id "
                    "AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id "
                    "LEFT JOIN citations c ON c.tenant_id=rr.tenant_id "
                    "AND c.workspace_id=rr.workspace_id AND c.run_result_id=rr.record_id "
                    "WHERE r.record_id=%s AND r.state='completed'" + notebook_clause
                    + " ORDER BY c.record_id",
                    params,
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
                    str(cast(Mapping[str, object], row[3]).get("origin", "raw_source")),
                    str(cast(Mapping[str, object], row[3]).get(
                        "context_item_id",
                        cast(Mapping[str, object], row[3])["source_version_id"],
                    )),
                    cast(
                        Mapping[str, str],
                        cast(Mapping[str, object], row[3]).get(
                            "locator",
                            {"kind": "page", "value": str(
                                cast(Mapping[str, object], row[3])["page"]
                            )},
                        ),
                    ),
                )
                for row in rows if row[2] is not None and row[3] is not None
            )
            return StoredQuestionAnswer(
                run_id, str(rows[0][0]), str(result_payload["answer"]),
                bool(result_payload["insufficient"]), citations,
            )
        except (KeyError, TypeError, ValueError):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID", status=500) from None

    def load_completed_for_replay(
        self, context: QuestionContext, run_id: str, request_fingerprint: str,
    ) -> StoredQuestionAnswer | None:
        if not _SAFE_ID.fullmatch(run_id) or not re.fullmatch(r"sha256:[0-9a-f]{64}", request_fingerprint):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "question.read"),
            ) as connection:
                row = connection.execute(
                    "SELECT r.canonical_json->>'request_fingerprint' FROM runs r "
                    "WHERE r.record_id=%s AND r.state='completed' AND EXISTS ("
                    "SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=r.tenant_id "
                    "AND nb.workspace_id=r.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='conversation_thread' AND nb.record_id=r.conversation_id)",
                    (run_id, context.notebook_id),
                ).fetchone()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if row is None:
            return None
        stored_fingerprint = row[0]
        if not isinstance(stored_fingerprint, str):
            raise QuestionRepositoryError("QUESTION_REPLAY_UNAVAILABLE", status=409)
        if stored_fingerprint != request_fingerprint:
            raise QuestionRepositoryError("IDEMPOTENCY_KEY_REUSED", status=409)
        result = self.load_completed(context, run_id)
        if result is None:
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID", status=500)
        return result

    def load_ready_source(
        self, context: QuestionContext, source_id: str, source_version_id: str,
    ) -> ReadyQuestionSource:
        if not _SAFE_ID.fullmatch(source_id) or not _SAFE_ID.fullmatch(source_version_id):
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "question.read"),
            ) as connection:
                row = connection.execute(
                    "SELECT s.record_id,sv.record_id,s.state,"
                    "COALESCE(sv.canonical_json->>'filename',s.canonical_json->>'filename',sv.record_id),"
                    "s.canonical_json->>'kind' FROM sources s JOIN source_versions sv ON "
                    "sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id "
                    "AND sv.source_id=s.record_id WHERE s.record_id=%s AND sv.record_id=%s "
                    "AND s.state='ready' AND EXISTS (SELECT 1 FROM index_versions iv WHERE "
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
        filename = str(row[3])
        if str(row[2]) != "ready" or (
            str(row[4]) != "studio_output" and not filename.lower().endswith(".pdf")
        ):
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
                notebook_clause = (
                    " AND EXISTS (SELECT 1 FROM run_results rr JOIN runs r ON "
                    "r.tenant_id=rr.tenant_id AND r.workspace_id=rr.workspace_id "
                    "AND r.record_id=rr.run_id JOIN notebook_bindings nb ON "
                    "nb.tenant_id=r.tenant_id AND nb.workspace_id=r.workspace_id "
                    "AND nb.binding_kind='conversation_thread' AND nb.record_id=r.conversation_id "
                    "WHERE rr.tenant_id=c.tenant_id AND rr.workspace_id=c.workspace_id "
                    "AND rr.record_id=c.run_result_id AND nb.notebook_id=%s)"
                    if context.notebook_id is not None else ""
                )
                row = connection.execute(
                    "SELECT c.canonical_json->>'source_id',c.source_version_id,"
                    "c.canonical_json->>'page',es.canonical_json->>'page' "
                    "FROM citations c JOIN evidence_spans es ON "
                    "es.tenant_id=c.tenant_id AND es.workspace_id=c.workspace_id "
                    "AND es.record_id=c.evidence_span_id WHERE c.record_id=%s" + notebook_clause,
                    (citation_id, context.notebook_id)
                    if context.notebook_id is not None else (citation_id,),
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

    def read_citation_content(
        self, context: QuestionContext, citation_id: str,
    ) -> tuple[CitationContent, dict[str, str]]:
        if not _SAFE_ID.fullmatch(citation_id):
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "citation.read"),
            ) as connection:
                notebook_clause = (
                    " AND EXISTS (SELECT 1 FROM run_results rr JOIN runs r ON "
                    "r.tenant_id=rr.tenant_id AND r.workspace_id=rr.workspace_id "
                    "AND r.record_id=rr.run_id JOIN notebook_bindings nb ON "
                    "nb.tenant_id=r.tenant_id AND nb.workspace_id=r.workspace_id "
                    "AND nb.binding_kind='conversation_thread' AND nb.record_id=r.conversation_id "
                    "WHERE rr.tenant_id=c.tenant_id AND rr.workspace_id=c.workspace_id "
                    "AND rr.record_id=c.run_result_id AND nb.notebook_id=%s)"
                    if context.notebook_id is not None else ""
                )
                row = connection.execute(
                    "SELECT c.canonical_json->>'source_id',c.source_version_id,"
                    "c.canonical_json->>'page',es.canonical_json->>'page',"
                    "es.canonical_json,s.canonical_json->>'kind',"
                    "COALESCE(sv.canonical_json->>'filename',s.canonical_json->>'filename',sv.record_id),"
                    "s.state,es.record_id FROM citations c JOIN evidence_spans es ON "
                    "es.tenant_id=c.tenant_id AND es.workspace_id=c.workspace_id "
                    "AND es.record_id=c.evidence_span_id JOIN source_versions sv ON "
                    "sv.tenant_id=c.tenant_id AND sv.workspace_id=c.workspace_id "
                    "AND sv.record_id=c.source_version_id JOIN sources s ON "
                    "s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id "
                    "AND s.record_id=sv.source_id WHERE c.record_id=%s AND s.state='ready' "
                    "AND EXISTS (SELECT 1 FROM index_versions iv WHERE "
                    "iv.tenant_id=sv.tenant_id AND iv.workspace_id=sv.workspace_id "
                    "AND iv.source_version_id=sv.record_id) AND NOT EXISTS ("
                    "SELECT 1 FROM source_versions newer WHERE newer.tenant_id=sv.tenant_id "
                    "AND newer.workspace_id=sv.workspace_id AND newer.source_id=sv.source_id "
                    "AND newer.version>sv.version)" + notebook_clause,
                    (citation_id, context.notebook_id)
                    if context.notebook_id is not None else (citation_id,),
                ).fetchone()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if row is None:
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        try:
            source_id = str(row[0])
            source_version_id = str(row[1])
            page = int(row[2])
            evidence_page = int(row[3])
            evidence = cast(Mapping[str, object], row[4])
            source_kind = str(row[5])
            filename = str(row[6])
            source_state = str(row[7])
            evidence_span_id = str(row[8])
        except (TypeError, ValueError):
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404) from None
        if page < 1 or page != evidence_page or source_state != "ready":
            raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
        if source_kind == "studio_output":
            if evidence.get("kind") != "approved_knowledge_snapshot":
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            text = evidence.get("text")
            if not isinstance(text, str) or not text.strip():
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            content = text.encode("utf-8")
            if len(content) > 1024 * 1024:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            return CitationContent(
                source_id, source_version_id, filename, content,
                "text/plain; charset=utf-8",
            ), {"kind": "section", "value": evidence_span_id}
        pdf, pdf_page = self.read_citation_pdf(context, citation_id)
        return CitationContent(
            pdf.source_id, pdf.source_version_id, pdf.filename,
            pdf.content, "application/pdf",
        ), {"kind": "page", "value": str(pdf_page)}

    def persist_completed(
        self, context: QuestionContext, *, run_id: str, source_id: str | None,
        source_version_id: str | None, question: str, selection: TextModelSelection,
        evidence: tuple[IndexedEvidenceChunk, ...], result: GroundedTextResult,
        provider_called: bool = True,
        egress_authorization: Mapping[str, object] | None = None,
        context_mode: str = "raw_only",
        context_sources: tuple[object, ...] | None = None,
        request_fingerprint: str | None = None,
    ) -> StoredQuestionAnswer:
        if (
            not _SAFE_ID.fullmatch(run_id) or not question.strip()
            or request_fingerprint is not None
            and not re.fullmatch(r"sha256:[0-9a-f]{64}", request_fingerprint)
        ):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        evidence_by_id = {item.chunk_id: item for item in evidence}
        if any(chunk_id not in evidence_by_id for chunk_id in result.cited_chunk_ids):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")

        context_items = tuple(context_sources or ())
        general = context_mode == "general_ungrounded"
        if general:
            if context_items or source_id is not None or source_version_id is not None or evidence or result.cited_chunk_ids:
                raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        elif not context_items:
            if source_id is None or source_version_id is None:
                raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
            context_items = (type("LegacyQuestionSource", (), {
                "origin": "raw_source", "context_item_id": source_id,
                "source_id": source_id, "source_version_id": source_version_id,
                "digest_sha256": None,
            })(),)
        source_versions = [str(item.source_version_id) for item in context_items]
        if context_mode not in {"raw_only", "daon_priority", "mixed", "general_ungrounded"}:
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        source_context = {
            (str(item.source_id), str(item.source_version_id)): item for item in context_items
        }
        if len(source_context) != len(context_items):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        if any(
            (item.source_id, item.source_version_id) not in source_context
            for item in evidence
        ):
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID")
        scope_id = self._opaque_id("scope", context_mode, *source_versions)
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
        conversation_id = self._opaque_id("conversation", run_id)

        citations = tuple(
            StoredCitation(
                self._opaque_id("citation", run_result_id, chunk_id),
                evidence_by_id[chunk_id].source_id,
                evidence_by_id[chunk_id].source_version_id,
                evidence_by_id[chunk_id].evidence_span_id,
                evidence_by_id[chunk_id].page,
                str(source_context[(
                    evidence_by_id[chunk_id].source_id,
                    evidence_by_id[chunk_id].source_version_id,
                )].origin),
                str(source_context[(
                    evidence_by_id[chunk_id].source_id,
                    evidence_by_id[chunk_id].source_version_id,
                )].context_item_id),
                {
                    "kind": "section" if str(source_context[(
                        evidence_by_id[chunk_id].source_id,
                        evidence_by_id[chunk_id].source_version_id,
                    )].origin) == "daon_knowledge" else "page",
                    "value": evidence_by_id[chunk_id].evidence_span_id
                    if str(source_context[(
                        evidence_by_id[chunk_id].source_id,
                        evidence_by_id[chunk_id].source_version_id,
                    )].origin) == "daon_knowledge"
                    else str(evidence_by_id[chunk_id].page),
                },
            )
            for chunk_id in result.cited_chunk_ids
        )
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, "question.execute"),
            ) as connection:
                if context.notebook_id is not None:
                    for item in context_items:
                        binding_kind = (
                            "knowledge_context"
                            if str(item.origin) == "daon_knowledge" else "source"
                        )
                        record_id = (
                            str(item.context_item_id)
                            if binding_kind == "knowledge_context" else str(item.source_id)
                        )
                        version_id = (
                            None if binding_kind == "knowledge_context"
                            else str(item.source_version_id)
                        )
                        selected = connection.execute(
                            "SELECT 1 FROM notebooks n WHERE n.tenant_id=%s AND n.workspace_id=%s "
                            "AND n.notebook_id=%s AND EXISTS (SELECT 1 FROM notebook_bindings nb "
                            "WHERE nb.tenant_id=n.tenant_id AND nb.workspace_id=n.workspace_id "
                            "AND nb.notebook_id=n.notebook_id AND nb.binding_kind=%s "
                            "AND nb.record_id=%s AND nb.version_id IS NOT DISTINCT FROM %s)",
                            (
                                context.tenant_id, context.workspace_id, context.notebook_id,
                                binding_kind, record_id, version_id,
                            ),
                        ).fetchone()
                        if selected is None:
                            raise QuestionRepositoryError(
                                "NOTEBOOK_SCOPE_MISMATCH", status=409,
                            )
                self._insert_canon(connection, context, "knowledge_scopes", scope_id, {
                    "source_version_ids": source_versions,
                    "mode": context_mode,
                    "items": [{
                        "origin": str(item.origin),
                        "context_item_id": str(item.context_item_id),
                        "source_id": str(item.source_id),
                        "source_version_id": str(item.source_version_id),
                        "digest_sha256": item.digest_sha256,
                    } for item in context_items],
                })
                self._insert_canon(connection, context, "scope_snapshots", scope_snapshot_id, {
                    "knowledge_scope_id": scope_id, "source_version_ids": source_versions,
                    "mode": context_mode,
                    "items": [{
                        "origin": str(item.origin),
                        "context_item_id": str(item.context_item_id),
                        "source_id": str(item.source_id),
                        "source_version_id": str(item.source_version_id),
                        "digest_sha256": item.digest_sha256,
                    } for item in context_items],
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
                self._insert_canon(connection, context, "conversations", conversation_id, {
                    "current_run_id": run_id, "current_run_result_id": run_result_id,
                })
                self._insert_canon(connection, context, "runs", run_id, {
                    "question": question, "source_id": source_id,
                    "source_version_id": source_version_id, "context_mode": context_mode,
                    "request_fingerprint": request_fingerprint,
                }, state="accepted", extra_columns=("conversation_id",),
                    extra_values=(conversation_id,))
                version = self._transition(connection, context, run_id, 1, "planning")
                run_snapshot_payload: dict[str, object] = {
                    "source_version_ids": source_versions,
                    "knowledge_scope_id": scope_id, "authority": [] if general else ["source"],
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
                    "prompt_version": "general-conversation-v1" if general else "cp3-grounded-question-v1",
                    "tool_version": "provider-chat-v1" if general else "document-index-v1",
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
                        "run_result_id": run_result_id, "source_id": citation.source_id,
                        "source_version_id": citation.source_version_id,
                        "evidence_span_id": citation.evidence_span_id, "page": citation.page,
                        "origin": citation.origin,
                        "context_item_id": citation.context_item_id,
                        "locator": dict(citation.locator or {
                            "kind": "page", "value": str(citation.page),
                        }),
                    }, extra_columns=("run_result_id", "source_version_id", "evidence_span_id"),
                        extra_values=(run_result_id, citation.source_version_id, citation.evidence_span_id))
                self._transition(connection, context, run_id, version, "completed")
                if context.notebook_id is not None:
                    connection.execute(
                        "WITH inserted AS (INSERT INTO notebook_bindings "
                        "(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                        "VALUES (%s,%s,%s,'conversation_thread',%s,NULL,%s,%s) "
                        "ON CONFLICT DO NOTHING RETURNING 1) "
                        "INSERT INTO notebook_activities "
                        "(tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) "
                        "SELECT %s,%s,%s,coalesce((SELECT max(sequence) FROM notebook_activities "
                        "WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s),0)+1,"
                        "'context_bound',%s,%s FROM inserted",
                        (
                            context.tenant_id, context.workspace_id, context.notebook_id,
                            conversation_id, context.actor_id, datetime.now(timezone.utc),
                            context.tenant_id, context.workspace_id, context.notebook_id,
                            context.tenant_id, context.workspace_id, context.notebook_id,
                            context.actor_id, datetime.now(timezone.utc),
                        ),
                    )
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
                            "context_mode": context_mode,
                            "source_version_ids": source_versions,
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
