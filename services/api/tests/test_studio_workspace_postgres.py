from __future__ import annotations

import unittest
import os
import hashlib
import json
from datetime import datetime, timezone

import psycopg

from daon_user_api.studio_workspace import StudioContext, StudioGenerationRequest
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
from daon_user_api.object_queue import StagedObject, StoredObject


class Result:
    def __init__(self, row=None, rows=()): self.row, self.rows = row, rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Connection:
    def __init__(self): self.statements = []
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM workspace_policies" in sql:
            common = {"active": True, "current": True, "workspace_id": "workspace-1", "version": 3}
            deny = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            return Result(({**common, "data_area": "cloud_sync", "authority_policy": "workspace_admin"}, {**common, "ruleset_version_id": "ruleset-v3"}, {**common, "profile": "trusted-source-v2"}, {**common, "scope": "workspace"}, {**common, "decision": "deny_external", "organization_policy_version_id": "org-policy-1", "organization_binding_id": "org-binding-1", "workspace_policy_version_id": "ws-policy-1", "workspace_binding_id": "ws-binding-1", "organization_policy": deny, "workspace_policy": deny}))
        if "FROM runs r JOIN egress_decisions" in sql:
            effective = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            frozen_base = {"organization_policy_version_id": "org-policy-1", "organization_binding_id": "org-binding-1", "workspace_policy_version_id": "ws-policy-1", "workspace_binding_id": "ws-binding-1", **effective}
            frozen = {**frozen_base, "fingerprint": "sha256:" + hashlib.sha256(json.dumps(frozen_base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            return Result(({"frozen_routing_context": frozen, "source_id": "source-1", "source_version_id": "source-version-1"}, "egress-1", {"run_id": "run-1", "frozen_routing_context": frozen}, "routing-1", {"run_id": "run-1", "egress_decision_id": "egress-1"}))
        if "FROM idempotency_records" in sql: return Result()
        if "FROM runs r" in sql: return Result(({"answer": "근거 답변", "insufficient": False}, 1, True))
        if "FROM citations" in sql: return Result(rows=(("citation-1", "source-version-1", "span-1", {"page": 2}),))
        if "transition_canon_state" in sql: return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class Transaction:
    def __init__(self, connection): self.connection = connection
    def __enter__(self): return self.connection
    def __exit__(self, *_args): return False


class Cloud:
    def __init__(self): self.connection = Connection()
    def _transaction(self, _context): return Transaction(self.connection)


class ExportConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM output_versions ov" in sql:
            return Result(({
                "output_type": "comparison_table", "purpose": "비교표", "source_version_ids": ["source-version-1"],
                "content": {"rows": [
                    {"key": "A", "baseline": "1", "current": "1", "state": "same", "evidence": ["citation-1", "citation-2"]},
                    {"key": "B", "baseline": "1", "current": "2", "state": "changed", "evidence": ["citation-3", "citation-4"]},
                ]},
            }, datetime(2026, 8, 13, tzinfo=timezone.utc), 4, "approved"))
        return super().execute(sql, params)


class KnowledgeRegistrationConnection(Connection):
    def __init__(self):
        super().__init__(); self.source_version_inserted = False

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM idempotency_records" in sql: return Result()
        if "SELECT state,version FROM output_versions" in sql: return Result(("approved", 2))
        if "SELECT canonical_json FROM output_versions" in sql:
            return Result(({"source_version_ids": ["source-version-1"]},))
        if "FROM source_versions" in sql and "derived_output_version_id" in sql: return Result()
        if sql.startswith("INSERT INTO source_versions"):
            self.source_version_inserted = True
        if sql.startswith("INSERT INTO knowledge_registrations") and not self.source_version_inserted:
            raise AssertionError("registered_source_version_id FK target must exist before registration")
        if "transition_canon_state" in sql: return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class RevisionConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM workspace_policies" in sql:
            return super().execute(sql, params)
        if "FROM runs r JOIN egress_decisions" in sql:
            return super().execute(sql, params)
        if "FROM runs r" in sql:
            return Result(({"answer": "새 생성 답변", "insufficient": False}, 1, True))
        if "FROM idempotency_records" in sql: return Result()
        if "FROM output_versions WHERE" in sql:
            return Result(("aggregate-1", 4, "settings-old", {"purpose": "기존", "audience": "기존 독자", "output_type": "evidence_report", "source_id": "source-1", "source_version_ids": ["source-version-1"], "run_id": "run-1", "run_result_id": "result-1", "ruleset_version_id": "ruleset-v3", "length": "short", "structure": "summary", "output_format": "pdf", "review_condition": "review_required", "content": {"body": "기존"}}, "revision_requested"))
        if "FROM run_results" in sql: return Result(({"answer": "새 생성 답변", "insufficient": False},))
        if "FROM citations" in sql: return Result(rows=(("citation-1", "source-version-1", "span-1", {"page": 7}),))
        if "transition_canon_state" in sql: return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class PolicyConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM workspace_policies" in sql:
            common = {"active": True, "current": True, "workspace_id": "workspace-1", "version": 3}
            deny = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            return Result(({**common, "data_area": "cloud_sync", "authority_policy": "workspace_admin"}, {**common, "ruleset_version_id": "ruleset-v3"}, {**common, "profile": "trusted-source-v2"}, {**common, "scope": "workspace"}, {**common, "decision": "deny_external", "organization_policy_version_id": "org-policy-1", "organization_binding_id": "org-binding-1", "workspace_policy_version_id": "ws-policy-1", "workspace_binding_id": "ws-binding-1", "organization_policy": deny, "workspace_policy": deny}))
        return Result()


class ObjectStorage:
    def __init__(self): self.content = b""; self.final_key = ""
    def put_staged(self, key, content, content_type, digest):
        self.content = content
        return StagedObject(key, digest, len(content), content_type, "etag-stage", None)
    def promote(self, staged, final_key, **_kwargs):
        self.final_key = final_key
        return StoredObject(final_key, staged.digest_sha256, staged.byte_size, staged.content_type, "etag", None)
    def get(self, key):
        assert key == self.final_key
        return self.content


class StudioWorkspacePostgresContractTests(unittest.TestCase):
    def request(self):
        return StudioGenerationRequest(
            output_type="evidence_report", source_id="source-1", source_version_ids=("source-version-1",),
            run_id="run-1", run_result_id="result-1", purpose="목적", audience="독자", ruleset_version_id="ruleset-v3",
            length="short", structure="summary", output_format="pdf", review_condition="review_required",
        )

    def test_generation_transaction_declares_all_canon_lineage_and_idempotency(self) -> None:
        sql = PostgresStudioWorkspaceRepository.generation_contract_sql()
        for token in (
            "generation_settings_snapshots", "generation_requests", "studio_outputs", "output_versions",
            "evidence_references", "audit_events", "idempotency_records", "tenant_id", "workspace_id",
        ):
            self.assertIn(token, sql)
        self.assertNotIn("UPDATE output_versions", sql.upper())

    def test_repository_rejects_workspace_mismatch_before_database_write(self) -> None:
        repository = PostgresStudioWorkspaceRepository(None)
        with self.assertRaisesRegex(Exception, "STUDIO_DATABASE_UNAVAILABLE"):
            repository.create_generation(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"), self.request(),
                "generation-key-0001",
            )

    def test_generation_writes_complete_canon_transaction_and_grounded_content(self) -> None:
        cloud = Cloud()
        output, replayed = PostgresStudioWorkspaceRepository(cloud).create_generation(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
            self.request(), "generation-key-0001",
        )
        self.assertFalse(replayed)
        self.assertEqual(output["output_type"], "evidence_report")
        self.assertEqual("근거 답변", output["content"]["body"])
        sql = "\n".join(item[0] for item in cloud.connection.statements)
        for table in ("generation_settings_snapshots", "generation_requests", "studio_outputs", "output_versions", "evidence_references", "audit_events", "idempotency_records"):
            self.assertIn(f"INSERT INTO {table}", sql)
        self.assertIn("server_policy_projection", repr(cloud.connection.statements))
        self.assertIn("originating_run", repr(cloud.connection.statements))

    def test_generation_rejects_missing_originating_run_decisions_before_writes(self) -> None:
        cloud = Cloud()
        original = cloud.connection.execute
        def execute(sql, params=()):
            if "FROM runs r JOIN egress_decisions" in sql:
                return Result()
            return original(sql, params)
        cloud.connection.execute = execute
        with self.assertRaisesRegex(Exception, "ORIGINATING_RUN_POLICY_UNAVAILABLE"):
            PostgresStudioWorkspaceRepository(cloud).create_generation(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
                self.request(), "generation-key-0001",
            )
        self.assertFalse(any(sql.startswith("INSERT INTO generation_settings_snapshots") for sql, _ in cloud.connection.statements))

    def test_approved_export_is_persisted_and_checksum_verified_in_object_storage(self) -> None:
        cloud = Cloud(); cloud.connection = ExportConnection(); storage = ObjectStorage()
        exported = PostgresStudioWorkspaceRepository(cloud, storage).export_output(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
            "output-1", "version-1", "xlsx",
        )
        self.assertTrue(exported.content.startswith(b"PK"))
        self.assertIn("/output/", storage.final_key)
        self.assertEqual(exported.checksum_sha256, __import__("hashlib").sha256(storage.content).hexdigest())

    def test_knowledge_registration_creates_source_version_before_fk_reference(self) -> None:
        cloud = Cloud(); cloud.connection = KnowledgeRegistrationConnection()
        result, replayed = PostgresStudioWorkspaceRepository(cloud).record_action(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
            "knowledge_registration", {"output_version_id": "version-1", "explicit": True},
            "knowledge-key-0001",
        )
        self.assertFalse(replayed)
        self.assertTrue(result["searchable"])

    def test_ai_and_settings_revisions_create_new_request_snapshot_and_resubmit_rejection(self) -> None:
        for revision_type in ("ai_regeneration", "settings_change"):
            cloud = Cloud(); cloud.connection = RevisionConnection()
            result, replayed = PostgresStudioWorkspaceRepository(cloud).create_version(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"), "output-1",
                {"previous_version_id": "version-1", "revision_type": revision_type, "change_reason": "재작업", "content": "새 내용", **({"settings": {"purpose": "새 목적", "audience": "새 독자", "source_version_ids": ["source-version-1"], "ruleset_version_id": "ruleset-v3", "length": "long", "structure": "detail", "output_format": "docx", "review_condition": "review_required"}} if revision_type == "settings_change" else {})},
                f"revision-key-{revision_type}",
            )
            self.assertFalse(replayed)
            sql = "\n".join(statement for statement, _ in cloud.connection.statements)
            self.assertIn("INSERT INTO generation_settings_snapshots", sql)
            self.assertIn("INSERT INTO generation_requests", sql)
            self.assertIn("INSERT INTO evidence_references", sql)
            self.assertIn("FROM runs r", sql)
            self.assertTrue(result["resubmission_of_rejected_version"])
            self.assertEqual(result["content"]["body"], "새 생성 답변")
            if revision_type == "settings_change": self.assertIn("새 목적", repr(cloud.connection.statements))

    def test_policy_projection_returns_six_server_validated_locks(self) -> None:
        projection = PostgresStudioWorkspaceRepository._policy_projection(
            PolicyConnection(), StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
        )
        locks = {lock["field"]: lock["value"] for lock in projection["locks"]}
        self.assertEqual(set(locks), {"rulesetVersionId", "reviewCondition", "authorityPolicy", "weightProfile", "dataArea", "egressPolicy"})
        self.assertEqual(locks["rulesetVersionId"], "ruleset-v3")

    def test_policy_projection_fails_closed_for_missing_inactive_stale_or_wrong_scope(self) -> None:
        context = StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1")
        for index, patch in ((0, None), (1, {"active": False}), (2, {"current": False}), (3, {"workspace_id": "workspace-other"}), (4, {"workspace_policy_version_id": ""})):
            connection = PolicyConnection(); original = connection.execute
            def execute(sql, params=(), *, index=index, patch=patch):
                result = original(sql, params)
                if "FROM workspace_policies" not in sql: return result
                values = list(result.row)
                if patch is None: values[index] = None
                else: values[index] = {**values[index], **patch}
                return Result(tuple(values))
            connection.execute = execute
            with self.assertRaisesRegex(Exception, "POLICY_PROJECTION_UNAVAILABLE"):
                PostgresStudioWorkspaceRepository._policy_projection(connection, context, run_id="run-1")

        for index, field in ((0, "data_area"), (0, "authority_policy"), (1, "ruleset_version_id"), (2, "profile"), (3, "scope")):
            connection = PolicyConnection(); original = connection.execute
            def execute(sql, params=(), *, index=index, field=field):
                result = original(sql, params)
                if "FROM workspace_policies" not in sql: return result
                values = list(result.row); values[index] = {**values[index], field: ""}
                return Result(tuple(values))
            connection.execute = execute
            with self.assertRaisesRegex(Exception, "POLICY_PROJECTION_UNAVAILABLE"):
                PostgresStudioWorkspaceRepository._policy_projection(connection, context, run_id="run-1")


@unittest.skipUnless(os.environ.get("DAON_STUDIO_TEST_DSN"), "dedicated PostgreSQL test DSN required")
class StudioWorkspaceRealPostgresIntegrationTests(unittest.TestCase):
    dsn = os.environ.get("DAON_STUDIO_TEST_DSN", "")

    @staticmethod
    def _canonical(connection, table, record_id, payload, *, state=None, extra=()):
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        columns = ["tenant_id", "workspace_id", "record_id", "aggregate_id", "version", "schema_version", "canonical_json", "canonical_text", "digest_sha256", "created_by", "trace_id"]
        values = ["tenant-it", "workspace-it", record_id, record_id, 1, 1, psycopg.types.json.Jsonb(payload), text, hashlib.sha256(text.encode()).hexdigest(), "actor-it", "trace-it"]
        if state is not None:
            columns.append("state"); values.append(state)
        for column, value in extra:
            columns.append(column); values.append(value)
        connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(values))})", values)

    def setUp(self):
        with psycopg.connect(self.dsn) as connection:
            connection.execute("INSERT INTO tenants (tenant_id,display_name) VALUES ('tenant-it','IT tenant'),('tenant-other','Other tenant')")
            connection.execute("INSERT INTO workspaces (tenant_id,workspace_id,display_name) VALUES ('tenant-it','workspace-it','IT workspace'),('tenant-other','workspace-other','Other workspace')")
            connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
            connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
            connection.execute("SELECT set_config('app.actor_id','actor-it',true)")
            self._canonical(connection, "generation_settings_snapshots", "settings-it", {"purpose": "통합"})
            self._canonical(connection, "generation_requests", "generation-it", {"purpose": "통합"}, state="configuring", extra=(("generation_settings_snapshot_id", "settings-it"),))
            connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-it',1,'confirmed','transition-generation-confirmed','IT','trace-it','policy-it')")
            connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-it',2,'submitted','transition-generation-submitted','IT','trace-it','policy-it')")
            self._canonical(connection, "studio_outputs", "output-it", {"title": "통합 산출물"}, extra=(("generation_request_id", "generation-it"),))
            self._canonical(connection, "sources", "source-original", {"kind": "upload"}, state="registered")
            source_version = 1
            for target in ("security_check", "processing", "indexing", "ready"):
                connection.execute("SELECT * FROM transition_canon_state('Source','source-original',%s,%s,%s,'IT','trace-it','policy-it')", (source_version, target, f"transition-source-{target}"))
                source_version += 1
            self._canonical(connection, "source_versions", "source-version-original", {"kind": "upload"}, extra=(("source_id", "source-original"),))
            self._canonical(connection, "output_versions", "output-version-it", {"source_version_ids": ["source-version-original"]}, state="generating", extra=(("studio_output_id", "output-it"), ("generation_settings_snapshot_id", "settings-it")))
            output_version = 1
            for target in ("draft", "review_requested", "in_review", "approved"):
                connection.execute("SELECT * FROM transition_canon_state('OutputVersion','output-version-it',%s,%s,%s,'IT','trace-it','policy-it')", (output_version, target, f"transition-output-{target}"))
                output_version += 1

    def test_rls_fk_transaction_and_registration_order(self):
        with psycopg.connect(self.dsn) as connection:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    self._canonical(connection, "knowledge_registrations", "bad-registration", {}, state="requested", extra=(("output_version_id", "output-version-it"), ("registered_source_version_id", "missing-source-version")))
            with connection.transaction():
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
                connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
                visible = connection.execute("SELECT workspace_id FROM workspaces ORDER BY workspace_id").fetchall()
                self.assertEqual(visible, [("workspace-it",)])
            with self.assertRaises(RuntimeError):
                with connection.transaction():
                    connection.execute("INSERT INTO tenants (tenant_id,display_name) VALUES ('rollback-it','Rollback')")
                    raise RuntimeError("force rollback")
            self.assertIsNone(connection.execute("SELECT 1 FROM tenants WHERE tenant_id='rollback-it'").fetchone())

        from daon_user_api.cloud_storage import PostgresCloudStore
        store = PostgresCloudStore(self.dsn)
        try:
            result, replayed = PostgresStudioWorkspaceRepository(store).record_action(
                StudioContext("tenant-it", "workspace-it", "actor-it", "trace-it", "policy-it"),
                "knowledge_registration", {"output_version_id": "output-version-it", "explicit": True},
                "knowledge-real-pg-0001",
            )
            self.assertFalse(replayed)
            self.assertTrue(result["searchable"])
            with psycopg.connect(self.dsn) as connection:
                self.assertIsNotNone(connection.execute("SELECT 1 FROM source_versions WHERE record_id=%s", (result["registered_source_version_id"],)).fetchone())
                self.assertIsNotNone(connection.execute("SELECT 1 FROM knowledge_registrations WHERE registered_source_version_id=%s AND state='registered'", (result["registered_source_version_id"],)).fetchone())
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
