from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "services" / "api" / "migrations" / "versions"
CLOUD_FOUNDATION = MIGRATIONS / "0001_cloud_foundation.py"
DATA_CANON = MIGRATIONS / "0003_data_canon_lineage.py"

EXPECTED_ENTITY_TABLES = {
    "workspace_policies", "step_up_authorizations", "access_decisions",
    "sources", "source_versions", "processing_runs", "understanding_results",
    "extraction_evidence", "transcription_runs", "transcript_versions",
    "transcript_segments", "evidence_spans", "index_versions",
    "knowledge_scopes", "weight_profiles", "scope_snapshots", "conflict_records",
    "ruleset_references", "ruleset_version_snapshots", "ruleset_bindings",
    "rule_evaluations", "provider_profiles", "runtime_nodes", "model_artifacts",
    "model_installations", "model_deployments", "role_bindings",
    "routing_policy_versions", "routing_decisions", "model_attempts",
    "conversations", "messages", "runs", "run_steps", "run_snapshots",
    "run_results", "citations", "generation_requests",
    "generation_settings_snapshots", "studio_outputs", "output_versions",
    "evidence_references", "review_requests", "approval_requests", "approvals",
    "deliveries", "knowledge_registrations", "connectors", "external_references",
    "egress_decisions", "audit_events", "notifications",
}


class SqlCapture:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


def load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load migration: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def emitted_upgrade(module: ModuleType) -> tuple[str, ...]:
    capture = SqlCapture()
    module.op = capture
    module.upgrade()
    return tuple(capture.statements)


class DataCanonMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cloud = load_migration(CLOUD_FOUNDATION, "cloud_foundation_contract")
        cls.canon = load_migration(DATA_CANON, "data_canon_contract")
        cls.cloud_sql = emitted_upgrade(cls.cloud)
        cls.canon_sql = emitted_upgrade(cls.canon)

    def test_migrations_create_every_design_entity_with_constrained_schema(self) -> None:
        actual_tables = set(self.canon.ENTITY_TABLES) | {"audit_events", "notifications"}
        self.assertEqual(actual_tables, EXPECTED_ENTITY_TABLES)

        for table in self.canon.ENTITY_TABLES:
            with self.subTest(table=table):
                statement = next(
                    (sql for sql in self.canon_sql if f"CREATE TABLE {table} (" in sql),
                    None,
                )
                self.assertIsNotNone(statement)
                assert statement is not None
                for token in (
                    "tenant_id text NOT NULL",
                    "workspace_id text NOT NULL",
                    "record_id text NOT NULL",
                    "canonical_json jsonb NOT NULL",
                    "digest_sha256 text NOT NULL",
                    "PRIMARY KEY (tenant_id, workspace_id, record_id)",
                    "UNIQUE (tenant_id, workspace_id, aggregate_id, version)",
                ):
                    self.assertIn(token, statement)

        cloud_sql = "\n".join(self.cloud_sql)
        for table in ("audit_events", "notifications"):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE {table} (", cloud_sql)
                self.assertIn(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY", cloud_sql)
                self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", cloud_sql)

    def test_upgrade_emits_rls_immutability_transition_and_lineage_contracts(self) -> None:
        self.assertEqual(self.canon.revision, "0003")
        self.assertEqual(self.canon.down_revision, "0002")
        sql = "\n".join(self.canon_sql)

        for table in self.canon.ENTITY_TABLES:
            with self.subTest(table=table):
                self.assertIn(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY", sql)
                self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", sql)

        for token in (
            "CANON_IMMUTABLE_MUTATION",
            "CANON_DIGEST_MISMATCH",
            "CANON_PREVIOUS_VERSION_INVALID",
            "CANON_TRANSITION_INVALID",
            "CANON_VERSION_CONFLICT",
            "canon_state_transitions",
            "canon_transition_attempts",
            "transition_canon_state",
            "CANON_ATTEMPT_ID_REUSED",
            "source_versions",
            "run_snapshots",
            "model_attempts",
            "output_versions",
            "knowledge_registrations",
        ):
            self.assertIn(token, sql)
        self.assertIn(("confirmed", "configuring"), self.canon.TRANSITIONS["GenerationRequest"])


if __name__ == "__main__":
    unittest.main()
