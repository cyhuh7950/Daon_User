from __future__ import annotations

import json
import secrets
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import psycopg

from daon_user_api.account_settings import AccountSettingsService
from daon_user_api.admin_users import AdminUserService
from daon_user_api.identity import DevicePlatform, IdentityError, IdentityPrincipal, PASSWORD_HASHER, _digest
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service


class AccountFixture(tuple):
    def __repr__(self): return "<isolated account fixture: credentials redacted>"


@pytest.fixture
def account():
    with tempfile.TemporaryDirectory(prefix="s36-account-", dir=".superpowers") as directory:
        identity, repository, audit, clock = create_service(Path(directory) / "identity.sqlite3")
        password = secrets.token_urlsafe(24)
        with repository.transaction() as connection:
            for name in ("account-a", "account-b"):
                repository._ensure_tenant(connection, name)
                connection.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,password_change_required,state) VALUES (?,?,?,?,?,?,?,?,?)",
                    (name, "local", name, name, f"{name}@example.test", PASSWORD_HASHER.hash(password), clock().isoformat(), False, "active"))
                connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES (?,?,?)", (name, name, "personal_owner"))
                connection.execute("INSERT INTO tenant_step_up_actions(tenant_id,action_group,is_mandatory) VALUES (?,?,1)", (name, "device_session_or_sync_key_revoke"))
        credentials = {name: identity.local_login(login_id=name, password=password, platform=DevicePlatform.WEB,
            trace_id=TRACE_ID, policy_version=POLICY_VERSION) for name in ("account-a", "account-b")}
        service = AccountSettingsService(identity)
        yield AccountFixture((service, identity, repository, audit, credentials, password))
        repository.close()


def options(token):
    return dict(access_token=token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)


def save(service, token, name="표시 이름", version=0, key="account-key-00001"):
    with service.repository.transaction() as connection:
        expected = connection.execute("SELECT session_id FROM sessions WHERE access_digest=?", (_digest(token),)).fetchone()[0]
    return service.save_profile(**options(token), display_name=name, expected_version=version, idempotency_key=key, expected_session_id=expected)


def test_profile_null_version_and_only_display_name_changes(account):
    service, _identity, repository, _audit, credentials, _password = account
    token = credentials["account-a"].access_token
    with repository.transaction() as connection:
        before = dict(connection.execute("SELECT * FROM users WHERE user_id='account-a'").fetchone())
    assert service.get_profile(**options(token)) == {"display_name": None, "version": 0}
    assert save(service, token, "  이름 😀  ") == {"display_name": "이름 😀", "version": 1, "replayed": False}
    assert service.get_profile(**options(token)) == {"display_name": "이름 😀", "version": 1}
    assert service.get_profile(**options(credentials["account-b"].access_token)) == {"display_name": None, "version": 0}
    with repository.transaction() as connection:
        assert dict(connection.execute("SELECT * FROM users WHERE user_id='account-a'").fetchone()) == before


def test_profile_exact_bounds_and_idempotency_conflict(account):
    service, _identity, _repository, _audit, credentials, _password = account
    token = credentials["account-a"].access_token
    for invalid in ("", "   ", "x" * 81, "a\nb", "a\x7fb", None):
        with pytest.raises(IdentityError) as error: save(service, token, invalid)
        assert error.value.http_status == 422
    assert save(service, token, "x" * 80)["version"] == 1
    assert save(service, token, "x" * 80) == {"display_name": "x" * 80, "version": 1, "replayed": True}
    with pytest.raises(IdentityError) as error: save(service, token, "different")
    assert error.value.http_status == 409
    for version in (True, -1, "1"):
        with pytest.raises(IdentityError) as error: save(service, token, "a", version, "invalid-version-key")
        assert error.value.http_status == 422
    with pytest.raises(IdentityError) as error: save(service, token, "a", 0, "stale-version-key")
    assert error.value.http_status == 412


def test_profile_request_failure_rolls_back_and_audit_outage_is_durable(account, monkeypatch):
    service, _identity, repository, audit, credentials, password = account
    token = credentials["account-a"].access_token
    original = service._record
    def failed(*args, **kwargs): raise IdentityError("ACCOUNT_STORAGE_UNAVAILABLE", 503)
    monkeypatch.setattr(service, "_record", failed)
    with pytest.raises(IdentityError): save(service, token)
    assert service.get_profile(**options(token)) == {"display_name": None, "version": 0}
    monkeypatch.setattr(service, "_record", original)
    append = audit.append
    monkeypatch.setattr(audit, "append", lambda _draft: (_ for _ in ()).throw(OSError("private audit failure")))
    assert save(service, token)["version"] == 1
    with repository.transaction() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM account_requests").fetchall()]
    assert len(rows) == 1 and rows[0]["delivered_at"] is None
    assert password not in json.dumps(rows) and token not in json.dumps(rows)
    monkeypatch.setattr(audit, "append", append)
    service.dispatch_pending_audits()
    service.dispatch_pending_audits()
    events = [item for item in audit.list(tenant_id="account-a").items if item.action == "identity.account.profile_updated"]
    assert len(events) == 1
    assert events[0].target_id == "account-a"


