from __future__ import annotations

import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from daon_user_api.admin_users import AdminUserService
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import (
    DevicePlatform,
    IdentityError,
    IdentityPrincipal,
    IdentityService,
    PASSWORD_HASHER,
)
from daon_user_api.identity_postgres import PostgresIdentityRepository
from test_identity_support import POLICY_VERSION, TRACE_ID


@pytest.mark.skipif(
    not os.environ.get("DAON_TEST_POSTGRES_DSN"),
    reason="isolated PostgreSQL test DSN is not configured",
)
def test_postgres_admin_recovery_and_user_suspension_revoke_sessions() -> None:
    repository = PostgresIdentityRepository(os.environ["DAON_TEST_POSTGRES_DSN"])
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(
        repository=repository,
        audit_store=audit,
        oidc_policies=(),
        clock=clock,
    )
    try:
        identity.ensure_initial_admin()
        identity.reset_initial_admin_password(
            trace_id="trace-postgres-admin-recovery", policy_version=POLICY_VERSION,
        )
        user_password = secrets.token_urlsafe(24)
        with repository.transaction() as connection:
            repository._ensure_tenant(connection, "postgres-user")
            connection.execute(
                "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
                "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "postgres-user", "local", "postgres-user", "postgres-user",
                    "postgres-user@example.test", PASSWORD_HASHER.hash(user_password),
                    datetime.now(timezone.utc), False, "active",
                ),
            )
            connection.execute(
                "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
                ("postgres-user", "postgres-user", "personal_owner"),
            )
        credentials = identity.local_login(
            login_id="postgres-user",
            password=user_password,
            platform=DevicePlatform.WEB,
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        service = AdminUserService(
            repository=repository,
            audit_store=audit,
            system_admin_user_ids=frozenset({"admin"}),
            clock=clock,
        )
        result = service.change_state(
            IdentityPrincipal("admin", "admin-session", "admin-device", "admin"),
            user_id="postgres-user",
            state="suspended",
            idempotency_key="postgres-suspend-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        assert result.user.state == "suspended"
        with repository.transaction() as connection:
            intent = connection.execute(
                "SELECT delivered_at,metadata_json FROM admin_audit_outbox "
                "WHERE operation='change_user_state' AND target_id='postgres-user'"
            ).fetchone()
        assert intent["delivered_at"] is not None
        assert "password" not in str(intent["metadata_json"]).lower()
        with pytest.raises(IdentityError) as revoked:
            identity.validate_access(
                credentials.access_token,
                trace_id=TRACE_ID,
                policy_version=POLICY_VERSION,
            )
        assert revoked.value.code == "SESSION_REVOKED"
    finally:
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("DAON_TEST_POSTGRES_DSN"),
    reason="isolated PostgreSQL test DSN is not configured",
)
def test_postgres_admin_can_create_pending_approval_user() -> None:
    repository = PostgresIdentityRepository(os.environ["DAON_TEST_POSTGRES_DSN"])
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(
        repository=repository,
        audit_store=audit,
        oidc_policies=(),
        clock=clock,
    )
    service = AdminUserService(
        repository=repository,
        audit_store=audit,
        system_admin_user_ids=frozenset({"admin"}),
        clock=clock,
    )
    principal = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
    user_id = None
    try:
        identity.ensure_initial_admin()
        created = service.create_user(
            principal,
            login_id="postgres-pending-user",
            email="postgres-pending-user@example.test",
            initial_password="initial postgres pending password",
            idempotency_key="postgres-create-pending-0001",
            trace_id=TRACE_ID,
            policy_version=POLICY_VERSION,
        )
        user_id = created.user.user_id
        assert created.user.state == "pending_approval"
    finally:
        if user_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    "DELETE FROM admin_audit_outbox WHERE target_id=?", (user_id,)
                )
                connection.execute("DELETE FROM memberships WHERE user_id=?", (user_id,))
                connection.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        repository.close()


