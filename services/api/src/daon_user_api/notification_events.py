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


@dataclass(frozen=True)
class _CompletedRecipientScope:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str
    notebook_id: str


def _studio_job_output_version(connection, context, job_id):
    """완료 Job key의 실제 생성 원장·Notebook·모든 근거를 읽는다. 과거 사건의 xmin은 검사하지 않는다."""
    row=connection.execute("""SELECT ov.record_id FROM public.studio_generation_jobs j
      JOIN public.studio_outputs so ON so.tenant_id=j.tenant_id AND so.workspace_id=j.workspace_id AND so.record_id=j.studio_output_id
      JOIN public.output_versions ov ON ov.tenant_id=so.tenant_id AND ov.workspace_id=so.workspace_id
        AND ov.studio_output_id=so.record_id AND ov.record_id=j.output_version_id
      JOIN public.generation_requests g ON g.tenant_id=so.tenant_id AND g.workspace_id=so.workspace_id AND g.record_id=so.generation_request_id
      JOIN public.generation_settings_snapshots gs ON gs.tenant_id=g.tenant_id AND gs.workspace_id=g.workspace_id
        AND gs.record_id=g.generation_settings_snapshot_id AND gs.record_id=ov.generation_settings_snapshot_id
      JOIN public.notebook_bindings b ON b.tenant_id=ov.tenant_id AND b.workspace_id=ov.workspace_id
        AND b.binding_kind='output_version' AND b.record_id=ov.record_id AND b.notebook_id=%s
      WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.job_id=%s AND j.actor_id=%s AND j.state='completed'
        AND j.request_json->>'notebook_id'=b.notebook_id
        AND so.created_by=j.actor_id AND ov.created_by=j.actor_id AND g.created_by=j.actor_id AND gs.created_by=j.actor_id
        AND so.trace_id=j.trace_id AND ov.trace_id=j.trace_id AND g.trace_id=j.trace_id AND gs.trace_id=j.trace_id
        AND g.state='submitted' AND g.canonical_json->>'notebook_id'=b.notebook_id
        AND g.canonical_json->'source_id'=j.request_json->'source_id'
        AND g.canonical_json->'source_version_ids'=j.request_json->'source_version_ids'
        AND ov.canonical_json->>'notebook_id'=b.notebook_id
        AND ov.canonical_json->'source_id'=j.request_json->'source_id'
        AND ov.canonical_json->'source_version_ids'=j.request_json->'source_version_ids'
        AND so.record_id='output-'||substr(encode(sha256(convert_to(j.tenant_id||'|'||j.workspace_id||'|'||j.actor_id||
          '|studio.generation.create|'||j.idempotency_key,'UTF8')),'hex'),1,32)
        AND ov.record_id='output-version-'||substr(encode(sha256(convert_to(j.tenant_id||'|'||j.workspace_id||'|'||j.actor_id||
          '|studio.generation.create|'||j.idempotency_key,'UTF8')),'hex'),1,32)
        AND g.record_id='generation-'||substr(encode(sha256(convert_to(j.tenant_id||'|'||j.workspace_id||'|'||j.actor_id||
          '|studio.generation.create|'||j.idempotency_key,'UTF8')),'hex'),1,32)
        AND gs.record_id='settings-'||substr(encode(sha256(convert_to(j.tenant_id||'|'||j.workspace_id||'|'||j.actor_id||
          '|studio.generation.create|'||j.idempotency_key,'UTF8')),'hex'),1,32)
        AND NOT EXISTS(SELECT 1 FROM public.evidence_references er LEFT JOIN public.evidence_spans es
          ON es.tenant_id=er.tenant_id AND es.workspace_id=er.workspace_id AND es.record_id=er.evidence_span_id
            AND es.source_version_id=er.source_version_id
          WHERE er.tenant_id=j.tenant_id AND er.workspace_id=j.workspace_id AND er.output_version_id=ov.record_id
            AND (es.record_id IS NULL OR NOT (j.request_json->'source_version_ids' @> jsonb_build_array(er.source_version_id))))
        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(j.request_json->'source_version_ids') x(version_id)
          WHERE NOT EXISTS(SELECT 1 FROM public.evidence_references er JOIN public.evidence_spans es
            ON es.tenant_id=er.tenant_id AND es.workspace_id=er.workspace_id AND es.record_id=er.evidence_span_id
              AND es.source_version_id=er.source_version_id
            WHERE er.tenant_id=j.tenant_id AND er.workspace_id=j.workspace_id AND er.output_version_id=ov.record_id
              AND er.source_version_id=x.version_id))""",
        (context.notebook_id,context.tenant_id,context.workspace_id,job_id,context.actor_id)).fetchone()
    return None if row is None else _checked_id(row[0])


