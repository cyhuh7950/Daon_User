from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from daon_user_api.admin_membership_roles import AdminMembershipRoleService
from daon_user_api import audit as audit_module
from daon_user_api.audit import ActorType, AuditEventDraft, AuditEventStore, AuditOutcome, PostgresSecurityAuditStore
from daon_user_api.authorization import AuthorizationError, AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import SqliteIdentityRepository
from daon_user_api import postgres_adapters
from test_authorization_support import FixedClock, POLICY_VERSION, TRACE_ID, principal


def fixture(tmp_path, *, audit_store=None):
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
        audit_store=audit_store,
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


def test_role_change_projects_one_approved_audit_event_and_marks_delivery(tmp_path):
    audit_store = AuditEventStore()
    repository, _, service = fixture(tmp_path, audit_store=audit_store)
    result = change(service)
    assert result.replayed is False
    with repository.transaction() as connection:
        operation = connection.execute(
            "SELECT event_id,delivered_at FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()
    assert operation["delivered_at"] is None
    assert audit_store.list(tenant_id="tenant-b").items == ()
    service._dispatch_pending_audit()
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()[0] is not None
    event = audit_store.read(str(operation["event_id"]))
    assert event is not None
    assert event.trace_id == "not-recorded"
    assert event.policy_version == "not-recorded"
    assert (event.actor_id, event.tenant_id, event.workspace_id, event.target_id,
            event.target_type, event.action) == (
        "admin", "tenant-b", "workspace-b", "target", "membership",
        "authorization.membership.changed",
    )
    assert dict(event.before) == {"role": "viewer", "version": 1}
    assert dict(event.after) == {"role": "editor", "version": 2}
    assert dict(event.metadata) == {
        "reason_code": "ROLE_DUTY_CHANGE", "outcome": "changed", "acl_version": 3,
    }
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1
    assert change(service).replayed is True
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1


def test_temporary_audit_failure_keeps_committed_role_and_retries_once(tmp_path):
    class ToggleAuditStore(AuditEventStore):
        available = False

        def append(self, draft):
            if not self.available:
                raise OSError("temporary audit outage")
            return super().append(draft)

    audit_store = ToggleAuditStore()
    repository, _, service = fixture(tmp_path, audit_store=audit_store)
    result = change(service)
    assert (result.role, result.version, result.acl_version) == (Role.EDITOR, 2, 3)
    with repository.transaction() as connection:
        operation = connection.execute(
            "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()
        role = connection.execute(
            "SELECT role,version FROM auth_memberships WHERE workspace_id='workspace-b' AND user_id='target'"
        ).fetchone()
    assert operation["delivered_at"] is None
    assert tuple(role) == ("editor", 2)
    assert audit_store.list(tenant_id="tenant-b").items == ()
    audit_store.available = True
    assert change(service).replayed is True
    service._dispatch_pending_audit()
    with repository.transaction() as connection:
        delivered_at = connection.execute(
            "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()[0]
    assert delivered_at is not None
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1


def test_delivery_marker_failure_retries_without_duplicate_projection(tmp_path):
    audit_store = AuditEventStore()
    repository, _, service = fixture(tmp_path, audit_store=audit_store)
    with repository.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_role_delivery BEFORE UPDATE OF delivered_at "
            "ON auth_admin_role_operations BEGIN SELECT RAISE(FAIL, 'temporary marker failure'); END"
        )
    assert change(service).version == 2
    service._dispatch_pending_audit()
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()[0] is None
        connection.execute("DROP TRIGGER fail_role_delivery")
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1
    assert change(service).replayed is True
    service._dispatch_pending_audit()
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT delivered_at FROM auth_admin_role_operations WHERE user_id='target'"
        ).fetchone()[0] is not None
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1


def test_unchanged_role_has_one_audit_outcome_without_acl_increment(tmp_path):
    audit_store = AuditEventStore()
    repository, _, service = fixture(tmp_path, audit_store=audit_store)
    result = change(service, role=Role.VIEWER)
    assert (result.version, result.acl_version) == (1, 2)
    service._dispatch_pending_audit()
    event = audit_store.list(tenant_id="tenant-b").items[0]
    assert (event.action, event.target_type) == (
        "authorization.membership.unchanged", "membership",
    )
    assert dict(event.before) == {"role": "viewer", "version": 1}
    assert dict(event.after) == {"role": "viewer", "version": 1}
    assert dict(event.metadata) == {
        "reason_code": "ROLE_DUTY_CHANGE", "outcome": "unchanged", "acl_version": 2,
    }
    assert change(service, role=Role.VIEWER).replayed is True
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1


def test_failed_first_batch_does_not_starve_thirty_third_pending_event(tmp_path):
    class SelectiveAuditStore(AuditEventStore):
        def append(self, draft):
            if draft.event_id != "pending-32":
                raise OSError("older tenant audit outage")
            return super().append(draft)

    audit_store = SelectiveAuditStore()
    repository, _, service = fixture(tmp_path, audit_store=audit_store)
    with repository.transaction() as connection:
        for number in range(33):
            connection.execute(
                "INSERT INTO auth_admin_role_operations("
                "actor_id,idempotency_key,request_fingerprint,event_id,tenant_id,workspace_id,user_id,"
                "old_role,new_role,old_version,new_version,acl_version,reason,outcome,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("admin", f"pending-key-{number:02d}", "fingerprint", f"pending-{number:02d}",
                 "tenant-b", "workspace-b", "target", "viewer", "editor", 1, 2, 3,
                 "ACCESS_REVIEW", "changed", f"2026-10-06T00:00:{number:02d}+00:00"),
            )
    service._dispatch_pending_audit()
    service._dispatch_pending_audit()
    with repository.transaction() as connection:
        rows = connection.execute(
            "SELECT event_id,delivered_at FROM auth_admin_role_operations ORDER BY created_at,event_id"
        ).fetchall()
    assert all(row["delivered_at"] is None for row in rows[:32])
    assert rows[32]["delivered_at"] is not None
    assert len(audit_store.list(tenant_id="tenant-b").items) == 1


