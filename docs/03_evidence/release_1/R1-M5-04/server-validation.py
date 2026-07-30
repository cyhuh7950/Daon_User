from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from pathlib import Path
from typing import Iterable

import psycopg

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.data_canon import CanonicalContext, _TRANSITIONS


ROOT = Path(__file__).resolve().parents[4]
MANIFEST = json.loads(
    (ROOT / "docs/03_architecture/data_canon_manifest.json").read_text(encoding="utf-8")
)
OWNER_DSN = os.environ["DAON_DB_MIGRATION_DSN"]
APP_DSN = os.environ["DAON_TEST_POSTGRES_DSN"]
DIGEST = hashlib.sha256(b"{}").hexdigest()

CONTEXT_A = CanonicalContext("tenant-canon-a", "workspace-canon-a", "actor-canon-a", "canon.write", "trace-canon-a")
CONTEXT_A2 = CanonicalContext("tenant-canon-a", "workspace-canon-a2", "actor-canon-a2", "canon.write", "trace-canon-a2")
CONTEXT_B = CanonicalContext("tenant-canon-b", "workspace-canon-b", "actor-canon-b", "canon.write", "trace-canon-b")

STATE_TABLES = {
    "Source": ("sources", "registered", {}),
    "ProcessingRun": ("processing_runs", "accepted", {"source_version_id": "source-version-base", "modality": "document", "trigger_type": "initial"}),
    "Run": ("runs", "accepted", {}),
    "GenerationRequest": ("generation_requests", "configuring", {}),
    "OutputVersion": ("output_versions", "generating", {"studio_output_id": "studio-output-base", "generation_settings_snapshot_id": "settings-base"}),
    "ApprovalRequest": ("approval_requests", "pending", {"output_version_id": "output-version-base"}),
    "KnowledgeRegistration": ("knowledge_registrations", "requested", {"output_version_id": "output-version-base"}),
}


def check(condition: bool, code: str) -> None:
    if not condition:
        raise AssertionError(code)


def set_scope(connection: psycopg.Connection[tuple[object, ...]], context: CanonicalContext) -> None:
    for name, value in (
        ("app.tenant_id", context.tenant_id), ("app.workspace_id", context.workspace_id),
        ("app.actor_id", context.actor_id), ("app.capability", context.capability),
        ("app.trace_id", context.trace_id),
    ):
        connection.execute("SELECT set_config(%s, %s, true)", (name, value))


def insert_canon(
    connection: psycopg.Connection[tuple[object, ...]], context: CanonicalContext,
    table: str, record_id: str, extra: dict[str, object] | None = None,
) -> None:
    values: dict[str, object] = {
        "tenant_id": context.tenant_id, "workspace_id": context.workspace_id,
        "record_id": record_id, "aggregate_id": record_id, "version": 1,
        "schema_version": 1, "canonical_json": "{}", "canonical_text": "{}",
        "digest_sha256": DIGEST, "created_by": context.actor_id, "trace_id": context.trace_id,
    }
    values.update(extra or {})
    columns = list(values)
    placeholders = ["%s::jsonb" if column == "canonical_json" else "%s" for column in columns]
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(placeholders)})",
        tuple(values[column] for column in columns),
    )


def shortest_path(
    edges: Iterable[tuple[str, str]], initial: str, target: str,
) -> list[tuple[str, str]]:
    queue: deque[tuple[str, list[tuple[str, str]]]] = deque([(initial, [])])
    seen = {initial}
    while queue:
        state, path = queue.popleft()
        if state == target:
            return path
        for source, destination in sorted(edges):
            if source == state and destination not in seen:
                seen.add(destination)
                queue.append((destination, [*path, (source, destination)]))
    raise AssertionError(f"CANON_UNREACHABLE_STATE:{target}")


def transition(
    connection: psycopg.Connection[tuple[object, ...]], entity: str, record_id: str,
    version: int, target: str, transition_id: str,
) -> int:
    row = connection.execute(
        "SELECT state, version FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
        (entity, record_id, version, target, transition_id, "SERVER_VALIDATION", transition_id, "policy-v1"),
    ).fetchone()
    check(row is not None and row[0] == target and row[1] == version + 1, "CANON_TRANSITION_RESULT_INVALID")
    return version + 1


def seed_foundation() -> None:
    cloud = PostgresCloudStore(APP_DSN)
    try:
        for context in (CONTEXT_A, CONTEXT_A2, CONTEXT_B):
            cloud.seed_scope(context.cloud_context())
    finally:
        cloud.close()
    with psycopg.connect(APP_DSN) as connection, connection.transaction():
        set_scope(connection, CONTEXT_A)
        insert_canon(connection, CONTEXT_A, "sources", "source-base")
        insert_canon(connection, CONTEXT_A, "source_versions", "source-version-base", {"source_id": "source-base"})
        insert_canon(connection, CONTEXT_A, "generation_settings_snapshots", "settings-base")
        insert_canon(connection, CONTEXT_A, "generation_requests", "generation-request-base")
        insert_canon(connection, CONTEXT_A, "studio_outputs", "studio-output-base", {"generation_request_id": "generation-request-base"})
        insert_canon(connection, CONTEXT_A, "output_versions", "output-version-base", {"studio_output_id": "studio-output-base", "generation_settings_snapshot_id": "settings-base"})