def _completed_event_access(connection, context, *, origin_event_id, notebook_id=None, origin_actor_id=None):
    """문서 최종/질문 완료 원장의 현재 본인·자료 접근을 읽는다. 발행 Tx 검사는 별도 SQL에서 한다."""
    from .authorization import Action
    from .open_law_source_access import _current_auth_grant, OpenLawAccessError, require_open_law_source_access
    _checked_id(origin_event_id)
    original=connection.execute('SELECT action,target_type,target_id,after_value->>\'state\' FROM public.audit_events '
        "WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s AND actor_id=%s AND outcome='succeeded'",
        (context.tenant_id,context.workspace_id,origin_event_id,
         context.actor_id if origin_actor_id is None else _checked_id(origin_actor_id))).fetchone()
    if original is None:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    action,target_type,target,origin_state=original
    if (action,target_type)==('canon.transition','ProcessingRun'):
        if origin_state not in {'completed','failed'}:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
        row=connection.execute("SELECT r.created_by,r.canonical_json->>'notebook_id',ARRAY[r.source_version_id],"
            "jsonb_build_array(jsonb_build_object('origin','raw_source','context_item_id',sv.source_id,"
            "'source_id',sv.source_id,'source_version_id',sv.record_id,'digest_sha256',NULL)) "
            'FROM public.processing_runs r JOIN public.source_versions sv ON sv.tenant_id=r.tenant_id '
            'AND sv.workspace_id=r.workspace_id AND sv.record_id=r.source_version_id '
            "WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s AND r.state=%s",
            (context.tenant_id,context.workspace_id,target,origin_state)).fetchone()
        if row is not None:
            notebook_id=row[1]
    elif (action,target_type)==('canon.transition','Run') and origin_state=='failed':
        # commit된 실패의 원 전이/receipt/정식 감사·snapshot 계보를 확인한다. 현재 xmin/trace는 요구하지 않는다.
        row=connection.execute("""SELECT r.created_by,%(notebook)s::text AS notebook_id,s.canonical_json->'source_version_ids' AS source_versions,
          s.canonical_json->'items' AS source_items,NULL::jsonb AS insufficient
        FROM public.runs r
        JOIN public.conversations c ON c.tenant_id=r.tenant_id AND c.workspace_id=r.workspace_id AND c.record_id=r.conversation_id
        JOIN public.notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id
          AND b.binding_kind='conversation_thread' AND b.record_id=c.record_id AND b.notebook_id=%(notebook)s AND b.created_by=r.created_by
        JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id
        JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id AND s.record_id=rs.scope_snapshot_id
        JOIN public.knowledge_scopes k ON k.tenant_id=s.tenant_id AND k.workspace_id=s.workspace_id AND k.record_id=s.knowledge_scope_id
        JOIN public.canon_state_transitions t ON t.tenant_id=r.tenant_id AND t.workspace_id=r.workspace_id
          AND t.entity_type='Run' AND t.record_id=r.record_id AND t.transition_id=%(origin)s
        JOIN public.canon_transition_attempts receipt ON receipt.tenant_id=t.tenant_id AND receipt.workspace_id=t.workspace_id
          AND receipt.entity_type='Run' AND receipt.record_id=r.record_id AND receipt.attempt_id=t.transition_id
        JOIN public.audit_events a ON a.tenant_id=t.tenant_id AND a.workspace_id=t.workspace_id AND a.event_id=t.transition_id
        JOIN public.egress_decisions e ON e.tenant_id=r.tenant_id AND e.workspace_id=r.workspace_id AND e.run_id=r.record_id
          AND e.record_id=rs.canonical_json->>'egress_decision_id'
        JOIN public.routing_decisions d ON d.tenant_id=e.tenant_id AND d.workspace_id=e.workspace_id AND d.egress_decision_id=e.record_id
          AND d.run_id=r.record_id
        JOIN public.audit_events ea ON ea.tenant_id=e.tenant_id AND ea.workspace_id=e.workspace_id
          AND ea.event_id='audit-egress-'||substr(encode(sha256(convert_to(r.record_id||'|'||e.record_id,'UTF8')),'hex'),1,32)
        WHERE r.tenant_id=%(tenant)s AND r.workspace_id=%(workspace)s AND r.record_id=%(target)s
          AND r.state='failed' AND r.version>1 AND r.created_by=%(actor)s
          AND r.canonical_json->>'context_mode' IN ('raw_only','general_ungrounded')
          AND (r.canonical_json->>'context_mode'<>'general_ungrounded'
            OR (r.canonical_json->'source_id'='null'::jsonb AND r.canonical_json->'source_version_id'='null'::jsonb))
          AND r.canonical_json->>'request_fingerprint' ~ '^sha256:[0-9a-f]{64}$'
          AND jsonb_typeof(r.canonical_json->'frozen_routing_context')='object'
          AND r.canonical_json->'frozen_routing_context'->>'fingerprint' ~ '^sha256:[0-9a-f]{64}$'
          AND r.canonical_json->'frozen_routing_context'->>'payload_fingerprint' ~ '^sha256:[0-9a-f]{64}$'
          AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['provider_kind','connection_id','provider_code','model_id','credential_version',
              'default_version','catalog_version','routing_owner','daon_fallback_allowed']) field
            WHERE (r.canonical_json->field) IS DISTINCT FROM (r.canonical_json->'frozen_routing_context'->field))
          AND %(origin)s='tr-'||substr(encode(sha256(convert_to(r.record_id||'|failed|'||(r.version-1)::text,'UTF8')),'hex'),1,32)
          AND t.target_state='failed' AND t.transition_version=r.version AND t.actor_id=r.created_by AND t.trace_id=r.trace_id
          AND t.source_state IN ('accepted','planning','retrieving','generating','validating') AND t.reason_code='QUESTION_FAILED'
          AND receipt.actor_id=t.actor_id AND receipt.trace_id=t.trace_id AND receipt.policy_version=t.policy_version
          AND receipt.source_state=t.source_state AND receipt.target_state=t.target_state AND receipt.reason_code=t.reason_code
          AND receipt.outcome='succeeded' AND receipt.result_state='failed' AND receipt.result_version=r.version
          AND receipt.expected_version=r.version-1 AND receipt.current_version=receipt.expected_version AND receipt.safe_error_code IS NULL
          AND a.actor_id=t.actor_id AND a.action='canon.transition' AND a.target_type='Run' AND a.target_id=r.record_id
          AND a.outcome='succeeded' AND a.trace_id=r.trace_id AND a.policy_version=t.policy_version
          AND a.before_value=jsonb_build_object('state',t.source_state,'version',r.version-1)
          AND a.after_value=jsonb_build_object('state','failed','version',r.version)
          AND a.metadata=jsonb_build_object('attempt_id',t.transition_id,'reason_code','QUESTION_FAILED')
          AND c.record_id='conversation-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
          AND c.created_by=r.created_by AND c.trace_id=r.trace_id
          AND c.canonical_json=jsonb_build_object('current_run_id',r.record_id,
            'current_run_result_id','run-result-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32))
          AND rs.record_id='run-snapshot-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
          AND s.record_id='scope-snapshot-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
          AND k.record_id='scope-'||substr(encode(sha256(convert_to(CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
            THEN 'general_ungrounded' ELSE 'raw_only|'||(SELECT string_agg(x.value,'|' ORDER BY x.ordinality)
              FROM jsonb_array_elements_text(s.canonical_json->'source_version_ids') WITH ORDINALITY x(value,ordinality)) END,'UTF8')),'hex'),1,32)
          AND rs.created_by=r.created_by AND rs.trace_id=r.trace_id AND s.created_by=r.created_by AND s.trace_id=r.trace_id
          AND k.canonical_json=jsonb_build_object('mode',r.canonical_json->>'context_mode',
            'source_version_ids',s.canonical_json->'source_version_ids','items',s.canonical_json->'items')
          AND jsonb_typeof(s.canonical_json->'source_version_ids')='array'
          AND jsonb_typeof(s.canonical_json->'items')='array'
          AND s.canonical_json->'source_version_ids'=(SELECT coalesce(jsonb_agg(x.value->'source_version_id' ORDER BY x.ordinality),'[]'::jsonb)
            FROM jsonb_array_elements(s.canonical_json->'items') WITH ORDINALITY x(value,ordinality))
          AND ((r.canonical_json->>'context_mode'='general_ungrounded'
              AND s.canonical_json->'source_version_ids'='[]'::jsonb AND s.canonical_json->'items'='[]'::jsonb)
            OR (r.canonical_json->>'context_mode'='raw_only' AND jsonb_array_length(s.canonical_json->'items') BETWEEN 1 AND 8
              AND s.canonical_json->'items'->0->>'source_id'=r.canonical_json->>'source_id'
              AND s.canonical_json->'items'->0->>'source_version_id'=r.canonical_json->>'source_version_id'
              AND (SELECT count(DISTINCT (x.value->>'source_id',x.value->>'source_version_id'))
                FROM jsonb_array_elements(s.canonical_json->'items') x(value))=jsonb_array_length(s.canonical_json->'items')
              AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.canonical_json->'items') x(value) WHERE NOT coalesce(
                jsonb_typeof(x.value->'source_id')='string' AND jsonb_typeof(x.value->'source_version_id')='string'
                AND x.value->>'source_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                AND x.value->>'source_version_id' ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
                AND x.value=jsonb_build_object('origin','raw_source','context_item_id',x.value->>'source_id',
                  'source_id',x.value->>'source_id','source_version_id',x.value->>'source_version_id','digest_sha256',NULL),false))))
          AND s.canonical_json=k.canonical_json||jsonb_build_object('knowledge_scope_id',k.record_id)
          AND rs.canonical_json->>'knowledge_scope_id'=k.record_id
          AND rs.canonical_json->'source_version_ids'=s.canonical_json->'source_version_ids'
          AND rs.canonical_json->'authority'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
            THEN '[]'::jsonb ELSE '["source"]'::jsonb END
          AND rs.canonical_json->>'prompt_version'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
            THEN 'general-conversation-v1' ELSE 'cp3-grounded-question-v1' END
          AND rs.canonical_json->>'tool_version'=CASE WHEN r.canonical_json->>'context_mode'='general_ungrounded'
            THEN 'provider-chat-v1' ELSE 'document-index-v1' END
          AND rs.canonical_json->'frozen_routing_context'=r.canonical_json->'frozen_routing_context'
          AND rs.canonical_json->>'user_policy_version'=t.policy_version
          AND rs.canonical_json->>'organization_policy_version'=t.policy_version
          AND e.record_id='egress-'||substr(encode(sha256(convert_to(r.record_id||'|'||
            (r.canonical_json->'frozen_routing_context'->>'fingerprint')||'|'||
            (r.canonical_json->'frozen_routing_context'->>'payload_fingerprint'),'UTF8')),'hex'),1,32)
          AND e.created_by=r.created_by AND e.trace_id=r.trace_id AND e.canonical_json->>'run_id'=r.record_id
          AND e.canonical_json->'allowed'='true'::jsonb
          AND e.canonical_json->'frozen_routing_context'=r.canonical_json->'frozen_routing_context'
          AND rs.canonical_json->>'egress_policy_fingerprint'=r.canonical_json->'frozen_routing_context'->>'fingerprint'
          AND d.record_id='routing-'||substr(encode(sha256(convert_to(r.record_id,'UTF8')),'hex'),1,32)
          AND d.created_by=r.created_by AND d.trace_id=r.trace_id AND d.canonical_json->>'run_id'=r.record_id
          AND d.canonical_json->>'routing_status'='selected' AND jsonb_typeof(d.canonical_json->'selected_deployment_id')='string'
          AND d.routing_policy_version_id=rs.routing_policy_version_id
          AND d.routing_policy_version_id='routing-policy-'||substr(encode(sha256(convert_to(
            (r.canonical_json->>'default_version')||'|'||(d.canonical_json->>'selected_deployment_id'),'UTF8')),'hex'),1,32)
          AND rs.canonical_json->>'routing_policy_version_id'=d.routing_policy_version_id
          AND rs.canonical_json->'candidate_order'=jsonb_build_array('deployment-'||substr(encode(sha256(convert_to(
            (d.canonical_json->>'selected_deployment_id')||'|'||(r.canonical_json->>'default_version'),'UTF8')),'hex'),1,32))
          AND ea.actor_id=r.created_by AND ea.action='question.egress.authorize' AND ea.target_type='Run' AND ea.target_id=r.record_id
          AND ea.outcome='succeeded' AND ea.trace_id=r.trace_id AND ea.policy_version=t.policy_version""",
            {'notebook':notebook_id,'origin':origin_event_id,'tenant':context.tenant_id,'workspace':context.workspace_id,
             'target':target,'actor':context.actor_id if origin_actor_id is None else origin_actor_id}).fetchone()
    elif (action,target_type)==('question.answer','Run'):
        expected_state='completed'
        row=connection.execute('SELECT r.created_by,%s,s.canonical_json->\'source_version_ids\',s.canonical_json->\'items\' '
            'FROM public.runs r JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id AND rs.workspace_id=r.workspace_id '
            'AND rs.run_id=r.record_id JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id '
            'AND s.record_id=rs.scope_snapshot_id WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s AND r.state=%s',
            (notebook_id,context.tenant_id,context.workspace_id,target,expected_state)).fetchone()
    elif (action,target_type)==('studio.generation.finish','StudioGenerationJob'):
        # 현재 확정 Job/version과 정식 원감사를 대조한다. 소비/조회에는 현재 transaction xmin을 요구하지 않는다.
        row=connection.execute('SELECT j.actor_id,j.request_json->>\'notebook_id\',j.request_json->\'source_version_ids\','
            "(SELECT jsonb_agg(jsonb_build_object('origin','raw_source','context_item_id',sv.source_id,"
            "'source_id',sv.source_id,'source_version_id',sv.record_id,'digest_sha256',NULL) ORDER BY x.ordinality) "
            "FROM jsonb_array_elements_text(j.request_json->'source_version_ids') WITH ORDINALITY x(version_id,ordinality) "
            'JOIN public.source_versions sv ON sv.tenant_id=j.tenant_id AND sv.workspace_id=j.workspace_id AND sv.record_id=x.version_id) '
            'FROM public.studio_generation_jobs j JOIN public.audit_events a ON a.tenant_id=j.tenant_id '
            'AND a.workspace_id=j.workspace_id AND a.target_id=j.job_id AND a.event_id=%s '
            'WHERE j.tenant_id=%s AND j.workspace_id=%s AND j.job_id=%s '
            "AND j.actor_id=%s AND j.state=%s AND j.state IN ('completed','failed','unavailable') "
            "AND j.version>1 AND j.lease_owner IS NULL AND j.lease_until IS NULL AND j.completed_at IS NOT NULL "
            "AND a.actor_id=j.actor_id AND a.action='studio.generation.finish' AND a.target_type='StudioGenerationJob' "
            "AND a.outcome='succeeded' AND a.trace_id=j.trace_id AND a.policy_version=j.policy_version "
            "AND a.trace_id=%s AND a.policy_version=%s AND j.request_json->>'notebook_id'=%s "
            "AND a.after_value=jsonb_build_object('state',j.state,'version',j.version) "
            "AND a.event_id='audit-studio-job-finish-'||substr(encode(sha256(convert_to("
            "j.tenant_id||'|'||j.workspace_id||'|'||j.job_id||'|'||j.version::text||'|'||j.state,'UTF8')),'hex'),1,32) "
            "AND jsonb_typeof(j.request_json->'source_version_ids')='array' "
            "AND jsonb_array_length(j.request_json->'source_version_ids')>0 "
            "AND (SELECT count(DISTINCT x.value) FROM jsonb_array_elements(j.request_json->'source_version_ids') x(value)) "
            "=jsonb_array_length(j.request_json->'source_version_ids') "
            "AND (j.request_json->>'source_id' IS NULL OR EXISTS(SELECT 1 FROM public.source_versions sv "
            "WHERE sv.tenant_id=j.tenant_id AND sv.workspace_id=j.workspace_id AND sv.source_id=j.request_json->>'source_id' "
            "AND j.request_json->'source_version_ids' @> jsonb_build_array(sv.record_id)))",
            (origin_event_id,context.tenant_id,context.workspace_id,target,context.actor_id,origin_state,
             context.trace_id,context.policy_version,notebook_id)).fetchone()
        if row is not None:
            notebook_id=row[1]
    else:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    if row is None:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    if notebook_id is None:
        # 과거 Notebook 미연결 작업에 가상 binding/수신 범위를 만들지 않는다.
        return None
    recipient=_CompletedRecipientScope(context.tenant_id,context.workspace_id,_checked_id(row[0]),
        context.trace_id,context.policy_version,_checked_id(notebook_id))
    if (action,target_type,origin_state)==('studio.generation.finish','StudioGenerationJob','completed'):
        if _studio_job_output_version(connection,recipient,target) is None:
            return None
    if not isinstance(row[2],list) or not isinstance(row[3],list):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    items=row[3]
    for item in items:
        if (not isinstance(item,dict) or set(item)!={'origin','context_item_id','source_id','source_version_id','digest_sha256'}
            or item['origin'] not in {'raw_source','daon_knowledge'}):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
        for field in ('context_item_id','source_id','source_version_id'):
            _checked_id(item[field])
        if item['origin']=='daon_knowledge':
            import re
            if not isinstance(item['digest_sha256'],str) or re.fullmatch('[0-9a-f]{64}',item['digest_sha256']) is None:
                raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
        elif item['context_item_id']!=item['source_id']:
            raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    if [item['source_version_id'] for item in items]!=row[2]:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    current=connection.execute("SELECT 1 FROM public.identity_memberships m JOIN public.identity_users u ON u.user_id=m.user_id "
        'JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id '
        "WHERE m.tenant_id=%s AND m.user_id=%s AND u.state='active' "
        "AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=m.tenant_id "
        "AND o.user_id=m.user_id AND o.state<>'active')",(recipient.tenant_id,recipient.actor_id)).fetchone()
    if current is None:
        return None
    # 자료가 없는 general도 현재 Notebook의 존재/폐기를 확인한다. 공유 Notebook의 owner 조건은 추가하지 않는다.
    notebook=connection.execute('SELECT 1 FROM public.notebooks n WHERE n.tenant_id=%s AND n.workspace_id=%s '
        'AND n.notebook_id=%s AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d '
        'WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id '
        "AND d.state IN ('accepted','deleting','completed'))",
        (recipient.tenant_id,recipient.workspace_id,recipient.notebook_id)).fetchone()
    if notebook is None:
        return None
    try:
        if (action,target_type,origin_state)==('canon.transition','Run','failed'):
            _current_auth_grant(recipient,connection,Action.QUERY)
        _current_auth_grant(recipient,connection,Action.VIEW)
        for item in items:
            from psycopg.types.json import Jsonb
            version=item['source_version_id']
            _checked_id(version)
            allowed=connection.execute('SELECT 1 FROM public.source_versions sv JOIN public.sources s '
                'ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id '
                'JOIN public.notebooks n ON n.tenant_id=sv.tenant_id AND n.workspace_id=sv.workspace_id '
                'WHERE sv.tenant_id=%s AND sv.workspace_id=%s AND sv.record_id=%s AND sv.source_id=%s AND n.notebook_id=%s '
                'AND ((%s=\'raw_source\' AND EXISTS(SELECT 1 FROM public.notebook_bindings b '
                "WHERE b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id AND b.binding_kind='source' "
                'AND b.record_id=sv.source_id AND b.version_id=sv.record_id)) '
                "OR (%s='daon_knowledge' AND EXISTS(SELECT 1 FROM public.notebook_bindings b JOIN public.scope_snapshots k "
                'ON k.tenant_id=b.tenant_id AND k.workspace_id=b.workspace_id AND k.record_id=b.record_id '
                'JOIN public.knowledge_scopes ks ON ks.tenant_id=k.tenant_id AND ks.workspace_id=k.workspace_id AND ks.record_id=k.knowledge_scope_id '
                'WHERE b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id '
                "AND b.binding_kind='knowledge_context' AND b.version_id IS NULL AND b.record_id=%s "
                "AND EXISTS(SELECT 1 FROM jsonb_array_elements(k.canonical_json->'items') x WHERE x=%s)))) "
                'AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE u.tenant_id=n.tenant_id '
                'AND u.workspace_id=n.workspace_id AND u.notebook_id=n.notebook_id AND u.source_id=sv.source_id AND u.source_version_id=sv.record_id) '
                'AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE d.tenant_id=sv.tenant_id AND d.workspace_id=sv.workspace_id '
                'AND d.source_id=sv.source_id AND d.source_active=false) '
                'AND NOT EXISTS(SELECT 1 FROM public.identity_auth_source_access a WHERE a.tenant_id=sv.tenant_id AND a.workspace_id=sv.workspace_id '
                'AND a.source_version_id=sv.record_id AND a.user_id=%s AND a.allowed=0) '
                'AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id '
                "AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))",
                (recipient.tenant_id,recipient.workspace_id,version,item['source_id'],recipient.notebook_id,
                 item['origin'],item['origin'],item['context_item_id'],Jsonb(item),recipient.actor_id)).fetchone()
            if allowed is None:
                return None
            with _snapshot_read_scope(connection,recipient):
                source_action=Action.QUERY if (action,target_type,origin_state)==('canon.transition','Run','failed') else Action.VIEW
                require_open_law_source_access(recipient,version,connection,action=source_action)
            if not _website_snapshot_current(connection,recipient,version):
                return None
    except OpenLawAccessError as error:
        from psycopg import Error
        from psycopg.pq import TransactionStatus
        cause=error
        seen=set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if isinstance(cause,Error):
                raise cause from None
            cause=cause.__cause__ or cause.__context__
        if connection.info.transaction_status==TransactionStatus.INERROR:
            raise NotificationError('DATABASE_UNAVAILABLE',503) from None
        return None
    return recipient


