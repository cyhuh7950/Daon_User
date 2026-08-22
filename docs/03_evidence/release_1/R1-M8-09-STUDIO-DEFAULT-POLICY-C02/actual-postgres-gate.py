from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "services/api/src"))

from daon_user_api.studio_workspace import StudioContext
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository


DSN = os.environ.get("DAON_DB_MIGRATION_DSN", "")
TABLES = (
    "workspace_policies",
    "knowledge_scopes",
    "weight_profiles",
    "ruleset_references",
    "ruleset_version_snapshots",
    "ruleset_bindings",
)


class GateCloudStore:
    @contextmanager
    def _transaction(self, access):  # type: ignore[no-untyped-def]
        with psycopg.connect(DSN) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute("SELECT set_config('app.tenant_id',%s,true)", (access.tenant_id,))
                connection.execute("SELECT set_config('app.workspace_id',%s,true)", (access.workspace_id,))
                yield connection


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def migrate(revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(ROOT / "services/api/alembic.ini"))
    if downgrade:
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


def canonical(
    connection: psycopg.Connection,
    table: str,
    tenant_id: str,
    workspace_id: str,
    record_id: str,
    payload: dict[str, object],
    *,
    extra: tuple[tuple[str, str], ...] = (),
    created_by: str = "gate:preexisting",
    aggregate_id: str | None = None,
    version: int = 1,
    previous_version_id: str | None = None,
) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    columns = [
        "tenant_id", "workspace_id", "record_id", "aggregate_id", "version",
        "schema_version", "canonical_json", "canonical_text", "digest_sha256",
        "created_by", "trace_id",
    ]
    values: list[object] = [
        tenant_id, workspace_id, record_id, aggregate_id or record_id, version, 1, Jsonb(payload), text,
        hashlib.sha256(text.encode()).hexdigest(), created_by, "gate:trace",
    ]
    if previous_version_id is not None:
        columns.append("previous_version_id")
        values.append(previous_version_id)
    for column, value in extra:
        columns.append(column)
        values.append(value)
    connection.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(values))})",
        values,
    )


def base_payload(workspace_id: str, kind: str, *, snapshot_id: str = "") -> dict[str, object]:
    common: dict[str, object] = {
        "active": True, "current": True, "version": 1, "workspace_id": workspace_id,
    }
    variants: dict[str, dict[str, object]] = {
        "workspace_policies": {"authority_policy": "workspace_admin", "data_area": "cloud_sync"},
        "knowledge_scopes": {"scope": "workspace"},
        "weight_profiles": {"profile": "trusted-source-v2"},
        "ruleset_references": {"name": "default-review-required"},
        "ruleset_version_snapshots": {"review_condition": "review_required", "rules": []},
        "ruleset_bindings": {"review_condition": "review_required", "ruleset_version_id": snapshot_id},
    }
    return {**common, **variants[kind]}


def seed_source_version(
    connection: psycopg.Connection, tenant_id: str, workspace_id: str, suffix: str,
) -> str:
    source_id = f"gate:source:{suffix}"
    version_id = f"gate:source-version:{suffix}"
    canonical(connection, "sources", tenant_id, workspace_id, source_id, {"name": suffix})
    canonical(
        connection, "source_versions", tenant_id, workspace_id, version_id,
        {"source_id": source_id}, extra=(("source_id", source_id),),
    )
    return version_id


def clean_legacy_case(connection: psycopg.Connection, workspace_id: str) -> None:
    for table in ("weight_profiles", "knowledge_scopes", "source_versions", "sources"):
        connection.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        connection.execute(
            f"DELETE FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id=%s",
            (workspace_id,),
        )
        connection.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
    connection.execute(
        "DELETE FROM workspaces WHERE tenant_id='tenant-gate' AND workspace_id=%s",
        (workspace_id,),
    )


