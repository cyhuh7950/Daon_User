from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from daon_user_api.admin_membership_roles import AdminMembershipRoleService
from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationError, AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import SqliteIdentityRepository
from test_authorization_support import FixedClock, POLICY_VERSION, TRACE_ID, principal


def fixture(tmp_path):
    repository = SqliteAuthorizationRepository(tmp_path / "auth.sqlite3")
    identity = SqliteIdentityRepository(tmp_path / "auth.sqlite3")
    with identity.transaction() as connection:
        for user_id in ("admin", "owner-a", "owner-b", "target", "role-target",
                        "inactive-target", "other-admin", "second-admin"):
            connection.execute(
                "INSERT INTO users(user_id,subject,state) VALUES (?,?,?)",
                (user_id, user_id, "active"),
            )
    clock = FixedClock()
    for tenant in ("a", "b"):
        repository.bootstrap_workspace(
            tenant_id=f"tenant-{tenant}", workspace_id=f"workspace-{tenant}",
            owner_user_id=f"owner-{tenant}", owner_role=Role.ORGANIZATION_ADMIN,
            workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=100,
            now=clock(),
        )
    ordinary = AuthorizationService(repository=repository, audit_store=AuditEventStore(), clock=clock)
    ordinary.set_membership(
        principal=principal("owner-b", "tenant-b"), workspace_id="workspace-b",
        user_id="target", role=Role.VIEWER, expected_version=0,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    service = AdminMembershipRoleService(
        repository=repository, system_admin_user_ids=frozenset({"admin"}), clock=clock,
    )
    return repository, ordinary, service


def change(service, *, actor=None, target="target", role=Role.EDITOR, version=1,
           key="role-change-key-0001", tenant="tenant-b", workspace="workspace-b",
           reason="ROLE_DUTY_CHANGE"):
    return service.change_existing_workspace_role(
        actor=actor or principal("admin", "tenant-a"), tenant_id=tenant,
        workspace_id=workspace, user_id=target, role=role,
        expected_version=version, idempotency_key=key, reason=reason,
    )


def test_cross_tenant_admin_changes_existing_role_without_expanding_ordinary_path(tmp_path):
    repository, ordinary, service = fixture(tmp_path)
    view = service.list_effective_memberships(principal("admin", "tenant-a"), "tenant-b", "target")
    assert view.tenant is None
    assert [(item.workspace_id, item.role, item.version) for item in view.workspaces] == [
        ("workspace-b", Role.VIEWER, 1)
    ]
    result = change(service)
    assert (result.role, result.version, result.acl_version, result.replayed) == (Role.EDITOR, 2, 3, False)
    with pytest.raises(AuthorizationError) as denied:
        ordinary.set_membership(
            principal=principal("admin", "tenant-a"), workspace_id="workspace-b",
            user_id="target", role=Role.VIEWER, expected_version=2,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert denied.value.http_status == 404
    assert repository.membership_version("tenant-b", "workspace-b", "target") == 2


def test_deleted_identity_with_stale_auth_role_is_hidden_and_patch_has_no_side_effect(tmp_path):
    repository, _, service = fixture(tmp_path)
    with repository.transaction() as connection:
        connection.execute("DELETE FROM users WHERE user_id='target'")
        before = connection.execute(
            "SELECT role,version FROM auth_memberships WHERE tenant_id='tenant-b' "
            "AND workspace_id='workspace-b' AND user_id='target'"
        ).fetchone()
        acl_before = connection.execute(
            "SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'"
        ).fetchone()[0]
    with pytest.raises(AuthorizationError) as hidden_get:
        service.list_effective_memberships(principal("admin", "tenant-a"), "tenant-b", "target")
    assert hidden_get.value.http_status == 404
    with pytest.raises(AuthorizationError) as hidden_patch:
        change(service)
    assert hidden_patch.value.http_status == 404
    with repository.transaction() as connection:
        after = connection.execute(
            "SELECT role,version FROM auth_memberships WHERE tenant_id='tenant-b' "
            "AND workspace_id='workspace-b' AND user_id='target'"
        ).fetchone()
        acl_after = connection.execute(
            "SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'"
        ).fetchone()[0]
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()[0]
    assert tuple(after) == tuple(before)
    assert acl_after == acl_before
    assert operation_count == 0


def test_deleted_identity_cannot_replay_prior_role_change(tmp_path):
    repository, _, service = fixture(tmp_path)
    change(service)
    with repository.transaction() as connection:
        connection.execute("DELETE FROM users WHERE user_id='target'")
    with pytest.raises(AuthorizationError) as hidden:
        change(service)
    assert hidden.value.http_status == 404
    with repository.transaction() as connection:
        role = connection.execute(
            "SELECT role,version FROM auth_memberships WHERE workspace_id='workspace-b' AND user_id='target'"
        ).fetchone()
        operations = connection.execute(
            "SELECT COUNT(*) FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()[0]
    assert tuple(role) == ("editor", 2)
    assert operations == 1


def test_active_tenant_role_without_workspace_is_read_only_and_hidden_outside_its_tenant(tmp_path):
    repository, _, service = fixture(tmp_path)
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("tenant-role-only", "role-target", "organization_admin", "active", 4,
             FixedClock()().isoformat()),
        )
        connection.execute(
            "INSERT INTO auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("tenant-inactive-only", "inactive-target", "organization_admin", "revoked", 1,
             FixedClock()().isoformat()),
        )
    result = service.list_effective_memberships(
        principal("admin", "tenant-a"), "tenant-role-only", "role-target",
    )
    assert result.tenant is not None
    assert (result.tenant.tenant_id, result.tenant.role, result.tenant.state,
            result.tenant.version) == ("tenant-role-only", Role.ORGANIZATION_ADMIN, "active", 4)
    assert result.workspaces == ()
    for tenant, user in (
        ("tenant-role-only", "missing"), ("tenant-inactive-only", "inactive-target"),
        ("tenant-missing", "role-target"), ("tenant-a", "role-target"),
    ):
        with pytest.raises(AuthorizationError) as hidden:
            service.list_effective_memberships(principal("admin", "tenant-a"), tenant, user)
        assert hidden.value.http_status == 404
    with pytest.raises(AuthorizationError) as no_workspace:
        change(service, tenant="tenant-role-only", workspace="workspace-b", target="role-target")
    assert no_workspace.value.http_status == 404


def test_nonadmin_hidden_targets_and_inactive_membership(tmp_path):
    repository, _, service = fixture(tmp_path)
    with pytest.raises(AuthorizationError) as forbidden:
        service.list_effective_memberships(principal("ordinary", "tenant-b"), "tenant-b", "target")
    assert forbidden.value.http_status == 403
    for tenant, workspace, target in [
        ("tenant-a", "workspace-b", "target"),
        ("tenant-b", "workspace-a", "target"),
        ("tenant-b", "workspace-b", "absent"),
    ]:
        with pytest.raises(AuthorizationError) as hidden:
            change(service, tenant=tenant, workspace=workspace, target=target)
        assert hidden.value.http_status == 404
    with repository.transaction() as connection:
        connection.execute("UPDATE auth_memberships SET state='revoked' WHERE user_id='target'")
    with pytest.raises(AuthorizationError) as hidden:
        change(service)
    assert hidden.value.http_status == 404


def test_tenant_role_is_read_only_and_last_workspace_admin_is_protected(tmp_path):
    repository, _, service = fixture(tmp_path)
    view = service.list_effective_memberships(principal("admin", "tenant-a"), "tenant-b", "owner-b")
    assert view.tenant.role is Role.ORGANIZATION_ADMIN
    assert view.workspaces == ()
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE auth_memberships SET role='workspace_admin' WHERE tenant_id='tenant-b' AND user_id='target'"
        )
    with pytest.raises(AuthorizationError) as last_admin:
        change(service, role=Role.VIEWER)
    assert last_admin.value.http_status == 403
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("tenant-b", "workspace-b", "other-admin", "workspace_admin", "active", 1, FixedClock()().isoformat()),
        )
    result = change(service, role=Role.VIEWER)
    assert result.role is Role.VIEWER and result.version == 2


