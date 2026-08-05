"""PostgreSQL canonical index for grounded single-PDF semantic facts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .document_processing import DocumentProcessingContext
from .document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
)


@dataclass(frozen=True, slots=True)
class IndexedEvidenceChunk:
    chunk_id: str
    source_id: str
    source_version_id: str
    page: int
    text: str
    evidence_span_id: str
    score: float = 0.0


class PostgresDocumentIndex:
    def __init__(self, cloud_store: PostgresCloudStore) -> None:
        self._cloud_store = cloud_store

    @staticmethod
    def _cloud_context(context: DocumentProcessingContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "source.index",
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
    def _normalized(text: str) -> str:
        return " ".join(text.casefold().split())

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[가-힣A-Za-z0-9]+", text.casefold()))

    @staticmethod
    def _transition_source(
        connection, context: DocumentProcessingContext, source_id: str,
        expected_version: int, target_state: str, reason_code: str,
    ) -> int:
        transition_id = PostgresDocumentIndex._opaque_id(
            "tr", "Source", source_id, target_state, str(expected_version),
        )
        row = connection.execute(
            "SELECT state,version,outcome,error_code FROM transition_canon_state"
            "(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "Source", source_id, expected_version, target_state, transition_id,
                reason_code, context.trace_id, context.policy_version,
            ),
        ).fetchone()
        if row is None or str(row[2]) != "succeeded" or row[1] is None:
            code = "CANON_TRANSITION_INVALID" if row is None or row[3] is None else str(row[3])
            raise DocumentUnderstandingError(code, status=409)
        return int(row[1])

    @staticmethod
    def _database_error(error: Exception) -> DocumentUnderstandingError:
        if isinstance(error, DocumentUnderstandingError):
            return error
        if isinstance(error, CloudDatabaseError):
            return DocumentUnderstandingError(
                "DOCUMENT_INDEX_DATABASE_UNAVAILABLE", status=503,
                retryable=error.retryable,
            )
        return DocumentUnderstandingError(
            "DOCUMENT_INDEX_DATABASE_UNAVAILABLE", status=503, retryable=True,
        )

    def index_result(
        self, context: DocumentProcessingContext, result: DocumentUnderstandingResult,
    ) -> str:
        if result.status != "ready" or result.conflict is not None:
            raise DocumentUnderstandingError("DOCUMENT_INDEX_REQUIRES_READY_UNDERSTANDING", status=409)
        page_texts = tuple(
            (page, text) for page, text in result.parser.page_texts
            if page > 0 and text.strip()
        )
        if not page_texts:
            raise DocumentUnderstandingError("PAGE_EVIDENCE_UNAVAILABLE", status=409)

        chunks: list[dict[str, object]] = []
        for ordinal, fact in enumerate(result.semantic.key_facts, start=1):
            normalized_fact = self._normalized(fact)
            page = next((
                page_number for page_number, page_text in page_texts
                if normalized_fact in self._normalized(page_text)
            ), None)
            if page is None:
                raise DocumentUnderstandingError("PAGE_EVIDENCE_UNAVAILABLE", status=409)
            chunk_id = self._opaque_id("chunk", result.source_version_id, str(ordinal), fact)
            evidence_span_id = self._opaque_id("span", chunk_id, str(page))
            chunks.append({
                "chunk_id": chunk_id,
                "source_id": result.source_id,
                "source_version_id": result.source_version_id,
                "page": page,
                "text": fact,
                "evidence_span_id": evidence_span_id,
            })
        if not chunks:
            raise DocumentUnderstandingError("DOCUMENT_SEMANTIC_CHUNKS_EMPTY", status=409)

        try:
            with self._cloud_store._transaction(self._cloud_context(context)) as connection:
                understanding_row = connection.execute(
                    "SELECT record_id,canonical_json FROM understanding_results "
                    "WHERE source_version_id=%s ORDER BY created_at DESC,record_id DESC LIMIT 1",
                    (result.source_version_id,),
                ).fetchone()
                if understanding_row is None:
                    raise DocumentUnderstandingError("UNDERSTANDING_RESULT_NOT_FOUND", status=409)
                understanding_payload = cast(Mapping[str, object], understanding_row[1])
                semantic_payload = understanding_payload.get("semantic")
                if (
                    understanding_payload.get("source_id") != result.source_id
                    or understanding_payload.get("status") != "ready"
                    or not isinstance(semantic_payload, dict)
                    or semantic_payload.get("key_facts") != list(result.semantic.key_facts)
                ):
                    raise DocumentUnderstandingError("UNDERSTANDING_RESULT_MISMATCH", status=409)
                understanding_result_id = str(understanding_row[0])
                index_version_id = self._opaque_id(
                    "iv", result.source_version_id, understanding_result_id,
                )
                index_payload: dict[str, object] = {
                    "source_id": result.source_id,
                    "source_version_id": result.source_version_id,
                    "understanding_result_id": understanding_result_id,
                    "strategy": "vision_llm_facts_with_parser_page_validation",
                    "chunks": chunks,
                    "lineage": dict(result.lineage),
                }
                index_text, index_digest = self._snapshot(index_payload)
                source_row = connection.execute(
                    "SELECT s.state,s.version,sv.source_id FROM source_versions sv "
                    "JOIN sources s ON s.tenant_id=sv.tenant_id AND "
                    "s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                    "WHERE sv.record_id=%s",
                    (result.source_version_id,),
                ).fetchone()
                if source_row is None or str(source_row[2]) != result.source_id:
                    raise DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)
                if str(source_row[0]) != "processing":
                    raise DocumentUnderstandingError("SOURCE_INDEX_STATE_INVALID", status=409)
                source_version = self._transition_source(
                    connection, context, result.source_id, int(source_row[1]),
                    "indexing", "DOCUMENT_INDEX_STARTED",
                )
                for chunk in chunks:
                    evidence_payload = {
                        "source_id": result.source_id,
                        "source_version_id": result.source_version_id,
                        "understanding_result_id": understanding_result_id,
                        "page": chunk["page"],
                        "text": chunk["text"],
                        "kind": "parser_page_validated_semantic_fact",
                    }
                    evidence_text, evidence_digest = self._snapshot(evidence_payload)
                    connection.execute(
                        "INSERT INTO evidence_spans "
                        "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                        "canonical_json,canonical_text,digest_sha256,created_by,trace_id,"
                        "source_version_id) VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                        (
                            context.tenant_id, context.workspace_id,
                            chunk["evidence_span_id"], chunk["evidence_span_id"],
                            Jsonb(evidence_payload), evidence_text, evidence_digest,
                            context.actor_id, context.trace_id, result.source_version_id,
                        ),
                    )
                connection.execute(
                    "INSERT INTO index_versions "
                    "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                    "canonical_json,canonical_text,digest_sha256,created_by,trace_id,"
                    "source_version_id) VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                    (
                        context.tenant_id, context.workspace_id, index_version_id,
                        index_version_id, Jsonb(index_payload), index_text, index_digest,
                        context.actor_id, context.trace_id, result.source_version_id,
                    ),
                )
                self._transition_source(
                    connection, context, result.source_id, source_version,
                    "ready", "DOCUMENT_INDEX_COMPLETED",
                )
            return index_version_id
        except Exception as error:
            raise self._database_error(error) from None

    def search(
        self, context: DocumentProcessingContext, *, source_id: str,
        source_version_id: str, query: str, limit: int = 10,
    ) -> tuple[IndexedEvidenceChunk, ...]:
        query_tokens = self._tokens(query)
        if not query_tokens or limit < 1:
            return ()
        try:
            with self._cloud_store._transaction(self._cloud_context(context)) as connection:
                row = connection.execute(
                    "SELECT canonical_json FROM index_versions WHERE source_version_id=%s "
                    "ORDER BY created_at DESC,record_id DESC LIMIT 1",
                    (source_version_id,),
                ).fetchone()
            if row is None:
                return ()
            payload = cast(Mapping[str, object], row[0])
            if str(payload.get("source_id", "")) != source_id:
                return ()
            raw_chunks = payload.get("chunks")
            if not isinstance(raw_chunks, list):
                raise DocumentUnderstandingError("DOCUMENT_INDEX_SNAPSHOT_INVALID", status=500)
            matches: list[IndexedEvidenceChunk] = []
            for raw in raw_chunks:
                if not isinstance(raw, dict) or str(raw.get("source_version_id", "")) != source_version_id:
                    continue
                overlap = query_tokens.intersection(self._tokens(str(raw.get("text", ""))))
                if not overlap:
                    continue
                matches.append(IndexedEvidenceChunk(
                    str(raw["chunk_id"]), source_id, source_version_id,
                    int(raw["page"]), str(raw["text"]), str(raw["evidence_span_id"]),
                    len(overlap) / len(query_tokens),
                ))
            return tuple(sorted(
                matches, key=lambda item: (-item.score, item.page, item.chunk_id),
            )[:limit])
        except Exception as error:
            raise self._database_error(error) from None