def verify_schema() -> dict[str, int]:
    mapped_tables = {
        mapping["table"] for mapping in MANIFEST["entity_mappings"].values()
        if mapping["table"] not in {"audit_events", "notifications"}
    }
    with psycopg.connect(OWNER_DSN) as connection:
        version = connection.execute("SHOW server_version").fetchone()
        check(version is not None and str(version[0]).startswith("18.4"), "POSTGRES_VERSION_INVALID")
        check(connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0003",), "MIGRATION_REVISION_INVALID")
        rls = connection.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(%s)",
            (list(mapped_tables | {"canon_state_transitions"}),),
        ).fetchall()
        check(len(rls) == len(mapped_tables) + 1, "CANON_TABLE_MISSING")
        check(all(row[1] and row[2] for row in rls), "CANON_RLS_NOT_FORCED")
        for mapping in MANIFEST["entity_mappings"].values():
            columns = {
                row[0] for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
                    (mapping["table"],),
                )
            }
            check(set(mapping["columns"].values()) <= columns, f"MANIFEST_COLUMN_MISSING:{mapping['table']}")
        fk_definitions = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE contype='f' AND conrelid::regclass::text = ANY(%s)",
            (list(mapped_tables),),
        ).fetchall()
        check(len(fk_definitions) >= 45, "CANON_FK_COVERAGE_LOW")
        check(all("(tenant_id, workspace_id" in str(row[0]) for row in fk_definitions), "CANON_SCOPE_FK_INCOMPLETE")
        expected_rules = sum(len(edges) for edges in _TRANSITIONS.values())
        check(connection.execute("SELECT count(*) FROM canon_transition_rules").fetchone() == (expected_rules,), "CANON_TRANSITION_RULE_DRIFT")
    return {"mapped_entities": len(MANIFEST["entity_mappings"]), "scoped_tables": len(rls), "foreign_keys": len(fk_definitions), "transition_rules": expected_rules}


def verify_transitions() -> dict[str, int]:
    executed = 0
    with psycopg.connect(APP_DSN) as connection:
        for entity, edges in _TRANSITIONS.items():
            table, initial, extra = STATE_TABLES[entity]
            for edge_index, (source, target) in enumerate(sorted(edges)):
                record_id = f"transition-{entity.lower()}-{edge_index}"
                with connection.transaction():
                    set_scope(connection, CONTEXT_A)
                    insert_canon(connection, CONTEXT_A, table, record_id, extra)
                    version = 1
                    for path_index, (_, step_target) in enumerate(shortest_path(edges, initial, source)):
                        version = transition(connection, entity, record_id, version, step_target, f"path-{entity.lower()}-{edge_index}-{path_index}")
                    transition(connection, entity, record_id, version, target, f"edge-{entity.lower()}-{edge_index}")
                    executed += 1
        with connection.transaction():
            set_scope(connection, CONTEXT_A)
            insert_canon(connection, CONTEXT_A, "sources", "illegal-transition-source")
            try:
                transition(connection, "Source", "illegal-transition-source", 1, "ready", "illegal-transition")
            except psycopg.errors.CheckViolation as error:
                check("CANON_TRANSITION_INVALID" in str(error), "ILLEGAL_TRANSITION_ERROR_UNSTABLE")
            else:
                raise AssertionError("ILLEGAL_TRANSITION_ACCEPTED")
        with connection.transaction():
            set_scope(connection, CONTEXT_A)
            insert_canon(connection, CONTEXT_A, "sources", "stale-transition-source")
            transition(connection, "Source", "stale-transition-source", 1, "security_check", "fresh-transition")
            try:
                transition(connection, "Source", "stale-transition-source", 1, "processing", "stale-transition")
            except psycopg.errors.SerializationFailure as error:
                check("CANON_VERSION_CONFLICT" in str(error), "VERSION_CONFLICT_ERROR_UNSTABLE")
            else:
                raise AssertionError("STALE_TRANSITION_ACCEPTED")
    return {"allowed_edges_executed": executed, "illegal_edges_rejected": 1, "lost_updates_rejected": 1}


