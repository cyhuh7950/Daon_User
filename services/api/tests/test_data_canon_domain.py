from __future__ import annotations

import hashlib
import os
import unittest
from datetime import UTC, datetime

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.data_canon import (
    CanonError,
    CanonicalContext,
    CanonicalSnapshot,
    PostgresDataCanonStore,
    canonical_json_bytes,
    transition_allowed,
)


class DataCanonDomainTests(unittest.TestCase):
    def test_canonical_json_and_digest_are_deterministic(self) -> None:
        first = canonical_json_bytes({"b": 2, "a": 1})
        second = canonical_json_bytes({"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertEqual(first, b'{"a":1,"b":2}')

    def test_snapshot_rejects_missing_fields_digest_and_previous_chain(self) -> None:
        payload = {"source_version_ids": ["sv-1"], "knowledge_scope_id": "scope-1"}
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        with self.assertRaisesRegex(CanonError, "CANON_DIGEST_MISMATCH"):
            CanonicalSnapshot("snapshot-1", "run-1", 1, 1, "0" * 64, None, payload)
        with self.assertRaisesRegex(CanonError, "CANON_PREVIOUS_VERSION_INVALID"):
            CanonicalSnapshot("snapshot-2", "run-1", 2, 1, digest, None, payload)
        with self.assertRaisesRegex(CanonError, "CANON_SNAPSHOT_INVALID"):
            CanonicalSnapshot("snapshot-1", "run-1", 1, 1, digest, None, ["not-object"])  # type: ignore[arg-type]

    def test_state_transition_matrix_allows_defined_edges_only(self) -> None:
        self.assertTrue(transition_allowed("Source", "registered", "security_check"))
        self.assertTrue(transition_allowed("Run", "validating", "completed"))
        self.assertTrue(transition_allowed("ApprovalRequest", "pending", "rejected"))
        self.assertFalse(transition_allowed("Source", "ready", "registered"))
        self.assertFalse(transition_allowed("Run", "completed", "generating"))
        self.assertFalse(transition_allowed("OutputVersion", "approved", "draft"))
        self.assertTrue(transition_allowed("GenerationRequest", "configuring", "confirmed"))
        self.assertTrue(transition_allowed("GenerationRequest", "confirmed", "submitted"))
        self.assertTrue(transition_allowed("GenerationRequest", "confirmed", "configuring"))
        self.assertFalse(transition_allowed("GenerationRequest", "submitted", "configuring"))
        self.assertFalse(transition_allowed("GenerationRequest", "submitted", "confirmed"))


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class PostgresDataCanonIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
        suffix = self._testMethodName
        self.context = CanonicalContext(
            f"tenant-canon-{suffix}",
            f"workspace-canon-{suffix}",
            f"actor-canon-{suffix}",
            "canon.write",
            f"trace-canon-{suffix}",
        )
        cloud = PostgresCloudStore(dsn)
        cloud.seed_scope(self.context.cloud_context())
        cloud.close()
        self.store = PostgresDataCanonStore(dsn)

    def tearDown(self) -> None:
        self.store.close()

    def test_source_version_lineage_transition_and_lost_update(self) -> None:
        source_id = "source-canon-1"
        self.store.create_source(self.context, source_id)
        payload = {"object_id": None, "title": "version one"}
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        self.store.append_source_version(
            self.context,
            source_version_id="source-version-canon-1",
            source_id=source_id,
            version_number=1,
            previous_version_id=None,
            canonical_payload=payload,
            digest_sha256=digest,
            created_at=datetime.now(UTC),
        )
        first = self.store.transition(
            self.context,
            entity_type="Source",
            record_id=source_id,
            expected_version=1,
            target_state="security_check",
            transition_id="transition-canon-1",
            reason_code="SOURCE_ACCEPTED",
            policy_version="policy-canon-1",
        )
        self.assertEqual((first.state, first.version), ("security_check", 2))
        self.assertEqual(self.store.count(self.context, "canon_transition_attempts", source_id), 1)
        with self.assertRaisesRegex(CanonError, "CANON_VERSION_CONFLICT"):
            self.store.transition(
                self.context,
                entity_type="Source",
                record_id=source_id,
                expected_version=1,
                target_state="processing",
                transition_id="transition-canon-stale",
                reason_code="STALE_WRITER",
                policy_version="policy-canon-1",
            )
        self.assertEqual(self.store.count(self.context, "canon_transition_attempts", source_id), 2)
        with self.assertRaisesRegex(CanonError, "CANON_RECORD_NOT_FOUND"):
            self.store.transition(
                self.context,
                entity_type="Source",
                record_id="source-canon-missing",
                expected_version=1,
                target_state="security_check",
                transition_id="transition-canon-missing",
                reason_code="MISSING_TARGET",
                policy_version="policy-canon-1",
            )
        self.assertEqual(
            self.store.count(
                self.context, "canon_transition_attempts", "source-canon-missing"
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
