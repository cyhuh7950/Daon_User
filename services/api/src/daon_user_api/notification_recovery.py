"""원요청자의 복구 완료·백업 준비·복구 실패 알림과 현재 접근을 검증한다."""
from __future__ import annotations

import hashlib
import re

from psycopg import Error, connect
from psycopg.pq import TransactionStatus

from .authorization import Action
from .audit import ActorType, AuditEventDraft, AuditOutcome, AuditValidationError
from .cloud_storage import CloudAccessContext
from .notification import NotificationError, _checked_id
from .notification_events import ResolvedNotificationTarget, _NotificationTupleReadConnection, _account_notification_scope
from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit
from .open_law_source_access import OpenLawAccessError, _current_auth_grant
from .recovery import RecoveryContext, RecoveryError


def _policy_manage_allowed(connection, context):
    """기존 현재 권한 읽기만 재사용한다. 실제 DB 오류는 접근 거부로 바꾸지 않는다."""
    try:
        _current_auth_grant(context, connection, Action.POLICY_MANAGE)
    except OpenLawAccessError as error:
        cause, seen = error, set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, Error):
                raise cause from None
            cause = cause.__cause__ or cause.__context__
        if connection.info.transaction_status == TransactionStatus.INERROR:
            raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
        return False
    return True


def enqueue_terminal_recovery_event(connection, *, context, origin_event_id, target_id) -> int:
    """새 terminal 원감사와 같은 업무 연결만 사용한다. payload/목적지/예외 본문은 읽지 않는다."""
    try:
        if not isinstance(context, RecoveryContext):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        for value in (origin_event_id, target_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version):
            _checked_id(value)
        reader = _NotificationTupleReadConnection(connection)
        original = reader.execute('SELECT action,target_type,target_id,actor_id,trace_id,policy_version '
            'FROM public.audit_events WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s',
            (context.tenant_id, context.workspace_id, origin_event_id)).fetchone()
        if original is None or original[0] not in {'backup.ready', 'restore.failed'}:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        resource = 'backup' if original[0] == 'backup.ready' else 'restore_request'
        if original[1:] != (resource, target_id, context.actor_id, context.trace_id, context.policy_version):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        table, key = ('backup_records', 'backup_id') if resource == 'backup' else ('restore_requests', 'request_id')
        owner = reader.execute(f'SELECT actor_id FROM public.{table} WHERE tenant_id=%s AND workspace_id=%s AND {key}=%s',
            (context.tenant_id, context.workspace_id, target_id)).fetchone()
        if owner is None:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        recipient = _checked_id(owner[0])
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=recipient, enabled=True) as scoped:
            member = scoped.execute('SELECT 1 FROM public.identity_memberships m '
                "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
                "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
                'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=%s '
                'JOIN public.workspaces f ON f.tenant_id=w.tenant_id AND f.workspace_id=w.workspace_id '
                'WHERE m.tenant_id=%s AND m.user_id=%s AND NOT EXISTS('
                'SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=m.tenant_id '
                "AND o.user_id=m.user_id AND w.workspace_kind='organization' AND o.state<>'active')",
                (context.workspace_id, context.tenant_id, recipient)).fetchone()
            current = CloudAccessContext(context.tenant_id, context.workspace_id, recipient, 'view')
            if member is None or not _policy_manage_allowed(scoped, current):
                return 0
            row = scoped.execute('SELECT public.enqueue_restore_completed_notification(%s,%s)',
                (origin_event_id, context.actor_id)).fetchone()
            if row is None or len(row) != 1 or type(row[0]) is not bool:
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            return int(row[0])
    except (Error, NotificationError):
        raise RecoveryError('RESOURCE_UNAVAILABLE', 503) from None


