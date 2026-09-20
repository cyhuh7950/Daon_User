from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from daon_user_api.admin_users import AdminUserService
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import (
    DevicePlatform,
    IdentityError,
    IdentityPrincipal,
    IdentityService,
    PASSWORD_HASHER,
    SqliteIdentityRepository,
)
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


def _add_local_user(repository, *, user_id: str, password: str) -> None:
    with repository.transaction() as connection:
        repository._ensure_tenant(connection, user_id)
        connection.execute(
            "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
            "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                "local",
                user_id,
                user_id,
                f"{user_id}@example.test",
                PASSWORD_HASHER.hash(password),
                "2026-07-29T00:00:00+00:00",
                False,
                "active",
            ),
        )
        connection.execute(
            "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
            (user_id, user_id, "personal_owner"),
        )


def test_reset_initial_password_is_atomic_and_revokes_all_sessions(tmp_path: Path) -> None:
    identity, repository, audit, _clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    first = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    identity.change_current_password(
        access_token=first.access_token,
        current_password="admin",
        new_password="a strong replacement password",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    active = identity.local_login(
        login_id="admin",
        password="a strong replacement password",
        platform=DevicePlatform.WEB,
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            (
                "family-admin-recovery",
                active.session_id,
                "active",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )

    identity.reset_initial_admin_password(
        trace_id="trace-admin-recovery-001", policy_version=POLICY_VERSION,
    )

    with repository.transaction() as connection:
        user = connection.execute(
            "SELECT state,password_change_required FROM users WHERE user_id=?", ("admin",)
        ).fetchone()
        session = connection.execute(
            "SELECT state FROM sessions WHERE session_id=?", (active.session_id,)
        ).fetchone()
        family = connection.execute(
            "SELECT state FROM refresh_families WHERE family_id=?",
            ("family-admin-recovery",),
        ).fetchone()
    assert tuple(user) == ("active", 1)
    assert session["state"] == "revoked"
    assert family["state"] == "revoked"
    with pytest.raises(IdentityError) as revoked:
        identity.validate_access(
            active.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert revoked.value.code == "SESSION_REVOKED"
    recovered = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    assert recovered.user_id == "admin"
    events = audit.list(
        tenant_id="admin", action="identity.initial_admin_password.reset"
    ).items
    assert len(events) == 1
    assert events[0].metadata == {"reason_code": "SERVER_CONSOLE_RECOVERY"}


def test_reset_initial_password_commits_durable_intent_when_central_audit_fails(tmp_path: Path) -> None:
    class FailingAudit:
        def append(self, _draft: object) -> None:
            raise RuntimeError("audit unavailable")

    identity, repository, _audit, _clock = create_service(
        tmp_path / "identity.sqlite3", audit_store=FailingAudit()
    )
    identity.ensure_initial_admin()
    identity.reset_initial_admin_password(
        trace_id="trace-admin-recovery-durable", policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT password_change_required FROM users WHERE user_id='admin'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM admin_audit_outbox WHERE delivered_at IS NULL"
        ).fetchone()[0] == 1
        assert "password" not in repr(connection.execute(
            "SELECT metadata_json FROM admin_audit_outbox"
        ).fetchall()).lower()
    working_audit = AuditEventStore()
    IdentityService(
        repository=repository, audit_store=working_audit, oidc_policies=(), clock=_clock,
    )
    assert len(working_audit.list(
        tenant_id="admin", action="identity.initial_admin_password.reset"
    ).items) == 1
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM admin_audit_outbox WHERE delivered_at IS NOT NULL"
        ).fetchone()[0] == 1


def test_reset_initial_password_commit_failure_rolls_back_domain_and_intent(tmp_path: Path) -> None:
    identity, repository, audit, _clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    with repository.transaction() as connection:
        before = tuple(connection.execute(
            "SELECT password_digest,password_change_required FROM users WHERE user_id='admin'"
        ).fetchone())
    original_transaction = repository.transaction

    @contextmanager
    def failing_transaction():
        with original_transaction() as connection:
            yield connection
            raise sqlite3.OperationalError("forced pre-commit failure")

    repository.transaction = failing_transaction  # type: ignore[method-assign]
    with pytest.raises(IdentityError) as failed:
        identity.reset_initial_admin_password(
            trace_id="trace-admin-recovery-commit-failure", policy_version=POLICY_VERSION,
        )
    assert failed.value.code == "PERSISTENCE_UNAVAILABLE"
    repository.transaction = original_transaction  # type: ignore[method-assign]
    with repository.transaction() as connection:
        after = tuple(connection.execute(
            "SELECT password_digest,password_change_required FROM users WHERE user_id='admin'"
        ).fetchone())
        assert connection.execute("SELECT COUNT(*) FROM admin_audit_outbox").fetchone()[0] == 0
    assert after == before
    assert audit.list(tenant_id="admin", action="identity.initial_admin_password.reset").items == ()


def test_admin_user_state_protection_revocation_and_idempotency(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    user_password = secrets.token_urlsafe(24)
    _add_local_user(repository, user_id="user-one", password=user_password)
    target = identity.local_login(
        login_id="user-one", password=user_password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            (
                "family-user-one",
                target.session_id,
                "active",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    admin = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
    normal = IdentityPrincipal("user-one", target.session_id, target.device_id, "user-one")

    with pytest.raises(IdentityError) as forbidden:
        service.list_users(normal)
    assert forbidden.value.code == "FORBIDDEN"

    listed_users = {user.user_id: user for user in service.list_users(admin)}
    assert listed_users["admin"].email is None
    assert listed_users["user-one"].email == "user-one@example.test"

    with pytest.raises(IdentityError) as protected:
        service.change_state(
            admin,
            user_id="admin",
            state="suspended",
            idempotency_key="protect-admin-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert protected.value.code == "PROTECTED_ADMIN_ACCOUNT"
    assert next(user for user in service.list_users(admin) if user.user_id == "admin").state == "active"

    changed = service.change_state(
        admin,
        user_id="user-one",
        state="suspended",
        idempotency_key="suspend-user-one-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    replayed = service.change_state(
        admin,
        user_id="user-one",
        state="suspended",
        idempotency_key="suspend-user-one-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert changed.user.state == "suspended"
    assert changed.replayed is False
    assert replayed.user.state == "suspended"
    assert replayed.replayed is True
    with pytest.raises(IdentityError) as revoked:
        identity.validate_access(
            target.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert revoked.value.code == "SESSION_REVOKED"
    with repository.transaction() as connection:
        family = connection.execute(
            "SELECT state FROM refresh_families WHERE family_id=?", ("family-user-one",)
        ).fetchone()
    assert family["state"] == "revoked"
    events = audit.list(tenant_id="admin", action="identity.user.state_changed").items
    assert len(events) == 1
    assert events[0].before == {"state": "active"}
    assert events[0].after == {"state": "suspended"}

    with pytest.raises(IdentityError) as reused:
        service.change_state(
            admin,
            user_id="user-one",
            state="active",
            idempotency_key="suspend-user-one-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    service.change_state(
        admin,
        user_id="user-one",
        state="active",
        idempotency_key="activate-user-one-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    relogin = identity.local_login(
        login_id="user-one", password=user_password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    assert relogin.user_id == "user-one"


def test_pending_email_state_change_is_rejected_without_mutation(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    password = secrets.token_urlsafe(24)
    _add_local_user(repository, user_id="pending-user", password=password)
    target = identity.local_login(
        login_id="pending-user", password=password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE users SET state='pending_email' WHERE user_id='pending-user'"
        )
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    admin = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")

    with pytest.raises(IdentityError) as rejected:
        service.change_state(
            admin,
            user_id="pending-user",
            state="active",
            idempotency_key="pending-user-state-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )

    assert rejected.value.code == "INVALID_USER_STATE"
    assert rejected.value.http_status == 409
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT state FROM users WHERE user_id='pending-user'"
        ).fetchone()[0] == "pending_email"
        assert connection.execute(
            "SELECT state FROM sessions WHERE session_id=?", (target.session_id,)
        ).fetchone()[0] == "active"
        assert connection.execute(
            "SELECT COUNT(*) FROM admin_audit_outbox WHERE operation='change_user_state'"
        ).fetchone()[0] == 0
    assert audit.list(
        tenant_id="admin", action="identity.user.state_changed"
    ).items == ()


def test_state_change_commit_failure_rolls_back_domain_sessions_and_idempotency(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    password = secrets.token_urlsafe(24)
    _add_local_user(repository, user_id="commit-target", password=password)
    target = identity.local_login(
        login_id="commit-target", password=password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    service = AdminUserService(
        repository=repository, audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}), clock=clock,
    )
    original_transaction = repository.transaction

    @contextmanager
    def failing_transaction():
        with original_transaction() as connection:
            yield connection
            raise sqlite3.OperationalError("forced pre-commit failure")

    repository.transaction = failing_transaction  # type: ignore[method-assign]
    with pytest.raises(IdentityError) as failed:
        service.change_state(
            IdentityPrincipal("admin", "admin-session", "admin-device", "admin"),
            user_id="commit-target", state="suspended",
            idempotency_key="commit-failure-target-0001", trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert failed.value.code == "PERSISTENCE_UNAVAILABLE"
    repository.transaction = original_transaction  # type: ignore[method-assign]
    identity.validate_access(target.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    with repository.transaction() as connection:
        assert connection.execute("SELECT state FROM users WHERE user_id='commit-target'").fetchone()[0] == "active"
        assert connection.execute("SELECT COUNT(*) FROM admin_audit_outbox").fetchone()[0] == 0
    assert audit.list(tenant_id="admin", action="identity.user.state_changed").items == ()


def test_state_change_central_audit_failure_keeps_retryable_idempotent_intent(tmp_path: Path) -> None:
    class FailingAudit:
        def append(self, _draft: object) -> None:
            raise RuntimeError("audit unavailable")

    identity, repository, _audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    _add_local_user(repository, user_id="durable-target", password=secrets.token_urlsafe(24))
    service = AdminUserService(
        repository=repository, audit_store=FailingAudit(),
        system_admin_user_ids=frozenset({"admin"}), clock=clock,
    )
    principal = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
    changed = service.change_state(
        principal, user_id="durable-target", state="suspended",
        idempotency_key="durable-target-state-0001", trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert changed.replayed is False
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM admin_audit_outbox WHERE delivered_at IS NULL"
        ).fetchone()[0] == 1
    working_audit = AuditEventStore()
    retrying = AdminUserService(
        repository=repository, audit_store=working_audit,
        system_admin_user_ids=frozenset({"admin"}), clock=clock,
    )
    replay = retrying.change_state(
        principal, user_id="durable-target", state="suspended",
        idempotency_key="durable-target-state-0001", trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert replay.replayed is True
    assert len(working_audit.list(tenant_id="admin", action="identity.user.state_changed").items) == 1


@pytest.mark.parametrize("corrupted_state", ["active", "pending_email"])
def test_corrupted_initial_admin_is_never_changed_by_second_system_admin(
    tmp_path: Path, corrupted_state: str,
) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    session = identity.local_login(
        login_id="admin", password="admin", platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE users SET subject='corrupted-admin',state=? WHERE user_id='admin'",
            (corrupted_state,),
        )
    service = AdminUserService(
        repository=repository, audit_store=audit,
        system_admin_user_ids=frozenset({"admin", "second-admin"}), clock=clock,
    )
    with pytest.raises(IdentityError) as conflict:
        service.change_state(
            IdentityPrincipal("second-admin", "second-session", "second-device", "second"),
            user_id="admin", state="suspended", idempotency_key="corrupt-admin-guard-0001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert conflict.value.code == "INITIAL_ADMIN_CONFLICT"
    assert conflict.value.http_status == 503
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT state FROM users WHERE user_id='admin'"
        ).fetchone()[0] == corrupted_state
        assert connection.execute("SELECT state FROM sessions WHERE session_id=?", (session.session_id,)).fetchone()[0] == "active"
        assert connection.execute("SELECT COUNT(*) FROM admin_audit_outbox").fetchone()[0] == 0
    assert audit.list(tenant_id="second", action="identity.user.state_changed").items == ()


def test_concurrent_same_idempotency_key_converges_and_mutates_once_sqlite(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.sqlite3"
    identity, first_repository, audit, base_clock = create_service(database_path)
    identity.ensure_initial_admin()
    password = secrets.token_urlsafe(24)
    _add_local_user(first_repository, user_id="concurrent-target", password=password)
    target = identity.local_login(
        login_id="concurrent-target", password=password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    with first_repository.transaction() as connection:
        connection.execute(
            "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            ("family-concurrent", target.session_id, "active", "2026-07-29T00:00:00+00:00", "2026-07-29T00:00:00+00:00"),
        )
        connection.execute("CREATE TABLE concurrency_probe(kind TEXT PRIMARY KEY,count INTEGER NOT NULL)")
        connection.execute("INSERT INTO concurrency_probe VALUES ('state',0),('session',0),('refresh',0)")
        connection.execute(
            "CREATE TRIGGER count_state_change AFTER UPDATE OF state ON users "
            "WHEN OLD.state<>NEW.state BEGIN UPDATE concurrency_probe SET count=count+1 WHERE kind='state'; END"
        )
        connection.execute(
            "CREATE TRIGGER count_session_revoke AFTER UPDATE OF state ON sessions "
            "WHEN OLD.state='active' AND NEW.state='revoked' BEGIN UPDATE concurrency_probe SET count=count+1 WHERE kind='session'; END"
        )
        connection.execute(
            "CREATE TRIGGER count_refresh_revoke AFTER UPDATE OF state ON refresh_families "
            "WHEN OLD.state='active' AND NEW.state='revoked' BEGIN UPDATE concurrency_probe SET count=count+1 WHERE kind='refresh'; END"
        )
    second_repository = SqliteIdentityRepository(database_path)
    barrier = threading.Barrier(2)
    clock_state = threading.local()

    def concurrent_clock():
        if not getattr(clock_state, "synchronized", False):
            clock_state.synchronized = True
            barrier.wait(timeout=5)
        return base_clock()

    services = tuple(
        AdminUserService(
            repository=repository, audit_store=audit,
            system_admin_user_ids=frozenset({"admin"}), clock=concurrent_clock,
        )
        for repository in (first_repository, second_repository)
    )
    principal = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    service.change_state, principal, user_id="concurrent-target",
                    state="suspended", idempotency_key="concurrent-same-key-0001",
                    trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                )
                for service in services
            ]
            results = [future.result(timeout=10) for future in futures]
        assert sorted(result.replayed for result in results) == [False, True]
        assert {result.user.state for result in results} == {"suspended"}
        with first_repository.transaction() as connection:
            assert dict(connection.execute("SELECT kind,count FROM concurrency_probe")) == {
                "state": 1, "session": 1, "refresh": 1,
            }
            assert connection.execute(
                "SELECT COUNT(*) FROM admin_audit_outbox WHERE operation='change_user_state'"
            ).fetchone()[0] == 1
        assert len(audit.list(tenant_id="admin", action="identity.user.state_changed").items) == 1
    finally:
        second_repository.close()


def test_concurrent_reused_key_with_different_fingerprint_fails_closed_sqlite(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent-reused.sqlite3"
    identity, first_repository, audit, base_clock = create_service(database_path)
    identity.ensure_initial_admin()
    _add_local_user(first_repository, user_id="target-a", password=secrets.token_urlsafe(24))
    _add_local_user(first_repository, user_id="target-b", password=secrets.token_urlsafe(24))
    second_repository = SqliteIdentityRepository(database_path)
    barrier = threading.Barrier(2)
    clock_state = threading.local()

    def concurrent_clock():
        if not getattr(clock_state, "synchronized", False):
            clock_state.synchronized = True
            barrier.wait(timeout=5)
        return base_clock()

    services = tuple(
        AdminUserService(
            repository=repository, audit_store=audit,
            system_admin_user_ids=frozenset({"admin"}), clock=concurrent_clock,
        )
        for repository in (first_repository, second_repository)
    )
    principal = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    service.change_state, principal, user_id=target, state="suspended",
                    idempotency_key="concurrent-reused-key-0001", trace_id=TRACE_ID,
                    policy_version=POLICY_VERSION,
                )
                for service, target in zip(services, ("target-a", "target-b"), strict=True)
            ]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=10))
                except IdentityError as error:
                    outcomes.append(error)
        assert sum(isinstance(item, IdentityError) and item.code == "IDEMPOTENCY_KEY_REUSED" for item in outcomes) == 1
        assert sum(not isinstance(item, IdentityError) for item in outcomes) == 1
        with first_repository.transaction() as connection:
            states = [row[0] for row in connection.execute(
                "SELECT state FROM users WHERE user_id IN ('target-a','target-b') ORDER BY user_id"
            ).fetchall()]
            assert states.count("suspended") == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM admin_audit_outbox WHERE operation='change_user_state'"
            ).fetchone()[0] == 1
        assert len(audit.list(tenant_id="admin", action="identity.user.state_changed").items) == 1
    finally:
        second_repository.close()


def test_admin_cli_uses_fixed_target_and_never_echoes_extra_arguments(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    identity, repository, _audit, _clock = create_service(database_path)
    identity.ensure_initial_admin()
    repository.close()
    environment = os.environ.copy()
    environment.update({
        "DAON_RUNTIME_PROFILE": "development",
        "DAON_API_DATABASE_PATH": str(database_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    })

    completed = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0
    assert completed.stdout == "INITIAL_ADMIN_PASSWORD_RESET\n"
    assert completed.stderr == ""

    marker = "sensitive-extra-argument"
    rejected = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password", marker],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert rejected.returncode == 2
    assert marker not in rejected.stdout
    assert marker not in rejected.stderr


def test_admin_cli_fails_closed_when_canonical_admin_is_missing(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    identity, repository, _audit, _clock = create_service(database_path)
    identity.ensure_initial_admin()
    with repository.transaction() as connection:
        connection.execute("DELETE FROM memberships WHERE user_id=?", ("admin",))
        connection.execute("DELETE FROM users WHERE user_id=?", ("admin",))
    repository.close()
    environment = os.environ.copy()
    environment.update({
        "DAON_RUNTIME_PROFILE": "development",
        "DAON_API_DATABASE_PATH": str(database_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    })

    completed = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "INITIAL_ADMIN_PASSWORD_RESET_FAILED:INITIAL_ADMIN_MISSING\n"


@pytest.mark.parametrize("kind", ["absent", "empty"])
def test_admin_cli_empty_database_fails_without_mutation(tmp_path: Path, kind: str) -> None:
    database_path = tmp_path / "empty.sqlite3"
    if kind == "empty":
        database_path.touch()
    before = database_path.read_bytes() if database_path.exists() else None
    before_entries = {path.name for path in tmp_path.iterdir()}
    environment = os.environ.copy()
    environment.update({
        "DAON_RUNTIME_PROFILE": "development",
        "DAON_API_DATABASE_PATH": str(database_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    })
    completed = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password"],
        capture_output=True, text=True, check=False, env=environment,
    )
    assert completed.returncode == 1
    assert completed.stderr == "INITIAL_ADMIN_PASSWORD_RESET_FAILED:INITIAL_ADMIN_MISSING\n"
    assert (database_path.read_bytes() if database_path.exists() else None) == before
    assert {path.name for path in tmp_path.iterdir()} == before_entries


def test_admin_cli_canonical_mismatch_fails_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "mismatch.sqlite3"
    identity, repository, _audit, _clock = create_service(database_path)
    identity.ensure_initial_admin()
    with repository.transaction() as connection:
        connection.execute("UPDATE users SET subject='mismatch' WHERE user_id='admin'")
    repository.close()
    before = database_path.read_bytes()
    before_entries = {path.name for path in tmp_path.iterdir()}
    environment = os.environ.copy()
    environment.update({
        "DAON_RUNTIME_PROFILE": "development", "DAON_API_DATABASE_PATH": str(database_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    })
    completed = subprocess.run(
        [sys.executable, "-m", "daon_user_api.admin_cli", "reset-initial-password"],
        capture_output=True, text=True, check=False, env=environment,
    )
    assert completed.returncode == 1
    assert completed.stderr == "INITIAL_ADMIN_PASSWORD_RESET_FAILED:INITIAL_ADMIN_CONFLICT\n"
    assert database_path.read_bytes() == before
    assert {path.name for path in tmp_path.iterdir()} == before_entries
def test_admin_can_register_update_approve_and_delete_general_user(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    admin = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")

    created = service.create_user(
        admin,
        login_id="managed-user",
        email="managed@example.test",
        initial_password="initial managed password",
        idempotency_key="register-managed-user-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert created.user.login_id == "managed-user"
    assert created.user.email == "managed@example.test"
    assert created.user.state == "pending_approval"
    assert created.user.protected is False

    updated = service.update_user(
        admin,
        user_id=created.user.user_id,
        email="managed.updated@example.test",
        idempotency_key="update-managed-user-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert updated.user.email == "managed.updated@example.test"
    assert updated.user.state == "pending_approval"

    approved = service.approve_user(
        admin,
        user_id=created.user.user_id,
        idempotency_key="approve-managed-user-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert approved.user.state == "active"
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT email_verified_at FROM users WHERE user_id=?",
            (created.user.user_id,),
        ).fetchone()[0] is not None
    credentials = identity.local_login(
        login_id="managed-user",
        password="initial managed password",
        platform=DevicePlatform.WEB,
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert credentials.user_id == created.user.user_id

    deleted = service.delete_user(
        admin,
        user_id=created.user.user_id,
        idempotency_key="delete-managed-user-0001",
        trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert deleted.replayed is False
    with repository.transaction() as connection:
        assert connection.execute(
            "SELECT 1 FROM users WHERE user_id=?", (created.user.user_id,)
        ).fetchone() is None
    assert audit.list(tenant_id="admin", action="identity.user.deleted").items


def test_admin_cannot_delete_initial_admin(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    admin = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")

    with pytest.raises(IdentityError) as error:
        service.delete_user(
            admin,
            user_id="admin",
            idempotency_key="delete-initial-admin-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
    assert error.value.code == "PROTECTED_ADMIN_ACCOUNT"


def test_admin_crud_mutations_replay_and_reject_key_reuse(tmp_path: Path) -> None:
    identity, repository, audit, clock = create_service(tmp_path / "identity.sqlite3")
    identity.ensure_initial_admin()
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    admin = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")

    created = service.create_user(
        admin, login_id="replay-user", email="replay@example.test",
        initial_password="initial replay password", idempotency_key="create-replay-user-001",
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    replayed_create = service.create_user(
        admin, login_id="replay-user", email="replay@example.test",
        initial_password="different password", idempotency_key="create-replay-user-001",
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    assert replayed_create.replayed is True
    assert replayed_create.user.user_id == created.user.user_id
    with pytest.raises(IdentityError) as reused_create:
        service.create_user(
            admin, login_id="other-user", email="other@example.test",
            initial_password="different password", idempotency_key="create-replay-user-001",
            trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
    assert reused_create.value.code == "IDEMPOTENCY_KEY_REUSED"

    updated = service.update_user(
        admin, user_id=created.user.user_id, email="replay.updated@example.test",
        idempotency_key="update-replay-user-001", trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    replayed_update = service.update_user(
        admin, user_id=created.user.user_id, email="replay.updated@example.test",
        idempotency_key="update-replay-user-001", trace_id=TRACE_ID,
        policy_version=POLICY_VERSION,
    )
    assert updated.replayed is False
    assert replayed_update.replayed is True
    assert replayed_update.user.email == "replay.updated@example.test"

    deleted = service.delete_user(
        admin, user_id=created.user.user_id, idempotency_key="delete-replay-user-001",
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    replayed_delete = service.delete_user(
        admin, user_id=created.user.user_id, idempotency_key="delete-replay-user-001",
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    assert deleted.replayed is False
    assert replayed_delete.replayed is True
