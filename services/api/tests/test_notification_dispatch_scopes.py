"""소유 QA의 실제 pending 범위 발견. 원문/사용자 인증/정식 worker 성공 증거가 아니다."""
import psycopg
import pytest

from test_egress_policy_notification_events import _activate, _policy_case
from test_organization_notification_events import organization_notification_case


_SIGNATURE = 'public.pending_notification_dispatch_scopes(integer,text,text,text)'
_CALL = 'SELECT * FROM public.pending_notification_dispatch_scopes(%s,%s,%s,%s)'


def test_dispatch_scopes_reads_real_pending_ids_with_cursor_and_no_direct_parent_grant(
    organization_notification_case,
):
    case = organization_notification_case
    with _policy_case(case) as policy:
        _activate(policy, 's42-dispatch-actual-policy-pending-0001')
        with psycopg.connect(case.admin, connect_timeout=3) as admin:
            expected = set()
            for table in ('notification_source_outbox', 'notification_audit_outbox'):
                expected.update(admin.execute(f'SELECT tenant_id,workspace_id,recipient_id FROM public.{table} '
                    "WHERE state='pending'").fetchall())
            expected.update(admin.execute('SELECT n.tenant_id,n.workspace_id,n.recipient_id '
                'FROM public.studio_approval_notices n WHERE NOT EXISTS('
                'SELECT 1 FROM public.notification_audit_outbox a WHERE a.tenant_id=n.tenant_id '
                'AND a.workspace_id=n.workspace_id AND a.recipient_id=n.recipient_id '
                'AND a.notification_id=n.notification_id AND a.source_event_id=n.notification_id '
                "AND a.action='notification.created')").fetchall())
            assert {(case.tenant, case.workspace, user) for user in (case.manager, case.recipient)} <= expected
            assert admin.execute('SELECT to_regprocedure(%s)', (_SIGNATURE,)).fetchone()[0] is not None, \
                'NOTIFICATION_PENDING_SCOPE_DISCOVERY_NOT_IMPLEMENTED'
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            assert connection.execute('SELECT current_user').fetchone() == ('daon_app',)
            saved = {'app.tenant_id': case.tenant, 'app.workspace_id': case.workspace,
                'app.actor_id': case.manager, 'app.capability': 'view'}
            for name, value in saved.items():
                connection.execute('SELECT set_config(%s,%s,true)', (name, value))
            connection.execute("SET LOCAL statement_timeout='5s'")
            found, cursor = [], (None, None, None)
            while True:
                page = connection.execute(_CALL, (17, *cursor)).fetchall()
                assert connection.execute('SELECT current_setting(%s,true)', ('app.capability',)).fetchone() == ('view',)
                assert len(page) <= 17 and len(page) == len(set(page))
                if page:
                    placeholders = ','.join('(%s,%s,%s)' for _ in page)
                    ordered = connection.execute('SELECT * FROM (VALUES '+placeholders+
                        ') AS p(t,w,u) ORDER BY t,w,u', tuple(value for row in page for value in row)).fetchall()
                    assert page == ordered
                assert all(len(row) == 3 and all(isinstance(value, str) for value in row) for row in page)
                if not page:
                    break
                if cursor[0] is not None:
                    # DB tuple 순서만 사용한다. Python/DB collation 순서가 같다고 추정하지 않는다.
                    assert connection.execute('SELECT ROW(%s,%s,%s)>ROW(%s,%s,%s)',
                        (*page[0], *cursor)).fetchone() == (True,)
                found.extend(page)
                cursor = page[-1]
            assert len(found) == len(set(found)) and set(found) == expected
            for arguments in ((0, None, None, None), (101, None, None, None), (1, case.tenant, None, None)):
                with pytest.raises(psycopg.Error) as invalid:
                    with connection.transaction():
                        connection.execute(_CALL, arguments).fetchall()
                assert invalid.value.sqlstate == '22023'
                assert connection.execute('SELECT current_setting(%s,true)', ('app.capability',)).fetchone() == ('view',)
            # 발견 문맥은 앱을 내부 역할로 바꾸지 않는다. source/audit 직접 SELECT는 계속 금지다.
            connection.execute("SELECT set_config('app.capability','notification.dispatch.scopes',true)")
            for table in ('notification_source_outbox', 'notification_audit_outbox'):
                with pytest.raises(psycopg.Error) as denied:
                    with connection.transaction():
                        connection.execute(f'SELECT tenant_id FROM public.{table} LIMIT 1').fetchall()
                assert denied.value.sqlstate == '42501'
            connection.execute("SELECT set_config('app.capability','view',true)")
            for name, value in saved.items():
                assert connection.execute('SELECT current_setting(%s,true)', (name,)).fetchone() == (value,)