def enqueue_restore_completed_event(connection, *, context, origin_event_id, request_id) -> int:
    """원 연결을 commit/close하지 않는다. source 실패는 최종 DB Tx와 함께 취소한다."""
    try:
        if not isinstance(context, RecoveryContext):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        for value in (origin_event_id, request_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version):
            _checked_id(value)
        reader = _NotificationTupleReadConnection(connection)
        # 원요청자는 request 행에서만 얻는다. 목적지/manifest/원문은 조회하거나 복사하지 않는다.
        origins = reader.execute(
            'SELECT a.tenant_id,a.workspace_id,a.actor_id,a.action,a.target_type,a.target_id,a.outcome,'
            'a.trace_id,a.policy_version,a.occurred_at,a.metadata,r.actor_id,r.state,r.version,'
            'v.verification_digest,v.lineage_verified,v.rls_verified,v.audit_chain_verified,v.retention_rechecked,'
            'i.actor_id,i.result,i.status '
            'FROM public.audit_events a JOIN public.restore_requests r ON r.tenant_id=a.tenant_id '
            'AND r.workspace_id=a.workspace_id AND r.request_id=a.target_id '
            'JOIN public.restore_verifications v ON v.tenant_id=r.tenant_id AND v.workspace_id=r.workspace_id '
            'AND v.request_id=r.request_id JOIN public.idempotency_records i ON i.tenant_id=r.tenant_id '
            "AND i.workspace_id=r.workspace_id AND i.operation='restore-execute' "
            "AND i.result->>'request_id'=r.request_id AND i.actor_id=a.actor_id "
            'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.event_id=%s AND r.request_id=%s '
            'AND a.before_value IS NULL AND a.after_value IS NULL',
            (context.tenant_id, context.workspace_id, origin_event_id, request_id)).fetchall()
        if len(origins) != 1 or len(origins[0]) != 22:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        original = origins[0]
        if (original[:9] != (context.tenant_id, context.workspace_id, context.actor_id, 'restore.completed',
                'restore_request', request_id, 'succeeded', context.trace_id, context.policy_version)
            or _time(original[9]) is None or type(original[10]) is not dict
            or set(original[10]) != {'previous_hash', 'event_hash'}
            or any(not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None
                for value in original[10].values())
            or original[12] != 'completed' or type(original[13]) is not int or original[13] < 1
            or not isinstance(original[14], str) or re.fullmatch('[0-9a-f]{64}', original[14]) is None
            or any(type(value) is not bool or not value for value in original[15:19])
            or original[19] != context.actor_id or type(original[20]) is not dict
            or original[20] != {'request_id': request_id} or original[21] != 'completed'):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        recipient_id = _checked_id(original[11])
        # 실행자의 완료 감사 trace/policy는 그대로다. 원요청 행의 과거 trace/policy로 대체하지 않는다.
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=recipient_id, enabled=True) as scoped:
            member = scoped.execute('SELECT 1 FROM public.identity_memberships m '
                "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
                "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
                'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=%s '
                'JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id '
                'WHERE m.tenant_id=%s AND m.user_id=%s '
                'AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o '
                'WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id '
                "AND w.workspace_kind='organization' AND o.state<>'active')",
                (context.workspace_id, context.tenant_id, recipient_id)).fetchone()
            if member is None:
                return 0
            recipient_context = CloudAccessContext(context.tenant_id, context.workspace_id, recipient_id, 'view')
            if not _policy_manage_allowed(scoped, recipient_context):
                return 0
            result = scoped.execute('SELECT public.enqueue_restore_completed_notification(%s,%s)',
                (origin_event_id, context.actor_id)).fetchone()
            if result is None or len(result) != 1 or type(result[0]) is not bool:
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            return int(result[0])
    except (Error, NotificationError, TypeError, ValueError):
        raise RecoveryError('RESOURCE_UNAVAILABLE', 503, retryable=False) from None


_TITLE = '복구 완료'
_SUMMARY = '요청한 복구 작업이 완료되었습니다.'
_ACTION = 'restore.completed'
_PREFIX = 'recovery-event-'
_ACTION_TEXT = {
    'restore.completed': ('restore_request', 'recovery', 'info', _TITLE, _SUMMARY),
    'backup.ready': ('backup', 'recovery', 'info', '백업 준비 완료', '요청한 백업이 준비되었습니다.'),
    'restore.failed': ('restore_request', 'warning', 'warning', '복구 실패', '요청한 복구 작업을 완료하지 못했습니다.'),
}
_TARGET_KEYS = frozenset({'accessible', 'title', 'summary', 'deep_link', 'source_event_id',
    'origin_event_id', 'target_type', 'target_id', 'kind', 'severity', 'trace_id',
    'policy_version', 'occurred_at', 'action'})


