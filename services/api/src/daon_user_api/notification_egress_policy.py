"""실제 Workspace 정책 저장과 같은 연결에서 현재 VIEW 수신자의 알림 의도만 기록한다."""
from __future__ import annotations

import hashlib

from psycopg import Error, connect

from .audit import ActorType, AuditEventDraft, AuditOutcome, AuditValidationError
from .cloud_storage import CloudAccessContext
from .egress_policy import EgressPolicyContext
from .notification import NotificationError, _checked_id
from .notification_events import (
    ResolvedNotificationTarget,
    _NotificationTupleReadConnection,
    _account_notification_scope,
    _account_view_allowed,
)
from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit


def enqueue_workspace_policy_event(connection, *, context, origin_event_id, binding_id, policy_version_id) -> int:
    """원 연결을 commit/close하지 않는다. SQL 장애는 바깥 CloudStore transaction으로 전파한다."""
    if not isinstance(context, EgressPolicyContext):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    try:
        for value in (origin_event_id, binding_id, policy_version_id, context.tenant_id,
            context.workspace_id, context.actor_id, context.trace_id, context.authorization_policy_version):
            _checked_id(value)
    except NotificationError:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503) from None
    reader = _NotificationTupleReadConnection(connection)
    # 원감사의 실제 식별자/작성자/범위와 새 binding을 결속한다. 정책 본문은 읽거나 복사하지 않는다.
    original = reader.execute(
        'SELECT a.tenant_id,a.workspace_id,a.actor_id,a.action,a.target_type,a.target_id,a.outcome,'
        'a.trace_id,a.policy_version,a.metadata,b.policy_version_id,b.binding_version '
        'FROM public.audit_events a JOIN public.egress_policy_bindings b '
        'ON b.tenant_id=a.tenant_id AND b.workspace_id=a.workspace_id AND b.binding_id=a.target_id '
        "AND b.scope_type='workspace' AND b.active=true AND b.current=true "
        'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.event_id=%s '
        'AND a.before_value IS NULL AND a.after_value IS NULL',
        (context.tenant_id, context.workspace_id, origin_event_id),
    ).fetchone()
    if original is None or len(original) != 12 or type(original[11]) is not int or original[11] < 1:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    if original != (context.tenant_id, context.workspace_id, context.actor_id, 'egress_policy.activate',
        'EgressPolicyBinding', binding_id, 'succeeded', context.trace_id, context.authorization_policy_version,
        {'scope_type': 'workspace', 'policy_version_id': policy_version_id, 'binding_version': original[11]},
        policy_version_id, original[11]):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    candidates = reader.execute(
        'SELECT DISTINCT m.user_id FROM public.identity_memberships m '
        "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
        "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
        'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=%s '
        'JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id '
        'WHERE m.tenant_id=%s AND (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles r '
        "WHERE r.tenant_id=w.tenant_id AND r.user_id=m.user_id AND r.state='active' "
        "AND ((w.workspace_kind='personal' AND r.role='personal_owner') "
        "OR (w.workspace_kind='organization' AND r.role='organization_admin'))) "
        'OR EXISTS(SELECT 1 FROM public.identity_auth_memberships am WHERE am.tenant_id=w.tenant_id '
        "AND am.workspace_id=w.workspace_id AND am.user_id=m.user_id AND am.state='active')) "
        'AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o '
        'WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id '
        "AND w.workspace_kind='organization' AND o.state<>'active') ORDER BY m.user_id",
        (context.workspace_id, context.tenant_id),
    ).fetchall()
    inserted = 0
    for row in candidates:
        if len(row) != 1:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        try:
            recipient_id = _checked_id(row[0])
        except NotificationError:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503) from None
        with _account_notification_scope(connection, tenant_id=context.tenant_id,
            workspace_id=context.workspace_id, actor_id=recipient_id) as scoped:
            recipient_context = CloudAccessContext(context.tenant_id, context.workspace_id, recipient_id, 'view')
            if not _account_view_allowed(scoped, recipient_context):
                continue
            result = scoped.execute('SELECT public.enqueue_egress_policy_notification(%s,%s)',
                (origin_event_id, context.actor_id)).fetchone()
            if result is None or len(result) != 1 or type(result[0]) is not bool:
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            inserted += int(result[0])
    return inserted


