"""로그인 source의 제한된 소비·현재 조회. producer 출생 증명의 I1은 별도다."""
from __future__ import annotations

import hashlib

from psycopg import Error, connect

from .audit import ActorType, AuditEventDraft, AuditOutcome, AuditValidationError
from .cloud_storage import CloudAccessContext
from .notification import NotificationError, _checked_id
from .notification_events import (
    ResolvedNotificationTarget,
    _NotificationTupleReadConnection,
    _account_notification_scope,
    _account_view_allowed,
)
from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit


_TITLE = '로그인 완료'
_SUMMARY = '본인 계정의 로그인이 완료되었습니다.'
_ACTION = 'identity.login.succeeded'
_PREFIX = 'identity-login-event-'
_TARGET_KEYS = frozenset({'accessible', 'title', 'summary', 'deep_link', 'source_event_id',
    'origin_event_id', 'target_type', 'target_id', 'kind', 'severity', 'trace_id',
    'policy_version', 'occurred_at', 'action'})


def _invalid():
    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)


def _current_target(reader, context, source_id):
    row = reader.execute('SELECT public.current_identity_login_notification_target(%s)', (source_id,)).fetchone()
    if row is None:
        _invalid()
    target = row[0]
    if target is None:
        return None
    # 로그인 원중앙감사 부재는 SQL 오류다. 조직의 awaiting_origin 형식은 허용하지 않는다.
    if type(target) is not dict or set(target) != _TARGET_KEYS or type(target['accessible']) is not bool:
        _invalid()
    if (target['source_event_id'], target['target_type'], target['kind'], target['severity'],
        target['title'], target['summary'], target['deep_link'], target['action']) != (
            source_id, 'session', 'authentication', 'info', _TITLE, _SUMMARY, '/settings/account', _ACTION):
        _invalid()
    try:
        for name in ('source_event_id', 'origin_event_id', 'target_id', 'trace_id', 'policy_version'):
            _checked_id(target[name])
    except NotificationError:
        _invalid()
    expected_source = _PREFIX + hashlib.sha256(
        f"{target['origin_event_id']}|{context.workspace_id}".encode()).hexdigest()[:32]
    if source_id != expected_source:
        _invalid()
    target = dict(target, occurred_at=_time(target['occurred_at']))
    if target['occurred_at'] is None:
        _invalid()
    return target


def _source_matches(source, context, target=None):
    if (source['tenant_id'], source['workspace_id'], source['recipient_id'], source['source_store'],
        source['action'], source['target_type'], source['kind'], source['severity'], source['notebook_id'],
        source['state'], source['version'], source['delivered_at']) != (
            context.tenant_id, context.workspace_id, context.actor_id, 'identity', _ACTION, 'session',
            'authentication', 'info', None, 'pending', 1, None):
        return False
    if target is None:
        return True
    return (source['source_event_id'], source['origin_event_id'], source['target_id'], source['trace_id'],
        source['policy_version'], _time(source['occurred_at'])) == (target['source_event_id'],
            target['origin_event_id'], target['target_id'], target['trace_id'], target['policy_version'], target['occurred_at'])


def _item_matches(item, context, target, provenance):
    return ((item.tenant_id, item.workspace_id, item.recipient_id, item.source_event_id,
        item.resource_type, item.resource_id, item.kind, item.severity, item.title, item.summary,
        item.deep_link, item.trace_id, item.delivery_state.value) == (
            context.tenant_id, context.workspace_id, context.actor_id, target['source_event_id'],
            'session', target['target_id'], 'authentication', 'info', _TITLE, _SUMMARY,
            '/settings/account', target['trace_id'], 'delivered')
        and (provenance.get('_source_store'), provenance.get('_origin_event_id'),
            provenance.get('_detail_source_event_id'), provenance.get('_notebook_id'),
            provenance.get('_policy_version')) == ('identity', target['origin_event_id'],
                target['source_event_id'], None, target['policy_version']))


def resolve_identity_login_notification(connection, context, item, *, provenance) -> ResolvedNotificationTarget | None:
    """원 session의 현재 활성 여부 대신 본인의 현재 Workspace 접근을 확인한다. 쓰기0."""
    if ((item.tenant_id, item.workspace_id, item.recipient_id) != (
            context.tenant_id, context.workspace_id, context.actor_id)
        or provenance.get('_source_store') != 'identity'
        or provenance.get('_detail_source_event_id') != item.source_event_id
        or provenance.get('_notebook_id') is not None or item.delivery_state.value != 'delivered'
        or not item.source_event_id.startswith(_PREFIX) or item.resource_type != 'session'
        or item.deep_link != '/settings/account'):
        return None
    try:
        reader = _NotificationTupleReadConnection(connection)
        target = _current_target(reader, context, item.source_event_id)
        if target is None or not target['accessible'] or not _account_view_allowed(reader, context):
            return None
        if not _item_matches(item, context, target, provenance):
            return None
        return ResolvedNotificationTarget(None, 'session', target['target_id'], '/settings/account')
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None


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
            before=None, after={'recipient_id': item.recipient_id, 'kind': 'authentication', 'delivery_state': 'delivered'},
            metadata={'source_event_id': item.source_event_id}))
    except (KeyError, TypeError, ValueError, AuditValidationError):
        _invalid()
    if actual != expected:
        _invalid()


def materialize_pending_identity_logins(dsn, *, context: CloudAccessContext, limit=100) -> int:
    """pending 잠금·현재 검사·알림/detail/created 의도/ack 검증을 한 Tx로 확정한다."""
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
                    cursor = reader.execute('SELECT * FROM public.pending_identity_login_notification_sources(%s)', (limit,))
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
                        python_view = _account_view_allowed(reader, context)
                        row = reader.execute('SELECT public.materialize_identity_login_notification(%s,%s)',
                            (source_id, target['accessible'] and python_view)).fetchone()
                        if row is None:
                            _invalid()
                        result = row[0]
                        if result is None:
                            continue  # Python 거부/SQL 허용이면 원 pending을 유지한다.
                        if type(result) is not dict:
                            _invalid()
                        if result == {'state': 'suppressed'}:
                            if target['accessible'] and not python_view:
                                _invalid()
                            continue
                        if (set(result) != {'state', 'notification_id', 'draft'} or result['state'] != 'delivered'
                            or not target['accessible'] or not python_view):
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