@pytest.mark.skipif(
    not os.environ.get("DAON_TEST_POSTGRES_DSN"),
    reason="isolated PostgreSQL test DSN is not configured",
)
def test_postgres_concurrent_same_key_converges_and_different_fingerprint_fails_closed() -> None:
    dsn = os.environ["DAON_TEST_POSTGRES_DSN"]
    setup_repository = PostgresIdentityRepository(dsn)
    audit = AuditEventStore()
    normal_clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(
        repository=setup_repository, audit_store=audit, oidc_policies=(), clock=normal_clock,
    )
    identity.ensure_initial_admin()
    password = secrets.token_urlsafe(24)
    with setup_repository.transaction() as connection:
        for user_id in ("pg-concurrent", "pg-target-a", "pg-target-b"):
            setup_repository._ensure_tenant(connection, user_id)
            connection.execute(
                "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,"
                "email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, "local", user_id, user_id, f"{user_id}@example.test",
                 PASSWORD_HASHER.hash(password), datetime.now(timezone.utc), False, "active"),
            )
            connection.execute(
                "INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",
                (user_id, user_id, "personal_owner"),
            )
    credentials = identity.local_login(
        login_id="pg-concurrent", password=password, platform=DevicePlatform.WEB,
        trace_id=TRACE_ID, policy_version=POLICY_VERSION,
    )
    with setup_repository.transaction() as connection:
        connection.execute(
            "INSERT INTO refresh_families(family_id,session_id,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?)",
            ("pg-family-concurrent", credentials.session_id, "active", normal_clock(), normal_clock()),
        )
        connection.execute("CREATE TABLE admin_concurrency_probe(kind text PRIMARY KEY,count integer NOT NULL)")
        connection.execute("INSERT INTO admin_concurrency_probe VALUES ('state',0),('session',0),('refresh',0)")
        connection.execute(
            "CREATE FUNCTION count_admin_concurrency() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN UPDATE admin_concurrency_probe SET count=count+1 WHERE kind=TG_ARGV[0]; RETURN NEW; END $$"
        )
        connection.execute(
            "CREATE TRIGGER count_admin_user_state AFTER UPDATE OF state ON users "
            "FOR EACH ROW WHEN (OLD.state IS DISTINCT FROM NEW.state) EXECUTE FUNCTION count_admin_concurrency('state')"
        )
        connection.execute(
            "CREATE TRIGGER count_admin_session_revoke AFTER UPDATE OF state ON sessions "
            "FOR EACH ROW WHEN (OLD.state='active' AND NEW.state='revoked') EXECUTE FUNCTION count_admin_concurrency('session')"
        )
        connection.execute(
            "CREATE TRIGGER count_admin_refresh_revoke AFTER UPDATE OF state ON refresh_families "
            "FOR EACH ROW WHEN (OLD.state='active' AND NEW.state='revoked') EXECUTE FUNCTION count_admin_concurrency('refresh')"
        )
        connection.execute(
            "CREATE FUNCTION delay_admin_outbox_insert() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN PERFORM pg_sleep(0.25); RETURN NEW; END $$"
        )
        connection.execute(
            "CREATE TRIGGER delay_admin_outbox BEFORE INSERT ON admin_audit_outbox "
            "FOR EACH ROW EXECUTE FUNCTION delay_admin_outbox_insert()"
        )

    def run_pair(targets: tuple[str, str], key: str):
        repositories = (PostgresIdentityRepository(dsn), PostgresIdentityRepository(dsn))
        barrier = threading.Barrier(2)
        clock_state = threading.local()

        def concurrent_clock():
            if not getattr(clock_state, "synchronized", False):
                clock_state.synchronized = True
                barrier.wait(timeout=5)
            return normal_clock()

        services = tuple(
            AdminUserService(
                repository=repository, audit_store=audit,
                system_admin_user_ids=frozenset({"admin"}), clock=concurrent_clock,
            )
            for repository in repositories
        )
        principal = IdentityPrincipal("admin", "admin-session", "admin-device", "admin")
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        service.change_state, principal, user_id=target, state="suspended",
                        idempotency_key=key, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
                    )
                    for service, target in zip(services, targets, strict=True)
                ]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=10))
                    except IdentityError as error:
                        outcomes.append(error)
                return outcomes
        finally:
            for repository in repositories:
                repository.close()

    try:
        same = run_pair(("pg-concurrent", "pg-concurrent"), "postgres-concurrent-same-0001")
        assert sorted(result.replayed for result in same) == [False, True]
        assert {result.user.state for result in same} == {"suspended"}

        different = run_pair(("pg-target-a", "pg-target-b"), "postgres-concurrent-reused-0001")
        assert sum(isinstance(item, IdentityError) and item.code == "IDEMPOTENCY_KEY_REUSED" for item in different) == 1
        assert sum(not isinstance(item, IdentityError) for item in different) == 1
        with setup_repository.transaction() as connection:
            counts = {row["kind"]: row["count"] for row in connection.execute(
                "SELECT kind,count FROM admin_concurrency_probe"
            ).fetchall()}
            assert counts == {"state": 2, "session": 1, "refresh": 1}
            assert connection.execute(
                "SELECT COUNT(*) FROM admin_audit_outbox WHERE operation='change_user_state' "
                "AND target_id IN ('pg-concurrent','pg-target-a','pg-target-b')"
            ).fetchone()[0] == 2
        assert len(audit.list(tenant_id="admin", action="identity.user.state_changed").items) == 2
    finally:
        setup_repository.close()
