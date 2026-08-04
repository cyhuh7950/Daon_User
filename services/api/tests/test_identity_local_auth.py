from datetime import datetime, timedelta, timezone

import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import DevicePlatform, IdentityError, IdentityService, SqliteIdentityRepository


class Sender:
    def __init__(self): self.messages = []
    def send(self, **message): self.messages.append(message)


class Clock:
    def __init__(self): self.now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    def __call__(self): return self.now
    def advance(self, **delta): self.now += timedelta(**delta)


def service(tmp_path, clock=None):
    sender = Sender()
    repo = SqliteIdentityRepository(tmp_path / "identity.db")
    service = IdentityService(repository=repo, audit_store=AuditEventStore(), oidc_policies=(), clock=clock or (lambda: datetime.now(timezone.utc)), email_sender=sender)
    return service, repo, sender


def test_signup_verify_login_and_password_reset(tmp_path):
    identity, repo, sender = service(tmp_path)
    identity.signup(login_id="alice", email="Alice@example.com", password="correct horse battery staple", trace_id="t1", policy_version="p1")
    assert len(sender.messages) == 1
    token = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
    identity.verify_email(token=token, trace_id="t2", policy_version="p1")
    credentials = identity.local_login(login_id="alice", password="correct horse battery staple", platform=DevicePlatform.WEB, trace_id="t3", policy_version="p1")
    assert credentials.user_id.startswith("usr-")
    identity.request_password_reset(identifier="alice", trace_id="t4", policy_version="p1")
    reset = sender.messages[-1]["body"].split(": ", 1)[1].splitlines()[0]
    identity.confirm_password_reset(token=reset, new_password="a newer correct password", trace_id="t5", policy_version="p1")
    with pytest.raises(IdentityError) as error:
        identity.validate_access(credentials.access_token, trace_id="t6", policy_version="p1")
    assert error.value.code == "SESSION_REVOKED"
    repo.close()


def test_smtp_missing_configuration_is_explicit(tmp_path):
    identity, repo, _ = service(tmp_path)
    identity._email_sender = type("Unavailable", (), {"send": lambda self, **kwargs: (_ for _ in ()).throw(IdentityError("EMAIL_DELIVERY_UNAVAILABLE", 503))})()
    with pytest.raises(IdentityError) as error:
        identity.signup(login_id="bob", email="bob@example.com", password="correct horse battery staple", trace_id="t1", policy_version="p1")
    assert error.value.code == "EMAIL_DELIVERY_UNAVAILABLE"
    repo.close()


def test_duplicate_signup_does_not_disclose_existing_account(tmp_path):
    identity, repo, sender = service(tmp_path)
    input_data = dict(login_id="alice", email="alice@example.com", password="correct horse battery staple", trace_id="t1", policy_version="p1")
    identity.signup(**input_data)
    identity.signup(**input_data)
    assert len(sender.messages) == 1
    repo.close()


def test_verification_and_reset_mail_requests_are_rate_limited(tmp_path):
    clock = Clock()
    identity, repo, sender = service(tmp_path, clock)
    identity.signup(login_id="alice", email="alice@example.com", password="correct horse battery staple", trace_id="t1", policy_version="p1")
    with pytest.raises(IdentityError) as verification_error:
        identity.resend_verification(identifier="alice", trace_id="t2", policy_version="p1")
    assert verification_error.value.code == "RATE_LIMITED"
    clock.advance(seconds=61)
    identity.resend_verification(identifier="alice", trace_id="t3", policy_version="p1")
    token = sender.messages[0]["body"].split(": ", 1)[1].splitlines()[0]
    identity.verify_email(token=token, trace_id="t4", policy_version="p1")
    identity.request_password_reset(identifier="alice", trace_id="t5", policy_version="p1")
    with pytest.raises(IdentityError) as reset_error:
        identity.request_password_reset(identifier="alice", trace_id="t6", policy_version="p1")
    assert reset_error.value.code == "RATE_LIMITED"
    repo.close()
