"""S24 lifecycle domain checks; PostgreSQL evidence is separately opt-in."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import pytest

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def test_s24_lifecycle_default_and_policy_boundaries_freeze_the_request():
    from daon_user_api.studio_approval_lifecycle import freeze_deadline, request_deadline
    original = freeze_deadline(NOW)
    assert original == {"ttl_days": 7, "ttl_policy_version": 0,
                        "expires_at": "2026-10-23T12:00:00Z", "remind_at": "2026-10-22T12:00:00Z"}
    assert freeze_deadline(NOW, 1, 4)["remind_at"] == "2026-10-16T12:00:00Z"
    assert freeze_deadline(NOW, 30, 5)["expires_at"] == "2026-11-15T12:00:00Z"
    freeze_deadline(NOW, 2, 6)
    assert request_deadline(original, NOW) == (NOW + timedelta(days=7), NOW + timedelta(days=6))
    assert request_deadline({"output_version_id": "legacy-v1"}, NOW) == (NOW + timedelta(days=7), NOW + timedelta(days=6))


@pytest.mark.parametrize("ttl", [0, 31, True, False, 1.0, "7", None])
def test_s24_lifecycle_rejects_invalid_ttl_without_coercion(ttl):
    from daon_user_api.studio_approval_lifecycle import freeze_deadline
    from daon_user_api.studio_workspace import StudioError
    with pytest.raises(StudioError, match="STUDIO_INPUT_INVALID"):
        freeze_deadline(NOW, ttl, 1)


def test_s24_lifecycle_due_and_expiry_are_exclusive_and_never_auto_approve():
    from daon_user_api.studio_approval_lifecycle import notice_due
    expiry = NOW + timedelta(days=1)
    assert notice_due("pending", expiry, NOW) == ("due_24h", NOW)
    assert notice_due("pending", expiry, expiry) == ("expired", expiry)
    for state in ("approved", "rejected", "withdrawn", "expired"):
        assert notice_due(state, expiry, expiry) is None
    assert notice_due("pending", expiry, NOW - timedelta(microseconds=1)) is None


@pytest.mark.parametrize("patch", [
    {"ttl_days": True}, {"ttl_policy_version": -1}, {"expires_at": "2027-01-01T00:00:00Z"},
    {"remind_at": "2026-10-23T12:00:00Z"}, {"expires_at": None},
])
def test_s24_lifecycle_bad_stored_deadline_is_not_silently_legacy(patch):
    from daon_user_api.studio_approval_lifecycle import freeze_deadline, request_deadline
    from daon_user_api.studio_workspace import StudioError
    with pytest.raises(StudioError, match="STUDIO_APPROVAL_DEADLINE_INVALID"):
        request_deadline({**freeze_deadline(NOW), **patch}, NOW)
    with pytest.raises(StudioError, match="STUDIO_APPROVAL_DEADLINE_INVALID"):
        request_deadline({"ttl_days": 7}, NOW)


def test_s24_lifecycle_expiry_commits_before_controlled_error_but_other_errors_rollback():
    from daon_user_api.studio_approval_lifecycle import approval_transaction, ApprovalExpired
    from daon_user_api.studio_workspace import StudioError
    events = []
    class Store:
        @contextmanager
        def _transaction(self, context):
            events.append("begin")
            try:
                yield object()
            except Exception:
                events.append("rollback")
                raise
            else:
                events.append("commit")
    with pytest.raises(StudioError, match="APPROVAL_REQUEST_EXPIRED"):
        with approval_transaction(Store(), object()):
            events.append("expiry-transition")
            raise ApprovalExpired()
    assert events == ["begin", "expiry-transition", "commit"]
    events.clear()
    with pytest.raises(StudioError, match="RESOURCE_UNAVAILABLE"):
        with approval_transaction(Store(), object()):
            raise StudioError("RESOURCE_UNAVAILABLE", 404)
    assert events == ["begin", "rollback"]


def test_s24_lifecycle_policy_target_is_a_safe_stable_scope_not_raw_ids():
    import hashlib
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace import StudioContext
    context = StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1")
    target = StudioApprovalLifecycleRepository._policy_target(context)
    assert target == "studio-approval-policy-" + hashlib.sha256(b'["tenant-1","workspace-1"]').hexdigest()


class ActionStore:
    """SQL boundary fixture, not a real PostgreSQL transaction/lock proof."""
    def __init__(self, *, expired=False, duplicate=False):
        from test_studio_workspace_postgres import ApprovalTransitionConnection, Result
        self.events = []
        self.connection = ApprovalTransitionConnection()
        original = self.connection.execute
        def execute(sql, params=()):
            if "studio_approval_deadline(canonical_json,created_at)" in sql:
                return Result(("pending", 1, NOW - timedelta(seconds=1) if expired else NOW + timedelta(days=7), NOW))
            if sql.startswith("SELECT record_id FROM approval_requests"):
                return Result(rows=(("other-pending",),) if duplicate else ())
            if "expire_studio_approval_request" in sql:
                self.events.append("expire")
                return Result((expired,))
            if "FROM studio_approval_policy_versions" in sql:
                return Result()
            if sql == "SELECT CURRENT_TIMESTAMP":
                return Result((NOW,))
            if "FROM review_requests" in sql:
                return Result((1,))
            return original(sql, params)
        self.connection.execute = execute

    @contextmanager
    def _transaction(self, _context):
        self.events.append("begin")
        try:
            yield self.connection
        except Exception:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")


def action_context():
    from daon_user_api.studio_workspace import StudioContext
    return StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")


def test_s24_lifecycle_existing_approval_path_commits_expiry_without_approval_insert():
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioError
    store = ActionStore(expired=True)
    with pytest.raises(StudioError, match="APPROVAL_REQUEST_EXPIRED"):
        PostgresStudioWorkspaceRepository(store).record_action(action_context(), "approval",
            {"output_version_id": "version-1", "approval_request_id": "request-1", "decision": "approved"}, "s24-approve-key-0001")
    assert store.events == ["begin", "expire", "commit"]
    assert not any(sql.startswith("INSERT INTO approvals") for sql, _ in store.connection.statements)


def test_s24_lifecycle_existing_request_path_freezes_db_time_and_policy_in_canonical():
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    store = ActionStore()
    result, replayed = PostgresStudioWorkspaceRepository(store).record_action(action_context(), "approval_request",
        {"output_version_id": "version-1", "review_request_id": "review-1"}, "s24-request-key-0001")
    assert replayed is False and result["action"] == "approval_request"
    inserted = next(params for sql, params in store.connection.statements if sql.startswith("INSERT INTO approval_requests"))
    payload = inserted[6].obj
    assert {name: payload[name] for name in ("ttl_days", "ttl_policy_version", "expires_at", "remind_at")} == {
        "ttl_days": 7, "ttl_policy_version": 0, "expires_at": "2026-10-23T12:00:00Z", "remind_at": "2026-10-22T12:00:00Z"}


def test_s24_lifecycle_existing_request_path_rejects_second_pending_before_insert():
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioError
    store = ActionStore(duplicate=True)
    with pytest.raises(StudioError, match="APPROVAL_REQUEST_PENDING"):
        PostgresStudioWorkspaceRepository(store).record_action(action_context(), "approval_request",
            {"output_version_id": "version-1", "review_request_id": "review-1"}, "s24-duplicate-key-001")
    assert not any(sql.startswith("INSERT INTO approval_requests") for sql, _ in store.connection.statements)


class PolicyStore(ActionStore):
    """Persisted SQL boundary double; actual RLS/receipt security needs opt-in PG."""
    def __init__(self, *, owner=True, receipt=True):
        import json
        from test_studio_workspace_postgres import Connection, Result
        super().__init__()
        self.policy = None
        self.receipts = {}
        original = self.connection.execute
        def execute(sql, params=()):
            if "FROM identity_auth_tenant_roles" in sql and not owner:
                return Result()
            if "FROM identity_auth_memberships" in sql and not owner:
                return Result({"role":"viewer","state":"active","version":1})
            if sql.startswith("SELECT state FROM identity_users") or "FROM identity_auth_" in sql:
                return Connection.execute(self.connection, sql, params)
            if sql.startswith("SELECT 1 FROM identity_step_up_consumptions"):
                return Result((1,) if receipt else None)
            if sql.startswith("SELECT ttl_days,policy_version"):
                return Result(self.policy)
            if sql.startswith("SELECT policy_version FROM studio_approval_policy_versions"):
                return Result((self.policy[1],) if self.policy else None)
            if sql.startswith("INSERT INTO studio_approval_policy_versions"):
                self.connection.statements.append((sql,params))
                self.policy=(params[2],params[1])
                return Result()
            if "FROM idempotency_records" in sql:
                return Result(self.receipts.get(params[-1]))
            if sql.startswith("INSERT INTO idempotency_records"):
                self.receipts[params[4]]=(params[5],json.loads(params[6]))
            return original(sql,params)
        self.connection.execute=execute


def test_s24_lifecycle_policy_is_versioned_with_cas_exact_replay_and_current_role_checks():
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace import StudioError
    store=PolicyStore(); repo=StudioApprovalLifecycleRepository(store); ctx=action_context()
    assert repo.get_policy(ctx)=={"ttl_days":7,"policy_version":0,"can_manage":True,"step_up_target_id":repo._policy_target(ctx)}
    saved,replay=repo.save_policy(ctx,1,0,"s24-policy-save-001")
    assert replay is False and saved["ttl_days"]==1 and saved["policy_version"]==1
    assert repo.save_policy(ctx,1,0,"s24-policy-save-001")== (saved,True)
    with pytest.raises(StudioError,match="IDEMPOTENCY_CONFLICT"):
        repo.save_policy(ctx,30,0,"s24-policy-save-001")
    with pytest.raises(StudioError,match="PRECONDITION_FAILED"):
        repo.save_policy(ctx,30,0,"s24-policy-save-002")
    assert store.policy==(1,1)


@pytest.mark.parametrize("owner,receipt,error",[(False,True,"RESOURCE_UNAVAILABLE"),(True,False,"STEP_UP_REQUIRED")])
def test_s24_lifecycle_policy_cannot_write_without_current_authority_and_consumed_receipt(owner,receipt,error):
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace import StudioError
    store=PolicyStore(owner=owner,receipt=receipt)
    with pytest.raises(StudioError,match=error):
        StudioApprovalLifecycleRepository(store).save_policy(action_context(),7,0,"s24-denied-policy-001")
    assert store.policy is None
    assert not store.receipts


@pytest.mark.parametrize("action", ["approval", "approval_request"])
def test_s24_lifecycle_app_without_canonical_update_can_use_scoped_lock(action):
    """Boundary double models app privilege denial; real lock/RLS proof is PG-only."""
    import psycopg
    from test_studio_workspace_postgres import Result
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    store = ActionStore()
    original = store.connection.execute
    calls = []
    def execute(sql, params=()):
        if "FOR UPDATE" in sql and ("output_versions" in sql or "approval_requests" in sql):
            raise psycopg.errors.InsufficientPrivilege("canonical UPDATE denied")
        if "lock_studio_approval_version" in sql:
            calls.append(params)
            return Result((True,))
        return original(sql, params)
    store.connection.execute = execute
    payload = {"output_version_id": "version-1"}
    if action == "approval":
        payload.update(approval_request_id="request-1", decision="approved")
    else:
        payload["review_request_id"] = "review-1"
    result, replay = PostgresStudioWorkspaceRepository(store).record_action(
        action_context(), action, payload, "s24-lock-action-0001")
    assert replay is False and result["action"] == action
    assert calls and all(params == ("version-1", "notebook-1") for params in calls)


def test_s24_lifecycle_late_deadline_rolls_back_approval_savepoint_and_commits_expiry():
    from types import SimpleNamespace
    from test_studio_workspace_postgres import Result
    from daon_user_api.studio_approval_lifecycle import approval_transaction
    from daon_user_api.studio_workspace import StudioError
    class DeadlineViolation(Exception):
        sqlstate = "23514"
        diag = SimpleNamespace(message_primary="APPROVAL_REQUEST_EXPIRED")
    store = ActionStore()
    state = {"approvals": 0}
    @contextmanager
    def savepoint():
        store.events.append("savepoint")
        try:
            yield
        except Exception:
            state["approvals"] = 0
            store.events.append("approval-rollback")
            raise
    store.connection.transaction = savepoint
    original = store.connection.execute
    def execute(sql, params=()):
        if "expire_studio_approval_request" in sql:
            store.events.append("late-expiry")
            return Result((True,))
        return original(sql, params)
    store.connection.execute = execute
    with pytest.raises(StudioError, match="APPROVAL_REQUEST_EXPIRED"):
        with approval_transaction(store, object(), expiry_request_id="request-1"):
            state["approvals"] = 1
            raise DeadlineViolation()
    assert state == {"approvals": 0}
    assert store.events == ["begin", "savepoint", "approval-rollback", "late-expiry", "commit"]
    store.events.clear()
    with pytest.raises(ValueError, match="unrelated"):
        with approval_transaction(store, object(), expiry_request_id="request-1"):
            raise ValueError("unrelated")
    assert store.events == ["begin", "savepoint", "approval-rollback", "rollback"]


def test_s24_lifecycle_notices_hide_overdue_pending_without_waiting_for_worker():
    from test_studio_workspace_postgres import Connection, Result
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    store = PolicyStore()
    original = store.connection.execute
    current = {"notice-old": ("pending", NOW, NOW), "notice-due": ("pending", NOW+timedelta(days=1), NOW), "notice-done": ("approved", NOW+timedelta(days=1), NOW)}
    rechecks = []
    def execute(sql, params=()):
        if sql.startswith("SELECT r.state,studio_approval_deadline(r.canonical_json,r.created_at),clock_timestamp() "):
            assert params[:2] == ("tenant-1", "workspace-1") and params[3] == "actor-1"
            rechecks.append(params[2])
            return Result(current.get(params[2]))
        if sql.startswith("SELECT n.notification_id"):
            # Last two columns are the stored deadline and DB clock, not host time.
            return Result(rows=[
                ("notice-old", "request-old", "version-1", "due_24h", NOW-timedelta(days=1), NOW, None, 1, "pending", NOW, NOW),
                ("notice-due", "request-due", "version-1", "due_24h", NOW, NOW, None, 1, "pending", NOW+timedelta(days=1), NOW),
                ("notice-done", "request-done", "version-1", "due_24h", NOW, NOW, None, 1, "approved", NOW+timedelta(days=1), NOW),
                ("notice-expired", "request-old", "version-1", "expired", NOW, NOW, None, 1, "expired", NOW, NOW),
            ])
        if sql.startswith("SELECT 1 FROM notebooks"):
            return Result((1,))
        if sql.startswith("SELECT DISTINCT source_version_id"):
            return Result(rows=(("source-version-1",),))
        if "SELECT count(*) FROM notebook_bindings" in sql or sql.startswith("SELECT canonical_json FROM source_versions"):
            return Connection.execute(store.connection, sql, params)
        return original(sql, params)
    store.connection.execute = execute
    result = StudioApprovalLifecycleRepository(store).list_notices(action_context())
    assert [item["notification_id"] for item in result["items"]] == ["notice-due", "notice-expired"]
    assert result["unread_count"] == 2
    assert "notice-due" in rechecks
    # 앞선 목록이 대기/미래 만료로 관측했어도 현재 회수 또는 DB 시각의
    # 정확한 만료 경계에서는 안내를 숨겨야 한다.
    for current_row in (("withdrawn", NOW+timedelta(days=1), NOW), ("pending", NOW+timedelta(days=1), NOW+timedelta(days=1))):
        current["notice-due"] = current_row
        result = StudioApprovalLifecycleRepository(store).list_notices(action_context())
        assert [item["notification_id"] for item in result["items"]] == ["notice-expired"]
        assert result["unread_count"] == 1


@pytest.mark.parametrize("action", ["approval", "approval_request"])
def test_s24_lifecycle_explicit_text_source_deny_blocks_approval_before_write(action):
    from test_studio_workspace_postgres import Result
    from daon_user_api.studio_workspace import StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    store=ActionStore()
    original=store.connection.execute
    deny_checks=[]
    def execute(sql,params=()):
        if "identity_auth_source_access" in sql:
            if sql.startswith("SELECT 1 FROM evidence_references er JOIN source_versions sv "):
                assert "AND a.user_id=%s AND a.allowed=0 WHERE er.tenant_id=%s AND er.workspace_id=%s AND er.output_version_id=%s" in sql
                assert params == ("actor-1","tenant-1","workspace-1","version-1")
                deny_checks.append("shared_output_guard")
            else:
                assert sql.startswith("SELECT 1 FROM evidence_references er JOIN identity_auth_source_access a ")
                assert "WHERE er.tenant_id=%s AND er.workspace_id=%s AND er.output_version_id=%s AND a.user_id=%s AND a.allowed=0" in sql
                assert params == ("tenant-1","workspace-1","version-1","actor-1")
                deny_checks.append("approval_version_guard")
            return Result((1,))
        return original(sql,params)
    store.connection.execute=execute
    payload={"output_version_id":"version-1"}
    payload.update({"approval_request_id":"request-1","decision":"approved"} if action=="approval" else {"review_request_id":"review-1"})
    with pytest.raises(StudioError,match="RESOURCE_UNAVAILABLE"):
        PostgresStudioWorkspaceRepository(store).record_action(action_context(),action,payload,"s24-explicit-deny-001")
    assert deny_checks == ["shared_output_guard"]
    assert not any(sql.startswith(("INSERT INTO approvals","INSERT INTO approval_requests")) for sql,_ in store.connection.statements)
    assert store.events == ["begin","rollback"]


@pytest.mark.parametrize("blocked",["other_notebook","source_denied"])
def test_s24_lifecycle_notice_limit_cannot_hide_current_accessible_notebook(blocked):
    """Execute the production list SELECT on SQLite; Auth SQL remains a boundary double, not PG/RLS evidence."""
    import json
    import sqlite3
    from test_studio_workspace_postgres import Connection, Result
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    db=sqlite3.connect(":memory:")
    db.executescript("""
      CREATE TABLE approval_requests(tenant_id,workspace_id,record_id,output_version_id,state,canonical_json,created_at);
      CREATE TABLE studio_approval_notices(tenant_id,workspace_id,recipient_id,notification_id,approval_request_id,event_kind,scheduled_at,created_at,read_at,version);
      CREATE TABLE notebooks(tenant_id,workspace_id,notebook_id,created_by);
      CREATE TABLE notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id);
      CREATE TABLE notebook_deletion_requests(tenant_id,workspace_id,notebook_id,state);
    """)
    stamp=NOW.isoformat(); expiry=(NOW+timedelta(days=1)).isoformat()
    db.create_function("clock_timestamp",0,lambda:stamp)
    db.create_function("studio_approval_deadline",2,lambda payload,created:json.loads(payload)["expires_at"])
    for i in range(101):
        version="version-valid" if i==100 else f"version-denied-{i:03}"
        notebook="notebook-other" if blocked=="other_notebook" and i<100 else "notebook-1"
        when=(NOW-timedelta(seconds=i)).isoformat()
        db.execute("INSERT INTO approval_requests VALUES(?,?,?,?,?,?,?)",("tenant-1","workspace-1",f"request-{i}",version,"pending",json.dumps({"expires_at":expiry}),when))
        db.execute("INSERT INTO studio_approval_notices VALUES(?,?,?,?,?,?,?,?,?,?)",("tenant-1","workspace-1","actor-1",f"notice-{i:03}",f"request-{i}","due_24h",stamp,when,None,1))
        db.execute("INSERT INTO notebook_bindings VALUES(?,?,?,?,?)",("tenant-1","workspace-1",notebook,"output_version",version))
    db.executemany("INSERT INTO notebooks VALUES(?,?,?,?)",[("tenant-1","workspace-1",name,"actor-1") for name in ("notebook-1","notebook-other")])
    store=PolicyStore(); original=store.connection.execute; reads=[]
    def execute(sql,params=()):
        if sql.startswith("SELECT r.state,studio_approval_deadline(r.canonical_json,r.created_at),clock_timestamp() "):
            row = db.execute(sql.replace("%s", "?"), params).fetchone()
            return Result(None if row is None else (row[0], datetime.fromisoformat(row[1]), datetime.fromisoformat(row[2])))
        if sql.startswith("SELECT n.notification_id"):
            reads.append(1)
            values=db.execute(sql.replace("%s","?"),tuple(v.isoformat() if isinstance(v,datetime) else v for v in params)).fetchall()
            converted=[]
            for raw in values:
                row=list(raw)
                for index in (4,5,6,9,10):
                    if len(row)>index and row[index] is not None: row[index]=datetime.fromisoformat(row[index])
                converted.append(tuple(row))
            return Result(rows=converted)
        if sql.startswith("SELECT 1 FROM notebooks"):
            version=params[-1]
            allowed=db.execute("SELECT 1 FROM notebook_bindings WHERE record_id=? AND notebook_id='notebook-1'",(version,)).fetchone()
            return Result(allowed)
        if sql.startswith("SELECT DISTINCT source_version_id"):
            return Result(rows=(("source-denied" if blocked=="source_denied" and params[-1]!="version-valid" else "source-version-1",),))
        if "SELECT count(*) FROM notebook_bindings" in sql:
            return Result((0 if params[3]==["source-denied"] else 1,))
        if sql.startswith("SELECT canonical_json FROM source_versions"):
            return Connection.execute(store.connection,sql,params)
        return original(sql,params)
    store.connection.execute=execute
    try:
        result=StudioApprovalLifecycleRepository(store).list_notices(action_context())
        assert [item["notification_id"] for item in result["items"]]==["notice-100"]
        assert result["unread_count"]==1
        assert len(reads)==(1 if blocked=="other_notebook" else 2)
    finally:
        db.close()
