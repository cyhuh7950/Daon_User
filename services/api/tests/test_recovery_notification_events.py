"""S42 승인된 Backup/Restore 확정 원사건의 source 저장 경계. 실행과 SQL은 main 소유다.

storage만 외부 I/O 없는 합성 포트다. 실제 복구 서비스와 목적지 metadata 저장은
기존 메서드를 사용하며 외부 Object·전체 DB·HTTP 복구 성공을 증명하지 않는다.
backup.ready/restore.failed는 추가 승인된 L2-R의 실제 전이/원감사/source를 요구한다.
"""
from __future__ import annotations

import hashlib
import inspect
import secrets
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from daon_user_api import recovery_postgres
from daon_user_api.authorization import Action, Role
from daon_user_api.notification_events import _NotificationTupleReadConnection
from daon_user_api.open_law_source_access import _current_auth_grant
from daon_user_api.recovery import BackupObjectInput, RecoveryContext, RecoveryError, RestoreDestination
from daon_user_api.recovery_postgres import PostgresRecoveryService

from test_notification_events import _existing_0069_studio_case, _studio_case_member


class _RecoveryCase(SimpleNamespace):
    def __repr__(self):
        return '<S42 소유 QA 복구 metadata fixture: 연결값 비출력>'


class _MetadataOnlyStorage:
    """허용한 합성 입력만 받는 외부 storage 포트. DB 저장 메서드는 대체하지 않는다."""

    def __init__(self, context, destination, objects):
        self.context, self.destination, self.objects = context, destination, objects
        self.verifications, self.restores = [], []

    def verify_source(self, context, objects):
        assert context == self.context and objects == self.objects
        self.verifications.append((context, objects))

    def restore(self, context, destination, objects):
        assert (context.tenant_id, context.workspace_id) == (
            self.context.tenant_id, self.context.workspace_id)
        assert destination == self.destination and objects == self.objects
        assert all(value.startswith('fixture-') for value in (
            destination.tenant_id, destination.workspace_id, destination.database_id, destination.bucket_id))
        self.restores.append((context, destination, objects))
        return True


class _NoDatabasePool:
    """생성자 flag 검사 중 연결 시작을 허용하지 않는다. DB 성공 응답 대역은 아니다."""

    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, **arguments):
        assert arguments['open'] is False
        self.closed = True

    def open(self, *args, **kwargs):
        pytest.fail('RECOVERY_FLAG_MUST_NOT_OPEN_DATABASE', pytrace=False)

    def connection(self, *args, **kwargs):
        pytest.fail('RECOVERY_FLAG_MUST_NOT_CONNECT_DATABASE', pytrace=False)

    def close(self):
        self.closed = True


@pytest.mark.parametrize('value', [None, 0, 1, 'true', 'false', [], {}])
def test_recovery_notification_flag_rejects_non_boolean_without_database(monkeypatch, value):
    monkeypatch.setattr(recovery_postgres, 'ConnectionPool', _NoDatabasePool)
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        PostgresRecoveryService('dbname=s42-no-database', object(), manifest_key=secrets.token_bytes(32),
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=value)


@pytest.mark.parametrize('arguments,expected', [({}, False), ({'notification_inbox_enabled': True}, True)])
def test_recovery_notification_flag_defaults_off_and_preserves_true_without_database(monkeypatch, arguments, expected):
    monkeypatch.setattr(recovery_postgres, 'ConnectionPool', _NoDatabasePool)
    service = PostgresRecoveryService('dbname=s42-no-database', object(), manifest_key=secrets.token_bytes(32),
        clock=lambda: datetime.now(timezone.utc), **arguments)
    try:
        assert service._notification_inbox_enabled is expected
        assert service._pool.closed is True
    finally:
        service.close()