def test_profile_cross_session_conflict_and_revoked_access(account):
    service, identity, repository, _audit, credentials, password = account
    first = credentials["account-a"]
    second = identity.local_login(login_id="account-a", password=password, platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    def change(pair):
        token, key = pair
        try: return save(AccountSettingsService(identity), token, key, 0, key)["version"]
        except IdentityError as error: return error.http_status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, [(first.access_token, "parallel-account-key-1"), (second.access_token, "parallel-account-key-2")])) == [1, 412]
    with repository.transaction() as connection:
        connection.execute("UPDATE sessions SET state='revoked' WHERE session_id=?", (first.session_id,))
    with pytest.raises(IdentityError) as error: service.get_profile(**options(first.access_token))
    assert error.value.http_status == 401
    assert service.get_profile(**options(second.access_token))["version"] == 1


@pytest.mark.parametrize("phase", ["lookup", "mark"])
@pytest.mark.parametrize("rollback_failure", [False, True])
def test_committed_profile_survives_deferred_audit_database_failure(account, monkeypatch, phase, rollback_failure):
    service, _identity, repository, audit, credentials, _password = account
    token = credentials["account-a"].access_token
    connect = repository._connect
    class FaultConnection:
        def __init__(self): self.inner = connect(); self.failed = False
        @property
        def in_transaction(self): return self.inner.in_transaction
        def close(self): self.inner.close()
        def execute(self, sql, params=()):
            if (phase == "lookup" and "SELECT * FROM account_requests" in sql or
                    phase == "mark" and "UPDATE account_requests SET delivered_at" in sql):
                self.failed = True
                raise sqlite3.OperationalError("private transport failure")
            if self.failed and rollback_failure and sql == "ROLLBACK":
                raise sqlite3.OperationalError("private rollback transport failure")
            return self.inner.execute(sql, params)
    monkeypatch.setattr(repository, "_connect", FaultConnection)
    # The product transaction has already committed: dispatch must not change its receipt.
    assert save(service, token) == {"display_name": "표시 이름", "version": 1, "replayed": False}
    monkeypatch.setattr(repository, "_connect", connect)
    with repository.transaction() as connection:
        row = connection.execute("SELECT delivered_at FROM account_requests").fetchone()
        assert row["delivered_at"] is None
        assert connection.execute("SELECT count(*) FROM account_requests").fetchone()[0] == 1
    assert save(service, token)["replayed"] is True
    assert service.get_profile(**options(token))["version"] == 1
    service.dispatch_pending_audits()
    events = [item for item in audit.list(tenant_id="account-a").items if item.action == "identity.account.profile_updated"]
    assert len(events) == 1


