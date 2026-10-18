"""현재 서버 범위에 결속한 지속 알림 저장소. 원업무 resolver 연결은 별도 단계다."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from psycopg import Error, connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .audit import AuditEventDraft, _validate_draft
from .cloud_storage import CloudAccessContext
from .notification import DeliveryState, InboxRequest, Notification, NotificationError, ReadTransition, _checked_id, _checked_time


def _time(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise NotificationError("DATABASE_RESPONSE_INVALID", 503) from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise NotificationError("DATABASE_RESPONSE_INVALID", 503)
    return value.astimezone(timezone.utc)


def notification_from_row(row) -> Notification:
    """변환 실패는 저장 transaction 안에서 발생시켜 부분 성공을 남기지 않는다."""
    try:
        return Notification(notification_id=_checked_id(row["notification_id"]),
            tenant_id=_checked_id(row["tenant_id"]), workspace_id=_checked_id(row["workspace_id"]),
            recipient_id=_checked_id(row["recipient_id"]), kind=row["kind"], severity=row["severity"],
            title=row["title"], summary=row["summary"], source_event_id=_checked_id(row["source_event_id"]),
            resource_type=_checked_id(row["resource_type"]), resource_id=_checked_id(row["resource_id"]),
            deep_link=row["deep_link"], audit_event_id=_checked_id(row["audit_event_id"]),
            trace_id=_checked_id(row["trace_id"]), delivery_state=DeliveryState(row["delivery_state"]),
            created_at=_time(row["created_at"]), delivered_at=_time(row["delivered_at"]),
            read_at=_time(row["read_at"]), version=int(row["version"]))
    except (KeyError, ValueError, TypeError):
        raise NotificationError("DATABASE_RESPONSE_INVALID", 503) from None


def serialize_notification_audit(draft: AuditEventDraft) -> dict:
    checked = _validate_draft(draft)
    # 중앙 감사의 기존 검증을 먼저 적용한다. 입력 검증 불가와 전달 장애를 구분한다.
    return json.loads(json.dumps(checked, ensure_ascii=False, sort_keys=True, allow_nan=False,
        default=lambda value: value.isoformat() if isinstance(value, datetime) else dict(value)))


_SELECT = """
SELECT n.*,d.kind,d.severity,d.title,d.summary,d.resource_type,d.resource_id,
  d.deep_link,d.trace_id,d.delivery_state,d.delivered_at,
  d.source_store AS _source_store,d.origin_event_id AS _origin_event_id,
  d.source_event_id AS _detail_source_event_id,d.notebook_id AS _notebook_id,d.policy_version AS _policy_version,
  'audit-notification-created-'||md5(n.tenant_id||'|'||n.notification_id) AS audit_event_id
FROM public.notifications n JOIN public.notification_details d
  ON d.tenant_id=n.tenant_id AND d.notification_id=n.notification_id
WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s
"""


def _inbox_access_denied(connection, error):
    """현재 권한 거부만 제외한다. 기존 law helper가 감싼 SQL 실패는 503으로 전달한다."""
    from psycopg.pq import TransactionStatus
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, Error):
            raise cause from None
        cause = cause.__cause__ or cause.__context__
    if connection.info.transaction_status == TransactionStatus.INERROR:
        raise NotificationError('DATABASE_UNAVAILABLE', 503) from None


def _inbox_owner_state(connection, row, kind):
    scope = (row['tenant_id'], row['workspace_id'])
    payload = row['canonical_json']
    if kind == 'approval':
        # 원 요청의 실제 review/Version 연결을 검사한다. TTL 계산은 읽기 함수이며 sweep하지 않는다.
        review = connection.execute(
            'SELECT record_id FROM public.review_requests WHERE tenant_id=%s AND workspace_id=%s '
            'AND record_id=%s AND output_version_id=%s',
            (*scope, row['review_request_id'], row['output_version_id'])).fetchone()
        if review is None or payload.get('review_request_id') != row['review_request_id']:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        status = row['state']
        if status not in {'pending', 'approved', 'rejected', 'expired', 'withdrawn'}:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        clock = connection.execute('SELECT public.studio_approval_deadline(%s,%s) AS deadline,'
            'clock_timestamp() AS current_time', (Jsonb(payload), row['created_at'])).fetchone()
        deadline = _time(clock['deadline'])
        if deadline is None:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        if status == 'pending' and _time(clock['current_time']) >= deadline:
            status = 'expired'
        return status, deadline
    if kind == 'delivery':
        approval = connection.execute(
            'SELECT a.record_id FROM public.approvals a JOIN public.approval_requests r '
            'ON r.tenant_id=a.tenant_id AND r.workspace_id=a.workspace_id AND r.record_id=a.approval_request_id '
            'AND r.output_version_id=a.output_version_id '
            'WHERE a.tenant_id=%s AND a.workspace_id=%s AND a.record_id=%s AND a.output_version_id=%s '
            "AND a.decision='approved' AND r.state='approved' AND a.canonical_json->>'decision'=a.decision",
            (*scope, row['approval_id'], row['output_version_id'])).fetchone()
        if (approval is None or payload.get('approval_id') != row['approval_id']
                or row['output_state'] != 'delivered' or row['state'] not in {None, 'delivered'}):
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        # 이는 외부 송신 성공이 아니라 실제 저장된 Delivery 기록 상태다.
        return 'delivered', None
    if row['state'] is not None:
        if row['state'] not in {'pending', 'in_review', 'approved', 'rejected', 'expired', 'withdrawn', 'cancelled'}:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        return row['state'], None
    # 기존 review 원장은 state를 저장하지 않는다. 해당 review의 실제 소유 요청 결정부터 반영한다.
    requests = connection.execute(
        'SELECT r.*,public.studio_approval_deadline(r.canonical_json,r.created_at) AS deadline,'
        'clock_timestamp() AS current_time FROM public.approval_requests r '
        'WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.review_request_id=%s AND r.output_version_id=%s '
        'ORDER BY r.created_at DESC,r.record_id DESC LIMIT 1',
        (*scope, row['record_id'], row['output_version_id'])).fetchone()
    if requests is not None:
        status = requests['state']
        if status not in {'pending', 'approved', 'rejected', 'expired', 'withdrawn'}:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        if status == 'pending':
            status = 'expired' if _time(requests['current_time']) >= _time(requests['deadline']) else 'in_review'
        return status, None
    status = {'draft': 'pending', 'review_requested': 'pending', 'in_review': 'in_review',
        'revision_requested': 'rejected', 'approved': 'approved', 'delivered': 'approved'}.get(row['output_state'])
    if status is None:
        raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
    return status, None


class PostgresNotificationRepository:
    """요청의 인증된 tenant/Workspace/actor만 받는다. 전역 조회·암묵 seed는 없다.

    L1은 내부 저장 경계다. 원자료 현재 접근 resolver와 실제 producer가 없는 동안
    runtime의 운영 발행/조회 경로를 이 adapter로 활성화하지 않는다.
    """
    transactional_audit = True

    def __init__(self, dsn: str, *, context: CloudAccessContext):
        if not isinstance(context, CloudAccessContext) or context.capability != "view":
            raise ValueError("NOTIFICATION_SCOPE_INVALID")
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("NOTIFICATION_DSN_REQUIRED")
        self._dsn, self.context = dsn, context

    @contextmanager
    def transaction(self):
        try:
            with connect(self._dsn, connect_timeout=3, row_factory=dict_row) as connection:
                with connection.transaction():
                    for name, value in (("tenant_id", self.context.tenant_id), ("workspace_id", self.context.workspace_id),
                                        ("actor_id", self.context.actor_id), ("capability", "view")):
                        connection.execute("SELECT set_config(%s,%s,true)", ("app." + name, value))
                    connection.execute("SET LOCAL statement_timeout='5s'")
                    connection.execute("SET LOCAL lock_timeout='2s'")
                    yield connection
        except Error as error:
            code, status = {"P0002": ("RESOURCE_UNAVAILABLE", 404), "P0003": ("VERSION_CONFLICT", 412),
                "23505": ("IDEMPOTENCY_CONFLICT", 409), "55000": ("INVALID_STATE_TRANSITION", 409),
                "22023": ("INVALID_INPUT", 400)}.get(error.sqlstate, ("DATABASE_UNAVAILABLE", 503))
            raise NotificationError(code, status) from None

    def _scope(self, tenant_id, recipient_id):
        if tenant_id != self.context.tenant_id or recipient_id != self.context.actor_id:
            raise NotificationError("RESOURCE_UNAVAILABLE", 404)
        return (tenant_id, self.context.workspace_id, recipient_id)

    def insert_if_absent(self, *_args):
        # 원장 resolver/producer가 연결되기 전 reference publish로 지속 사건을 합성하지 않는다.
        raise NotificationError("NOTIFICATION_SOURCE_NOT_CONNECTED", 503)

    def all_inbox_requests(self):
        """실제 소유 원장만 현재 권한으로 읽는다. Inbox/source/만료 처리 쓰기는 없다."""
        from .authorization import Action
        from .notification_events import (
            _NotificationTupleReadConnection, _can_receive_studio, make_studio_event,
            notebook_notification_link,
        )
        from .open_law_source_access import OpenLawAccessError, _current_auth_grant
        from .studio_workspace import StudioContext

        result = []
        with self.transaction() as connection:
            reader = _NotificationTupleReadConnection(connection)
            for table, operation, kind, action in (
                ('review_requests', 'review', 'review', Action.REVIEW),
                ('approval_requests', 'approval_request', 'approval', Action.APPROVE),
                ('deliveries', 'delivery', 'delivery', Action.DELIVER),
            ):
                state = 'r.state' if table == 'approval_requests' else 'NULL::text'
                rows = connection.execute(
                    f'SELECT r.*,{state} AS state,v.canonical_json AS output_payload,v.state AS output_state,'
                    'a.event_id AS origin_event_id,a.policy_version AS origin_policy_version '
                    f'FROM public.{table} r JOIN public.output_versions v '
                    'ON v.tenant_id=r.tenant_id AND v.workspace_id=r.workspace_id AND v.record_id=r.output_version_id '
                    'JOIN public.audit_events a ON a.tenant_id=r.tenant_id AND a.workspace_id=r.workspace_id '
                    'AND a.actor_id=r.created_by AND a.target_id=r.record_id AND a.trace_id=r.trace_id '
                    "AND a.outcome='succeeded' AND a.action=%s AND a.target_type=%s "
                    "AND a.event_id='audit-'||substr(r.record_id,length(%s)+2) "
                    "AND a.metadata=jsonb_build_object('result_id',r.record_id) "
                    'WHERE r.tenant_id=%s AND r.workspace_id=%s ORDER BY r.record_id',
                    ('studio.' + operation, table, operation.replace('_', '-'),
                     self.context.tenant_id, self.context.workspace_id),
                ).fetchall()
                for row in rows:
                    try:
                        record = _checked_id(row['record_id'])
                        version = _checked_id(row['output_version_id'])
                        actor = _checked_id(row['created_by'])
                        payload, output = row['canonical_json'], row['output_payload']
                        if (not isinstance(payload, dict) or not isinstance(output, dict)
                                or payload.get('output_version_id') != version
                                or re.fullmatch(operation.replace('_', '-') + r'-[0-9a-f]{32}', record) is None
                                or type(row['version']) is not int or row['version'] < 1):
                            raise ValueError
                        lineage = output.get('source_lineage', {})
                        if not isinstance(lineage, dict):
                            raise ValueError
                        notebook = output.get('notebook_id', lineage.get('notebook_id'))
                        if notebook is None:
                            # 원Notebook이 없는 legacy 입력을 현재 첫 binding으로 추정하지 않는다.
                            continue
                        notebook = _checked_id(notebook)
                        if (lineage.get('notebook_id', notebook) != notebook
                                or lineage.get('tenant_id', self.context.tenant_id) != self.context.tenant_id
                                or lineage.get('workspace_id', self.context.workspace_id) != self.context.workspace_id):
                            raise ValueError
                        if kind == 'delivery' and actor != self.context.actor_id:
                            continue
                        original = StudioContext(self.context.tenant_id, self.context.workspace_id,
                            actor, _checked_id(row['trace_id']), _checked_id(row['origin_policy_version']), notebook)
                        current = replace(original, actor_id=self.context.actor_id)
                        # requester에게 VIEW만 허용하는 알림 helper의 예외보다 먼저 정확한 처리 Action을 검사한다.
                        try:
                            _current_auth_grant(current, reader, action)
                        except OpenLawAccessError as error:
                            _inbox_access_denied(reader, error)
                            continue
                        event = make_studio_event(original, operation='studio.' + operation,
                            target_type=table, target_id=record, origin_event_id=row['origin_event_id'])
                        if not _can_receive_studio(reader, current, event, version, actor):
                            continue
                        status, due = _inbox_owner_state(connection, row, kind)
                        result.append(InboxRequest(record, kind, status, current.tenant_id,
                            current.workspace_id, current.actor_id, actor, due, 'output_version', version,
                            notebook_notification_link(notebook, version)))
                    except NotificationError as error:
                        if error.code == 'INVALID_INPUT':
                            raise NotificationError('DATABASE_RESPONSE_INVALID', 503) from None
                        raise
                    except (KeyError, TypeError, ValueError):
                        raise NotificationError('DATABASE_RESPONSE_INVALID', 503) from None
        return tuple(result)

    def notifications_for(self, *, tenant_id, recipient_id):
        scope = self._scope(tenant_id, recipient_id)
        with self.transaction() as connection:
            rows = connection.execute(_SELECT + " ORDER BY n.created_at DESC,n.notification_id DESC", scope).fetchall()
            return tuple(item for row in rows if (item := self._current_item(connection, row)) is not None)

    def _current_item(self, connection, row):
        from .notification_events import resolve_notification_target
        if row is None:
            return None
        item = notification_from_row(row)
        return item if resolve_notification_target(connection, self.context, item, provenance=row) is not None else None

    def notification_for(self, *, tenant_id, recipient_id, notification_id):
        scope = self._scope(tenant_id, recipient_id)
        with self.transaction() as connection:
            row = connection.execute(_SELECT + " AND n.notification_id=%s", (*scope, _checked_id(notification_id))).fetchone()
            return self._current_item(connection, row)

    def transition_read(self, *, notification_id, tenant_id, recipient_id, expected_etag,
                        idempotency_key, fingerprint, read_at, on_first_transition,
                        audit_draft_factory=None):
        scope = self._scope(tenant_id, recipient_id)
        match = re.fullmatch(r'"notification-([1-9][0-9]*)"', expected_etag or "")
        if match is None:
            raise NotificationError("VERSION_CONFLICT", 412)
        if audit_draft_factory is None:
            raise NotificationError("NOTIFICATION_AUDIT_REQUIRED", 503)
        with self.transaction() as connection:
            row = connection.execute(_SELECT + " AND n.notification_id=%s", (*scope, _checked_id(notification_id))).fetchone()
            item = self._current_item(connection, row)
            if item is None:
                raise NotificationError("RESOURCE_UNAVAILABLE", 404)
            # replay에서도 요청의 원 version으로 draft를 구성하되 SQL은 영수증을 먼저 판정한다.
            original = replace(item, version=int(match[1]), read_at=None)
            after = replace(original, read_at=_checked_time(read_at), version=original.version + 1)
            draft = replace(audit_draft_factory(original, after), event_id="audit-notification-read-" + hashlib.sha256(
                f"{tenant_id}|{self.context.workspace_id}|{recipient_id}|{notification_id}".encode()).hexdigest()[:32])
            payload = serialize_notification_audit(draft)
            result = connection.execute("SELECT public.read_notification_inbox(%s,%s,%s,%s,%s) AS receipt",
                (notification_id, int(match[1]), idempotency_key, fingerprint, Jsonb(payload))).fetchone()["receipt"]
            updated = replace(item, read_at=_time(result["read_at"]), version=int(result["version"]))
            return ReadTransition(updated, result["first_transition"])

    def encode_cursor(self, item_id):
        return base64.urlsafe_b64encode(_checked_id(item_id).encode("ascii")).decode("ascii").rstrip("=")

    def decode_cursor(self, cursor):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512:
                raise ValueError
            item_id = _checked_id(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True).decode("ascii"))
            if self.encode_cursor(item_id) != cursor:
                raise ValueError
            return item_id
        except (ValueError, UnicodeError):
            raise NotificationError("INVALID_CURSOR") from None


class ScopedPostgresNotificationRepository:
    """한 요청의 인증된 tenant/본인/실제 Workspace들만 조합한다. 전역 조회/임의 scope fallback은 없다."""
    transactional_audit = True
    encode_cursor = PostgresNotificationRepository.encode_cursor
    decode_cursor = PostgresNotificationRepository.decode_cursor

    def __init__(self, dsn: str, *, tenant_id: str, recipient_id: str, contexts: tuple[CloudAccessContext, ...],
                 studio_approval_repository=None):
        self.tenant_id, self.recipient_id = _checked_id(tenant_id), _checked_id(recipient_id)
        if not isinstance(dsn, str) or not dsn or not isinstance(contexts, tuple):
            raise ValueError('NOTIFICATION_SCOPE_INVALID')
        self._repositories = {}
        self._approval_repositories = {}
        for context in contexts:
            if (not isinstance(context, CloudAccessContext) or context.tenant_id != tenant_id
                or context.actor_id != recipient_id or context.capability != 'view'
                or context.workspace_id in self._repositories):
                raise ValueError('NOTIFICATION_SCOPE_INVALID')
            self._repositories[context.workspace_id] = PostgresNotificationRepository(dsn, context=context)
            if studio_approval_repository is not None:
                from .notification_studio_approval import StudioApprovalNotificationRepository
                self._approval_repositories[context.workspace_id] = StudioApprovalNotificationRepository(
                    studio_approval_repository, context=context)

    def _scope(self, tenant_id, recipient_id):
        if (tenant_id, recipient_id) != (self.tenant_id, self.recipient_id):
            raise NotificationError('RESOURCE_UNAVAILABLE', 404)

    def insert_if_absent(self, *_args):
        # 실제 원업무/source 의도 없이 Reference publish를 지속 알림으로 바꾸지 않는다.
        raise NotificationError('NOTIFICATION_SOURCE_NOT_CONNECTED', 503)

    def all_inbox_requests(self):
        result, seen = [], set()
        # S24 notice는 별도 원장 알림이다. Review/Approval/Delivery 사본을 만들지 않는다.
        for repository in self._repositories.values():
            for item in repository.all_inbox_requests():
                if item.request_id in seen:
                    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                seen.add(item.request_id)
                result.append(item)
        return tuple(result)

    def notifications_for(self, *, tenant_id, recipient_id):
        self._scope(tenant_id, recipient_id)
        result, seen = [], set()
        for repository in (*self._repositories.values(), *self._approval_repositories.values()):
            for item in repository.notifications_for(tenant_id=tenant_id, recipient_id=recipient_id):
                if item.notification_id in seen:
                    raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
                seen.add(item.notification_id)
                result.append(item)
        return tuple(result)

    def notification_for(self, *, tenant_id, recipient_id, notification_id):
        self._scope(tenant_id, recipient_id)
        _checked_id(notification_id)
        for repository in (*self._repositories.values(), *self._approval_repositories.values()):
            item = repository.notification_for(tenant_id=tenant_id, recipient_id=recipient_id,
                notification_id=notification_id)
            if item is not None:
                return item
        return None

    def transition_read(self, *, tenant_id, recipient_id, notification_id, **request):
        item = self.notification_for(tenant_id=tenant_id, recipient_id=recipient_id, notification_id=notification_id)
        if item is None:
            raise NotificationError('RESOURCE_UNAVAILABLE', 404)
        # 실제 read transaction 안에서도 원자료 접근/영수증을 다시 검사한다.
        repository = (self._approval_repositories.get(item.workspace_id)
            if item.resource_type == 'StudioApprovalNotice' else self._repositories[item.workspace_id])
        if repository is None:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        return repository.transition_read(tenant_id=tenant_id, recipient_id=recipient_id,
            notification_id=notification_id, **request)


_FUNCTIONS = {
    'current_password_reset_notification_event(text)': ('fc59fb30e4fd86b5c61dc307a3a22422', False),
    'enqueue_password_reset_notification(text)': ('22c7a089f560c90e77b925d199137538', True),
    'pending_password_reset_notification_sources(integer)': ('a8b0bb9585e795ecbdcd03b95795ad81', True),
    'current_password_reset_notification_target(text)': ('4f00c49aebfc578df348cb2deaf11a2c', True),
    'materialize_password_reset_notification(text,boolean)': ('16bebb702f50a2d335351f6aad96831a', True),
    'guard_identity_password_change_event()': ('4c15e2e951d16aa776027eedcdb31ddf', False),
    'current_password_change_notification_event(text)': ('a6082b27138da1b4833a406e88b38f60', False),
    'enqueue_password_change_notification(text)': ('aef35e924dd20c82130bbe584187cf9c', True),
    'pending_password_change_notification_sources(integer)': ('e0d55595b481095b5cdf65620022fa2b', True),
    'current_password_change_notification_target(text)': ('d14841a93c61c37ad230c8f3f5a8aaf7', True),
    'materialize_password_change_notification(text,boolean)': ('c2ae409f6144ba3e25c8d6041ab3dce7', True),
    'pending_notification_dispatch_scopes(integer,text,text,text)': ('f87c951bf61ce8176ba73e6f6ffefb93', True),
    'lock_question_knowledge_selection(text,text,text,text,text,text)': ('5158462d9140ecf4c161ffa6a30bacaa', True),
    'guard_studio_generation_job()': ('04d7723c5788dcda1cb745c350f044bb', False),
    'guard_notification_storage()': ('6a78b05a96c78166a15943d63f4de6f6', False),
    'notification_parent_is_protected(text,text,text)': ('7e00b3b39175cc9f01e4ce9d8763eab7', True),
    'guard_notification_parent()': ('7022f7ea6cee704464a284f87a5954ea', False),
    'read_notification_inbox(text,integer,text,text,jsonb)': ('efb5fc74a837a6ac60a03313ca9b4bea', True),
    'pending_notification_audits(integer)': ('8d2c769634b2404fe529a2b9f3dbcf8f', True),
    'ack_notification_audit(text,text)': ('f6d1ec7ab371ffd75a42c8502aac2dbd', True),
    'enqueue_studio_notification(text,text,text)': ('3bc0696eea9259160aaffab394a8cf4d', True),
    'enqueue_completed_notification(text,text)': ('4eb8139442398ac73a025741c3aaaac3', True),
    'pending_studio_notification_sources(integer)': ('dbf008b81b6f550b50d2a6706a66629d', True),
    'current_studio_notification_version(text)': ('a7134c5d8d15b5fb318f004a0ccadc71', True),
    'materialize_studio_notification(text,boolean)': ('3581f0e901671110f514a09def494a47', True),
    'current_completed_notification_target(text)': ('45d1a83759640e82c74e1028734750bf', True),
    'pending_completed_notification_sources(integer)': ('66d477d0698d437de1b90215733cf7a4', True),
    'materialize_completed_notification(text,boolean)': ('59ca4a51ca10c73df6dcf30225353f99', True),
    'current_account_notification_event(text)': ('2f96c3a5e44dcb90c7d7005b1b5db0f5', True),
    'enqueue_account_notification(text)': ('5ac7c47dc757ed8400a9f6e09b6e88c9', True),
    'pending_account_notification_sources(integer)': ('2ae45d30d049d071930bba50b7f913db', True),
    'current_account_notification_target(text)': ('d394cc40eb94a708582eb447202a31b7', True),
    'materialize_account_notification(text,boolean)': ('b01e83f39c85a84111c0c7a755f64703', True),
    'current_studio_notice_notification_target(text)': ('f1df67e5a5fd2c38ef444f2d3a2b35ac', True),
    'valid_studio_notice_notification_audit(public.notification_audit_outbox)': ('db6fdca1f817085296c712d34aa02312', True),
    'record_studio_notice_notification(text,text,text,text,text)': ('ea017ae6847b65e0323fd5bca2824073', True),
    'current_studio_notice_notification_audit(text)': ('4e8219663f5bbe9e67ae6c53a7462928', True),
    'capture_studio_notice_notification()': ('d241dd33f2e55a928baf12d5ffb59a11', True),
    'current_organization_role_notification_origin(text,text)': ('54d88e7a964e2056057159eac90999e2', False),
    'enqueue_organization_role_notification(text,text)': ('4d77ae99e30e2c4ced56c3760eddcb56', True),
    'pending_organization_role_notification_sources(integer)': ('8790cf07a7d5abccac45291745284dcc', True),
    'current_organization_role_notification_target(text)': ('d5347f968cb228da6098de8a0c27b7a2', True),
    'materialize_organization_role_notification(text,boolean)': ('8629841973bf9ea3f50433973f7ffaa6', True),
    'enqueue_identity_login_notification(text,text)': ('66c7996c00f2aede5e252ffc348c0c8c', True),
    'verify_identity_login_notification_source(text,text)': ('860093876c2ded3bbfdcf3e59e915bf1', True),
    'capture_identity_login_source()': ('edff04cf93eb52cb1518c3a5cbcd2477', True),
    'pending_identity_login_notification_sources(integer)': ('434d62b6b001247594dccd12be7bbd9b', True),
    'current_identity_login_notification_target(text)': ('17c8f3fbf31ee7d5d4928effe76da69e', True),
    'materialize_identity_login_notification(text,boolean)': ('ac983b2d0825b60fdba124a7decf1316', True),
    'enqueue_c9_role_notification(text,text)': ('3d6518c7828956ce83b0503b9a55f99b', True),
    'pending_c9_role_notification_sources(integer)': ('5a225c172f191d72a750745afb52ddf3', True),
    'current_c9_role_notification_target(text)': ('79d32bbc33dd42d29e0db53f6c86386e', True),
    'materialize_c9_role_notification(text,boolean)': ('5338e0fced89665e02557646d1d7ef2c', True),
    'current_egress_policy_notification_origin(text)': ('f7197bcb477ee481ba5eb6e9b6c6f87b', False),
    'enqueue_egress_policy_notification(text,text)': ('a4cf3ca6530a80709ecdf2e175c048ff', True),
    'pending_egress_policy_notification_sources(integer)': ('beda521303fdd2469284d26c7fce850b', True),
    'current_egress_policy_notification_target(text)': ('6dcca6a6c8854777ef6c75bd4a999f08', True),
    'materialize_egress_policy_notification(text,boolean)': ('53bb93f752f8a024468e28b62ac825a0', True),
    'source_access_notification_access(text,text)': ('63999ad4790fef4f01f5ee6ebfc9f587', True),
    'enqueue_source_access_notification(text,text,text,integer)': ('b55f7a86e90831caa5fb7f13c3e7bc5d', True),
    'pending_source_access_notification_sources(integer)': ('dab2b71f6e1d182a72dbeac98f7dd930', True),
    'current_source_access_notification_target(text)': ('ab763f3c2a7cfc8d23b1688a23717876', True),
    'materialize_source_access_notification(text,boolean)': ('206cefc5755af314b70ee8e58727dfb1', True),
    'current_terminal_recovery_notification_origin(text,text)': ('cc64092836a0204dd2f7c03c026aee59', False),
    'enqueue_restore_completed_notification(text,text)': ('0bc496cf2786b410e649366b1e6e66c1', True),
    'pending_restore_completed_notification_sources(integer)': ('b83c454338b37e2beb2390cc63c01f9e', True),
    'current_restore_completed_notification_target(text)': ('3738977457a3c1be764ea99ec7c9ea54', True),
    'materialize_restore_completed_notification(text,boolean)': ('1f7e4c0ff76d59fa31c60e50abbec0ad', True),
}
_PRIVATE_FUNCTIONS = frozenset({'current_studio_notification_version(text)','current_completed_notification_target(text)',
    'current_account_notification_event(text)','current_account_notification_target(text)',
    'current_studio_notice_notification_target(text)',
    'valid_studio_notice_notification_audit(public.notification_audit_outbox)','capture_studio_notice_notification()',
    'current_organization_role_notification_origin(text,text)',
    'current_egress_policy_notification_origin(text)',
    'enqueue_identity_login_notification(text,text)','capture_identity_login_source()',
    'current_terminal_recovery_notification_origin(text,text)',
    'guard_identity_password_change_event()','current_password_change_notification_event(text)',
    'current_password_reset_notification_event(text)',
    'source_access_notification_access(text,text)'})
_ORGANIZATION_ORIGIN_FUNCTION = 'current_organization_role_notification_origin(text,text)'
_STUDIO_NOTICE_FUNCTION_SHAPES = {
    'current_password_reset_notification_event(text)': ('jsonb', 0),
    'enqueue_password_reset_notification(text)': ('boolean', 0),
    'pending_password_reset_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_password_reset_notification_target(text)': ('jsonb', 0),
    'materialize_password_reset_notification(text,boolean)': ('jsonb', 0),
    'guard_identity_password_change_event()': ('trigger', 0),
    'current_password_change_notification_event(text)': ('jsonb', 0),
    'enqueue_password_change_notification(text)': ('boolean', 0),
    'pending_password_change_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_password_change_notification_target(text)': ('jsonb', 0),
    'materialize_password_change_notification(text,boolean)': ('jsonb', 0),
    'pending_notification_dispatch_scopes(integer,text,text,text)': ('record', 3),
    'lock_question_knowledge_selection(text,text,text,text,text,text)': ('boolean', 0),
    'current_studio_notice_notification_target(text)': ('jsonb', 0),
    'valid_studio_notice_notification_audit(public.notification_audit_outbox)': ('boolean', 0),
    'record_studio_notice_notification(text,text,text,text,text)': ('boolean', 1),
    'current_studio_notice_notification_audit(text)': ('jsonb', 0),
    'capture_studio_notice_notification()': ('trigger', 0),
    'current_organization_role_notification_origin(text,text)': ('jsonb', 0),
    'enqueue_organization_role_notification(text,text)': ('boolean', 0),
    'pending_organization_role_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_organization_role_notification_target(text)': ('jsonb', 0),
    'materialize_organization_role_notification(text,boolean)': ('jsonb', 0),
    'enqueue_identity_login_notification(text,text)': ('boolean', 0),
    'verify_identity_login_notification_source(text,text)': ('boolean', 0),
    'capture_identity_login_source()': ('trigger', 0),
    'pending_identity_login_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_identity_login_notification_target(text)': ('jsonb', 0),
    'materialize_identity_login_notification(text,boolean)': ('jsonb', 0),
    'enqueue_c9_role_notification(text,text)': ('boolean', 0),
    'pending_c9_role_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_c9_role_notification_target(text)': ('jsonb', 0),
    'materialize_c9_role_notification(text,boolean)': ('jsonb', 0),
    'enqueue_egress_policy_notification(text,text)': ('boolean', 0),
    'current_egress_policy_notification_origin(text)': ('jsonb', 0),
    'pending_egress_policy_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_egress_policy_notification_target(text)': ('jsonb', 0),
    'materialize_egress_policy_notification(text,boolean)': ('jsonb', 0),
    'enqueue_source_access_notification(text,text,text,integer)': ('boolean', 0),
    'source_access_notification_access(text,text)': ('boolean', 0),
    'pending_source_access_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_source_access_notification_target(text)': ('jsonb', 0),
    'materialize_source_access_notification(text,boolean)': ('jsonb', 0),
    'enqueue_restore_completed_notification(text,text)': ('boolean', 0),
    'current_terminal_recovery_notification_origin(text,text)': ('jsonb', 0),
    'pending_restore_completed_notification_sources(integer)': ('public.notification_source_outbox', 0),
    'current_restore_completed_notification_target(text)': ('jsonb', 0),
    'materialize_restore_completed_notification(text,boolean)': ('jsonb', 0),
}
_ID = " ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'"
_STORE = "source_store IN ('identity','authorization','organization','document','question','studio','recovery')"
_KIND = "kind IN ('authentication','device','membership','policy','run_state','run_failed','review','approval','delivery','warning','recovery')"
_SEVERITY = "severity IN ('info','warning','critical')"
_ACK = "(state='pending' AND delivered_at IS NULL AND version=1) OR (state IN ('delivered','suppressed') AND delivered_at IS NOT NULL AND version=2)"
_CHECKS = {
    'identity_password_change_events': [field + _ID for field in ('event_id','trace_id','policy_version')],
    'notification_source_outbox': [field + _ID for field in ('source_event_id','origin_event_id','action','target_type','target_id','notebook_id','trace_id','policy_version')]
        + [_STORE,_KIND,_SEVERITY,"state IN ('pending','delivered','suppressed')",'version>0',_ACK],
    'notification_details': [field + _ID for field in ('notification_id','origin_event_id','source_event_id','resource_type','resource_id','notebook_id','trace_id','policy_version')]
        + [_STORE,_KIND,_SEVERITY,r"length(title) BETWEEN 1 AND 160 AND title=btrim(title) AND title !~ '[<>\x01-\x1f]'",
            r"length(summary) BETWEEN 1 AND 500 AND summary=btrim(summary) AND summary !~ '[<>\x01-\x1f]'",
            r"length(deep_link) BETWEEN 1 AND 512 AND deep_link !~ '[\x01-\x20]' AND (deep_link ~ '^/(operations|inbox|notifications|workspaces)([/?:#]|$)' OR deep_link ~ '^/notebooks/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}[?](output_version_id|source_version_id|run_id|studio_job_id)=[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' OR deep_link = '/settings/account') AND deep_link !~ '^//'",
            "delivery_state IN ('pending','delivered','failed','suppressed')","(delivery_state='delivered')=(delivered_at IS NOT NULL)"],
    'notification_audit_outbox': ['event_id'+_ID,"action IN ('notification.created','notification.read')",
        "jsonb_typeof(draft)='object'","fingerprint ~ '^[0-9a-f]{64}$'","state IN ('pending','delivered')",'version>0',
        "(state='pending' AND delivered_at IS NULL AND version=1) OR (state='delivered' AND delivered_at IS NOT NULL AND version=2)"],
}

# 0069가 의존하는 다섯 표만 검사한다. 키 순서·참조 표/열·즉시 검증 의미까지 고정한다.
_KEYS = {
    'identity_password_change_events': [
        ('p',('event_id',),None,()), ('u',('source_session_id',),None,()),
        ('f',('tenant_id',),'identity_tenants',('tenant_id',))],
    'notification_source_outbox': [
        ('p',('tenant_id','workspace_id','source_event_id','recipient_id'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id')),
        ('f',('tenant_id','recipient_id'),'user_accounts',('tenant_id','user_id'))],
    'notification_details': [
        ('p',('tenant_id','notification_id'),None,()),
        ('f',('tenant_id','notification_id'),'notifications',('tenant_id','notification_id'))],
    'notification_audit_outbox': [
        ('p',('event_id',),None,()),
        ('u',('tenant_id','notification_id','action'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id'))],
    'notifications': [
        ('p',('tenant_id','notification_id'),None,()),
        ('u',('tenant_id','recipient_id','source_event_id'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id')),
        ('f',('tenant_id','recipient_id'),'user_accounts',('tenant_id','user_id'))],
    'idempotency_records': [
        ('p',('tenant_id','workspace_id','actor_id','operation','idempotency_key'),None,()),
        ('f',('tenant_id','workspace_id'),'workspaces',('tenant_id','workspace_id'))],
}
_COLUMNS = {
    'identity_password_change_events': ('event_id','tenant_id','user_id','source_session_id','occurred_at','trace_id','policy_version'),
    'notification_source_outbox': ('tenant_id','workspace_id','source_event_id','recipient_id','source_store',
        'origin_event_id','action','target_type','target_id','kind','severity','notebook_id','trace_id',
        'policy_version','occurred_at','state','delivered_at','version'),
    'notification_details': ('tenant_id','notification_id','source_store','origin_event_id','source_event_id',
        'kind','severity','title','summary','resource_type','resource_id','notebook_id','deep_link',
        'trace_id','policy_version','delivery_state','delivered_at'),
    'notification_audit_outbox': ('event_id','tenant_id','workspace_id','recipient_id','notification_id',
        'source_event_id','action','draft','fingerprint','state','delivered_at','version'),
    'notifications': ('tenant_id','workspace_id','notification_id','recipient_id','source_event_id',
        'read_at','version','created_at'),
    'idempotency_records': ('tenant_id','workspace_id','actor_id','operation','idempotency_key',
        'request_fingerprint','result','status','expires_at','created_at'),
}


def _normalize_expression(expression):
    expression = re.sub(r'::text\b', '', expression)
    expression = re.sub(r'=\s*ANY\s*\(\s*ARRAY\[(.*?)\]\s*\)', r' IN (\1)', expression, flags=re.S | re.I)
    expression = re.sub(r'(length\(\w+\)) BETWEEN (\d+) AND (\d+)', r'\1>=\2 AND \1<=\3', expression, flags=re.I)
    parts = re.split(r"('(?:''|[^'])*')", expression)
    return ''.join(part if index % 2 else re.sub(r'[\s()]', '', part).lower() for index, part in enumerate(parts))


def notification_storage_is_protected(connection) -> bool:
    """승인 후보의 owner·RLS·정확 CHECK/정책·함수 본문·실행 ACL을 읽기만 검증한다."""
    def values(row): return tuple(row.values()) if isinstance(row, dict) else tuple(row)
    try:
        roles = connection.execute("""SELECT count(*)=3 AND bool_and(NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
            AND NOT rolcreaterole AND NOT rolcreatedb) FROM pg_catalog.pg_roles
            WHERE rolname IN ('daon_notebook_delete','daon_account_owner','daon_organization_owner')""").fetchone()
        if not roles or not values(roles)[0]: return False
        if values(connection.execute("SELECT pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER') OR NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_app','MEMBER')").fetchone())[0]: return False
        # 승인된 기존 Job 표의 소유권/앱 최소 권한만 검사한다. 기존 RLS는 새로 켜거나 바꾸지 않는다.
        job = connection.execute("""SELECT c.relkind='r',c.relowner='daon_notebook_delete'::regrole,
            NOT c.relrowsecurity AND NOT c.relforcerowsecurity,
            pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT'),
            pg_catalog.has_table_privilege('daon_app',c.oid,'INSERT'),
            pg_catalog.has_table_privilege('daon_app',c.oid,'UPDATE'),
            NOT pg_catalog.has_table_privilege('daon_app',c.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER'),
            NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
              WHERE a.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole)
                OR a.grantee='daon_app'::regrole AND (a.is_grantable OR a.privilege_type NOT IN ('SELECT','INSERT','UPDATE'))),
            NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL)
            FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass('public.studio_generation_jobs')""").fetchone()
        if job is None or values(job)!=(True,)*9: return False
        for table, keys in _KEYS.items():
            if table in {'notifications','idempotency_records'}:
                row = connection.execute("""SELECT relowner='daon_app'::regrole AND relrowsecurity AND relforcerowsecurity
                    FROM pg_catalog.pg_class WHERE oid=pg_catalog.to_regclass(%s)""",('public.'+table,)).fetchone()
                if not row or not values(row)[0]: return False
            rows = connection.execute("""SELECT k.contype,
                ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(num,pos)
                    JOIN pg_catalog.pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=x.num ORDER BY x.pos),
                CASE WHEN k.contype='f' THEN rn.nspname END, r.relname,
                ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(num,pos)
                    JOIN pg_catalog.pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.num ORDER BY x.pos),
                k.convalidated,NOT k.condeferrable AND NOT k.condeferred,
                CASE WHEN k.contype='f' THEN k.confupdtype='a' AND k.confdeltype='a' AND k.confmatchtype='s'
                    ELSE EXISTS(SELECT 1 FROM pg_catalog.pg_index i WHERE i.indexrelid=k.conindid
                      AND i.indisunique AND i.indisvalid AND i.indisready AND i.indimmediate
                      AND i.indnkeyatts=cardinality(k.conkey) AND i.indpred IS NULL AND i.indexprs IS NULL) END
                FROM pg_catalog.pg_constraint k LEFT JOIN pg_catalog.pg_class r ON r.oid=k.confrelid
                LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=r.relnamespace
                WHERE k.conrelid=pg_catalog.to_regclass(%s) AND k.contype IN ('p','u','f')""",('public.'+table,)).fetchall()
            actual_keys = set()
            for row in rows:
                kind, columns, namespace, target, targets, validated, immediate, rules = values(row)
                if not (validated and immediate and rules) or (kind=='f' and namespace!='public'): return False
                actual_keys.add((kind,tuple(columns),target,tuple(targets)))
            if len(rows)!=len(keys) or actual_keys!=set(keys): return False
            rows = connection.execute("""SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,
                pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated
                FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=pg_catalog.to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped""",('public.'+table,)).fetchall()
            if len(rows)!=len(_COLUMNS[table]) or {values(r)[0] for r in rows}!=set(_COLUMNS[table]): return False
            for row in rows:
                name, kind, required, default, identity, generated = values(row)
                expected_kind = ('integer' if name=='version' else 'jsonb' if name in {'draft','result'}
                    else 'timestamp with time zone' if name in {'occurred_at','delivered_at','read_at','created_at','expires_at'} else 'text')
                expected_default = ('1' if name=='version' else "'pending'" if name=='state'
                    else 'now' if name=='created_at' else None)
                if (kind,required,identity,generated)!=(expected_kind,name not in {'notebook_id','delivered_at','read_at'},'',''): return False
                if (None if default is None else _normalize_expression(default))!=expected_default: return False
            if table in {'notifications','idempotency_records'}:
                expected_checks = ['version>0'] if table=='notifications' else ["status IN ('completed','failed')"]
                rows = connection.execute("SELECT pg_catalog.pg_get_expr(conbin,conrelid) FROM pg_catalog.pg_constraint WHERE conrelid=pg_catalog.to_regclass(%s) AND contype='c' AND convalidated",('public.'+table,)).fetchall()
                if sorted(_normalize_expression(values(r)[0]) for r in rows)!=sorted(map(_normalize_expression,expected_checks)): return False
                policy = table+'_scope'
                expression = "tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'')"
                rows = connection.execute("""SELECT polname,polcmd,polpermissive,polroles=ARRAY[0::oid],
                    pg_catalog.pg_get_expr(polqual,polrelid),pg_catalog.pg_get_expr(polwithcheck,polrelid)
                    FROM pg_catalog.pg_policy WHERE polrelid=pg_catalog.to_regclass(%s)""",('public.'+table,)).fetchall()
                if len(rows)!=1: return False
                name,command,permissive,public,using,check = values(rows[0])
                if (name,command,permissive,public)!=(policy,'*',True,True) or using is None or check is None: return False
                if _normalize_expression(using)!=_normalize_expression(expression) or _normalize_expression(check)!=_normalize_expression(expression): return False
        scope = "tenant_id=nullif(current_setting('app.tenant_id',true),'') AND workspace_id=nullif(current_setting('app.workspace_id',true),'') AND recipient_id=nullif(current_setting('app.actor_id',true),'')"
        policies = {
            'identity_password_change_events': [('identity_password_change_event_owner_scope','*','daon_app',
                "tenant_id=nullif(current_setting('app.tenant_id',true),'') AND user_id=nullif(current_setting('app.actor_id',true),'')")],
            'notification_source_outbox': [('notification_source_scope','*','daon_notebook_delete',scope),
                ('notification_source_dispatch_scope_read','r','daon_notebook_delete',
                 "current_user='daon_notebook_delete'::name AND current_setting('app.capability',true)='notification.dispatch.scopes'")],
            'notification_audit_outbox': [('notification_audit_scope','*','daon_notebook_delete',scope),
                ('notification_audit_dispatch_scope_read','r','daon_notebook_delete',
                 "current_user='daon_notebook_delete'::name AND current_setting('app.capability',true)='notification.dispatch.scopes'")],
            'notification_details': [('notification_detail_recipient','*','daon_app',"""tenant_id=nullif(current_setting('app.tenant_id',true),'') AND EXISTS(
              SELECT 1 FROM public.notifications n WHERE n.tenant_id=notification_details.tenant_id
              AND n.notification_id=notification_details.notification_id
              AND n.workspace_id=nullif(current_setting('app.workspace_id',true),'')
              AND n.recipient_id=nullif(current_setting('app.actor_id',true),''))"""),
              ('notification_detail_guard_scope','r','daon_notebook_delete',"""tenant_id=nullif(current_setting('app.tenant_id',true),'') AND EXISTS(
              SELECT 1 FROM public.notifications n WHERE n.tenant_id=notification_details.tenant_id
              AND n.notification_id=notification_details.notification_id
              AND n.workspace_id=nullif(current_setting('app.workspace_id',true),''))""")],
        }
        for table, checks in _CHECKS.items():
            password_table = table == 'identity_password_change_events'
            forbidden = ('UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER' if password_table
                else 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            row = connection.execute("""SELECT c.relowner='daon_notebook_delete'::regrole AND c.relrowsecurity AND c.relforcerowsecurity
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                  WHERE a.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole) OR a.is_grantable)
                AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,%s)
                AND (NOT %s OR (pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT')
                    AND pg_catalog.has_table_privilege('daon_app',c.oid,'INSERT')))
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=c.oid AND attacl IS NOT NULL)
                FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(%s)""", (forbidden,password_table,'public.'+table)).fetchone()
            if not row or not values(row)[0]: return False
            definitions = connection.execute("SELECT pg_catalog.pg_get_expr(conbin,conrelid) FROM pg_catalog.pg_constraint WHERE conrelid=pg_catalog.to_regclass(%s) AND contype='c' AND convalidated",('public.'+table,)).fetchall()
            if sorted(_normalize_expression(values(r)[0]) for r in definitions) != sorted(map(_normalize_expression,checks)): return False
            rows = connection.execute("SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid=pg_catalog.to_regclass(%s)",('public.'+table,)).fetchone()
            if values(rows)[0]!=len(policies[table]): return False
            for name, command, role, expression in policies[table]:
                row = connection.execute("""SELECT polcmd,polpermissive,polroles=ARRAY[%s::regrole::oid],pg_catalog.pg_get_expr(polqual,polrelid),pg_catalog.pg_get_expr(polwithcheck,polrelid)
                    FROM pg_catalog.pg_policy WHERE polrelid=pg_catalog.to_regclass(%s) AND polname=%s""",(role,'public.'+table,name)).fetchone()
                if row is None: return False
                actual=values(row)
                # pg_get_expr는 public. 한정을 정규화한다.
                actual_expression=actual[3].replace('public.notifications','notifications')
                expected_expression=expression.replace('public.notifications','notifications')
                if actual[:3]!=(command,True,True) or _normalize_expression(actual_expression)!=_normalize_expression(expected_expression): return False
                if password_table:
                    if actual[4] is None or _normalize_expression(actual[4])!=_normalize_expression(expected_expression): return False
                elif actual[4] is not None: return False
        for signature, (digest, definer) in _FUNCTIONS.items():
            # 신규 private INVOKER 함수 하나만 기존 조직 owner를 유지한다.
            owner = 'daon_organization_owner' if signature == _ORGANIZATION_ORIGIN_FUNCTION else 'daon_notebook_delete'
            allowed_roles = [owner, 'daon_notebook_delete'] if signature == _ORGANIZATION_ORIGIN_FUNCTION else ['daon_app', owner]
            expected_config = ['search_path=pg_catalog, pg_temp']
            if signature in {'lock_question_knowledge_selection(text,text,text,text,text,text)',
                'pending_notification_dispatch_scopes(integer,text,text,text)'}:
                expected_config.append('row_security=on')
            row = connection.execute("""SELECT md5(p.prosrc),p.prosecdef,p.proowner=%s::regrole,
                p.proconfig=%s::text[],
                NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
                  WHERE a.grantee<>ALL(%s::regrole[]) OR a.is_grantable),
                pg_catalog.has_function_privilege('daon_app',p.oid,'EXECUTE')
                FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(%s)""",(owner,expected_config,allowed_roles,'public.'+signature)).fetchone()
            if row is None or values(row)!=(digest,definer,True,True,True,definer and signature not in _PRIVATE_FUNCTIONS): return False
            if signature == _ORGANIZATION_ORIGIN_FUNCTION:
                private = connection.execute("""SELECT
                    pg_catalog.has_function_privilege('daon_notebook_delete',p.oid,'EXECUTE'),
                    NOT pg_catalog.pg_has_role('daon_app','daon_organization_owner','MEMBER'),
                    NOT pg_catalog.pg_has_role('daon_notebook_delete','daon_organization_owner','MEMBER'),
                    NOT r.rolinherit
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
                    WHERE p.oid=pg_catalog.to_regprocedure(%s)""",('public.'+signature,)).fetchone()
                if private is None or values(private)!=(True,)*4: return False
            if signature in _STUDIO_NOTICE_FUNCTION_SHAPES:
                result_type, defaults = _STUDIO_NOTICE_FUNCTION_SHAPES[signature]
                shape = connection.execute("""SELECT p.prorettype=pg_catalog.to_regtype(%s)
                    AND p.proretset=%s AND p.pronargdefaults=%s AND p.prokind='f'
                    AND p.provolatile='v' AND p.proparallel='u' AND NOT p.proisstrict AND NOT p.proleakproof
                    AND p.prolang=(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql')
                    FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(%s)""",
                    (result_type,signature in {'pending_organization_role_notification_sources(integer)',
                        'pending_identity_login_notification_sources(integer)',
                        'pending_c9_role_notification_sources(integer)',
                        'pending_egress_policy_notification_sources(integer)',
                        'pending_source_access_notification_sources(integer)',
                        'pending_restore_completed_notification_sources(integer)',
                        'pending_notification_dispatch_scopes(integer,text,text,text)',
                        'pending_password_change_notification_sources(integer)',
                        'pending_password_reset_notification_sources(integer)'},
                     defaults,'public.'+signature)).fetchone()
                if shape is None or values(shape)!=(True,): return False
            if signature == 'pending_notification_dispatch_scopes(integer,text,text,text)':
                shape = connection.execute("""SELECT p.proallargtypes=ARRAY['integer'::regtype,'text'::regtype,
                    'text'::regtype,'text'::regtype,'text'::regtype,'text'::regtype,'text'::regtype]::oid[]
                    AND p.proargmodes=ARRAY['i','i','i','i','t','t','t']::"char"[]
                    AND p.proargnames=ARRAY['p_limit','p_after_tenant','p_after_workspace','p_after_recipient',
                        'tenant_id','workspace_id','recipient_id']
                    AND pg_catalog.pg_get_expr(p.proargdefaults,0)='NULL::text, NULL::text, NULL::text'
                    FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(%s)""",('public.'+signature,)).fetchone()
                if shape is None or values(shape)!=(True,): return False
        # 승인된 추가 SELECT 외 기존 S24 수신자 정책/소유권/앱 쓰기 경계는 유지한다.
        notice = connection.execute("""SELECT c.relowner='postgres'::regrole AND c.relrowsecurity AND c.relforcerowsecurity
            AND pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT')
            AND pg_catalog.has_table_privilege('daon_app',c.oid,'INSERT')
            AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'UPDATE')
            AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'DELETE')
            FROM pg_catalog.pg_class c WHERE c.oid='public.studio_approval_notices'::regclass""").fetchone()
        if notice is None or values(notice)!=(True,): return False
        notice_policies = connection.execute("""SELECT polname,polcmd,polroles,pg_catalog.pg_get_expr(polqual,polrelid),
            pg_catalog.pg_get_expr(polwithcheck,polrelid),polpermissive FROM pg_catalog.pg_policy
            WHERE polrelid='public.studio_approval_notices'::regclass ORDER BY polname""").fetchall()
        if len(notice_policies)!=2: return False
        expected_notice_policies = {
            'studio_approval_notice_recipient': ('*','daon_app',scope,scope),
            'studio_notice_dispatch_scope_read': ('r','daon_notebook_delete',
                "current_user='daon_notebook_delete'::name AND current_setting('app.capability',true)='notification.dispatch.scopes'",None),
        }
        for row in notice_policies:
            name,command,roles,expression,check,permissive=values(row)
            if name not in expected_notice_policies or not permissive: return False
            expected_command,role,expected_using,expected_check=expected_notice_policies[name]
            role_oid=connection.execute('SELECT %s::regrole::oid',(role,)).fetchone()
            if command!=expected_command or role_oid is None or roles!=[values(role_oid)[0]]: return False
            if expression is None or _normalize_expression(expression)!=_normalize_expression(expected_using): return False
            if (None if check is None else _normalize_expression(check))!=(None if expected_check is None else _normalize_expression(expected_check)): return False
        expected_triggers = {
            ('studio_generation_jobs','studio_generation_jobs_guard','guard_studio_generation_job',23),
            ('notification_details','notification_details_guard','guard_notification_storage',31),
            ('notification_details','notification_details_created','guard_notification_storage',5),
            ('notification_source_outbox','notification_source_guard','guard_notification_storage',31),
            ('notification_audit_outbox','notification_audit_guard','guard_notification_storage',31),
            ('notifications','notification_parent_read_guard','guard_notification_parent',27),
            ('studio_approval_notices','studio_notice_notification_created','capture_studio_notice_notification',5),
            ('identity_sessions','identity_login_source_insert','capture_identity_login_source',5),
            ('identity_password_change_events','identity_password_change_event_guard','guard_identity_password_change_event',31),
        }
        rows = connection.execute("""SELECT c.relname,t.tgname,p.proname,t.tgtype FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgname=ANY(%s) AND t.tgrelid=ANY(%s::regclass[])
              AND c.relnamespace='public'::regnamespace
              AND t.tgfoid=pg_catalog.to_regprocedure('public.'||p.proname||'()')
              AND p.pronamespace='public'::regnamespace AND p.pronargs=0
              AND p.prorettype='pg_catalog.trigger'::regtype
              AND t.tgqual IS NULL AND t.tgattr=''::int2vector
              AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')""",
            ([r[1] for r in expected_triggers],sorted({'public.'+r[0] for r in expected_triggers}))).fetchall()
        return {values(r) for r in rows}==expected_triggers
    except (Error,TypeError,ValueError,KeyError):
        return False