@dataclass(frozen=True)
class ResolvedNotificationTarget:
    notebook_id: str | None
    resource_type: str
    resource_id: str
    deep_link: str


class _NotificationTupleReadConnection:
    """repo의 dict row 설정은 유지하고 기존 접근 helper의 cursor만 같은 연결에서 tuple로 읽는다."""
    def __init__(self, connection):
        self._connection=connection

    def __getattr__(self, name):
        return getattr(self._connection,name)

    def execute(self, query, params=None):
        from psycopg.rows import tuple_row
        return self._connection.cursor(row_factory=tuple_row).execute(query,params)


def resolve_notification_target(connection, context, item, *, provenance):
    """불변 detail의 source 결속과 원감사·현재 자료를 재검증한다. 쓰기/소비/ack는 하지 않는다."""
    store=provenance.get('_source_store')
    if store == 'identity':
        if item.source_event_id.startswith('identity-reset-event-'):
            from .notification_password_reset import resolve_password_reset_notification
            return resolve_password_reset_notification(connection, context, item, provenance=provenance)
        if item.source_event_id.startswith('identity-password-event-'):
            from .notification_password import resolve_password_change_notification
            return resolve_password_change_notification(connection, context, item, provenance=provenance)
        if item.source_event_id.startswith('identity-login-event-'):
            from .notification_identity_login import resolve_identity_login_notification
            return resolve_identity_login_notification(connection, context, item, provenance=provenance)
        return _resolve_account_notification(connection, context, item, provenance=provenance)
    if store == 'organization':
        from .notification_organization import resolve_organization_notification
        return resolve_organization_notification(connection, context, item, provenance=provenance)
    if store == 'authorization' and item.source_event_id.startswith('c9-role-event-'):
        from .notification_c9_roles import resolve_c9_role_notification
        return resolve_c9_role_notification(connection, context, item, provenance=provenance)
    if store == 'authorization' and item.source_event_id.startswith('egress-policy-event-'):
        from .notification_egress_policy import resolve_workspace_policy_notification
        return resolve_workspace_policy_notification(connection, context, item, provenance=provenance)
    if store == 'authorization' and item.source_event_id.startswith('source-acl-event-'):
        from .notification_source_access import resolve_source_access_notification
        return resolve_source_access_notification(connection, context, item, provenance=provenance)
    if store == 'recovery' and item.source_event_id.startswith('recovery-event-'):
        from .notification_recovery import resolve_restore_completed_notification
        return resolve_restore_completed_notification(connection, context, item, provenance=provenance)
    origin=provenance.get('_origin_event_id')
    notebook=provenance.get('_notebook_id')
    policy=provenance.get('_policy_version')
    if (store not in {'document','question','studio'}
        or (item.tenant_id,item.workspace_id,item.recipient_id)!=(context.tenant_id,context.workspace_id,context.actor_id)
        or provenance.get('_detail_source_event_id')!=item.source_event_id
        or item.delivery_state.value!='delivered'):
        return None
    try:
        for value in (origin,notebook,policy):
            _checked_id(value)
    except NotificationError:
        return None
    expected_source=store+'-event-'+hashlib.sha256(f'{origin}|{context.workspace_id}'.encode()).hexdigest()[:32]
    if item.source_event_id!=expected_source:
        return None
    # 기존 detail guard가 source/수신자/범위를 결속했다. app에 비공개인 outbox는 직접 읽지 않는다.
    reader=_NotificationTupleReadConnection(connection)
    original=reader.execute("SELECT actor_id,action,target_type,target_id,trace_id,policy_version,after_value->>'state' "
        "FROM public.audit_events WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s AND outcome='succeeded'",
        (context.tenant_id,context.workspace_id,origin)).fetchone()
    if original is None or original[2:6]!=(item.resource_type,item.resource_id,item.trace_id,policy):
        return None
    actor,action,target_type,target,_,_,state=original
    scope=_CompletedRecipientScope(context.tenant_id,context.workspace_id,context.actor_id,item.trace_id,policy,notebook)
    try:
        if store=='studio' and (action,target_type)!=('studio.generation.finish','StudioGenerationJob'):
            if action not in _STUDIO_ACTIONS or target_type!=_STUDIO_ACTIONS[action][0]:
                return None
            if (item.kind,item.severity)!=(_STUDIO_ACTIONS[action][1],'info'):
                return None
            event=make_studio_event(replace(scope,actor_id=actor),operation=action,target_type=target_type,
                target_id=target,origin_event_id=origin)
            version,requester=_studio_target(reader,scope,event)
            if not _can_receive_studio(reader,scope,event,version,requester):
                return None
            link=notebook_notification_link(notebook,version)
        else:
            studio_job=store=='studio'
            failed=(action=='canon.transition' and state=='failed') or (
                studio_job and action=='studio.generation.finish' and state in {'failed','unavailable'})
            if (store=='document' and (action,target_type,state) not in {
                    ('canon.transition','ProcessingRun','completed'),('canon.transition','ProcessingRun','failed')}
                or store=='question' and not ((action,target_type)==('question.answer','Run')
                    or (action,target_type,state)==('canon.transition','Run','failed'))
                or studio_job and state not in {'completed','failed','unavailable'}):
                return None
            if (item.kind,item.severity)!=(('run_failed','warning') if failed else ('run_state','info')):
                return None
            if store=='question' and action=='question.answer':
                # 현재 Run/snapshot과 실제 conversation의 해당 Notebook 결속을 함께 확인한다.
                bound=reader.execute('SELECT 1 FROM public.runs r JOIN public.conversations c '
                    'ON c.tenant_id=r.tenant_id AND c.workspace_id=r.workspace_id AND c.record_id=r.conversation_id '
                    'JOIN public.notebook_bindings b ON b.tenant_id=c.tenant_id AND b.workspace_id=c.workspace_id '
                    "AND b.binding_kind='conversation_thread' AND b.record_id=c.record_id AND b.notebook_id=%s "
                    'AND b.created_by=r.created_by JOIN public.run_snapshots rs ON rs.tenant_id=r.tenant_id '
                    'AND rs.workspace_id=r.workspace_id AND rs.run_id=r.record_id '
                    'JOIN public.scope_snapshots s ON s.tenant_id=rs.tenant_id AND s.workspace_id=rs.workspace_id '
                    'AND s.record_id=rs.scope_snapshot_id JOIN public.knowledge_scopes k ON k.tenant_id=s.tenant_id '
                    'AND k.workspace_id=s.workspace_id AND k.record_id=s.knowledge_scope_id '
                    'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s '
                    "AND r.state='completed' AND r.created_by=%s AND c.created_by=r.created_by "
                    'AND r.trace_id=%s AND rs.created_by=r.created_by AND rs.trace_id=r.trace_id '
                    "AND rs.canonical_json->'source_version_ids'=s.canonical_json->'source_version_ids'",
                    (notebook,context.tenant_id,context.workspace_id,target,actor,item.trace_id)).fetchone()
                if bound is None:
                    return None
            recipient=_completed_event_access(reader,scope,origin_event_id=origin,
                notebook_id=notebook,origin_actor_id=actor)
            if recipient is None or (recipient.actor_id,recipient.notebook_id)!=(context.actor_id,notebook):
                return None
            if store=='document':
                row=reader.execute('SELECT source_version_id FROM public.processing_runs '
                    'WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                    (context.tenant_id,context.workspace_id,target)).fetchone()
                if row is None:
                    return None
                query='source_version_id='+quote(_checked_id(row[0]),safe='._-:')
            elif studio_job:
                if failed:
                    query='studio_job_id='+quote(_checked_id(target),safe='._-:')
                else:
                    version=_studio_job_output_version(reader,scope,target)
                    if version is None:
                        return None
                    query='output_version_id='+quote(version,safe='._-:')
            else:
                query='run_id='+quote(_checked_id(target),safe='._-:')
            link='/notebooks/'+quote(notebook,safe='._-:')+'?'+query
    except NotificationError as error:
        if error.code=='NOTIFICATION_SOURCE_INVALID':
            return None
        raise
    if item.deep_link!=link:
        return None
    return ResolvedNotificationTarget(notebook,item.resource_type,item.resource_id,link)


def enqueue_completed_event(connection, context, *, origin_event_id, notebook_id=None):
    """현재 접근과 실제 원업무 Tx 검증 뒤에만 의도를 저장한다. 전달된 connection을 commit/close하지 않는다."""
    recipient=_completed_event_access(connection,context,origin_event_id=origin_event_id,notebook_id=notebook_id)
    if recipient is None:
        return 0
    return int(connection.execute('SELECT public.enqueue_completed_notification(%s,%s)',
        (origin_event_id,recipient.notebook_id)).fetchone()[0])


def enqueue_studio_job_terminal(connection, job, *, state):
    """실제 최종 Job 원감사와 본인 source를 업무 connection에 기록한다. commit/close하지 않는다."""
    from psycopg.types.json import Jsonb
    row=connection.execute('SELECT actor_id,trace_id,policy_version,request_json,version,state FROM public.studio_generation_jobs '
        'WHERE tenant_id=%s AND workspace_id=%s AND job_id=%s',
        (job.tenant_id,job.workspace_id,job.job_id)).fetchone()
    if (row is None or row[0]!=job.actor_id or row[4]!=job.version+1 or row[5]!=state
        or state not in {'completed','failed','unavailable'} or not isinstance(row[3],dict)):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID',400)
    actor,trace,policy,request,version,_=row
    for value in (job.tenant_id,job.workspace_id,job.job_id,actor,trace,policy):
        _checked_id(value)
    # SQL 제한 함수도 이 단일 생성식으로 실제 job/최종 version/state의 원감사를 대조한다.
    origin='audit-studio-job-finish-'+hashlib.sha256(
        '|'.join((job.tenant_id,job.workspace_id,job.job_id,str(version),state)).encode()).hexdigest()[:32]
    connection.execute('INSERT INTO public.audit_events '
        '(event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,after_value) '
        "VALUES(%s,%s,%s,%s,'studio.generation.finish','StudioGenerationJob',%s,'succeeded',%s,%s,%s)",
        (origin,job.tenant_id,job.workspace_id,actor,job.job_id,trace,policy,Jsonb({'state':state,'version':version})))
    notebook=request.get('notebook_id')
    if notebook is None:
        return 0
    context=_CompletedRecipientScope(job.tenant_id,job.workspace_id,actor,trace,policy,_checked_id(notebook))
    return enqueue_completed_event(connection,context,origin_event_id=origin,notebook_id=notebook)


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


def materialize_pending_completed(dsn, *, context, limit=100):
    """문서/질문/Studio 최종 사건을 소비한다. DB/DTO 실패는 소비 Tx 전체 rollback이다."""
    from psycopg import Error,connect
    from .audit import ActorType,AuditEventDraft,AuditOutcome
    from .notification_postgres import _SELECT,_time,notification_from_row,serialize_notification_audit
    if type(limit) is not int or not 1<=limit<=100:
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
                cursor=connection.execute('SELECT * FROM public.pending_completed_notification_sources(%s)',(limit,))
                columns=[column.name for column in cursor.description]
                sources=[dict(zip(columns,row)) for row in cursor.fetchall()]
                for source in sources:
                    scope=_CompletedRecipientScope(context.tenant_id,context.workspace_id,context.actor_id,
                        source['trace_id'],source['policy_version'],source['notebook_id'])
                    original=connection.execute('SELECT actor_id,action,target_type,target_id,trace_id,policy_version,occurred_at,after_value->>\'state\' '
                        "FROM public.audit_events WHERE tenant_id=%s AND workspace_id=%s AND event_id=%s AND outcome='succeeded'",
                        (context.tenant_id,context.workspace_id,source['origin_event_id'])).fetchone()
                    studio_job=source['source_store']=='studio'
                    failed=original is not None and ((original[1]=='canon.transition' and original[7]=='failed')
                        or (studio_job and original[1]=='studio.generation.finish' and original[7] in {'failed','unavailable'}))
                    expected_action,expected_type=(('studio.generation.finish','StudioGenerationJob') if studio_job else
                        ('canon.transition','ProcessingRun') if source['source_store']=='document' else
                        ('canon.transition' if failed else 'question.answer','Run'))
                    expected_kind,expected_severity=('run_failed','warning') if failed else ('run_state','info')
                    event_id=source['source_store']+'-event-'+hashlib.sha256(
                        f"{source['origin_event_id']}|{context.workspace_id}".encode()).hexdigest()[:32]
                    if ((source['action'],source['target_type'],source['kind'],source['severity'],source['source_event_id'])!=
                        (expected_action,expected_type,expected_kind,expected_severity,event_id)):
                        raise NotificationError('NOTIFICATION_SOURCE_INVALID',503)
                    if original is not None and original[1:7]!=(source['action'],source['target_type'],source['target_id'],
                            source['trace_id'],source['policy_version'],source['occurred_at']):
                        raise NotificationError('NOTIFICATION_SOURCE_INVALID',503)
                    allowed=False
                    if original is not None:
                        try:
                            recipient=_completed_event_access(connection,scope,origin_event_id=source['origin_event_id'],
                                notebook_id=source['notebook_id'],origin_actor_id=original[0])
                        except NotificationError as error:
                            if error.code!='NOTIFICATION_SOURCE_INVALID':
                                raise
                        else:
                            allowed=recipient is not None and (recipient.actor_id,recipient.notebook_id)==(
                                context.actor_id,source['notebook_id'])
                    result=connection.execute('SELECT public.materialize_completed_notification(%s,%s)',
                        (source['source_event_id'],allowed)).fetchone()[0]
                    if result is None or result['state']=='suppressed':
                        continue
                    if result['state']!='delivered':
                        raise NotificationError('DATABASE_RESPONSE_INVALID',503)
                    cursor=connection.execute(_SELECT+' AND n.notification_id=%s',
                        (context.tenant_id,context.workspace_id,context.actor_id,result['notification_id']))
                    row=cursor.fetchone()
                    if row is None:
                        raise NotificationError('DATABASE_RESPONSE_INVALID',503)
                    item=notification_from_row(dict(zip((column.name for column in cursor.description),row)))
                    if source['source_store']=='document':
                        target=connection.execute('SELECT source_version_id FROM public.processing_runs WHERE tenant_id=%s '
                            'AND workspace_id=%s AND record_id=%s',
                            (context.tenant_id,context.workspace_id,source['target_id'])).fetchone()
                        if target is None:
                            raise NotificationError('DATABASE_RESPONSE_INVALID',503)
                        query='source_version_id='+quote(_checked_id(target[0]),safe='._-:')
                    elif studio_job:
                        if failed:
                            query='studio_job_id='+quote(_checked_id(source['target_id']),safe='._-:')
                        else:
                            version=_studio_job_output_version(connection,scope,source['target_id'])
                            if version is None:
                                raise NotificationError('DATABASE_RESPONSE_INVALID',503)
                            query='output_version_id='+quote(version,safe='._-:')
                    else:
                        query='run_id='+quote(_checked_id(source['target_id']),safe='._-:')
                    link='/notebooks/'+quote(_checked_id(source['notebook_id']),safe='._-:')+'?'+query
                    if (item.deep_link,item.resource_type,item.resource_id)!=(link,source['target_type'],source['target_id']):
                        raise NotificationError('DATABASE_RESPONSE_INVALID',503)
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


_ACCOUNT_OPERATIONS = {
    'device.trust': ('identity.device.trusted', 'device', 'device', 'info', {},
        '기기 신뢰 설정', '본인 기기의 신뢰 설정이 변경되었습니다.'),
    'device.revoke': ('identity.device.revoked', 'device', 'device', 'warning',
        {'sync_key_revoke_required': True}, '기기 철회', '본인 기기가 철회되었습니다.'),
    'session.revoke': ('identity.session.revoked', 'session', 'authentication', 'warning',
        {'reason_code': 'USER_REQUESTED'}, '세션 철회', '본인 세션이 철회되었습니다.'),
}


@contextmanager
def _account_notification_scope(connection, *, tenant_id, actor_id, workspace_id=None, enabled=None):
    """동일 실제 PG 연결의 한시 읽기 문맥이다. 역할/인증/transaction을 만들지 않는다."""
    from psycopg.pq import TransactionStatus
    raw = getattr(connection, '_connection', connection)
    reader = _NotificationTupleReadConnection(raw)
    settings = [('daon.account_actor', actor_id), ('app.tenant_id', tenant_id), ('app.actor_id', actor_id)]
    if workspace_id is not None:
        settings.extend((('app.workspace_id', workspace_id), ('app.capability', 'view')))
    if enabled is not None:
        if type(enabled) is not bool:
            raise NotificationError('INVALID_INPUT', 400)
        settings.append(('app.notification_inbox_enabled', 'true' if enabled else 'false'))
    previous = [(name, reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]) for name, _ in settings]
    try:
        for name, value in settings:
            reader.execute('SELECT set_config(%s,%s,true)', (name, value))
        yield reader
    finally:
        # 중단된 SQL transaction은 바깥 rollback/savepoint에 문맥 복원도 맡긴다.
        if raw.info.transaction_status != TransactionStatus.INERROR:
            for name, value in reversed(previous):
                reader.execute('SELECT set_config(%s,%s,true)', (name, value or ''))


