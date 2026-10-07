from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

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
    inserted_identity = False
    clock = lambda: datetime.now(timezone.utc)
    service = AdminMembershipRoleService(
        repository=repository, system_admin_user_ids=frozenset({"c9-r1-admin"}), clock=clock,
    )
    try:
        with repository.transaction() as connection:
            inserted_identity = connection.execute(
                "INSERT INTO identity_users(user_id,subject,state) VALUES (?,?,?) "
                "ON CONFLICT DO NOTHING",
                ("c9-r1-target", "c9-r1-target", "active"),
            ).rowcount == 1
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
            if inserted_identity:
                connection.execute("DELETE FROM identity_users WHERE user_id=?", ("c9-r1-target",))
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("DAON_C9_R1_ISOLATED_POSTGRES_DSN"),
    reason="dedicated C9 R1 PostgreSQL database is not configured",
)
def test_postgres_role_audit_outbox_projects_once_retries_and_cleans_up():
    from datetime import datetime, timezone
    from time import monotonic
    from uuid import uuid4

    import psycopg

    from daon_user_api.admin_membership_roles import AdminMembershipRoleService
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.authorization import Role
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository
    from test_authorization_support import principal

    dsn = os.environ["DAON_C9_R1_ISOLATED_POSTGRES_DSN"]
    location = urlsplit(dsn)
    assert location.hostname == "127.0.0.1" and location.path == "/daon_user_c9_r1_qa"
    with psycopg.connect(dsn) as guard:
        assert guard.execute("SELECT current_database()").fetchone()[0] == "daon_user_c9_r1_qa"
        assert guard.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0054"
        assert guard.execute("SELECT COUNT(*) FROM security_audit_events").fetchone()[0] == 0

    suffix = uuid4().hex[:12]
    marker = f"c9-r5-audit-{suffix}"
    target = f"c9-r5-target-{suffix}"
    actor = f"c9-r5-admin-{suffix}"
    clock = lambda: datetime.now(timezone.utc)
    repository = PostgresAuthorizationRepository(dsn)
    audit_store = PostgresSecurityAuditStore(dsn)
    service = AdminMembershipRoleService(
        repository=repository, system_admin_user_ids=frozenset({actor}),
        clock=clock, audit_store=audit_store,
    )
    try:
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO identity_users(user_id,subject,state) VALUES (?,?,?)",
                (target, target, "active"),
            )
        repository.bootstrap_workspace(
            tenant_id=marker, workspace_id=marker, owner_user_id=f"c9-r5-owner-{suffix}",
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization",
            data_area="cloud_sync", cost_limit_cents=100, now=clock(),
        )
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (marker, marker, target, "viewer", "active", 1, clock()),
            )

        def change(role, version, key):
            return service.change_existing_workspace_role(
                actor=principal(actor, "outside-tenant"), tenant_id=marker,
                workspace_id=marker, user_id=target, role=role,
                expected_version=version, idempotency_key=key, reason="ACCESS_REVIEW",
            )

        first_key = f"c9-r5-audit-first-{suffix}"
        first = change(Role.EDITOR, 1, first_key)
        assert (first.role, first.version, first.replayed) == (Role.EDITOR, 2, False)
        with repository.transaction() as connection:
            pending = connection.execute(
                "SELECT event_id,delivered_at FROM auth_admin_role_operations WHERE tenant_id=?",
                (marker,),
            ).fetchone()
        event_id = str(pending["event_id"])
        assert pending["delivered_at"] is None
        assert audit_store.read(event_id, tenant_id=marker) is None

        service._dispatch_pending_audit()
        projected = audit_store.read(event_id, tenant_id=marker)
        assert projected is not None
        assert (projected.actor_id, projected.target_id, projected.tenant_id,
                projected.workspace_id, projected.action) == (
            actor, target, marker, marker, "authorization.membership.changed",
        )
        assert dict(projected.before) == {"role": "viewer", "version": 1}
        assert dict(projected.after) == {"role": "editor", "version": 2}
        assert dict(projected.metadata)["reason_code"] == "ACCESS_REVIEW"
        with repository.transaction() as connection:
            assert connection.execute(
                "SELECT delivered_at FROM auth_admin_role_operations WHERE event_id=?",
                (event_id,),
            ).fetchone()[0] is not None
        assert change(Role.EDITOR, 1, first_key).replayed is True
        assert len(audit_store.list(tenant_id=marker).items) == 1

        second = change(Role.VIEWER, 2, f"c9-r5-audit-second-{suffix}")
        assert (second.role, second.version) == (Role.VIEWER, 3)
        with repository.transaction() as connection:
            second_event_id = str(connection.execute(
                "SELECT event_id FROM auth_admin_role_operations WHERE tenant_id=? AND event_id<>?",
                (marker, event_id),
            ).fetchone()[0])
        with psycopg.connect(dsn) as blocker:
            blocker.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (marker,))
            started = monotonic()
            service._dispatch_pending_audit()
            assert monotonic() - started < 10.0
            with repository.transaction() as connection:
                assert connection.execute(
                    "SELECT delivered_at FROM auth_admin_role_operations WHERE event_id=?",
                    (second_event_id,),
                ).fetchone()[0] is None
            blocker.rollback()
        service._dispatch_pending_audit()
        assert audit_store.read(second_event_id, tenant_id=marker) is not None
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE auth_admin_role_operations SET delivered_at=NULL WHERE event_id=?",
                (event_id,),
            )
        service._dispatch_pending_audit()
        assert len(audit_store.list(tenant_id=marker).items) == 2
        with repository.transaction() as connection:
            rows = connection.execute(
                "SELECT delivered_at FROM auth_admin_role_operations WHERE tenant_id=?",
                (marker,),
            ).fetchall()
        assert len(rows) == 2 and all(row[0] is not None for row in rows)
    finally:
        audit_store.close()
        with psycopg.connect(dsn) as cleanup:
            cleanup.execute("LOCK TABLE security_audit_events IN ACCESS EXCLUSIVE MODE")
            assert cleanup.execute(
                "SELECT COUNT(*) FROM security_audit_events WHERE tenant_id<>%s", (marker,),
            ).fetchone()[0] == 0
            if cleanup.execute(
                "SELECT COUNT(*) FROM security_audit_events WHERE tenant_id=%s", (marker,),
            ).fetchone()[0]:
                # QA-only append-only table: DELETE is deliberately prohibited by migration 0015.
                cleanup.execute("TRUNCATE security_audit_events RESTART IDENTITY")
        with repository.transaction() as connection:
            connection.execute("DELETE FROM auth_admin_role_operations WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_memberships WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_workspace_policies WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_tenant_policies WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_tenant_roles WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM auth_workspaces WHERE tenant_id=?", (marker,))
            connection.execute("DELETE FROM identity_users WHERE user_id=?", (target,))
        repository.close()
        with psycopg.connect(dsn) as check:
            assert check.execute("SELECT COUNT(*) FROM security_audit_events").fetchone()[0] == 0
            assert check.execute(
                "SELECT COUNT(*) FROM identity_auth_admin_role_operations WHERE tenant_id=%s",
                (marker,),
            ).fetchone()[0] == 0