def enqueue_organization_policy_event(connection, *, context, origin_event_id, binding_id, policy_version_id) -> int:
    """조직 원감사 범위는 보존하고 실제 조직 Workspace의 현재 수신자만 같은 Tx에 결속한다."""
    from .authorization import Action
    from .open_law_source_access import OpenLawAccessError, _current_auth_grant
    from psycopg.pq import TransactionStatus

    if not isinstance(context, EgressPolicyContext):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    try:
        for value in (origin_event_id, binding_id, policy_version_id, context.tenant_id,
            context.organization_id, context.workspace_id, context.actor_id, context.trace_id,
            context.authorization_policy_version):
            _checked_id(value)
    except NotificationError:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503) from None
    reader = _NotificationTupleReadConnection(connection)
    # 원관리자의 동적 권한은 원 context에서 검사한다. 수신자 GUC를 인증 증거로 취급하지 않는다.
    try:
        _current_auth_grant(context, reader, Action.POLICY_MANAGE)
    except OpenLawAccessError as error:
        cause, seen = error, set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause, Error):
                raise cause from None
            cause = cause.__cause__ or cause.__context__
        if reader.info.transaction_status == TransactionStatus.INERROR:
            raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
        raise NotificationError('CURRENT_ACCESS_DENIED', 403) from None
    original = reader.execute(
        'SELECT a.tenant_id,a.workspace_id,a.actor_id,a.action,a.target_type,a.target_id,a.outcome,'
        'a.trace_id,a.policy_version,a.metadata,b.policy_version_id,b.binding_version '
        'FROM public.audit_events a JOIN public.egress_policy_bindings b '
        'ON b.tenant_id=a.tenant_id AND b.binding_id=a.target_id '
        'JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id '
        'AND p.organization_id=b.organization_id AND p.policy_version_id=b.policy_version_id '
        'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.event_id=%s '
        "AND b.organization_id=%s AND b.workspace_id IS NULL AND b.scope_type='organization' "
        "AND b.active=true AND b.current=true AND p.workspace_id IS NULL AND p.scope_type='organization' "
        "AND p.state='active' AND p.policy_version>0 AND b.created_by=a.actor_id AND p.created_by=a.actor_id "
        'AND b.trace_id=a.trace_id AND p.trace_id=a.trace_id '
        'AND a.before_value IS NULL AND a.after_value IS NULL',
        (context.tenant_id, context.workspace_id, origin_event_id, context.organization_id),
    ).fetchone()
    if original is None or len(original) != 12 or type(original[11]) is not int or original[11] < 1:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    if original != (context.tenant_id, context.workspace_id, context.actor_id, 'egress_policy.activate',
        'EgressPolicyBinding', binding_id, 'succeeded', context.trace_id, context.authorization_policy_version,
        {'scope_type': 'organization', 'policy_version_id': policy_version_id, 'binding_version': original[11]},
        policy_version_id, original[11]):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)

    inserted, after = 0, ''
    while True:
        # tenant의 auth 디렉터리만 순회한다. Foundation은 각 Workspace RLS 문맥에서 대조한다.
        workspaces = reader.execute(
            'SELECT workspace_id FROM public.identity_auth_workspaces WHERE tenant_id=%s '
            "AND workspace_kind='organization' AND workspace_id>%s ORDER BY workspace_id LIMIT 100",
            (context.tenant_id, after)).fetchall()
        if not workspaces:
            break
        for row in workspaces:
            try:
                if len(row) != 1:
                    raise ValueError
                workspace = _checked_id(row[0])
                if workspace == after:
                    raise ValueError
            except (ValueError, TypeError, NotificationError):
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503) from None
            after = workspace
            with _account_notification_scope(connection, tenant_id=context.tenant_id,
                workspace_id=workspace, actor_id=context.actor_id) as scoped:
                bound = scoped.execute(
                    'SELECT 1 FROM public.workspaces w JOIN public.identity_auth_workspaces aw '
                    'ON aw.tenant_id=w.tenant_id AND aw.workspace_id=w.workspace_id '
                    "AND aw.workspace_kind='organization' "
                    'JOIN public.egress_policy_bindings b ON b.tenant_id=w.tenant_id AND b.workspace_id=w.workspace_id '
                    "AND b.scope_type='workspace' AND b.organization_id=%s AND b.active=true AND b.current=true "
                    'JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id '
                    'AND p.organization_id=b.organization_id AND p.policy_version_id=b.policy_version_id '
                    "AND p.scope_type='workspace' AND p.workspace_id=w.workspace_id AND p.state='active' "
                    'WHERE w.tenant_id=%s AND w.workspace_id=%s',
                    (context.organization_id, context.tenant_id, workspace)).fetchone()
                if bound is None:
                    continue
                candidates = scoped.execute(
                    'SELECT DISTINCT m.user_id FROM public.identity_memberships m '
                    "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
                    "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
                    'WHERE m.tenant_id=%s AND (EXISTS(SELECT 1 FROM public.identity_auth_tenant_roles r '
                    "WHERE r.tenant_id=m.tenant_id AND r.user_id=m.user_id AND r.role='organization_admin' AND r.state='active') "
                    'OR EXISTS(SELECT 1 FROM public.identity_auth_memberships am WHERE am.tenant_id=m.tenant_id '
                    "AND am.workspace_id=%s AND am.user_id=m.user_id AND am.state='active')) "
                    'AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o '
                    "WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id AND o.state<>'active') ORDER BY m.user_id",
                    (context.tenant_id, workspace)).fetchall()
                for candidate in candidates:
                    try:
                        if len(candidate) != 1:
                            raise ValueError
                        recipient = _checked_id(candidate[0])
                    except (ValueError, TypeError, NotificationError):
                        raise NotificationError('DATABASE_RESPONSE_INVALID', 503) from None
                    with _account_notification_scope(connection, tenant_id=context.tenant_id,
                        workspace_id=workspace, actor_id=recipient) as recipient_reader:
                        current = CloudAccessContext(context.tenant_id, workspace, recipient, 'view')
                        if not _account_view_allowed(recipient_reader, current):
                            continue
                        result = recipient_reader.execute('SELECT public.enqueue_egress_policy_notification(%s,%s)',
                            (origin_event_id, context.actor_id)).fetchone()
                        if result is None or len(result) != 1 or type(result[0]) is not bool:
                            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                        inserted += int(result[0])
    return inserted