def _account_event_data(connection, *, tenant_id, actor_id, request_event_id):
    """원 계정 요청 원장의 주사건 실제 index를 확인한다. 중앙 감사의 존재를 합성하지 않는다."""
    import json
    for value in (tenant_id, actor_id, request_event_id):
        _checked_id(value)
    row = connection.execute('SELECT operation,target_id,event_id,trace_id,policy_version,created_at,audit_events_json,response_json '
        'FROM public.identity_account_requests WHERE tenant_id=%s AND actor_id=%s AND event_id=%s',
        (tenant_id, actor_id, request_event_id)).fetchone()
    if row is None or row[0] not in _ACCOUNT_OPERATIONS:
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    operation, target, event_id, trace, policy, occurred_at, event_json, response_json = row
    action, target_type, kind, severity, metadata, title, summary = _ACCOUNT_OPERATIONS[operation]
    try:
        collected, response = json.loads(event_json), json.loads(response_json)
        if not isinstance(collected, list) or not isinstance(response, dict):
            raise ValueError
        primary = [(index, event) for index, event in enumerate(collected)
            if isinstance(event, dict) and (event.get('action'), event.get('target_type')) == (action, target_type)]
        if len(primary) != 1:
            raise ValueError
        index, event = primary[0]
        if (set(event) != {'action', 'target_type', 'target_id', 'metadata'}
            or event['target_id'] != target or event['metadata'] != metadata
            or response.get(target_type+'_id') != target or response.get('replayed') is not False):
            raise ValueError
        for value in (target, trace, policy):
            _checked_id(value)
    except (ValueError, TypeError, NotificationError):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503) from None
    return dict(operation=operation, origin_event_id=event_id+'-'+str(index), action=action,
        target_type=target_type, target_id=target, kind=kind, severity=severity, title=title, summary=summary,
        trace_id=trace, policy_version=policy, occurred_at=occurred_at)


