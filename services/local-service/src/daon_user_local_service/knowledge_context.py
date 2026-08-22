from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Callable, Protocol


class OfflineStudioError(RuntimeError):
    """Stable fail-closed offline Studio error."""


class KnowledgeContextMode(str, Enum):
    DAON_PRIORITY = "daon_priority"
    MIXED = "mixed"
    RAW_ONLY = "raw_only"


@dataclass(frozen=True, slots=True)
class KnowledgeContextItem:
    item_id: str
    origin: str
    producer: str
    version_id: str
    digest: str
    authority: str
    quality_state: str
    weight: float
    producer_version: str = ""
    registration_id: str | None = None
    source_id: str | None = None
    index_version_id: str | None = None
    evidence_span_ids: tuple[str, ...] = ()
    review_state: str = ""
    processing_state: str = ""
    effective_at: str | None = None
    expires_at: str | None = None
    conflict_state: str = "none"
    conflict_acknowledged: bool = False
    unverified: bool = False
    selection_reason: str = ""
    registration_state: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeContextSnapshot:
    snapshot_id: str
    workspace_id: str
    mode: KnowledgeContextMode
    items: tuple[KnowledgeContextItem, ...]
    knowledge_scope_id: str
    weight_profile_id: str
    warnings: tuple[str, ...]
    digest: str
    schema_version: int = 1
    created_at: str = "1970-01-01T00:00:00Z"


