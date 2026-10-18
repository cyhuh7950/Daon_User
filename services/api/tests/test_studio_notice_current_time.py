"""Native 알림 목록의 현재 시각 분기 단위. 실제 PG 시간 경과 시험과 분리한다."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
from daon_user_api.studio_workspace import StudioContext


def test_native_due_notice_list_rechecks_database_time_after_source_access(monkeypatch):
    before = datetime.now(timezone.utc)
    deadline = before + timedelta(seconds=1)
    calls = []
    row = ('approval-notice-clock', 'request-clock', 'version-clock', 'due_24h',
        before-timedelta(hours=1), before-timedelta(hours=1), None, 1, 'pending', deadline, before)

    class Connection:
        def execute(self, query, params=None):
            if query.startswith('SELECT n.notification_id,'):
                return SimpleNamespace(fetchall=lambda: [row])
            if query.startswith('SELECT r.state,'):
                calls.append('current_state_and_time')
                return SimpleNamespace(fetchone=lambda: ('pending', deadline, deadline+timedelta(seconds=1)))
            pytest.fail('읽기 중 예상하지 않은 SQL: ' + query.split()[0])

    @contextmanager
    def transaction(_context):
        yield Connection()

    repository = StudioApprovalLifecycleRepository(SimpleNamespace(_transaction=transaction))
    monkeypatch.setattr(repository, '_grant', lambda *_: None)
    monkeypatch.setattr(repository, '_version_access', lambda *_: calls.append('current_source_access'))
    context = StudioContext('scope-tenant', 'scope-workspace', 'scope-user', 'trace-clock', 'policy-clock', 'notebook-clock')
    assert repository.list_notices(context) == {'items': [], 'unread_count': 0}
    assert calls == ['current_source_access', 'current_state_and_time']