def _account_view_allowed(connection, context):
    from .authorization import Action
    from .open_law_source_access import OpenLawAccessError, _current_auth_grant
    from psycopg import Error
    from psycopg.pq import TransactionStatus
    try:
        _current_auth_grant(context, connection, Action.VIEW)
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


def enqueue_account_event(connection, identity, principal, *, request_event_id):
    """신규 성공 계정 원장과 동일 연결에만 Workspace별 본인 source를 추가한다. commit/close0."""
    from .identity import IdentityError
    tenant, actor = str(principal['tenant_id']), str(principal['user_id'])
    try:
        with _account_notification_scope(connection, tenant_id=tenant, actor_id=actor, enabled=True) as reader:
            event = _account_event_data(reader, tenant_id=tenant, actor_id=actor, request_event_id=request_event_id)
            # 외부 wrapper는 새 transaction을 열므로 원업무 연결을 받는 내부 메서드만 쓴다.
            workspaces = identity._notification_workspace_ids(connection, user_id=actor, tenant_id=tenant)
            inserted = 0
            for workspace in workspaces:
                _checked_id(workspace)
                context = _CompletedRecipientScope(tenant, workspace, actor, event['trace_id'], event['policy_version'], None)
                with _account_notification_scope(connection, tenant_id=tenant, actor_id=actor, workspace_id=workspace) as scoped:
                    if not _account_view_allowed(scoped, context):
                        continue
                    foundation = scoped.execute('SELECT 1 FROM public.workspaces w JOIN public.user_accounts a '
                        "ON a.tenant_id=w.tenant_id AND a.user_id=%s AND a.status='active' "
                        'WHERE w.tenant_id=%s AND w.workspace_id=%s', (actor, tenant, workspace)).fetchone()
                    if foundation is None:
                        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
                    inserted += int(scoped.execute('SELECT public.enqueue_account_notification(%s)',
                        (event['origin_event_id'],)).fetchone()[0])
            return inserted
    except NotificationError:
        raise IdentityError('ACCOUNT_STORAGE_UNAVAILABLE', 503) from None


