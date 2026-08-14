from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import cast

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .local_storage import (
    LocalEncryptedStore,
    LocalStorageError,
    RawSourceCanonicalInput,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPES = frozenset({"application/pdf", "text/plain", "text/markdown"})
_MAX_SOURCE_BYTES = 25 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_MAX_SPAN_BYTES = 256 * 1024
_MAX_PAGES = 1_000


class RawSourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RawSourceView:
    source_id: str
    source_version_id: str
    index_version_id: str
    filename: str
    content_type: str
    object_id: str
    digest_sha256: str
    evidence_span_ids: tuple[str, ...]
    quality_state: str = "unverified"


def _fail(code: str) -> RawSourceError:
    return RawSourceError(code)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_common(
    workspace_id: str, filename: str, content_type: str, content: bytes,
    digest: str, idempotency_key: str,
) -> None:
    if not _SAFE_ID.fullmatch(workspace_id) or not _SAFE_ID.fullmatch(idempotency_key):
        raise _fail("RAW_SOURCE_INPUT_INVALID")
    if (
        not isinstance(filename, str) or not filename or len(filename) > 255
        or filename != filename.strip() or any(char in filename for char in ("/", "\\", "\x00"))
    ):
        raise _fail("RAW_SOURCE_INPUT_INVALID")
    if content_type not in _CONTENT_TYPES:
        raise _fail("RAW_SOURCE_CONTENT_TYPE_UNSUPPORTED")
    if not isinstance(content, bytes) or not content or len(content) > _MAX_SOURCE_BYTES:
        raise _fail("RAW_SOURCE_SIZE_INVALID")
    if not _DIGEST.fullmatch(digest) or hashlib.sha256(content).hexdigest() != digest:
        raise _fail("RAW_SOURCE_DIGEST_MISMATCH")


def _extract_text(content_type: str, content: bytes) -> tuple[tuple[int, str], ...]:
    if content_type in {"text/plain", "text/markdown"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _fail("RAW_SOURCE_TEXT_INVALID") from error
        pieces = tuple(
            (index + 1, part.strip())
            for index, part in enumerate(re.split(r"\n\s*\n", text)) if part.strip()
        )
    else:
        try:
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted or len(reader.pages) > _MAX_PAGES:
                raise _fail("RAW_SOURCE_PDF_INVALID")
            extracted: list[tuple[int, str]] = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    extracted.append((page_number, text))
            pieces = tuple(extracted)
        except RawSourceError:
            raise
        except (PdfReadError, ValueError, TypeError, OSError) as error:
            raise _fail("RAW_SOURCE_PDF_INVALID") from error
    if not pieces:
        raise _fail("RAW_SOURCE_EVIDENCE_EMPTY")
    sizes = [len(text.encode("utf-8")) for _, text in pieces]
    if any(size > _MAX_SPAN_BYTES for size in sizes) or sum(sizes) > _MAX_EVIDENCE_BYTES:
        raise _fail("RAW_SOURCE_EVIDENCE_TOO_LARGE")
    return pieces


class RawSourceService:
    def __init__(self, store: LocalEncryptedStore) -> None:
        self._store = store

    def import_source(
        self, *, workspace_id: str, filename: str, content_type: str, content: bytes,
        content_digest_sha256: str, idempotency_key: str,
    ) -> RawSourceView:
        _validate_common(
            workspace_id, filename, content_type, content,
            content_digest_sha256, idempotency_key,
        )
        existing = self._store.list_canonical_envelopes(
            workspace_id, "source", "SourceVersion"
        )
        replay = [row for row in existing if row.payload.get("idempotency_key") == idempotency_key]
        if replay:
            view = self._view(replay[-1].payload)
            if (
                view.filename != filename or view.content_type != content_type
                or view.digest_sha256 != content_digest_sha256
            ):
                raise _fail("RAW_SOURCE_IDEMPOTENCY_CONFLICT")
            return view

        evidence = _extract_text(content_type, content)
        identity = hashlib.sha256(
            f"{workspace_id}|{filename}|{content_digest_sha256}".encode("utf-8")
        ).hexdigest()[:32]
        source_id = f"raw-source:{identity}"
        source_version_id = f"{source_id}:v1"
        index_version_id = f"{source_id}:index:1"
        evidence_ids = tuple(
            f"{source_id}:evidence:{index}" for index in range(1, len(evidence) + 1)
        )
        if any(row.aggregate_id == source_id for row in existing):
            raise _fail("RAW_SOURCE_IMMUTABLE")

        created_at = _timestamp()
        try:
            def envelopes(object_id: str) -> tuple[RawSourceCanonicalInput, ...]:
                source_payload: dict[str, object] = {
                    "source_id": source_id,
                    "source_version_id": source_version_id,
                    "filename": filename,
                    "content_type": content_type,
                    "object_id": object_id,
                    "digest_sha256": content_digest_sha256,
                    "index_version_id": index_version_id,
                    "evidence_span_ids": list(evidence_ids),
                    "quality_state": "unverified",
                    "authority": "user_source",
                    "data_area": "local_private",
                    "idempotency_key": idempotency_key,
                }
                index_payload: dict[str, object] = {
                    "source_version_id": source_version_id,
                    "evidence_span_ids": list(evidence_ids),
                    "state": "completed",
                    "extractor": "pypdf-6.14.2" if content_type == "application/pdf" else "utf8",
                }
                items = [
                    RawSourceCanonicalInput(
                        "SourceVersion", source_version_id, source_id,
                        source_payload, created_at,
                    ),
                    RawSourceCanonicalInput(
                        "IndexVersion", index_version_id, source_id,
                        index_payload, created_at,
                    ),
                ]
                for evidence_id, (page, text) in zip(evidence_ids, evidence, strict=True):
                    items.append(RawSourceCanonicalInput(
                        "EvidenceSpan", evidence_id, evidence_id,
                        {
                            "source_version_id": source_version_id,
                            "index_version_id": index_version_id,
                            "page": page,
                            "text": text,
                            "text_digest_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "origin": "raw_source",
                            "unverified": True,
                        },
                        created_at,
                    ))
                return tuple(items)

            object_id = self._store.put_raw_source_bundle(
                workspace_id, content, content_type=content_type,
                build_envelopes=envelopes,
            )
            source_payload = dict(envelopes(object_id)[0].payload)
            return self._view(source_payload)
        except (LocalStorageError, RawSourceError) as error:
            if isinstance(error, RawSourceError):
                raise
            raise _fail("RAW_SOURCE_STORAGE_FAILED") from error

    def list_sources(self, workspace_id: str) -> tuple[RawSourceView, ...]:
        if not _SAFE_ID.fullmatch(workspace_id):
            raise _fail("RAW_SOURCE_INPUT_INVALID")
        return tuple(
            self._view(row.payload)
            for row in self._store.list_canonical_envelopes(
                workspace_id, "source", "SourceVersion"
            )
        )

    @staticmethod
    def _view(payload: dict[str, object]) -> RawSourceView:
        try:
            if set(payload) != {
                "source_id", "source_version_id", "filename", "content_type",
                "object_id", "digest_sha256", "index_version_id",
                "evidence_span_ids", "quality_state", "authority", "data_area",
                "idempotency_key",
            }:
                raise ValueError
            evidence = payload["evidence_span_ids"]
            text_fields = {
                name: payload[name]
                for name in (
                    "source_id", "source_version_id", "filename", "content_type",
                    "object_id", "digest_sha256", "index_version_id", "idempotency_key",
                )
            }
            if (
                not evidence
                or not isinstance(evidence, list)
                or not all(isinstance(item, str) and _SAFE_ID.fullmatch(item) for item in evidence)
                or not all(isinstance(value, str) for value in text_fields.values())
                or not isinstance(payload["filename"], str)
                or not payload["filename"]
                or payload["filename"] != payload["filename"].strip()
                or any(char in payload["filename"] for char in ("/", "\\", "\x00"))
                or not all(_SAFE_ID.fullmatch(str(text_fields[name])) for name in (
                    "source_id", "source_version_id", "object_id", "index_version_id",
                    "idempotency_key",
                ))
                or payload["content_type"] not in _CONTENT_TYPES
                or not _DIGEST.fullmatch(str(payload["digest_sha256"]))
                or payload["quality_state"] != "unverified"
                or payload["authority"] != "user_source"
                or payload["data_area"] != "local_private"
            ):
                raise ValueError
            safe_text = cast(dict[str, str], text_fields)
            return RawSourceView(
                source_id=safe_text["source_id"],
                source_version_id=safe_text["source_version_id"],
                index_version_id=safe_text["index_version_id"],
                filename=safe_text["filename"],
                content_type=safe_text["content_type"],
                object_id=safe_text["object_id"],
                digest_sha256=safe_text["digest_sha256"],
                evidence_span_ids=tuple(evidence),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise _fail("RAW_SOURCE_PROJECTION_INVALID") from error
