"""C3-only PG boundary test; main supplies a fresh dedicated 0066 baseline."""
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
from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import DevicePlatform, IdentityError, IdentityService, PASSWORD_HASHER, _digest, _iso
from daon_user_api.identity_postgres import PostgresIdentityRepository
from test_identity_support import POLICY_VERSION, TRACE_ID


@pytest.mark.skipif(not os.environ.get("DAON_TEST_ACCOUNT_POSTGRES_DSN"), reason="dedicated S36 PostgreSQL DSN not configured")
def test_c3_pg_sensitive_atomicity_target_lock_projection_and_strict_audit_payload(monkeypatch):
    dsn = os.environ["DAON_TEST_ACCOUNT_POSTGRES_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "daon_user_s36_qa": pytest.fail("S36_DATABASE_NAME_REQUIRED", pytrace=False)
    with psycopg.connect(dsn) as connection:
        assert connection.info.dbname == "daon_user_s36_qa"
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("0066",)
        assert connection.execute("SELECT count(*) FROM public.identity_users").fetchone() == (0,)
    monkeypatch.setenv("DAON_DB_MIGRATION_DSN", dsn)
    command.upgrade(Config(str(Path(__file__).resolve().parents[1]/"alembic.ini")), "0067")
    with psycopg.connect(dsn) as connection:
        # Real SQL, not a source-pattern assertion: JSON null/wrong types/extra
        # secret-like fields must fail the same CHECK used by the ledger.
        for event in (
            '{"action":null,"target_type":"device","target_id":"x","metadata":{}}',
            '{"action":"identity.device.trusted","target_type":null,"target_id":"x","metadata":{}}',
            '{"action":"identity.device.trusted","target_type":"device","target_id":"x","metadata":null}',
            '{"action":"identity.device.trusted","target_type":"device","target_id":"x","metadata":{"password":"rejected-input"}}',
        ):
            assert connection.execute("SELECT public.valid_identity_account_events(%s,'device.trust')", ("["+event+"]",)).fetchone() == (False,)
        result = connection.execute("SELECT pg_get_function_result('public.pending_identity_account_audits(text)'::regprocedure)").fetchone()[0]
        assert all(name not in result for name in ("display_name", "response_json", "fingerprint", "idempotency_key"))
        assert "audit_events_json" in result
    runtime_dsn = make_conninfo(dsn, options="-c role=daon_app")
    repository = PostgresIdentityRepository(runtime_dsn)
    audit = AuditEventStore()
    clock = lambda: datetime.now(timezone.utc)
    identity = IdentityService(repository=repository, audit_store=audit, oidc_policies=(), clock=clock)
    service = AccountSettingsService(identity)
    password = secrets.token_urlsafe(24)
    with repository.transaction() as connection:
        for actor in ("s36-c3-a", "s36-c3-b"):
            repository._ensure_tenant(connection, actor)
            connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                (actor,"local",actor,actor,f"{actor}@example.test",PASSWORD_HASHER.hash(password),clock(),False,"active"))
            connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)",(actor,actor,"personal_owner"))
    login = lambda actor: identity.local_login(login_id=actor,password=password,platform=DevicePlatform.WEB,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
    native_login = lambda: identity.local_native_login(login_id="s36-c3-a",password=password,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
    first, target, foreign = login("s36-c3-a"), native_login(), login("s36-c3-b")
    assert target.refresh_token is not None
    options = dict(access_token=first.access_token,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
    assert service.save_profile(**options,display_name="S36 C3 QA",expected_version=0,idempotency_key="s36-c3-profile-01",expected_session_id=first.session_id)["version"] == 1
    assert service.get_profile(**options) == {"display_name": "S36 C3 QA", "version": 1}
    device_page = service.list_devices(**options)
    session_page = service.list_sessions(**options)
    assert len(device_page["items"]) == len(session_page["items"]) == 2
    for row in device_page["items"]:
        assert "T" in row["last_seen_at"] and row["last_seen_at"].endswith("+00:00")
        datetime.fromisoformat(row["last_seen_at"])
    for row in session_page["items"]:
        assert "T" in row["expires_at"] and row["expires_at"].endswith("+00:00")
        datetime.fromisoformat(row["expires_at"])
    service.trust_device(**options,device_id=first.device_id,idempotency_key="s36-c3-trust-01")
    issue = lambda target_id: identity.issue_step_up_after_reauthentication(**options,password=password,action_group="device_session_or_sync_key_revoke",target_id=target_id)
    # A valid own grant aimed at someone else's device must not be consumed.
    invalid_grant = issue(foreign.device_id)
    with pytest.raises(IdentityError) as denied:
        service.revoke_device(**options,device_id=foreign.device_id,step_up_authorization=invalid_grant.authorization,idempotency_key="s36-c3-denied-01")
    assert denied.value.http_status == 404
    with repository.transaction() as connection:
        assert connection.execute("SELECT used_at FROM step_up_authorizations WHERE authorization_digest=?",(_digest(invalid_grant.authorization),)).fetchone()[0] is None
    grant = issue(target.device_id)
    arguments = dict(**options,device_id=target.device_id,step_up_authorization=grant.authorization,idempotency_key="s36-c3-revoke-01")
    original = service._record
    monkeypatch.setattr(service,"_record",lambda *args,**kwargs: (_ for _ in ()).throw(IdentityError("ACCOUNT_STORAGE_UNAVAILABLE",503)))
    with pytest.raises(IdentityError): service.revoke_device(**arguments)
    with repository.transaction() as connection:
        assert connection.execute("SELECT state FROM devices WHERE device_id=?",(target.device_id,)).fetchone()[0] == "registered"
        assert connection.execute("SELECT used_at FROM step_up_authorizations WHERE authorization_digest=?",(_digest(grant.authorization),)).fetchone()[0] is None
    monkeypatch.setattr(service,"_record",original)
    append = audit.append
    monkeypatch.setattr(audit,"append",lambda _draft: (_ for _ in ()).throw(OSError("isolated audit outage")))
    assert service.revoke_device(**arguments)["sync_key_revoke_required"] is True
    assert service.revoke_device(**arguments)["replayed"] is True
    with repository.transaction() as connection:
        account_actor_scope(connection, first.user_id)
        row = connection.execute("SELECT response_json,audit_events_json,delivered_at FROM account_requests WHERE operation='device.revoke'").fetchone()
        assert row is not None
        assert row["delivered_at"] is None
        assert all(secret not in str(row) for secret in (password,first.access_token,grant.authorization))
    assert identity.validate_access(first.access_token,trace_id=TRACE_ID,policy_version=POLICY_VERSION).user_id == first.user_id
    assert identity.validate_access(foreign.access_token,trace_id=TRACE_ID,policy_version=POLICY_VERSION).user_id == foreign.user_id
    monkeypatch.setattr(audit,"append",append)
    with pytest.raises(IdentityError) as revoked_access:
        identity.validate_access(target.access_token,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
    assert revoked_access.value.http_status == 401
    with pytest.raises(IdentityError) as revoked_refresh:
        identity.rotate_refresh(target.refresh_token,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
    assert revoked_refresh.value.http_status == 401
    service.dispatch_pending_audits(); service.dispatch_pending_audits()
    events = audit.list(tenant_id=first.tenant_id).items
    assert len([event for event in events if event.action=="identity.step_up.used"]) == 1
    assert len([event for event in events if event.action=="identity.session.revoked" and event.target_id==target.session_id]) == 1
    assert len([event for event in events if event.action=="identity.device.revoked"]) == 1
    # Independent repositories exercise the PG target/user locks, not an RLock.
    second_target = native_login()
    second_grant = issue(second_target.session_id)
    def revoke(key):
        own_repository = PostgresIdentityRepository(runtime_dsn)
        own_service = AccountSettingsService(IdentityService(repository=own_repository,audit_store=audit,oidc_policies=(),clock=clock))
        try:
            try: return own_service.revoke_session(**options,session_id=second_target.session_id,step_up_authorization=second_grant.authorization,idempotency_key=key)["replayed"]
            except IdentityError as error: return error.http_status
        finally: own_repository.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(revoke,("s36-c3-parallel-01",)*2)) == [False, True]
    with pytest.raises(IdentityError) as revoked_refresh:
        identity.rotate_refresh(second_target.refresh_token,trace_id=TRACE_ID,policy_version=POLICY_VERSION)
    assert revoked_refresh.value.http_status == 401
    repository.close()