def _assert_policy_manage(scope, context, expected_role):
    # 실제 현재 권한 표를 같은 소유 QA에서 읽는다. Principal/추가 인증/HTTP 대역 성공 주장이 아니다.
    with psycopg.connect(scope.app, connect_timeout=3) as connection:
        for name, value in (('tenant_id', context.tenant_id), ('workspace_id', context.workspace_id),
            ('actor_id', context.actor_id)):
            connection.execute('SELECT set_config(%s,%s,true)', ('app.' + name, value))
        grant = _current_auth_grant(context, _NotificationTupleReadConnection(connection), Action.POLICY_MANAGE)
        assert grant.allowed is True and grant.action is Action.POLICY_MANAGE and grant.role is expected_role


@pytest.fixture
def recovery_notification_case():
    scope = _existing_0069_studio_case()
    ctx = scope.context
    with psycopg.connect(scope.admin, connect_timeout=3) as connection:
        assert connection.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database "
            'WHERE datname=current_database()').fetchone() == (
                'S42 L2a disposable QA 2026-10-17 main owned; remove after tests; no production data',)
    context = RecoveryContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, ctx.trace_id,
        ctx.policy_version, organization_admin=True)
    _assert_policy_manage(scope, context, Role.PERSONAL_OWNER)
    destination = RestoreDestination('fixture-s42-recovery-tenant-' + scope.suffix,
        'fixture-s42-recovery-workspace-' + scope.suffix, 'fixture-s42-recovery-database-' + scope.suffix,
        'fixture-s42-recovery-bucket-' + scope.suffix)
    # 신규 합성 inventory의 checksum/size다. 실제 Object나 원문을 읽지 않는다.
    content = b'S42 synthetic recovery metadata only'
    objects = (BackupObjectInput(scope.suffix, hashlib.sha256(content).hexdigest(), len(content)),)
    storage = _MetadataOnlyStorage(context, destination, objects)
    arguments = {'notification_inbox_enabled': True} if (
        'notification_inbox_enabled' in inspect.signature(PostgresRecoveryService).parameters) else {}
    manifest_key = secrets.token_bytes(32)
    service = PostgresRecoveryService(scope.app, storage, manifest_key=manifest_key,
        clock=lambda: datetime.now(timezone.utc), **arguments)
    try:
        assert _sources(scope) == []
        # 기존 완료/실패 시험의 선행 백업은 OFF로 분리한다. 실제 저장·manifest 검사는 유지한다.
        preparation = PostgresRecoveryService(scope.app, storage, manifest_key=manifest_key,
            clock=lambda: datetime.now(timezone.utc), notification_inbox_enabled=False)
        try:
            backup = preparation.create_backup(context, trigger='manual', schema_revision='0069',
                retention_watermark='s42-owned-recovery-watermark', objects=objects,
                idempotency_key='s42-recovery-backup-0001')
        finally:
            preparation.close()
        assert backup.state == 'ready' and backup.version == 4 and backup.verified_at is not None
        preview = service.create_restore_preview(context, backup.backup_id, destination=destination,
            idempotency_key='s42-recovery-preview-0001', step_up_verified=True)
        assert (preview.state, preview.version, preview.preview.version) == ('preview_ready', 2, 1)
        assert preview.preview.included_object_ids == (objects[0].object_id,)
        assert preview.preview.excluded_object_ids == () and preview.preview.exclusion_reasons == ()
        assert storage.restores == [] and _sources(scope) == []
        yield _RecoveryCase(scope=scope, context=context, destination=destination, objects=objects,
            storage=storage, service=service, backup=backup, preview=preview,
            execute_key='s42-recovery-execute-0001')
    finally:
        service.close()


def _execute(case, *, context=None):
    return case.service.execute_restore(context or case.context, case.preview.request_id,
        expected_version=case.preview.version, preview_version=case.preview.preview.version,
        idempotency_key=case.execute_key, step_up_verified=True)


