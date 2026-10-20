"""기존 인증을 바꾸지 않는 보안 알림의 엄격 flag 계약만 확인한다."""
from datetime import datetime, timezone
import secrets

import pytest

from daon_user_api.identity import IdentityService


def _service(**arguments):
    return IdentityService(repository=object(), audit_store=object(), oidc_policies=(),
        clock=lambda: datetime.now(timezone.utc), email_sender=object(),
        step_up_token_key=secrets.token_bytes(32), dispatch_pending_audits=False, **arguments)


@pytest.mark.parametrize('value', [None, 0, 1, '', 'false', 'true', []])
def test_identity_security_notification_flag_rejects_non_boolean(value):
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        _service(notification_inbox_enabled=value)


@pytest.mark.parametrize('arguments,expected', [({}, False), ({'notification_inbox_enabled': True}, True)])
def test_identity_security_notification_flag_defaults_off(arguments, expected):
    assert _service(**arguments)._notification_inbox_enabled is expected