def test_other_system_admin_target_is_protected(tmp_path):
    repository, _, _ = fixture(tmp_path)
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("tenant-b", "workspace-b", "second-admin", "viewer", "active", 1, FixedClock()().isoformat()),
        )
    service = AdminMembershipRoleService(
        repository=repository, system_admin_user_ids=frozenset({"admin", "second-admin"}), clock=FixedClock(),
    )
    with pytest.raises(AuthorizationError) as protected:
        change(service, target="second-admin")
    assert protected.value.http_status == 403


def test_invalid_scope_and_idempotency_key_are_422(tmp_path):
    _, _, service = fixture(tmp_path)
    with pytest.raises(AuthorizationError) as scope:
        change(service, tenant="bad/tenant")
    assert scope.value.http_status == 422
    with pytest.raises(AuthorizationError) as key:
        change(service, key="invalid key with spaces")
    assert key.value.http_status == 422


@pytest.mark.parametrize("reason", [
    "approved-role-change", "ROLE_DUTY_CHANGE ", "role_duty_change",
    "qa@example.invalid", "sk-test-placeholder", "UNKNOWN", "", None,
])
def test_unapproved_reason_is_rejected_before_role_or_audit_write(tmp_path, reason):
    repository, _, service = fixture(tmp_path)
    with repository.transaction() as connection:
        before_acl = connection.execute(
            "SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'"
        ).fetchone()[0]
    with pytest.raises(AuthorizationError) as invalid:
        change(service, reason=reason)
    assert invalid.value.http_status == 422
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT role,version FROM auth_memberships WHERE workspace_id='workspace-b' AND user_id='target'"
        ).fetchone()
        assert tuple(row) == ("viewer", 1)
        assert connection.execute(
            "SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'"
        ).fetchone()[0] == before_acl
        assert connection.execute("SELECT COUNT(*) FROM auth_admin_role_operations").fetchone()[0] == 0