def _sources(scope):
    with psycopg.connect(scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        return connection.execute('SELECT tenant_id,workspace_id,source_event_id,recipient_id,source_store,'
            'origin_event_id,action,target_type,target_id,kind,severity,notebook_id,trace_id,policy_version,'
            'occurred_at,state,delivered_at,version FROM public.notification_source_outbox '
            "WHERE tenant_id=%s AND workspace_id=%s AND source_store='recovery' ORDER BY source_event_id,recipient_id",
            (scope.context.tenant_id, scope.context.workspace_id)).fetchall()


def _snapshot(case):
    # 암호화 inventory/조정 manifest·자격값은 읽지 않는다. 이번 합성 scope의 안전한 원장 필드만 비교한다.
    columns = {
        'backup_records': 'backup_id,actor_id,state,version,audit_event_id,verified_at,created_at',
        'backup_manifests': 'backup_id,manifest_version,manifest_digest,object_count,created_at',
        'restore_requests': 'request_id,backup_id,actor_id,state,version,policy_version,trace_id,audit_event_id,created_at,updated_at',
        'restore_previews': 'request_id,preview_version,destination_tenant_id,destination_workspace_id,'
            'destination_database_id,destination_bucket_id,created_at',
        'restore_verifications': 'verification_id,request_id,verification_digest,lineage_verified,rls_verified,'
            'audit_chain_verified,retention_rechecked,created_at',
    }
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        result = {table: connection.execute(f'SELECT {fields} FROM public.{table} '
            'WHERE tenant_id=%s AND workspace_id=%s ORDER BY 1,2',
            (case.context.tenant_id, case.context.workspace_id)).fetchall() for table, fields in columns.items()}
        result['operations'] = connection.execute('SELECT actor_id,operation,idempotency_key,request_fingerprint,'
            "result,status,expires_at FROM public.idempotency_records WHERE tenant_id=%s AND workspace_id=%s "
            "AND operation IN ('restore-execute','restore-cancel') ORDER BY actor_id,operation,idempotency_key",
            (case.context.tenant_id, case.context.workspace_id)).fetchall()
        result['audits'] = connection.execute('SELECT event_id,actor_id,action,target_type,target_id,outcome,'
            'trace_id,policy_version,metadata,occurred_at FROM public.audit_events '
            "WHERE tenant_id=%s AND workspace_id=%s AND target_type IN ('backup','restore_request') ORDER BY event_id",
            (case.context.tenant_id, case.context.workspace_id)).fetchall()
        result['notification_counts'] = tuple(connection.execute(f'SELECT count(*) FROM public.{table} '
            'WHERE tenant_id=%s', (case.context.tenant_id,)).fetchone()[0]
            for table in ('notifications', 'notification_details', 'notification_audit_outbox'))
    result['sources'] = _sources(case.scope)
    return result


def _assert_destination_metadata(case):
    target, item = case.destination, case.objects[0]
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT tenant_id FROM public.tenants WHERE tenant_id=%s',
            (target.tenant_id,)).fetchall() == [(target.tenant_id,)]
        assert connection.execute('SELECT tenant_id,workspace_id FROM public.workspaces WHERE tenant_id=%s '
            'AND workspace_id=%s', (target.tenant_id, target.workspace_id)).fetchall() == [
                (target.tenant_id, target.workspace_id)]
        rows = connection.execute('SELECT tenant_id,workspace_id,object_id,digest_sha256,byte_size,status,'
            'cleanup_pending,created_by,trace_id,version FROM public.object_records WHERE tenant_id=%s '
            'AND workspace_id=%s ORDER BY object_id', (target.tenant_id, target.workspace_id)).fetchall()
    assert rows == [(target.tenant_id, target.workspace_id, item.object_id, item.checksum_sha256,
        item.byte_size, 'completed', False, case.storage.restores[0][0].actor_id,
        case.storage.restores[0][0].trace_id, 1)]
    return rows