def _invalid():
    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)


def _expected_source_id(context, origin_event_id):
    return _PREFIX + hashlib.sha256(
        f'{origin_event_id}|{context.workspace_id}|{context.actor_id}'.encode()).hexdigest()[:32]


def _restore_link(request_id):
    return _recovery_link('restore_request', request_id)


def _recovery_link(resource_type, identifier):
    return '/operations?' + ('backup_id=' if resource_type == 'backup' else 'restore_request_id=') + identifier.replace(':', '%3A')


def _current_target(reader, context, source_id):
    row = reader.execute('SELECT public.current_restore_completed_notification_target(%s)', (source_id,)).fetchone()
    if row is None or len(row) != 1:
        _invalid()
    target = row[0]
    if target is None:
        return None
    if type(target) is not dict or set(target) != _TARGET_KEYS or type(target['accessible']) is not bool:
        _invalid()
    try:
        for name in ('source_event_id', 'origin_event_id', 'target_id', 'trace_id', 'policy_version'):
            _checked_id(target[name])
    except NotificationError:
        _invalid()
    if not isinstance(target['action'], str) or target['action'] not in _ACTION_TEXT:
        _invalid()
    resource, kind, severity, title, summary = _ACTION_TEXT[target['action']]
    if (target['source_event_id'], target['target_type'], target['kind'], target['severity'],
        target['title'], target['summary'], target['deep_link'], target['action']) != (
            source_id, resource, kind, severity, title, summary, _recovery_link(resource, target['target_id']), target['action']):
        _invalid()
    if source_id != _expected_source_id(context, target['origin_event_id']):
        _invalid()
    target = dict(target, occurred_at=_time(target['occurred_at']))
    if target['occurred_at'] is None:
        _invalid()
    return target


def _current_restore_access_allowed(reader, context, target):
    """원 완료·감사·verification과 현재 수신자 권한을 읽는다. 만료 receipt/xmin은 요구하지 않는다."""
    if target['action'] in {'backup.ready', 'restore.failed'}:
        # 위 제한 SQL target이 원행/원감사/현재 identity·소속을 exact 검사했다.
        return target['accessible'] and _policy_manage_allowed(reader, context)
    originals = reader.execute(
        'SELECT a.actor_id,a.action,a.target_type,a.target_id,a.outcome,a.trace_id,a.policy_version,'
        'a.occurred_at,a.metadata,r.actor_id,r.state,r.version,v.verification_digest,'
        'v.lineage_verified,v.rls_verified,v.audit_chain_verified,v.retention_rechecked '
        'FROM public.audit_events a JOIN public.restore_requests r ON r.tenant_id=a.tenant_id '
        'AND r.workspace_id=a.workspace_id AND r.request_id=a.target_id '
        'JOIN public.restore_verifications v ON v.tenant_id=r.tenant_id AND v.workspace_id=r.workspace_id '
        'AND v.request_id=r.request_id WHERE a.tenant_id=%s AND a.workspace_id=%s '
        'AND a.event_id=%s AND r.request_id=%s AND a.before_value IS NULL AND a.after_value IS NULL',
        (context.tenant_id, context.workspace_id, target['origin_event_id'], target['target_id'])).fetchall()
    if len(originals) != 1 or len(originals[0]) != 17:
        _invalid()
    original = originals[0]
    try:
        _checked_id(original[0])
    except NotificationError:
        _invalid()
    # 원감사 actor는 executor다. 원요청자와 동일하다고 가정하거나 과거 trace/policy를 대입하지 않는다.
    if (original[1:7] != (_ACTION, 'restore_request', target['target_id'], 'succeeded',
            target['trace_id'], target['policy_version'])
        or _time(original[7]) != target['occurred_at'] or type(original[8]) is not dict
        or set(original[8]) != {'previous_hash', 'event_hash'}
        or any(not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None
            for value in original[8].values())
        or original[9] != context.actor_id or original[10] != 'completed'
        or type(original[11]) is not int or original[11] < 1
        or not isinstance(original[12], str) or re.fullmatch('[0-9a-f]{64}', original[12]) is None
        or any(type(value) is not bool or not value for value in original[13:17])):
        _invalid()
    member = reader.execute('SELECT 1 FROM public.identity_memberships m '
        "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
        "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
        'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=%s '
        'JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id '
        'WHERE m.tenant_id=%s AND m.user_id=%s '
        'AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o '
        'WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id '
        "AND w.workspace_kind='organization' AND o.state<>'active')",
        (context.workspace_id, context.tenant_id, context.actor_id)).fetchone()
    return member is not None and _policy_manage_allowed(reader, context)


