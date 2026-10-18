"""S42 조직 소비 SQL의 실제 원감사 미전달 경계. 정식 환경/화면 증거가 아니다."""
import psycopg
import pytest

from test_organization_notification_events import (
    organization_notification_case, _operation, _organization_notification_ledger,
)


def test_organization_notification_sql_keeps_source_pending_before_origin_audit(organization_notification_case):
    case = organization_notification_case
    _operation(case, 'organization.member.role', case.recipient,
        dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
            role='editor', expected_version=1, expected_auth_version=1, reason='S42_PENDING_ORIGIN'),
        's42-org-sql-pending-origin')
    before = _organization_notification_ledger(case)
    source = before['notification_source_outbox'][0][0]
    with psycopg.connect(case.app, connect_timeout=3) as connection:
        settings = {'app.tenant_id': case.tenant, 'app.workspace_id': case.workspace,
            'app.actor_id': case.recipient, 'app.capability': 'view',
            'daon.organization_actor': 's42-saved-manager-context',
            'daon.organization_tenant': 's42-saved-tenant-context'}
        for name, value in settings.items():
            connection.execute('SELECT set_config(%s,%s,true)', (name, value))
        pending = connection.execute('SELECT source_event_id FROM public.pending_organization_role_notification_sources(1)').fetchall()
        assert pending == [(source['source_event_id'],)]
        assert connection.execute('SELECT public.current_organization_role_notification_target(%s)',
            (source['source_event_id'],)).fetchone() == ({'state': 'awaiting_origin'},)
        assert connection.execute('SELECT public.materialize_organization_role_notification(%s,true)',
            (source['source_event_id'],)).fetchone() == (None,)
        assert connection.execute('SELECT public.current_organization_role_notification_target(%s)',
            ('s42-not-owned-source',)).fetchone() == (None,)
        assert connection.execute("SELECT current_setting('daon.organization_actor'),current_setting('daon.organization_tenant')").fetchone() == (
            settings['daon.organization_actor'], settings['daon.organization_tenant'])
    assert _organization_notification_ledger(case) == before


def test_organization_notification_sql_dispatches_real_origin_then_delivers_and_restores_scope(organization_notification_case):
    from daon_user_api.audit import PostgresSecurityAuditStore

    case = organization_notification_case
    _operation(case, 'organization.member.role', case.recipient,
        dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
            role='editor', expected_version=1, expected_auth_version=1, reason='S42_REAL_AUDIT_NOT_NOTIFICATION_TEXT'),
        's42-org-sql-real-delivery')
    source = _organization_notification_ledger(case)['notification_source_outbox'][0][0]
    audit = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        case.repository._audit_store = audit
        # 실제 기존 dispatcher를 사용한다. 원감사를 직접 삽입/append하거나 대체하지 않는다.
        assert case.repository.dispatch_pending_management_audits(limit=100) >= 1
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            settings = {'app.tenant_id': case.tenant, 'app.workspace_id': case.workspace,
                'app.actor_id': case.recipient, 'app.capability': 'view',
                'daon.organization_actor': 's42-saved-manager-context',
                'daon.organization_tenant': 's42-saved-tenant-context'}
            for name, value in settings.items():
                connection.execute('SELECT set_config(%s,%s,true)', (name, value))
            target = connection.execute('SELECT public.current_organization_role_notification_target(%s)',
                (source['source_event_id'],)).fetchone()[0]
            assert target['accessible'] is True
            assert (target['origin_event_id'], target['target_id'], target['source_event_id'], target['deep_link']) == (
                source['origin_event_id'], case.recipient, source['source_event_id'], '/notifications')
            assert 'S42_REAL_AUDIT_NOT_NOTIFICATION_TEXT' not in str(target)
            assert connection.execute("SELECT current_setting('daon.organization_actor'),current_setting('daon.organization_tenant')").fetchone() == (
                settings['daon.organization_actor'], settings['daon.organization_tenant'])
            delivered = connection.execute('SELECT public.materialize_organization_role_notification(%s,true)',
                (source['source_event_id'],)).fetchone()[0]
            assert delivered['state'] == 'delivered'
            assert delivered['draft']['action'] == 'notification.created'
            assert connection.execute('SELECT public.materialize_organization_role_notification(%s,true)',
                (source['source_event_id'],)).fetchone() == (None,)
            assert connection.execute('SELECT source_event_id FROM public.pending_organization_role_notification_sources(1)').fetchall() == []
        after = _organization_notification_ledger(case)
        assert len(after['notifications']) == len(after['notification_details']) == len(after['notification_audit_outbox']) == 1
        assert after['notification_source_outbox'][0][0]['state'] == 'delivered'
        with psycopg.connect(case.admin, connect_timeout=3) as connection:
            assert connection.execute('SELECT count(*) FROM security_audit_events WHERE event_id=%s AND tenant_id=%s',
                (source['origin_event_id'], case.tenant)).fetchone() == (1,)
    finally:
        audit.close()


