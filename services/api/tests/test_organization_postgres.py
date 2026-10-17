"""S39 저장 기반: 로컬 실행 경계와 명시 격리 DSN의 실제 PostgreSQL 시험."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql


VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _migration():
    path = VERSIONS / "0068_organization_management_operations.py"
    assert path.is_file(), "0068 저장 기반 migration이 필요합니다"
    spec = importlib.util.spec_from_file_location("s39_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0068_upgrade_sends_exact_fixed_sql_without_bind_parameters():
    module = _migration()
    calls = []
    with patch.object(module.op, "execute", side_effect=calls.append):
        module.upgrade()
    assert module.revision == "0068" and module.down_revision == "0067"
    assert len(calls) == 1
    assert calls[0] == (VERSIONS / "0068_organization_management_operations.sql").read_text(encoding="utf-8")
    assert text(calls[0]).compile(dialect=postgresql.dialect()).params == {}


def test_0068_downgrade_keeps_single_guarded_transaction_and_has_no_bind_parameters():
    module = _migration()
    calls = []
    with patch.object(module.op, "execute", side_effect=calls.append):
        module.downgrade()
    assert len(calls) == 1
    assert text(calls[0]).compile(dialect=postgresql.dialect()).params == {}


def test_organization_pg_fixture_rejects_non_dedicated_database_before_connect():
    from psycopg.conninfo import conninfo_to_dict
    for dsn in ("dbname=daon_user", "dbname=postgres", "dbname=daon_user_s39_qa_", "dbname=daon_user_s39_qa_bad-name"):
        assert not _isolated_database_name(conninfo_to_dict(dsn).get("dbname", ""))
    assert _isolated_database_name("daon_user_s39_qa_20261017")


def _isolated_database_name(name):
    import re
    return re.fullmatch(r"daon_user_s39_qa_[a-z0-9_]+", name) is not None


def test_l2_adapter_maps_new_receipt_table_without_changing_legacy_history():
    from daon_user_api.postgres_adapters import PostgresCompatConnection
    connection = PostgresCompatConnection.__new__(PostgresCompatConnection)
    connection._prefixes = ("organization_",)
    assert connection._sql("SELECT * FROM organization_operation_outbox WHERE actor_id=?") == "SELECT * FROM identity_org_operation_outbox WHERE actor_id=%s"
    assert connection._sql("SELECT * FROM tenant_membership_role_history") == "SELECT * FROM identity_org_role_history"


@pytest.mark.parametrize("existing_options", ["", "-c role=daon_app -c application_name=s39-local-options -c statement_timeout=99000 -c lock_timeout=99000"])
def test_l2_audit_connection_preserves_dsn_options_and_appends_bounded_timeouts(existing_options):
    from threading import RLock
    from types import SimpleNamespace
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from daon_user_api import postgres_adapters
    effective = []
    closed = []
    def transport(dsn, **kwargs):
        # 실제 PG 접속은 하지 않고 실제 helper/compat/libpq 연결 인자 경계를 소비한다.
        effective.append(conninfo_to_dict(make_conninfo(dsn, **{key: value for key, value in kwargs.items() if key != "row_factory"})))
        return SimpleNamespace(execute=lambda *_args: SimpleNamespace(fetchone=lambda: (1,)), close=lambda: closed.append(True))
    repository = postgres_adapters.PostgresOrganizationRepository.__new__(postgres_adapters.PostgresOrganizationRepository)
    repository._dsn = make_conninfo(dbname="s39_local_no_connection", options=existing_options)
    repository._lock = RLock()
    with patch.object(postgres_adapters.psycopg, "connect", transport):
        with repository._management_audit_transaction() as connection:
            assert connection.execute("SELECT 1").fetchone() == (1,)
    assert len(effective) == 1 and closed == [True]
    options = effective[0]["options"]
    assert options == (existing_options + " -c statement_timeout=5000 -c lock_timeout=2000").strip()
    assert effective[0]["connect_timeout"] == "2"


def test_0068_protection_probe_rejects_missing_catalog_checks_or_tampered_functions():
    from daon_user_api.postgres_adapters import organization_management_storage_is_protected
    import hashlib
    import re
    source = (VERSIONS / "0068_organization_management_operations.sql").read_text(encoding="utf-8")
    hashes = {name: hashlib.md5(body.encode()).hexdigest() for name, body in re.findall(r"CREATE FUNCTION public\.(\w+).*?AS \$fn\$(.*?)\$fn\$;", source, re.S)}
    functions = []
    for name, digest in hashes.items():
        functions.append((name, digest, "sql" if name == "pending_identity_org_audits" else "plpgsql",
                          "i" if name == "valid_identity_org_operation" else "s" if name == "pending_identity_org_audits" else "v",
                          name in {"pending_identity_org_audits", "ack_identity_org_audit"},
                          "daon_organization_owner", ["search_path=pg_catalog, pg_temp"], False,
                          name in {"valid_identity_org_operation", "pending_identity_org_audits", "ack_identity_org_audit"}))
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows
    class Connection:
        def __init__(self, checks, rows, constraints=None):
            self.results = iter([Result([(checks,)]), Result(rows), Result(constraints if constraints is not None else constraint_rows)])
        def execute(self, *_args): return next(self.results)
    constraint_rows = [
        ("CHECK ((actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",),
        ("CHECK ((operation = ANY (ARRAY['organization.join.create'::text, 'organization.join.decide'::text, 'organization.member.role'::text, 'organization.member.state'::text, 'organization.invitation.create'::text, 'organization.invitation.revoke'::text])))",),
        ("CHECK (((char_length(idempotency_key) >= 16) AND (char_length(idempotency_key) <= 128)))",),
        ("CHECK ((tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",),
        ("CHECK ((target_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",),
        ("CHECK ((request_fingerprint ~ '^[0-9a-f]{64}$'::text))",),
        ("CHECK ((event_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'::text))",),
        ("CHECK (valid_identity_org_operation(operation, response_json, audit_json))",),
        ("""CHECK (((((response_json)::jsonb ->> 'tenant_id'::text) = tenant_id) AND
 (CASE WHEN (operation = ANY (ARRAY['organization.join.create'::text, 'organization.join.decide'::text])) THEN ((response_json)::jsonb ->> 'request_id'::text)
 WHEN (operation = ANY (ARRAY['organization.invitation.create'::text, 'organization.invitation.revoke'::text])) THEN ((response_json)::jsonb ->> 'invitation_id'::text)
 ELSE ((response_json)::jsonb ->> 'user_id'::text) END = target_id)))""",),
    ]
    assert organization_management_storage_is_protected(Connection([True] * 9, functions))
    for index in range(len(constraint_rows)):
        altered = constraint_rows[:index] + [("CHECK (true)",)] + constraint_rows[index+1:]
        assert not organization_management_storage_is_protected(Connection([True] * 9, functions, altered))
    for index in range(9):
        checks = [True] * 9; checks[index] = False
        assert not organization_management_storage_is_protected(Connection(checks, functions))
    assert not organization_management_storage_is_protected(Connection([True] * 8, functions))
    for index in range(len(functions)):
        assert not organization_management_storage_is_protected(Connection([True] * 9, functions[:index] + functions[index+1:]))
        for field, invalid in ((1, "changed-body"), (2, "sql-unknown"), (4, not functions[index][4]), (5, "daon_app"), (6, ["search_path=public"]), (7, True), (8, not functions[index][8])):
            altered = list(functions[index]); altered[field] = invalid
            assert not organization_management_storage_is_protected(Connection([True] * 9, functions[:index] + [tuple(altered)] + functions[index+1:]))


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="S39 전용 격리 PostgreSQL DSN 필요 — 실제 DDL 미실행")
def test_0068_isolated_storage_migration_scope_immutability_and_readiness(monkeypatch):
    import hashlib
    import json
    import secrets
    from datetime import datetime, timezone
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from alembic import command
    from alembic.config import Config
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.postgres_adapters import organization_management_storage_is_protected
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.identity import DevicePlatform, IdentityService, PASSWORD_HASHER
    from daon_user_api.identity_postgres import PostgresIdentityRepository

    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if not _isolated_database_name(conninfo_to_dict(dsn).get("dbname", "")):
        pytest.fail("S39_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        if not _isolated_database_name(connection.info.dbname): pytest.fail("S39_ISOLATED_DATABASE_REQUIRED", pytrace=False)
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
        assert connection.execute("SELECT to_regclass('public.identity_org_operation_outbox')").fetchone() == (None,)
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    repository = PostgresIdentityRepository(app_dsn)
    audit_store = AuditEventStore()
    password = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    try:
        with repository.transaction() as connection:
            repository._ensure_tenant(connection, "s39-legacy-user")
            connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                ("s39-legacy-user", "local", "s39-legacy-user", "s39-legacy-login", "s39-legacy@example.test", PASSWORD_HASHER.hash(password), now, False, "active"))
            connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)", ("s39-legacy-user", "s39-legacy-user", "personal_owner"))
        identity = IdentityService(repository=repository, audit_store=audit_store, oidc_policies=(), clock=lambda: now)
        identity.local_login(login_id="s39-legacy-login", password=password, platform=DevicePlatform.WEB, trace_id="s39-trace", policy_version="s39-policy")
    finally:
        repository.close()
    with psycopg.connect(dsn) as connection:
        now = datetime.now(timezone.utc)
        connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,'organization','cloud_sync',1000,1,1,%s)", ("s39-w-a", "s39-tenant-a", now))
        connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,'organization','cloud_sync',1000,1,1,%s)", ("s39-w-b", "s39-tenant-b", now))
        connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,'personal','cloud_sync',1000,1,1,%s)", ("s39-w-personal", "s39-tenant-a", now))
        connection.execute("INSERT INTO public.identity_org_invitation_codes VALUES ('s39-legacy-inv','s39-tenant-a','s39-admin','digest-test',%s,1,0,'active',1,%s,%s)", (now, now, now))
        connection.execute("INSERT INTO public.identity_org_tenant_memberships VALUES ('s39-tenant-a','s39-legacy-user','member','active',1,%s)", (now,))
        connection.execute("INSERT INTO public.identity_org_join_requests VALUES ('s39-legacy-request','s39-tenant-a','s39-legacy-user','s39-legacy-inv','pending',NULL,NULL,1,%s,%s)", (now, now))
        index_oid = connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone()[0]
        old_invitation = connection.execute("SELECT to_jsonb(r) FROM public.identity_org_invitation_codes r ORDER BY invitation_id").fetchall()
        old_member = connection.execute("SELECT to_jsonb(r) FROM public.identity_org_tenant_memberships r ORDER BY tenant_id,user_id").fetchall()
        old_metadata = connection.execute("SELECT relname,pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('public.identity_org_invitation_codes'::regclass,'public.identity_org_tenant_memberships'::regclass,'public.identity_org_join_requests'::regclass,'public.identity_users'::regclass,'public.identity_sessions'::regclass,'public.identity_admin_audit_outbox'::regclass) ORDER BY relname").fetchall()
        old_users = connection.execute("SELECT to_jsonb(r) FROM public.identity_users r ORDER BY user_id").fetchall()
        old_sessions = connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall()
        assert old_users and old_sessions
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    config = Config(str(VERSIONS.parents[1] / "alembic.ini"))
    command.upgrade(config, "0068")
    command.downgrade(config, "0067")
    command.upgrade(config, "0068")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone()[0] == index_oid
        assert connection.execute("SELECT to_jsonb(r)-'workspace_id' FROM public.identity_org_invitation_codes r ORDER BY invitation_id").fetchall() == old_invitation
        assert connection.execute("SELECT to_jsonb(r)-'join_workspace_id' FROM public.identity_org_tenant_memberships r ORDER BY tenant_id,user_id").fetchall() == old_member
        assert connection.execute("SELECT workspace_id FROM public.identity_org_invitation_codes WHERE invitation_id='s39-legacy-inv'").fetchone() == (None,)
        assert connection.execute("SELECT join_workspace_id FROM public.identity_org_tenant_memberships WHERE user_id='s39-legacy-user'").fetchone() == (None,)
        assert connection.execute("SELECT relname,pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('public.identity_org_invitation_codes'::regclass,'public.identity_org_tenant_memberships'::regclass,'public.identity_org_join_requests'::regclass,'public.identity_users'::regclass,'public.identity_sessions'::regclass,'public.identity_admin_audit_outbox'::regclass) ORDER BY relname").fetchall() == old_metadata
        assert organization_management_storage_is_protected(connection)
        for tamper in (
            "ALTER TABLE public.identity_org_operation_outbox DISABLE ROW LEVEL SECURITY",
            "ALTER TABLE public.identity_org_operation_outbox NO FORCE ROW LEVEL SECURITY",
            "ALTER TABLE public.identity_org_operation_outbox OWNER TO daon_app",
            "GRANT DELETE ON public.identity_org_operation_outbox TO daon_app",
            "GRANT UPDATE(response_json) ON public.identity_org_operation_outbox TO daon_app",
            "ALTER TABLE public.identity_org_invitation_codes DISABLE TRIGGER identity_org_invitation_workspace_guard",
            "ALTER FUNCTION public.pending_identity_org_audits(integer) SET search_path=public",
            "GRANT EXECUTE ON FUNCTION public.pending_identity_org_audits(integer) TO PUBLIC",
            "ALTER FUNCTION public.ack_identity_org_audit(text) SECURITY INVOKER",
            "ALTER TABLE public.identity_org_operation_outbox DROP CONSTRAINT identity_org_operation_payload, ADD CONSTRAINT identity_org_operation_payload CHECK(true)",
            "DROP INDEX public.identity_org_join_pending_unique",
        ):
            connection.execute("SAVEPOINT protection_check")
            connection.execute(tamper)
            assert not organization_management_storage_is_protected(connection), tamper
            connection.execute("ROLLBACK TO SAVEPOINT protection_check")
            assert organization_management_storage_is_protected(connection)
    for enabled in (False, True):
        store = PostgresCloudStore(app_dsn, organization_admin_v2_enabled=enabled)
        try: assert store.readiness().ready
        finally: store.close()
    def scope(connection, tenant="s39-tenant-a", actor="s39-actor"):
        connection.execute("SELECT set_config('daon.organization_actor',%s,true),set_config('daon.organization_tenant',%s,true)", (actor, tenant))
    response = dict(request_id="s39-request", tenant_id="s39-tenant-a", user_id="s39-user", invitation_id=None,
                    state="pending", decision_reason=None, decided_by=None, version=1, created_at=now.isoformat(), updated_at=now.isoformat())
    audit = dict(action="organization.join_request.created", workspace_id="s39-w-a", before=None, after="pending", reason=None, version=1, auth_version=None)
    for change in ({"code_digest": "must-not-store"}, {"access_credential": "must-not-store"}, {"created_at": "2026-10-17"}, {"created_at": None}, {"version": True}, {"state": {"secret": "must-not-store"}}):
        with psycopg.connect(app_dsn) as connection:
            assert not connection.execute("SELECT public.valid_identity_org_operation(%s,%s,%s)", ("organization.join.create", json.dumps(response | change), json.dumps(audit))).fetchone()[0]
    for altered in (audit | {"action": None}, audit | {"reason": {"secret": "must-not-store"}}, audit | {"unknown": "must-not-store"}):
        with psycopg.connect(app_dsn) as connection:
            assert not connection.execute("SELECT public.valid_identity_org_operation(%s,%s,%s)", ("organization.join.create", json.dumps(response), json.dumps(altered))).fetchone()[0]
    with psycopg.connect(app_dsn) as connection:
        scope(connection)
        for index in range(101):
            connection.execute("INSERT INTO public.identity_org_operation_outbox VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
                ("s39-actor", "organization.join.create", f"s39-key-{index:016d}", "s39-tenant-a", "s39-request", hashlib.sha256(f"s39-{index}".encode()).hexdigest(), json.dumps(response), f"s39-event-{index:03d}", json.dumps(audit), "s39-trace", "s39-policy", now))
        assert connection.execute("SELECT count(*) FROM public.identity_org_operation_outbox").fetchone() == (101,)
        for limit, expected in ((None, 0), (-1, 0), (0, 0), (1, 1), (10000, 100)):
            rows = connection.execute("SELECT * FROM public.pending_identity_org_audits(%s)", (limit,)).fetchall()
            assert len(rows) == expected
            assert all(len(row) == 9 for row in rows)
        assert connection.execute("SELECT public.ack_identity_org_audit('s39-event-000')").fetchone() == (True,)
        assert connection.execute("SELECT public.ack_identity_org_audit('s39-event-000')").fetchone() == (False,)
    for invalid_sql in ("DELETE FROM public.identity_org_operation_outbox", "UPDATE public.identity_org_operation_outbox SET delivered_at=now()", "UPDATE public.identity_org_operation_outbox SET response_json='{}'"):
        with pytest.raises(psycopg.Error):
            with psycopg.connect(app_dsn) as connection:
                scope(connection); connection.execute(invalid_sql)
    for wrong_workspace in ("s39-w-b", "s39-w-personal", "s39-w-missing"):
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(app_dsn) as connection:
                connection.execute("UPDATE public.identity_org_invitation_codes SET workspace_id=%s WHERE invitation_id='s39-legacy-inv'", (wrong_workspace,))
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(app_dsn) as connection:
                connection.execute("UPDATE public.identity_org_tenant_memberships SET join_workspace_id=%s WHERE user_id='s39-legacy-user'", (wrong_workspace,))
    with psycopg.connect(app_dsn) as connection:
        connection.execute("UPDATE public.identity_org_invitation_codes SET workspace_id='s39-w-a' WHERE invitation_id='s39-legacy-inv'")
        connection.execute("UPDATE public.identity_org_tenant_memberships SET join_workspace_id='s39-w-a' WHERE user_id='s39-legacy-user'")
    for immutable_update in (
        "UPDATE public.identity_org_invitation_codes SET workspace_id=NULL WHERE invitation_id='s39-legacy-inv'",
        "UPDATE public.identity_org_tenant_memberships SET join_workspace_id=NULL WHERE user_id='s39-legacy-user'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(app_dsn) as connection:
                connection.execute(immutable_update)
    with psycopg.connect(app_dsn) as connection:
        scope(connection, tenant="s39-tenant-b")
        assert connection.execute("SELECT * FROM public.identity_org_operation_outbox").fetchall() == []
        scope(connection, actor="s39-other-actor")
        assert connection.execute("SELECT * FROM public.identity_org_operation_outbox").fetchall() == []
    with pytest.raises(psycopg.Error):
        with psycopg.connect(app_dsn) as connection:
            scope(connection, actor="s39-other-actor")
            connection.execute("INSERT INTO public.identity_org_operation_outbox VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)", ("s39-actor", "organization.join.create", "s39-other-key-000000", "s39-tenant-a", "s39-request", "a"*64, json.dumps(response), "s39-other-event", json.dumps(audit), "s39-trace", "s39-policy", now))
    with pytest.raises(Exception, match="ORGANIZATION_FORWARD_RECOVERY_REQUIRED"):
        command.downgrade(config, "0067")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("0068",)
        assert connection.execute("SELECT count(*) FROM public.identity_org_operation_outbox").fetchone() == (101,)
        assert connection.execute("SELECT to_jsonb(r) FROM public.identity_users r ORDER BY user_id").fetchall() == old_users
        assert connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall() == old_sessions


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="S39 전용 격리 DSN 필요 — L2 실제 PG 경합 미실행")
def test_l2_isolated_atomic_operations_two_connections_and_durable_audits(monkeypatch):
    """L1과 별도 선택한다. 동일 승인 자원을 fresh0067로 재생성한 뒤 한 번 실행한다."""
    import hashlib
    import json
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import asdict
    from datetime import datetime, timedelta, timezone
    from threading import Barrier
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from alembic import command
    from alembic.config import Config
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import MINIMUM_STEP_UP_ACTION_GROUPS
    from daon_user_api.organization_membership import OrganizationWorkflowError
    from daon_user_api.postgres_adapters import PostgresOrganizationRepository

    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s39_qa_20261017":
        pytest.fail("S39_EXACT_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    # J1 이후의 정상 사용자/기존 조직 전제만 합성한다. 원본 사용자·세션은 복제하지 않는다.
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
        connection.execute("INSERT INTO public.identity_tenants(tenant_id) VALUES ('s39-l2-tenant')")
        for action in MINIMUM_STEP_UP_ACTION_GROUPS:
            connection.execute("INSERT INTO public.identity_tenant_step_up_actions VALUES ('s39-l2-tenant',%s,1)", (action,))
        for user in ("s39-l2-user-double", "s39-l2-limit-user-0", "s39-l2-limit-user-1", "s39-l2-user-revoke"):
            connection.execute("INSERT INTO public.identity_users(user_id,issuer,subject,login_id,email,email_verified_at,password_change_required,state) VALUES (%s,'local',%s,%s,%s,%s,false,'active')",
                (user, user, user+"-login", user+"@example.test", datetime.now(timezone.utc)))
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s39_qa_20261017"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
        assert connection.execute("SELECT to_regclass('public.identity_org_operation_outbox')").fetchone() == (None,)
        old_users = connection.execute("SELECT to_jsonb(r) FROM public.identity_users r ORDER BY user_id").fetchall()
        old_sessions = connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall()
        index_oid = connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone()[0]
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    command.upgrade(Config(str(VERSIONS.parents[1] / "alembic.ini")), "0068")
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    store = PostgresSecurityAuditStore(dsn)
    repositories = [PostgresOrganizationRepository(app_dsn, audit_store=store) for _ in range(2)]
    for repository in repositories:
        with repository._management_audit_transaction() as connection:
            scope = connection.execute("SELECT current_user AS effective_user,current_setting('statement_timeout') AS statement_timeout,current_setting('lock_timeout') AS lock_timeout").fetchone()
            assert (scope[0], scope[1], scope[2]) == ("daon_app", "5s", "2s")
    tenant, workspace, actor = "s39-l2-tenant", "s39-l2-workspace", "s39-l2-admin"
    with psycopg.connect(app_dsn) as connection:
        connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,'organization','cloud_sync',1000,1,1,%s)", (workspace, tenant, datetime.now(timezone.utc)))
    now = datetime.now(timezone.utc)

    def run(repository, operation, target, payload, key, user=actor):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return repository.execute_management_operation(operation="organization."+operation, actor_id=user, tenant_id=tenant,
            target_id=target, payload=payload, idempotency_key=key, request_fingerprint=fingerprint,
            trace_id="s39-l2-pg-trace", policy_version="s39-l2-pg-policy")

    def invitation(tag):
        return repositories[0].create_invitation(tenant_id=tenant, created_by=actor, code="s39-l2-code-"+tag,
            expires_at=now+timedelta(days=1), max_uses=1, now=now, workspace_id=workspace)

    def join(tag, user):
        return repositories[0].create_join_request(tenant_id=tenant, user_id=user,
            invitation_code="s39-l2-code-"+tag, now=now)

    def race(left, right):
        barrier = Barrier(2)
        def compete(index, arguments):
            barrier.wait(timeout=5)
            try:
                return run(repositories[index], *arguments)
            except OrganizationWorkflowError as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(compete, 0, left)
            b = executor.submit(compete, 1, right)
            return a.result(timeout=15), b.result(timeout=15)

    def one_winner(results):
        assert sum(not isinstance(value, OrganizationWorkflowError) for value in results) == 1

    try:
        # 두 repository는 서로 다른 프로세스 잠금을 가지며 각 transaction은 새 PG 연결이다.
        assert repositories[0]._lock is not repositories[1]._lock
        with repositories[0].transaction() as left, repositories[1].transaction() as right:
            assert left.execute("SELECT pg_backend_pid()").fetchone()[0] != right.execute("SELECT pg_backend_pid()").fetchone()[0]

        invitation("double")
        request = join("double", "s39-l2-user-double")
        decision = {"approved": True, "expected_version": 1, "role": "member"}
        one_winner(race(("join.decide", request.request_id, decision, "s39-l2-double-approve-0001"),
                        ("join.decide", request.request_id, decision, "s39-l2-double-approve-0002")))

        invitation("limit")
        requests = [join("limit", "s39-l2-limit-user-"+str(index)) for index in range(2)]
        one_winner(race(("join.decide", requests[0].request_id, decision, "s39-l2-limit-approve-0001"),
                        ("join.decide", requests[1].request_id, decision, "s39-l2-limit-approve-0002")))

        revocable = invitation("revoke")
        request_revoke = join("revoke", "s39-l2-user-revoke")
        one_winner(race(("join.decide", request_revoke.request_id, decision, "s39-l2-revoke-race-0001"),
                        ("invitation.revoke", revocable.invitation_id, {"expected_version": 1}, "s39-l2-revoke-race-0002")))

        payload = {"code": "s39-l2-same-key-code", "workspace_id": workspace, "expires_at": now+timedelta(days=1), "max_uses": 1}
        same = race(("invitation.create", None, payload, "s39-l2-concurrent-key-0001"),
                    ("invitation.create", None, payload, "s39-l2-concurrent-key-0001"))
        assert all(not isinstance(value, OrganizationWorkflowError) for value in same)
        assert sorted(value.replayed for value in same) == [False, True]
        assert same[0].value == same[1].value
        with pytest.raises(OrganizationWorkflowError, match="IDEMPOTENCY_KEY_REUSED"):
            run(repositories[0], "invitation.create", None, payload | {"max_uses": 2}, "s39-l2-concurrent-key-0001")

        with psycopg.connect(app_dsn) as connection:
            old_role_history = connection.execute("SELECT * FROM public.identity_org_role_history ORDER BY history_id").fetchall()
        one_winner(race(("member.role", "s39-l2-user-double", {"workspace_id": workspace, "role": "viewer", "expected_version": 1, "expected_auth_version": 1}, "s39-l2-stale-member-0001"),
                        ("member.role", "s39-l2-user-double", {"workspace_id": workspace, "role": "editor", "expected_version": 1, "expected_auth_version": 1}, "s39-l2-stale-member-0002")))
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT * FROM public.identity_org_role_history ORDER BY history_id").fetchall() == old_role_history
        with pytest.raises(OrganizationWorkflowError, match="REQUEST_CONFLICT"):
            run(repositories[0], "join.decide", request.request_id, decision | {"expected_version": 2}, "s39-l2-stale-request-0001")

        # 각 실패 지점의 INSERT가 같은 transaction에서 모두 롤백된다.
        for boundary in ("_management_view", "_record_management_operation"):
            original = getattr(repositories[0], boundary)
            def fail_after_stage(*args, _original=original, **kwargs):
                _original(*args, **kwargs)
                raise OrganizationWorkflowError("INJECTED_STORAGE_FAILURE", 503)
            with patch.object(repositories[0], boundary, fail_after_stage), pytest.raises(OrganizationWorkflowError, match="INJECTED_STORAGE_FAILURE"):
                run(repositories[0], "invitation.create", None, payload | {"code": "s39-l2-rollback-"+boundary}, "s39-l2-rollback-"+boundary)

        # 구 원장은 그대로 replay하고 신규 감사/영수증을 소급 생성하지 않는다.
        legacy = invitation("legacy")
        legacy_payload = {"code": "s39-l2-code-legacy", "expires_at": now+timedelta(days=1), "max_uses": 1}
        fingerprint = hashlib.sha256(json.dumps(legacy_payload, sort_keys=True, default=str).encode()).hexdigest()
        legacy_value = {key: value.isoformat() if isinstance(value, datetime) else value.value if hasattr(value, "value") else value for key, value in asdict(legacy).items()}
        repositories[0].idempotency_record(operation="organization.invitation.create", actor_id=actor,
            key="s39-l2-legacy-key-0001", fingerprint=fingerprint, result=legacy_value, now=now)
        assert run(repositories[0], "invitation.create", None, legacy_payload, "s39-l2-legacy-key-0001").replayed
        with pytest.raises(OrganizationWorkflowError, match="IDEMPOTENCY_KEY_REUSED"):
            run(repositories[0], "invitation.create", None, legacy_payload | {"max_uses": 2}, "s39-l2-legacy-key-0001")

        with psycopg.connect(dsn) as connection:
            receipts = connection.execute("SELECT event_id,response_json,audit_json,created_at FROM public.identity_org_operation_outbox ORDER BY event_id").fetchall()
            assert len(receipts) == 5
            assert all("s39-l2-code-" not in response and "code_digest" not in response and "s39-l2-code-" not in audit for _, response, audit, _ in receipts)
            for _, response, _, _ in receipts:
                projected = json.loads(response)
                for name, value in projected.items():
                    if name.endswith("_at"):
                        assert "T" in value and value.endswith("+00:00")
                        assert datetime.fromisoformat(value).utcoffset() == timedelta(0)
            assert connection.execute("SELECT count(*) FROM public.identity_org_invitation_codes WHERE code_digest IN (%s,%s)",
                tuple(hashlib.sha256(("s39-l2-rollback-"+name).encode()).hexdigest() for name in ("_management_view", "_record_management_operation"))).fetchone() == (0,)
            # 실제 역할 변경은 새 감사 원장에 남고 과거 표기 역할 이력은 그대로 보존한다.
            assert connection.execute("SELECT count(*) FROM public.identity_org_role_history").fetchone() == (3 if connection.execute("SELECT state FROM public.identity_org_join_requests WHERE request_id=%s", (request_revoke.request_id,)).fetchone()[0] == "approved" else 2,)
            assert connection.execute("SELECT count(*) FROM public.identity_org_invitation_codes WHERE used_count>max_uses").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM public.identity_org_join_requests WHERE invitation_id=%s AND state='approved'", (revocable.invitation_id,)).fetchone()[0] == connection.execute("SELECT used_count FROM public.identity_org_invitation_codes WHERE invitation_id=%s", (revocable.invitation_id,)).fetchone()[0]

        # 실제 중앙 PG sink에 기록된 뒤 ack 장애 → 재시작 → 같은 tenant/event 확인 후 ack.
        with patch.object(repositories[0], "_ack_management_audit", side_effect=OSError("synthetic ack outage")):
            assert repositories[0].dispatch_pending_management_audits() == 0
        assert len(store.list(tenant_id=tenant).items) == len(receipts)
        store.close()
        store = PostgresSecurityAuditStore(dsn)
        restarted = PostgresOrganizationRepository(app_dsn, audit_store=store)
        try:
            assert restarted.dispatch_pending_management_audits() == len(receipts)
            assert restarted.dispatch_pending_management_audits() == 0
        finally:
            restarted.close()
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT count(*) FROM public.identity_org_operation_outbox WHERE delivered_at IS NULL").fetchone() == (0,)
            assert connection.execute("SELECT event_id,response_json,audit_json,created_at FROM public.identity_org_operation_outbox ORDER BY event_id").fetchall() == receipts
            assert connection.execute("SELECT count(*) FROM public.security_audit_events WHERE tenant_id=%s", (tenant,)).fetchone() == (len(receipts),)
            assert connection.execute("SELECT to_jsonb(r) FROM public.identity_users r ORDER BY user_id").fetchall() == old_users
            assert connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall() == old_sessions
            assert connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone() == (index_oid,)
    finally:
        store.close()
        for repository in repositories: repository.close()


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="S39 전용 격리 DSN 필요 — J1 실제 PG 미실행")
def test_j1_isolated_join_access_atomicity_and_s38_auth_version_lock(monkeypatch):
    """L1/L2와 섞지 않고 exact 단일 승인 자원을 fresh0067로 준비해 선택 실행한다."""
    import hashlib
    import json
    import time
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    from threading import Barrier, Event
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from alembic import command
    from alembic.config import Config
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import MINIMUM_STEP_UP_ACTION_GROUPS
    from daon_user_api.organization_membership import OrganizationWorkflowError
    from daon_user_api.postgres_adapters import PostgresOrganizationRepository

    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s39_qa_20261017":
        pytest.fail("S39_EXACT_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s39_qa_20261017"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
        assert connection.execute("SELECT to_regclass('public.identity_org_operation_outbox')").fetchone() == (None,)
        old_users = connection.execute("SELECT to_jsonb(r) FROM public.identity_users r ORDER BY user_id").fetchall()
        old_sessions = connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall()
        old_index = connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone()[0]
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    command.upgrade(Config(str(VERSIONS.parents[1] / "alembic.ini")), "0068")
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    store = PostgresSecurityAuditStore(dsn)
    repositories = [PostgresOrganizationRepository(app_dsn, audit_store=store) for _ in range(2)]
    now = datetime.now(timezone.utc)
    tenant, workspace, other = "s39-j1-tenant", "s39-j1-workspace", "s39-j1-other-workspace"
    users = ["s39-j1-user", "s39-j1-existing", "s39-j1-unbound", "s39-j1-rollback-dto", "s39-j1-rollback-audit"]
    with psycopg.connect(app_dsn) as connection:
        assert connection.execute("SELECT current_user").fetchone() == ("daon_app",)
        connection.execute("INSERT INTO public.identity_tenants(tenant_id) VALUES (%s)", (tenant,))
        for action in MINIMUM_STEP_UP_ACTION_GROUPS:
            connection.execute("INSERT INTO public.identity_tenant_step_up_actions(tenant_id,action_group,is_mandatory) VALUES (%s,%s,1)", (tenant, action))
        for user in users:
            connection.execute("INSERT INTO public.identity_users(user_id,issuer,subject,login_id,email,email_verified_at,password_change_required,state) VALUES (%s,'local',%s,%s,%s,%s,false,'active')", (user, user, user+"-login", user+"@example.test", now))
        for identifier in (workspace, other):
            connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,'organization','cloud_sync',1000,1,1,%s)", (identifier, tenant, now))
        connection.execute("INSERT INTO public.identity_auth_memberships VALUES (%s,%s,%s,'reviewer','active',7,%s)", (tenant, other, users[1], now))

    def run(repository, operation, target, payload, key):
        return repository.execute_management_operation(operation="organization."+operation, actor_id="s39-j1-admin", tenant_id=tenant,
            target_id=target, payload=payload, idempotency_key=key,
            request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), trace_id="s39-j1-pg-trace", policy_version="s39-j1-pg-policy")

    def prepare(user, *, bound=True):
        code = "j1-pg-synthetic-"+user
        invitation = repositories[0].create_invitation(tenant_id=tenant, created_by="s39-j1-admin", code=code,
            workspace_id=workspace if bound else None, expires_at=now+timedelta(days=1), max_uses=1, now=now)
        request = repositories[0].create_join_request(tenant_id=tenant, user_id=user, invitation_code=code, now=now)
        return invitation, request

    decision = {"approved": True, "expected_version": 1, "role": "member"}
    try:
        invitation, request = prepare(users[0])
        barrier = Barrier(2)
        def approve(index):
            barrier.wait(timeout=5)
            return run(repositories[index], "join.decide", request.request_id, decision, "s39-j1-same-key-0001")
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(approve, 0), pool.submit(approve, 1)
            results = [a.result(timeout=15), b.result(timeout=15)]
        assert sorted(result.replayed for result in results) == [False, True]
        assert results[0].value == results[1].value
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT tenant_id,user_id,role FROM public.identity_memberships WHERE tenant_id=%s", (tenant,)).fetchall() == [(tenant, users[0], "member")]
            assert connection.execute("SELECT workspace_id,role,state,version FROM public.identity_auth_memberships WHERE user_id=%s", (users[0],)).fetchall() == [(workspace, "viewer", "active", 1)]
            assert connection.execute("SELECT role,state,version,join_workspace_id FROM public.identity_org_tenant_memberships WHERE user_id=%s", (users[0],)).fetchone() == ("member", "active", 1, workspace)
            assert connection.execute("SELECT acl_version FROM public.identity_auth_workspaces WHERE workspace_id=%s", (workspace,)).fetchone() == (2,)
            assert connection.execute("SELECT acl_version FROM public.identity_auth_workspaces WHERE workspace_id=%s", (other,)).fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM public.identity_auth_tenant_roles WHERE tenant_id=%s", (tenant,)).fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM public.identity_tenant_step_up_actions WHERE tenant_id=%s", (tenant,)).fetchone() == (7,)
            assert connection.execute("SELECT used_count FROM public.identity_org_invitation_codes WHERE invitation_id=%s", (invitation.invitation_id,)).fetchone() == (1,)

        for user, bound, error_code in ((users[1], True, "ORGANIZATION_MEMBERSHIP_CONFLICT"), (users[2], False, "ORGANIZATION_INVITATION_SCOPE_REQUIRED")):
            denied_invitation, denied_request = prepare(user, bound=bound)
            with pytest.raises(OrganizationWorkflowError, match=error_code):
                run(repositories[0], "join.decide", denied_request.request_id, decision, "s39-j1-denied-"+user)
            with psycopg.connect(app_dsn) as connection:
                assert connection.execute("SELECT state,version FROM public.identity_org_join_requests WHERE request_id=%s", (denied_request.request_id,)).fetchone() == ("pending", 1)
                assert connection.execute("SELECT used_count FROM public.identity_org_invitation_codes WHERE invitation_id=%s", (denied_invitation.invitation_id,)).fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM public.identity_memberships WHERE tenant_id=%s AND user_id=%s", (tenant, user)).fetchone() == (0,)

        for user, method in ((users[3], "_management_view"), (users[4], "_record_management_operation")):
            rollback_invitation, rollback_request = prepare(user)
            original = getattr(repositories[0], method)
            def fail_after_stage(*args, _original=original, **kwargs):
                _original(*args, **kwargs)
                raise OrganizationWorkflowError("J1_INJECTED_STORAGE_FAILURE", 503)
            with patch.object(repositories[0], method, fail_after_stage), pytest.raises(OrganizationWorkflowError, match="J1_INJECTED_STORAGE_FAILURE"):
                run(repositories[0], "join.decide", rollback_request.request_id, decision, "s39-j1-rollback-"+user)
            with psycopg.connect(app_dsn) as connection:
                for table in ("identity_memberships", "identity_auth_memberships", "identity_org_tenant_memberships", "identity_org_role_history"):
                    assert connection.execute(f"SELECT count(*) FROM public.{table} WHERE tenant_id=%s AND user_id=%s", (tenant, user)).fetchone() == (0,)
                assert connection.execute("SELECT state,version FROM public.identity_org_join_requests WHERE request_id=%s", (rollback_request.request_id,)).fetchone() == ("pending", 1)
                assert connection.execute("SELECT used_count FROM public.identity_org_invitation_codes WHERE invitation_id=%s", (rollback_invitation.invitation_id,)).fetchone() == (0,)

        # 기존 S38과 같은 Workspace→auth 잠금을 별도 실제 연결에서 먼저 보유한다.
        started = Event()
        backend = []
        original_lock = repositories[0]._lock_management_target
        def observe_lock(connection, *args):
            backend.append(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
            started.set()
            return original_lock(connection, *args)
        role_payload = {"workspace_id": workspace, "role": "editor", "expected_version": 1, "expected_auth_version": 1, "reason": "역할 조정"}
        with psycopg.connect(app_dsn) as s38, ThreadPoolExecutor(max_workers=1) as pool:
            s38.execute("SELECT workspace_id FROM public.identity_auth_workspaces WHERE workspace_id=%s FOR UPDATE", (workspace,))
            s38.execute("SELECT user_id FROM public.identity_auth_memberships WHERE workspace_id=%s AND user_id=%s FOR UPDATE", (workspace, users[0]))
            with patch.object(repositories[0], "_lock_management_target", observe_lock):
                future = pool.submit(run, repositories[0], "member.role", users[0], role_payload, "s39-j1-stale-s38-0001")
                blocked = False
                try:
                    assert started.wait(timeout=5)
                    deadline = time.monotonic()+5
                    with psycopg.connect(dsn, autocommit=True) as observer:
                        while time.monotonic() < deadline:
                            row = observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (backend[0],)).fetchone()
                            if row == ("Lock",):
                                blocked = True
                                break
                            time.sleep(0.01)
                    s38.execute("UPDATE public.identity_auth_memberships SET role='reviewer',version=2 WHERE workspace_id=%s AND user_id=%s", (workspace, users[0]))
                    s38.execute("UPDATE public.identity_auth_workspaces SET acl_version=3 WHERE workspace_id=%s", (workspace,))
                    s38.commit()
                finally:
                    # 검증 실패여도 executor 종료 대기 전에 보유 잠금을 해제한다.
                    s38.rollback()
                assert blocked
                with pytest.raises(OrganizationWorkflowError, match="MEMBERSHIP_CONFLICT"):
                    future.result(timeout=10)
        with repositories[0].transaction() as connection:
            assert repositories[0]._access_bridge.read_join_access(connection=connection, tenant_id=tenant, workspace_id=workspace, user_id=users[0]) == {
                "access_role": "reviewer", "access_state": "active", "access_version": 2}
        updated = run(repositories[0], "member.role", users[0], role_payload | {"expected_auth_version": 2}, "s39-j1-role-exact-0001")
        assert (updated.value["role"], updated.value["version"], updated.value["access_role"], updated.value["access_version"]) == ("member", 2, "editor", 3)
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT role,version FROM public.identity_auth_memberships WHERE workspace_id=%s AND user_id=%s", (other, users[1])).fetchone() == ("reviewer", 7)
            assert connection.execute("SELECT acl_version FROM public.identity_auth_workspaces WHERE workspace_id=%s", (workspace,)).fetchone() == (4,)
            assert connection.execute("SELECT count(*) FROM public.identity_org_role_history WHERE user_id=%s", (users[0],)).fetchone() == (1,)
        assert repositories[0].dispatch_pending_management_audits() == 2
        assert repositories[1].dispatch_pending_management_audits() == 0
        with psycopg.connect(dsn) as connection:
            receipts = connection.execute("SELECT response_json,audit_json,delivered_at FROM public.identity_org_operation_outbox").fetchall()
            assert len(receipts) == 2 and all(row[2] is not None for row in receipts)
            role_audit = next(json.loads(row[1]) for row in receipts if json.loads(row[1])["action"] == "organization.membership.role_changed")
            assert (role_audit["before"], role_audit["after"], role_audit["auth_version"]) == ("reviewer", "editor", 3)
            assert all("code_digest" not in response and "j1-pg-synthetic-" not in response+audit for response, audit, _ in receipts)
            assert connection.execute("SELECT to_jsonb(r) FROM public.identity_users r WHERE user_id<>ALL(%s) ORDER BY user_id", (users,)).fetchall() == old_users
            assert connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall() == old_sessions
            assert connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone() == (old_index,)
    finally:
        for repository in repositories:
            repository.close()
        store.close()


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="S39 전용 DSN 필요 — L3 실제 PG 미실행")
def test_l3_isolated_api_foundation_scope_and_protected_activation(monkeypatch):
    """L1/L2/J1과 별도 fresh0067 복제의 동일 승인 DB에서만 선택한다."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from alembic import command
    from alembic.config import Config
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from daon_user_api.identity import IdentityPrincipal
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.organization_api import OrganizationApi
    from daon_user_api.organization_membership import OrganizationWorkflowError
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository, PostgresOrganizationRepository
    from daon_user_api.runtime import _organization_management_ready
    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s39_qa_20261017": pytest.fail("S39_EXACT_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s39_qa_20261017"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    command.upgrade(Config(str(VERSIONS.parents[1] / "alembic.ini")), "0068")
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    identity, auth, repo = PostgresIdentityRepository(app_dsn), PostgresAuthorizationRepository(app_dsn), PostgresOrganizationRepository(app_dsn)
    cloud = PostgresCloudStore(app_dsn, organization_admin_v2_enabled=True)
    dependencies = SimpleNamespace(settings=SimpleNamespace(organization_admin_v2_enabled=True), identity_repository=identity, authorization_repository=auth, organization_repository=repo, cloud_store=cloud)
    now = datetime.now(timezone.utc)
    try:
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("daon_app",)
            for tenant, workspace, name, kind in (("l3-tenant-a", "l3-workspace-a", "격리 조직 A", "organization"), ("l3-tenant-b", "l3-workspace-b", "격리 조직 B", "organization"), ("l3-personal", "l3-personal-workspace", "개인 조직 아님", "personal")):
                connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,%s,'cloud_sync',1000,1,1,%s)", (workspace, tenant, kind, now))
                connection.execute("SELECT set_config('app.tenant_id',%s,true),set_config('app.workspace_id',%s,true)", (tenant, workspace))
                connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES (%s,%s)", (tenant, name))
                connection.execute("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) VALUES (%s,%s,%s)", (tenant, workspace, "정확 작업공간 "+workspace))
        assert _organization_management_ready(dependencies) is True
        dependencies.settings.organization_admin_v2_enabled = False
        assert _organization_management_ready(dependencies) is False
        dependencies.settings.organization_admin_v2_enabled = True
        actual_identity = dependencies.identity_repository
        dependencies.identity_repository = SimpleNamespace(_dsn=app_dsn)
        assert _organization_management_ready(dependencies) is False
        dependencies.identity_repository = actual_identity
        app = FastAPI()
        @app.exception_handler(OrganizationWorkflowError)
        async def error(_request: Request, failure: OrganizationWorkflowError):
            return JSONResponse(status_code=failure.status, content={"code": failure.code})
        principal = IdentityPrincipal("l3-system-admin", "l3-original-session", "device", "internal-admin")
        OrganizationApi(repo, auth, system_admin_checker=lambda actor: actor.user_id == "l3-system-admin", management_available=lambda: _organization_management_ready(dependencies), policy_version="l3-pg-policy").mount(app, lambda _request: principal)
        with TestClient(app) as client:
            assert client.get("/api/v1/organization/admin/tenants").json()["data"] == [{"tenant_id": "l3-tenant-a", "display_name": "격리 조직 A"}, {"tenant_id": "l3-tenant-b", "display_name": "격리 조직 B"}]
            assert client.get("/api/v1/organization/tenants/l3-tenant-b/workspaces").json()["data"] == [{"workspace_id": "l3-workspace-b", "display_name": "정확 작업공간 l3-workspace-b"}]
            assert client.get("/api/v1/organization/tenants/l3-personal/workspaces").status_code == 404
            assert client.get("/api/v1/organization/tenants/missing/invitations").status_code == 404
            payload = {"workspace_id": "l3-workspace-a", "code": "l3-pg-synthetic-code", "expires_at": (now+timedelta(days=1)).isoformat(), "max_uses": 1}
            headers = {"Idempotency-Key": "l3-pg-create-invitation-0001", "X-Daon-Expected-Session-Id": "l3-original-session"}
            changed = client.post("/api/v1/organization/tenants/l3-tenant-a/invitations", json=payload, headers=headers)
            assert changed.status_code == 201
            data = changed.json()["data"]
            assert set(data) == {"invitation_id", "tenant_id", "workspace_id", "created_by", "expires_at", "max_uses", "used_count", "state", "version", "created_at", "updated_at"}
            assert client.post("/api/v1/organization/tenants/l3-tenant-a/invitations", json=payload, headers=headers).json()["data"] == data
            assert client.post("/api/v1/organization/tenants/l3-tenant-a/invitations", json=payload, headers=headers | {"X-Daon-Expected-Session-Id": "other-session"}).status_code == 409
            assert client.get("/api/v1/organization/tenants/l3-tenant-a/invitations").json()["data"] == [data]
            assert client.get("/api/v1/organization/tenants/l3-tenant-b/invitations").json()["data"] == []
            dependencies.settings.organization_admin_v2_enabled = False
            blocked = client.post("/api/v1/organization/tenants/l3-tenant-a/invitations", json=payload, headers=headers | {"Idempotency-Key": "l3-pg-flag-closed-0001"})
            assert blocked.status_code == 503
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT count(*) FROM public.identity_memberships WHERE user_id='l3-system-admin'").fetchone() == (0,)
            assert connection.execute("SELECT count(*) FROM public.identity_org_operation_outbox WHERE actor_id='l3-system-admin'").fetchone() == (1,)
            response, audit = connection.execute("SELECT response_json,audit_json FROM public.identity_org_operation_outbox WHERE actor_id='l3-system-admin'").fetchone()
            assert "code_digest" not in response+audit and "l3-pg-synthetic-code" not in response+audit
    finally:
        identity.close(); auth.close(); repo.close(); cloud.close()


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="J2 전용 격리 DSN 필요 — 실제 PG 미실행")
def test_j2_isolated_pending_replay_concurrency_and_zero_access(monkeypatch):
    """main이 exact 단일 승인 자원을 fresh0067로 복제한 뒤 이 한 건만 실행한다."""
    import hashlib
    import json
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    from threading import Barrier
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from alembic import command
    from alembic.config import Config
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from daon_user_api.audit import PostgresSecurityAuditStore
    from daon_user_api.identity import IdentityPrincipal
    from daon_user_api.organization_api import OrganizationApi
    from daon_user_api.organization_membership import OrganizationWorkflowError
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository, PostgresOrganizationRepository

    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s39_qa_20261017": pytest.fail("S39_EXACT_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s39_qa_20261017"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
        assert connection.execute("SELECT to_regclass('public.identity_org_operation_outbox')").fetchone() == (None,)
        old_users = connection.execute("SELECT to_jsonb(r) FROM public.identity_users r ORDER BY user_id").fetchall()
        old_sessions = connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall()
        index_oid = connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone()[0]
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    command.upgrade(Config(str(VERSIONS.parents[1] / "alembic.ini")), "0068")
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    store = PostgresSecurityAuditStore(dsn)
    repositories = [PostgresOrganizationRepository(app_dsn, audit_store=store) for _ in range(2)]
    auth = PostgresAuthorizationRepository(app_dsn)
    now = datetime.now(timezone.utc)
    tenant, workspace = "s39-j2-tenant", "s39-j2-workspace"
    users = ["s39-j2-member", "s39-j2-race", "s39-j2-same-key", "s39-j2-rollback"]
    code = "j2-pg-synthetic-code"
    try:
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("daon_app",)
            connection.execute("INSERT INTO public.identity_tenants(tenant_id) VALUES (%s)", (tenant,))
            for user in users:
                connection.execute("INSERT INTO public.identity_users(user_id,issuer,subject,login_id,email,email_verified_at,password_change_required,state) VALUES (%s,'local',%s,%s,%s,%s,false,'active')", (user, user, user+"-login", user+"@example.test", now))
            connection.execute("INSERT INTO public.identity_auth_workspaces VALUES (%s,%s,'organization','cloud_sync',1000,1,1,%s)", (workspace, tenant, now))
        invitation = repositories[0].create_invitation(tenant_id=tenant, created_by="j2-admin", code=code, workspace_id=workspace, expires_at=now+timedelta(days=1), max_uses=1, now=now)
        app = FastAPI()
        @app.exception_handler(OrganizationWorkflowError)
        async def workflow_error(_request: Request, failure: OrganizationWorkflowError):
            return JSONResponse(status_code=failure.status, content={"code": failure.code})
        current = {"principal": IdentityPrincipal(users[0], "j2-session", "device", "personal")}
        # 실제 PG 저장 경계다. 세션 principal은 fixture이며 정식 인증/flag 검증을 주장하지 않는다.
        OrganizationApi(repositories[0], auth, policy_version="j2-pg-policy").mount(app, lambda _request: current["principal"])
        headers = {"Idempotency-Key": "j2-pg-operation-0001", "X-Daon-Expected-Session-Id": "j2-session"}
        with TestClient(app) as client:
            created = client.post("/api/v1/organization/join-requests", json={"invitation_code": code}, headers=headers)
            assert created.status_code == 202 and created.json()["data"]["state"] == "pending"
            duplicate = client.post("/api/v1/organization/join-requests", json={"invitation_code": code}, headers=headers | {"Idempotency-Key": "j2-pg-operation-0002"})
            assert duplicate.status_code == 409 and duplicate.json()["code"] == "JOIN_REQUEST_ALREADY_PENDING"
            listed = client.get("/api/v1/organization/join-requests")
            assert listed.status_code == 200 and listed.json()["data"] == [created.json()["data"]]
            assert all("T" in listed.json()["data"][0][field] and listed.json()["data"][0][field].endswith("+00:00") for field in ("created_at", "updated_at"))
            current["principal"] = IdentityPrincipal(users[1], "other-session", "other-device", "personal")
            assert client.get("/api/v1/organization/join-requests").json()["data"] == []
            current["principal"] = IdentityPrincipal(users[0], "j2-session", "device", "personal")

        def run(index, user, key, invitation_code=code):
            payload = {"tenant_id": None, "invitation_code": invitation_code}
            try:
                return repositories[index].execute_management_operation(operation="organization.join.create", actor_id=user, tenant_id=None, target_id=None,
                    payload=payload, idempotency_key=key, request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), trace_id="j2-pg-trace", policy_version="j2-pg-policy")
            except OrganizationWorkflowError as error:
                return error.code

        for user, keys in ((users[1], ["j2-pg-race-new-key-0001", "j2-pg-race-new-key-0002"]), (users[2], ["j2-pg-race-same-key-0001"]*2)):
            barrier = Barrier(2)
            def concurrent(index):
                barrier.wait(timeout=5)
                return run(index, user, keys[index])
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(concurrent, index) for index in (0, 1)]
                results = [future.result(timeout=15) for future in futures]
            if user == users[1]:
                assert sum(result == "JOIN_REQUEST_ALREADY_PENDING" for result in results) == 1
                assert len([result for result in results if not isinstance(result, str)]) == 1
            else:
                assert sorted(result.replayed for result in results) == [False, True]
                assert results[0].value == results[1].value
        original = repositories[0]._record_management_operation
        def receipt_failure(connection, **values):
            original(connection, **values)
            raise OrganizationWorkflowError("PERSISTENCE_UNAVAILABLE", 503)
        with monkeypatch.context() as patch:
            patch.setattr(repositories[0], "_record_management_operation", receipt_failure)
            assert run(0, users[3], "j2-pg-rollback-operation-0001") == "PERSISTENCE_UNAVAILABLE"
        repositories[0].create_invitation(tenant_id=tenant, created_by="j2-admin", code="j2-pg-synthetic-unbound", expires_at=now+timedelta(days=1), max_uses=1, now=now)
        assert run(0, users[3], "j2-pg-unbound-operation-0001", "j2-pg-synthetic-unbound") == "INVITATION_INVALID"
        repositories[0].revoke_invitation(tenant_id=tenant, invitation_id=invitation.invitation_id, actor_id="j2-admin", expected_version=1, now=now)
        with TestClient(app) as client:
            replay = client.post("/api/v1/organization/join-requests", json={"invitation_code": code}, headers=headers)
            assert replay.status_code == 202 and replay.json()["data"] == created.json()["data"]
        # API/직접 경로 모두 여기까지 전달기를 호출하지 않는다. 선전달 가설을 실제 메타데이터로 구분한다.
        with psycopg.connect(dsn) as connection:
            before_delivery = connection.execute("SELECT event_id,actor_id,trace_id,policy_version,delivered_at IS NOT NULL AS delivered FROM public.identity_org_operation_outbox WHERE tenant_id=%s ORDER BY event_id", (tenant,)).fetchall()
            sink_before = connection.execute("SELECT event_id FROM public.security_audit_events WHERE tenant_id=%s", (tenant,)).fetchall()
        assert len(before_delivery) == 3 and {row[1] for row in before_delivery} == set(users[:3])
        assert all(not row[4] for row in before_delivery) and sink_before == []
        append_errors = []
        actual_append = store._append_admin_role
        def observed_append(draft):
            try:
                return actual_append(draft)
            except Exception as error:
                # 원문 예외/DSN/입력은 출력하지 않고 식별자·예외종류·SQLSTATE만 남긴다.
                append_errors.append((draft.event_id, draft.actor_id, type(error).__name__, getattr(error, "sqlstate", None)))
                raise
        with monkeypatch.context() as patch:
            patch.setattr(store, "_append_admin_role", observed_append)
            delivered = repositories[0].dispatch_pending_management_audits()
        with psycopg.connect(dsn) as connection:
            after_delivery = connection.execute("SELECT event_id,actor_id,delivered_at IS NOT NULL AS delivered FROM public.identity_org_operation_outbox WHERE tenant_id=%s ORDER BY event_id", (tenant,)).fetchall()
            sink_after = connection.execute("SELECT event_id,actor_id FROM public.security_audit_events WHERE tenant_id=%s ORDER BY event_id", (tenant,)).fetchall()
        assert delivered == 3, {"append_errors": append_errors, "receipts": after_delivery, "sink": sink_after}
        assert append_errors == []
        event_ids = {row[0] for row in before_delivery}
        assert len(after_delivery) == 3 and all(row[2] for row in after_delivery)
        assert len(sink_after) == 3 and {row[0] for row in sink_after} == event_ids
        for event_id, actor_id, trace_id, policy_version, _ in before_delivery:
            event = store.read(event_id, tenant_id=tenant)
            assert event is not None
            assert (event.actor_id, event.trace_id, event.policy_version) == (actor_id, trace_id, policy_version)
            assert event.action == "organization.join_request.created"
            assert dict(event.metadata) == {"reason": None, "version": 1, "auth_version": None}
        assert repositories[1].dispatch_pending_management_audits() == 0
        assert {event.event_id for event in store.list(tenant_id=tenant).items} == event_ids
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT count(*) FROM public.identity_org_join_requests WHERE user_id=ANY(%s)", (users,)).fetchone() == (3,)
            assert connection.execute("SELECT count(*) FROM public.identity_org_operation_outbox").fetchone() == (3,)
            assert connection.execute("SELECT count(*) FROM public.identity_org_join_requests WHERE user_id=%s", (users[3],)).fetchone() == (0,)
            for table in ("identity_memberships", "identity_auth_memberships", "identity_org_tenant_memberships"):
                assert connection.execute(f"SELECT count(*) FROM public.{table} WHERE user_id=ANY(%s)", (users,)).fetchone() == (0,)
            assert connection.execute("SELECT sum(used_count) FROM public.identity_org_invitation_codes WHERE tenant_id=%s", (tenant,)).fetchone() == (0,)
            assert connection.execute("SELECT acl_version FROM public.identity_auth_workspaces WHERE workspace_id=%s", (workspace,)).fetchone() == (1,)
            receipts = connection.execute("SELECT response_json,audit_json,delivered_at FROM public.identity_org_operation_outbox").fetchall()
            assert all(delivered is not None and code not in response+audit and "code_digest" not in response+audit for response, audit, delivered in receipts)
            assert connection.execute("SELECT to_jsonb(r) FROM public.identity_users r WHERE user_id<>ALL(%s) ORDER BY user_id", (users,)).fetchall() == old_users
            assert connection.execute("SELECT to_jsonb(r) FROM public.identity_sessions r ORDER BY session_id").fetchall() == old_sessions
            assert connection.execute("SELECT 'public.identity_org_join_pending_unique'::regclass::oid").fetchone() == (index_oid,)
    finally:
        for repository in repositories: repository.close()
        auth.close(); store.close()


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="J3 전용 격리 DSN 필요 — 실제 PG 미실행")
def test_j3_isolated_approval_suspension_authorization_and_personal_return(monkeypatch, tmp_path):
    """fresh0067 exact 단일 자원에서 별도 선택한다. 앞선 J2 node 상태와 혼용하지 않는다."""
    import hashlib
    import json
    from datetime import datetime, timedelta, timezone
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from daon_user_api import runtime
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import Action, AuthorizationError, AuthorizationService, HistoricalResultDescriptor, Role
    from daon_user_api.identity import IdentityError, IdentityService, MINIMUM_STEP_UP_ACTION_GROUPS
    from daon_user_api.identity_postgres import PostgresIdentityRepository
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository, PostgresOrganizationRepository
    from test_identity_support import POLICY_VERSION, TRACE_ID, native_login, policy

    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s39_qa_20261017":
        pytest.fail("S39_EXACT_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s39_qa_20261017"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
        assert connection.execute("SELECT to_regclass('public.identity_org_operation_outbox')").fetchone() == (None,)
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    command.upgrade(Config(str(VERSIONS.parents[1] / "alembic.ini")), "0068")
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    identities = PostgresIdentityRepository(app_dsn)
    auth = PostgresAuthorizationRepository(app_dsn)
    workflow = PostgresOrganizationRepository(app_dsn)
    audit = AuditEventStore()
    identity = IdentityService(repository=identities, audit_store=audit, oidc_policies=(policy(),), clock=lambda: datetime.now(timezone.utc))
    authorization = AuthorizationService(repository=auth, audit_store=audit, clock=lambda: datetime.now(timezone.utc))
    now = datetime.now(timezone.utc)
    tenant, workspace = "s39-j3-org", "s39-j3-workspace"
    try:
        # 외부 OIDC 교환만 비민감 대역이다. 아래 세션·권한·workflow와 물리표는 실제 PG다.
        original = native_login(identity)
        auth.bootstrap_workspace(tenant_id="tenant-001", workspace_id="s39-j3-personal", owner_user_id=original.user_id,
            owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync", cost_limit_cents=100, now=now)
        auth.bootstrap_workspace(tenant_id=tenant, workspace_id=workspace, owner_user_id="s39-j3-admin",
            owner_role=Role.ORGANIZATION_ADMIN, workspace_kind="organization", data_area="cloud_sync", cost_limit_cents=100, now=now)
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("daon_app",)
            connection.execute("UPDATE public.identity_memberships SET role='personal_owner' WHERE tenant_id='tenant-001' AND user_id=%s", (original.user_id,))
            connection.execute("INSERT INTO public.identity_tenants VALUES (%s)", (tenant,))
            for action in MINIMUM_STEP_UP_ACTION_GROUPS:
                connection.execute("INSERT INTO public.identity_tenant_step_up_actions VALUES (%s,%s,1)", (tenant, action))
            for name, display in (("tenant-001", "격리 개인"), (tenant, "격리 승인 조직")):
                connection.execute("SELECT set_config('app.tenant_id',%s,true)", (name,))
                connection.execute("INSERT INTO public.tenants(tenant_id,display_name) VALUES (%s,%s)", (name, display))
        workflow.create_invitation(tenant_id=tenant, workspace_id=workspace, created_by="s39-j3-admin",
            code="j3-pg-synthetic-invitation", expires_at=now+timedelta(days=1), max_uses=1, now=now)
        request = workflow.create_join_request(tenant_id=tenant, user_id=original.user_id, invitation_code="j3-pg-synthetic-invitation", now=now)
        personal = (("tenant-001", "personal", "s39-j3-personal"),)
        assert identity.selectable_tenant_workspaces(user_id=original.user_id) == personal
        def execute(operation, target, payload, key):
            return workflow.execute_management_operation(operation=operation, actor_id="s39-j3-admin", tenant_id=tenant,
                target_id=target, payload=payload, idempotency_key=key,
                request_fingerprint=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        execute("organization.join.decide", request.request_id, {"approved": True, "role": "member", "expected_version": 1}, "j3-pg-approval-0001")
        assert set(identity.selectable_tenant_workspaces(user_id=original.user_id)) == {personal[0], (tenant, "organization", workspace)}
        organization, selected = identity.switch_session_tenant(access_token=original.access_token, tenant_id=tenant, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        assert selected == workspace and organization.session_id != original.session_id
        principal = identity.validate_access(organization.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        assert authorization.authorize_action(principal=principal, workspace_id=workspace, action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION).role is Role.VIEWER
        with pytest.raises(AuthorizationError) as viewer_write:
            authorization.authorize_action(principal=principal, workspace_id=workspace, action=Action.EDIT, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        assert viewer_write.value.http_status == 403
        with pytest.raises(AuthorizationError) as foreign:
            authorization.authorize_action(principal=principal, workspace_id="s39-j3-personal", action=Action.VIEW, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        assert foreign.value.http_status == 404
        with pytest.raises(IdentityError): identity.validate_access(original.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        with pytest.raises(IdentityError): identity.rotate_refresh(original.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        auth.insert_historical_result(HistoricalResultDescriptor("j3-pg-result", "answer", tenant, workspace, (), (), (), POLICY_VERSION, 1), now)
        execute("organization.member.state", original.user_id, {"active": False, "expected_version": 1, "reason": "접근 중지"}, "j3-pg-suspend-0001")
        assert identity.selectable_tenant_workspaces(user_id=original.user_id) == personal
        assert identity.describe_access(organization.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION).principal == principal
        with pytest.raises(IdentityError) as unavailable:
            identity.switch_session_tenant(access_token=organization.access_token, tenant_id=tenant, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
        assert unavailable.value.code == "TENANT_UNAVAILABLE"
        dependencies = runtime.RuntimeDependencies(settings=runtime.RuntimeSettings.for_test(database_path=tmp_path / "j3-pg-unused.sqlite", policy_version=POLICY_VERSION),
            identity_service=identity, identity_repository=identities, authorization_service=authorization,
            authorization_repository=auth, audit_store=audit)
        # workflow prefix middleware 없이도 전역 export/access-decisions의 실제 권한 gate가 동작한다.
        headers = {"Authorization": f"Bearer {organization.access_token}", "Idempotency-Key": "j3-pg-http-operation-0001"}
        with TestClient(runtime.create_app(dependencies)) as client:
            session = client.get("/api/v1/session", headers=headers)
            assert session.status_code == 200 and session.json()["data"]["recovery_operations"] == []
            listed = client.get("/api/v1/session/tenants", headers=headers)
            assert listed.status_code == 200
            assert listed.json()["data"]["current_tenant_id"] == tenant
            assert [row["tenant_id"] for row in listed.json()["data"]["tenants"]] == ["tenant-001"]
            for method, url, body in [
                ("GET", f"/api/v1/studio-outputs/output/versions/version/exports/pdf?workspace_id={workspace}&notebook_id=notebook", None),
                ("POST", "/api/v1/access-decisions", {"resource_id": "j3-pg-result", "action": "open_source"}),
            ]:
                response = client.request(method, url, headers=headers, **({"json": body} if body else {}))
                assert response.status_code == 403 and response.json()["error"]["code"] == "ORGANIZATION_MEMBERSHIP_SUSPENDED"
            # runtime lifespan이 저장소를 닫기 전에 같은 실제 세션의 개인 복귀를 검증한다.
            returned, selected = identity.switch_session_tenant(access_token=organization.access_token, tenant_id="tenant-001", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            assert returned.session_id != organization.session_id and selected == personal[0][2]
            with pytest.raises(IdentityError): identity.validate_access(organization.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            with pytest.raises(IdentityError): identity.rotate_refresh(organization.refresh_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
            with psycopg.connect(app_dsn) as connection:
                connection.execute("UPDATE public.identity_auth_memberships SET state='suspended',version=version+1 WHERE tenant_id=%s AND user_id=%s", (tenant, original.user_id))
            execute("organization.member.state", original.user_id, {"active": True, "expected_version": 2, "reason": "업무 복귀"}, "j3-pg-active-0001")
            assert identity.selectable_tenant_workspaces(user_id=original.user_id) == personal
            with psycopg.connect(app_dsn) as connection:
                assert connection.execute("SELECT role,state,version FROM public.identity_auth_memberships WHERE tenant_id=%s AND user_id=%s", (tenant, original.user_id)).fetchall() == [("viewer", "suspended", 2)]
                assert connection.execute("SELECT count(*) FROM public.identity_memberships WHERE user_id='s39-j3-admin'").fetchone() == (0,)
    finally:
        workflow.close(); auth.close(); identities.close()


@pytest.mark.skipif(not os.environ.get("DAON_ORGANIZATION_TEST_DSN"), reason="J3 감사 조회 전용 격리 DSN 필요 — 실제 PG 미실행")
def test_j3_isolated_suspended_tenant_audit_read(monkeypatch):
    """fresh0067의 기존 물리표에서 감사 gate만 검사한다. 0068 적용·기존 공동 node 재실행은 없다."""
    from datetime import datetime, timezone
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from daon_user_api.audit import AuditEventStore
    from daon_user_api.authorization import AuthorizationError, AuthorizationService, Role
    from daon_user_api.identity import IdentityPrincipal
    from daon_user_api.postgres_adapters import PostgresAuthorizationRepository

    dsn = os.environ["DAON_ORGANIZATION_TEST_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s39_qa_20261017":
        pytest.fail("S39_EXACT_ISOLATED_DATABASE_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s39_qa_20261017"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    repository = PostgresAuthorizationRepository(app_dsn)
    service = AuthorizationService(repository=repository, audit_store=AuditEventStore(), clock=lambda: datetime.now(timezone.utc))
    user, tenant, personal = "s39-j3-audit-manager", "s39-j3-audit-org", "s39-j3-audit-personal"
    manager = IdentityPrincipal(user, "j3-audit-session", "j3-audit-device", tenant)
    owner = IdentityPrincipal(user, "j3-personal-session", "j3-personal-device", personal)
    try:
        with psycopg.connect(app_dsn) as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("daon_app",)
            for target in (tenant, personal):
                connection.execute("INSERT INTO public.identity_tenants VALUES (%s)", (target,))
            connection.execute("INSERT INTO public.identity_users(user_id,issuer,subject,login_id,state) VALUES (%s,'local',%s,%s,'active')", (user, user, user+"-login"))
        for target, workspace, role, kind in ((tenant, "j3-audit-workspace", Role.ORGANIZATION_ADMIN, "organization"), (personal, "j3-audit-personal-workspace", Role.PERSONAL_OWNER, "personal")):
            repository.bootstrap_workspace(tenant_id=target, workspace_id=workspace, owner_user_id=user, owner_role=role,
                workspace_kind=kind, data_area="cloud_sync", cost_limit_cents=100, now=datetime.now(timezone.utc))
        with psycopg.connect(app_dsn) as connection:
            connection.execute("INSERT INTO public.identity_org_tenant_memberships(tenant_id,user_id,role,state,version,updated_at) VALUES (%s,%s,'member','suspended',1,%s)", (tenant, user, datetime.now(timezone.utc)))
        def read(principal):
            return service.authorize_audit_read(principal=principal, workspace_id=None, trace_id="j3-audit-trace", policy_version="j3-audit-policy")
        with pytest.raises(AuthorizationError) as denied:
            read(manager)
        assert (denied.value.code, denied.value.http_status) == ("ORGANIZATION_MEMBERSHIP_SUSPENDED", 403)
        assert read(owner) is None
        with psycopg.connect(app_dsn) as connection:
            connection.execute("UPDATE public.identity_org_tenant_memberships SET state='active' WHERE tenant_id=%s AND user_id=%s", (tenant, user))
        assert read(manager) is None
        # 저장소/역할/정책을 바꾸지 않고 해당 workflow 조회에만 실제 PG SQL 오류를 주입한다.
        actual_connect = repository._connect
        def failing_connection():
            connection = actual_connect()
            actual_execute = connection.execute
            def execute(statement, parameters=()):
                if "public.identity_org_tenant_memberships" in statement:
                    return actual_execute("SELECT 1/0")
                return actual_execute(statement, parameters)
            connection.execute = execute
            return connection
        with monkeypatch.context() as patch:
            patch.setattr(repository, "_connect", failing_connection)
            with pytest.raises(AuthorizationError) as unavailable:
                read(manager)
            assert unavailable.value.http_status == 503
            assert read(owner) is None
        with psycopg.connect(app_dsn) as connection:
            connection.execute("DELETE FROM public.identity_org_tenant_memberships WHERE tenant_id=%s AND user_id=%s", (tenant, user))
        assert read(manager) is None
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0067",)]
    finally:
        repository.close()
