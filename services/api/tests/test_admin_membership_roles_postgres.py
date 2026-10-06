from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_0054_migration_follows_0053_and_keeps_auth_outbox_additive():
    source = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
              "0054_c9_admin_membership_roles.py").read_text(encoding="utf-8")
    assert 'revision = "0054"' in source
    assert 'down_revision = "0053"' in source
    assert "identity_auth_admin_role_operations" in source
    assert "delivered_at" in source
    assert "UPDATE identity_auth_memberships" not in source
    assert "reason text NOT NULL CHECK (reason IN" in source
    for reason in ("ROLE_DUTY_CHANGE", "ACCESS_REVIEW", "SECURITY_RESTRICTION", "CORRECTION", "OTHER"):
        assert f"'{reason}'" in source


@pytest.mark.skipif(
    not os.environ.get("DAON_C9_R1_ISOLATED_POSTGRES_DSN"),
    reason="dedicated C9 R1 PostgreSQL database is not configured",
)
def test_postgres_role_change_concurrency_and_audit_rollback():
    # The caller must provide an isolated, disposable C9 R1 database migrated to 0054.
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from uuid import uuid4

    from daon_user_api.admin_membership_roles import AdminMembershipRoleService
    from daon_user_api.authorization import AuthorizationError, Role
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from test_authorization_support import principal

    dsn = os.environ["DAON_C9_R1_ISOLATED_POSTGRES_DSN"]
    suffix = uuid4().hex[:12]
    marker = f"c9r1-{suffix}"
    constraint = f"c9_r1_fail_audit_{suffix}"
    repository = PostgresAuthorizationRepository(dsn)
    clock = lambda: datetime.now(timezone.utc)
    service = AdminMembershipRoleService(
        repository=repository, system_admin_user_ids=frozenset({"c9-r1-admin"}), clock=clock,
    )
    try:
        repository.bootstrap_workspace(
            tenant_id=marker, workspace_id=marker, owner_user_id="c9-r1-owner",
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
            data_area="cloud_sync", cost_limit_cents=100, now=clock(),
        )
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (marker, marker, "c9-r1-target", "viewer", "active", 1, clock().isoformat()),
            )
        def attempt(role):
            try:
                service.change_existing_workspace_role(
                    actor=principal("c9-r1-admin", "outside-tenant"), tenant_id=marker,
                    workspace_id=marker, user_id="c9-r1-target", role=role,
                    expected_version=1, idempotency_key=f"c9-r1-{role.value}-race-0001",
                    reason="ACCESS_REVIEW",
                )
                return 200
            except AuthorizationError as error:
                return error.http_status
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(attempt, (Role.EDITOR, Role.REVIEWER))) == [200, 412]
        # Only this disposable, isolated DB may receive the deliberately invalid INSERT.
        with pytest.raises(AuthorizationError) as rejected:
            with repository.transaction() as connection:
                connection.execute(
                    "INSERT INTO auth_admin_role_operations("
                    "actor_id,idempotency_key,request_fingerprint,event_id,tenant_id,workspace_id,user_id,"
                    "old_role,new_role,old_version,new_version,acl_version,reason,outcome,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("c9-r1-admin", f"c9-r1-invalid-{suffix}", "0" * 64, f"c9-r1-invalid-{suffix}",
                     marker, marker, "c9-r1-target", "viewer", "editor", 1, 2, 3,
                     "free text", "changed", clock().isoformat()),
                )
        assert rejected.value.http_status == 503
        with repository.transaction() as connection:
            row = connection.execute(
                "SELECT role,version FROM auth_memberships WHERE tenant_id=? AND workspace_id=? AND user_id=?",
                (marker, marker, "c9-r1-target"),
            ).fetchone()
            acl = connection.execute(
                "SELECT acl_version FROM auth_workspaces WHERE workspace_id=?", (marker,)
            ).fetchone()[0]
            before = (str(row["role"]), int(row["version"]), int(acl))
        # A deliberate outbox CHECK violation must roll back both auth writes.
        with repository.transaction() as connection:
            connection.execute(
                f"ALTER TABLE auth_admin_role_operations ADD CONSTRAINT {constraint} "
                "CHECK (reason <> 'CORRECTION') NOT VALID"
            )
        with pytest.raises(AuthorizationError) as failed:
            service.change_existing_workspace_role(
                actor=principal("c9-r1-admin", "outside-tenant"), tenant_id=marker,
                workspace_id=marker, user_id="c9-r1-target", role=Role.APPROVER,
                expected_version=2, idempotency_key="c9-r1-audit-fail-0001", reason="CORRECTION",
            )
        assert failed.value.http_status == 503
        with repository.transaction() as connection:
            row = connection.execute(
                "SELECT role,version FROM auth_memberships WHERE tenant_id=? AND workspace_id=? AND user_id=?",
                (marker, marker, "c9-r1-target"),
            ).fetchone()
            acl = connection.execute(
                "SELECT acl_version FROM auth_workspaces WHERE workspace_id=?", (marker,)
            ).fetchone()[0]
            assert (str(row["role"]), int(row["version"]), int(acl)) == before
    finally:
        with repository.transaction() as connection:
            connection.execute(f"ALTER TABLE auth_admin_role_operations DROP CONSTRAINT IF EXISTS {constraint}")
            connection.execute("DELETE FROM auth_admin_role_operations WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_memberships WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_workspace_policies WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_tenant_policies WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_tenant_roles WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_workspaces WHERE tenant_id=?", (marker,))
        repository.close()
