from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[4]
DSN = os.environ.get("DAON_DB_MIGRATION_DSN", "")
TABLES = (
    "workspace_policies",
    "knowledge_scopes",
    "weight_profiles",
    "ruleset_references",
    "ruleset_version_snapshots",
    "ruleset_bindings",
)


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
            "('tenant-gate','workspace-conflict','Conflict'),"
            "('tenant-other','workspace-other','Other')"
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

    try:
        migrate("0012", downgrade=True)
    except Exception as exc:
        require("STUDIO_DEFAULT_POLICY_ROLLBACK_BLOCKED" in str(exc), "downgrade fail-close code")
    else:
        raise AssertionError("referenced downgrade unexpectedly succeeded")

    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013", "fail-close revision")
        require(connection.execute("SELECT count(*) FROM ruleset_bindings WHERE created_by='migration:0013'").fetchone()[0] > 0, "fail-close rows")
        connection.execute("ALTER TABLE rule_evaluations DISABLE TRIGGER rule_evaluations_immutable")
        connection.execute("DELETE FROM rule_evaluations WHERE record_id='gate:non-owned-evaluation'")
        connection.execute("ALTER TABLE rule_evaluations ENABLE TRIGGER rule_evaluations_immutable")

    migrate("0012", downgrade=True)
    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT count(*) FROM knowledge_scopes WHERE record_id='preexisting:scope'").fetchone()[0] == 1, "owned-only rollback")
        require(sum(connection.execute(f"SELECT count(*) FROM {table} WHERE created_by='migration:0013'").fetchone()[0] for table in TABLES) == 0, "owned cleanup")
        require(connection.execute("SELECT count(*) FROM pg_trigger WHERE tgname='studio_workspace_defaults_after_insert'").fetchone()[0] == 0, "trigger cleanup")

    migrate("0013")
    with psycopg.connect(DSN) as connection:
        require(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0013", "reapply revision")
        suffix = hashlib.md5(b"tenant-gate|workspace-new").hexdigest()
        require(connection.execute("SELECT count(*) FROM workspace_policies WHERE record_id=%s", (f"studio-default:workspace-policy:{suffix}",)).fetchone()[0] == 1, "deterministic reapply")

    print("PASS postgres=actual migration=0001-0013 conflict=fail-close latest-invalid=fail-close history-tie=fail-close revision0012+rows-preserved backfill=missing+partial+full-preserved0 trigger=6 digest=valid fk=rejected immutable=rejected rls=cross-tenant0 downgrade=fail-close+owned-only reapply=deterministic")


if __name__ == "__main__":
    main()