def test_continuous_new_outbox_rows_do_not_starve_failed_older_row(tmp_path):
    class SelectiveAuditStore(AuditEventStore):
        old_attempts = 0

        def append(self, draft):
            if draft.event_id == "rolling-old":
                self.old_attempts += 1
                raise OSError("older event temporarily unavailable")
            return super().append(draft)

    audit_store = SelectiveAuditStore()
    repository, _, service = fixture(tmp_path, audit_store=audit_store)

    def insert_operation(number, event_id):
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_admin_role_operations("
                "actor_id,idempotency_key,request_fingerprint,event_id,tenant_id,workspace_id,user_id,"
                "old_role,new_role,old_version,new_version,acl_version,reason,outcome,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("admin", f"rolling-key-{number:02d}", "fingerprint", event_id,
                 "tenant-b", "workspace-b", "target", "viewer", "editor", 1, 2, 3,
                 "ACCESS_REVIEW", "changed", f"2026-10-06T00:00:{number:02d}+00:00"),
            )

    insert_operation(0, "rolling-old")
    for number in range(1, 6):
        insert_operation(number, f"rolling-new-{number}")
        service._dispatch_pending_audit()

    with repository.transaction() as connection:
        rows = connection.execute(
            "SELECT event_id,delivered_at FROM auth_admin_role_operations ORDER BY created_at,event_id"
        ).fetchall()
    assert rows[0]["delivered_at"] is None
    assert audit_store.old_attempts >= 2
    assert sum(row["delivered_at"] is not None for row in rows[1:]) >= 3
    assert len(audit_store.list(tenant_id="tenant-b").items) >= 3


