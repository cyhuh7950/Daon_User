"""기존 SourceACL 사건의 raw·법령·웹 알림 의도와 현재 접근·소비를 검증한다."""
from __future__ import annotations

import hashlib
from contextlib import nullcontext

from psycopg import Error, connect
from psycopg.pq import TransactionStatus

from .audit import ActorType, AuditEventDraft, AuditOutcome, AuditValidationError
from .authorization import Action, AuthorizationError
from .cloud_storage import CloudAccessContext
from .document_index_postgres import _source_access_allowed
from .internet_connector import WebsiteImportContext
from .notification import NotificationError, _checked_id
from .notification_events import (
    ResolvedNotificationTarget,
    _NotificationTupleReadConnection,
    _account_notification_scope,
    _account_view_allowed,
    _snapshot_read_scope,
    _website_snapshot_current,
)
from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit
from .open_law_source_access import OpenLawAccessError, require_open_law_source_access


def _special_source_access_allowed(connection, context, source_version_id):
    """현재 법령·웹 접근만 재사용한다. 감싸진 DB 오류는 권한 거부로 삼키지 않는다."""
    try:
        with _snapshot_read_scope(connection, context):
            require_open_law_source_access(context, source_version_id, connection, action=Action.VIEW)
            return _website_snapshot_current(connection, context, source_version_id)
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


def _special_source_context(connection, *, context, source_id, source_version_id, canonical,
    trace_id, policy_version):
    """실제 binding과 canonical snapshot의 소유 Notebook 문맥이 정확히 하나일 때만 반환한다."""
    tables = {'mcp_open_law': 'open_law_source_snapshots', 'website': 'website_source_snapshots'}
    table = tables.get(canonical.get('origin_kind'))
    if table is None:
        return None
    try:
        snapshot_id = _checked_id(canonical.get('snapshot_id'))
    except NotificationError:
        return None
    if any(not isinstance(canonical.get(key), str) or not canonical[key]
        for key in ('raw_sha256', 'text_sha256')):
        return None
    candidates = connection.execute(
        'SELECT DISTINCT n.notebook_id FROM public.notebook_bindings b JOIN public.notebooks n '
        'ON n.tenant_id=b.tenant_id AND n.workspace_id=b.workspace_id AND n.notebook_id=b.notebook_id '
        'WHERE b.tenant_id=%s AND b.workspace_id=%s AND b.binding_kind=\'source\' '
        'AND b.record_id=%s AND b.version_id=%s AND n.created_by=%s '
        'AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id '
        'AND u.workspace_id=b.workspace_id AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id '
        'AND u.source_version_id=b.version_id) '
        'AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id '
        'AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id '
        "AND d.state IN ('accepted','deleting','completed')) ORDER BY n.notebook_id",
        (context.tenant_id, context.workspace_id, source_id, source_version_id, context.actor_id)).fetchall()
    valid = []
    for (notebook_id,) in candidates:
        special = WebsiteImportContext(context.tenant_id, context.workspace_id, notebook_id,
            context.actor_id, trace_id, policy_version)
        with _snapshot_read_scope(connection, special):
            # 표 이름은 위의 서버 고정 두 항목뿐이다. snapshot RLS도 실제 수신자/Notebook으로 읽는다.
            snapshot = connection.execute(
                f'SELECT snapshot_id,source_id,source_version_id,notebook_id,created_by,raw_sha256,text_sha256 '
                f'FROM public.{table} WHERE tenant_id=%s AND workspace_id=%s AND snapshot_id=%s '
                'AND source_id=%s AND source_version_id=%s AND notebook_id=%s AND created_by=%s',
                (context.tenant_id, context.workspace_id, snapshot_id, source_id, source_version_id,
                    notebook_id, context.actor_id)).fetchone()
            if snapshot != (snapshot_id, source_id, source_version_id, notebook_id, context.actor_id,
                canonical['raw_sha256'], canonical['text_sha256']):
                continue
            if not _special_source_access_allowed(connection, special, source_version_id):
                continue
        valid.append(special)
    return valid[0] if len(valid) == 1 else None