def _assert_completed_origin(case, result, executor):
    assert (result.request_id, result.backup_id, result.state, result.version, result.preview.version,
        result.preview.destination) == (case.preview.request_id, case.backup.backup_id, 'completed', 6, 2, case.destination)
    assert result.verification_digest is not None
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        origins = connection.execute('SELECT event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,'
            'outcome,trace_id,policy_version,metadata,occurred_at,before_value,after_value FROM public.audit_events '
            "WHERE tenant_id=%s AND workspace_id=%s AND action='restore.completed' AND target_id=%s",
            (case.context.tenant_id, case.context.workspace_id, result.request_id)).fetchall()
        assert len(origins) == 1
        root = origins[0]
        expected = dict(tenant_id=executor.tenant_id, workspace_id=executor.workspace_id, actor_id=executor.actor_id,
            action='restore.completed', target_type='restore_request', target_id=result.request_id,
            outcome='succeeded', trace_id=executor.trace_id, policy_version=executor.policy_version,
            before_value=None, after_value=None)
        assert {name: root[name] for name in expected} == expected
        assert root['occurred_at'].tzinfo is not None
        assert set(root['metadata']) == {'previous_hash', 'event_hash'}
        assert all(isinstance(value, str) and len(value) == 64 for value in root['metadata'].values())
        # 기존 요청 감사 포인터는 완료 감사로 덮어쓰지 않는다.
        request = connection.execute('SELECT r.actor_id,r.audit_event_id,a.action FROM public.restore_requests r '
            'JOIN public.audit_events a ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id '
            'AND a.event_id=r.audit_event_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.request_id=%s',
            (case.context.tenant_id, case.context.workspace_id, result.request_id)).fetchone()
        assert request['actor_id'] == case.context.actor_id and request['action'] == 'restore.previewed'
        assert request['audit_event_id'] != root['event_id']
        verifications = connection.execute('SELECT verification_digest,lineage_verified,rls_verified,'
            'audit_chain_verified,retention_rechecked FROM public.restore_verifications WHERE tenant_id=%s '
            'AND workspace_id=%s AND request_id=%s',
            (case.context.tenant_id, case.context.workspace_id, result.request_id)).fetchall()
        assert verifications == [dict(verification_digest=result.verification_digest, lineage_verified=True,
            rls_verified=True, audit_chain_verified=True, retention_rechecked=True)]
        assert connection.execute('SELECT actor_id,result,status FROM public.idempotency_records WHERE tenant_id=%s '
            "AND workspace_id=%s AND operation='restore-execute' AND idempotency_key=%s",
            (case.context.tenant_id, case.context.workspace_id, case.execute_key)).fetchall() == [
                dict(actor_id=executor.actor_id, result={'request_id': result.request_id}, status='completed')]
    return root


def _assert_completed_source(case, result, executor):
    # 미구현 flag 인자는 생략해도 실제 원업무/원감사/verification/receipt 뒤 source 누락까지 도달한다.
    root = _assert_completed_origin(case, result, executor)
    source_id = 'recovery-event-' + hashlib.sha256(
        f"{root['event_id']}|{case.context.workspace_id}|{case.context.actor_id}".encode()).hexdigest()[:32]
    assert _sources(case.scope) == [dict(tenant_id=case.context.tenant_id, workspace_id=case.context.workspace_id,
        source_event_id=source_id, recipient_id=case.context.actor_id, source_store='recovery',
        origin_event_id=root['event_id'], action='restore.completed', target_type='restore_request',
        target_id=result.request_id, kind='recovery', severity='info', notebook_id=None,
        trace_id=executor.trace_id, policy_version=executor.policy_version, occurred_at=root['occurred_at'],
        state='pending', delivered_at=None, version=1)], 'RESTORE_COMPLETED_SOURCE_INTENT_MISSING'
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute('SELECT r.xmin=v.xmin AND v.xmin=a.xmin AND a.xmin=i.xmin AND i.xmin=s.xmin '
            'FROM public.restore_requests r JOIN public.restore_verifications v ON v.tenant_id=r.tenant_id '
            'AND v.workspace_id=r.workspace_id AND v.request_id=r.request_id JOIN public.audit_events a '
            'ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id AND a.event_id=%s '
            'JOIN public.idempotency_records i ON i.tenant_id=r.tenant_id AND i.workspace_id=r.workspace_id '
            "AND i.operation='restore-execute' AND i.idempotency_key=%s AND i.actor_id=%s "
            'JOIN public.notification_source_outbox s ON s.tenant_id=r.tenant_id AND s.workspace_id=r.workspace_id '
            'AND s.origin_event_id=a.event_id AND s.recipient_id=r.actor_id AND s.source_event_id=%s '
            'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.request_id=%s',
            (root['event_id'], case.execute_key, executor.actor_id, source_id, case.context.tenant_id,
                case.context.workspace_id, result.request_id)).fetchall() == [(True,)]
    return source_id


