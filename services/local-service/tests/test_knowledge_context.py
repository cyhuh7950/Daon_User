from __future__ import annotations

from datetime import UTC, datetime

import pytest

from daon_user_local_service.knowledge_context import (
    KnowledgeContextProjector,
    OfflineStudioError,
)


WORKSPACE = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class ProjectionSource:
    def __init__(self) -> None:
        self.knowledge = {
            "knowledge-daon3": {
                "producer": "daon3", "producer_version": "3.0.0",
                "registration_id": "registration-v3",
                "version_id": "output-v3", "digest": "a" * 64,
                "registration_digest": "a" * 64, "quality_state": "approved",
                "review_state": "approved", "registration_state": "registered",
                "authority": "approved", "effective": True,
                "effective_at": "2026-08-13T12:00:00Z",
                "expires_at": "2026-08-15T12:00:00Z",
            }
        }
        self.raw = {
            "source-v1": {
                "source_id": "source-1", "version_id": "source-v1", "digest": "b" * 64,
                "index_version_id": "index-v1", "evidence_span_ids": ["span-v1"],
                "processing_state": "completed", "review_state": "unverified",
                "quality_state": "unverified", "authority": "user_source",
                "conflict_state": "none", "conflict_acknowledged": False,
                "local": True,
            }
        }

    def get_daon_knowledge(self, *, workspace_id: str, knowledge_id: str) -> dict[str, object] | None:
        return self.knowledge.get(knowledge_id)

    def get_raw_source(self, *, workspace_id: str, source_version_id: str) -> dict[str, object] | None:
        return self.raw.get(source_version_id)


def test_daon_priority_and_mixed_preserve_explicit_origin_and_order() -> None:
    projector = KnowledgeContextProjector(ProjectionSource(), clock=lambda: NOW)
    priority = projector.project(
        workspace_id=WORKSPACE, mode="daon_priority",
        daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=("source-v1",),
    )
    assert priority.mode.value == "daon_priority"
    assert [item.origin for item in priority.items] == ["daon_knowledge", "raw_source"]
    mixed = projector.project(
        workspace_id=WORKSPACE, mode="mixed",
        daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=("source-v1",),
    )
    assert {item.origin for item in mixed.items} == {"daon_knowledge", "raw_source"}
    assert mixed.schema_version == 1
    assert mixed.created_at == "2026-08-14T12:00:00Z"
    assert mixed.items[0].registration_id == "registration-v3"
    assert mixed.items[0].registration_state == "registered"
    assert mixed.items[0].effective_at == "2026-08-13T12:00:00Z"
    assert mixed.items[1].source_id == "source-1"
    assert mixed.items[1].index_version_id == "index-v1"
    assert mixed.items[1].evidence_span_ids == ("span-v1",)
    assert mixed.digest == projector.project(
        workspace_id=WORKSPACE, mode="mixed",
        daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=("source-v1",),
    ).digest


def test_fail_closed_knowledge_and_explicit_raw_only_warning() -> None:
    source = ProjectionSource()
    projector = KnowledgeContextProjector(source, clock=lambda: NOW)
    with pytest.raises(OfflineStudioError, match="DAON_KNOWLEDGE_UNAVAILABLE"):
        projector.project(
            workspace_id=WORKSPACE, mode="daon_priority",
            daon_knowledge_ids=(), raw_source_version_ids=("source-v1",),
        )
    source.knowledge["knowledge-daon3"]["registration_digest"] = "c" * 64
    with pytest.raises(OfflineStudioError, match="DAON_KNOWLEDGE_STALE"):
        projector.project(
            workspace_id=WORKSPACE, mode="mixed",
            daon_knowledge_ids=("knowledge-daon3",), raw_source_version_ids=(),
        )
    raw = projector.project(
        workspace_id=WORKSPACE, mode="raw_only",
        daon_knowledge_ids=(), raw_source_version_ids=("source-v1",),
    )
    assert raw.warnings == ("unverified_input", "strengthened_review_required")


def test_raw_is_never_silently_promoted_and_non_local_source_fails() -> None:
    source = ProjectionSource()
    source.raw["source-v1"]["local"] = False
    with pytest.raises(OfflineStudioError, match="RAW_SOURCE_NOT_LOCAL"):
        KnowledgeContextProjector(source, clock=lambda: NOW).project(
            workspace_id=WORKSPACE, mode="raw_only",
            daon_knowledge_ids=(), raw_source_version_ids=("source-v1",),
        )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("registration_state", "draft", "DAON_KNOWLEDGE_UNAVAILABLE"),
        ("review_state", "pending", "DAON_KNOWLEDGE_UNAVAILABLE"),
        ("authority", "unverified", "DAON_KNOWLEDGE_UNAVAILABLE"),
        ("effective_at", "2026-08-15T12:00:00Z", "DAON_KNOWLEDGE_UNAVAILABLE"),
        ("expires_at", "2026-08-14T12:00:00Z", "DAON_KNOWLEDGE_UNAVAILABLE"),
    ),
)
def test_daon_knowledge_requires_approved_current_registration(
    field: str, value: object, code: str
) -> None:
    source = ProjectionSource()
    source.knowledge["knowledge-daon3"][field] = value

    with pytest.raises(OfflineStudioError, match=code):
        KnowledgeContextProjector(source, clock=lambda: NOW).project(
            workspace_id=WORKSPACE,
            mode="daon_priority",
            daon_knowledge_ids=("knowledge-daon3",),
            raw_source_version_ids=(),
        )


def test_raw_lineage_and_unresolved_conflict_are_preserved_not_hidden() -> None:
    source = ProjectionSource()
    source.raw["source-v1"]["conflict_state"] = "unresolved"
    snapshot = KnowledgeContextProjector(source, clock=lambda: NOW).project(
        workspace_id=WORKSPACE,
        mode="mixed",
        daon_knowledge_ids=("knowledge-daon3",),
        raw_source_version_ids=("source-v1",),
    )

    raw = snapshot.items[1]
    assert raw.origin == "raw_source"
    assert raw.processing_state == "completed"
    assert raw.review_state == "unverified"
    assert raw.conflict_state == "unresolved"
    assert raw.conflict_acknowledged is False
    assert "unresolved_conflict:source-v1" in snapshot.warnings


@pytest.mark.parametrize(
    ("field", "value"),
    (("processing_state", "processing"), ("review_state", "rejected")),
)
def test_raw_source_requires_completed_processing_and_allowed_review(
    field: str, value: object
) -> None:
    source = ProjectionSource()
    source.raw["source-v1"][field] = value

    with pytest.raises(OfflineStudioError, match="RAW_SOURCE_UNAVAILABLE"):
        KnowledgeContextProjector(source, clock=lambda: NOW).project(
            workspace_id=WORKSPACE,
            mode="raw_only",
            daon_knowledge_ids=(),
            raw_source_version_ids=("source-v1",),
        )
