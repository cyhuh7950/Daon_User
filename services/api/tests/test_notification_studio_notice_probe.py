"""S24 신규 제한 함수/trigger의 보호 probe만 검증한다. 업무 여정 성공과 분리한다."""
from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from daon_user_api.notification_postgres import notification_storage_is_protected


def test_s24_probe_rejects_changed_function_flags_private_execute_and_missing_hook():
    dsn = os.environ.get('DAON_NOTIFICATION_TEST_DSN')
    if not dsn:
        pytest.skip('S42 소유 격리 PG DSN 미제공')
    if conninfo_to_dict(dsn).get('dbname') != 'daon_user_s42_qa_20261017_l2':
        pytest.fail('S42_PROBE_OWNED_DATABASE_REQUIRED', pytrace=False)

    class RollbackProbeMutation(Exception):
        pass

    mutations = (
        ('flags', 'ALTER FUNCTION public.current_studio_notice_notification_audit(text) STABLE'),
        ('private_execute', 'GRANT EXECUTE ON FUNCTION public.current_studio_notice_notification_target(text) TO daon_app'),
        ('hook', 'DROP TRIGGER studio_notice_notification_created ON public.studio_approval_notices'),
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone() == (
            'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
        assert notification_storage_is_protected(connection) is True
        rows_before = {table: connection.execute(f"SELECT count(*),md5(coalesce(string_agg(to_jsonb(r)::text,'' "
            f"ORDER BY to_jsonb(r)::text),'')) FROM public.{table} r").fetchone() for table in (
                'studio_approval_notices', 'notifications', 'notification_details', 'notification_source_outbox',
                'notification_audit_outbox')}
        for _label, statement in mutations:
            # 단일 세션의 savepoint 안에서만 변경하고 즉시 취소한다. 다른 연결에는 공개하지 않는다.
            with pytest.raises(RollbackProbeMutation):
                with connection.transaction():
                    connection.execute(statement)
                    assert notification_storage_is_protected(connection) is False
                    raise RollbackProbeMutation()
            assert notification_storage_is_protected(connection) is True
        assert {table: connection.execute(f"SELECT count(*),md5(coalesce(string_agg(to_jsonb(r)::text,'' "
            f"ORDER BY to_jsonb(r)::text),'')) FROM public.{table} r").fetchone() for table in rows_before} == rows_before