def test_restore_completed_records_one_atomic_source_and_replay_adds_none(recovery_notification_case):
    case = recovery_notification_case
    before = _snapshot(case)
    result = _execute(case)
    _assert_completed_source(case, result, case.context)
    assert len(case.storage.restores) == 1
    _assert_destination_metadata(case)
    after = _snapshot(case)
    assert after['backup_records'] == before['backup_records']
    assert after['backup_manifests'] == before['backup_manifests']
    assert after['notification_counts'] == before['notification_counts'] == (0, 0, 0)
    assert len(after['audits']) == len(before['audits']) + 1
    assert _execute(case) == result
    assert _snapshot(case) == after and len(case.storage.restores) == 1


def _assert_terminal_recovery_root_and_source(case, *, action, target_type, target_id,
    outcome, before_value, after_value, kind, severity):
    """DB 원감사·원행 전이와 immutable source를 읽는다. 원사건/알림 직접 INSERT0."""
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        roots = connection.execute('SELECT event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,'
            'outcome,trace_id,policy_version,metadata,occurred_at,before_value,after_value '
            'FROM public.audit_events WHERE tenant_id=%s AND workspace_id=%s AND action=%s AND target_id=%s',
            (case.context.tenant_id, case.context.workspace_id, action, target_id)).fetchall()
        assert len(roots) == 1, action.upper().replace('.', '_') + '_ORIGIN_AUDIT_MISSING'
        root = roots[0]
        expected = dict(tenant_id=case.context.tenant_id, workspace_id=case.context.workspace_id,
            actor_id=case.context.actor_id, action=action, target_type=target_type, target_id=target_id,
            outcome=outcome, trace_id=case.context.trace_id, policy_version=case.context.policy_version,
            before_value=before_value, after_value=after_value)
        assert {name: root[name] for name in expected} == expected
        assert root['occurred_at'].tzinfo is not None
        metadata_keys = {'previous_hash', 'event_hash'}
        if action == 'restore.failed':
            metadata_keys.add('reason_code')
            assert root['metadata']['reason_code'] == 'RESTORE_PROCESSING_FAILED'
        assert set(root['metadata']) == metadata_keys
        for key in ('previous_hash', 'event_hash'):
            assert isinstance(root['metadata'][key], str) and len(root['metadata'][key]) == 64
            assert all(character in '0123456789abcdef' for character in root['metadata'][key])
    source_id = 'recovery-event-' + hashlib.sha256(
        f"{root['event_id']}|{case.context.workspace_id}|{case.context.actor_id}".encode()).hexdigest()[:32]
    assert _sources(case.scope) == [dict(tenant_id=case.context.tenant_id,
        workspace_id=case.context.workspace_id, source_event_id=source_id, recipient_id=case.context.actor_id,
        source_store='recovery', origin_event_id=root['event_id'], action=action, target_type=target_type,
        target_id=target_id, kind=kind, severity=severity, notebook_id=None, trace_id=case.context.trace_id,
        policy_version=case.context.policy_version, occurred_at=root['occurred_at'], state='pending',
        delivered_at=None, version=1)], action.upper().replace('.', '_') + '_SOURCE_INTENT_MISSING'
    table, primary = ('backup_records', 'backup_id') if target_type == 'backup' else ('restore_requests', 'request_id')
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        assert connection.execute(f'SELECT r.xmin=a.xmin AND a.xmin=s.xmin FROM public.{table} r '
            'JOIN public.audit_events a ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id '
            'AND a.event_id=%s JOIN public.notification_source_outbox s ON s.tenant_id=r.tenant_id '
            'AND s.workspace_id=r.workspace_id AND s.origin_event_id=a.event_id AND s.recipient_id=r.actor_id '
            f'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.{primary}=%s AND s.source_event_id=%s',
            (root['event_id'], case.context.tenant_id, case.context.workspace_id, target_id, source_id)
        ).fetchall() == [(True,)]
    return root