def test_administrator_deletes_mutable_profile_without_removing_account_history(account):
    service, _identity, repository, audit, credentials, _password = account
    assert save(service, credentials["account-a"].access_token)["version"] == 1
    admin = AdminUserService(repository=repository, audit_store=audit, system_admin_user_ids=frozenset({"admin"}), clock=service.identity._clock)
    result = admin.delete_user(IdentityPrincipal("admin", "admin-session", "admin-device", "admin"), user_id="account-a",
        idempotency_key="account-delete-key-001", trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert result.user_id == "account-a"
    with repository.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM account_profiles").fetchone()[0] == 0
        assert connection.execute("SELECT actor_id FROM account_requests").fetchone()[0] == "account-a"
        assert connection.execute("SELECT count(*) FROM users WHERE user_id='account-a'").fetchone()[0] == 0


@pytest.mark.parametrize("connection_number", [3, 4])
def test_committed_receipt_survives_audit_connection_creation_failure(account, monkeypatch, connection_number):
    service, _identity, repository, audit, credentials, _password = account
    token = credentials["account-a"].access_token
    connect = repository._connect
    calls = 0  # fixture binding lookup, product commit, audit lookup, audit mark
    def unavailable():
        nonlocal calls
        calls += 1
        if calls == connection_number: raise psycopg.OperationalError("private connection unavailable")
        return connect()
    monkeypatch.setattr(repository, "_connect", unavailable)
    assert save(service, token)["version"] == 1
    monkeypatch.setattr(repository, "_connect", connect)
    assert save(service, token)["replayed"] is True
    assert service.get_profile(**options(token))["version"] == 1
    assert len([item for item in audit.list(tenant_id="account-a").items if item.action == "identity.account.profile_updated"]) == 1


def test_account_lists_are_scoped_exact_and_cursor_bound(account):
    service, identity, repository, _audit, credentials, password = account
    first = credentials["account-a"]
    other = identity.local_login(login_id="account-a", password=password, platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    for method, fields, id_field in ((service.list_devices, {"device_id", "platform", "state", "last_seen_at", "is_current"}, "device_id"),
            (service.list_sessions, {"session_id", "device_id", "client_kind", "state", "expires_at", "is_current"}, "session_id")):
        page = method(**options(first.access_token), limit=1)
        assert len(page["items"]) == 1 and page["next_cursor"]
        following = method(**options(first.access_token), limit=1, cursor=page["next_cursor"])
        assert following["next_cursor"] is None
        rows = page["items"] + following["items"]
        assert len({row[id_field] for row in rows}) == 2
        assert all(set(row) == fields for row in rows)
        assert sum(row["is_current"] for row in rows) == 1
        for cursor, token in ((page["next_cursor"] + "x", first.access_token), (page["next_cursor"], credentials["account-b"].access_token), (page["next_cursor"], other.access_token)):
            with pytest.raises(IdentityError) as invalid: method(**options(token), cursor=cursor)
            assert invalid.value.http_status == 422
        for limit in (0, 101, True, "20"):
            with pytest.raises(IdentityError): method(**options(first.access_token), limit=limit)
    with repository.transaction() as connection:
        # Same actor in a different tenant must not leak into either projection.
        connection.execute("UPDATE devices SET tenant_id='account-b' WHERE device_id=?", (other.device_id,))
        connection.execute("UPDATE sessions SET tenant_id='account-b' WHERE session_id=?", (other.session_id,))
    assert len(service.list_devices(**options(first.access_token))["items"]) == 1
    assert len(service.list_sessions(**options(first.access_token))["items"]) == 1


def test_existing_device_revoke_rejects_other_owner_before_consuming_step_up(account):
    _service, identity, repository, _audit, credentials, _password = account
    first, other = credentials["account-a"], credentials["account-b"]
    with repository.transaction() as connection:
        connection.execute("UPDATE devices SET tenant_id='account-a' WHERE device_id=?", (other.device_id,))
    grant = identity.issue_step_up(access_token=first.access_token, action_group="device_session_or_sync_key_revoke", target_id=other.device_id, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    with pytest.raises(IdentityError) as denied:
        identity.revoke_device(**options(first.access_token), device_id=other.device_id, step_up_authorization=grant.authorization)
    assert denied.value.http_status == 404
    with repository.transaction() as connection:
        assert connection.execute("SELECT used_at FROM step_up_authorizations").fetchone()[0] is None
        assert connection.execute("SELECT state FROM devices WHERE device_id=?", (other.device_id,)).fetchone()[0] == "registered"


def test_account_sensitive_mutation_is_atomic_replayed_and_deferred(account, monkeypatch):
    service, identity, repository, audit, credentials, password = account
    first = credentials["account-a"]
    other = identity.local_login(login_id="account-a", password=password, platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert service.trust_device(**options(first.access_token), device_id=first.device_id, idempotency_key="account-trust-0001")["replayed"] is False
    with pytest.raises(IdentityError): service.trust_device(**options(first.access_token), device_id=other.device_id, idempotency_key="account-trust-other")
    grant = identity.issue_step_up_after_reauthentication(**options(first.access_token), password=password,
        action_group="device_session_or_sync_key_revoke", target_id=other.device_id)
    arguments = dict(**options(first.access_token), device_id=other.device_id, step_up_authorization=grant.authorization, idempotency_key="account-revoke-0001")
    record = service._record
    monkeypatch.setattr(service, "_record", lambda *args, **kwargs: (_ for _ in ()).throw(IdentityError("ACCOUNT_STORAGE_UNAVAILABLE", 503)))
    with pytest.raises(IdentityError): service.revoke_device(**arguments)
    with repository.transaction() as connection:
        assert connection.execute("SELECT used_at FROM step_up_authorizations").fetchone()[0] is None
        assert connection.execute("SELECT state FROM devices WHERE device_id=?", (other.device_id,)).fetchone()[0] == "registered"
    monkeypatch.setattr(service, "_record", record)
    append = audit.append
    monkeypatch.setattr(audit, "append", lambda _draft: (_ for _ in ()).throw(OSError("isolated audit unavailable")))
    result = service.revoke_device(**arguments)
    assert result == {"device_id": other.device_id, "sync_key_revoke_required": True, "replayed": False}
    assert service.revoke_device(**arguments)["replayed"] is True
    with pytest.raises(IdentityError): identity.validate_access(other.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert service.get_profile(**options(first.access_token))["version"] == 0
    with repository.transaction() as connection:
        request = dict(connection.execute("SELECT * FROM account_requests WHERE operation='device.revoke'").fetchone())
        assert password not in json.dumps(request) and grant.authorization not in json.dumps(request)
    monkeypatch.setattr(audit, "append", append)
    service.dispatch_pending_audits()
    service.dispatch_pending_audits()
    events = audit.list(tenant_id="account-a").items
    assert len([item for item in events if item.action == "identity.step_up.used"]) == 1
    assert len([item for item in events if item.action == "identity.device.revoked"]) == 1
    assert any(item.action == "identity.session.revoked" and item.target_id == other.session_id for item in events)


def test_shared_sensitive_methods_preserve_safe_denied_audit_before_step_up(account):
    _service, identity, repository, audit, credentials, _password = account
    actor, foreign = credentials["account-a"], credentials["account-b"]
    with repository.transaction() as connection:
        connection.execute("UPDATE devices SET state='revoked' WHERE device_id=?", (actor.device_id,))
    for target in (foreign.device_id, "dev-missing", actor.device_id):
        with pytest.raises(IdentityError): identity.trust_device(**options(actor.access_token), device_id=target)
    denied = [event for event in audit.list(tenant_id="account-a").items if event.action == "identity.device.trust_denied"]
    assert len(denied) == 3
    assert all(event.metadata["reason_code"] == "DEVICE_BINDING_DENIED" for event in denied)
    for target in (foreign.session_id, "ses-missing"):
        grant = identity.issue_step_up(**options(actor.access_token), action_group="device_session_or_sync_key_revoke", target_id=target)
        with pytest.raises(IdentityError): identity.revoke_session(**options(actor.access_token), session_id=target, step_up_authorization=grant.authorization)
    denied = [event for event in audit.list(tenant_id="account-a").items if event.action == "identity.session.revoke_denied"]
    assert len(denied) == 2 and all(event.target_id == "unavailable-session" for event in denied)
    assert all(event.metadata["reason_code"] == "SESSION_TARGET_UNAVAILABLE" for event in denied)
    with repository.transaction() as connection:
        assert all(row[0] is None for row in connection.execute("SELECT used_at FROM step_up_authorizations").fetchall())


def test_profile_session_binding_precedes_replay_and_preserves_all_account_writes(account, monkeypatch):
    service, identity, repository, audit, credentials, password = account
    first = credentials["account-a"]
    second = identity.local_login(login_id="account-a", password=password, platform=DevicePlatform.WEB, trace_id=TRACE_ID, policy_version=POLICY_VERSION)
    assert save(service, first.access_token)["version"] == 1
    before_events = len(audit.list(tenant_id="account-a").items)
    def replay_must_not_run(*args): raise AssertionError("binding must precede replay")
    monkeypatch.setattr(service, "_replay", replay_must_not_run)
    for token in (second.access_token, credentials["account-b"].access_token):
        with pytest.raises(IdentityError) as changed:
            service.save_profile(**options(token), display_name="표시 이름", expected_version=0, idempotency_key="account-key-00001", expected_session_id=first.session_id)
        assert changed.value.code == "ACCOUNT_SESSION_CHANGED" and changed.value.http_status == 409
    for malformed in (None, "", 123, "bad/id", "x"*129):
        with pytest.raises(IdentityError) as invalid:
            service.save_profile(**options(first.access_token), display_name="표시 이름", expected_version=1, idempotency_key="malformed-binding-01", expected_session_id=malformed)
        assert invalid.value.http_status == 422
    with repository.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM account_profiles").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM account_requests").fetchone()[0] == 1
    assert service.get_profile(**options(first.access_token)) == {"display_name": "표시 이름", "version": 1}
    assert service.get_profile(**options(credentials["account-b"].access_token)) == {"display_name": None, "version": 0}
    assert len(audit.list(tenant_id="account-a").items) == before_events
