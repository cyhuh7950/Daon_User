from datetime import datetime, timezone

import pytest

from daon_user_api.audit import AuditEventStore
from daon_user_api.identity import DevicePlatform, IdentityError, IdentityService, SqliteIdentityRepository


class Sender:
    def __init__(self): self.messages = []
    def send(self, **message): self.messages.append(message)


def service(tmp_path):
    sender = Sender()
    repo = SqliteIdentityRepository(tmp_path / "identity.db")
    service = IdentityService(repository=repo, audit_store=AuditEventStore(), oidc_policies=(), clock=lambda: datetime.now(timezone.utc), email_sender=sender)
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