def enqueue_source_access_event(connection, *, event, recipient_id, source_version_id, allowed, acl_version) -> int:
    """원감사의 대상 user를 추정하지 않고 같은 setter의 실제 ACL 키/after를 확인한다. commit/close0."""
    try:
        if (not isinstance(event, AuditEventDraft) or event.actor_type is not ActorType.USER
            or event.outcome is not AuditOutcome.SUCCEEDED or event.action != 'authorization.source_access.changed'
            or event.target_type != 'source_access' or event.target_id != source_version_id
            or event.workspace_id is None or event.metadata != {} or type(allowed) is not bool
            or type(acl_version) is not int or acl_version < 1
            or event.after != {'allowed': allowed, 'version': acl_version}
            or type(event.after['allowed']) is not bool or type(event.after['version']) is not int):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        for value in (event.event_id, event.actor_id, event.tenant_id, event.workspace_id,
            event.trace_id, event.policy_version, recipient_id, source_version_id):
            _checked_id(value)
        if _time(event.occurred_at) is None:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        if event.before is None:
            if acl_version != 1:
                raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
        elif (set(event.before) != {'allowed', 'version'} or type(event.before['allowed']) is not bool
            or type(event.before['version']) is not int or event.before['version'] != acl_version - 1
            or event.before['version'] < 1):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)

        # Authorization PG compat 연결의 실제 원 연결만 사용한다. 역할·감사 원장을 만들지 않는다.
        raw = connection._conn
        with _account_notification_scope(raw, tenant_id=event.tenant_id, workspace_id=event.workspace_id,
            actor_id=recipient_id, enabled=True) as reader:
            current = reader.execute('SELECT allowed,version FROM public.identity_auth_source_access '
                'WHERE tenant_id=%s AND workspace_id=%s AND user_id=%s AND source_version_id=%s',
                (event.tenant_id, event.workspace_id, recipient_id, source_version_id)).fetchone()
            if current != (int(allowed), acl_version):
                raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
            member = reader.execute('SELECT 1 FROM public.identity_memberships m '
                "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
                "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
                'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=%s '
                'JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id '
                'WHERE m.tenant_id=%s AND m.user_id=%s '
                'AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o '
                'WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id '
                "AND w.workspace_kind='organization' AND o.state<>'active')",
                (event.workspace_id, event.tenant_id, recipient_id)).fetchone()
            if member is None:
                return 0
            context = CloudAccessContext(event.tenant_id, event.workspace_id, recipient_id, 'view')
            if not _account_view_allowed(reader, context):
                return 0
            source = reader.execute('SELECT sv.source_id,s.state,sv.canonical_json '
                'FROM public.source_versions sv JOIN public.sources s ON s.tenant_id=sv.tenant_id '
                'AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
                'WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s '
                'AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=s.tenant_id '
                'AND d.workspace_id=s.workspace_id AND d.source_id=s.record_id AND d.source_active=false)',
                (event.tenant_id, event.workspace_id, source_version_id)).fetchone()
            # 자료가 없는 기존 setter도 ACL 저장은 성공한다. raw에는 ready/index/latest 제한을 추가하지 않는다.
            if source is None or source[1] in {'expired', 'disabled', 'deleting', 'deleted'}:
                return 0
            if type(source[2]) is not dict:
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            if not _source_access_allowed(reader, context, source_version_id):
                return 0
            special = None
            if source[2].get('origin_kind') in {'mcp_open_law', 'website'}:
                special = _special_source_context(reader, context=context, source_id=source[0],
                    source_version_id=source_version_id, canonical=source[2],
                    trace_id=event.trace_id, policy_version=event.policy_version)
                if special is None:
                    return 0
            # 특별자료 RPC도 검증한 실제 Notebook 범위에서만 실행하고 ambient 문맥은 복원한다.
            with _snapshot_read_scope(reader, special) if special is not None else nullcontext():
                result = reader.execute('SELECT public.enqueue_source_access_notification(%s,%s,%s,%s)',
                    (event.event_id, event.actor_id, source_version_id, acl_version)).fetchone()
            if result is None or len(result) != 1 or type(result[0]) is not bool:
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            return int(result[0])
    except (Error, NotificationError, TypeError, ValueError):
        raise AuthorizationError('PERSISTENCE_UNAVAILABLE', 503) from None


