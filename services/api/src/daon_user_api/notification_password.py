"""P1 인증된 현재 비밀번호 변경의 사건/source·소비·현재 조회. reset 경로는 포함하지 않는다."""
from __future__ import annotations

import hashlib
import sqlite3

from psycopg import Error, connect

from .audit import ActorType, AuditEventDraft, AuditOutcome, AuditValidationError
from .cloud_storage import CloudAccessContext
from .notification import NotificationError, _checked_id
from .notification_events import (
    ResolvedNotificationTarget,
    _account_notification_scope, _account_view_allowed,
)
from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit


_ACTION = 'identity.password.changed'
_PREFIX = 'identity-password-event-'
_TITLE = '비밀번호 변경'
_SUMMARY = '본인 계정의 비밀번호가 변경되었습니다.'
_TARGET_KEYS = frozenset({'accessible', 'title', 'summary', 'deep_link', 'source_event_id',
    'origin_event_id', 'target_type', 'target_id', 'kind', 'severity', 'trace_id',
    'policy_version', 'occurred_at', 'action'})


def _invalid():
    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)


def record_password_change_event(identityconn, identity, principal, *, event):
    """검증한 원 principal과 기존 성공 draft만 같은 Tx에 기록한다. commit/close·새 인증0."""
    from .identity import IdentityError
    from .organization_membership import OrganizationWorkflowError

    if identity._notification_inbox_enabled is not True or not hasattr(identityconn, '_connection'):
        return 0
    try:
        tenant, actor, session = (principal['tenant_id'], principal['user_id'], principal['session_id'])
        for value in (tenant, actor, session):
            _checked_id(value)
        if (not isinstance(event, AuditEventDraft) or event.actor_type is not ActorType.USER
            or event.outcome is not AuditOutcome.SUCCEEDED
            or (event.tenant_id, event.actor_id, event.workspace_id, event.action, event.target_type,
                event.target_id, event.before, event.after) != (tenant, actor, None, _ACTION, 'user', actor, None, None)
            or dict(event.metadata) != {'reason_code': 'CURRENT_PASSWORD_CHANGED'}):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        # 기존 중앙 감사 검증/발생 시각을 그대로 재사용한다. 비밀번호 관련 입력은 받지 않는다.
        serialize_notification_audit(event)
        for value in (event.event_id, event.trace_id, event.policy_version):
            _checked_id(value)
        occurred_at = _time(event.occurred_at)
        if occurred_at is None:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        inserted = 0
        with _account_notification_scope(identityconn, tenant_id=tenant, actor_id=actor, enabled=True) as reader:
            source_session = reader.execute('SELECT session_id,tenant_id,user_id,state FROM public.identity_sessions '
                'WHERE session_id=%s AND tenant_id=%s AND user_id=%s', (session, tenant, actor)).fetchone()
            if source_session != (session, tenant, actor, 'revoked'):
                raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
            cursor = reader.execute('INSERT INTO public.identity_password_change_events '
                '(event_id,tenant_id,user_id,source_session_id,occurred_at,trace_id,policy_version) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (event.event_id, tenant, actor, session, occurred_at, event.trace_id, event.policy_version))
            if cursor.rowcount != 1:
                raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
            # 같은 실제 identity 연결의 현재 교집합만 사용한다. 가상 소속/Workspace 생성0.
            workspaces = identity._notification_workspace_ids(identityconn, user_id=actor, tenant_id=tenant)
            for workspace in workspaces:
                _checked_id(workspace)
                with _account_notification_scope(identityconn, tenant_id=tenant, actor_id=actor,
                    workspace_id=workspace) as scoped:
                    foundation = scoped.execute('SELECT 1 FROM public.workspaces w JOIN public.user_accounts a '
                        "ON a.tenant_id=w.tenant_id AND a.user_id=%s AND a.status='active' "
                        'WHERE w.tenant_id=%s AND w.workspace_id=%s', (actor, tenant, workspace)).fetchone()
                    if foundation is None or not _account_view_allowed(scoped,
                        CloudAccessContext(tenant, workspace, actor, 'view')):
                        continue
                    row = scoped.execute('SELECT public.enqueue_password_change_notification(%s)',
                        (event.event_id,)).fetchone()
                    if row is None or len(row) != 1 or type(row[0]) is not bool:
                        _invalid()
                    inserted += int(row[0])
        return inserted
    except (Error, sqlite3.Error, NotificationError, OrganizationWorkflowError,
        AuditValidationError, KeyError, TypeError, ValueError):
        # 원 비밀번호/세션 철회·새 사건/source는 바깥 원업무 Tx와 함께 rollback한다.
        # 이미 별도 commit된 중앙 감사 ghost를 삭제/보상하지 않는다.
        raise IdentityError('PERSISTENCE_UNAVAILABLE', 503) from None


def _current_target(reader, context, source_id):
    row = reader.execute('SELECT public.current_password_change_notification_target(%s)', (source_id,)).fetchone()
    if row is None or len(row) != 1:
        _invalid()
    target = row[0]
    if target is None:
        return None
    if type(target) is not dict or set(target) != _TARGET_KEYS or type(target['accessible']) is not bool:
        _invalid()
    if (target['source_event_id'], target['target_type'], target['target_id'], target['kind'], target['severity'],
        target['title'], target['summary'], target['deep_link'], target['action']) != (
            source_id, 'user', context.actor_id, 'authentication', 'info',
            _TITLE, _SUMMARY, '/settings/account', _ACTION):
        _invalid()
    try:
        for name in ('source_event_id', 'origin_event_id', 'target_id', 'trace_id', 'policy_version'):
            _checked_id(target[name])
    except NotificationError:
        _invalid()
    expected = _PREFIX + hashlib.sha256(
        f"{target['origin_event_id']}|{context.workspace_id}".encode()).hexdigest()[:32]
    if source_id != expected:
        _invalid()
    target = dict(target, occurred_at=_time(target['occurred_at']))
    if target['occurred_at'] is None:
        _invalid()
    return target


def _source_matches(source, context, target=None):
    if (source['tenant_id'], source['workspace_id'], source['recipient_id'], source['source_store'],
        source['action'], source['target_type'], source['target_id'], source['kind'], source['severity'],
        source['notebook_id'], source['state'], source['version'], source['delivered_at']) != (
            context.tenant_id, context.workspace_id, context.actor_id, 'identity', _ACTION, 'user', context.actor_id,
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
            'user', context.actor_id, 'authentication', 'info', _TITLE, _SUMMARY,
            '/settings/account', target['trace_id'], 'delivered')
        and (provenance.get('_source_store'), provenance.get('_origin_event_id'),
            provenance.get('_detail_source_event_id'), provenance.get('_notebook_id'),
            provenance.get('_policy_version')) == ('identity', target['origin_event_id'],
                target['source_event_id'], None, target['policy_version']))


