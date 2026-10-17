"""Run ONLY against main's empty, dedicated 0066 S36 QA database.

DAON_TEST_ACCOUNT_POSTGRES_DSN must name daon_user_s36_qa. The runner owns
the container/tunnel and baseline migration. This test owns 0067 up/down only.
"""
from __future__ import annotations

import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from daon_user_api.account_settings import AccountSettingsService, account_actor_scope
from daon_user_api.admin_users import AdminUserService
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import DevicePlatform, IdentityError, IdentityPrincipal, IdentityService, PASSWORD_HASHER, _digest
from daon_user_api.identity_postgres import PostgresIdentityRepository
from test_identity_support import POLICY_VERSION, TRACE_ID


def _options(token):
    return dict(access_token=token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)


def _save(service, token, name="S36 QA profile", version=0, key="s36-profile-key-01"):
    with service.repository.transaction() as connection:
        expected = connection.execute("SELECT session_id FROM sessions WHERE access_digest=?", (_digest(token),)).fetchone()[0]
    return service.save_profile(**_options(token), display_name=name, expected_version=version, idempotency_key=key, expected_session_id=expected)


@pytest.mark.skipif(not os.environ.get("DAON_TEST_ACCOUNT_POSTGRES_DSN"), reason="dedicated S36 PostgreSQL DSN not configured")
def test_0067_isolated_migration_rls_persistence_conflicts_and_deletion(monkeypatch):
    dsn = os.environ["DAON_TEST_ACCOUNT_POSTGRES_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s36_qa":
        pytest.fail("S36_DATABASE_NAME_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        if connection.info.dbname != "daon_user_s36_qa": pytest.fail("S36_DATABASE_NAME_REQUIRED", pytrace=False)
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0066",)]
        assert connection.execute("SELECT to_regclass('public.identity_account_profiles')").fetchone() == (None,)
        assert connection.execute("SELECT count(*) FROM public.identity_users").fetchone()[0] == 0
        protected_before = connection.execute("SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN ('public.identity_admin_audit_outbox'::regclass,'public.identity_session_audit_outbox'::regclass) ORDER BY 1,2").fetchall()
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    runtime_dsn = make_conninfo(dsn, options="-c role=daon_app")
    repository = PostgresIdentityRepository(runtime_dsn)
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(repository=repository, audit_store=audit, oidc_policies=(), clock=clock)
    password = secrets.token_urlsafe(24)
    with repository.transaction() as connection:
        for actor in ("s36-qa-a", "s36-qa-b"):
            repository._ensure_tenant(connection, actor)
            connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                (actor, "local", actor, actor, f"{actor}@example.test", PASSWORD_HASHER.hash(password), clock(), False, "active"))
            connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)", (actor, actor, "personal_owner"))
    credentials = identity.local_login(login_id="s36-qa-a", password=password, platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    with psycopg.connect(dsn) as connection:
        users_before = connection.execute("SELECT * FROM public.identity_users ORDER BY user_id").fetchall()
        sessions_before = connection.execute("SELECT * FROM public.identity_sessions ORDER BY session_id").fetchall()
    command.upgrade(config, "0067")
    command.downgrade(config, "0066")  # New tables empty, existing users/sessions are not removed.
    command.upgrade(config, "0067")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT * FROM public.identity_users ORDER BY user_id").fetchall() == users_before
        assert connection.execute("SELECT * FROM public.identity_sessions ORDER BY session_id").fetchall() == sessions_before
        assert connection.execute("SELECT conrelid::regclass::text,conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN ('public.identity_admin_audit_outbox'::regclass,'public.identity_session_audit_outbox'::regclass) ORDER BY 1,2").fetchall() == protected_before
        assert connection.execute("SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('public.identity_account_profiles'::regclass,'public.identity_account_requests'::regclass)").fetchall() == [("daon_account_owner", True, True)] * 2
        assert connection.execute("SELECT pg_has_role('daon_app','daon_account_owner','MEMBER'),has_table_privilege('daon_app','public.identity_account_requests','DELETE'),has_table_privilege('daon_app','public.identity_account_requests','UPDATE'),has_column_privilege('daon_app','public.identity_account_requests','response_json','UPDATE')").fetchone() == (False, False, False, False)
        owner, definer, search_path, result = connection.execute("SELECT pg_get_userbyid(proowner),prosecdef,proconfig,pg_get_function_result(oid) FROM pg_proc WHERE oid='public.pending_identity_account_audits(text)'::regprocedure").fetchone()
        assert owner == "daon_account_owner" and definer and search_path == ["search_path=pg_catalog, pg_temp"]
        assert all(field not in result for field in ("response_json", "display_name", "fingerprint", "idempotency_key"))
        assert connection.execute("SELECT EXISTS(SELECT 1 FROM pg_proc p,LATERAL aclexplode(p.proacl) a WHERE p.oid='public.pending_identity_account_audits(text)'::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')").fetchone() == (False,)
    service = AccountSettingsService(identity)
    assert service.get_profile(**_options(credentials.access_token)) == {"display_name": None, "version": 0}
    append = audit.append
    monkeypatch.setattr(audit, "append", lambda _draft: (_ for _ in ()).throw(OSError("isolated audit unavailable")))
    assert _save(service, credentials.access_token)["version"] == 1
    assert _save(service, credentials.access_token)["replayed"] is True
    with repository.transaction() as connection:
        account_actor_scope(connection, "s36-qa-b")
        assert connection.execute("SELECT * FROM account_profiles").fetchall() == []
        assert connection.execute("SELECT * FROM account_requests").fetchall() == []
    with pytest.raises(IdentityError):
        with repository.transaction() as connection:
            account_actor_scope(connection, "s36-qa-b")
            connection.execute("INSERT INTO account_profiles(user_id,display_name,version,updated_at) VALUES (?,?,?,?)", ("s36-qa-a", "forbidden", 1, clock()))
    with psycopg.connect(dsn) as connection:
        profile_before = connection.execute("SELECT * FROM public.identity_account_profiles").fetchall()
        requests_before = connection.execute("SELECT * FROM public.identity_account_requests").fetchall()
        assert password not in str(requests_before) and credentials.access_token not in str(requests_before)
    with pytest.raises(Exception, match="ACCOUNT_FORWARD_RECOVERY_REQUIRED"):
        command.downgrade(config, "0066")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("0067",)
        assert connection.execute("SELECT * FROM public.identity_account_profiles").fetchall() == profile_before
        assert connection.execute("SELECT * FROM public.identity_account_requests").fetchall() == requests_before
    restarted_repository = PostgresIdentityRepository(runtime_dsn)
    restarted = AccountSettingsService(IdentityService(repository=restarted_repository, audit_store=audit, oidc_policies=(), clock=clock))
    assert restarted.get_profile(**_options(credentials.access_token))["version"] == 1
    monkeypatch.setattr(audit, "append", append)
    restarted.dispatch_pending_audits()
    restarted.dispatch_pending_audits()
    assert len([event for event in audit.list(tenant_id="s36-qa-a").items if event.action == "identity.account.profile_updated"]) == 1
    def concurrent_update(key):
        own_repository = PostgresIdentityRepository(runtime_dsn)
        try:
            own_service = AccountSettingsService(IdentityService(repository=own_repository, audit_store=audit, oidc_policies=(), clock=clock))
            try: return _save(own_service, credentials.access_token, "parallel", 1, key)["version"]
            except IdentityError as error: return error.http_status
        finally: own_repository.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(concurrent_update, ("s36-parallel-01", "s36-parallel-02"))) == [2, 412]
    # Administrator cleanup removes mutable profile only; actor/audit intent survives.
    admin = AdminUserService(repository=repository, audit_store=audit, system_admin_user_ids=frozenset({"s36-admin"}), clock=clock)
    admin.delete_user(IdentityPrincipal("s36-admin", "s36-admin-session", "s36-admin-device", "s36-admin"),
        user_id="s36-qa-a", idempotency_key="s36-admin-delete-01", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT count(*) FROM public.identity_account_profiles").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM public.identity_account_requests WHERE actor_id='s36-qa-a'").fetchone() == (2,)
    repository.close()
    restarted_repository.close()
