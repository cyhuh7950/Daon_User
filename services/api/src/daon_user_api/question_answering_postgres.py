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
from .document_processing import text_source_format
from .document_understanding_adapter import DocumentUnderstandingError
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
    provider_kind: str | None = None
    egress_scope: Mapping[str, object] | None = None


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
    def _replay_egress_scope(
        selection: TextModelSelection, frozen_scope: object,
    ) -> Mapping[str, object] | None:
        if selection.provider_kind != "external_api" or not isinstance(frozen_scope, Mapping):
            return None
        return {
            "classification": frozen_scope.get("classification"),
            "destination": frozen_scope.get("destination"),
            "masking_required": frozen_scope.get("masking_required"),
            "max_bytes": frozen_scope.get("payload_bytes"),
            "redaction_required": frozen_scope.get("redaction_required"),
        }

    @staticmethod
    def _run_canonical_payload(
        *, question: str, source_id: str | None, source_version_id: str | None,
        context_mode: str, request_fingerprint: str | None,
        selection: TextModelSelection, frozen_scope: object,
    ) -> Mapping[str, object]:
        return {
            "question": question,
            "source_id": source_id,
            "source_version_id": source_version_id,
            "context_mode": context_mode,
            "request_fingerprint": request_fingerprint,
            "provider_kind": selection.provider_kind,
            "connection_id": selection.connection_id,
            "provider_code": selection.provider_code,
            "model_id": selection.model_id,
            "credential_version": selection.credential_version,
            "default_version": selection.binding_version,
            "catalog_version": selection.catalog_version,
            "routing_owner": selection.routing_owner,
            "daon_fallback_allowed": selection.daon_fallback_allowed,
            "egress_scope": PostgresQuestionAnsweringRepository._replay_egress_scope(
                selection, frozen_scope,
            ),
            "frozen_routing_context": frozen_scope,
        }

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
        capability: str, *, require_notebook_binding: bool = False,
    ) -> tuple[object, ...]:
        if not _SAFE_ID.fullmatch(source_id) or not _SAFE_ID.fullmatch(source_version_id):
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        try:
            with self._cloud_store._transaction(
                self._cloud_context(context, capability),
            ) as connection:
                notebook_clause = (
                    " AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE "
                    "nb.tenant_id=sv.tenant_id AND nb.workspace_id=sv.workspace_id "
                    "AND nb.notebook_id=%s AND nb.binding_kind='source' "
                    "AND nb.record_id=sv.source_id AND nb.version_id=sv.record_id "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_source_unbindings u WHERE "
                    "u.tenant_id=nb.tenant_id AND u.workspace_id=nb.workspace_id "
                    "AND u.notebook_id=nb.notebook_id AND u.source_id=nb.record_id "
                    "AND u.source_version_id=nb.version_id))"
                    if require_notebook_binding and context.notebook_id is not None else ""
                )
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
                    "AND newer.version>sv.version) AND NOT EXISTS (SELECT 1 FROM deletion_requests dr "
                    "WHERE dr.tenant_id=s.tenant_id AND dr.workspace_id=s.workspace_id "
                    "AND dr.source_id=s.record_id AND dr.source_active=false)" + notebook_clause,
                    (source_id, source_version_id, context.notebook_id)
                    if notebook_clause else (source_id, source_version_id),
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
                    "SELECT rr.record_id,rr.canonical_json,c.record_id,c.canonical_json,"
                    "r.canonical_json->>'provider_kind',r.canonical_json->'egress_scope' "
                    "FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id "
                    "AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id "
                    "LEFT JOIN citations c ON c.tenant_id=rr.tenant_id "
                    "AND c.workspace_id=rr.workspace_id AND c.run_result_id=rr.record_id "
                    "WHERE r.record_id=%s AND r.state='completed' AND NOT EXISTS (SELECT 1 FROM citations blocked_citation "
                    "JOIN deletion_requests dr ON dr.tenant_id=blocked_citation.tenant_id "
                    "AND dr.workspace_id=blocked_citation.workspace_id "
                    "AND dr.source_id=blocked_citation.canonical_json->>'source_id' AND dr.source_active=false "
                    "WHERE blocked_citation.tenant_id=rr.tenant_id AND blocked_citation.workspace_id=rr.workspace_id "
                    "AND blocked_citation.run_result_id=rr.record_id)" + notebook_clause
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
                str(rows[0][4]) if rows[0][4] is not None else None,
                cast(Mapping[str, object], rows[0][5]) if rows[0][5] is not None else None,
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
                    "SELECT r.canonical_json->>'request_fingerprint',EXISTS ("
                    "SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=r.tenant_id "
                    "AND nb.workspace_id=r.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='conversation_thread' AND nb.record_id=r.conversation_id) "
                    "FROM runs r WHERE r.record_id=%s AND r.state='completed'",
                    (context.notebook_id, run_id),
                ).fetchone()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if row is None:
            return None
        if row[1] is not True:
            raise QuestionRepositoryError("NOTEBOOK_NOT_FOUND", status=404)
        stored_fingerprint = row[0]
        if not isinstance(stored_fingerprint, str):
            raise QuestionRepositoryError("QUESTION_REPLAY_UNAVAILABLE", status=409)
        if stored_fingerprint != request_fingerprint:
            raise QuestionRepositoryError("IDEMPOTENCY_KEY_REUSED", status=409)
        result = self.load_completed(context, run_id)
        if result is None:
            raise QuestionRepositoryError("QUESTION_RESULT_INVALID", status=500)
        if result.provider_kind not in {"external_api", "server_internal", "local_runtime"}:
            raise QuestionRepositoryError("QUESTION_REPLAY_UNAVAILABLE", status=409)
        if result.provider_kind == "external_api" and not isinstance(result.egress_scope, Mapping):
            raise QuestionRepositoryError("QUESTION_REPLAY_UNAVAILABLE", status=409)
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
                notebook_clause = (
                    " AND (s.canonical_json->>'kind'='studio_output' OR EXISTS ("
                    "SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=sv.tenant_id "
                    "AND nb.workspace_id=sv.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='source' AND nb.record_id=sv.source_id "
                    "AND nb.version_id=sv.record_id AND NOT EXISTS ("
                    "SELECT 1 FROM notebook_source_unbindings u WHERE "
                    "u.tenant_id=nb.tenant_id AND u.workspace_id=nb.workspace_id "
                    "AND u.notebook_id=nb.notebook_id AND u.source_id=nb.record_id "
                    "AND u.source_version_id=nb.version_id)))"
                    if context.notebook_id is not None else ""
                )
                row = connection.execute(
                    "SELECT s.record_id,sv.record_id,s.state,"
                    "COALESCE(sv.canonical_json->>'filename',s.canonical_json->>'filename',sv.record_id),"
                    "s.canonical_json->>'kind',o.content_type,o.status FROM sources s JOIN source_versions sv ON "
                    "sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id "
                    "AND sv.source_id=s.record_id LEFT JOIN object_records o ON "
                    "o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id "
                    "AND o.object_id=sv.object_id WHERE s.record_id=%s AND sv.record_id=%s "
                    "AND s.state='ready' AND EXISTS (SELECT 1 FROM index_versions iv WHERE "
                    "iv.tenant_id=sv.tenant_id AND iv.workspace_id=sv.workspace_id "
                    "AND iv.source_version_id=sv.record_id) AND NOT EXISTS ("
                    "SELECT 1 FROM source_versions newer WHERE newer.tenant_id=sv.tenant_id "
                    "AND newer.workspace_id=sv.workspace_id AND newer.source_id=sv.source_id "
                    "AND newer.version>sv.version) AND NOT EXISTS (SELECT 1 FROM deletion_requests dr "
                    "WHERE dr.tenant_id=s.tenant_id AND dr.workspace_id=s.workspace_id "
                    "AND dr.source_id=s.record_id AND dr.source_active=false)" + notebook_clause,
                    (source_id, source_version_id, context.notebook_id)
                    if notebook_clause else (source_id, source_version_id),
                ).fetchone()
        except CloudDatabaseError as error:
            raise QuestionRepositoryError(
                "QUESTION_DATABASE_UNAVAILABLE", status=503, retryable=error.retryable,
            ) from None
        if row is None:
            raise QuestionRepositoryError("QUESTION_SOURCE_UNAVAILABLE", status=404)
        filename = str(row[3])
        xlsx_ready = (filename.lower().endswith('.xlsx') and len(row) == 7
            and str(row[5]) == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            and str(row[6]) == 'completed')
        docx_ready = (filename.lower().endswith('.docx') and len(row) == 7
            and str(row[5]) == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            and str(row[6]) == 'completed')
        pptx_ready = (filename.lower().endswith('.pptx') and len(row) == 7
            and str(row[5]) == 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            and str(row[6]) == 'completed')
        audio_ready = (filename.lower().endswith('.wav') and len(row) == 7
            and str(row[5]) == 'audio/wav' and str(row[6]) == 'completed')
        if str(row[2]) != "ready" or (
            str(row[4]) != "studio_output"
            and not (filename.lower().endswith((".pdf", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg")) or xlsx_ready or docx_ready or pptx_ready or audio_ready)
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
                    "AND es.record_id=c.evidence_span_id WHERE c.record_id=%s "
                    "AND NOT EXISTS (SELECT 1 FROM deletion_requests dr WHERE dr.tenant_id=c.tenant_id "
                    "AND dr.workspace_id=c.workspace_id AND dr.source_id=c.canonical_json->>'source_id' "
                    "AND dr.source_active=false)" + notebook_clause,
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
                    "s.state,es.record_id,c.canonical_json->'locator' FROM citations c JOIN evidence_spans es ON "
                    "es.tenant_id=c.tenant_id AND es.workspace_id=c.workspace_id "
                    "AND es.record_id=c.evidence_span_id AND es.source_version_id=c.source_version_id JOIN source_versions sv ON "
                    "sv.tenant_id=c.tenant_id AND sv.workspace_id=c.workspace_id "
                    "AND sv.record_id=c.source_version_id JOIN sources s ON "
                    "s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id "
                    "AND s.record_id=sv.source_id WHERE c.record_id=%s AND s.state='ready' "
                    "AND EXISTS (SELECT 1 FROM index_versions iv WHERE "
                    "iv.tenant_id=sv.tenant_id AND iv.workspace_id=sv.workspace_id "
                    "AND iv.source_version_id=sv.record_id) AND NOT EXISTS ("
                    "SELECT 1 FROM source_versions newer WHERE newer.tenant_id=sv.tenant_id "
                    "AND newer.workspace_id=sv.workspace_id AND newer.source_id=sv.source_id "
                    "AND newer.version>sv.version) AND NOT EXISTS (SELECT 1 FROM deletion_requests dr "
                    "WHERE dr.tenant_id=s.tenant_id AND dr.workspace_id=s.workspace_id "
                    "AND dr.source_id=s.record_id AND dr.source_active=false)" + notebook_clause,
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
        stored_locator = row[9] if len(row) > 9 else None
        if evidence.get('kind') == 'audio_segment_fact' or isinstance(stored_locator, dict) and stored_locator.get('kind') == 'audio_segment':
            from .audio_source import read_wav, read_audio_transcript_view
            from .audio_document_understanding import read_audio_result_transcript
            from .document_processing import StoredSourceDocument
            from .document_understanding_adapter import DocumentUnderstandingResult, ParserValidation, SemanticUnderstanding
            from .source_ingest import SourceRejected
            locator = evidence.get('locator')
            understanding_id = evidence.get('understanding_result_id')
            if (context.notebook_id is None or evidence.get('kind') != 'audio_segment_fact'
                or evidence.get('source_id') != source_id or evidence.get('source_version_id') != source_version_id
                or not isinstance(locator, dict) or set(locator) != {'kind', 'value'} or locator.get('kind') != 'audio_segment'
                or locator != stored_locator or page != 1 or not isinstance(understanding_id, str) or not _SAFE_ID.fullmatch(understanding_id)):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
            original = self._row(context, source_id, source_version_id, 'citation.read', require_notebook_binding=True)
            if str(original[3]) != filename or not filename.lower().endswith('.wav') or str(original[8]) != 'audio/wav' or str(original[9]) != 'completed':
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
            try:
                with self._cloud_store._transaction(self._cloud_context(context, 'citation.read')) as connection:
                    stored = connection.execute('SELECT record_id,canonical_json FROM understanding_results WHERE record_id=%s AND source_version_id=%s',
                        (understanding_id, source_version_id)).fetchone()
                if stored is None or str(stored[0]) != understanding_id or not isinstance(stored[1], dict):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
                payload = stored[1];parser = payload.get('parser_validation');semantic = payload.get('semantic')
                if (payload.get('source_id') != source_id or payload.get('source_version_id') != source_version_id
                    or payload.get('status') != 'ready' or payload.get('conflict') is not None
                    or not isinstance(parser, dict) or set(parser) != {'text', 'markdown', 'html', 'pages', 'page_texts', 'role', 'regions', 'audio_evidence'}
                    or not isinstance(semantic, dict) or set(semantic) != {'title', 'summary', 'key_facts'}
                    or not isinstance(semantic['key_facts'], list) or not all(isinstance(v, str) for v in semantic['key_facts'])
                    or not isinstance(parser['audio_evidence'], list) or not isinstance(payload.get('lineage'), dict)):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                ObjectKeyPolicy().validate_final(self._cloud_context(context, 'object.read'), 'source', str(original[5]))
                content = self._object_storage.get(str(original[5]))
                if len(content) != int(original[7]) or hashlib.sha256(content).hexdigest() != str(original[6]).removeprefix('sha256:'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                understanding = DocumentUnderstandingResult(source_id, source_version_id, 'ready', (),
                    SemanticUnderstanding(semantic['title'], semantic['summary'], tuple(semantic['key_facts'])),
                    ParserValidation(parser['text'], parser['markdown'], parser['html'], tuple(parser['pages']), tuple(parser['page_texts']),
                        role=parser['role'], regions=tuple(parser['regions']), audio_evidence=tuple(parser['audio_evidence'])), payload['lineage'])
                transcript = read_audio_result_transcript(StoredSourceDocument(source_id, source_version_id, filename, content, 'audio/wav'), understanding)
                if payload['lineage']['transcript_sha256'] != evidence.get('transcript_sha256'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                view = read_audio_transcript_view(read_wav(content), transcript, locator['value'])
                selected = next(s for s in view['segments'] if s['segment_id'] == view['selected_segment_id'])
                if selected['text'][view['selected_start']:view['selected_end']] != evidence.get('text'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                view['source_version_id'] = source_version_id
                body = canonical_json_bytes(view)
                if len(body) > 32768: raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                return CitationContent(source_id, source_version_id, filename, body, 'application/json'), locator
            except (SourceRejected, DocumentUnderstandingError, ValueError, TypeError, KeyError, StopIteration):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409) from None
            except (CloudDatabaseError, ObjectQueueError, ObjectStorageError):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=503) from None
        if evidence.get('kind') == 'docx_block_fact' or isinstance(stored_locator, dict) and stored_locator.get('kind') == 'docx_block':
            from .source_ingest import SourceRejected
            from .docx_source import read_docx, read_docx_block_view
            locator = evidence.get('locator')
            if (context.notebook_id is None or evidence.get('kind') != 'docx_block_fact'
                or evidence.get('source_id') != source_id or evidence.get('source_version_id') != source_version_id
                or not isinstance(locator, dict) or set(locator) != {'kind', 'value'}
                or locator.get('kind') != 'docx_block' or locator != stored_locator or page != 1):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
            original = self._row(context, source_id, source_version_id, 'citation.read', require_notebook_binding=True)
            if (str(original[3]) != filename or not filename.lower().endswith('.docx')
                or str(original[8]) != 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                or str(original[9]) != 'completed'):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
            try:
                ObjectKeyPolicy().validate_final(self._cloud_context(context, 'object.read'), 'source', str(original[5]))
                content = self._object_storage.get(str(original[5]))
                if len(content) != int(original[7]) or hashlib.sha256(content).hexdigest() != str(original[6]).removeprefix('sha256:'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                view = read_docx_block_view(read_docx(content), locator['value'])
                selected = next(b for b in view['blocks'] if b['block_id'] == view['selected_block_id'])
                if selected['text'][view['selected_start']:view['selected_end']] != evidence.get('text'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                view['source_version_id'] = source_version_id
            except SourceRejected:
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409) from None
            except (ObjectQueueError, ObjectStorageError):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=503) from None
            body = canonical_json_bytes(view)
            if len(body) > 32 * 1024:
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
            return CitationContent(source_id, source_version_id, filename, body, 'application/json'), locator
        if evidence.get('kind') == 'pptx_block_fact' or isinstance(stored_locator, dict) and stored_locator.get('kind') == 'pptx_block':
            from .source_ingest import SourceRejected
            from .pptx_source import read_pptx, read_pptx_block_view
            locator = evidence.get('locator')
            if (context.notebook_id is None or evidence.get('kind') != 'pptx_block_fact'
                or evidence.get('source_id') != source_id or evidence.get('source_version_id') != source_version_id
                or not isinstance(locator, dict) or set(locator) != {'kind', 'value'}
                or locator.get('kind') != 'pptx_block' or locator != stored_locator or page != 1):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
            original = self._row(context, source_id, source_version_id, 'citation.read', require_notebook_binding=True)
            if (str(original[3]) != filename or not filename.lower().endswith('.pptx')
                or str(original[8]) != 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                or str(original[9]) != 'completed'):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=404)
            try:
                ObjectKeyPolicy().validate_final(self._cloud_context(context, 'object.read'), 'source', str(original[5]))
                content = self._object_storage.get(str(original[5]))
                if len(content) != int(original[7]) or hashlib.sha256(content).hexdigest() != str(original[6]).removeprefix('sha256:'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                view = read_pptx_block_view(read_pptx(content), locator['value'])
                selected = next(b for b in view['blocks'] if b['block_id'] == view['selected_block_id'])
                if selected['text'][view['selected_start']:view['selected_end']] != evidence.get('text'):
                    raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
                view['source_version_id'] = source_version_id
            except SourceRejected:
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409) from None
            except (ObjectQueueError, ObjectStorageError):
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=503) from None
            body = canonical_json_bytes(view)
            if len(body) > 32 * 1024:
                raise QuestionRepositoryError('CITATION_CONTENT_UNAVAILABLE', status=409)
            return CitationContent(source_id, source_version_id, filename, body, 'application/json'), locator
        if evidence.get("kind") == "xlsx_cell_fact" or isinstance(stored_locator, dict) and stored_locator.get("kind") == "sheet_cell":
            from .source_ingest import SourceRejected
            from .xlsx_source import read_xlsx, read_sheet_cell_view
            locator = evidence.get("locator")
            if (
                context.notebook_id is None or evidence.get("kind") != "xlsx_cell_fact"
                or evidence.get("source_id") != source_id
                or evidence.get("source_version_id") != source_version_id
                or not isinstance(locator, dict) or set(locator) != {"kind", "value"}
                or locator.get("kind") != "sheet_cell" or locator != stored_locator
            ):
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
            original = self._row(context, source_id, source_version_id, "citation.read", require_notebook_binding=True)
            if (
                str(original[3]) != filename or not filename.lower().endswith(".xlsx")
                or str(original[8]) != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                or str(original[9]) != "completed"
            ):
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
            try:
                ObjectKeyPolicy().validate_final(self._cloud_context(context, "object.read"), "source", str(original[5]))
                content = self._object_storage.get(str(original[5]))
                if len(content) != int(original[7]) or hashlib.sha256(content).hexdigest() != str(original[6]):
                    raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
                view = read_sheet_cell_view(read_xlsx(content), locator["value"])
                view["source_version_id"] = source_version_id
            except SourceRejected:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409) from None
            except (ObjectQueueError, ObjectStorageError):
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=503) from None
            return CitationContent(source_id, source_version_id, filename, canonical_json_bytes(view), "application/json"), locator
        if evidence.get("kind") == "image_region_fact":
            from .image_document_understanding import normalize_image, validate_image_region
            if context.notebook_id is None or evidence.get("source_id") != source_id or evidence.get("source_version_id") != source_version_id:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
            locator = evidence.get("locator")
            if not isinstance(locator, dict) or set(locator) != {"kind", "value"} or locator.get("kind") != "image_region" or len(row) < 10 or row[9] != locator:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            original = self._row(context, source_id, source_version_id, "citation.read", require_notebook_binding=True)
            if original[3] != filename or str(original[8]) not in {"image/png", "image/jpeg"} or str(original[9]) != "completed":
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
            try:
                ObjectKeyPolicy().validate_final(self._cloud_context(context, "object.read"), "source", str(original[5]))
                content = self._object_storage.get(str(original[5]))
                if len(content) != int(original[7]) or hashlib.sha256(content).hexdigest() != str(original[6]):
                    raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
                image = normalize_image(content, str(original[8]))
                coordinates = validate_image_region(locator["value"])
                if coordinates[4:] != (image.width, image.height):
                    raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            except DocumentUnderstandingError:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409) from None
            except (ObjectQueueError, ObjectStorageError):
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=503) from None
            return CitationContent(source_id, source_version_id, filename, content, str(original[8])), locator
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
        if evidence.get("kind") == "text_source_verbatim_fact":
            if context.notebook_id is None:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
            original = self._row(
                context, source_id, source_version_id, "citation.read",
                require_notebook_binding=True,
            )
            if str(original[3]) != filename or text_source_format(filename, str(original[8])) is None or str(original[9]) != "completed":
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=404)
            try:
                ObjectKeyPolicy().validate_final(
                    self._cloud_context(context, "object.read"), "source", str(original[5]),
                )
                content = self._object_storage.get(str(original[5]))
            except (ObjectQueueError, ObjectStorageError):
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=503) from None
            if (
                len(content) > 1024 * 1024 or len(content) != int(original[7])
                or hashlib.sha256(content).hexdigest() != str(original[6])
            ):
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            try:
                text = content.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409) from None
            if not text.strip() or not isinstance(evidence.get("text"), str) or evidence["text"] not in text:
                raise QuestionRepositoryError("CITATION_CONTENT_UNAVAILABLE", status=409)
            return CitationContent(
                source_id, source_version_id, filename, content, "text/plain; charset=utf-8",
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
                    "kind": evidence_by_id[chunk_id].locator_kind if evidence_by_id[chunk_id].locator_kind in {'image_region', 'sheet_cell', 'docx_block', 'pptx_block', 'audio_segment'} else "section" if evidence_by_id[chunk_id].locator_kind == "section" or str(source_context[(
                        evidence_by_id[chunk_id].source_id,
                        evidence_by_id[chunk_id].source_version_id,
                    )].origin) == "daon_knowledge" else "page",
                    "value": evidence_by_id[chunk_id].locator_value if evidence_by_id[chunk_id].locator_kind in {'image_region', 'sheet_cell', 'docx_block', 'pptx_block', 'audio_segment'} else evidence_by_id[chunk_id].evidence_span_id
                    if evidence_by_id[chunk_id].locator_kind == "section" or str(source_context[(
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
                frozen_scope = authorization.get("frozen_routing_context")
                replay_egress_scope = self._replay_egress_scope(selection, frozen_scope)
                self._insert_canon(connection, context, "runs", run_id,
                    self._run_canonical_payload(
                        question=question, source_id=source_id,
                        source_version_id=source_version_id, context_mode=context_mode,
                        request_fingerprint=request_fingerprint, selection=selection,
                        frozen_scope=frozen_scope,
                    ), state="accepted", extra_columns=("conversation_id",),
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
            selection.provider_kind, replay_egress_scope,
        )