_TITLE = '외부 전송 정책 변경'
_SUMMARY = '작업공간의 외부 전송 정책이 변경되었습니다.'
_ACTION = 'egress_policy.activate'
_PREFIX = 'egress-policy-event-'
_TARGET_KEYS = frozenset({'accessible', 'title', 'summary', 'deep_link', 'source_event_id',
    'origin_event_id', 'target_type', 'target_id', 'kind', 'severity', 'trace_id',
    'policy_version', 'occurred_at', 'action'})


def _invalid():
    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)


def _expected_source_id(context, origin_event_id):
    return _PREFIX + hashlib.sha256(
        f'{origin_event_id}|{context.workspace_id}|{context.actor_id}'.encode()).hexdigest()[:32]


def _current_target(reader, context, source_id):
    row = reader.execute('SELECT public.current_egress_policy_notification_target(%s)', (source_id,)).fetchone()
    if row is None or len(row) != 1:
        _invalid()
    target = row[0]
    if target is None:
        return None
    # 원감사는 source와 같은 업무 Tx에서 저장된다. 별도 전달 대기 형식은 허용하지 않는다.
    if type(target) is not dict or set(target) != _TARGET_KEYS or type(target['accessible']) is not bool:
        _invalid()
    if (target['source_event_id'], target['target_type'], target['kind'], target['severity'],
        target['title'], target['summary'], target['deep_link'], target['action']) != (
            source_id, 'EgressPolicyBinding', 'policy', 'info', _TITLE, _SUMMARY, '/notifications', _ACTION):
        _invalid()
    try:
        for name in ('source_event_id', 'origin_event_id', 'target_id', 'trace_id', 'policy_version'):
            _checked_id(target[name])
    except NotificationError:
        _invalid()
    if source_id != _expected_source_id(context, target['origin_event_id']):
        _invalid()
    target = dict(target, occurred_at=_time(target['occurred_at']))
    if target['occurred_at'] is None:
        _invalid()
    return target


