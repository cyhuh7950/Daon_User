"""S24 approval deadlines and scoped durable notices; no external delivery."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from .authorization import Action, Role
from .cloud_storage import CloudAccessContext
from .data_canon import canonical_json_bytes
from .open_law_source_access import _current_auth_grant, OpenLawAccessError
from .studio_workspace import StudioContext, StudioError, _identifier


def _ttl(value):
    if type(value) is not int or not 1 <= value <= 30:
        raise StudioError("STUDIO_INPUT_INVALID")
    return value


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StudioError("STUDIO_APPROVAL_DEADLINE_INVALID", 409)
    return value.astimezone(timezone.utc)


def _stamp(value):
    return _utc(value).isoformat().replace("+00:00", "Z")


def freeze_deadline(db_now, ttl_days=7, policy_version=0):
    _ttl(ttl_days)
    if type(policy_version) is not int or policy_version < 0:
        raise StudioError("STUDIO_INPUT_INVALID")
    expiry = _utc(db_now) + timedelta(days=ttl_days)
    return {"ttl_days": ttl_days, "ttl_policy_version": policy_version,
            "expires_at": _stamp(expiry), "remind_at": _stamp(expiry - timedelta(days=1))}


def request_deadline(payload, created_at):
    fields = {"ttl_days", "ttl_policy_version", "expires_at", "remind_at"}
    if not fields.intersection(payload):
        expiry = _utc(created_at) + timedelta(days=7)
        return expiry, expiry - timedelta(days=1)
    try:
        expected = freeze_deadline(created_at, payload["ttl_days"], payload["ttl_policy_version"])
        expires = _utc(datetime.fromisoformat(payload["expires_at"]))
        remind = _utc(datetime.fromisoformat(payload["remind_at"]))
        if _stamp(expires) != expected["expires_at"] or _stamp(remind) != expected["remind_at"]:
            raise ValueError
        return expires, remind
    except (KeyError, TypeError, ValueError, StudioError):
        raise StudioError("STUDIO_APPROVAL_DEADLINE_INVALID", 409) from None


def notice_due(state, expires_at, db_now):
    if state != "pending":
        return None
    expiry, now = _utc(expires_at), _utc(db_now)
    if now >= expiry:
        return "expired", expiry
    remind = expiry - timedelta(days=1)
    return ("due_24h", remind) if now >= remind else None


class ApprovalExpired(Exception):
    """Only raised after a successful expiry transition, before any approval write."""


@contextmanager
def approval_transaction(store, cloud_context, *, expiry_request_id=None):
    expired = False
    with store._transaction(cloud_context) as connection:
        try:
            # Only an approval write needs this savepoint. A deadline crossed
            # during INSERT/transition must not retain a half-written approval.
            with connection.transaction() if expiry_request_id is not None else nullcontext():
                try:
                    yield connection
                except ApprovalExpired:
                    expired = True
        except Exception as error:
            if (expiry_request_id is None or getattr(error, "sqlstate", None) != "23514"
                    or getattr(getattr(error, "diag", None), "message_primary", None) != "APPROVAL_REQUEST_EXPIRED"):
                raise
            changed = connection.execute("SELECT expire_studio_approval_request(%s)", (expiry_request_id,)).fetchone()
            if changed is None or changed[0] is not True:
                raise
            expired = True
    if expired:
        raise StudioError("APPROVAL_REQUEST_EXPIRED", 409)


def lock_approval_version(connection, context, version_id):
    _identifier(version_id)
    if not context.notebook_id:
        raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
    denied = connection.execute(
        "SELECT 1 FROM evidence_references er JOIN identity_auth_source_access a "
        "ON a.tenant_id=er.tenant_id AND a.workspace_id=er.workspace_id AND a.source_version_id=er.source_version_id "
        "WHERE er.tenant_id=%s AND er.workspace_id=%s AND er.output_version_id=%s AND a.user_id=%s AND a.allowed=0 LIMIT 1",
        (context.tenant_id, context.workspace_id, version_id, context.actor_id),
    ).fetchone()
    if denied is not None:
        raise StudioError("RESOURCE_UNAVAILABLE", 404)
    row = connection.execute("SELECT lock_studio_approval_version(%s,%s)",
                             (version_id, context.notebook_id)).fetchone()
    if row is None or row[0] is not True:
        raise StudioError("RESOURCE_UNAVAILABLE", 404)


def require_pending(connection, context, request_id, version_id):
    """Must precede approval INSERT, under approval_transaction's commit boundary."""
    lock_approval_version(connection, context, version_id)
    row = connection.execute(
        "SELECT state,version,studio_approval_deadline(canonical_json,created_at),clock_timestamp() "
        "FROM approval_requests WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND output_version_id=%s",
        (context.tenant_id, context.workspace_id, request_id, version_id),
    ).fetchone()
    if row is None or row[0] != "pending":
        raise StudioError("APPROVAL_REQUEST_REQUIRED", 409)
    if row[3] >= row[2]:
        connection.execute("SELECT expire_studio_approval_request(%s)", (request_id,))
        raise ApprovalExpired()
    return int(row[1])