def test_backup_ready_records_final_audit_and_atomic_source_then_replays_without_writes(recovery_notification_case):
    case = recovery_notification_case
    before = _snapshot(case)
    arguments = dict(trigger='manual', schema_revision='0069',
        retention_watermark='s42-owned-ready-watermark', objects=case.objects,
        idempotency_key='s42-recovery-ready-notification-0001')
    backup = case.service.create_backup(case.context, **arguments)
    assert backup.backup_id != case.backup.backup_id
    assert (backup.state, backup.version) == ('ready', 4)
    assert backup.verified_at is not None and backup.verified_at.tzinfo is not None
    assert case.storage.restores == []
    with psycopg.connect(case.scope.admin, connect_timeout=3, row_factory=dict_row) as connection:
        row = connection.execute('SELECT b.actor_id,b.state,b.version,b.verified_at,b.audit_event_id,'
            'a.action,a.target_id,m.object_count FROM public.backup_records b '
            'JOIN public.audit_events a ON a.tenant_id=b.tenant_id AND a.workspace_id=b.workspace_id '
            'AND a.event_id=b.audit_event_id JOIN public.backup_manifests m '
            'ON m.tenant_id=b.tenant_id AND m.workspace_id=b.workspace_id AND m.backup_id=b.backup_id '
            'WHERE b.tenant_id=%s AND b.workspace_id=%s AND b.backup_id=%s',
            (case.context.tenant_id, case.context.workspace_id, backup.backup_id)).fetchone()
        assert row is not None
        assert (row['actor_id'], row['state'], row['version'], row['verified_at'], row['action'],
            row['target_id'], row['object_count']) == (case.context.actor_id, 'ready', 4, backup.verified_at,
                'backup.requested', backup.backup_id, len(case.objects))
    root = _assert_terminal_recovery_root_and_source(case, action='backup.ready', target_type='backup',
        target_id=backup.backup_id, outcome='succeeded', before_value={'state': 'verifying', 'version': 3},
        after_value={'state': 'ready', 'version': 4}, kind='recovery', severity='info')
    assert root['event_id'] != row['audit_event_id']
    after = _snapshot(case)
    assert after['restore_requests'] == before['restore_requests']
    assert after['restore_previews'] == before['restore_previews']
    assert after['restore_verifications'] == before['restore_verifications']
    assert after['operations'] == before['operations']
    assert after['notification_counts'] == before['notification_counts'] == (0, 0, 0)
    assert len(after['audits']) == len(before['audits']) + 2
    assert case.service.create_backup(case.context, **arguments) == backup
    assert _snapshot(case) == after and case.storage.restores == []