def seed_deny_egress(connection: psycopg.Connection, workspace_id: str) -> None:
    payload = {
        "allowed_destinations": [],
        "allowed_provider_kinds": [],
        "classification": "restricted",
        "masking_required": True,
        "max_bytes": 0,
        "mode": "deny_external",
        "redaction_required": True,
        "required_approver": "organization_admin",
    }
    canonical_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical_text.encode()).hexdigest()
    for scope_type, target_workspace, suffix in (
        ("organization", None, "organization"),
        ("workspace", workspace_id, workspace_id),
    ):
        policy_id = "gate:egress-policy:" + hashlib.md5(suffix.encode()).hexdigest()
        binding_id = "gate:egress-binding:" + hashlib.md5(suffix.encode()).hexdigest()
        connection.execute(
            "INSERT INTO egress_policy_versions "
            "(tenant_id,organization_id,workspace_id,policy_version_id,scope_type,policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id) "
            "VALUES ('tenant-gate','tenant-gate',%s,%s,%s,1,'active',%s,%s,%s,'gate:repository','gate:repository')",
            (target_workspace, policy_id, scope_type, Jsonb(payload), canonical_text, digest),
        )
        connection.execute(
            "INSERT INTO egress_policy_bindings "
            "(tenant_id,organization_id,workspace_id,binding_id,scope_type,policy_version_id,binding_version,active,current,created_by,trace_id) "
            "VALUES ('tenant-gate','tenant-gate',%s,%s,%s,%s,1,true,true,'gate:repository','gate:repository')",
            (target_workspace, binding_id, scope_type, policy_id),
        )


