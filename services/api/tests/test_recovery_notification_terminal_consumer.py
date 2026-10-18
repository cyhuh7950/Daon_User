"""신규 terminal 원업무→알림/읽음. storage/논리 principal은 시험 포트이며 실제 외부 복구/로그인이 아니다."""
from types import SimpleNamespace

import psycopg
import pytest

from daon_user_api.cloud_storage import CloudAccessContext
from daon_user_api.notification import NotificationError
from daon_user_api.notification_postgres import PostgresNotificationRepository
from daon_user_api.notification_recovery import materialize_pending_restore_completed
from daon_user_api.recovery import RecoveryError
from test_recovery_notification_events import recovery_notification_case, _execute, _sources
from test_recovery_notification_consumer import _service, _list, _read_arguments, _snapshot, _business


@pytest.mark.parametrize('terminal', ['backup.ready', 'restore.failed'])
def test_terminal_recovery_materializes_reopens_reads_and_current_revocation_blocks(
    recovery_notification_case, monkeypatch, terminal,
):
    case = recovery_notification_case
    if terminal == 'backup.ready':
        backup = case.service.create_backup(case.context, trigger='manual', schema_revision='0069',
            retention_watermark='s42-terminal-consumer', objects=case.objects,
            idempotency_key='s42-terminal-consumer-backup-0001')
        resource, identifier, title, kind, severity = 'backup', backup.backup_id, '백업 준비 완료', 'recovery', 'info'
    else:
        real_restore = case.storage.restore

        def fail(context, destination, objects):
            real_restore(context, destination, objects)
            raise RecoveryError('RESOURCE_UNAVAILABLE', 503, retryable=True)

        with monkeypatch.context() as patch:
            patch.setattr(case.storage, 'restore', fail)
            with pytest.raises(RecoveryError) as failed:
                _execute(case)
        assert (failed.value.code, failed.value.status) == ('RESOURCE_UNAVAILABLE', 503)
        resource, identifier, title, kind, severity = 'restore_request', case.preview.request_id, '복구 실패', 'warning', 'warning'
    source, = _sources(case.scope)
    assert (source['action'], source['target_type'], source['target_id'], source['state']) == (terminal, resource, identifier, 'pending')
    context = CloudAccessContext(case.context.tenant_id, case.context.workspace_id, case.context.actor_id, 'view')
    business = _business(case)
    with psycopg.connect(case.scope.app, connect_timeout=3) as connection:
        assert connection.execute("SELECT has_function_privilege('daon_app',"
            "'public.current_terminal_recovery_notification_origin(text,text)','EXECUTE')").fetchone() == (False,)
        with pytest.raises(psycopg.Error) as denied:
            with connection.transaction():
                connection.execute('SELECT public.current_terminal_recovery_notification_origin(%s,%s)',
                    (source['origin_event_id'], case.context.actor_id)).fetchone()
        assert denied.value.sqlstate == '42501'
    assert materialize_pending_restore_completed(case.scope.app, context=context) == 1
    delivered = _snapshot(case)
    assert materialize_pending_restore_completed(case.scope.app, context=context) == 0
    assert _snapshot(case) == delivered and _business(case) == business
    with _service(case, SimpleNamespace(context=context)) as (service, repository, principal):
        page = _list(case, service, principal)
        assert len(page.items) == page.unread_count == 1
        item = page.items[0]
        link = '/operations?' + ('backup_id=' if resource == 'backup' else 'restore_request_id=') + identifier
        assert (item.source_event_id, item.resource_type, item.resource_id, item.title, item.kind, item.severity, item.deep_link) == (
            source['source_event_id'], resource, identifier, title, kind, severity, link)
        assert service.get_notification(principal=principal, notification_id=item.notification_id,
            trace_id=case.context.trace_id, policy_version=case.context.policy_version) == item
        reopened = PostgresNotificationRepository(case.scope.app, context=context)
        assert reopened.notifications_for(tenant_id=context.tenant_id, recipient_id=context.actor_id) == (item,)
        assert _snapshot(case) == delivered
        arguments = _read_arguments(case, item, principal)
        read = service.mark_read(**arguments)
        assert read.version == 2 and read.read_at is not None
        after_read = _snapshot(case)
        assert service.mark_read(**arguments) == read and _snapshot(case) == after_read
        assert _business(case) == business
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            assert connection.execute("UPDATE public.identity_auth_tenant_roles SET state='suspended',version=version+1 "
                "WHERE tenant_id=%s AND user_id=%s AND role='personal_owner' RETURNING user_id",
                (context.tenant_id, context.actor_id)).fetchall() == [(context.actor_id,)]
        revoked = _snapshot(case)
        assert _list(case, service, principal).items == ()
        assert reopened.notification_for(tenant_id=context.tenant_id, recipient_id=context.actor_id,
            notification_id=item.notification_id) is None
        with pytest.raises(NotificationError) as hidden:
            service.get_notification(principal=principal, notification_id=item.notification_id,
                trace_id=case.context.trace_id, policy_version=case.context.policy_version)
        assert (hidden.value.code, hidden.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        with pytest.raises(NotificationError) as replay:
            service.mark_read(**arguments)
        assert (replay.value.code, replay.value.http_status) == ('RESOURCE_UNAVAILABLE', 404)
        assert _snapshot(case) == revoked and _business(case) == business


@pytest.mark.parametrize('terminal', ['backup.ready', 'restore.failed'])
def test_terminal_source_insert_then_sql_failure_rolls_back_final_tx_not_prior_materialize(
    recovery_notification_case, monkeypatch, terminal,
):
    from daon_user_api import notification_recovery
    from test_recovery_notification_events import _snapshot as recovery_snapshot, _assert_destination_metadata

    case = recovery_notification_case
    before, phases = recovery_snapshot(case), []
    real_emit = notification_recovery.enqueue_terminal_recovery_event

    def fail_after_real_source(connection, **arguments):
        inserted = real_emit(connection, **arguments)
        assert inserted == 1
        phases.append('actual_source_inserted')
        try:
            connection.execute('SELECT 1/0')
        except psycopg.Error as error:
            assert error.sqlstate == '22012'
            phases.append('sql_22012')
            raise
        pytest.fail('TERMINAL_SOURCE_FAILURE_NOT_INJECTED', pytrace=False)

    with monkeypatch.context() as patch:
        patch.setattr(notification_recovery, 'enqueue_terminal_recovery_event', fail_after_real_source)
        if terminal == 'restore.failed':
            real_materialize = case.service._materialize_fixture

            def fail_after_materialize(*arguments):
                real_materialize(*arguments)
                phases.append('actual_destination_commit')
                raise RecoveryError('RESOURCE_UNAVAILABLE', 503)

            patch.setattr(case.service, '_materialize_fixture', fail_after_materialize)
        with pytest.raises(RecoveryError) as failed:
            if terminal == 'backup.ready':
                case.service.create_backup(case.context, trigger='manual', schema_revision='0069',
                    retention_watermark='s42-terminal-rollback', objects=case.objects,
                    idempotency_key='s42-terminal-rollback-backup-0001')
            else:
                _execute(case)
    assert (failed.value.code, failed.value.status) == ('RESOURCE_UNAVAILABLE', 503)
    after = recovery_snapshot(case)
    if terminal == 'backup.ready':
        assert phases == ['actual_source_inserted', 'sql_22012']
        assert after == before
    else:
        assert phases == ['actual_destination_commit', 'actual_source_inserted', 'sql_22012']
        _assert_destination_metadata(case)
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT state,version,audit_event_id FROM public.restore_requests '
                'WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',
                (case.context.tenant_id, case.context.workspace_id, case.preview.request_id)).fetchone() == (
                    'restoring', 4, before['restore_requests'][0][7])
        for key in ('audits', 'sources', 'notification_counts', 'operations', 'restore_verifications'):
            assert after[key] == before[key]
        assert len(case.storage.restores) == 1