@pytest.mark.parametrize("reason", [
    "ROLE_DUTY_CHANGE", "ACCESS_REVIEW", "SECURITY_RESTRICTION", "CORRECTION", "OTHER",
])
def test_approved_reason_code_is_the_only_audit_reason(tmp_path, reason):
    repository, _, service = fixture(tmp_path)
    change(service, reason=reason)
    with repository.transaction() as connection:
        stored = connection.execute("SELECT reason FROM auth_admin_role_operations").fetchone()[0]
        assert stored == reason


def test_same_idempotency_key_with_different_reason_code_is_409(tmp_path):
    repository, _, service = fixture(tmp_path)
    first = change(service, reason="ROLE_DUTY_CHANGE")
    with pytest.raises(AuthorizationError) as reused:
        change(service, reason="ACCESS_REVIEW")
    assert reused.value.http_status == 409
    with repository.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM auth_admin_role_operations").fetchone()[0] == 1
        assert connection.execute("SELECT reason FROM auth_admin_role_operations").fetchone()[0] == "ROLE_DUTY_CHANGE"
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'").fetchone()[0] == first.acl_version


def test_sqlite_audit_reason_check_rejects_unapproved_value(tmp_path):
    repository, _, service = fixture(tmp_path)
    change(service)
    with pytest.raises(AuthorizationError) as rejected:
        with repository.transaction() as connection:
            connection.execute("UPDATE auth_admin_role_operations SET reason='free text'")
    assert rejected.value.http_status == 409
    with repository.transaction() as connection:
        assert connection.execute("SELECT reason FROM auth_admin_role_operations").fetchone()[0] == "ROLE_DUTY_CHANGE"


def _legacy_reason_database(path, reasons):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE auth_admin_role_operations ("
            "actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, "
            "request_fingerprint TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, "
            "tenant_id TEXT NOT NULL, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, "
            "old_role TEXT NOT NULL, new_role TEXT NOT NULL, "
            "old_version INTEGER NOT NULL, new_version INTEGER NOT NULL, acl_version INTEGER NOT NULL, "
            "reason TEXT NOT NULL, outcome TEXT NOT NULL CHECK(outcome IN ('changed','unchanged')), "
            "created_at TEXT NOT NULL, delivered_at TEXT, PRIMARY KEY(actor_id,idempotency_key))"
        )
        for number, reason in enumerate(reasons):
            connection.execute(
                "INSERT INTO auth_admin_role_operations ("
                "actor_id,idempotency_key,request_fingerprint,event_id,tenant_id,workspace_id,user_id,"
                "old_role,new_role,old_version,new_version,acl_version,reason,outcome,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("actor", f"key-{number}", "fingerprint", f"event-{number}", "tenant", "workspace", "user",
                 "viewer", "editor", 1, 2, 2, reason, "changed", "2026-10-06T00:00:00+00:00"),
            )


def test_legacy_sqlite_reason_guard_rejects_direct_insert_update_and_preserves_valid_rows(tmp_path):
    path = tmp_path / "legacy-auth.sqlite3"
    _legacy_reason_database(path, ["ROLE_DUTY_CHANGE"])
    repository = SqliteAuthorizationRepository(path)
    repository.close()
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO auth_admin_role_operations ("
                "actor_id,idempotency_key,request_fingerprint,event_id,tenant_id,workspace_id,user_id,"
                "old_role,new_role,old_version,new_version,acl_version,reason,outcome,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("actor", "bad-key", "fingerprint", "bad-event", "tenant", "workspace", "user",
                 "viewer", "editor", 1, 2, 2, "free text", "changed", "2026-10-06T00:00:00+00:00"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE auth_admin_role_operations SET reason='free text' WHERE event_id='event-0'")
        assert connection.execute(
            "SELECT event_id,reason FROM auth_admin_role_operations ORDER BY event_id"
        ).fetchall() == [("event-0", "ROLE_DUTY_CHANGE")]
        connection.execute("UPDATE auth_admin_role_operations SET reason='ACCESS_REVIEW' WHERE event_id='event-0'")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT event_id,reason FROM auth_admin_role_operations"
        ).fetchall() == [("event-0", "ACCESS_REVIEW")]