def test_c9_keyset_cursor_keeps_postgres_timestamptz_as_datetime():
    timestamp = datetime(2026, 10, 6, tzinfo=timezone.utc)
    operation = {
        "event_id": "auth-admin-role-cursor", "created_at": timestamp,
        "actor_id": "admin", "tenant_id": "tenant-b", "workspace_id": "workspace-b",
        "user_id": "target", "outcome": "changed", "old_role": "viewer", "new_role": "editor",
        "old_version": 1, "new_version": 2, "reason": "ACCESS_REVIEW", "acl_version": 3,
    }
    queries = []
    batch_reads = 0

    class FakeConnection:
        def execute(self, sql, params=()):
            nonlocal batch_reads
            if sql.startswith("SELECT"):
                queries.append((sql, params))
                if "DESC LIMIT 1" in sql:
                    return SimpleNamespace(fetchone=lambda: operation)
                batch_reads += 1
                return SimpleNamespace(fetchall=lambda: [operation] if batch_reads == 1 else [])
            raise AssertionError("failed append must not update delivery marker")

    class FakeRepository:
        @contextmanager
        def transaction(self):
            yield FakeConnection()

        _admin_audit_transaction = transaction

    class FailingStore:
        def append(self, draft):
            raise OSError("temporary audit outage")

    service = AdminMembershipRoleService(
        repository=FakeRepository(), system_admin_user_ids=frozenset({"admin"}),
        clock=lambda: timestamp, audit_store=FailingStore(),
    )
    service._dispatch_pending_audit()
    service._dispatch_pending_audit()
    keyset_queries = [(sql, params) for sql, params in queries if "(created_at,event_id) >" in sql]
    assert len(keyset_queries) == 1
    assert keyset_queries[0][1] == (
        timestamp, "auth-admin-role-cursor", timestamp, "auth-admin-role-cursor",
    )
    sent = []

    class FakePostgresConnection:
        def execute(self, sql, params):
            sent.append((sql, params))
            return SimpleNamespace(fetchall=lambda: [])

    adapter = object.__new__(postgres_adapters.PostgresCompatConnection)
    adapter._conn = FakePostgresConnection()
    adapter._prefixes = ("auth_",)
    adapter.execute(*keyset_queries[0])
    assert "identity_auth_admin_role_operations" in sent[0][0]
    assert "(created_at,event_id) > (%s,%s)" in sent[0][0]
    assert sent[0][1][0] is timestamp
    assert sent[0][1][2] is timestamp


def test_c9_auth_outbox_postgres_connection_and_sql_are_bounded(monkeypatch):
    connects = []
    statements = []

    class FakeConnection:
        def execute(self, statement, params=()):
            statements.append(statement)
            return SimpleNamespace(fetchone=lambda: None)

        def close(self):
            pass

    def fake_connect(dsn, **kwargs):
        connects.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(postgres_adapters.psycopg, "connect", fake_connect)
    repository = postgres_adapters.PostgresAuthorizationRepository("postgresql://audit-test")
    with repository._admin_audit_transaction() as connection:
        connection.execute("SELECT 1")
    assert len(connects) == 2
    assert "connect_timeout" not in connects[0]
    assert connects[1]["connect_timeout"] <= 2
    assert "statement_timeout" in connects[1]["options"]
    assert "lock_timeout" in connects[1]["options"]
    assert statements[-2:] == ["SELECT 1", "COMMIT"]


def test_c9_postgres_audit_append_bounds_lock_and_sql_without_changing_normal_append():
    statements = []

    class FakeConnection:
        @contextmanager
        def transaction(self):
            yield self

        def execute(self, statement, params=()):
            statements.append(statement)
            return SimpleNamespace(fetchone=lambda: None)

    class FakePool:
        closed = False

        @contextmanager
        def connection(self, timeout=None):
            yield FakeConnection()

    store = PostgresSecurityAuditStore("postgresql://audit-test")
    store._pool = FakePool()
    draft = AuditEventDraft(
        event_id="auth-admin-role-test", occurred_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        actor_id="admin", actor_type=ActorType.USER, tenant_id="tenant-b",
        workspace_id="workspace-b", action="authorization.membership.changed",
        target_type="membership", target_id="target", outcome=AuditOutcome.SUCCEEDED,
        trace_id="not-recorded", policy_version="not-recorded",
        before={"role": "viewer", "version": 1}, after={"role": "editor", "version": 2},
        metadata={"reason_code": "ACCESS_REVIEW", "outcome": "changed", "acl_version": 3},
    )
    store._append_admin_role(draft)
    assert any("SET LOCAL statement_timeout" in sql for sql in statements)
    assert any("SET LOCAL lock_timeout" in sql for sql in statements)
    statements.clear()
    store.append(draft)
    assert not any("SET LOCAL statement_timeout" in sql or "SET LOCAL lock_timeout" in sql
                   for sql in statements)


