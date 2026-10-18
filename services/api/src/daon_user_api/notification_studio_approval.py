"""S24 notice의 단일 원장을 S42 계약으로 읽는다. 알림 사본/GET 쓰기는 없다."""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import replace

from psycopg import Error
from psycopg.pq import TransactionStatus

from .audit import ActorType
from .cloud_storage import CloudAccessContext, CloudDatabaseError
from .notification import DeliveryState, Notification, NotificationError, ReadTransition, _checked_id, _checked_time
from .notification_events import notebook_notification_link
from .notification_postgres import PostgresNotificationRepository, _time, serialize_notification_audit
from .studio_workspace import StudioContext, StudioError


_TEXT = {
    'due_24h': ('승인 기한 임박', '승인 요청 기한이 24시간 이내입니다.', 'warning'),
    'expired': ('승인 요청 만료', '승인 요청의 처리 기한이 지났습니다.', 'warning'),
    'withdrawn': ('승인 요청 회수', '본인이 승인 요청을 회수했습니다.', 'info'),
}
_SELECT = """
SELECT n.notification_id,n.approval_request_id,r.output_version_id,n.event_kind,
  n.created_at,n.read_at,n.version,r.state,
  public.studio_approval_deadline(r.canonical_json,r.created_at),n.scheduled_at,
  ARRAY(SELECT DISTINCT nb.notebook_id FROM public.notebooks nb
    JOIN public.notebook_bindings b ON b.tenant_id=nb.tenant_id AND b.workspace_id=nb.workspace_id
      AND b.notebook_id=nb.notebook_id AND b.binding_kind='output_version'
      AND b.record_id=r.output_version_id
    WHERE nb.tenant_id=n.tenant_id AND nb.workspace_id=n.workspace_id AND nb.created_by=n.recipient_id
      AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE d.tenant_id=nb.tenant_id
        AND d.workspace_id=nb.workspace_id AND d.notebook_id=nb.notebook_id
        AND d.state IN ('accepted','deleting','completed')))
FROM public.studio_approval_notices n JOIN public.approval_requests r
  ON r.tenant_id=n.tenant_id AND r.workspace_id=n.workspace_id AND r.record_id=n.approval_request_id
WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s AND r.created_by=n.recipient_id
"""


