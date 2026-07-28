from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from daon_user_api.audit import (  # noqa: E402
    ActorType,
    AuditDuplicateEventError,
    AuditEventDraft,
    AuditEventStore,
    AuditOutcome,
    AuditValidationError,
    IntegrityCode,
)


UTC_1 = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)


def draft(
    event_id: str = "audit-event-001",
    *,
    occurred_at: datetime = UTC_1,
    actor_id: str = "actor-001",
    actor_type: ActorType = ActorType.USER,
    tenant_id: str = "tenant-001",
    workspace_id: str | None = "workspace-001",
    action: str = "workspace.policy.changed",
    target_type: str = "workspace_policy",
    target_id: str = "policy-001",
    outcome: AuditOutcome = AuditOutcome.SUCCEEDED,
    trace_id: str = "trace-001",
    policy_version: str = "policy-v3",
    before: object = None,
    after: object = None,
    metadata: object = None,
) -> AuditEventDraft:
    return AuditEventDraft(
        event_id=event_id,
        occurred_at=occurred_at,
        actor_id=actor_id,
        actor_type=actor_type,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        trace_id=trace_id,
        policy_version=policy_version,
        before={"status": "draft"} if before is None else before,
        after={"status": "approved"} if after is None else after,
        metadata={"reason_code": "review_complete"} if metadata is None else metadata,
    )


class AuditCoreTests(unittest.TestCase):
    def test_package_exports_public_audit_contract(self) -> None:
        import daon_user_api

        self.assertIs(daon_user_api.AuditEventStore, AuditEventStore)
        self.assertIs(daon_user_api.AuditEventDraft, AuditEventDraft)
        self.assertIn("AuditEvent", daon_user_api.__all__)

    def test_append_assigns_sequence_chain_and_immutable_safe_projection(self) -> None:
        store = AuditEventStore()
        first = store.append(draft())
        second = store.append(
            draft(
                "audit-event-002",
                occurred_at=UTC_1 + timedelta(seconds=1),
                before={"nested": {"value": 1}, "items": ["a", "b"]},
            )
        )

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(first.previous_event_hash, "0" * 64)
        self.assertEqual(second.previous_event_hash, first.event_hash)
        self.assertRegex(first.event_hash, r"^[0-9a-f]{64}$")
        self.assertTrue(store.verify_integrity().valid)
        with self.assertRaises(FrozenInstanceError):
            first.action = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            second.before["nested"]["value"] = 2  # type: ignore[index]
        with self.assertRaises(TypeError):
            second.metadata["new"] = "value"  # type: ignore[index]

    def test_hash_is_deterministic_for_canonical_json_and_all_semantic_fields(self) -> None:
        left = AuditEventStore().append(
            draft(before={"b": 2, "a": 1}, after={"목록": [2, 1]})
        )
        right = AuditEventStore().append(
            draft(before={"a": 1, "b": 2}, after={"목록": [2, 1]})
        )
        changed = AuditEventStore().append(
            draft(before={"a": 1, "b": 2}, after={"목록": [1, 2]})
        )

        self.assertEqual(left.event_hash, right.event_hash)
        self.assertNotEqual(left.event_hash, changed.event_hash)

    def test_required_values_time_and_enums_fail_before_storage_change(self) -> None:
        replacements = {
            "event_id": "",
            "actor_id": " ",
            "tenant_id": "",
            "action": "",
            "target_type": "",
            "target_id": "",
            "trace_id": "",
            "policy_version": "",
            "occurred_at": datetime(2026, 7, 29, 1, 0),
            "actor_type": "admin",
            "outcome": "ok",
        }
        for field, value in replacements.items():
            with self.subTest(field=field):
                store = AuditEventStore()
                with self.assertRaises(AuditValidationError):
                    store.append(replace(draft(), **{field: value}))
                self.assertEqual(store.list(tenant_id="tenant-001").items, ())

        non_utc = timezone(timedelta(hours=9))
        with self.assertRaises(AuditValidationError):
            AuditEventStore().append(
                draft(occurred_at=datetime(2026, 7, 29, 10, 0, tzinfo=non_utc))
            )

    def test_duplicate_event_id_is_atomic_and_does_not_change_chain(self) -> None:
        store = AuditEventStore()
        first = store.append(draft())
        with self.assertRaises(AuditDuplicateEventError):
            store.append(draft(after={"status": "different"}))

        page = store.list(tenant_id="tenant-001")
        self.assertEqual(page.items, (first,))
        self.assertIsNone(page.next_cursor)
        self.assertTrue(store.verify_integrity().valid)

    def test_nested_sensitive_keys_internal_locations_and_non_json_values_are_rejected(self) -> None:
        unsafe_values = [
            {"profile": {"password": "masked"}},
            {"apiKey": "masked"},
            {"provider": {"raw_provider_error": "masked"}},
            {"internal_url": "masked"},
            {"location": "http://service.internal/audit"},
            {"location": "http://127.0.0.1:8080"},
            {"location": "http://host.docker.internal:8080"},
            {"value": object()},
            {"value": float("nan")},
        ]
        for index, unsafe in enumerate(unsafe_values):
            with self.subTest(index=index):
                store = AuditEventStore()
                with self.assertRaises(AuditValidationError) as context:
                    store.append(draft(before=unsafe))
                self.assertNotIn("masked", str(context.exception))
                self.assertEqual(store.list(tenant_id="tenant-001").items, ())

    def test_list_filters_trace_lineage_and_opaque_cursor(self) -> None:
        store = AuditEventStore()
        store.append(draft("event-1", occurred_at=UTC_1, action="source.created"))
        store.append(
            draft(
                "event-2",
                occurred_at=UTC_1 + timedelta(minutes=1),
                action="source.updated",
                outcome=AuditOutcome.DENIED,
            )
        )
        store.append(
            draft(
                "event-3",
                occurred_at=UTC_1 + timedelta(minutes=2),
                workspace_id="workspace-002",
                trace_id="trace-002",
            )
        )
        store.append(
            draft(
                "event-4",
                occurred_at=UTC_1 + timedelta(minutes=3),
                tenant_id="tenant-002",
            )
        )

        first_page = store.list(tenant_id="tenant-001", limit=1)
        self.assertEqual([item.event_id for item in first_page.items], ["event-1"])
        self.assertIsNotNone(first_page.next_cursor)
        self.assertNotEqual(first_page.next_cursor, "1")
        second_page = store.list(
            tenant_id="tenant-001", cursor=first_page.next_cursor, limit=10
        )
        self.assertEqual(
            [item.event_id for item in second_page.items], ["event-2", "event-3"]
        )
        trace_page = store.list(tenant_id="tenant-001", trace_id="trace-001")
        self.assertEqual([item.event_id for item in trace_page.items], ["event-1", "event-2"])
        denied = store.list(
            tenant_id="tenant-001",
            workspace_id="workspace-001",
            action="source.updated",
            outcome=AuditOutcome.DENIED,
            occurred_after=UTC_1 + timedelta(seconds=30),
            occurred_before=UTC_1 + timedelta(minutes=1),
        )
        self.assertEqual([item.event_id for item in denied.items], ["event-2"])

    def test_public_store_surface_has_no_mutation_or_internal_list_escape(self) -> None:
        store = AuditEventStore()
        event = store.append(draft())
        page = store.list(tenant_id="tenant-001")
        self.assertIsInstance(page.items, tuple)
        self.assertIs(store.read(event.event_id), event)
        for forbidden in ("update", "delete", "replace", "clear"):
            self.assertFalse(hasattr(store, forbidden))


if __name__ == "__main__":
    unittest.main()