def resolve_password_change_notification(connection, context, item, *, provenance) -> ResolvedNotificationTarget | None:
    """원 session은 이미 철회됐다. 현재 조회자의 본인 Workspace/VIEW만 재검증한다. 쓰기0."""
    if ((item.tenant_id, item.workspace_id, item.recipient_id) != (
            context.tenant_id, context.workspace_id, context.actor_id)
        or provenance.get('_source_store') != 'identity'
        or provenance.get('_detail_source_event_id') != item.source_event_id
        or provenance.get('_notebook_id') is not None or item.delivery_state.value != 'delivered'
        or not item.source_event_id.startswith(_PREFIX) or item.resource_type != 'user'
        or item.resource_id != context.actor_id or item.deep_link != '/settings/account'):
        return None
    try:
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=context.actor_id) as reader:
            target = _current_target(reader, context, item.source_event_id)
            if target is None or not target['accessible'] or not _account_view_allowed(reader, context):
                return None
            if not _item_matches(item, context, target, provenance):
                return None
            return ResolvedNotificationTarget(None, 'user', context.actor_id, '/settings/account')
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
            before=None, after={'recipient_id': item.recipient_id, 'kind': 'authentication', 'delivery_state': 'delivered'},
            metadata={'source_event_id': item.source_event_id}))
    except (KeyError, TypeError, ValueError, AuditValidationError):
        _invalid()
    if actual != expected:
        _invalid()


def materialize_pending_password_changes(dsn, *, context: CloudAccessContext, limit=100) -> int:
    """현재 검사·알림/detail/created 의도/source ack를 동일 Tx에서 검증한다."""
    if (not isinstance(dsn, str) or not dsn.strip() or not isinstance(context, CloudAccessContext)
        or context.capability != 'view' or type(limit) is not int or not 1 <= limit <= 100):
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
                    cursor = reader.execute('SELECT * FROM public.pending_password_change_notification_sources(%s)', (limit,))
                    sources = [dict(zip((column.name for column in cursor.description), row)) for row in cursor.fetchall()]
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
                        row = reader.execute('SELECT public.materialize_password_change_notification(%s,%s)',
                            (source_id, target['accessible'] and python_view)).fetchone()
                        if row is None or len(row) != 1:
                            _invalid()
                        result = row[0]
                        if result is None:
                            continue  # 현재 Python 거부/SQL 허용이면 원 pending을 보존한다.
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
                            or item.version != 1 or item.read_at is not None or item.delivered_at is None
                            or item.created_at != item.delivered_at):
                            _invalid()
                        _validate_created_audit(result['draft'], item, target)
                        delivered += 1
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
    except (KeyError, TypeError, ValueError):
        _invalid()
    return delivered