class KnowledgeProjectionPort(Protocol):
    def get_daon_knowledge(
        self, *, workspace_id: str, knowledge_id: str
    ) -> Mapping[str, object] | None: ...

    def get_raw_source(
        self, *, workspace_id: str, source_version_id: str
    ) -> Mapping[str, object] | None: ...


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _text(record: Mapping[str, object], name: str, code: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value:
        raise OfflineStudioError(code)
    return value


def _timestamp(record: Mapping[str, object], name: str, code: str) -> datetime:
    value = _text(record, name, code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise OfflineStudioError(code) from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise OfflineStudioError(code)
    return parsed.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise OfflineStudioError("KNOWLEDGE_CONTEXT_CLOCK_INVALID")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def context_item_lineage(item: KnowledgeContextItem) -> dict[str, object]:
    """Return safe immutable lineage for a selected Context item."""
    return {
        "item_id": item.item_id,
        "origin": item.origin,
        "producer": item.producer,
        "producer_version": item.producer_version,
        "registration_id": item.registration_id,
        "registration_state": item.registration_state,
        "source_id": item.source_id,
        "version_id": item.version_id,
        "index_version_id": item.index_version_id,
        "evidence_span_ids": list(item.evidence_span_ids),
        "digest": item.digest,
        "authority": item.authority,
        "quality_state": item.quality_state,
        "review_state": item.review_state,
        "conflict_state": item.conflict_state,
        "unverified": item.unverified,
    }


def citation_lineage(item: KnowledgeContextItem) -> dict[str, object]:
    """Return Context lineage using the output Citation identifier."""
    payload = context_item_lineage(item)
    payload["citation_id"] = payload.pop("item_id")
    return payload


class KnowledgeContextProjector:
    def __init__(
        self,
        source: KnowledgeProjectionPort,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._clock = clock or (lambda: datetime.now(UTC))

    def project(
        self,
        *,
        workspace_id: str,
        mode: str,
        daon_knowledge_ids: tuple[str, ...],
        raw_source_version_ids: tuple[str, ...],
    ) -> KnowledgeContextSnapshot:
        try:
            selected_mode = KnowledgeContextMode(mode)
        except ValueError:
            raise OfflineStudioError("KNOWLEDGE_CONTEXT_MODE_INVALID") from None
        if selected_mode is KnowledgeContextMode.RAW_ONLY and daon_knowledge_ids:
            raise OfflineStudioError("RAW_ONLY_SELECTION_INVALID")

        now = self._clock()
        created_at = _iso_z(now)
        now = now.astimezone(UTC)

        items: list[KnowledgeContextItem] = []
        warnings: list[str] = []
        seen_digests: set[str] = set()
        for knowledge_id in daon_knowledge_ids:
            record = self._source.get_daon_knowledge(
                workspace_id=workspace_id, knowledge_id=knowledge_id
            )
            if record is None or record.get("effective") is not True:
                raise OfflineStudioError("DAON_KNOWLEDGE_UNAVAILABLE")
            producer = _text(record, "producer", "DAON_KNOWLEDGE_INVALID")
            if producer not in {"daon2", "daon2_5", "daon3"}:
                raise OfflineStudioError("DAON_KNOWLEDGE_INVALID")
            effective_at = _timestamp(record, "effective_at", "DAON_KNOWLEDGE_INVALID")
            expires_at = _timestamp(record, "expires_at", "DAON_KNOWLEDGE_INVALID")
            if (
                record.get("registration_state") != "registered"
                or record.get("review_state") != "approved"
                or record.get("authority") != "approved"
                or not effective_at <= now < expires_at
            ):
                raise OfflineStudioError("DAON_KNOWLEDGE_UNAVAILABLE")
            digest = _text(record, "digest", "DAON_KNOWLEDGE_INVALID")
            if digest != record.get("registration_digest"):
                raise OfflineStudioError("DAON_KNOWLEDGE_STALE")
            if digest in seen_digests:
                warnings.append(f"duplicate_digest_excluded:{knowledge_id}")
                continue
            seen_digests.add(digest)
            items.append(KnowledgeContextItem(
                knowledge_id, "daon_knowledge", producer,
                _text(record, "version_id", "DAON_KNOWLEDGE_INVALID"), digest,
                _text(record, "authority", "DAON_KNOWLEDGE_INVALID"),
                _text(record, "quality_state", "DAON_KNOWLEDGE_INVALID"), 1.0,
                producer_version=_text(
                    record, "producer_version", "DAON_KNOWLEDGE_INVALID"
                ),
                registration_id=_text(
                    record, "registration_id", "DAON_KNOWLEDGE_INVALID"
                ),
                registration_state="registered",
                review_state="approved",
                effective_at=_iso_z(effective_at),
                expires_at=_iso_z(expires_at),
                selection_reason="registered_knowledge_priority",
            ))
        if selected_mode is KnowledgeContextMode.DAON_PRIORITY and not items:
            raise OfflineStudioError("DAON_KNOWLEDGE_UNAVAILABLE")

        for source_version_id in raw_source_version_ids:
            record = self._source.get_raw_source(
                workspace_id=workspace_id, source_version_id=source_version_id
            )
            if record is None:
                raise OfflineStudioError("RAW_SOURCE_UNAVAILABLE")
            if record.get("local") is not True:
                raise OfflineStudioError("RAW_SOURCE_NOT_LOCAL")
            if (
                record.get("processing_state") != "completed"
                or record.get("review_state") not in {
                    "unverified", "approved", "needs_review"
                }
            ):
                raise OfflineStudioError("RAW_SOURCE_UNAVAILABLE")
            digest = _text(record, "digest", "RAW_SOURCE_INVALID")
            if digest in seen_digests:
                warnings.append(f"duplicate_digest_excluded:{source_version_id}")
                continue
            seen_digests.add(digest)
            evidence_span_ids = record.get("evidence_span_ids")
            if (
                not isinstance(evidence_span_ids, list)
                or not evidence_span_ids
                or any(not isinstance(item, str) or not item for item in evidence_span_ids)
                or len(evidence_span_ids) != len(set(evidence_span_ids))
                or not record.get("index_version_id")
            ):
                raise OfflineStudioError("RAW_SOURCE_INVALID")
            conflict_state = _text(record, "conflict_state", "RAW_SOURCE_INVALID")
            conflict_acknowledged = record.get("conflict_acknowledged")
            if (
                conflict_state not in {"none", "unresolved", "resolved"}
                or not isinstance(conflict_acknowledged, bool)
            ):
                raise OfflineStudioError("RAW_SOURCE_INVALID")
            if conflict_state == "unresolved":
                warnings.append(f"unresolved_conflict:{source_version_id}")
            items.append(KnowledgeContextItem(
                source_version_id, "raw_source", "raw",
                _text(record, "version_id", "RAW_SOURCE_INVALID"), digest,
                _text(record, "authority", "RAW_SOURCE_INVALID"),
                _text(record, "quality_state", "RAW_SOURCE_INVALID"),
                0.6 if selected_mode is KnowledgeContextMode.DAON_PRIORITY else 1.0,
                source_id=_text(record, "source_id", "RAW_SOURCE_INVALID"),
                index_version_id=_text(
                    record, "index_version_id", "RAW_SOURCE_INVALID"
                ),
                evidence_span_ids=tuple(evidence_span_ids),
                review_state=_text(record, "review_state", "RAW_SOURCE_INVALID"),
                processing_state="completed",
                conflict_state=conflict_state,
                conflict_acknowledged=conflict_acknowledged,
                unverified=record.get("review_state") != "approved",
                selection_reason="explicit_raw_source",
            ))
        if selected_mode is KnowledgeContextMode.RAW_ONLY:
            if not items:
                raise OfflineStudioError("RAW_SOURCE_UNAVAILABLE")
            warnings.extend(("unverified_input", "strengthened_review_required"))
        if not items:
            raise OfflineStudioError("KNOWLEDGE_CONTEXT_EMPTY")

        payload = {
            "schema_version": 1, "created_at": created_at,
            "workspace_id": workspace_id, "mode": selected_mode.value,
            "items": [asdict(item) for item in items], "warnings": warnings,
            "knowledge_scope_id": "offline-scope-v1", "weight_profile_id": "offline-weights-v1",
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return KnowledgeContextSnapshot(
            f"scope-{digest[:24]}", workspace_id, selected_mode, tuple(items),
            "offline-scope-v1", "offline-weights-v1", tuple(warnings), digest,
            1, created_at,
        )
