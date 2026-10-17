"""L2a Studio 확정 기록의 알림 의도·현재 접근·중앙 전달 경계."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
from urllib.parse import quote

from .notification import NotificationError, _checked_id


_STUDIO_ACTIONS = {
    'studio.review': ('review_requests','review','검토 요청','검토할 산출물이 있습니다.'),
    'studio.approval_request': ('approval_requests','approval','승인 요청','승인 요청 상태를 확인하세요.'),
    'studio.approval': ('approvals','approval','승인 처리','산출물 승인 처리 상태를 확인하세요.'),
    'studio.delivery': ('deliveries','delivery','전달 기록','산출물 전달 기록을 확인하세요.'),
}


@dataclass(frozen=True)
class StudioNotificationEvent:
    tenant_id: str
    workspace_id: str
    actor_id: str
    notebook_id: str
    origin_event_id: str
    source_event_id: str
    action: str
    target_type: str
    target_id: str
    kind: str
    trace_id: str
    policy_version: str
    source_store: str = 'studio'


def make_studio_event(context, *, operation, target_type, target_id, origin_event_id):
    if operation not in _STUDIO_ACTIONS or target_type != _STUDIO_ACTIONS[operation][0]:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    for value in (context.tenant_id,context.workspace_id,context.actor_id,context.notebook_id,
                  context.trace_id,context.policy_version,target_id,origin_event_id):
        _checked_id(value)
    source = 'studio-event-'+hashlib.sha256(f'{origin_event_id}|{context.workspace_id}'.encode()).hexdigest()[:32]
    return StudioNotificationEvent(context.tenant_id,context.workspace_id,context.actor_id,context.notebook_id,
        origin_event_id,source,operation,target_type,target_id,_STUDIO_ACTIONS[operation][1],context.trace_id,context.policy_version)


def notebook_notification_link(notebook_id, output_version_id):
    # 한 개의 승인된 query만 생성한다. 임의 URL/redirect/원문은 입력받지 않는다.
    notebook, version = _checked_id(notebook_id), _checked_id(output_version_id)
    return f'/notebooks/{quote(notebook,safe="._-:")}?output_version_id={quote(version,safe="._-:")}'


def _studio_target(connection, context, event):
    """허용된 실제 기록과 원감사만 읽는다. 감사 문자열만으로 사건을 합성하지 않는다."""
    if event.action not in _STUDIO_ACTIONS or event.target_type != _STUDIO_ACTIONS[event.action][0]:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    # 표 이름은 위 서버 allowlist에서만 나온다.
    row = connection.execute(
        f"SELECT r.output_version_id,r.created_by FROM public.{event.target_type} r "
        "JOIN public.audit_events a ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id "
        "AND a.target_id=r.record_id AND a.actor_id=r.created_by "
        "WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s "
        "AND a.event_id=%s AND a.action=%s AND a.target_type=%s AND a.outcome='succeeded' "
        "AND a.trace_id=%s AND a.policy_version=%s",
        (context.tenant_id,context.workspace_id,event.target_id,event.origin_event_id,event.action,
         event.target_type,event.trace_id,event.policy_version)).fetchone()
    if row is None or row[1] != event.actor_id:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    requester=row[1]
    if event.action=='studio.approval':
        # 승인처리자와 원요청자를 구분한다. 누락된 원요청자를 actor로 추정하지 않는다.
        request=connection.execute(
            'SELECT r.created_by FROM public.approvals a JOIN public.approval_requests r '
            'ON r.tenant_id=a.tenant_id AND r.workspace_id=a.workspace_id AND r.record_id=a.approval_request_id '
            'AND r.output_version_id=a.output_version_id WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.record_id=%s',
            (context.tenant_id,context.workspace_id,event.target_id)).fetchone()
        if request is None:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
        requester=_checked_id(request[0])
    return row[0],requester


def _can_receive_studio(connection, context, event, version, requester):
    """기존 공유 Notebook 정책과 실제 현재 Action/Source 조건의 교집합만 사용한다."""
    from .authorization import Action
    from .open_law_source_access import _current_auth_grant, OpenLawAccessError, require_open_law_source_access
    current = connection.execute(
        "SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id "
        "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id "
        "WHERE m.tenant_id=%s AND m.user_id=%s AND u.state='active' "
        "AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o "
        "WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id AND o.state<>'active')",
        (context.tenant_id,context.actor_id)).fetchone()
    if current is None:
        return False
    bound = connection.execute(
        "SELECT 1 FROM public.notebooks n JOIN public.notebook_bindings b "
        "ON b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id "
        "JOIN public.output_versions v ON v.tenant_id=b.tenant_id AND v.workspace_id=b.workspace_id AND v.record_id=b.record_id "
        "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s "
        "AND b.binding_kind='output_version' AND b.record_id=%s "
        "AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id "
        "AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))",
        (context.tenant_id,context.workspace_id,context.notebook_id,version)).fetchone()
    if bound is None:
        return False
    action = (Action.DELIVER if event.action=='studio.delivery' else Action.VIEW
        if context.actor_id==requester else Action.REVIEW if event.action=='studio.review' else Action.APPROVE)
    if event.action=='studio.delivery' and context.actor_id!=requester:
        return False
    try:
        _current_auth_grant(context,connection,Action.VIEW)
        if action is not Action.VIEW:
            _current_auth_grant(context,connection,action)
        sources=connection.execute(
            "SELECT er.source_version_id,sv.source_id FROM public.evidence_references er "
            "JOIN public.source_versions sv ON sv.tenant_id=er.tenant_id AND sv.workspace_id=er.workspace_id "
            "AND sv.record_id=er.source_version_id WHERE er.tenant_id=%s AND er.workspace_id=%s AND er.output_version_id=%s",
            (context.tenant_id,context.workspace_id,version)).fetchall()
        for source_version,source in sources:
            allowed=connection.execute(
                "SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=%s AND b.workspace_id=%s "
                "AND b.notebook_id=%s AND b.binding_kind='source' AND b.record_id=%s AND b.version_id=%s "
                "AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=b.tenant_id "
                "AND u.workspace_id=b.workspace_id AND u.notebook_id=b.notebook_id AND u.source_id=b.record_id AND u.source_version_id=b.version_id) "
                "AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=b.tenant_id "
                "AND d.workspace_id=b.workspace_id AND d.source_id=b.record_id AND d.source_active=false) "
                "AND NOT EXISTS(SELECT 1 FROM public.identity_auth_source_access a WHERE a.tenant_id=b.tenant_id "
                "AND a.workspace_id=b.workspace_id AND a.source_version_id=b.version_id AND a.user_id=%s AND a.allowed=0)",
                (context.tenant_id,context.workspace_id,context.notebook_id,source,source_version,context.actor_id)).fetchone()
            if allowed is None:
                return False
            with _snapshot_read_scope(connection,context):
                require_open_law_source_access(context,source_version,connection,action=action)
            if not _website_snapshot_current(connection,context,source_version):
                return False
    except OpenLawAccessError as error:
        from psycopg import Error
        from psycopg.pq import TransactionStatus
        # 공통 law 검사에서 감싼 실제 DB 오류는 권한 거부로 소비하지 않는다.
        cause=error
        seen=set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause,Error):
                raise cause from None
            cause=cause.__cause__ or cause.__context__
        if connection.info.transaction_status == TransactionStatus.INERROR:
            raise NotificationError('DATABASE_UNAVAILABLE',503) from None
        return False
    # DB 오류는 여기서 삼키지 않는다. 호출 transaction이 rollback되어 pending으로 남는다.
    return True


@contextmanager
def _snapshot_read_scope(connection,context):
    """실제 수신자의 특별 snapshot 읽기 범위만 한시 적용한다. 역할/세션을 만들지 않는다."""
    previous=connection.execute("SELECT current_setting('app.actor_id',true),current_setting('app.notebook_id',true)").fetchone()
    try:
        connection.execute("SELECT set_config('app.actor_id',%s,true)",(context.actor_id,))
        connection.execute("SELECT set_config('app.notebook_id',%s,true)",(context.notebook_id,))
        yield
    finally:
        from psycopg.pq import TransactionStatus
        # SQL 오류로 이미 중단된 transaction은 원 오류를 보존하고 바깥 rollback에 맡긴다.
        if connection.info.transaction_status != TransactionStatus.INERROR:
            connection.execute("SELECT set_config('app.actor_id',%s,true)",(previous[0] or '',))
            connection.execute("SELECT set_config('app.notebook_id',%s,true)",(previous[1] or '',))


def _website_snapshot_current(connection,context,source_version):
    """website_source_postgres의 _notebook/get_snapshot 현재 조회 조건만 같은 connection에서 적용한다."""
    marker=connection.execute('SELECT canonical_json FROM public.source_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
        (context.tenant_id,context.workspace_id,source_version)).fetchone()
    if marker is None:
        return False
    if marker[0].get('origin_kind')!='website':
        return True
    with _snapshot_read_scope(connection,context):
        return connection.execute(
            'SELECT w.snapshot_id FROM public.website_source_snapshots w JOIN public.sources s '
            'ON s.tenant_id=w.tenant_id AND s.workspace_id=w.workspace_id AND s.record_id=w.source_id '
            'JOIN public.notebooks n ON n.tenant_id=w.tenant_id AND n.workspace_id=w.workspace_id AND n.notebook_id=w.notebook_id '
            'JOIN public.source_versions sv ON sv.tenant_id=w.tenant_id AND sv.workspace_id=w.workspace_id AND sv.record_id=w.source_version_id '
            'WHERE w.tenant_id=%s AND w.workspace_id=%s AND w.notebook_id=%s AND w.source_version_id=%s '
            "AND w.created_by=%s AND n.created_by=%s AND s.state='ready' "
            "AND EXISTS(SELECT 1 FROM public.notebook_bindings b WHERE b.tenant_id=w.tenant_id AND b.workspace_id=w.workspace_id "
            "AND b.notebook_id=w.notebook_id AND b.binding_kind='source' AND b.record_id=w.source_id AND b.version_id=w.source_version_id) "
            'AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=w.tenant_id AND u.workspace_id=w.workspace_id '
            'AND u.notebook_id=w.notebook_id AND u.source_id=w.source_id AND u.source_version_id=w.source_version_id) '
            'AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.workspace_id=w.workspace_id '
            'AND newer.source_id=w.source_id AND newer.version>sv.version) '
            'AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=w.tenant_id AND d.workspace_id=w.workspace_id '
            'AND d.source_id=w.source_id AND d.source_active=false) '
            "AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id "
            "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))",
            (context.tenant_id,context.workspace_id,context.notebook_id,source_version,context.actor_id,context.actor_id)).fetchone() is not None


def enqueue_studio_event(connection, context, *, operation, target_type, target_id, origin_event_id):
    event=make_studio_event(context,operation=operation,target_type=target_type,target_id=target_id,
        origin_event_id=origin_event_id)
    version,requester=_studio_target(connection,context,event)
    candidates=connection.execute(
        "SELECT m.user_id FROM public.identity_memberships m JOIN public.user_accounts a "
        "ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id WHERE m.tenant_id=%s ORDER BY m.user_id",
        (context.tenant_id,)).fetchall()
    inserted=0
    for (recipient,) in candidates:
        recipient_context=replace(context,actor_id=recipient)
        if _can_receive_studio(connection,recipient_context,event,version,requester):
            inserted+=int(connection.execute('SELECT public.enqueue_studio_notification(%s,%s,%s)',
                (event.origin_event_id,event.notebook_id,recipient)).fetchone()[0])
    return inserted


def materialize_pending_studio(dsn, *, context, limit=100):
    """현재 수신자의 등록 의도만 소비한다. DTO/감사의도 검증까지 실패하면 모두 rollback한다."""
    from psycopg import Error, connect
    from .audit import ActorType, AuditEventDraft, AuditOutcome
    from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit

    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError('INVALID_INPUT',400)
    for value in (context.tenant_id,context.workspace_id,context.actor_id):
        _checked_id(value)
    delivered=0
    try:
        with connect(dsn,connect_timeout=3) as connection:
            with connection.transaction():
                for name,value in (('tenant_id',context.tenant_id),('workspace_id',context.workspace_id),
                                   ('actor_id',context.actor_id),('capability','view')):
                    connection.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
                connection.execute("SET LOCAL statement_timeout='5s'")
                connection.execute("SET LOCAL lock_timeout='2s'")
                cursor=connection.execute('SELECT * FROM public.pending_studio_notification_sources(%s)',(limit,))
                columns=[column.name for column in cursor.description]
                sources=[dict(zip(columns,row)) for row in cursor.fetchall()]
                for source in sources:
                    recipient_context=replace(context,notebook_id=source['notebook_id'],
                        trace_id=source['trace_id'],policy_version=source['policy_version'])
                    original=connection.execute('SELECT actor_id FROM public.audit_events WHERE tenant_id=%s '
                        'AND workspace_id=%s AND event_id=%s AND outcome=\'succeeded\'',
                        (context.tenant_id,context.workspace_id,source['origin_event_id'])).fetchone()
                    allowed=False
                    version=None
                    if original is not None:
                        event=make_studio_event(replace(recipient_context,actor_id=original[0]),operation=source['action'],
                            target_type=source['target_type'],target_id=source['target_id'],origin_event_id=source['origin_event_id'])
                        if (event.source_event_id,event.source_store,event.kind,source['severity']) != (
                                source['source_event_id'],source['source_store'],source['kind'],'info'):
                            raise NotificationError('NOTIFICATION_SOURCE_INVALID',503)
                        try:
                            version,requester=_studio_target(connection,recipient_context,event)
                        except NotificationError as error:
                            if error.code!='NOTIFICATION_SOURCE_INVALID':
                                raise
                        else:
                            allowed=_can_receive_studio(connection,recipient_context,event,version,requester)
                    result=connection.execute('SELECT public.materialize_studio_notification(%s,%s)',
                        (source['source_event_id'],allowed)).fetchone()[0]
                    if result is None or result['state']=='suppressed':
                        continue
                    cursor=connection.execute(_SELECT+' AND n.notification_id=%s',
                        (context.tenant_id,context.workspace_id,context.actor_id,result['notification_id']))
                    row=cursor.fetchone()
                    if row is None:
                        raise NotificationError('DATABASE_RESPONSE_INVALID',503)
                    item=notification_from_row(dict(zip((column.name for column in cursor.description),row)))
                    if item.deep_link!=notebook_notification_link(source['notebook_id'],version):
                        raise NotificationError('DATABASE_RESPONSE_INVALID',503)
                    # 실제 INSERT trigger가 만든 draft를 같은 transaction에서 중앙 검증한다.
                    draft=dict(result['draft'])
                    draft['occurred_at']=_time(draft['occurred_at'])
                    draft['actor_type']=ActorType(draft['actor_type'])
                    draft['outcome']=AuditOutcome(draft['outcome'])
                    serialize_notification_audit(AuditEventDraft(**draft))
                    delivered+=1
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE',503) from None
    return delivered


def _ack_notification_audit(dsn, *, context, event_id, fingerprint):
    """중앙 commit과 내용 확인 후, 원래 수신자 범위의 의도만 별도 transaction으로 ack한다."""
    from .cloud_storage import CloudAccessContext
    from .notification_postgres import PostgresNotificationRepository
    repository=PostgresNotificationRepository(dsn,context=CloudAccessContext(
        context.tenant_id,context.workspace_id,context.actor_id,'view'))
    with repository.transaction() as connection:
        return connection.execute('SELECT public.ack_notification_audit(%s,%s) AS acknowledged',
            (event_id,fingerprint)).fetchone()['acknowledged']


def dispatch_pending_notification_audits(dsn, *, context, audit_store, limit=100, stop=None):
    """확정된 알림 의도를 중앙에 전달한다. 실패/내용 충돌은 pending이며 원업무를 되돌리지 않는다."""
    from dataclasses import fields
    from .audit import ActorType,AuditDuplicateEventError,AuditEventDraft,AuditOutcome,PostgresSecurityAuditStore
    from .cloud_storage import CloudAccessContext
    from .notification_postgres import PostgresNotificationRepository,_time,serialize_notification_audit
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError('INVALID_INPUT',400)
    if stop is not None and stop.is_set():
        return 0
    repository=PostgresNotificationRepository(dsn,context=CloudAccessContext(
        context.tenant_id,context.workspace_id,context.actor_id,'view'))
    try:
        with repository.transaction() as connection:
            pending=connection.execute('SELECT event_id,draft,fingerprint,'
                "fingerprint=encode(sha256(convert_to(draft::text,'UTF8')),'hex') AS intact "
                'FROM public.pending_notification_audits(%s)',(limit,)).fetchall()
    except Exception:
        # 읽기 장애도 전달 성공으로 세지 않는다. 기존 의도는 변경하지 않는다.
        return 0
    delivered=0
    for row in pending:
        if stop is not None and stop.is_set():
            break
        try:
            if not row['intact']:
                continue
            payload=dict(row['draft'])
            payload['occurred_at']=_time(payload['occurred_at'])
            payload['actor_type']=ActorType(payload['actor_type'])
            payload['outcome']=AuditOutcome(payload['outcome'])
            draft=AuditEventDraft(**payload)
            expected=serialize_notification_audit(draft)
            if (draft.event_id,draft.tenant_id,draft.workspace_id)!=(
                    row['event_id'],context.tenant_id,context.workspace_id):
                continue
            if draft.action not in {'notification.created','notification.read'}:
                continue
            def read_current():
                if isinstance(audit_store,PostgresSecurityAuditStore):
                    return audit_store.read(draft.event_id,tenant_id=draft.tenant_id)
                return audit_store.read(draft.event_id)
            existing=read_current()
            if existing is None:
                append=getattr(audit_store,'_append_admin_role',audit_store.append)
                try:
                    append(draft)
                except AuditDuplicateEventError:
                    # 다른 전달자가 먼저 commit한 경우도 같은 ID의 실제 내용 확인이 필수다.
                    pass
                existing=read_current()
            if existing is None:
                continue
            stored=AuditEventDraft(**{field.name:getattr(existing,field.name) for field in fields(AuditEventDraft)})
            if serialize_notification_audit(stored)!=expected:
                continue
            if stop is not None and stop.is_set():
                break
            delivered+=int(_ack_notification_audit(dsn,context=context,event_id=draft.event_id,
                fingerprint=row['fingerprint']))
        except Exception:
            # 중앙 append/조회/ack의 불확정도 다음 유한 sweep에서 같은 ID로 확인한다.
            continue
    return delivered
