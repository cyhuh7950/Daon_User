"""C9 알림의 후보 SQL 한 함수 보호 검사. 소유 QA에서 savepoint 변조를 취소한다."""
import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from test_c9_role_notification_events import _service, _change, _ledger, _assert_changed_source
from test_organization_notification_events import organization_notification_case


def test_c9_sql_waits_for_actual_central_origin_and_keeps_source_pending(organization_notification_case):
    from daon_user_api.notification_events import _account_notification_scope

    case = organization_notification_case
    service, repository, actor = _service(case)
    try:
        key = 's42-c9-sql-before-central'
        assert _change(service, actor, case, key=key).version == 2
        source_id = _assert_changed_source(case, key)
        before = _ledger(case)
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            with _account_notification_scope(connection, tenant_id=case.tenant,
                workspace_id=case.workspace, actor_id=case.recipient) as reader:
                assert reader.execute('SELECT source_event_id FROM public.pending_c9_role_notification_sources(1)').fetchall() == [(source_id,)]
                assert reader.execute('SELECT public.current_c9_role_notification_target(%s)',
                    (source_id,)).fetchone() == ({'state': 'awaiting_origin'},)
                assert reader.execute('SELECT public.materialize_c9_role_notification(%s,true)',
                    (source_id,)).fetchone() == (None,)
                assert reader.execute('SELECT public.current_c9_role_notification_target(%s)',
                    ('s42-c9-not-my-source',)).fetchone() == (None,)
        assert _ledger(case) == before
    finally:
        repository.close()


def test_c9_enqueue_exact_probe_rejects_changed_shape_and_expanded_execute():
    from daon_user_api.notification_postgres import notification_storage_is_protected

    dsn = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:
        pytest.skip('소유 QA DSN 없음: 실제 PG 미실행')
    assert conninfo_to_dict(dsn).get('dbname') == 'daon_user_s42_qa_20261017_l2'
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT current_database(),(SELECT version_num FROM public.alembic_version)').fetchone() == (
            'daon_user_s42_qa_20261017_l2', '0069')
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone() == (
            'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
        assert notification_storage_is_protected(connection)
        for mutation in (
            'ALTER FUNCTION public.enqueue_c9_role_notification(text,text) STABLE',
            'GRANT EXECUTE ON FUNCTION public.enqueue_c9_role_notification(text,text) TO PUBLIC',
        ):
            connection.execute('SAVEPOINT s42_c9_negative')
            connection.execute(mutation)
            assert notification_storage_is_protected(connection) is False
            connection.execute('ROLLBACK TO SAVEPOINT s42_c9_negative')
            connection.execute('RELEASE SAVEPOINT s42_c9_negative')
            assert notification_storage_is_protected(connection)