def test_restore_storage_failure_records_failed_transition_audit_and_atomic_source(
    recovery_notification_case, monkeypatch,
):
    case = recovery_notification_case
    before = _snapshot(case)
    storage_error = RecoveryError('RESOURCE_UNAVAILABLE', 503, retryable=True)
    real_restore, phases = case.storage.restore, []

    def fail_storage(context, destination, objects):
        # 외부 storage 포트의 실패만 주입한다. restoring/failed 저장 메서드는 실제 PG다.
        real_restore(context, destination, objects)
        with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
            row = connection.execute('SELECT state,version FROM public.restore_requests '
                'WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',
                (case.context.tenant_id, case.context.workspace_id, case.preview.request_id)).fetchone()
            assert row == ('restoring', 4)
        phases.append('committed_restoring_v4_before_storage_failure')
        raise storage_error

    with monkeypatch.context() as patch:
        patch.setattr(case.storage, 'restore', fail_storage)
        with pytest.raises(RecoveryError) as failed:
            _execute(case)
    assert failed.value is storage_error
    assert (failed.value.code, failed.value.status, failed.value.retryable) == ('RESOURCE_UNAVAILABLE', 503, True)
    assert phases == ['committed_restoring_v4_before_storage_failure']
    current = case.service.get_restore_request(case.context, case.preview.request_id)
    assert (current.state, current.version, current.preview.version, current.verification_digest) == ('failed', 5, 2, None)
    after = _snapshot(case)
    assert after['backup_records'] == before['backup_records']
    assert after['backup_manifests'] == before['backup_manifests']
    assert after['restore_verifications'] == before['restore_verifications'] == []
    assert after['operations'] == before['operations'] == []
    assert after['notification_counts'] == before['notification_counts'] == (0, 0, 0)
    assert len(after['restore_previews']) == len(before['restore_previews']) + 1
    assert len(case.storage.restores) == 1
    with psycopg.connect(case.scope.admin, connect_timeout=3) as connection:
        request = connection.execute('SELECT actor_id,audit_event_id FROM public.restore_requests '
            'WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s',
            (case.context.tenant_id, case.context.workspace_id, current.request_id)).fetchone()
        assert request == (case.context.actor_id, before['restore_requests'][0][7])
        assert connection.execute('SELECT count(*) FROM public.workspaces WHERE tenant_id=%s AND workspace_id=%s',
            (case.destination.tenant_id, case.destination.workspace_id)).fetchone() == (0,)
    root = _assert_terminal_recovery_root_and_source(case, action='restore.failed', target_type='restore_request',
        target_id=current.request_id, outcome='failed', before_value={'state': 'restoring', 'version': 4},
        after_value={'state': 'failed', 'version': 5}, kind='warning', severity='warning')
    assert root['event_id'] != request[1]
    final = _snapshot(case)
    assert len(final['audits']) == len(before['audits']) + 1
    with pytest.raises(RecoveryError) as retry:
        case.service.execute_restore(case.context, current.request_id, expected_version=current.version,
            preview_version=current.preview.version, idempotency_key=case.execute_key, step_up_verified=True)
    assert (retry.value.code, retry.value.status) == ('RESTORE_STATE_INVALID', 409)
    assert _snapshot(case) == final and len(case.storage.restores) == 1


def test_restore_completed_source_recipient_is_requester_not_other_authorized_executor(recovery_notification_case):
    case = recovery_notification_case
    # 기존 허용 QA 멤버의 workspace_admin 경로만 사용한다. 전역 관리자/tenant role 승격은 없다.
    executor_id = _studio_case_member(case.scope, role='workspace_admin')
    executor = replace(case.context, actor_id=executor_id, trace_id='s42-restore-executor-' + case.scope.suffix)
    _assert_policy_manage(case.scope, executor, Role.WORKSPACE_ADMIN)
    assert executor.actor_id != case.context.actor_id and executor.trace_id != case.context.trace_id
    result = _execute(case, context=executor)
    _assert_completed_source(case, result, executor)
    _assert_destination_metadata(case)
    after = _snapshot(case)
    assert _execute(case, context=executor) == result
    assert _snapshot(case) == after and len(case.storage.restores) == 1