_TITLE = '자료 접근권한 변경'
_SUMMARY = '본인의 자료 접근권한이 변경되었습니다.'
_ACTION = 'authorization.source_access.changed'
_PREFIX = 'source-acl-event-'
_TARGET_KEYS = frozenset({'accessible', 'title', 'summary', 'deep_link', 'source_event_id',
    'origin_event_id', 'target_type', 'target_id', 'kind', 'severity', 'trace_id',
    'policy_version', 'occurred_at', 'action'})


def _invalid():
    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)


def _expected_source_id(context, origin_event_id):
    return _PREFIX + hashlib.sha256(
        f'{origin_event_id}|{context.workspace_id}|{context.actor_id}'.encode()).hexdigest()[:32]


def _current_target(reader, context, source_id):
    row = reader.execute('SELECT public.current_source_access_notification_target(%s)', (source_id,)).fetchone()
    if row is None or len(row) != 1:
        _invalid()
    target = row[0]
    if target is None:
        return None
    # 원감사 부재는 SQL 오류다. 별도 전달 대기나 임의의 부분 응답은 허용하지 않는다.
    if type(target) is not dict or set(target) != _TARGET_KEYS or type(target['accessible']) is not bool:
        _invalid()
    if (target['source_event_id'], target['target_type'], target['kind'], target['severity'],
        target['title'], target['summary'], target['deep_link'], target['action']) != (
            source_id, 'source_access', 'membership', 'info', _TITLE, _SUMMARY, '/notifications', _ACTION):
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


def _current_source_access_allowed(reader, context, target, notebook_id):
    """원 SV의 현재 marker와 실제 snapshot 문맥을 읽어 저장된 Notebook을 별도로 결속한다."""
    if notebook_id is not None:
        try:
            _checked_id(notebook_id)
        except NotificationError:
            return False
    source = reader.execute('SELECT sv.source_id,s.state,sv.canonical_json '
        'FROM public.source_versions sv JOIN public.sources s ON s.tenant_id=sv.tenant_id '
        'AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
        'WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s '
        'AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=s.tenant_id '
        'AND d.workspace_id=s.workspace_id AND d.source_id=s.record_id AND d.source_active=false)',
        (context.tenant_id, context.workspace_id, target['target_id'])).fetchone()
    if source is None or source[1] in {'expired', 'disabled', 'deleting', 'deleted'}:
        return False
    if type(source[2]) is not dict:
        _invalid()
    if not _source_access_allowed(reader, context, target['target_id']):
        return False
    if source[2].get('origin_kind') not in {'mcp_open_law', 'website'}:
        return notebook_id is None
    if notebook_id is None:
        return False
    special = _special_source_context(reader, context=context, source_id=source[0],
        source_version_id=target['target_id'], canonical=source[2],
        trace_id=target['trace_id'], policy_version=target['policy_version'])
    return special is not None and special.notebook_id == notebook_id


def _source_matches(source, context, target=None):
    # nullable의 모양만 확인한다. 실제 raw/special 구분·원 Notebook 결속은 현재 자료 검사로 확인한다.
    if source['notebook_id'] is not None:
        try:
            _checked_id(source['notebook_id'])
        except NotificationError:
            return False
    if (source['tenant_id'], source['workspace_id'], source['recipient_id'], source['source_store'],
        source['action'], source['target_type'], source['kind'], source['severity'],
        source['state'], source['version'], source['delivered_at']) != (
            context.tenant_id, context.workspace_id, context.actor_id, 'authorization', _ACTION,
            'source_access', 'membership', 'info', 'pending', 1, None):
        return False
    if source['source_event_id'] != _expected_source_id(context, source['origin_event_id']):
        return False
    if target is None:
        return True
    # 원감사와 원 SourceVersion을 고정한다. 현재 ACL 버전으로 원사건을 대체하지 않는다.
    return (source['source_event_id'], source['origin_event_id'], source['target_id'], source['trace_id'],
        source['policy_version'], _time(source['occurred_at'])) == (target['source_event_id'],
            target['origin_event_id'], target['target_id'], target['trace_id'], target['policy_version'], target['occurred_at'])