def verify_lineage_immutability_and_scope() -> dict[str, int]:
    with psycopg.connect(APP_DSN) as connection:
        with connection.transaction():
            set_scope(connection, CONTEXT_A)
            insert_canon(connection, CONTEXT_A, "source_versions", "source-version-2", {"aggregate_id": "source-version-base", "version": 2, "previous_version_id": "source-version-base", "source_id": "source-base"})
            insert_canon(connection, CONTEXT_A, "processing_runs", "processing-document", {"source_version_id": "source-version-2", "modality": "document", "trigger_type": "initial"})
            insert_canon(connection, CONTEXT_A, "processing_runs", "processing-audio", {"source_version_id": "source-version-2", "modality": "audio", "trigger_type": "manual_request"})
            insert_canon(connection, CONTEXT_A, "processing_runs", "processing-audio-retry", {"source_version_id": "source-version-2", "retry_of_processing_run_id": "processing-audio", "modality": "audio", "trigger_type": "retry"})
            insert_canon(connection, CONTEXT_A, "knowledge_scopes", "knowledge-scope")
            insert_canon(connection, CONTEXT_A, "scope_snapshots", "scope-snapshot", {"knowledge_scope_id": "knowledge-scope"})
            insert_canon(connection, CONTEXT_A, "routing_policy_versions", "routing-policy")
            insert_canon(connection, CONTEXT_A, "runs", "run-lineage")
            snapshot_payload = {
                "source_version_ids": ["source-version-2"], "knowledge_scope_id": "knowledge-scope", "authority": {},
                "weights_requested": {}, "weights_effective": {}, "weight_clamps": [], "ruleset_snapshot_ids": [],
                "routing_policy_version_id": "routing-policy", "candidate_order": [], "data_area": "cloud_sync",
                "data_classification": "internal", "egress_decision_id": None, "user_policy_version": "user-v1",
                "organization_policy_version": "org-v1", "cost_limit": 0, "currency": "KRW",
                "prompt_version": "prompt-v1", "tool_version": "tool-v1",
            }
            snapshot_text = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"))
            insert_canon(connection, CONTEXT_A, "run_snapshots", "run-snapshot", {"run_id": "run-lineage", "scope_snapshot_id": "scope-snapshot", "routing_policy_version_id": "routing-policy", "canonical_json": snapshot_text, "canonical_text": snapshot_text, "digest_sha256": hashlib.sha256(snapshot_text.encode()).hexdigest()})
            insert_canon(connection, CONTEXT_A, "run_results", "run-result", {"run_id": "run-lineage"})
            insert_canon(connection, CONTEXT_A, "evidence_spans", "evidence-span", {"source_version_id": "source-version-2"})
            insert_canon(connection, CONTEXT_A, "citations", "citation", {"run_result_id": "run-result", "source_version_id": "source-version-2", "evidence_span_id": "evidence-span"})
            check(connection.execute("SELECT source_version_id, evidence_span_id FROM citations WHERE record_id='citation'").fetchone() == ("source-version-2", "evidence-span"), "CITATION_LINEAGE_DRIFT")
        for statement in (
            "UPDATE source_versions SET schema_version=2 WHERE record_id='source-version-2'",
            "DELETE FROM run_snapshots WHERE record_id='run-snapshot'",
        ):
            try:
                with connection.transaction():
                    set_scope(connection, CONTEXT_A)
                    connection.execute(statement)
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError("IMMUTABLE_MUTATION_ACCEPTED")
        try:
            with connection.transaction():
                set_scope(connection, CONTEXT_A)
                insert_canon(connection, CONTEXT_A, "source_versions", "bad-digest", {"source_id": "source-base", "digest_sha256": "0" * 64})
        except psycopg.errors.InvalidParameterValue as error:
            check("CANON_DIGEST_MISMATCH" in str(error), "DIGEST_ERROR_UNSTABLE")
        else:
            raise AssertionError("BAD_DIGEST_ACCEPTED")
        with connection.transaction():
            set_scope(connection, CONTEXT_A2)
            check(connection.execute("SELECT count(*) FROM sources WHERE record_id='source-base'").fetchone() == (0,), "RLS_CROSS_WORKSPACE_VISIBLE")
        try:
            with connection.transaction():
                set_scope(connection, CONTEXT_A2)
                insert_canon(connection, CONTEXT_A2, "source_versions", "cross-scope-version", {"source_id": "source-base"})
        except psycopg.errors.ForeignKeyViolation:
            pass
        else:
            raise AssertionError("CROSS_SCOPE_FK_ACCEPTED")
        with connection.transaction():
            set_scope(connection, CONTEXT_B)
            check(connection.execute("SELECT count(*) FROM sources WHERE record_id='source-base'").fetchone() == (0,), "RLS_CROSS_TENANT_VISIBLE")
    return {"lineage_paths": 5, "immutable_mutations_rejected": 2, "invalid_digests_rejected": 1, "cross_scope_rows": 0}


def main() -> None:
    schema = verify_schema()
    seed_foundation()
    transitions = verify_transitions()
    lineage = verify_lineage_immutability_and_scope()
    print(json.dumps({"status": "pass", "schema": schema, "transitions": transitions, "lineage": lineage}, sort_keys=True))


if __name__ == "__main__":
    main()