def test_legacy_sqlite_with_existing_unapproved_reason_fails_closed_without_rewriting_rows(tmp_path):
    path = tmp_path / "legacy-invalid.sqlite3"
    _legacy_reason_database(path, ["ROLE_DUTY_CHANGE", "free text"])
    with pytest.raises(AuthorizationError) as blocked:
        SqliteAuthorizationRepository(path)
    assert blocked.value.code == "AUDIT_REASON_LEGACY_INVALID"
    assert blocked.value.http_status == 503
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT event_id,reason FROM auth_admin_role_operations ORDER BY event_id"
        ).fetchall() == [("event-0", "ROLE_DUTY_CHANGE"), ("event-1", "free text")]


def test_legacy_sqlite_rejects_conflicting_reason_guard_instead_of_silently_skipping_it(tmp_path):
    path = tmp_path / "legacy-conflicting-trigger.sqlite3"
    _legacy_reason_database(path, ["ROLE_DUTY_CHANGE"])
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER auth_admin_role_reason_insert_guard "
            "BEFORE INSERT ON auth_admin_role_operations BEGIN SELECT 1; END"
        )
    with pytest.raises(AuthorizationError) as blocked:
        SqliteAuthorizationRepository(path)
    assert blocked.value.code == "AUDIT_REASON_GUARD_INVALID"
    assert blocked.value.http_status == 503


def test_legacy_sqlite_reason_guard_survives_repository_restart(tmp_path):
    path = tmp_path / "legacy-restart.sqlite3"
    _legacy_reason_database(path, ["ROLE_DUTY_CHANGE"])
    SqliteAuthorizationRepository(path).close()
    SqliteAuthorizationRepository(path).close()
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE auth_admin_role_operations SET reason='free text'")


def test_role_guards_version_replay_and_noop(tmp_path):
    repository, _, service = fixture(tmp_path)
    for role in (Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN):
        with pytest.raises(AuthorizationError) as invalid:
            change(service, role=role)
        assert invalid.value.http_status == 422
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("tenant-b", "workspace-b", "admin", "viewer", "active", 1, FixedClock()().isoformat()),
        )
    with pytest.raises(AuthorizationError) as self_change:
        change(service, target="admin")
    assert self_change.value.http_status == 403
    with pytest.raises(AuthorizationError) as stale:
        change(service, version=3)
    assert stale.value.http_status == 412
    first = change(service)
    with repository.transaction() as connection:
        event = connection.execute(
            "SELECT old_role,new_role,old_version,new_version,reason,outcome,delivered_at "
            "FROM auth_admin_role_operations WHERE actor_id='admin' AND idempotency_key='role-change-key-0001'"
        ).fetchone()
        assert tuple(event) == ("viewer", "editor", 1, 2, "ROLE_DUTY_CHANGE", "changed", None)
    replay = change(service)
    assert replay.replayed and replay.version == first.version
    with pytest.raises(AuthorizationError) as reused:
        change(service, role=Role.REVIEWER)
    assert reused.value.http_status == 409
    noop = change(service, role=Role.EDITOR, version=2, key="role-noop-key-0001")
    assert noop.version == 2 and noop.acl_version == first.acl_version
    assert repository.membership_version("tenant-b", "workspace-b", "target") == 2


def test_audit_failure_rolls_back_role_and_acl(tmp_path):
    repository, _, service = fixture(tmp_path)
    with repository.transaction() as connection:
        connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON auth_admin_role_operations BEGIN SELECT RAISE(FAIL, 'audit failed'); END")
        acl = connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'").fetchone()[0]
    with pytest.raises(AuthorizationError) as failed:
        change(service)
    assert failed.value.http_status == 503
    with repository.transaction() as connection:
        row = connection.execute("SELECT role,version FROM auth_memberships WHERE workspace_id='workspace-b' AND user_id='target'").fetchone()
        assert (row[0], row[1]) == ("viewer", 1)
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'").fetchone()[0] == acl


def test_concurrent_writers_have_one_winner(tmp_path):
    repository, _, service = fixture(tmp_path)
    def attempt(role):
        try:
            change(service, role=role, key=f"race-{role.value}-0001")
            return 200
        except AuthorizationError as error:
            return error.http_status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, (Role.EDITOR, Role.REVIEWER))) == [200, 412]
    with repository.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM auth_admin_role_operations").fetchone()[0] == 1
        assert connection.execute("SELECT acl_version FROM auth_workspaces WHERE workspace_id='workspace-b'").fetchone()[0] == 3