class StudioApprovalNotificationRepository:
    """기존 승인 notice와 저장된 projection 감사만 읽고, 읽음은 원 메서드에 위임한다."""
    transactional_audit = True
    encode_cursor = PostgresNotificationRepository.encode_cursor
    decode_cursor = PostgresNotificationRepository.decode_cursor

    def __init__(self, lifecycle_repository, *, context):
        if (not isinstance(context, CloudAccessContext) or context.capability != 'view'
            or not getattr(lifecycle_repository._store, '_notification_inbox_enabled', False)):
            raise ValueError('NOTIFICATION_SCOPE_INVALID')
        self.context, self.lifecycle = context, lifecycle_repository

    def _scope(self, tenant_id, recipient_id):
        if (tenant_id, recipient_id) != (self.context.tenant_id, self.context.actor_id):
            raise NotificationError('RESOURCE_UNAVAILABLE', 404)
        return tenant_id, self.context.workspace_id, recipient_id

    @contextmanager
    def _transaction(self):
        try:
            with self.lifecycle._store._transaction(self.context) as connection:
                yield connection
        except (CloudDatabaseError, Error):
            raise NotificationError('DATABASE_UNAVAILABLE', 503) from None

    @staticmethod
    def _notebook_for(row):
        if row is None:
            return None
        if row[3] not in _TEXT:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        # 원 요청에 없는 첫/마지막 Notebook을 추정하지 않는다.
        notebooks = row[10]
        if len(notebooks) != 1:
            return None
        return _checked_id(notebooks[0])

    @staticmethod
    @contextmanager
    def _notebook_context(connection, notebook):
        previous = connection.execute("SELECT current_setting('app.notebook_id',true)").fetchone()[0]
        try:
            connection.execute("SELECT set_config('app.notebook_id',%s,true)", (notebook,))
            yield
        finally:
            if connection.info.transaction_status != TransactionStatus.INERROR:
                connection.execute("SELECT set_config('app.notebook_id',%s,true)", (previous or '',))

    def _current(self, connection, row):
        notebook = self._notebook_for(row)
        if notebook is None:
            return None
        with self._notebook_context(connection, notebook):
            return self._current_in_notebook(connection, row, notebook)

    def _current_access(self, connection, row, notebook, *, trace_id, policy_version):
        """저장된 projection 유무와 독립적인 기존 본인·자료·기한 검사다."""
        _notice, _request, version, event, _created, _read, _revision, state, deadline, scheduled, _notebooks = row
        active = connection.execute("SELECT 1 FROM public.identity_memberships m "
            "JOIN public.identity_users u ON u.user_id=m.user_id AND u.state='active' "
            "JOIN public.user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
            "JOIN public.identity_auth_workspaces w ON w.tenant_id=m.tenant_id "
            "WHERE m.tenant_id=%s AND m.user_id=%s AND w.workspace_id=%s "
            "AND NOT EXISTS(SELECT 1 FROM public.identity_org_tenant_memberships o WHERE o.tenant_id=m.tenant_id "
            "AND o.user_id=m.user_id AND w.workspace_kind='organization' AND o.state<>'active')",
            (self.context.tenant_id, self.context.actor_id, self.context.workspace_id)).fetchone()
        if active is None:
            return False
        context = StudioContext(self.context.tenant_id, self.context.workspace_id, self.context.actor_id,
            trace_id, policy_version, notebook)
        try:
            from .authorization import Action
            self.lifecycle._version_access(connection, context, version, Action.VIEW)
        except StudioError as error:
            cause, seen = error, set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, Error):
                    raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
                cause = cause.__cause__ or cause.__context__
            if connection.info.transaction_status == TransactionStatus.INERROR or error.status >= 500:
                raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
            if error.status == 404 or error.code == 'NOTEBOOK_SCOPE_MISMATCH':
                return False
            raise
        now = connection.execute('SELECT clock_timestamp()').fetchone()[0]
        if (event == 'due_24h' and (state != 'pending' or now >= deadline or now < scheduled)
            or event == 'expired' and state != 'expired' or event == 'withdrawn' and state != 'withdrawn'):
            return False
        return True

    @staticmethod
    def _stored_projection(connection, notice, created):
        projection = connection.execute('SELECT public.current_studio_notice_notification_audit(%s)', (notice,)).fetchone()[0]
        if projection is None:
            return None
        if not isinstance(projection, dict) or set(projection) != {'audit_event_id','trace_id','policy_version','projection_at'}:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        for key in ('trace_id', 'policy_version', 'audit_event_id'):
            _checked_id(projection[key])
        delivered_at, created_at = _time(projection['projection_at']), _time(created)
        if delivered_at is None or created_at is None or delivered_at < created_at:
            raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
        return projection

    def _current_in_notebook(self, connection, row, notebook):
        notice, _request, version, event, created, read, revision, _state, _deadline, _scheduled, _notebooks = row
        projection = self._stored_projection(connection, notice, created)
        if projection is None:
            return None  # GET은 구 notice의 projection을 생성하지 않는다.
        trace, policy, audit_id = (projection[key] for key in ('trace_id', 'policy_version', 'audit_event_id'))
        if not self._current_access(connection, row, notebook, trace_id=trace, policy_version=policy):
            return None
        title, summary, severity = _TEXT[event]
        return Notification(notification_id=_checked_id(notice), tenant_id=self.context.tenant_id,
            workspace_id=self.context.workspace_id, recipient_id=self.context.actor_id,
            kind='approval', severity=severity, title=title, summary=summary, source_event_id=notice,
            resource_type='StudioApprovalNotice', resource_id=notice,
            deep_link=notebook_notification_link(notebook, version), audit_event_id=audit_id, trace_id=trace,
            delivery_state=DeliveryState.DELIVERED, created_at=_time(created), delivered_at=_time(projection['projection_at']),
            read_at=_time(read), version=int(revision))

    def _prepare_projection(self, connection, row, *, trace_id, policy_version):
        notebook = self._notebook_for(row)
        if notebook is None:
            return 'unavailable'
        with self._notebook_context(connection, notebook):
            if not self._current_access(connection, row, notebook, trace_id=trace_id, policy_version=policy_version):
                return 'unavailable'
            notice, created = _checked_id(row[0]), row[4]
            if self._stored_projection(connection, notice, created) is not None:
                return 'already'
            recorded = connection.execute(
                "SELECT public.record_studio_notice_notification(%s,'notification.created',%s,%s,NULL)",
                (notice, trace_id, policy_version)).fetchone()
            if recorded is None or type(recorded[0]) is not bool:
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            projection = self._stored_projection(connection, notice, created)
            if projection is None:
                # SQL 검사 사이의 현재 접근 철회도 새 의도와 함께 savepoint에서
                # rollback한다. false/유실을 발행 성공으로 세지 않는다.
                raise NotificationError('RESOURCE_UNAVAILABLE', 404)
            if not recorded[0]:
                return 'already'  # 다른 준비자가 먼저 확정한 동일한 실제 저장값이다.
            if (projection['trace_id'], projection['policy_version']) != (trace_id, policy_version):
                raise NotificationError('DATABASE_RESPONSE_INVALID', 503)
            return 'prepared'

    def prepare_projections(self, *, limit=100, after=None, trace_id, policy_version):
        """현재 exact Scope의 ID 배치만 준비한다. GET/전역 탐색/자동 실행은 없다.

        next_cursor는 처리한 마지막 notice ID이고 eof일 때만 None이다. 호출자는
        다음 sweep에서 None으로 wrap하여 새로 commit된 낮은 ID와 실패 건을 다시
        검사한다. 접근 불가/실패도 cursor는 전진하므로 뒤 정상 건을 막지 않는다.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise NotificationError('INVALID_INPUT', 400)
        trace_id, policy_version = _checked_id(trace_id), _checked_id(policy_version)
        scope = (self.context.tenant_id, self.context.workspace_id, self.context.actor_id)
        query, params = _SELECT, scope
        if after is not None:
            query += ' AND n.notification_id>%s'
            params += (_checked_id(after),)
        query += ' ORDER BY n.notification_id LIMIT %s'
        with self._transaction() as connection:
            rows = connection.execute(query, (*params, limit + 1)).fetchall()
            batch = rows[:limit]
            eof = len(rows) <= limit
            result = dict(scanned=len(batch), prepared=0, already=0, unavailable=0, failed=0,
                next_cursor=None if eof else _checked_id(batch[-1][0]), eof=eof)
            for row in batch:
                try:
                    # cloud transaction 안의 건별 savepoint다. 실패한 원본 건만
                    # rollback하며 이미 준비한 다른 건의 확정을 보상 DELETE하지 않는다.
                    with connection.transaction():
                        outcome = self._prepare_projection(connection, row,
                            trace_id=trace_id, policy_version=policy_version)
                except NotificationError as error:
                    outcome = 'unavailable' if error.http_status in (403, 404) else 'failed'
                except StudioError as error:
                    outcome = 'unavailable' if error.status in (403, 404) else 'failed'
                except (Error, CloudDatabaseError):
                    outcome = 'failed'
                result[outcome] += 1
        return result  # outer commit 실패 시 성공 집계를 반환하지 않는다.

    def notifications_for(self, *, tenant_id, recipient_id):
        scope = self._scope(tenant_id, recipient_id)
        with self._transaction() as connection:
            rows = connection.execute(_SELECT + ' ORDER BY n.created_at DESC,n.notification_id DESC', scope).fetchall()
            return tuple(item for row in rows if (item := self._current(connection, row)) is not None)

    def notification_for(self, *, tenant_id, recipient_id, notification_id):
        scope = self._scope(tenant_id, recipient_id)
        with self._transaction() as connection:
            row = connection.execute(_SELECT + ' AND n.notification_id=%s', (*scope, _checked_id(notification_id))).fetchone()
            return self._current(connection, row)

    def transition_read(self, *, tenant_id, recipient_id, notification_id, expected_etag, idempotency_key,
                        fingerprint, read_at, on_first_transition, audit_draft_factory=None):
        scope = self._scope(tenant_id, recipient_id)
        match = re.fullmatch(r'"notification-([1-9][0-9]{0,9})"', expected_etag or '')
        if match is None or int(match[1]) > 2**31-1:
            raise NotificationError('VERSION_CONFLICT', 412)
        if audit_draft_factory is None:
            raise NotificationError('NOTIFICATION_AUDIT_REQUIRED', 503)
        with self._transaction() as connection:
            row = connection.execute(_SELECT + ' AND n.notification_id=%s', (*scope, _checked_id(notification_id))).fetchone()
            item = self._current(connection, row)
            if item is None:
                raise NotificationError('RESOURCE_UNAVAILABLE', 404)
            notebook = row[10][0]
        before = replace(item, version=int(match[1]), read_at=None)
        after = replace(before, version=before.version+1, read_at=_checked_time(read_at))
        draft = audit_draft_factory(before, after)
        serialize_notification_audit(draft)
        if (draft.actor_id, draft.actor_type, draft.tenant_id, draft.workspace_id, draft.action,
            draft.target_type, draft.target_id) != (self.context.actor_id, ActorType.USER, self.context.tenant_id,
                self.context.workspace_id, 'notification.read', 'notification', item.notification_id):
            raise NotificationError('NOTIFICATION_AUDIT_REQUIRED', 503)
        context = StudioContext(self.context.tenant_id, self.context.workspace_id, self.context.actor_id,
            draft.trace_id, draft.policy_version, notebook)
        try:
            result, replayed = self.lifecycle.mark_notice_read(context, notification_id, int(match[1]), idempotency_key)
        except (CloudDatabaseError, Error):
            raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
        except StudioError as error:
            cause, seen = error, set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, Error):
                    raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
                cause = cause.__cause__ or cause.__context__
            if error.status >= 500:
                raise NotificationError('DATABASE_UNAVAILABLE', 503) from None
            if error.status == 404 or error.code == 'NOTEBOOK_SCOPE_MISMATCH':
                raise NotificationError('RESOURCE_UNAVAILABLE', 404) from None
            if error.status == 412:
                raise NotificationError('VERSION_CONFLICT', 412) from None
            if error.code in {'IDEMPOTENCY_KEY_REUSED', 'IDEMPOTENCY_CONFLICT'}:
                raise NotificationError('IDEMPOTENCY_CONFLICT', 409) from None
            raise
        return ReadTransition(replace(item, version=int(result['version']), read_at=_time(result['read_at'])), not replayed)