@pytest.mark.parametrize('boundary', ['revoked', 'workspace_kind', 'sql_failure'])
def test_organization_notification_sql_rechecks_access_and_rolls_back_materialized_rows(
    organization_notification_case, boundary,
):
    from daon_user_api.audit import PostgresSecurityAuditStore

    case = organization_notification_case
    _operation(case, 'organization.member.role', case.recipient,
        dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
            role='editor', expected_version=1, expected_auth_version=1, reason='S42_SQL_BOUNDARY'),
        's42-org-sql-boundary-0001')
    source = _organization_notification_ledger(case)['notification_source_outbox'][0][0]
    audit = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        case.repository._audit_store = audit
        assert case.repository.dispatch_pending_management_audits(limit=100) >= 1
        if boundary == 'revoked':
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                connection.execute("UPDATE identity_auth_memberships SET state='revoked',version=version+1,updated_at=now() "
                    'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s', (case.tenant, case.workspace, case.recipient))
        elif boundary == 'workspace_kind':
            with psycopg.connect(case.admin, connect_timeout=3) as connection:
                connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())",
                    (case.tenant, case.recipient))
                connection.execute("UPDATE identity_auth_workspaces SET workspace_kind='personal' WHERE tenant_id=%s AND workspace_id=%s",
                    (case.tenant, case.workspace))
        before = _organization_notification_ledger(case)
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            settings = {'app.tenant_id': case.tenant, 'app.workspace_id': case.workspace,
                'app.actor_id': case.recipient, 'app.capability': 'view',
                'daon.organization_actor': 's42-saved-error-manager',
                'daon.organization_tenant': 's42-saved-error-tenant'}
            for name, value in settings.items():
                connection.execute('SELECT set_config(%s,%s,true)', (name, value))
            if boundary in ('revoked', 'workspace_kind'):
                if boundary == 'workspace_kind':
                    from daon_user_api.authorization import Action
                    from daon_user_api.cloud_storage import CloudAccessContext
                    from daon_user_api.notification_events import _NotificationTupleReadConnection
                    from daon_user_api.open_law_source_access import _current_auth_grant, OpenLawAccessError
                    with pytest.raises(OpenLawAccessError):
                        _current_auth_grant(CloudAccessContext(case.tenant, case.workspace, case.recipient, 'view'),
                            _NotificationTupleReadConnection(connection), Action.VIEW)
                target = connection.execute('SELECT public.current_organization_role_notification_target(%s)',
                    (source['source_event_id'],)).fetchone()[0]
                assert target['accessible'] is False
                assert connection.execute('SELECT public.materialize_organization_role_notification(%s,true)',
                    (source['source_event_id'],)).fetchone() == ({'state': 'suppressed'},)
            else:
                with pytest.raises(psycopg.Error) as failed:
                    with connection.transaction():
                        result = connection.execute('SELECT public.materialize_organization_role_notification(%s,true)',
                            (source['source_event_id'],)).fetchone()[0]
                        assert result['state'] == 'delivered' and result['draft']['action'] == 'notification.created'
                        connection.execute('SELECT 1/0')
                assert failed.value.sqlstate == '22012'
            assert connection.execute("SELECT current_setting('daon.organization_actor'),current_setting('daon.organization_tenant')").fetchone() == (
                settings['daon.organization_actor'], settings['daon.organization_tenant'])
        after = _organization_notification_ledger(case)
        assert after['notifications'] == after['notification_details'] == after['notification_audit_outbox'] == []
        if boundary == 'sql_failure':
            assert after == before
        else:
            assert after['notification_source_outbox'][0][0]['state'] == 'suppressed'
            assert {key: value for key, value in after.items() if key != 'notification_source_outbox'} == {
                key: value for key, value in before.items() if key != 'notification_source_outbox'}
    finally:
        audit.close()


def test_organization_notification_sql_rejects_same_origin_audit_with_different_reason(organization_notification_case):
    """부정 주입한 동일 ID의 다른 감사 내용도 원장과 불일치하면 소비하지 않아야 한다."""
    from dataclasses import replace
    from daon_user_api.audit import PostgresSecurityAuditStore
    from psycopg.rows import dict_row

    case = organization_notification_case
    _operation(case, 'organization.member.role', case.recipient,
        dict(tenant_id=case.tenant, user_id=case.recipient, workspace_id=case.workspace,
            role='editor', expected_version=1, expected_auth_version=1, reason='S42_ORIGINAL_REASON'),
        's42-org-different-audit-reason')
    before = _organization_notification_ledger(case)
    source = before['notification_source_outbox'][0][0]
    with psycopg.connect(case.admin, connect_timeout=3, row_factory=dict_row) as connection:
        origin = connection.execute('SELECT * FROM identity_org_operation_outbox WHERE event_id=%s',
            (source['origin_event_id'],)).fetchone()
    draft = case.repository._management_audit_draft(origin)
    audit = PostgresSecurityAuditStore(case.app, min_size=0, max_size=1)
    try:
        # 실제 중앙 저장기에 부정 사례를 주입한다. 정상 dispatcher 성공 증거로 계산하지 않는다.
        audit.append(replace(draft, metadata={**draft.metadata, 'reason': 'S42_DIFFERENT_REASON'}))
        with psycopg.connect(case.app, connect_timeout=3) as connection:
            for name, value in (('app.tenant_id', case.tenant), ('app.workspace_id', case.workspace),
                ('app.actor_id', case.recipient), ('app.capability', 'view'),
                ('daon.organization_actor', 's42-error-original-manager'),
                ('daon.organization_tenant', 's42-error-original-tenant')):
                connection.execute('SELECT set_config(%s,%s,true)', (name, value))
            with pytest.raises(psycopg.Error) as failed:
                with connection.transaction():
                    connection.execute('SELECT public.current_organization_role_notification_target(%s)',
                        (source['source_event_id'],)).fetchone()
            assert failed.value.sqlstate == '23514'
            assert connection.execute("SELECT current_setting('daon.organization_actor'),current_setting('daon.organization_tenant')").fetchone() == (
                's42-error-original-manager', 's42-error-original-tenant')
        assert _organization_notification_ledger(case) == before
    finally:
        audit.close()