def _checked_identity_login_draft(event, oidc_transaction_id):
    """로그인 성공의 비민감 15필드만 INSERT 영수증 문맥에 허용한다."""
    from .audit import ActorType, AuditEventDraft, AuditOutcome
    from .notification_postgres import _time, serialize_notification_audit

    if (not isinstance(event, AuditEventDraft) or event.actor_type is not ActorType.USER
        or event.outcome is not AuditOutcome.SUCCEEDED or event.action != 'identity.login.succeeded'
        or event.target_type != 'session' or event.actor_id == 'anonymous'
        or event.workspace_id is not None or event.before is not None or event.after is not None):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    try:
        metadata = dict(event.metadata)
        client_type = metadata.get('client_type')
        if client_type not in ('web', 'native'):
            raise ValueError
        if oidc_transaction_id is None:
            if metadata != {'client_type': client_type, 'auth_method': 'local'}:
                raise ValueError
        else:
            _checked_id(oidc_transaction_id)
            if metadata != {'client_type': client_type}:
                raise ValueError
        for value in (event.event_id, event.actor_id, event.tenant_id, event.target_id,
            event.trace_id, event.policy_version):
            _checked_id(value)
        if _time(event.occurred_at) is None:
            raise ValueError
    except (TypeError, ValueError, NotificationError):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503) from None
    return serialize_notification_audit(event)