def test_restore_completed_source_sql_failure_rolls_back_final_tx_not_prior_destination_commit(
    recovery_notification_case, monkeypatch,
):
    case = recovery_notification_case
    before = _snapshot(case)
    real_transaction = case.service._transaction
    phases = []

    class ObservedCursor:
        def __init__(self, cursor, raw):
            self.cursor, self.raw, self.source_rpc = cursor, raw, False

        def __getattr__(self, name):
            return getattr(self.cursor, name)

        def _observe(self, query, parameters):
            if not (isinstance(query, str) and query.lstrip().startswith('SELECT public.enqueue_')
                and isinstance(parameters, (tuple, list))):
                return
            roots = self.raw.execute('SELECT event_id FROM public.audit_events WHERE tenant_id=%s '
                "AND workspace_id=%s AND action='restore.completed' AND target_id=%s AND outcome='succeeded'",
                (case.context.tenant_id, case.context.workspace_id, case.preview.request_id)).fetchall()
            self.source_rpc = len(roots) == 1 and roots[0]['event_id'] in parameters

        def execute(self, query, parameters=None):
            self.cursor.execute(query, parameters)
            self._observe(query, parameters)
            return self

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.source_rpc:
                actual = tuple(row.values()) if isinstance(row, dict) else row
                assert actual is not None and len(actual) == 1 and type(actual[0]) is bool and actual[0] is True
                # True는 SQL ROW_COUNT=1 계약이다. 앱의 비공개 outbox SELECT 권한은 추가하지 않는다.
                phases.append('source_rpc_true')
                try:
                    self.raw.execute('SELECT 1/0')
                except psycopg.Error as error:
                    assert error.sqlstate == '22012'
                    phases.append('sql_22012')
                    raise
                pytest.fail('RESTORE_SOURCE_SQL_FAILURE_NOT_INJECTED', pytrace=False)
            return row

    class ObservedConnection:
        def __init__(self, raw):
            self.raw = raw

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def cursor(self, *args, **kwargs):
            return ObservedCursor(self.raw.cursor(*args, **kwargs), self.raw)

        def execute(self, query, parameters=None):
            cursor = ObservedCursor(self.raw.execute(query, parameters), self.raw)
            cursor._observe(query, parameters)
            return cursor

    @contextmanager
    def observed_transaction(*args, **kwargs):
        with real_transaction(*args, **kwargs) as connection:
            yield ObservedConnection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(case.service, '_transaction', observed_transaction)
        with pytest.raises(RecoveryError) as failed:
            _execute(case)
    assert (failed.value.code, failed.value.status) == ('RESOURCE_UNAVAILABLE', 503)
    assert phases == ['source_rpc_true', 'sql_22012']
    after = _snapshot(case)
    assert after['backup_records'] == before['backup_records']
    assert after['backup_manifests'] == before['backup_manifests']
    assert after['audits'] == before['audits']
    assert after['restore_verifications'] == before['restore_verifications'] == []
    assert after['operations'] == before['operations'] == []
    assert after['sources'] == before['sources'] == []
    assert after['notification_counts'] == before['notification_counts'] == (0, 0, 0)
    current = case.service.get_restore_request(case.context, case.preview.request_id)
    assert (current.state, current.version, current.preview.version, current.verification_digest) == ('restoring', 4, 2, None)
    assert len(after['restore_previews']) == len(before['restore_previews']) + 1
    assert len(case.storage.restores) == 1
    destination_rows = _assert_destination_metadata(case)
    # 이미 commit된 restoring과 목적지 metadata는 최종 source/감사 Tx rollback 대상이 아니다.
    with pytest.raises(RecoveryError) as retry:
        case.service.execute_restore(case.context, current.request_id, expected_version=current.version,
            preview_version=current.preview.version, idempotency_key=case.execute_key, step_up_verified=True)
    assert (retry.value.code, retry.value.status) == ('RESTORE_STATE_INVALID', 409)
    assert _snapshot(case) == after and _assert_destination_metadata(case) == destination_rows
    assert len(case.storage.restores) == 1