def _item_matches(item, context, target, provenance, *, expected_notebook_id):
    return ((item.tenant_id, item.workspace_id, item.recipient_id, item.source_event_id,
        item.resource_type, item.resource_id, item.kind, item.severity, item.title, item.summary,
        item.deep_link, item.trace_id, item.delivery_state.value) == (
            context.tenant_id, context.workspace_id, context.actor_id, target['source_event_id'],
            'source_access', target['target_id'], 'membership', 'info', _TITLE, _SUMMARY,
            '/notifications', target['trace_id'], 'delivered')
        and (provenance.get('_source_store'), provenance.get('_origin_event_id'),
            provenance.get('_detail_source_event_id'), provenance.get('_notebook_id'),
            provenance.get('_policy_version')) == ('authorization', target['origin_event_id'],
                target['source_event_id'], expected_notebook_id, target['policy_version']))


def resolve_source_access_notification(connection, context, item, *, provenance) -> ResolvedNotificationTarget | None:
    """같은 연결에서 원사건·현재 접근·불변 detail을 검증한다. GET/읽음 replay 쓰기0."""
    if not isinstance(context, CloudAccessContext) or context.capability != 'view':
        return None
    if ((item.tenant_id, item.workspace_id, item.recipient_id) != (
            context.tenant_id, context.workspace_id, context.actor_id)
        or provenance.get('_source_store') != 'authorization'
        or not item.source_event_id.startswith(_PREFIX)
        or provenance.get('_detail_source_event_id') != item.source_event_id
        or item.delivery_state.value != 'delivered'
        or item.resource_type != 'source_access' or item.deep_link != '/notifications'):
        return None
    try:
        reader = _NotificationTupleReadConnection(connection)
        target = _current_target(reader, context, item.source_event_id)
        if target is None or not target['accessible'] or not _account_view_allowed(reader, context):
            return None
        notebook_id = provenance.get('_notebook_id')
        if not _current_source_access_allowed(reader, context, target, notebook_id):
            return None
        if not _item_matches(item, context, target, provenance, expected_notebook_id=notebook_id):
            return None
        return ResolvedNotificationTarget(notebook_id, 'source_access', target['target_id'], '/notifications')
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
            before=None, after={'recipient_id': item.recipient_id, 'kind': 'membership', 'delivery_state': 'delivered'},
            metadata={'source_event_id': item.source_event_id}))
    except (KeyError, TypeError, ValueError, AuditValidationError):
        _invalid()
    if actual != expected:
        _invalid()


def materialize_pending_source_access(dsn, *, context: CloudAccessContext, limit=100) -> int:
    """pending 잠금부터 현재 검사·알림/detail/감사의도/ack 검증까지 한 Tx로 확정한다."""
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
                    cursor = reader.execute('SELECT * FROM public.pending_source_access_notification_sources(%s)', (limit,))
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
                        python_allowed = (_account_view_allowed(reader, context)
                            and _current_source_access_allowed(reader, context, target, source['notebook_id']))
                        row = reader.execute('SELECT public.materialize_source_access_notification(%s,%s)',
                            (source_id, target['accessible'] and python_allowed)).fetchone()
                        if row is None or len(row) != 1:
                            _invalid()
                        result = row[0]
                        if result is None:
                            continue  # Python 거부/SQL 허용이면 임의 억제하지 않고 pending을 보존한다.
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
                        if (not _item_matches(item, context, target, detail,
                                expected_notebook_id=source['notebook_id']) or item.notification_id != expected_id
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
