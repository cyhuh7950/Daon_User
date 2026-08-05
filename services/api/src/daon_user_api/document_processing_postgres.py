"""PostgreSQL/Object Storage repository for auditable document processing."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .document_processing import DocumentProcessingContext, StoredSourceDocument
from .document_understanding_adapter import (
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
)
from .object_queue import ObjectKeyPolicy, ObjectStorageError, ObjectStoragePort


class PostgresDocumentProcessingRepository:
    def __init__(self, cloud_store: PostgresCloudStore, object_storage: ObjectStoragePort) -> None:
        self._cloud_store = cloud_store
        self._object_storage = object_storage

    @staticmethod
    def _cloud_context(context: DocumentProcessingContext, capability: str) -> CloudAccessContext:
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
    def _transition(
        connection, context: DocumentProcessingContext, *, entity_type: str,
        aggregate_id: str,
        expected_version: int, target_state: str, reason_code: str,
    ) -> int:
        transition_id = PostgresDocumentProcessingRepository._opaque_id(
            "tr", entity_type, aggregate_id, target_state, str(expected_version),
        )
        row = connection.execute(
            "SELECT state,version,outcome,error_code FROM transition_canon_state"
            "(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                entity_type, aggregate_id, expected_version, target_state,
                transition_id, reason_code, context.trace_id, context.policy_version,
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
                "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503,
                retryable=error.retryable,
            )
        return DocumentUnderstandingError(
            "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503, retryable=True,
        )

    def load_source_document(
        self, context: DocumentProcessingContext, source_id: str,
    ) -> StoredSourceDocument:
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.read")) as connection:
                row = connection.execute(
                    "SELECT sv.record_id,sv.object_id,sv.canonical_json,o.object_key,"
                    "o.digest_sha256,o.byte_size,o.content_type,o.status "
                    "FROM source_versions sv JOIN object_records o ON "
                    "o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id "
                    "AND o.object_id=sv.object_id WHERE sv.source_id=%s "
                    "ORDER BY sv.version DESC LIMIT 1",
                    (source_id,),
                ).fetchone()
            if row is None:
                raise DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)
            payload = cast(Mapping[str, object], row[2])
            filename = str(payload.get("filename", ""))
            object_key = str(row[3])
            if (
                str(row[7]) != "completed" or str(row[6]) != "application/pdf"
                or not filename.lower().endswith(".pdf")
            ):
                raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409)
            object_context = self._cloud_context(context, "object.read")
            ObjectKeyPolicy().validate_final(object_context, "source", object_key)
            content = self._object_storage.get(object_key)
            if (
                len(content) != int(row[5])
                or hashlib.sha256(content).hexdigest() != str(row[4])
                or not content.startswith(b"%PDF-")
            ):
                raise DocumentUnderstandingError("SOURCE_OBJECT_CHECKSUM_MISMATCH", status=409)
            return StoredSourceDocument(source_id, str(row[0]), filename, content)
        except DocumentUnderstandingError:
            raise
        except ObjectStorageError as error:
            raise DocumentUnderstandingError(
                error.code, status=503 if error.retryable else 409, retryable=error.retryable,
            ) from None
        except Exception as error:
            raise self._database_error(error) from None

    def start(self, context: DocumentProcessingContext, source_version_id: str) -> str:
        processing_run_id = self._opaque_id(
            "pr", context.tenant_id, context.workspace_id, source_version_id, context.trace_id,
        )
        payload: dict[str, object] = {
            "source_version_id": source_version_id,
            "modality": "document",
            "trigger_type": "initial",
            "vision_llm_first": True,
            "parser_role": "validation_only",
        }
        text, digest = self._snapshot(payload)
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                connection.execute(
                    "INSERT INTO processing_runs "
                    "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                    "canonical_json,canonical_text,digest_sha256,state,created_by,trace_id,"
                    "source_version_id,modality,trigger_type) "
                    "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,'accepted',%s,%s,%s,'document','initial') "
                    "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                    (
                        context.tenant_id, context.workspace_id, processing_run_id,
                        processing_run_id, Jsonb(payload), text, digest, context.actor_id,
                        context.trace_id, source_version_id,
                    ),
                )
                row = connection.execute(
                    "SELECT state,version,source_version_id FROM processing_runs WHERE record_id=%s",
                    (processing_run_id,),
                ).fetchone()
                if row is None or str(row[2]) != source_version_id:
                    raise DocumentUnderstandingError("PROCESSING_RUN_CONFLICT", status=409)
                source_row = connection.execute(
                    "SELECT sv.source_id,s.state,s.version FROM source_versions sv "
                    "JOIN sources s ON s.tenant_id=sv.tenant_id AND "
                    "s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                    "WHERE sv.record_id=%s",
                    (source_version_id,),
                ).fetchone()
                if source_row is None:
                    raise DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)
                source_id, source_state, source_version = (
                    str(source_row[0]), str(source_row[1]), int(source_row[2])
                )
                if source_state == "registered":
                    source_version = self._transition(
                        connection, context, entity_type="Source", aggregate_id=source_id,
                        expected_version=source_version, target_state="security_check",
                        reason_code="PDF_SECURITY_CHECK_PASSED",
                    )
                    source_state = "security_check"
                if source_state == "security_check":
                    self._transition(
                        connection, context, entity_type="Source", aggregate_id=source_id,
                        expected_version=source_version, target_state="processing",
                        reason_code="DOCUMENT_PROCESSING_STARTED",
                    )
                elif source_state in {"waiting_model", "partial_understanding", "needs_review", "failed"}:
                    self._transition(
                        connection, context, entity_type="Source", aggregate_id=source_id,
                        expected_version=source_version, target_state="processing",
                        reason_code="DOCUMENT_PROCESSING_RESTARTED",
                    )
                elif source_state != "processing":
                    raise DocumentUnderstandingError("SOURCE_PROCESSING_STATE_INVALID", status=409)
                if str(row[0]) == "accepted" and int(row[1]) == 1:
                    self._transition(
                        connection, context, entity_type="ProcessingRun",
                        aggregate_id=processing_run_id,
                        expected_version=1, target_state="vision_llm_understanding",
                        reason_code="ORIGINAL_PDF_UNDERSTANDING_STARTED",
                    )
                elif str(row[0]) != "vision_llm_understanding":
                    raise DocumentUnderstandingError("PROCESSING_RUN_CONFLICT", status=409)
            return processing_run_id
        except Exception as error:
            raise self._database_error(error) from None

    def complete(
        self, context: DocumentProcessingContext, processing_run_id: str,
        result: DocumentUnderstandingResult,
    ) -> None:
        understanding_id = self._opaque_id("ur", processing_run_id, result.source_version_id)
        payload: dict[str, object] = {
            "source_id": result.source_id,
            "source_version_id": result.source_version_id,
            "status": result.status,
            "semantic": {
                "title": result.semantic.title, "summary": result.semantic.summary,
                "key_facts": list(result.semantic.key_facts),
            },
            "parser_validation": {
                "text": result.parser.text, "markdown": result.parser.markdown,
                "html": result.parser.html, "pages": list(result.parser.pages),
                "page_texts": [
                    {"page": page, "text": page_text}
                    for page, page_text in result.parser.page_texts
                ],
                "role": result.parser.role,
            },
            "lineage": dict(result.lineage),
            "conflict": result.conflict,
        }
        text, digest = self._snapshot(payload)
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                version = self._transition(
                    connection, context, entity_type="ProcessingRun",
                    aggregate_id=processing_run_id,
                    expected_version=2, target_state="parser_ocr_validation",
                    reason_code="SEMANTIC_UNDERSTANDING_COMPLETED",
                )
                version = self._transition(
                    connection, context, entity_type="ProcessingRun",
                    aggregate_id=processing_run_id,
                    expected_version=version, target_state="evidence_reconciliation",
                    reason_code="PARSER_VALIDATION_COMPLETED",
                )
                connection.execute(
                    "INSERT INTO understanding_results "
                    "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                    "canonical_json,canonical_text,digest_sha256,created_by,trace_id,"
                    "processing_run_id,source_version_id) "
                    "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                    (
                        context.tenant_id, context.workspace_id, understanding_id,
                        understanding_id, Jsonb(payload), text, digest, context.actor_id,
                        context.trace_id, processing_run_id, result.source_version_id,
                    ),
                )
                for page in result.parser.pages:
                    evidence_id = self._opaque_id("ev", understanding_id, str(page))
                    evidence = {
                        "page": page, "source_version_id": result.source_version_id,
                        "understanding_result_id": understanding_id,
                        "parser_role": "validation_only",
                    }
                    evidence_text, evidence_digest = self._snapshot(evidence)
                    connection.execute(
                        "INSERT INTO extraction_evidence "
                        "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                        "canonical_json,canonical_text,digest_sha256,created_by,trace_id,"
                        "understanding_result_id,source_version_id) "
                        "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                        (
                            context.tenant_id, context.workspace_id, evidence_id, evidence_id,
                            Jsonb(evidence), evidence_text, evidence_digest, context.actor_id,
                            context.trace_id, understanding_id, result.source_version_id,
                        ),
                    )
                self._transition(
                    connection, context, entity_type="ProcessingRun",
                    aggregate_id=processing_run_id,
                    expected_version=version, target_state="completed",
                    reason_code="EVIDENCE_RECONCILIATION_COMPLETED",
                )
                if result.status == "needs_review":
                    source_row = connection.execute(
                        "SELECT sv.source_id,s.state,s.version FROM source_versions sv "
                        "JOIN sources s ON s.tenant_id=sv.tenant_id AND "
                        "s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                        "WHERE sv.record_id=%s",
                        (result.source_version_id,),
                    ).fetchone()
                    if source_row is None or str(source_row[1]) != "processing":
                        raise DocumentUnderstandingError("SOURCE_PROCESSING_STATE_INVALID", status=409)
                    self._transition(
                        connection, context, entity_type="Source",
                        aggregate_id=str(source_row[0]), expected_version=int(source_row[2]),
                        target_state="needs_review",
                        reason_code="UNDERSTANDING_PARSER_CONFLICT",
                    )
        except Exception as error:
            raise self._database_error(error) from None

    def fail(
        self, context: DocumentProcessingContext, processing_run_id: str,
        code: str, *, retryable: bool,
    ) -> None:
        reason = code if code.isascii() and code.replace("_", "").isalnum() else (
            "RETRYABLE_PROVIDER_FAILURE" if retryable else "DOCUMENT_PROCESSING_FAILED"
        )
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                row = connection.execute(
                    "SELECT pr.state,pr.version,sv.source_id,s.state,s.version "
                    "FROM processing_runs pr JOIN source_versions sv ON "
                    "sv.tenant_id=pr.tenant_id AND sv.workspace_id=pr.workspace_id "
                    "AND sv.record_id=pr.source_version_id JOIN sources s ON "
                    "s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id "
                    "AND s.record_id=sv.source_id WHERE pr.record_id=%s",
                    (processing_run_id,),
                ).fetchone()
                if row is None:
                    raise DocumentUnderstandingError("PROCESSING_RUN_NOT_FOUND", status=404)
                if str(row[0]) != "failed":
                    self._transition(
                        connection, context, entity_type="ProcessingRun",
                        aggregate_id=processing_run_id,
                        expected_version=int(row[1]), target_state="failed",
                        reason_code=reason,
                    )
                source_target = "waiting_model" if retryable else "failed"
                if str(row[3]) == "processing":
                    self._transition(
                        connection, context, entity_type="Source",
                        aggregate_id=str(row[2]), expected_version=int(row[4]),
                        target_state=source_target, reason_code=reason,
                    )
        except Exception as error:
            raise self._database_error(error) from None