def _identity_login_workspaces(connection, identity_service, *, tenant_id, actor_id):
    from .cloud_storage import CloudAccessContext
    eligible = []
    with _account_notification_scope(connection, tenant_id=tenant_id, actor_id=actor_id):
        workspaces = identity_service._notification_workspace_ids(connection,
            user_id=actor_id, tenant_id=tenant_id)
        for workspace in workspaces:
            _checked_id(workspace)
            with _account_notification_scope(connection, tenant_id=tenant_id,
                actor_id=actor_id, workspace_id=workspace) as reader:
                # identity/auth에 있어도 Foundation이 없는 범위는 발행0이다. 행 생성/가상 소속0.
                foundation = reader.execute('SELECT 1 FROM public.workspaces w JOIN public.user_accounts a '
                    "ON a.tenant_id=w.tenant_id AND a.user_id=%s AND a.status='active' "
                    'WHERE w.tenant_id=%s AND w.workspace_id=%s',
                    (actor_id, tenant_id, workspace)).fetchone()
                if foundation is None:
                    continue
                context = CloudAccessContext(tenant_id, workspace, actor_id, 'view')
                if not _account_view_allowed(reader, context):
                    continue
                if workspace not in eligible:
                    eligible.append(workspace)
    return tuple(eligible)


@contextmanager
def prepare_identity_login_event(connection, identity_service, *, tenant_id, actor_id,
    event_factory, oidc_transaction_id=None):
    """정상 backend의 INSERT 직전에만 draft/scope를 고정한다. GUC는 독립 인증이 아니다."""
    import json
    from psycopg.pq import TransactionStatus
    workspaces = _identity_login_workspaces(connection, identity_service,
        tenant_id=tenant_id, actor_id=actor_id)
    if not workspaces:
        yield None
        return
    event = event_factory()
    payload = _checked_identity_login_draft(event, oidc_transaction_id)
    if (event.tenant_id, event.actor_id) != (tenant_id, actor_id):
        raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
    settings = (
        ('app.identity_login_emit', 'true'),
        ('app.identity_login_draft', json.dumps(payload, ensure_ascii=False, allow_nan=False)),
        ('app.identity_login_workspaces', json.dumps(workspaces)),
        ('app.identity_login_oidc', oidc_transaction_id or ''),
    )
    raw = getattr(connection, '_connection', connection)
    with _account_notification_scope(connection, tenant_id=tenant_id, actor_id=actor_id, enabled=True) as reader:
        previous = [(name, reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0])
            for name, _ in settings]
        try:
            for name, value in settings:
                reader.execute('SELECT set_config(%s,%s,true)', (name, value))
            yield event
        finally:
            # SQL 중단 시 바깥 rollback이 transaction-local 문맥도 복원한다.
            if raw.info.transaction_status != TransactionStatus.INERROR:
                for name, value in reversed(previous):
                    reader.execute('SELECT set_config(%s,%s,true)', (name, value or ''))


def enqueue_identity_login_event(connection, identity_service, *, event, oidc_transaction_id=None):
    """INSERT trigger 영수증을 중앙 append/Native 자식 저장 뒤 읽기만 재검증한다."""
    import json
    payload = _checked_identity_login_draft(event, oidc_transaction_id)
    verified = 0
    with _account_notification_scope(connection, tenant_id=event.tenant_id,
        actor_id=event.actor_id, enabled=True) as reader:
        try:
            settings = {name: reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]
                for name in ('app.identity_login_emit', 'app.identity_login_draft',
                    'app.identity_login_workspaces', 'app.identity_login_oidc')}
            scopes = json.loads(settings['app.identity_login_workspaces'])
            if (settings['app.identity_login_emit'] != 'true'
                or json.loads(settings['app.identity_login_draft']) != payload
                or (settings['app.identity_login_oidc'] or None) != oidc_transaction_id
                or not isinstance(scopes, list) or not scopes
                or any(not isinstance(scope, str) for scope in scopes)
                or len(set(scopes)) != len(scopes)):
                raise ValueError
            for scope in scopes:
                _checked_id(scope)
        except (ValueError, TypeError, NotificationError):
            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503) from None
        current = _identity_login_workspaces(connection, identity_service,
            tenant_id=event.tenant_id, actor_id=event.actor_id)
        for workspace in scopes:
            # INSERT 이후 실제 철회된 범위는 소비 시 suppressed된다. 새 범위는 추가하지 않는다.
            if workspace not in current:
                continue
            with _account_notification_scope(connection, tenant_id=event.tenant_id,
                actor_id=event.actor_id, workspace_id=workspace) as scoped:
                result = scoped.execute('SELECT public.verify_identity_login_notification_source(%s,%s)',
                    (event.event_id, oidc_transaction_id)).fetchone()
                if result is None or type(result[0]) is not bool:
                    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                if result[0] is not True:
                    raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
                verified += 1
    return verified


# S42_ACCOUNT_NOTIFICATION_CONSUMER_BEGIN
def _account_current_event(connection, context, origin):
    """현재 계정 사건을 같은 연결에서 읽는다. 원 session의 현재 활성은 요구하지 않는다."""
    import re
    if not isinstance(origin, str):
        return None
    request_event, separator, index = origin.rpartition('-')
    if not separator or re.fullmatch(r'0|[1-9][0-9]{0,8}', index) is None:
        return None
    with _account_notification_scope(connection, tenant_id=context.tenant_id, actor_id=context.actor_id,
        workspace_id=context.workspace_id) as reader:
        try:
            event = _account_event_data(reader, tenant_id=context.tenant_id, actor_id=context.actor_id,
                request_event_id=request_event)
        except NotificationError as error:
            if error.code == 'NOTIFICATION_SOURCE_INVALID':
                return None
            raise
        if event['origin_event_id'] != origin:
            return None
        membership = reader.execute('SELECT 1 FROM public.identity_memberships m '
            "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
            "JOIN public.user_accounts ua ON ua.tenant_id=m.tenant_id AND ua.user_id=m.user_id AND ua.status='active' "
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id '
            'JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id '
            'WHERE m.tenant_id=%s AND m.user_id=%s AND w.workspace_id=%s '
            'AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=m.tenant_id '
            "AND o.user_id=m.user_id AND w.workspace_kind='organization' AND o.state<>'active')",
            (context.tenant_id, context.actor_id, context.workspace_id)).fetchone()
        if membership is None or not _account_view_allowed(reader, context):
            return None
        # SQL table/key는 원장에서 검증된 두 내부 상수만 사용한다.
        table, key = ('identity_devices', 'device_id') if event['target_type'] == 'device' else ('identity_sessions', 'session_id')
        owned = reader.execute(f'SELECT 1 FROM public.{table} WHERE tenant_id=%s AND user_id=%s AND {key}=%s',
            (context.tenant_id, context.actor_id, event['target_id'])).fetchone()
        return event if owned is not None else None