def new_request_deadline(connection, context, version_id):
    lock_approval_version(connection, context, version_id)
    pending = connection.execute(
        "SELECT record_id FROM approval_requests WHERE tenant_id=%s AND workspace_id=%s AND output_version_id=%s AND state='pending'",
        (context.tenant_id, context.workspace_id, version_id),
    ).fetchall()
    for row in pending:
        expired = connection.execute("SELECT expire_studio_approval_request(%s)", (row[0],)).fetchone()
        if not expired or not expired[0]:
            raise StudioError("APPROVAL_REQUEST_PENDING", 409)
    policy = connection.execute(
        "SELECT ttl_days,policy_version FROM studio_approval_policy_versions WHERE tenant_id=%s ORDER BY policy_version DESC LIMIT 1",
        (context.tenant_id,),
    ).fetchone()
    db_now = connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    return freeze_deadline(db_now, int(policy[0]) if policy else 7, int(policy[1]) if policy else 0)


class StudioApprovalLifecycleRepository:
    def __init__(self, cloud_store):
        self._store = cloud_store

    @staticmethod
    def _cloud(context, capability):
        return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, capability)

    def _notification_context(self, connection, *, trace_id, policy_version, notebook_id=None):
        # OFF/0068 호출은 기존 SQL 경로를 유지한다. 설정은 현재 cloud
        # transaction에만 속하며 pool의 다음 요청으로 전달하지 않는다.
        if not getattr(self._store, "_notification_inbox_enabled", False):
            return
        for name, value in (("app.notification_trace_id", trace_id),
                            ("app.notification_policy_version", policy_version)):
            _identifier(value)
            connection.execute("SELECT set_config(%s,%s,true)", (name, value))
        if notebook_id is not None:
            _identifier(notebook_id)
            connection.execute("SELECT set_config('app.notebook_id',%s,true)", (notebook_id,))

    def _notification_identity_access(self, connection, context):
        if not getattr(self._store, "_notification_inbox_enabled", False):
            return
        row = connection.execute(
            "SELECT 1 FROM identity_memberships m JOIN identity_users u ON u.user_id=m.user_id AND u.state='active' "
            "JOIN user_accounts a ON a.tenant_id=m.tenant_id AND a.user_id=m.user_id AND a.status='active' "
            "JOIN identity_auth_workspaces w ON w.tenant_id=m.tenant_id AND w.workspace_id=%s "
            "WHERE m.tenant_id=%s AND m.user_id=%s AND NOT EXISTS(SELECT 1 FROM identity_org_tenant_memberships o "
            "WHERE o.tenant_id=m.tenant_id AND o.user_id=m.user_id AND w.workspace_kind='organization' AND o.state<>'active')",
            (context.workspace_id, context.tenant_id, context.actor_id),
        ).fetchone()
        if row is None:
            raise StudioError("RESOURCE_UNAVAILABLE", 404)

    @staticmethod
    def _grant(connection, context, action):
        try:
            return _current_auth_grant(context, connection, action)
        except OpenLawAccessError:
            raise StudioError("RESOURCE_UNAVAILABLE", 404) from None

    @staticmethod
    def _policy_target(context):
        return "studio-approval-policy-" + hashlib.sha256(json.dumps(
            [context.tenant_id, context.workspace_id], separators=(",", ":")).encode()).hexdigest()

    def get_policy(self, context):
        with self._store._transaction(self._cloud(context, "studio.approval.policy.read")) as connection:
            self._grant(connection, context, Action.VIEW)
            can_manage = False
            try:
                grant = self._grant(connection, context, Action.POLICY_MANAGE)
                can_manage = grant.role in {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN}
            except StudioError:
                pass
            row = connection.execute("SELECT ttl_days,policy_version FROM studio_approval_policy_versions WHERE tenant_id=%s ORDER BY policy_version DESC LIMIT 1", (context.tenant_id,)).fetchone()
            return {"ttl_days": int(row[0]) if row else 7, "policy_version": int(row[1]) if row else 0,
                    "can_manage": can_manage, "step_up_target_id": self._policy_target(context)}

    def _replay(self, connection, context, operation, key, payload):
        from .studio_workspace_postgres import PostgresStudioWorkspaceRepository
        _identifier(key)
        if not 16 <= len(key) <= 128:
            raise StudioError("STUDIO_INPUT_INVALID")
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                           ("|".join((context.tenant_id, context.workspace_id, context.actor_id, operation, key)),))
        fingerprint = hashlib.sha256(canonical_json_bytes({"request": payload})).hexdigest()
        repo = PostgresStudioWorkspaceRepository(self._store)
        return repo, fingerprint, repo._replay(connection, context, operation, key, fingerprint)

    def save_policy(self, context, ttl_days, expected_policy_version, key):
        _ttl(ttl_days)
        if type(expected_policy_version) is not int or expected_policy_version < 0:
            raise StudioError("STUDIO_INPUT_INVALID")
        operation = "studio.approval.policy.save"
        with self._store._transaction(self._cloud(context, operation)) as connection:
            grant = self._grant(connection, context, Action.POLICY_MANAGE)
            if grant.role not in {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN}:
                raise StudioError("RESOURCE_UNAVAILABLE", 404)
            # The HTTP layer consumes the normal current-session Step-up first.
            # This receipt check does not create or impersonate a session.
            proof = connection.execute(
                "SELECT 1 FROM identity_step_up_consumptions c JOIN identity_step_up_authorizations a ON a.step_up_id=c.step_up_id "
                "WHERE c.tenant_id=%s AND c.actor_id=%s AND c.operation=%s AND c.idempotency_key=%s "
                "AND a.action_group='organization_security_or_connector_policy_change' AND a.target_id=%s "
                "AND a.policy_version=%s AND a.used_at IS NOT NULL",
                (context.tenant_id, context.actor_id, operation, key, self._policy_target(context), context.policy_version),
            ).fetchone()
            if proof is None:
                raise StudioError("STEP_UP_REQUIRED", 403)
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("studio-approval-policy|" + context.tenant_id,))
            repo, fingerprint, replay = self._replay(connection, context, operation, key, [ttl_days, expected_policy_version])
            if replay is not None:
                return replay, True
            current = connection.execute("SELECT policy_version FROM studio_approval_policy_versions WHERE tenant_id=%s ORDER BY policy_version DESC LIMIT 1", (context.tenant_id,)).fetchone()
            version = int(current[0]) if current else 0
            if expected_policy_version != version:
                raise StudioError("PRECONDITION_FAILED", 412)
            for setting, value in (("app.studio_approval_policy_key", key),
                                   ("app.studio_approval_policy_target", self._policy_target(context)),
                                   ("app.studio_approval_policy_version", context.policy_version)):
                connection.execute("SELECT set_config(%s,%s,true)", (setting, value))
            connection.execute("INSERT INTO studio_approval_policy_versions(tenant_id,policy_version,ttl_days,changed_by) VALUES(%s,%s,%s,%s)",
                               (context.tenant_id, version + 1, ttl_days, context.actor_id))
            result = {"ttl_days": ttl_days, "policy_version": version + 1, "can_manage": True, "step_up_target_id": self._policy_target(context)}
            repo._finish(connection, context, operation, key, fingerprint, result, "StudioApprovalPolicy", self._policy_target(context))
            return result, False

    def _version_access(self, connection, context, version_id, action):
        from .studio_workspace_postgres import PostgresStudioWorkspaceRepository
        self._grant(connection, context, action)
        if not context.notebook_id:
            raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
        repo = PostgresStudioWorkspaceRepository(self._store)
        bound = connection.execute(
            "SELECT 1 FROM notebooks n JOIN notebook_bindings b ON b.tenant_id=n.tenant_id AND b.workspace_id=n.workspace_id AND b.notebook_id=n.notebook_id "
            "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notebook_id=%s AND n.created_by=%s "
            "AND b.binding_kind='output_version' AND b.record_id=%s AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d "
            "WHERE d.tenant_id=n.tenant_id AND d.workspace_id=n.workspace_id AND d.notebook_id=n.notebook_id AND d.state IN ('accepted','deleting','completed'))",
            (context.tenant_id, context.workspace_id, context.notebook_id, context.actor_id, version_id),
        ).fetchone()
        if bound is None:
            raise StudioError("RESOURCE_UNAVAILABLE", 404)
        versions = connection.execute("SELECT DISTINCT source_version_id FROM evidence_references WHERE tenant_id=%s AND workspace_id=%s AND output_version_id=%s",
                                      (context.tenant_id, context.workspace_id, version_id)).fetchall()
        # The current generation binding/ACL checks also cover unbinding and deletion.
        repo._require_generation_scope(connection, context, tuple(str(row[0]) for row in versions), action=action)
        return repo

    def withdraw(self, context, request_id, output_version_id, key):
        _identifier(request_id); _identifier(output_version_id)
        operation = "studio.approval.withdraw"
        with approval_transaction(self._store, self._cloud(context, operation)) as connection:
            self._notification_context(connection, trace_id=context.trace_id,
                policy_version=context.policy_version, notebook_id=context.notebook_id)
            repo = self._version_access(connection, context, output_version_id, Action.EDIT)
            lock_approval_version(connection, context, output_version_id)
            own = connection.execute("SELECT created_by FROM approval_requests WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND output_version_id=%s",
                                     (context.tenant_id, context.workspace_id, request_id, output_version_id)).fetchone()
            if own is None or own[0] != context.actor_id:
                raise StudioError("RESOURCE_UNAVAILABLE", 404)
            _, fingerprint, replay = self._replay(connection, context, operation, key, [request_id, output_version_id, context.notebook_id])
            if replay is not None:
                return replay, True
            version = require_pending(connection, context, request_id, output_version_id)
            repo._transition(connection, context, "ApprovalRequest", request_id, version, "withdrawn",
                             repo._opaque("withdraw", context.tenant_id, context.workspace_id, request_id))
            connection.execute("SELECT record_studio_approval_notice(%s,'withdrawn')", (request_id,))
            result = {"record_id": request_id, "action": "withdraw", "status": "withdrawn", "output_version_id": output_version_id}
            repo._finish(connection, context, operation, key, fingerprint, result, "ApprovalRequest", request_id)
            return result, False

    @staticmethod
    def _due_notice_is_current(connection, context, notification_id, scheduled_at):
        current = connection.execute(
            "SELECT r.state,studio_approval_deadline(r.canonical_json,r.created_at),clock_timestamp() "
            "FROM approval_requests r JOIN studio_approval_notices n ON n.tenant_id=r.tenant_id "
            "AND n.workspace_id=r.workspace_id AND n.approval_request_id=r.record_id "
            "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notification_id=%s AND n.recipient_id=%s",
            (context.tenant_id, context.workspace_id, notification_id, context.actor_id),
        ).fetchone()
        return current is not None and current[0] == "pending" and scheduled_at <= current[2] < current[1]

    def list_notices(self, context):
        with self._store._transaction(self._cloud(context, "studio.approval.notices.read")) as connection:
            self._grant(connection, context, Action.VIEW)
            if not context.notebook_id:
                raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
            items, cursor = [], None
            while len(items) < 100:
                params = (context.tenant_id, context.workspace_id, context.actor_id, context.notebook_id, context.actor_id)
                after = ""
                if cursor is not None:
                    after = " AND (n.created_at,n.notification_id)<(%s,%s)"
                    params += cursor
                rows = connection.execute(
                    "SELECT n.notification_id,n.approval_request_id,r.output_version_id,n.event_kind,n.scheduled_at,n.created_at,n.read_at,n.version "
                    "FROM studio_approval_notices n JOIN approval_requests r ON r.tenant_id=n.tenant_id AND r.workspace_id=n.workspace_id AND r.record_id=n.approval_request_id "
                    "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.recipient_id=%s "
                    "AND EXISTS(SELECT 1 FROM notebooks nb JOIN notebook_bindings b ON b.tenant_id=nb.tenant_id AND b.workspace_id=nb.workspace_id AND b.notebook_id=nb.notebook_id "
                    "WHERE nb.tenant_id=n.tenant_id AND nb.workspace_id=n.workspace_id AND nb.notebook_id=%s AND nb.created_by=%s "
                    "AND b.binding_kind='output_version' AND b.record_id=r.output_version_id "
                    "AND NOT EXISTS(SELECT 1 FROM notebook_deletion_requests d WHERE d.tenant_id=nb.tenant_id AND d.workspace_id=nb.workspace_id AND d.notebook_id=nb.notebook_id AND d.state IN ('accepted','deleting','completed'))) "
                    "AND (n.event_kind<>'due_24h' OR (r.state='pending' AND clock_timestamp()>=n.scheduled_at AND clock_timestamp()<studio_approval_deadline(r.canonical_json,r.created_at)))"
                    + after + " ORDER BY n.created_at DESC,n.notification_id DESC LIMIT 100", params,
                ).fetchall()
                for row in rows:
                    try:
                        self._version_access(connection, context, str(row[2]), Action.VIEW)
                    except StudioError as error:
                        if error.status == 404 or error.code == "NOTEBOOK_SCOPE_MISMATCH":
                            continue
                        raise
                    if row[3] == "due_24h" and not self._due_notice_is_current(connection, context, str(row[0]), row[4]):
                        continue
                    items.append(dict(zip(("notification_id", "approval_request_id", "output_version_id", "event_kind", "scheduled_at", "created_at", "read_at", "version"),
                                          (*row[:4], _stamp(row[4]), _stamp(row[5]), _stamp(row[6]) if row[6] else None, int(row[7])))))
                    if len(items) == 100:
                        break
                if len(rows) < 100:
                    break
                next_cursor = (rows[-1][5], rows[-1][0])
                if next_cursor == cursor:
                    raise StudioError("STUDIO_RESULT_INVALID", 500)
                cursor = next_cursor
            return {"items": items, "unread_count": sum(item["read_at"] is None for item in items)}

    def mark_notice_read(self, context, notification_id, expected_version, key):
        _identifier(notification_id)
        if type(expected_version) is not int or expected_version < 1:
            raise StudioError("STUDIO_INPUT_INVALID")
        operation = "studio.approval.notice.read"
        with self._store._transaction(self._cloud(context, operation)) as connection:
            self._notification_context(connection, trace_id=context.trace_id,
                policy_version=context.policy_version, notebook_id=context.notebook_id)
            row = connection.execute(
                "SELECT r.output_version_id,n.event_kind,n.scheduled_at "
                "FROM studio_approval_notices n JOIN approval_requests r ON r.tenant_id=n.tenant_id AND r.workspace_id=n.workspace_id AND r.record_id=n.approval_request_id "
                "WHERE n.tenant_id=%s AND n.workspace_id=%s AND n.notification_id=%s AND n.recipient_id=%s FOR UPDATE OF n",
                (context.tenant_id, context.workspace_id, notification_id, context.actor_id),
            ).fetchone()
            if row is None:
                raise StudioError("RESOURCE_UNAVAILABLE", 404)
            repo = self._version_access(connection, context, str(row[0]), Action.VIEW)
            self._notification_identity_access(connection, context)
            _, fingerprint, replay = self._replay(connection, context, operation, key, [notification_id, expected_version, context.notebook_id])
            if row[1] == "due_24h" and not self._due_notice_is_current(connection, context, notification_id, row[2]):
                raise StudioError("RESOURCE_UNAVAILABLE", 404)
            if replay is not None:
                return replay, True
            read = connection.execute("UPDATE studio_approval_notices SET read_at=CURRENT_TIMESTAMP,version=version+1 WHERE tenant_id=%s AND workspace_id=%s AND notification_id=%s AND recipient_id=%s AND version=%s AND read_at IS NULL RETURNING version,read_at",
                                      (context.tenant_id, context.workspace_id, notification_id, context.actor_id, expected_version)).fetchone()
            if read is None:
                raise StudioError("PRECONDITION_FAILED", 412)
            result = {"notification_id": notification_id, "version": int(read[0]), "read_at": _stamp(read[1])}
            repo._finish(connection, context, operation, key, fingerprint, result, "StudioApprovalNotice", notification_id)
            if getattr(self._store, "_notification_inbox_enabled", False):
                recorded = connection.execute("SELECT public.record_studio_notice_notification(%s,'notification.read',%s,%s,%s)",
                    (notification_id, context.trace_id, context.policy_version, key)).fetchone()
                if recorded is None or recorded[0] is not True:
                    raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
            return result, False

    def sweep_due(self, worker_id, limit=100):
        _identifier(worker_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StudioError("STUDIO_INPUT_INVALID")
        # No tenant/recipient/state can be supplied to the cross-scope DB entrypoint.
        context = CloudAccessContext("studio-sweep", "studio-sweep", worker_id, "studio.approval.sweep")
        with self._store._transaction(context) as connection:
            self._notification_context(connection, trace_id="trace-studio-sweep-" + uuid4().hex,
                policy_version="s42-studio-notice-v1")
            row = connection.execute("SELECT processed_count,notice_count,expired_count FROM sweep_studio_approval_lifecycle(%s,%s)", (worker_id, limit)).fetchone()
            return dict(zip(("processed_count", "notice_count", "expired_count"), map(int, row)))
