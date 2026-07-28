from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from daon_user_api.audit import (  # noqa: E402
    ActorType,
    AuditEventDraft,
    AuditEventStore,
    AuditOutcome,
    IntegrityCode,
)


BASE_TIME = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)


def change_draft(index: int, *, trace_id: str = "trace-lineage-001") -> AuditEventDraft:
    return AuditEventDraft(
        event_id=f"change-{index:03d}",
        occurred_at=BASE_TIME + timedelta(seconds=index),
        actor_id="actor-001",
        actor_type=ActorType.USER,
        tenant_id="tenant-001",
        workspace_id="workspace-001",
        action="workspace.policy.changed",
        target_type="workspace_policy",
        target_id="policy-001",
        outcome=AuditOutcome.SUCCEEDED,
        trace_id=trace_id,
        policy_version="policy-v3",
        before={"revision": index - 1},
        after={"revision": index},
        metadata={"change_index": index},
    )


class AuditIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = AuditEventStore()
        self.events = tuple(self.store.append(change_draft(i)) for i in range(1, 4))

    def test_three_event_change_lineage_preserves_before_after_and_trace(self) -> None:
        page = self.store.list(tenant_id="tenant-001", trace_id="trace-lineage-001")
        self.assertEqual(page.items, self.events)
        self.assertEqual([event.before["revision"] for event in page.items], [0, 1, 2])
        self.assertEqual([event.after["revision"] for event in page.items], [1, 2, 3])
        self.assertEqual(self.store.verify_integrity().code, IntegrityCode.OK)

    def test_field_order_missing_previous_and_hash_tampering_are_detected(self) -> None:
        cases = {
            "field": (
                (self.events[0], replace(self.events[1], action="changed"), self.events[2]),
                IntegrityCode.EVENT_HASH_MISMATCH,
            ),
            "order": (
                (self.events[1], self.events[0], self.events[2]),
                IntegrityCode.SEQUENCE_MISMATCH,
            ),
            "missing": (
                (self.events[0], self.events[2]),
                IntegrityCode.SEQUENCE_MISMATCH,
            ),
            "previous_hash": (
                (
                    self.events[0],
                    replace(self.events[1], previous_event_hash="f" * 64),
                    self.events[2],
                ),
                IntegrityCode.PREVIOUS_HASH_MISMATCH,
            ),
            "event_hash": (
                (self.events[0], replace(self.events[1], event_hash="f" * 64), self.events[2]),
                IntegrityCode.EVENT_HASH_MISMATCH,
            ),
        }
        for name, (candidate, expected) in cases.items():
            with self.subTest(name=name):
                result = self.store.verify_integrity(candidate)
                self.assertFalse(result.valid)
                self.assertEqual(result.code, expected)
                self.assertNotIn("revision", result.message)

    def test_concurrent_append_assigns_one_atomic_chain(self) -> None:
        store = AuditEventStore()
        with ThreadPoolExecutor(max_workers=8) as executor:
            events = list(executor.map(lambda index: store.append(change_draft(index)), range(1, 33)))

        snapshot = store.list(tenant_id="tenant-001", limit=100).items
        self.assertEqual(len(snapshot), 32)
        self.assertEqual({event.sequence for event in events}, set(range(1, 33)))
        self.assertTrue(store.verify_integrity().valid)


if __name__ == "__main__":
    unittest.main()
