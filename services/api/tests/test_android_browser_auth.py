"""Android browser bridge storage and one-time exchange contract."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import base64
import hashlib

import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import SqliteAuthorizationRepository
from daon_user_api.identity import (
    ClientKind, DevicePlatform, IdentityError, IdentityService, SqliteIdentityRepository,
)


CLIENT = "qa-android"
REDIRECT = "https://auth.qa.example.test/android/callback"
ORIGIN = "https://auth.qa.example.test"
STATE = "synthetic-state-with-enough-entropy-123"
VERIFIER = "v" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class Sender:
    def __init__(self):
        self.messages = []

    def send(self, **message):
        self.messages.append(message)


@pytest.fixture
def setup(tmp_path):
    from daon_user_api.android_browser_auth import AndroidBrowserAuthService

    clock, sender = Clock(), Sender()
    path = tmp_path / "android-identity.sqlite"
    repo = SqliteIdentityRepository(path)
    SqliteAuthorizationRepository(path).close()
    identity = IdentityService(repository=repo, audit_store=AuditEventStore(),
                               oidc_policies=(), clock=clock, email_sender=sender)
    identity.signup(login_id="alice", email="alice@example.test",
                    password="correct horse battery staple", trace_id="setup", policy_version="p1")
    verification = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
    identity.verify_email(token=verification, trace_id="setup", policy_version="p1")
    web = identity.local_login(login_id="alice", password="correct horse battery staple",
                               platform=DevicePlatform.WEB, trace_id="setup", policy_version="p1")
    with repo.transaction() as connection:
        connection.execute(
            "INSERT INTO auth_workspaces(workspace_id,tenant_id,workspace_kind,data_area,cost_limit_cents,acl_version,version,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("qa-personal-workspace", web.tenant_id, "personal", "personal", 0, 1, 1, clock.now.isoformat()),
        )
        connection.execute(
            "INSERT INTO auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) VALUES (?,?,?,?,?,?)",
            (web.tenant_id, web.user_id, "personal_owner", "active", 1, clock.now.isoformat()),
        )
    service = AndroidBrowserAuthService(repository=repo, identity=identity, clock=clock,
                                        clients={CLIENT: frozenset({REDIRECT})},
                                        authorization_origin=ORIGIN)
    yield service, identity, repo, web, clock
    repo.close()


def begin(service):
    return service.begin(client_id=CLIENT, redirect_uri=REDIRECT, state=STATE,
                         code_challenge=CHALLENGE, code_challenge_method="S256", trace_id="qa")


def approve(service, web):
    start = begin(service)
    redirect = service.authorize(transaction_id=start.transaction_id,
                                 web_session=web.access_token,
                                 selected_tenant_id=web.tenant_id, trace_id="qa")
    return start, redirect


def exchange(service, start, redirect, verifier=VERIFIER):
    return service.exchange(transaction_id=start.transaction_id, code=redirect.code,
                            code_verifier=verifier, redirect_uri=REDIRECT, trace_id="qa")


def test_begin_requires_registered_https_client_redirect_and_s256(setup):
    service, _, _, _, _ = setup
    start = begin(service)
    assert start.authorization_url == f"{ORIGIN}/auth/android/authorize?transaction_id={start.transaction_id}"
    for changes in ({"client_id": "unknown"}, {"redirect_uri": "https://evil.example.test/callback"},
                    {"redirect_uri": "http://auth.qa.example.test/android/callback"},
                    {"code_challenge_method": "plain"}, {"code_challenge": "bad"}):
        request = dict(client_id=CLIENT, redirect_uri=REDIRECT, state=STATE,
                       code_challenge=CHALLENGE, code_challenge_method="S256", trace_id="qa")
        request.update(changes)
        with pytest.raises(IdentityError):
            service.begin(**request)


def test_unconfigured_policy_fails_closed(setup):
    from daon_user_api.android_browser_auth import AndroidBrowserAuthService
    _, identity, repo, _, clock = setup
    service = AndroidBrowserAuthService(repository=repo, identity=identity, clock=clock)
    with pytest.raises(IdentityError):
        begin(service)


@pytest.mark.parametrize("bad_redirect", ["https://localhost/android/callback",
                                          "https://auth.qa.example.test/android/callback\r"])
def test_policy_rejects_localhost_and_control_characters(setup, bad_redirect):
    from daon_user_api.android_browser_auth import AndroidBrowserAuthService
    _, identity, repo, _, clock = setup
    with pytest.raises(IdentityError):
        AndroidBrowserAuthService(repository=repo, identity=identity, clock=clock,
                                  clients={CLIENT: frozenset({bad_redirect})},
                                  authorization_origin=ORIGIN)


def test_exchange_creates_android_native_session_and_persists_only_code_digest(setup):
    service, identity, repo, web, _ = setup
    start, redirect = approve(service, web)
    credentials = exchange(service, start, redirect)
    assert credentials.client_kind is ClientKind.NATIVE
    assert credentials.user_id == web.user_id and credentials.tenant_id == web.tenant_id
    assert credentials.refresh_token and credentials.access_token != web.access_token
    with repo.transaction() as connection:
        row = connection.execute("SELECT code_digest,used_at FROM android_auth_transactions WHERE transaction_id=?",
                                 (start.transaction_id,)).fetchone()
        device = connection.execute("SELECT platform FROM devices WHERE device_id=?", (credentials.device_id,)).fetchone()
    assert row["code_digest"] == hashlib.sha256(redirect.code.encode()).hexdigest()
    assert row["used_at"] is not None and redirect.code not in str(tuple(row))
    assert device["platform"] == "android"
    assert identity.rotate_refresh(credentials.refresh_token, trace_id="qa", policy_version="p1").session_id == credentials.session_id


def test_transaction_and_code_have_separate_expiry(setup):
    service, _, _, web, clock = setup
    first = begin(service)
    clock.advance(minutes=5)
    with pytest.raises(IdentityError):
        service.authorize(transaction_id=first.transaction_id, web_session=web.access_token,
                          selected_tenant_id=web.tenant_id, trace_id="qa")
    second, redirect = approve(service, web)
    clock.advance(seconds=60)
    with pytest.raises(IdentityError):
        exchange(service, second, redirect)


def test_wrong_verifier_redirect_and_replay_do_not_issue_session(setup):
    service, _, repo, web, _ = setup
    start, redirect = approve(service, web)
    with pytest.raises(IdentityError):
        exchange(service, start, redirect, verifier="x" * 43)
    with pytest.raises(IdentityError):
        service.exchange(transaction_id=start.transaction_id, code=redirect.code,
                         code_verifier=VERIFIER, redirect_uri="https://evil.example.test/callback", trace_id="qa")
    first = exchange(service, start, redirect)
    with pytest.raises(IdentityError):
        exchange(service, start, redirect)
    with repo.transaction() as connection:
        count = connection.execute("SELECT count(*) FROM sessions WHERE session_id=?", (first.session_id,)).fetchone()[0]
    assert count == 1


def test_authorize_requires_current_web_user_and_selected_tenant(setup):
    service, identity, repo, web, _ = setup
    start = begin(service)
    with pytest.raises(IdentityError):
        service.authorize(transaction_id=start.transaction_id, web_session="invalid",
                          selected_tenant_id=web.tenant_id, trace_id="qa")
    with pytest.raises(IdentityError):
        service.authorize(transaction_id=start.transaction_id, web_session=web.access_token,
                          selected_tenant_id="other-tenant", trace_id="qa")
    with repo.transaction() as connection:
        connection.execute("UPDATE users SET state='suspended' WHERE user_id=?", (web.user_id,))
    with pytest.raises(IdentityError):
        service.authorize(transaction_id=start.transaction_id, web_session=web.access_token,
                          selected_tenant_id=web.tenant_id, trace_id="qa")


def test_exchange_rechecks_user_and_tenant_access(setup):
    service, _, repo, web, _ = setup
    start, redirect = approve(service, web)
    with repo.transaction() as connection:
        connection.execute("UPDATE auth_tenant_roles SET state='revoked' WHERE user_id=?", (web.user_id,))
    with pytest.raises(IdentityError):
        exchange(service, start, redirect)
    with repo.transaction() as connection:
        count = connection.execute("SELECT count(*) FROM sessions WHERE client_kind='native'").fetchone()[0]
    assert count == 0


def test_exchange_rechecks_disabled_user(setup):
    service, _, repo, web, _ = setup
    start, redirect = approve(service, web)
    with repo.transaction() as connection:
        connection.execute("UPDATE users SET state='suspended' WHERE user_id=?", (web.user_id,))
    with pytest.raises(IdentityError):
        exchange(service, start, redirect)
    with repo.transaction() as connection:
        row = connection.execute("SELECT used_at FROM android_auth_transactions WHERE transaction_id=?",
                                 (start.transaction_id,)).fetchone()
    assert row["used_at"] is None


def test_exchange_rolls_back_consumption_and_session_if_refresh_insert_fails(setup):
    service, _, repo, web, _ = setup
    start, redirect = approve(service, web)
    with repo.transaction() as connection:
        connection.execute("CREATE TRIGGER reject_android_refresh BEFORE INSERT ON refresh_tokens "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic refresh failure'); END")
    with pytest.raises(IdentityError) as failure:
        exchange(service, start, redirect)
    assert failure.value.code == "PERSISTENCE_UNAVAILABLE"
    with repo.transaction() as connection:
        row = connection.execute("SELECT used_at FROM android_auth_transactions WHERE transaction_id=?",
                                 (start.transaction_id,)).fetchone()
        count = connection.execute("SELECT count(*) FROM sessions WHERE client_kind='native'").fetchone()[0]
        connection.execute("DROP TRIGGER reject_android_refresh")
    assert row["used_at"] is None and count == 0
    assert exchange(service, start, redirect).client_kind is ClientKind.NATIVE


def test_pending_code_survives_service_restart(setup):
    from daon_user_api.android_browser_auth import AndroidBrowserAuthService
    service, identity, repo, web, clock = setup
    start, redirect = approve(service, web)
    resumed = AndroidBrowserAuthService(repository=repo, identity=identity, clock=clock,
                                        clients={CLIENT: frozenset({REDIRECT})},
                                        authorization_origin=ORIGIN)
    assert exchange(resumed, start, redirect).client_kind is ClientKind.NATIVE
    with pytest.raises(IdentityError):
        exchange(service, start, redirect)


def test_parallel_exchange_has_one_winner(setup):
    service, _, repo, web, _ = setup
    start, redirect = approve(service, web)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: _attempt_exchange(service, start, redirect), range(2)))
    assert sum(isinstance(item, str) for item in outcomes) == 1
    with repo.transaction() as connection:
        count = connection.execute("SELECT count(*) FROM sessions WHERE client_kind='native'").fetchone()[0]
    assert count == 1


def _attempt_exchange(service, start, redirect):
    try:
        return exchange(service, start, redirect).session_id
    except IdentityError as error:
        return error