def _source_matches(source, context, target=None):
    if not isinstance(source['action'], str) or source['action'] not in _ACTION_TEXT:
        return False
    resource, kind, severity, _title, _summary = _ACTION_TEXT[source['action']]
    if (source['tenant_id'], source['workspace_id'], source['recipient_id'], source['source_store'],
        source['action'], source['target_type'], source['kind'], source['severity'], source['notebook_id'],
        source['state'], source['version'], source['delivered_at']) != (
            context.tenant_id, context.workspace_id, context.actor_id, 'recovery', source['action'],
            resource, kind, severity, None, 'pending', 1, None):
        return False
    if source['source_event_id'] != _expected_source_id(context, source['origin_event_id']):
        return False
    if target is None:
        return True
    return (source['action'], source['source_event_id'], source['origin_event_id'], source['target_id'], source['trace_id'],
        source['policy_version'], _time(source['occurred_at'])) == (target['action'], target['source_event_id'],
            target['origin_event_id'], target['target_id'], target['trace_id'], target['policy_version'], target['occurred_at'])


def _item_matches(item, context, target, provenance):
    return ((item.tenant_id, item.workspace_id, item.recipient_id, item.source_event_id,
        item.resource_type, item.resource_id, item.kind, item.severity, item.title, item.summary,
        item.deep_link, item.trace_id, item.delivery_state.value) == (
            context.tenant_id, context.workspace_id, context.actor_id, target['source_event_id'],
            target['target_type'], target['target_id'], target['kind'], target['severity'], target['title'], target['summary'],
            target['deep_link'], target['trace_id'], 'delivered')
        and (provenance.get('_source_store'), provenance.get('_origin_event_id'),
            provenance.get('_detail_source_event_id'), provenance.get('_notebook_id'),
            provenance.get('_policy_version')) == ('recovery', target['origin_event_id'],
                target['source_event_id'], None, target['policy_version']))


def resolve_restore_completed_notification(connection, context, item, *, provenance) -> ResolvedNotificationTarget | None:
    """원 수신자·완료·현재 POLICY_MANAGE·불변 detail을 같은 연결에서 재검사한다. GET 쓰기0."""
    if not isinstance(context, CloudAccessContext) or context.capability != 'view':
        return None
    if ((item.tenant_id, item.workspace_id, item.recipient_id) != (
            context.tenant_id, context.workspace_id, context.actor_id)
        or provenance.get('_source_store') != 'recovery'
        or not item.source_event_id.startswith(_PREFIX)
        or provenance.get('_detail_source_event_id') != item.source_event_id
        or provenance.get('_notebook_id') is not None or item.delivery_state.value != 'delivered'
        or item.resource_type not in {'backup', 'restore_request'} or item.deep_link != _recovery_link(item.resource_type, item.resource_id)):
        return None
    try:
        reader = _NotificationTupleReadConnection(connection)
        target = _current_target(reader, context, item.source_event_id)
        if target is None or not target['accessible'] or not _current_restore_access_allowed(reader, context, target):
            return None
        if not _item_matches(item, context, target, provenance):
            return None
        return ResolvedNotificationTarget(None, target['target_type'], target['target_id'], target['deep_link'])
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
    except (KeyError, TypeError, ValueError):
        _invalid()