def _source_matches(source, context, target=None):
    if (source['tenant_id'], source['workspace_id'], source['recipient_id'], source['source_store'],
        source['action'], source['target_type'], source['kind'], source['severity'], source['notebook_id'],
        source['state'], source['version'], source['delivered_at']) != (
            context.tenant_id, context.workspace_id, context.actor_id, 'authorization', _ACTION,
            'EgressPolicyBinding', 'policy', 'info', None, 'pending', 1, None):
        return False
    if source['source_event_id'] != _expected_source_id(context, source['origin_event_id']):
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
            'EgressPolicyBinding', target['target_id'], 'policy', 'info', _TITLE, _SUMMARY,
            '/notifications', target['trace_id'], 'delivered')
        and (provenance.get('_source_store'), provenance.get('_origin_event_id'),
            provenance.get('_detail_source_event_id'), provenance.get('_notebook_id'),
            provenance.get('_policy_version')) == ('authorization', target['origin_event_id'],
                target['source_event_id'], None, target['policy_version']))


def resolve_workspace_policy_notification(connection, context, item, *, provenance) -> ResolvedNotificationTarget | None:
    """같은 연결의 원 binding·원감사·현재 VIEW·불변 detail을 검사한다. GET/읽음 replay 쓰기0."""
    if not isinstance(context, CloudAccessContext) or context.capability != 'view':
        return None
    if ((item.tenant_id, item.workspace_id, item.recipient_id) != (
            context.tenant_id, context.workspace_id, context.actor_id)
        or provenance.get('_source_store') != 'authorization'
        or not item.source_event_id.startswith(_PREFIX)
        or provenance.get('_detail_source_event_id') != item.source_event_id
        or provenance.get('_notebook_id') is not None or item.delivery_state.value != 'delivered'
        or item.resource_type != 'EgressPolicyBinding' or item.deep_link != '/notifications'):
        return None
    try:
        reader = _NotificationTupleReadConnection(connection)
        target = _current_target(reader, context, item.source_event_id)
        if target is None or not target['accessible'] or not _account_view_allowed(reader, context):
            return None
        if not _item_matches(item, context, target, provenance):
            return None
        return ResolvedNotificationTarget(None, 'EgressPolicyBinding', target['target_id'], '/notifications')
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
            before=None, after={'recipient_id': item.recipient_id, 'kind': 'policy', 'delivery_state': 'delivered'},
            metadata={'source_event_id': item.source_event_id}))
    except (KeyError, TypeError, ValueError, AuditValidationError):
        _invalid()
    if actual != expected:
        _invalid()


def materialize_pending_workspace_policies(dsn, *, context: CloudAccessContext, limit=100) -> int:
    """pending 잠금·현재 검사·알림/detail/created 감사/ack 검증을 한 Tx로 확정한다."""
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
                    cursor = reader.execute('SELECT * FROM public.pending_egress_policy_notification_sources(%s)', (limit,))
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
                        row = reader.execute('SELECT public.materialize_egress_policy_notification(%s,%s)',
                            (source_id, target['accessible'] and python_view)).fetchone()
                        if row is None or len(row) != 1:
                            _invalid()
                        result = row[0]
                        if result is None:
                            continue  # Python 거부/SQL 허용의 차이는 pending으로 유지한다.
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