def main() -> None:
    require(bool(DSN), "DAON_DB_MIGRATION_DSN_REQUIRED")
    migrate("0012")

    with psycopg.connect(DSN) as connection:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('tenant-gate','Gate'),('tenant-other','Other')")
        connection.execute(
            "INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES "
            "('tenant-gate','workspace-missing','Missing'),"
            "('tenant-gate','workspace-partial','Partial'),"
            "('tenant-gate','workspace-full','Full'),"
            "('tenant-gate','workspace-legacy','Legacy'),"
            "('tenant-gate','workspace-conflict','Conflict'),"
            "('tenant-other','workspace-other','Other')"
        )
        legacy_source_id = "gate:legacy-source"
        legacy_source_version_id = "gate:legacy-source-version"
        legacy_scope_id = "scope-" + hashlib.sha256(legacy_source_version_id.encode()).hexdigest()[:32]
        canonical(
            connection, "sources", "tenant-gate", "workspace-legacy",
            legacy_source_id, {"name": "legacy gate source"},
        )
        canonical(
            connection, "source_versions", "tenant-gate", "workspace-legacy",
            legacy_source_version_id, {"source_id": legacy_source_id},
            extra=(("source_id", legacy_source_id),),
        )
        canonical(
            connection, "knowledge_scopes", "tenant-gate", "workspace-legacy",
            legacy_scope_id,
            {"mode": "single_source", "source_version_ids": [legacy_source_version_id]},
        )
        canonical(
            connection, "knowledge_scopes", "tenant-gate", "workspace-partial",
            "preexisting:scope", base_payload("workspace-partial", "knowledge_scopes"),
        )
        full_ids = {
            "workspace_policies": "preexisting:full-policy",
            "knowledge_scopes": "preexisting:full-scope",
            "weight_profiles": "preexisting:full-weight",
            "ruleset_references": "preexisting:full-ruleset",
            "ruleset_version_snapshots": "preexisting:full-snapshot",
            "ruleset_bindings": "preexisting:full-binding",
        }
        canonical(connection, "workspace_policies", "tenant-gate", "workspace-full", full_ids["workspace_policies"], base_payload("workspace-full", "workspace_policies"))
        canonical(connection, "knowledge_scopes", "tenant-gate", "workspace-full", full_ids["knowledge_scopes"], base_payload("workspace-full", "knowledge_scopes"))
        canonical(connection, "weight_profiles", "tenant-gate", "workspace-full", full_ids["weight_profiles"], base_payload("workspace-full", "weight_profiles"), extra=(("knowledge_scope_id", full_ids["knowledge_scopes"]),))
        canonical(connection, "ruleset_references", "tenant-gate", "workspace-full", full_ids["ruleset_references"], base_payload("workspace-full", "ruleset_references"))
        canonical(connection, "ruleset_version_snapshots", "tenant-gate", "workspace-full", full_ids["ruleset_version_snapshots"], base_payload("workspace-full", "ruleset_version_snapshots"), extra=(("ruleset_reference_id", full_ids["ruleset_references"]),))
        canonical(connection, "ruleset_bindings", "tenant-gate", "workspace-full", full_ids["ruleset_bindings"], base_payload("workspace-full", "ruleset_bindings", snapshot_id=full_ids["ruleset_version_snapshots"]), extra=(("ruleset_reference_id", full_ids["ruleset_references"]), ("ruleset_version_snapshot_id", full_ids["ruleset_version_snapshots"])))

        conflict_suffix = hashlib.md5(b"tenant-gate|workspace-conflict").hexdigest()
        canonical(
            connection,
            "workspace_policies",
            "tenant-gate",
            "workspace-conflict",
            f"studio-default:workspace-policy:{conflict_suffix}",
            {
                **base_payload("workspace-conflict", "workspace_policies"),
                "active": False,
            },
            created_by="gate:non-owned",
        )

    try:
        migrate("0013")
    except Exception as exc:
        require("STUDIO_DEFAULT_POLICY_ID_CONFLICT" in str(exc), "deterministic conflict code")
    else:
        raise AssertionError("deterministic invalid ID conflict unexpectedly succeeded")

    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012", "conflict revision rollback")
        require(connection.execute("SELECT count(*) FROM workspace_policies WHERE workspace_id='workspace-conflict' AND created_by='gate:non-owned'").fetchone()[0] == 1, "conflict row preserved")
        connection.execute("ALTER TABLE workspace_policies DISABLE TRIGGER workspace_policies_immutable")
        connection.execute("DELETE FROM workspace_policies WHERE workspace_id='workspace-conflict' AND created_by='gate:non-owned'")
        connection.execute("ALTER TABLE workspace_policies ENABLE TRIGGER workspace_policies_immutable")

        connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES ('tenant-gate','workspace-latest-invalid','Latest invalid')")
        history_v1 = "history:workspace-policy:v1"
        canonical(
            connection, "workspace_policies", "tenant-gate", "workspace-latest-invalid",
            history_v1, base_payload("workspace-latest-invalid", "workspace_policies"),
            aggregate_id="history:workspace-policy",
        )
        canonical(
            connection, "workspace_policies", "tenant-gate", "workspace-latest-invalid",
            "history:workspace-policy:v2",
            {**base_payload("workspace-latest-invalid", "workspace_policies"), "active": False, "version": 2},
            aggregate_id="history:workspace-policy", version=2, previous_version_id=history_v1,
        )

    try:
        migrate("0013")
    except Exception as exc:
        require("STUDIO_DEFAULT_POLICY_LATEST_INVALID" in str(exc), "latest invalid code")
    else:
        raise AssertionError("inactive latest policy unexpectedly succeeded")

    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012", "latest invalid revision rollback")
        require(connection.execute("SELECT count(*) FROM workspace_policies WHERE workspace_id='workspace-latest-invalid'").fetchone()[0] == 2, "latest invalid rows preserved")
        connection.execute("ALTER TABLE workspace_policies DISABLE TRIGGER workspace_policies_immutable")
        connection.execute("DELETE FROM workspace_policies WHERE workspace_id='workspace-latest-invalid' AND version=2")
        connection.execute("ALTER TABLE workspace_policies ENABLE TRIGGER workspace_policies_immutable")
        connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES ('tenant-gate','workspace-history-tie','History tie')")
        canonical(connection, "workspace_policies", "tenant-gate", "workspace-history-tie", "tie:policy:a", base_payload("workspace-history-tie", "workspace_policies"))
        canonical(connection, "workspace_policies", "tenant-gate", "workspace-history-tie", "tie:policy:b", base_payload("workspace-history-tie", "workspace_policies"))

    try:
        migrate("0013")
    except Exception as exc:
        require("STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS" in str(exc), "history ambiguity code")
    else:
        raise AssertionError("same max version history unexpectedly succeeded")

    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012", "history tie revision rollback")
        require(connection.execute("SELECT count(*) FROM workspace_policies WHERE workspace_id='workspace-history-tie'").fetchone()[0] == 2, "history tie rows preserved")
        connection.execute("ALTER TABLE workspace_policies DISABLE TRIGGER workspace_policies_immutable")
        connection.execute("DELETE FROM workspace_policies WHERE workspace_id='workspace-history-tie' AND record_id='tie:policy:b'")
        connection.execute("ALTER TABLE workspace_policies ENABLE TRIGGER workspace_policies_immutable")

    legacy_negative_cases: tuple[tuple[str, dict[str, object], str, bool, str], ...] = (
        ("extra-key", {"mode": "single_source", "source_version_ids": [], "extra": True}, "exact", True, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("missing-key", {"mode": "single_source"}, "exact", True, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("wrong-mode", {"mode": "workspace", "source_version_ids": []}, "exact", True, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("array-many", {"mode": "single_source", "source_version_ids": []}, "exact", True, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("array-string", {"mode": "single_source", "source_version_ids": "invalid"}, "exact", True, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("wrong-record", {"mode": "single_source", "source_version_ids": []}, "wrong", True, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("missing-source", {"mode": "single_source", "source_version_ids": ["gate:missing-source-version"]}, "exact", False, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
        ("cross-workspace", {"mode": "single_source", "source_version_ids": []}, "exact", False, "STUDIO_DEFAULT_POLICY_LATEST_INVALID"),
    )
    for case_name, template, record_mode, seed_local, expected_code in legacy_negative_cases:
        workspace_id = f"workspace-legacy-invalid-{case_name}"
        with psycopg.connect(DSN) as connection:
            connection.execute(
                "INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES ('tenant-gate',%s,%s)",
                (workspace_id, case_name),
            )
            if case_name == "cross-workspace":
                source_version_id = seed_source_version(connection, "tenant-other", "workspace-other", case_name)
            elif seed_local:
                source_version_id = seed_source_version(connection, "tenant-gate", workspace_id, case_name)
            else:
                source_version_id = str(template["source_version_ids"][0])
            payload = dict(template)
            if case_name == "array-many":
                payload["source_version_ids"] = [source_version_id, "gate:second"]
            elif case_name not in {"missing-key", "array-string", "missing-source"}:
                payload["source_version_ids"] = [source_version_id]
            scope_id = "scope-" + hashlib.sha256(source_version_id.encode()).hexdigest()[:32]
            if record_mode == "wrong":
                scope_id = "scope-wrong"
            canonical(connection, "knowledge_scopes", "tenant-gate", workspace_id, scope_id, payload)
        try:
            migrate("0013")
        except Exception as exc:
            require(expected_code in str(exc), f"legacy negative code {case_name}")
        else:
            raise AssertionError(f"legacy negative unexpectedly succeeded: {case_name}")
        with psycopg.connect(DSN) as connection:
            require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012", f"legacy negative revision {case_name}")
            require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE tenant_id='tenant-gate' AND workspace_id=%s", (workspace_id,)).fetchone()[0] == 1, f"legacy negative rows {case_name}")
            clean_legacy_case(connection, workspace_id)
            if case_name == "cross-workspace":
                connection.execute("ALTER TABLE source_versions DISABLE TRIGGER USER")
                connection.execute("DELETE FROM source_versions WHERE tenant_id='tenant-other' AND workspace_id='workspace-other' AND record_id=%s", (source_version_id,))
                connection.execute("ALTER TABLE source_versions ENABLE TRIGGER USER")
                connection.execute("ALTER TABLE sources DISABLE TRIGGER USER")
                connection.execute("DELETE FROM sources WHERE tenant_id='tenant-other' AND workspace_id='workspace-other' AND record_id=%s", (f"gate:source:{case_name}",))
                connection.execute("ALTER TABLE sources ENABLE TRIGGER USER")

    collision_workspace = "workspace-legacy-compat-collision"
    with psycopg.connect(DSN) as connection:
        connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES ('tenant-gate',%s,'Collision')", (collision_workspace,))
        collision_source_version = seed_source_version(connection, "tenant-gate", collision_workspace, "compat-collision")
        collision_v1 = "scope-" + hashlib.sha256(collision_source_version.encode()).hexdigest()[:32]
        canonical(connection, "knowledge_scopes", "tenant-gate", collision_workspace, collision_v1, {"mode": "single_source", "source_version_ids": [collision_source_version]})
        collision_id = "studio-compat:knowledge-scope-v2:" + hashlib.md5(f"tenant-gate|{collision_workspace}|{collision_v1}".encode()).hexdigest()
        canonical(
            connection, "knowledge_scopes", "tenant-gate", collision_workspace, collision_id,
            {"active": True, "current": True, "mode": "single_source", "scope": "workspace", "source_version_ids": [collision_source_version], "version": 2, "workspace_id": collision_workspace},
            aggregate_id=collision_v1, version=2, previous_version_id=collision_v1,
        )
    try:
        migrate("0013")
    except Exception as exc:
        require("STUDIO_DEFAULT_POLICY_ID_CONFLICT" in str(exc), "compat collision code")
    else:
        raise AssertionError("compat collision unexpectedly succeeded")
    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012", "compat collision revision")
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE workspace_id=%s", (collision_workspace,)).fetchone()[0] == 2, "compat collision rows")
        clean_legacy_case(connection, collision_workspace)

    migrate("0013")
    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013", "revision")
        missing_count = sum(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id='workspace-missing' AND created_by='migration:0013'"
            ).fetchone()[0]
            for table in TABLES
        )
        partial_count = sum(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id='workspace-partial' AND created_by='migration:0013'"
            ).fetchone()[0]
            for table in TABLES
        )
        require(missing_count == 6, "missing backfill")
        require(partial_count == 5, "partial backfill")
        legacy_rows = connection.execute(
            "SELECT record_id,aggregate_id,version,previous_version_id,canonical_json,created_by "
            "FROM knowledge_scopes WHERE tenant_id='tenant-gate' AND workspace_id='workspace-legacy' "
            "ORDER BY version"
        ).fetchall()
        require(len(legacy_rows) == 2, "legacy v2 append")
        require(legacy_rows[0][0] == legacy_scope_id and legacy_rows[0][2] == 1, "legacy v1 preserved")
        require(
            legacy_rows[1][1] == legacy_scope_id
            and legacy_rows[1][2] == 2
            and legacy_rows[1][3] == legacy_scope_id
            and legacy_rows[1][4] == {
                "active": True,
                "current": True,
                "mode": "single_source",
                "scope": "workspace",
                "source_version_ids": [legacy_source_version_id],
                "version": 2,
                "workspace_id": "workspace-legacy",
            }
            and legacy_rows[1][5] == "migration:0013",
            "legacy v2 exact",
        )
        legacy_v2_id = legacy_rows[1][0]
        require(
            connection.execute(
                "SELECT knowledge_scope_id FROM weight_profiles "
                "WHERE tenant_id='tenant-gate' AND workspace_id='workspace-legacy'"
            ).fetchone()[0] == legacy_v2_id,
            "legacy weight v2 FK",
        )
        connection.execute("SELECT ensure_studio_workspace_defaults('tenant-gate','workspace-legacy')")
        connection.execute("SELECT ensure_studio_workspace_defaults('tenant-gate','workspace-legacy')")
        require(
            connection.execute(
                "SELECT count(*) FROM knowledge_scopes "
                "WHERE tenant_id='tenant-gate' AND workspace_id='workspace-legacy'"
            ).fetchone()[0] == 2,
            "legacy idempotency",
        )
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE record_id='preexisting:scope'").fetchone()[0] == 1, "scope preserved")
        require(sum(connection.execute(f"SELECT count(*) FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id='workspace-full' AND record_id LIKE 'preexisting:full-%'").fetchone()[0] for table in TABLES) == 6, "full config preserved")
        full_owned_before = sum(connection.execute(f"SELECT count(*) FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id='workspace-full' AND created_by='migration:0013'").fetchone()[0] for table in TABLES)
        connection.execute("SELECT ensure_studio_workspace_defaults('tenant-gate','workspace-full')")
        connection.execute("SELECT ensure_studio_workspace_defaults('tenant-gate','workspace-full')")
        full_owned_after = sum(connection.execute(f"SELECT count(*) FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id='workspace-full' AND created_by='migration:0013'").fetchone()[0] for table in TABLES)
        require((full_owned_before, full_owned_after) == (0, 0), "full config idempotency")
        require(connection.execute("SELECT knowledge_scope_id FROM weight_profiles WHERE tenant_id='tenant-gate' AND workspace_id='workspace-partial'").fetchone()[0] == "preexisting:scope", "weight scope")
        for table in TABLES:
            invalid = connection.execute(
                f"SELECT count(*) FROM {table} WHERE created_by='migration:0013' AND "
                "digest_sha256 <> encode(sha256(convert_to(canonical_text,'UTF8')),'hex')"
            ).fetchone()[0]
            require(invalid == 0, f"digest {table}")

        connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES ('tenant-gate','workspace-new','New')")
        trigger_count = sum(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id='tenant-gate' AND workspace_id='workspace-new' AND created_by='migration:0013'"
            ).fetchone()[0]
            for table in TABLES
        )
        require(trigger_count == 6, "new workspace trigger")

        record_id = connection.execute(
            "SELECT record_id FROM workspace_policies WHERE tenant_id='tenant-gate' AND workspace_id='workspace-new'"
        ).fetchone()[0]
        try:
            with connection.transaction():
                connection.execute(
                    "UPDATE workspace_policies SET trace_id='changed' WHERE tenant_id='tenant-gate' AND workspace_id='workspace-new' AND record_id=%s",
                    (record_id,),
                )
        except psycopg.Error as exc:
            require(exc.sqlstate == "55000", "immutable sqlstate")
        else:
            raise AssertionError("immutable update accepted")

        try:
            with connection.transaction():
                canonical(
                    connection, "weight_profiles", "tenant-gate", "workspace-new",
                    "gate:bad-weight", base_payload("workspace-new", "weight_profiles"),
                    extra=(("knowledge_scope_id", "missing-scope"),),
                )
        except psycopg.errors.ForeignKeyViolation:
            pass
        else:
            raise AssertionError("FK rejection missing")

        with connection.transaction():
            connection.execute("SET LOCAL ROLE daon_app")
            connection.execute("SELECT set_config('app.tenant_id','tenant-gate',true)")
            connection.execute("SELECT set_config('app.workspace_id','workspace-new',true)")
            visible = connection.execute(
                "SELECT count(*) FROM workspace_policies WHERE workspace_id IN ('workspace-new','workspace-other')"
            ).fetchone()[0]
            require(visible == 1, "RLS cross tenant")

        binding_id, snapshot_id = connection.execute(
            "SELECT record_id,ruleset_version_snapshot_id FROM ruleset_bindings "
            "WHERE tenant_id='tenant-gate' AND workspace_id='workspace-new' AND created_by='migration:0013'"
        ).fetchone()
        canonical(
            connection, "rule_evaluations", "tenant-gate", "workspace-new", "gate:non-owned-evaluation",
            {"result": "review_required"},
            extra=(("ruleset_binding_id", binding_id), ("ruleset_version_snapshot_id", snapshot_id)),
            created_by="gate:non-owned",
        )
        connection.execute("RESET ROLE")
        canonical(
            connection, "scope_snapshots", "tenant-gate", "workspace-legacy",
            "gate:non-owned-legacy-scope-snapshot",
            {"knowledge_scope_id": legacy_v2_id, "source_version_ids": [legacy_source_version_id]},
            extra=(("knowledge_scope_id", legacy_v2_id),), created_by="gate:non-owned",
        )

    try:
        migrate("0012", downgrade=True)
    except Exception as exc:
        require("STUDIO_DEFAULT_POLICY_ROLLBACK_BLOCKED" in str(exc), "downgrade fail-close code")
    else:
        raise AssertionError("referenced downgrade unexpectedly succeeded")

    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013", "fail-close revision")
        require(connection.execute("SELECT count(*) FROM ruleset_bindings WHERE created_by='migration:0013'").fetchone()[0] > 0, "fail-close rows")
        require(connection.execute("SELECT count(*) FROM scope_snapshots WHERE record_id='gate:non-owned-legacy-scope-snapshot'").fetchone()[0] == 1, "legacy v2 non-owned reference preserved")
        connection.execute("ALTER TABLE scope_snapshots DISABLE TRIGGER scope_snapshots_immutable")
        connection.execute("DELETE FROM scope_snapshots WHERE record_id='gate:non-owned-legacy-scope-snapshot'")
        connection.execute("ALTER TABLE scope_snapshots ENABLE TRIGGER scope_snapshots_immutable")
        connection.execute("ALTER TABLE rule_evaluations DISABLE TRIGGER rule_evaluations_immutable")
        connection.execute("DELETE FROM rule_evaluations WHERE record_id='gate:non-owned-evaluation'")
        connection.execute("ALTER TABLE rule_evaluations ENABLE TRIGGER rule_evaluations_immutable")

    migrate("0012", downgrade=True)
    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE record_id='preexisting:scope'").fetchone()[0] == 1, "owned-only rollback")
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE tenant_id='tenant-gate' AND workspace_id='workspace-legacy' AND record_id=%s AND version=1", (legacy_scope_id,)).fetchone()[0] == 1, "legacy v1 preserved on downgrade")
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE tenant_id='tenant-gate' AND workspace_id='workspace-legacy'").fetchone()[0] == 1, "legacy owned v2 removed on downgrade")
        require(sum(connection.execute(f"SELECT count(*) FROM {table} WHERE created_by='migration:0013'").fetchone()[0] for table in TABLES) == 0, "owned cleanup")
        require(connection.execute("SELECT count(*) FROM pg_trigger WHERE tgname='studio_workspace_defaults_after_insert'").fetchone()[0] == 0, "trigger cleanup")

    migrate("0013")
    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013", "reapply revision")
        suffix = hashlib.md5(b"tenant-gate|workspace-new").hexdigest()
        require(connection.execute("SELECT count(*) FROM workspace_policies WHERE record_id=%s", (f"studio-default:workspace-policy:{suffix}",)).fetchone()[0] == 1, "deterministic reapply")
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE tenant_id='tenant-gate' AND workspace_id='workspace-legacy' AND record_id=%s AND version=2 AND previous_version_id=%s", (legacy_v2_id, legacy_scope_id)).fetchone()[0] == 1, "legacy deterministic v2 reapply")

        seed_deny_egress(connection, "workspace-new")

    context = StudioContext(
        "tenant-gate", "workspace-new", "gate-actor", "gate-trace", "0013",
    )
    result = PostgresStudioWorkspaceRepository(GateCloudStore()).list_outputs(context)
    require(result["outputs"] == (), "repository outputs empty")
    require(len(result["studio_locks"]) == 6, "repository policy locks")
    with psycopg.connect(DSN) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL ROLE daon_app")
            connection.execute("SELECT set_config('app.tenant_id','tenant-gate',true)")
            connection.execute("SELECT set_config('app.workspace_id','workspace-new',true)")
            cross_tenant = connection.execute(
                "SELECT count(*) FROM workspace_policies WHERE tenant_id='tenant-other' OR workspace_id='workspace-other'"
            ).fetchone()[0]
            require(cross_tenant == 0, "repository RLS cross tenant")

    print("PASS postgres=actual migration=0001-0013 legacy=v1-to-v2+idempotent+weight-fk negatives=8+compat-collision conflict=fail-close latest-invalid=fail-close history-tie=fail-close revision0012+rows-preserved backfill=missing+partial+full-preserved0 trigger=6 digest=valid fk=rejected immutable=rejected rls=cross-tenant0 repository=sqlstate0+outputs0+locks6 downgrade=legacy-ref-fail-close+owned-v2-only reapply=deterministic")


if __name__ == "__main__":
    main()