def _validate_created_audit(payload, item, target):
    if type(payload) is not dict:
        _invalid()
    try:
        values = dict(payload)
        values['occurred_at'] = _time(values['occurred_at'])
        values['actor_type'] = ActorType(values['actor_type'])
        values['outcome'] = AuditOutcome(values['outcome'])
        actual = serialize_notification_audit(AuditEventDraft(**values))
        expected = serialize_notification_audit(AuditEventDraft(
            event_id=item.audit_event_id, occurred_at=item.created_at, actor_id='notification-service',
            actor_type=ActorType.SERVICE, tenant_id=item.tenant_id, workspace_id=item.workspace_id,
            action='notification.created', target_type='notification', target_id=item.notification_id,
            outcome=AuditOutcome.SUCCEEDED, trace_id=target['trace_id'], policy_version=target['policy_version'],
            before=None, after={'recipient_id': item.recipient_id, 'kind': target['kind'], 'delivery_state': 'delivered'},
            metadata={'source_event_id': item.source_event_id}))
    except (KeyError, TypeError, ValueError, AuditValidationError):
        _invalid()
    if actual != expected:
        _invalid()


def materialize_pending_restore_completed(dsn, *, context: CloudAccessContext, limit=100) -> int:
    """pending 잠금·현재 검사·알림/detail/created 감사/ack·DTO 검증을 한 Tx로 확정한다."""
    if (not isinstance(dsn, str) or not dsn.strip()
        or not isinstance(context, CloudAccessContext) or context.capability != 'view'
        or type(limit) is not int or not 1 <= limit <= 100):
        raise NotificationError('INVALID_INPUT', 400)
    for value in (context.tenant_id, context.workspace_id, context.actor_id):
        _checked_id(value)
    delivered = 0
    try:
        with connect(dsn, connect_timeout=3) as connection:
            with connection.transaction():
                with _account_notification_scope(connection, tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id, actor_id=context.actor_id) as reader:
                    reader.execute("SET LOCAL statement_timeout='5s'")
                    reader.execute("SET LOCAL lock_timeout='2s'")
                    cursor = reader.execute('SELECT * FROM public.pending_restore_completed_notification_sources(%s)', (limit,))
                    columns = tuple(column.name for column in cursor.description)
                    sources = [dict(zip(columns, row)) for row in cursor.fetchall()]
                    for source in sources:
                        if not _source_matches(source, context):
                            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
                        try:
                            source_id = _checked_id(source['source_event_id'])
                        except NotificationError:
                            _invalid()
                        target = _current_target(reader, context, source_id)
                        if target is None or not _source_matches(source, context, target):
                            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
                        python_allowed = _current_restore_access_allowed(reader, context, target)
                        if target['accessible'] != python_allowed:
                            continue  # 현재 접근 판정이 다르면 임의 전달/억제하지 않고 pending을 유지한다.
                        row = reader.execute('SELECT public.materialize_restore_completed_notification(%s,%s)',
                            (source_id, target['accessible'] and python_allowed)).fetchone()
                        if row is None or len(row) != 1:
                            _invalid()
                        result = row[0]
                        if result is None:
                            continue  # SQL 허용/Python 거부이면 임의 억제하지 않고 pending을 유지한다.
                        if type(result) is not dict:
                            _invalid()
                        if result == {'state': 'suppressed'}:
                            if target['accessible'] and not python_allowed:
                                _invalid()
                            continue
                        if (set(result) != {'state', 'notification_id', 'draft'} or result['state'] != 'delivered'
                            or not target['accessible'] or not python_allowed):
                            _invalid()
                        expected_id = 'notification-' + hashlib.sha256(
                            f'{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{source_id}'.encode()).hexdigest()[:32]
                        if result['notification_id'] != expected_id:
                            _invalid()
                        cursor = reader.execute(_SELECT + ' AND n.notification_id=%s',
                            (context.tenant_id, context.workspace_id, context.actor_id, expected_id))
                        values = cursor.fetchone()
                        if values is None:
                            _invalid()
                        detail = dict(zip((column.name for column in cursor.description), values))
                        try:
                            item = notification_from_row(detail)
                        except NotificationError:
                            _invalid()
                        if (not _item_matches(item, context, target, detail) or item.notification_id != expected_id
                            or item.version != 1 or item.read_at is not None
                            or item.delivered_at is None or item.created_at != item.delivered_at):
                            _invalid()
                        _validate_created_audit(result['draft'], item, target)
                        delivered += 1
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
    except (KeyError, TypeError, ValueError):
        _invalid()
    return delivered
