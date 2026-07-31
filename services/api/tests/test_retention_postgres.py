from __future__ import annotations

import os
import secrets
import unittest
from datetime import UTC, datetime, timedelta

from daon_user_api.cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class RetentionPostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PostgresCloudStore(os.environ["DAON_TEST_POSTGRES_DSN"])
        suffix = secrets.token_hex(6)
        tenant = f"tenant-retention-{suffix}"
        actor = f"actor-retention-{suffix}"
        self.scope_a = CloudAccessContext(
            tenant, f"workspace-retention-a-{suffix}", actor, "retention.write"
        )
        self.scope_b = CloudAccessContext(
            tenant, f"workspace-retention-b-{suffix}", actor, "retention.write"
        )
        self.store.seed_scope(self.scope_a)
        self.store.seed_scope(self.scope_b)
        self.request_id = f"deletion-{suffix}"
        self.source_id = f"fixture-source-{suffix}"
        self.now = datetime.now(UTC)

    def tearDown(self) -> None:
        self.store.close()

    def _seed_request(self) -> None:
        with self.store._transaction(self.scope_a) as connection:
            connection.execute(
                "INSERT INTO deletion_request_locator "
                "(tenant_id,request_id,workspace_id,source_id,created_at) VALUES (%s,%s,%s,%s,%s)",
                (self.scope_a.tenant_id, self.request_id, self.scope_a.workspace_id,
                 self.source_id, self.now),
            )
            connection.execute(
                "INSERT INTO deletion_requests "
                "(tenant_id,workspace_id,request_id,source_id,actor_id,state,version,source_active,"
                "purge_started,grace_until,policy_version,idempotency_key,request_fingerprint,"
                "trace_id,created_at,updated_at) VALUES "
                "(%s,%s,%s,%s,%s,'grace_period',1,false,false,%s,'policy-retention-v1',"
                "'idem-retention-create',%s,%s,%s,%s)",
                (self.scope_a.tenant_id, self.scope_a.workspace_id, self.request_id,
                 self.source_id, self.scope_a.actor_id, self.now + timedelta(days=30),
                 "a" * 64, f"trace-{self.request_id}", self.now, self.now),
            )
            for kind in (
                "original_content", "index", "preview", "cache",
                "known_local_copy", "sync_reference",
            ):
                connection.execute(
                    "INSERT INTO deletion_cleanup_items "
                    "(tenant_id,workspace_id,request_id,reference_id,derivative_kind,state,"
                    "acknowledgement_required,attempt_count,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,'pending',%s,0,%s)",
                    (self.scope_a.tenant_id, self.scope_a.workspace_id, self.request_id,
                     f"fixture-{kind}-{self.request_id}", kind,
                     kind == "known_local_copy", self.now),
                )

    def test_rls_cross_scope_fk_append_only_and_hold_priority(self) -> None:
        self._seed_request()
        with self.store._transaction(self.scope_b) as connection:
            count = connection.execute(
                "SELECT count(*) FROM deletion_requests WHERE request_id=%s", (self.request_id,)
            ).fetchone()
            self.assertEqual(int((count or (1,))[0]), 0)
        with self.assertRaises(CloudDatabaseError):
            with self.store._transaction(self.scope_b) as connection:
                connection.execute(
                    "INSERT INTO deletion_cleanup_items "
                    "(tenant_id,workspace_id,request_id,reference_id,derivative_kind,state,"
                    "acknowledgement_required,attempt_count,updated_at) "
                    "VALUES (%s,%s,%s,'fixture-cross-scope','cache','pending',false,0,%s)",
                    (self.scope_b.tenant_id, self.scope_b.workspace_id, self.request_id, self.now),
                )
        hold_id = f"legal-hold-{secrets.token_hex(6)}"
        with self.store._transaction(self.scope_a) as connection:
            connection.execute(
                "INSERT INTO legal_holds "
                "(tenant_id,workspace_id,hold_id,source_id,actor_id,state,version,policy_version,"
                "idempotency_key,request_fingerprint,trace_id,created_at) "
                "VALUES (%s,%s,%s,%s,%s,'active',1,'policy-retention-v1','idem-hold',%s,%s,%s)",
                (self.scope_a.tenant_id, self.scope_a.workspace_id, hold_id, self.source_id,
                 self.scope_a.actor_id, "b" * 64, f"trace-{hold_id}", self.now),
            )
        with self.assertRaises(CloudDatabaseError):
            with self.store._transaction(self.scope_a) as connection:
                connection.execute("SELECT set_config('app.retention_transition','allowed',true)")
                connection.execute(
                    "UPDATE deletion_requests SET state='cleanup_pending',version=2,updated_at=%s "
                    "WHERE request_id=%s", (self.now, self.request_id),
                )
        lineage_id = f"lineage-{secrets.token_hex(6)}"
        with self.store._transaction(self.scope_a) as connection:
            connection.execute(
                "INSERT INTO retention_lineage "
                "(tenant_id,workspace_id,lineage_id,request_id,actor_id,action,target_id,"
                "policy_version,trace_id,previous_hash,event_hash,occurred_at,retain_until) "
                "VALUES (%s,%s,%s,%s,%s,'deletion.requested',%s,'policy-retention-v1',%s,%s,%s,%s,%s)",
                (self.scope_a.tenant_id, self.scope_a.workspace_id,
                 lineage_id, self.request_id, self.scope_a.actor_id,
                 self.source_id, f"trace-{self.request_id}", "0" * 64, "c" * 64,
                 self.now, self.now + timedelta(days=365)),
            )
        with self.assertRaises(CloudDatabaseError):
            with self.store._transaction(self.scope_a) as connection:
                connection.execute(
                    "DELETE FROM retention_lineage WHERE lineage_id=%s", (lineage_id,)
                )


if __name__ == "__main__":
    unittest.main()