def test_c9_postgres_auth_outbox_does_not_wait_indefinitely_for_shared_lock(monkeypatch):
    monkeypatch.setattr(postgres_adapters, "_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS", 0.02, raising=False)
    class FakeConnection:
        def execute(self, statement, params=()):
            return SimpleNamespace(fetchone=lambda: None)

        def close(self):
            pass

    monkeypatch.setattr(postgres_adapters.psycopg, "connect", lambda *args, **kwargs: FakeConnection())
    repository = postgres_adapters.PostgresAuthorizationRepository("postgresql://audit-test")
    entered, release = threading.Event(), threading.Event()

    def hold_repository_lock():
        with repository._lock:
            entered.set()
            release.wait(0.5)

    holder = threading.Thread(target=hold_repository_lock)
    holder.start()
    try:
        assert entered.wait(1.0)
        started = time.monotonic()
        with pytest.raises(AuthorizationError) as unavailable:
            with repository._admin_audit_transaction():
                pass
        assert unavailable.value.http_status == 503
        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        holder.join(1.0)


def test_c9_postgres_audit_append_does_not_wait_indefinitely_for_shared_lock(monkeypatch):
    monkeypatch.setattr(audit_module, "_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS", 0.02, raising=False)
    store = PostgresSecurityAuditStore("postgresql://audit-test")
    class FakeConnection:
        @contextmanager
        def transaction(self):
            yield self

        def execute(self, statement, params=()):
            return SimpleNamespace(fetchone=lambda: None)

    class FakePool:
        closed = False

        @contextmanager
        def connection(self, timeout=None):
            yield FakeConnection()

    store._pool = FakePool()
    entered, release = threading.Event(), threading.Event()

    def hold_audit_lock():
        with store._lock:
            entered.set()
            release.wait(0.5)

    holder = threading.Thread(target=hold_audit_lock)
    holder.start()
    try:
        assert entered.wait(1.0)
        draft = AuditEventDraft(
            event_id="auth-admin-role-lock-test", occurred_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
            actor_id="admin", actor_type=ActorType.USER, tenant_id="tenant-b",
            workspace_id="workspace-b", action="authorization.membership.changed",
            target_type="membership", target_id="target", outcome=AuditOutcome.SUCCEEDED,
            trace_id="not-recorded", policy_version="not-recorded",
            before={"role": "viewer", "version": 1}, after={"role": "editor", "version": 2},
            metadata={"reason_code": "ACCESS_REVIEW", "outcome": "changed", "acl_version": 3},
        )
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            store._append_admin_role(draft)
        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        holder.join(1.0)


def test_c9_postgres_audit_pool_open_lock_is_bounded(monkeypatch):
    monkeypatch.setattr(audit_module, "_ADMIN_ROLE_AUDIT_LOCK_TIMEOUT_SECONDS", 0.02)
    store = PostgresSecurityAuditStore("postgresql://audit-test")
    class FakeConnection:
        @contextmanager
        def transaction(self):
            yield self

        def execute(self, statement, params=()):
            return SimpleNamespace(fetchone=lambda: None)

    class FakePool:
        closed = True

        def open(self, wait=False):
            self.closed = False

        @contextmanager
        def connection(self, timeout=None):
            yield FakeConnection()

    store._pool = FakePool()
    entered, release = threading.Event(), threading.Event()

    def hold_open_lock():
        with store._open_lock:
            entered.set()
            release.wait(0.5)

    holder = threading.Thread(target=hold_open_lock)
    holder.start()
    try:
        assert entered.wait(1.0)
        draft = AuditEventDraft(
            event_id="auth-admin-role-open-lock", occurred_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
            actor_id="admin", actor_type=ActorType.USER, tenant_id="tenant-b",
            workspace_id="workspace-b", action="authorization.membership.changed",
            target_type="membership", target_id="target", outcome=AuditOutcome.SUCCEEDED,
            trace_id="not-recorded", policy_version="not-recorded",
            before={"role": "viewer", "version": 1}, after={"role": "editor", "version": 2},
            metadata={"reason_code": "ACCESS_REVIEW", "outcome": "changed", "acl_version": 3},
        )
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            store._append_admin_role(draft)
        assert time.monotonic() - started < 0.2
    finally:
        release.set()
        holder.join(1.0)


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
