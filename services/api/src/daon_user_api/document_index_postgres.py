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
    locator_kind: str = "page"
    locator_value: str | None = None


def _source_access_allowed(connection, context, source_version_id: str) -> bool:
    """현재 정확한 범위/사용자의 명시 deny만 거부한다. ACL 행 없음은 기존대로 허용한다."""
    row = connection.execute(
        'SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access '
        'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s '
        'AND source_version_id=%s AND allowed=0)',
        (context.tenant_id, context.workspace_id, context.actor_id, source_version_id),
    ).fetchone()
    return row is not None and bool(row[0])


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
        cell_source = result.lineage.get('source_format') == 'xlsx'
        block_source = result.lineage.get('source_format') == 'docx'
        slide_source = result.lineage.get('source_format') == 'pptx'
        audio_source = result.lineage.get('source_format') == 'wav'
        if not page_texts and not cell_source and not block_source and not slide_source and not audio_source:
            raise DocumentUnderstandingError("PAGE_EVIDENCE_UNAVAILABLE", status=409)

        chunks: list[dict[str, object]] = []
        all_facts_located = True
        for ordinal, fact in (() if cell_source or block_source or slide_source or audio_source else enumerate(result.semantic.key_facts, start=1)):
            normalized_fact = self._normalized(fact)
            page = next((
                page_number for page_number, page_text in page_texts
                if normalized_fact in self._normalized(page_text)
            ), None)
            if page is None:
                all_facts_located = False
                break
            chunk_id = self._opaque_id("chunk", result.source_version_id, str(ordinal), fact)
            evidence_span_id = self._opaque_id("span", chunk_id, str(page))
            chunks.append({
                "chunk_id": chunk_id,
                "source_id": result.source_id,
                "source_version_id": result.source_version_id,
                "page": page,
                "text": fact,
                "evidence_span_id": evidence_span_id,
                "evidence_kind": "parser_page_validated_semantic_fact",
            })
        text_source = result.lineage.get("source_format") in {"txt", "md", "csv"}
        image_source = result.lineage.get("source_format") == "image"
        if audio_source:
            from .audio_document_understanding import read_canonical_audio_transcript, validate_audio_evidence
            transcript = read_canonical_audio_transcript(result.lineage)
            validate_audio_evidence(result.parser.audio_evidence, result.semantic.key_facts, transcript)
            if result.parser.pages or result.parser.page_texts or result.parser.regions or result.parser.cell_evidence or result.parser.block_evidence or result.parser.slide_evidence or result.parser.role != 'validation_only':
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
            for ordinal, segment in enumerate(result.parser.audio_evidence, 1):
                chunk_id = self._opaque_id('chunk', result.source_version_id, str(ordinal), segment['text'])
                chunks.append({'chunk_id': chunk_id, 'source_id': result.source_id,
                    'source_version_id': result.source_version_id, 'page': 1, 'text': segment['text'],
                    'evidence_span_id': self._opaque_id('span', chunk_id, segment['locator_value']),
                    'evidence_kind': 'audio_segment_fact', 'locator_value': segment['locator_value']})
        if block_source:
            from .docx_document_understanding import validate_block_evidence
            validate_block_evidence(result.parser.block_evidence, result.semantic.key_facts)
            if result.parser.pages or result.parser.page_texts or result.parser.regions or result.parser.cell_evidence or result.parser.role != 'validation_only':
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
            for ordinal, block in enumerate(result.parser.block_evidence, 1):
                chunk_id = self._opaque_id('chunk', result.source_version_id, str(ordinal), block['text'])
                chunks.append({'chunk_id': chunk_id, 'source_id': result.source_id,
                    'source_version_id': result.source_version_id, 'page': 1, 'text': block['text'],
                    'evidence_span_id': self._opaque_id('span', chunk_id, block['locator_value']),
                    'evidence_kind': 'docx_block_fact', 'locator_value': block['locator_value']})
        if slide_source:
            from .pptx_document_understanding import validate_slide_evidence
            validate_slide_evidence(result.parser.slide_evidence, result.semantic.key_facts)
            if result.parser.pages or result.parser.page_texts or result.parser.regions or result.parser.cell_evidence or result.parser.block_evidence or result.parser.role != 'validation_only':
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
            for ordinal, block in enumerate(result.parser.slide_evidence, 1):
                chunk_id = self._opaque_id('chunk', result.source_version_id, str(ordinal), block['text'])
                chunks.append({'chunk_id': chunk_id, 'source_id': result.source_id,
                    'source_version_id': result.source_version_id, 'page': 1, 'text': block['text'],
                    'evidence_span_id': self._opaque_id('span', chunk_id, block['locator_value']),
                    'evidence_kind': 'pptx_block_fact', 'locator_value': block['locator_value']})
        if cell_source:
            from .xlsx_document_understanding import validate_cell_evidence
            validate_cell_evidence(result.parser.cell_evidence, result.semantic.key_facts)
            if result.parser.pages or result.parser.page_texts or result.parser.regions:
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
            for ordinal, cell in enumerate(result.parser.cell_evidence, 1):
                chunk_id = self._opaque_id('chunk', result.source_version_id, str(ordinal), cell['text'])
                chunks.append({'chunk_id': chunk_id, 'source_id': result.source_id,
                    'source_version_id': result.source_version_id, 'page': 1, 'text': cell['text'],
                    'evidence_span_id': self._opaque_id('span', chunk_id, cell['locator_value']),
                    'evidence_kind': 'xlsx_cell_fact', 'locator_value': cell['locator_value']})
        if image_source:
            from .image_document_understanding import validate_image_region
            if len(result.parser.regions) != len(result.semantic.key_facts):
                raise DocumentUnderstandingError("IMAGE_REGION_INVALID", status=409)
            chunks = []
            for ordinal, fact in enumerate(result.semantic.key_facts, start=1):
                matches = [r for r in result.parser.regions if r.get("text") == fact]
                if len(matches) != 1:
                    raise DocumentUnderstandingError("IMAGE_REGION_INVALID", status=409)
                value = matches[0].get("value")
                validate_image_region(value)
                chunk_id = self._opaque_id("chunk", result.source_version_id, str(ordinal), fact)
                chunks.append({"chunk_id": chunk_id, "source_id": result.source_id,
                    "source_version_id": result.source_version_id, "page": 1, "text": fact,
                    "evidence_span_id": self._opaque_id("span", chunk_id, value),
                    "evidence_kind": "image_region_fact", "locator_value": value})
            all_facts_located = True
        if text_source:
            for chunk in chunks:
                chunk["evidence_kind"] = "text_source_verbatim_fact"
        strategy = "text_llm_facts_with_original_validation" if text_source else "vision_llm_facts_with_parser_page_validation"
        if image_source:
            strategy = "native_image_facts_with_ocr_region_validation"
        if cell_source:
            strategy = 'native_xlsx_facts_with_cell_validation'
        if block_source:
            strategy = 'native_docx_facts_with_block_validation'
        if slide_source:
            strategy = 'native_pptx_facts_with_block_validation'
        if audio_source:
            strategy = 'native_audio_facts_with_transcript_validation'
        if not all_facts_located:
            if text_source:
                raise DocumentUnderstandingError("TEXT_UNDERSTANDING_UNGROUNDED", status=409)
            strategy = "vision_llm_facts_with_parser_page_fallback"
            chunks = []
            for page, page_text in page_texts:
                chunk_id = self._opaque_id(
                    "chunk", result.source_version_id, "parser-page", str(page), page_text,
                )
                evidence_span_id = self._opaque_id("span", chunk_id, str(page))
                chunks.append({
                    "chunk_id": chunk_id,
                    "source_id": result.source_id,
                    "source_version_id": result.source_version_id,
                    "page": page,
                    "text": page_text,
                    "evidence_span_id": evidence_span_id,
                    "evidence_kind": "parser_page_grounded_fallback",
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
                canonical_lineage = understanding_payload.get("lineage")
                if audio_source or isinstance(canonical_lineage, dict) and canonical_lineage.get('source_format') == 'wav':
                    parser_payload = understanding_payload.get('parser_validation')
                    if (not audio_source or understanding_payload.get('source_version_id') != result.source_version_id
                        or not isinstance(parser_payload, dict) or parser_payload.get('audio_evidence') != list(result.parser.audio_evidence)
                        or any(parser_payload.get(key) != [] for key in ('pages', 'page_texts', 'regions'))
                        or any(parser_payload.get(key, []) != [] for key in ('cell_evidence', 'block_evidence', 'slide_evidence'))
                        or parser_payload.get('role') != 'validation_only' or canonical_lineage != dict(result.lineage)
                        or result.lineage.get('reconciliation') != 'matched'):
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                    extraction = connection.execute(
                        'SELECT canonical_json FROM extraction_evidence WHERE source_version_id=%s AND understanding_result_id=%s ORDER BY created_at DESC,record_id DESC LIMIT 1',
                        (result.source_version_id, str(understanding_row[0])),
                    ).fetchone()
                    expected = {'source_version_id': result.source_version_id,
                        'understanding_result_id': str(understanding_row[0]), 'parser_role': 'validation_only',
                        'transcript_sha256': result.lineage['transcript_sha256'], 'audio_evidence': list(result.parser.audio_evidence)}
                    if extraction is None or extraction[0] != expected:
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                if block_source or isinstance(canonical_lineage, dict) and canonical_lineage.get('source_format') == 'docx':
                    parser_payload = understanding_payload.get('parser_validation')
                    if (not block_source or understanding_payload.get('source_version_id') != result.source_version_id
                        or not isinstance(parser_payload, dict) or parser_payload.get('block_evidence') != list(result.parser.block_evidence)
                        or any(parser_payload.get(key) != [] for key in ('pages', 'page_texts', 'regions'))
                        or parser_payload.get('cell_evidence', []) != [] or parser_payload.get('role') != 'validation_only'
                        or canonical_lineage != dict(result.lineage)
                        or any(not re.fullmatch(r'[0-9a-f]{64}', str(result.lineage.get(key, ''))) for key in ('source_digest', 'native_input_digest'))
                        or result.lineage.get('reconciliation') != 'matched'):
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                    extraction = connection.execute(
                        'SELECT canonical_json FROM extraction_evidence WHERE source_version_id=%s AND understanding_result_id=%s ORDER BY created_at DESC,record_id DESC LIMIT 1',
                        (result.source_version_id, str(understanding_row[0])),
                    ).fetchone()
                    expected = {'source_version_id': result.source_version_id,
                        'understanding_result_id': str(understanding_row[0]), 'parser_role': 'validation_only',
                        'block_evidence': list(result.parser.block_evidence)}
                    if extraction is None or extraction[0] != expected:
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                if slide_source or isinstance(canonical_lineage, dict) and canonical_lineage.get('source_format') == 'pptx':
                    parser_payload = understanding_payload.get('parser_validation')
                    if (not slide_source or understanding_payload.get('source_version_id') != result.source_version_id
                        or not isinstance(parser_payload, dict) or parser_payload.get('slide_evidence') != list(result.parser.slide_evidence)
                        or any(parser_payload.get(key) != [] for key in ('pages', 'page_texts', 'regions'))
                        or parser_payload.get('cell_evidence', []) != [] or parser_payload.get('block_evidence', []) != [] or parser_payload.get('role') != 'validation_only'
                        or canonical_lineage != dict(result.lineage)
                        or any(not re.fullmatch(r'[0-9a-f]{64}', str(result.lineage.get(key, ''))) for key in ('source_digest', 'native_input_digest'))
                        or result.lineage.get('reconciliation') != 'matched'):
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                    extraction = connection.execute(
                        'SELECT canonical_json FROM extraction_evidence WHERE source_version_id=%s AND understanding_result_id=%s ORDER BY created_at DESC,record_id DESC LIMIT 1',
                        (result.source_version_id, str(understanding_row[0])),
                    ).fetchone()
                    expected = {'source_version_id': result.source_version_id,
                        'understanding_result_id': str(understanding_row[0]), 'parser_role': 'validation_only',
                        'slide_evidence': list(result.parser.slide_evidence)}
                    if extraction is None or extraction[0] != expected:
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                if cell_source or isinstance(canonical_lineage, dict) and canonical_lineage.get('source_format') == 'xlsx':
                    parser_payload = understanding_payload.get('parser_validation')
                    if not cell_source or understanding_payload.get('source_version_id') != result.source_version_id or not isinstance(parser_payload, dict) or parser_payload.get('cell_evidence') != list(result.parser.cell_evidence) or canonical_lineage != dict(result.lineage) or any(not re.fullmatch(r'[0-9a-f]{64}', str(result.lineage.get(key, ''))) for key in ('source_digest', 'native_input_digest')) or result.lineage.get('reconciliation') != 'matched':
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                    extraction = connection.execute(
                        'SELECT canonical_json FROM extraction_evidence WHERE source_version_id=%s AND understanding_result_id=%s ORDER BY created_at DESC,record_id DESC LIMIT 1',
                        (result.source_version_id, str(understanding_row[0])),
                    ).fetchone()
                    expected = {'source_version_id': result.source_version_id,
                        'understanding_result_id': str(understanding_row[0]), 'parser_role': 'validation_only',
                        'cell_evidence': list(result.parser.cell_evidence)}
                    if extraction is None or extraction[0] != expected:
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                if image_source or isinstance(canonical_lineage, dict) and canonical_lineage.get("source_format") == "image":
                    parser_payload = understanding_payload.get("parser_validation")
                    if (
                        not image_source
                        or understanding_payload.get("source_version_id") != result.source_version_id
                        or not isinstance(parser_payload, dict)
                        or parser_payload.get("regions") != list(result.parser.regions)
                        or not isinstance(canonical_lineage, dict)
                        or canonical_lineage != dict(result.lineage)
                        or not re.fullmatch(r"[0-9a-f]{64}", str(result.lineage.get("source_digest", "")))
                        or result.lineage.get("exif_orientation") not in tuple(map(str, range(1, 9)))
                    ):
                        raise DocumentUnderstandingError("UNDERSTANDING_RESULT_MISMATCH", status=409)
                    for region in result.parser.regions:
                        coordinates = validate_image_region(region.get("value"))
                        if (str(coordinates[4]), str(coordinates[5])) != (
                            result.lineage.get("image_width"), result.lineage.get("image_height"),
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
                    "strategy": strategy,
                    "chunks": chunks,
                    "lineage": dict(result.lineage),
                }
                index_text, index_digest = self._snapshot(index_payload)
                source_row = connection.execute(
                    "SELECT s.state,s.version,sv.source_id FROM source_versions sv "
                    "JOIN sources s ON s.tenant_id=sv.tenant_id AND "
                    "s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                    "WHERE sv.record_id=%s" + (" AND sv.version=(SELECT MAX(newer.version) FROM source_versions newer WHERE newer.tenant_id=sv.tenant_id AND newer.workspace_id=sv.workspace_id AND newer.source_id=sv.source_id)" if cell_source or block_source or slide_source or audio_source else ""),
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
                        "kind": chunk["evidence_kind"],
                    }
                    if image_source:
                        evidence_payload["locator"] = {"kind": "image_region", "value": chunk["locator_value"]}
                    if cell_source:
                        evidence_payload['locator'] = {'kind': 'sheet_cell', 'value': chunk['locator_value']}
                    if block_source:
                        evidence_payload['locator'] = {'kind': 'docx_block', 'value': chunk['locator_value']}
                    if slide_source:
                        evidence_payload['locator'] = {'kind': 'pptx_block', 'value': chunk['locator_value']}
                    if audio_source:
                        evidence_payload['locator'] = {'kind': 'audio_segment', 'value': chunk['locator_value']}
                        evidence_payload['transcript_sha256'] = result.lineage['transcript_sha256']
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
                if not _source_access_allowed(connection, context, source_version_id):
                    raise DocumentUnderstandingError('SOURCE_NOT_FOUND', status=404)
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
            cell_context = payload.get("strategy") == "native_xlsx_facts_with_cell_validation"
            block_context = payload.get('strategy') == 'native_docx_facts_with_block_validation'
            slide_context = payload.get('strategy') == 'native_pptx_facts_with_block_validation'
            audio_context = payload.get('strategy') == 'native_audio_facts_with_transcript_validation'
            if audio_context:
                from .audio_document_understanding import read_canonical_audio_transcript
                if payload.get('source_version_id') != source_version_id or not 1 <= len(raw_chunks) <= 5:
                    raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                audio_transcript = read_canonical_audio_transcript(payload.get('lineage', {}))
            if slide_context and (payload.get('source_version_id') != source_version_id or not 1 <= len(raw_chunks) <= 5):
                raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
            if block_context and payload.get('source_version_id') != source_version_id:
                raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
            matches: list[IndexedEvidenceChunk] = []
            for raw in raw_chunks:
                if (block_context or slide_context or audio_context) and (not isinstance(raw, dict) or raw.get('source_version_id') != source_version_id):
                    raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                if not isinstance(raw, dict) or str(raw.get("source_version_id", "")) != source_version_id:
                    continue
                overlap = query_tokens.intersection(self._tokens(str(raw.get("text", ""))))
                sheet_cell = raw.get('evidence_kind') == 'xlsx_cell_fact'
                docx_block = raw.get('evidence_kind') == 'docx_block_fact'
                pptx_block = raw.get('evidence_kind') == 'pptx_block_fact'
                audio_segment = raw.get('evidence_kind') == 'audio_segment_fact'
                if audio_context and not audio_segment:
                    raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                if slide_context and not pptx_block:
                    raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                if block_context and not docx_block:
                    raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                if cell_context and not sheet_cell:
                    raise DocumentUnderstandingError("DOCUMENT_INDEX_SNAPSHOT_INVALID", status=409)
                if not overlap and not (cell_context and sheet_cell or block_context and docx_block or slide_context and pptx_block or audio_context and audio_segment):
                    continue
                image_region = raw.get("evidence_kind") == "image_region_fact"
                if audio_segment:
                    from .audio_document_understanding import validate_audio_evidence
                    if not audio_context or raw.get('source_id') != source_id or raw.get('page') != 1 or not isinstance(raw.get('text'), str):
                        raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                    from .audio_source import decode_audio_segment
                    from .source_ingest import SourceRejected
                    try:
                        ident, *_ = decode_audio_segment(raw.get('locator_value'))
                        validate_audio_evidence(({'segment_id': ident, 'text': raw['text'], 'locator_value': raw['locator_value']},), (raw['text'],), audio_transcript)
                    except (SourceRejected, DocumentUnderstandingError):
                        raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409) from None
                if docx_block:
                    from .docx_source import decode_docx_block
                    from .source_ingest import SourceRejected
                    try:
                        _block, start, end = decode_docx_block(raw.get('locator_value'))
                        if not block_context or raw.get('source_id') != source_id or not isinstance(raw.get('text'), str) or end - start != len(raw['text']) or raw.get('page') != 1:
                            raise SourceRejected('CORRUPTED_SOURCE')
                    except SourceRejected:
                        raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409) from None
                if pptx_block:
                    from .pptx_source import decode_pptx_block
                    from .source_ingest import SourceRejected
                    try:
                        _block, start, end = decode_pptx_block(raw.get('locator_value'))
                        if not slide_context or raw.get('source_id') != source_id or not isinstance(raw.get('text'), str) or end - start != len(raw['text']) or raw.get('page') != 1:
                            raise SourceRejected('CORRUPTED_SOURCE')
                    except SourceRejected:
                        raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409) from None
                if sheet_cell:
                    from .xlsx_source import decode_sheet_cell
                    from .source_ingest import SourceRejected
                    try:
                        decode_sheet_cell(raw.get('locator_value'))
                    except SourceRejected:
                        raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409) from None
                    if raw.get('source_id') != source_id:
                        raise DocumentUnderstandingError('DOCUMENT_INDEX_SNAPSHOT_INVALID', status=409)
                if image_region:
                    from .image_document_understanding import validate_image_region
                    validate_image_region(raw.get("locator_value"))
                    if raw.get("source_id") != source_id:
                        raise DocumentUnderstandingError("DOCUMENT_INDEX_SNAPSHOT_INVALID", status=409)
                matches.append(IndexedEvidenceChunk(
                    str(raw["chunk_id"]), source_id, source_version_id,
                    int(raw["page"]), str(raw["text"]), str(raw["evidence_span_id"]),
                    len(overlap) / len(query_tokens),
                    'audio_segment' if audio_segment else 'pptx_block' if pptx_block else 'docx_block' if docx_block else 'sheet_cell' if sheet_cell else "image_region" if image_region else "section" if raw.get("evidence_kind") == "text_source_verbatim_fact" else "page",
                    str(raw["locator_value"]) if image_region or sheet_cell or docx_block or pptx_block or audio_segment else None,
                ))
            return tuple(sorted(
                matches, key=lambda item: (-item.score, item.page, item.chunk_id),
            )[:min(limit, 5) if cell_context or block_context or slide_context or audio_context else limit])
        except Exception as error:
            raise self._database_error(error) from None
