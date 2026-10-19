"""R2 보호 원장/원자 재처리 adapter. 0072는 미적용 초안이다.

current_policy_reader는 R3의 같은 connection 기반 현재 정책/FrozenContext 접합이다.
미주입이면 실패하며 비용0/허용으로 대체하지 않는다. 현재 계정/EDIT/ANALYZE/
조직·Source 검사는 이 adapter가 독립 재검증한다. 호출자가 준 pure bool은 인가가
아니다. SQL 대역 시험은 실제 PostgreSQL 권한/잠금/삭제 경합 증거가 아니다.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from .authorization import Action, Role
from .cloud_storage import CloudAccessContext
from .data_canon import canonical_json_bytes
from .document_index_postgres import _source_access_allowed
from .document_processing import DocumentProcessingContext
from .document_processing_postgres import PostgresDocumentProcessingRepository
from .document_readiness import (
    CurrentReprocessEligibility, ReadinessDispatcherSettings, ReadinessModeProof, ReprocessDecision,
    RoleWaitEvidence, ValidatedReadinessObservation, decide_reprocess,
)
from .document_understanding_adapter import DocumentUnderstandingError
from .notification_source_access import _special_source_access_allowed
from .open_law_source_access import OpenLawAccessError, _AuthReadConnection, _current_auth_grant
from .organization_access import OrganizationAccessBridge
from .organization_membership import OrganizationWorkflowError
from .workspace_model_defaults import ReadinessPolicySnapshot


@dataclass(frozen=True, slots=True, repr=False)
class ReprocessReceipt:
    outcome: str
    safe_code: str
    new_run_id: str | None
    received_at: datetime


def _error(code="DOCUMENT_PROCESSING_CONTEXT_INVALID", status=400):
    return DocumentUnderstandingError(code, status=status)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _wait_payload(wait):
    value = asdict(wait)
    for field in ("failed_at", "not_before"):
        value[field] = value[field].isoformat()
    return value


def _wait(value):
    payload = dict(value)
    payload["mode"] = ReadinessModeProof(**payload["mode"])
    for field in ("failed_at", "not_before"):
        payload[field] = datetime.fromisoformat(payload[field])
    return RoleWaitEvidence(**payload)


def _observation(value):
    payload = dict(value)
    for field in ("observed_at", "received_at"):
        payload[field] = datetime.fromisoformat(payload[field])
    return ValidatedReadinessObservation(**payload)


class _BudgetResult:
    def __init__(self, result, connection):
        self._result, self._connection = result, connection

    def __getattr__(self, name):
        return getattr(self._result, name)

    def fetchone(self):
        self._connection.check()
        value = self._result.fetchone()
        self._connection.check()
        return value

    def fetchall(self):
        self._connection.check()
        value = self._result.fetchall()
        self._connection.check()
        return value


class _BudgetConnection:
    """한 dispatch의 절대 예산. 각 SQL/잠금에 남은 DB timeout을 재설정한다."""
    def __init__(self, connection, deadline):
        self._connection, self._deadline = connection, deadline

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def check(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
        return remaining

    def execute(self, sql, params=()):
        remaining = self.check()
        self._connection.execute("SELECT set_config('statement_timeout',%s,true)",
                                 (str(max(1, int(remaining * 1000))),))
        self.check()
        result = self._connection.execute(sql, params)
        self.check()
        return _BudgetResult(result, self)


class PostgresDocumentReadinessRepository:
    def __init__(self, cloud_store, *, settings=None, discovery_transaction=None, current_policy_reader=None):
        self._store = cloud_store
        self.settings = ReadinessDispatcherSettings() if settings is None else settings
        if type(self.settings) is not ReadinessDispatcherSettings:
            raise _error()
        self._discovery_transaction = discovery_transaction or self._global_transaction
        self._current_policy_reader = current_policy_reader
        # 같은 repository를 poll 사이에 재사용한다. 재시작 시 처음부터 재발견하며 ack가 아니다.
        self._discovery_scopes = self.settings.scopes
        self._after_cursor = None

    @contextmanager
    def _global_transaction(self):
        # 기존 API pool 대여만. 새 DSN/pool/role·원문·키 소비 없음.
        self._store._ensure_pool_open()
        with self._store._pool.connection(timeout=2.0) as connection:
            with connection.transaction():
                yield connection

    @staticmethod
    def _mapped(error):
        if isinstance(error, DocumentUnderstandingError):
            return error
        if isinstance(error, (OpenLawAccessError, OrganizationWorkflowError)):
            return _error("CURRENT_ACCESS_DENIED", 403)
        return _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)

    @staticmethod
    def _lock(connection, value):
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (value,))

    def _source_locks(self, connection, wait):
        # 기존 reprocess/영구삭제의 Notebook -> unbind -> retention-source 순서.
        for lock in (f"notebook-delete|{wait.tenant_id}|{wait.workspace_id}|{wait.notebook_id}",
                     f"unbind-notebook|{wait.tenant_id}|{wait.workspace_id}|{wait.notebook_id}",
                     f"retention-source|{wait.tenant_id}|{wait.workspace_id}|{wait.source_id}"):
            self._lock(connection, lock)

    def _authorize(self, connection, context, wait):
        scoped = SimpleNamespace(**vars_context(context), notebook_id=wait.notebook_id)
        edit = _current_auth_grant(scoped, connection, Action.EDIT)
        _current_auth_grant(scoped, connection, Action.ANALYZE)
        OrganizationAccessBridge(postgres=True).assert_organization_active(
            connection=_AuthReadConnection(connection), tenant_id=context.tenant_id, user_id=context.actor_id)
        if (not _source_access_allowed(connection, scoped, wait.source_version_id)
                or not _special_source_access_allowed(connection, scoped, wait.source_version_id)):
            raise _error("CURRENT_ACCESS_DENIED", 403)
        return edit

    @staticmethod
    def _parent(connection, context, wait, grant):
        row = connection.execute(
            "SELECT /* readiness-parent */ jsonb_build_object('state',s.state,'version',s.version,"
            "'source_version_id',sv.record_id,'run_id',latest.record_id,'run_state',latest.state,"
            "'canonical_json',latest.canonical_json,'modality',latest.modality) "
            "FROM public.sources s JOIN public.source_versions sv ON (sv.tenant_id,sv.workspace_id,sv.source_id)="
            "(s.tenant_id,s.workspace_id,s.record_id) JOIN public.notebooks n ON "
            "(n.tenant_id,n.workspace_id,n.notebook_id)=(s.tenant_id,s.workspace_id,%s) "
            "JOIN public.notebook_bindings b ON (b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)="
            "(n.tenant_id,n.workspace_id,n.notebook_id,s.record_id,sv.record_id) AND b.binding_kind='source' "
            "JOIN LATERAL(SELECT p.record_id,p.state,p.canonical_json,p.modality FROM public.processing_runs p "
            "WHERE (p.tenant_id,p.workspace_id,p.source_version_id)=(sv.tenant_id,sv.workspace_id,sv.record_id) "
            "ORDER BY p.created_at DESC,p.record_id DESC LIMIT 1) latest ON true "
            "WHERE (s.tenant_id,s.workspace_id,s.record_id,sv.record_id)=(%s,%s,%s,%s) "
            "AND (s.created_by=%s OR %s) AND s.state NOT IN ('disabled','expired','deleting','deleted') "
            "AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE "
            "(newer.tenant_id,newer.workspace_id,newer.source_id)=(s.tenant_id,s.workspace_id,s.record_id) "
            "AND newer.version>sv.version) AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u "
            "WHERE (u.tenant_id,u.workspace_id,u.notebook_id,u.source_id,u.source_version_id)="
            "(b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)) "
            "AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE "
            "(d.tenant_id,d.workspace_id,d.notebook_id)=(n.tenant_id,n.workspace_id,n.notebook_id)) "
            "AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE "
            "(d.tenant_id,d.workspace_id,d.source_id)=(s.tenant_id,s.workspace_id,s.record_id) AND NOT d.source_active)",
            (wait.notebook_id, context.tenant_id, context.workspace_id, wait.source_id, wait.source_version_id,
             context.actor_id, grant.role in {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN}),
        ).fetchone()
        if row is None:
            raise _error("SOURCE_NOT_FOUND", 404)
        return row[0]

    def record_wait(self, connection, context, processing_run_id, evidence):
        if (type(evidence) is not RoleWaitEvidence or type(context) is not DocumentProcessingContext
                or (context.tenant_id, context.workspace_id, context.actor_id, processing_run_id)
                != (evidence.tenant_id, evidence.workspace_id, evidence.actor_id, evidence.processing_run_id)):
            raise _error()
        try:
            self._source_locks(connection, evidence)
            grant = self._authorize(connection, context, evidence)
            self._parent(connection, context, evidence, grant)
            row = connection.execute("SELECT public.record_document_role_wait(%s)",
                                     (Jsonb(_wait_payload(evidence)),)).fetchone()
            if row is None:
                raise _error("SOURCE_NOT_FOUND", 404)
            return _wait(row[0])
        except Exception as error:
            raise self._mapped(error) from None

    def record_observation(self, validated_observation):
        if type(validated_observation) is not ValidatedReadinessObservation:
            raise _error()
        # DB 배정 두 필드는 INSERT 인자로 전달하지 않는다. producer의 실제 원 관측만.
        payload = asdict(validated_observation)
        for field in ("received_at", "observation_sequence", "content_digest"):
            payload.pop(field)
        payload["observed_at"] = validated_observation.observed_at.isoformat()
        canonical = _json(payload)
        payload["content_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        try:
            with self._discovery_transaction() as connection:
                row = connection.execute("SELECT public.record_document_readiness_observation(%s,%s)",
                                         (Jsonb(payload), canonical)).fetchone()
                if row is None:
                    raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
                return _observation(row[0])
        except Exception as error:
            raise self._mapped(error) from None

    def dispatch_ready(self, *, max_items=32, max_seconds=2):
        if (type(max_items) is not int or not 1 <= max_items <= self.settings.max_batch
                or type(max_seconds) is not int or not 1 <= max_seconds <= self.settings.max_seconds):
            raise _error()
        if self._discovery_scopes != self.settings.scopes:
            self._discovery_scopes = self.settings.scopes
            self._after_cursor = None
        if not self.settings.scopes:
            return ()
        if self._current_policy_reader is None:
            raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
        deadline = time.monotonic() + max_seconds
        results = []
        try:
            with self._discovery_transaction() as raw:
                connection = _BudgetConnection(raw, deadline)
                configured = [dict(zip(("tenant_id", "workspace_id", "actor_id", "source_version_id"), row))
                              for row in sorted(self.settings.scopes)]
                scopes = connection.execute("SELECT * FROM public.discover_document_role_waits(%s,%s,%s)",
                    (Jsonb(configured), None if self._after_cursor is None else list(self._after_cursor),
                     max_items)).fetchall()
                connection.check()
            if not scopes:
                self._after_cursor = None  # 이번 호출은 끝내고 다음 poll부터 wrap한다.
                return ()
            for scope in scopes:
                if time.monotonic() >= deadline:
                    break
                if (len(scope) != 8 or any(type(v) is not str or re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", v) is None for v in scope)
                        or scope[7] not in ("text_generation", "image_understanding", "document_parsing",
                                             "speech_to_text", "unknown")):
                    raise _error()
                t, w, a, notebook, source, version, run, slot = scope
                if (t, w, a, version) not in self.settings.scopes:
                    continue
                # 검증된 실제 시도 행까지만 진행한다. None/403/DB 실패도 ack 없이 다음 행으로,
                # page의 아직 시도하지 않은 행은 건너뛰지 않는다. wrap/재시작 때 다시 발견한다.
                self._after_cursor = (t, w, run, slot)
                context = DocumentProcessingContext(t, w, a, "rdy-dispatch-" + uuid.uuid4().hex, "readiness-v1")
                with self._store._transaction(CloudAccessContext(t, w, a, "source.process")) as raw:
                    connection = _BudgetConnection(raw, deadline)
                    row = connection.execute("SELECT public.read_document_role_wait(%s,%s,%s,%s)",
                                             (t, w, run, slot)).fetchone()
                    if row is None or row[0] is None:
                        continue
                    wait = _wait(row[0])
                    if (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id,
                        wait.source_id, wait.source_version_id, wait.processing_run_id, wait.capability or "unknown") != tuple(scope):
                        raise _error()
                    results.append(self._dispatch_one(connection, context, wait))
                    # 만료된 Tx는 결과/업무행을 commit하지 않고 바깥 context에서 rollback.
                    connection.check()
            return tuple(results)
        except Exception as error:
            raise self._mapped(error) from None

    def _dispatch_one(self, connection, context, wait):
        self._source_locks(connection, wait)
        grant = self._authorize(connection, context, wait)
        context = replace(context, policy_version=grant.policy_version)
        parent = self._parent(connection, context, wait, grant)
        policy_current = self._read_current_snapshot(connection, context, wait, grant)
        current = policy_current.eligibility
        key = (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.source_version_id,
               wait.capability or "unknown", current.frozen_context_digest)
        self._lock(connection, "document-readiness-context|" + "|".join(key))
        # 권한과 Source는 port bool이 아니라 이 connection의 실제 기존 helper로 확인한다.
        grant = self._authorize(connection, context, wait)
        parent = self._parent(connection, context, wait, grant)
        history = connection.execute(
            "SELECT /* readiness-history */ value FROM public.read_document_reprocess_history(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS h(value)",
            (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id, wait.source_id,
             wait.source_version_id, wait.processing_run_id, wait.capability or "unknown", current.frozen_context_digest)).fetchall()
        prior = [row[0] for row in history]
        completed = next((p for p in prior if p["new_run_id"] is not None), None)
        active = connection.execute(
            "SELECT /* readiness-active */ 1 FROM public.processing_runs p "
            "LEFT JOIN public.document_processing_jobs j ON (j.tenant_id,j.workspace_id,j.processing_run_id)="
            "(p.tenant_id,p.workspace_id,p.record_id) WHERE (p.tenant_id,p.workspace_id,p.source_version_id)=(%s,%s,%s) "
            "AND (p.state NOT IN ('completed','failed','policy_blocked') OR j.state IN ('pending','leased','retry_wait')) LIMIT 1",
            (wait.tenant_id, wait.workspace_id, wait.source_version_id)).fetchone() is not None
        event_row = None if current.dependency_connection_id is None else connection.execute(
            "SELECT public.read_latest_document_readiness_observation(%s)",
            (current.dependency_connection_id,)).fetchone()
        observation = None if event_row is None or event_row[0] is None else _observation(event_row[0])
        if observation is None:
            # receipt에는 실제 event FK가 필요하다. 없는 사건을 발명하지 않는다.
            return ReprocessReceipt("deferred", "READINESS_NOT_READY", None,
                                    connection.execute("SELECT clock_timestamp()").fetchone()[0])
        if completed is not None and completed["event_id"] == observation.event_id:
            return self._receipt(completed)
        now = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        # 기존 helper의 EDIT/ANALYZE 재검증은 완료됐지만 B1의 granular LLM deny를
        # 그 grant로 덮어쓰지 않는다. snapshot bool 자체가 현재 DB 인가를 대신하지도 않는다.
        current = replace(current, source_current=True, active_run=active,
            scope_enabled=(wait.tenant_id, wait.workspace_id, wait.actor_id, wait.source_version_id) in self.settings.scopes,
            context_already_decided=completed is not None,
            cost_blocked_context_exists=any(p["outcome"] == "blocked_cost" and p["new_run_id"] is not None for p in prior),
            latest_observation_sequence=observation.observation_sequence)
        # R3 port의 현재 의존성 metadata와 최신 관측 일치를 R1이 비교한다.
        decision = decide_reprocess(replace(wait, processing_run_id=parent["run_id"]), observation,
                                    replace(current, prior_run_id=parent["run_id"]), db_now=now)
        run_id = None
        if decision.create_run:
            known_block = connection.execute(
                "SELECT public.read_document_blocked_parent(%s,%s,%s,%s,%s,%s,%s,%s)",
                (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id, wait.source_id,
                 wait.source_version_id, wait.processing_run_id, parent["run_id"])).fetchone()
            linked_block = known_block is not None and known_block[0] is True
            if parent["run_id"] != wait.processing_run_id and not linked_block:
                raise _error("SOURCE_REPROCESSING_STATE_INVALID", 409)
            if parent["state"] not in {"waiting_model", "needs_review"}:
                raise _error("SOURCE_REPROCESSING_STATE_INVALID", 409)
            if parent["run_state"] not in {"failed", "policy_blocked"}:
                raise _error("SOURCE_REPROCESSING_STATE_INVALID", 409)
            if parent["state"] == "needs_review":
                if not linked_block:
                    raise _error("SOURCE_REPROCESSING_STATE_INVALID", 409)
            # 최종 쓰기 직전에도 기존 자료 접근을 재검증한다. 새 공통 직렬화 주장은 없다.
            final_grant = self._authorize(connection, context, wait)
            if final_grant != grant:
                raise _error("CURRENT_ACCESS_DENIED", 403)
            final_snapshot = self._read_current_snapshot(connection, context, wait, final_grant)
            if final_snapshot != policy_current:
                raise _error("SOURCE_VERSION_CONFLICT", 409)
            latest = self._parent(connection, context, wait, grant)
            if latest != parent:
                raise _error("SOURCE_VERSION_CONFLICT", 409)
            run_id = self._create_run(connection, context, wait, parent, current, observation, decision, grant, final_snapshot)
        outcome = {"enqueue": "enqueued", "block_cost": "blocked_cost", "block_policy": "blocked_policy",
                   "deny": "denied", "suppress": "suppressed", "expire": "expired", "defer": "deferred"}[decision.action]
        payload = {"tenant_id": wait.tenant_id, "workspace_id": wait.workspace_id, "actor_id": wait.actor_id,
            "notebook_id": wait.notebook_id, "source_id": wait.source_id, "source_version_id": wait.source_version_id,
            "processing_run_id": wait.processing_run_id, "capability_slot": wait.capability or "unknown",
            "event_id": observation.event_id, "frozen_context_digest": current.frozen_context_digest,
            "phase": "deferred" if outcome == "deferred" else "final", "outcome": outcome,
            "safe_code": decision.safe_code, "new_run_id": run_id, "received_at": now.isoformat()}
        row = connection.execute("SELECT public.record_document_reprocess_receipt(%s)", (Jsonb(payload),)).fetchone()
        if row is None:
            raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
        return self._receipt(row[0])

    def _read_current_snapshot(self, connection, context, wait, grant):
        result = self._current_policy_reader(connection, context, wait, grant)
        if type(result) is not ReadinessPolicySnapshot:
            raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
        # frozen dataclass도 잘못된 서버 port가 우회 구성할 수 있으므로 내용 결속을 재검증한다.
        snapshot = ReadinessPolicySnapshot(result.eligibility, result.frozen_context_json)
        current = snapshot.eligibility
        body = json.loads(snapshot.frozen_context_json)
        if ((current.tenant_id, current.workspace_id, current.actor_id, current.notebook_id,
             current.source_version_id, current.prior_run_id, current.capability)
                != (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id,
                    wait.source_version_id, wait.processing_run_id, wait.capability)
                or body["mode"] != {"mode": wait.mode.mode, "origin": wait.mode.origin}):
            raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
        binding = body["binding"]
        if ((current.dependency_connection_id is None and binding is not None)
                or (current.dependency_connection_id is not None
                    and (type(binding) is not dict or binding.get("connection_id") != current.dependency_connection_id))):
            raise _error("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", 503)
        return snapshot

    @staticmethod
    def _receipt(payload):
        fields = {"tenant_id", "workspace_id", "actor_id", "notebook_id", "source_id", "source_version_id",
                  "processing_run_id", "capability_slot", "event_id", "frozen_context_digest", "phase",
                  "outcome", "safe_code", "new_run_id", "received_at"}
        if type(payload) is not dict or set(payload) != fields:
            raise _error()
        for field in fields - {"new_run_id"}:
            if type(payload[field]) is not str:
                raise _error()
        for field in ("tenant_id", "workspace_id", "actor_id", "notebook_id", "source_id",
                      "source_version_id", "processing_run_id"):
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", payload[field]) is None:
                raise _error()
        actions = {"enqueued": "enqueue", "blocked_cost": "block_cost", "blocked_policy": "block_policy",
                   "denied": "deny", "suppressed": "suppress", "expired": "expire", "deferred": "defer"}
        action = actions.get(payload["outcome"])
        if action is None or payload["phase"] != ("deferred" if action == "defer" else "final"):
            raise _error()
        creates_run = action in ("enqueue", "block_cost", "block_policy")
        ReprocessDecision(action, payload["safe_code"], creates_run)
        if ((payload["new_run_id"] is not None) != creates_run
                or (payload["new_run_id"] is not None and (type(payload["new_run_id"]) is not str
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", payload["new_run_id"]) is None))
                or re.fullmatch(r"[0-9a-f]{64}", payload["frozen_context_digest"]) is None
                or re.fullmatch(r"rdy-[0-9a-f]{32}", payload["event_id"]) is None
                or payload["capability_slot"] not in ("unknown", "text_generation", "image_understanding", "document_parsing", "speech_to_text")):
            raise _error()
        try:
            received_at = datetime.fromisoformat(payload["received_at"])
        except ValueError:
            raise _error() from None
        if received_at.tzinfo is None or received_at.utcoffset() != timedelta(0):
            raise _error()
        return ReprocessReceipt(payload["outcome"], payload["safe_code"], payload["new_run_id"],
                                received_at)

    @staticmethod
    def _create_run(connection, context, wait, parent, current, observation, decision, grant, snapshot):
        run_id = "pr-rdy-" + _digest([wait.tenant_id, wait.workspace_id, wait.actor_id,
            wait.source_version_id, wait.capability, current.frozen_context_digest])[:32]
        blocked = decision.action != "enqueue"
        policy = connection.execute(
            "SELECT /* readiness-policy */ data_area,cost_limit_cents,acl_version FROM public.identity_auth_workspaces "
            "WHERE tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)).fetchone()
        if policy is None or policy[2] != grant.acl_version:
            raise _error("CURRENT_ACCESS_DENIED", 403)
        frozen_policy = json.loads(snapshot.frozen_context_json)["workspace_policy"]
        if (type(frozen_policy) is not dict
                or (policy[0], policy[1]) != (frozen_policy.get("data_area"), frozen_policy.get("cost_limit_cents"))):
            # 최종 snapshot 이후의 실제 정책 재조회도 같은 본문에 결속한다.
            # 동일 ACL version만으로 비용/자료 영역의 동시 변경을 허용하지 않는다.
            raise _error("SOURCE_VERSION_CONFLICT", 409)
        payload = {"source_version_id": wait.source_version_id, "notebook_id": wait.notebook_id,
            "modality": parent["modality"], "trigger_type": "readiness_event",
            "retry_of_processing_run_id": parent["run_id"], "trigger_event_id": observation.event_id,
            "readiness_context": {"version": 1, "mode": wait.mode.mode, "mode_origin": wait.mode.origin,
                                  "frozen_context_digest": snapshot.eligibility.frozen_context_digest,
                                  "frozen_context": json.loads(snapshot.frozen_context_json),
                                  "wait_processing_run_id": wait.processing_run_id},
            "authorization_snapshot": {"role": grant.role.value, "role_scope": grant.role_scope.value,
                "membership_version": grant.membership_version, "acl_version": grant.acl_version,
                "policy_version": grant.policy_version},
            "policy_snapshot": {"data_area": policy[0], "cost_limit_cents": policy[1],
                                "routing_policy_version": grant.policy_version}}
        if parent["modality"] == "audio":
            payload["asr_then_llm"] = True
        else:
            payload.update(vision_llm_first=True, parser_role="validation_only")
        text = canonical_json_bytes(payload).decode("utf-8")
        connection.execute(
            "INSERT INTO public.processing_runs (tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
            "canonical_json,canonical_text,digest_sha256,state,created_by,trace_id,source_version_id,modality,"
            "trigger_type,retry_of_processing_run_id,trigger_event_id) "
            "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,'accepted',%s,%s,%s,%s,'readiness_event',%s,%s)",
            (wait.tenant_id, wait.workspace_id, run_id, run_id, Jsonb(payload), text,
             hashlib.sha256(text.encode("utf-8")).hexdigest(), wait.actor_id, context.trace_id,
             wait.source_version_id, parent["modality"], parent["run_id"], observation.event_id))
        transition = PostgresDocumentProcessingRepository._transition
        transition(connection, context, entity_type="ProcessingRun", aggregate_id=run_id, expected_version=1,
                   target_state="policy_blocked" if blocked else ("speech_to_text" if parent["modality"] == "audio" else "vision_llm_understanding"),
                   reason_code=decision.safe_code)
        source_target = "needs_review" if blocked else "processing"
        if source_target != parent["state"]:
            source_version = parent["version"]
            if blocked and parent["state"] == "waiting_model":
                source_version = transition(connection, context, entity_type="Source", aggregate_id=wait.source_id,
                    expected_version=source_version, target_state="processing", reason_code=decision.safe_code)
            transition(connection, context, entity_type="Source", aggregate_id=wait.source_id,
                       expected_version=source_version, target_state=source_target, reason_code=decision.safe_code)
        if not blocked:
            connection.execute(
                "INSERT INTO public.document_processing_jobs (tenant_id,workspace_id,job_id,source_id,source_version_id,"
                "processing_run_id,state,created_by,trace_id,policy_version) VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s)",
                (wait.tenant_id, wait.workspace_id, "dpj-" + run_id, wait.source_id, wait.source_version_id,
                 run_id, wait.actor_id, context.trace_id, context.policy_version))
        connection.execute(
            "INSERT INTO public.audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,"
            "outcome,trace_id,policy_version,metadata) VALUES (%s,%s,%s,%s,'source.readiness_reprocess','processing_run',"
            "%s,%s,%s,%s,%s)", ("audit-" + run_id, wait.tenant_id, wait.workspace_id, wait.actor_id, run_id,
            "denied" if blocked else "succeeded", context.trace_id, context.policy_version,
            Jsonb({"retry_of_processing_run_id": parent["run_id"], "trigger_type": "readiness_event",
                   "source_version_id": wait.source_version_id, "safe_code": decision.safe_code})))
        return run_id


def vars_context(context):
    return {key: getattr(context, key) for key in ("tenant_id", "workspace_id", "actor_id", "trace_id", "policy_version")}