def _account_source_matches(source, event, context):
    expected_source = 'identity-event-' + hashlib.sha256(
        (event['origin_event_id']+'|'+context.workspace_id).encode()).hexdigest()[:32]
    return (source['tenant_id'], source['workspace_id'], source['recipient_id'], source['source_store'],
        source['source_event_id'], source['origin_event_id'], source['notebook_id'], source['action'], source['target_type'],
        source['target_id'], source['kind'], source['severity'], source['trace_id'], source['policy_version'], source['occurred_at']) == (
            context.tenant_id, context.workspace_id, context.actor_id, 'identity', expected_source,
            event['origin_event_id'], None, event['action'], event['target_type'], event['target_id'], event['kind'],
            event['severity'], event['trace_id'], event['policy_version'], event['occurred_at'])


def _resolve_account_notification(connection, context, item, *, provenance):
    """delivered 조회/읽음 replay 전에도 원장·본인·현재 VIEW를 검증한다. source 소비/감사 쓰기0."""
    origin = provenance.get('_origin_event_id')
    if ((item.tenant_id, item.workspace_id, item.recipient_id) != (context.tenant_id, context.workspace_id, context.actor_id)
        or item.delivery_state.value != 'delivered' or provenance.get('_notebook_id') is not None
        or provenance.get('_detail_source_event_id') != item.source_event_id
        or item.deep_link != '/settings/account'):
        return None
    event = _account_current_event(connection, context, origin)
    if event is None:
        return None
    expected_source = 'identity-event-' + hashlib.sha256((origin+'|'+context.workspace_id).encode()).hexdigest()[:32]
    if (item.source_event_id, item.resource_type, item.resource_id, item.kind, item.severity, item.trace_id,
        provenance.get('_policy_version'), item.title, item.summary) != (
            expected_source, event['target_type'], event['target_id'], event['kind'], event['severity'], event['trace_id'],
            event['policy_version'], event['title'], event['summary']):
        return None
    return ResolvedNotificationTarget(None, event['target_type'], event['target_id'], '/settings/account')


def materialize_pending_accounts(dsn, *, context, limit=100):
    """실제 계정 source만 소비한다. bool 대신 SQL이 현재 접근을 재검증하며 DB 장애는 전체 rollback한다."""
    from psycopg import Error, connect
    from .audit import ActorType, AuditEventDraft, AuditOutcome
    from .notification_postgres import _SELECT, _time, notification_from_row, serialize_notification_audit
    if type(limit) is not int or not 1 <= limit <= 100:
        raise NotificationError('INVALID_INPUT', 400)
    for value in (context.tenant_id, context.workspace_id, context.actor_id):
        _checked_id(value)
    delivered = 0
    try:
        with connect(dsn, connect_timeout=3) as connection:
            with connection.transaction():
                with _account_notification_scope(connection, tenant_id=context.tenant_id, actor_id=context.actor_id,
                    workspace_id=context.workspace_id) as reader:
                    reader.execute("SET LOCAL statement_timeout='5s'")
                    reader.execute("SET LOCAL lock_timeout='2s'")
                    cursor = reader.execute('SELECT * FROM public.pending_account_notification_sources(%s)', (limit,))
                    sources = [dict(zip((column.name for column in cursor.description), row)) for row in cursor.fetchall()]
                    for source in sources:
                        event = _account_current_event(reader, context, source['origin_event_id'])
                        if event is not None and not _account_source_matches(source, event, context):
                            raise NotificationError('NOTIFICATION_SOURCE_INVALID', 503)
                        result = reader.execute('SELECT public.materialize_account_notification(%s,%s)',
                            (source['source_event_id'], event is not None)).fetchone()[0]
                        if result is None or result['state'] == 'suppressed':
                            continue
                        if result['state'] != 'delivered' or event is None:
                            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                        cursor = reader.execute(_SELECT+' AND n.notification_id=%s',
                            (context.tenant_id, context.workspace_id, context.actor_id, result['notification_id']))
                        row = cursor.fetchone()
                        if row is None:
                            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                        item = notification_from_row(dict(zip((column.name for column in cursor.description), row)))
                        if (item.title, item.summary, item.resource_type, item.resource_id, item.deep_link) != (
                            event['title'], event['summary'], event['target_type'], event['target_id'], '/settings/account'):
                            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                        draft = dict(result['draft'])
                        draft['occurred_at'] = _time(draft['occurred_at'])
                        draft['actor_type'] = ActorType(draft['actor_type'])
                        draft['outcome'] = AuditOutcome(draft['outcome'])
                        serialize_notification_audit(AuditEventDraft(**draft))
                        delivered += 1
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
    return delivered
# S42_ACCOUNT_NOTIFICATION_CONSUMER_END


def enqueue_organization_role_event(connection, *, origin_event_id, tenant_id, recipient_id, workspace_id):
    """신규 조직 역할/상태 원장 뒤 같은 PG 연결의 실제 활성 본인 source만 기록한다. commit/close0."""
    from .cloud_storage import CloudAccessContext
    from psycopg import Error
    from psycopg.pq import TransactionStatus

    for value in (origin_event_id, tenant_id, recipient_id, workspace_id):
        _checked_id(value)
    # organization adapter의 compat 연결은 identity adapter와 달리 _conn을 보유한다.
    raw = connection._conn
    reader = _NotificationTupleReadConnection(raw)
    settings = (('app.tenant_id', tenant_id), ('app.workspace_id', workspace_id),
        ('app.actor_id', recipient_id), ('app.capability', 'view'), ('app.notification_inbox_enabled', 'true'))
    previous = []
    try:
        previous = [(name, reader.execute('SELECT current_setting(%s,true)', (name,)).fetchone()[0]) for name, _ in settings]
        for name, value in settings:
            reader.execute('SELECT set_config(%s,%s,true)', (name, value))
        context = CloudAccessContext(tenant_id, workspace_id, recipient_id, 'view')
        # 이 기존 helper는 계정 원장이 아니라 동일 연결의 일반 실효 VIEW만 검사한다.
        if not _account_view_allowed(reader, context):
            return
        target = reader.execute('SELECT 1 FROM public.identity_memberships m '
            "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
            "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
            'JOIN public.identity_org_tenant_memberships o ON o.tenant_id=m.tenant_id AND o.user_id=m.user_id '
            "AND o.role='member' AND o.state='active' AND o.join_workspace_id=%s "
            'JOIN public.identity_auth_workspaces w ON w.tenant_id=o.tenant_id AND w.workspace_id=o.join_workspace_id '
            "AND w.workspace_kind='organization' JOIN public.workspaces cw ON cw.tenant_id=w.tenant_id AND cw.workspace_id=w.workspace_id "
            "WHERE m.tenant_id=%s AND m.user_id=%s AND m.role='member'",
            (workspace_id, tenant_id, recipient_id)).fetchone()
        if target is None:
            return
        result = reader.execute('SELECT public.enqueue_organization_role_notification(%s,%s)',
            (origin_event_id, recipient_id)).fetchone()
        if result is None or type(result[0]) is not bool:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
    except Error:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
    finally:
        if raw.info.transaction_status != TransactionStatus.INERROR:
            for name, value in reversed(previous):
                reader.execute('SELECT set_config(%s,%s,true)', (name, value or ''))
