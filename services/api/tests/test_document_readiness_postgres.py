"""R2 실행 adapter/SQL parameter·rollback 대역. 실제 PG/RLS/경합 증거 아님."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from daon_user_api.authorization import Role, RoleScope
from daon_user_api.data_canon import transition_allowed
from daon_user_api.document_processing import DocumentProcessingContext
from daon_user_api.document_readiness import (
    CurrentReprocessEligibility, ReadinessDispatcherSettings, ReadinessModeProof,
    RoleWaitEvidence, ValidatedReadinessObservation,
)
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.workspace_model_defaults import ReadinessPolicySnapshot


NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)


def synthetic_snapshot(current, *, mode, policy_values=("internal", 2000, 1), body=None):
    """SQL 포트용 합성 결정이다. 실제 B1 비용 확인/외부 호출 성공 근거가 아니다."""
    if body is None:
        body = {"schema_version": 1,
            "scope": {"tenant_id": current.tenant_id, "workspace_id": current.workspace_id,
                      "actor_id": current.actor_id, "notebook_id": current.notebook_id,
                      "source_version_id": current.source_version_id},
            "mode": {"mode": mode.mode, "origin": mode.origin}, "role": current.capability,
            "authorization": {"role": "editor", "role_scope": "workspace", "rules": [],
                              "actions": [{"action": "edit", "permissions": []}, {"action": "analyze", "permissions": []}]},
            "workspace_policy": {"workspace_kind": "organization", "data_area": policy_values[0],
                                 "cost_limit_cents": policy_values[1]},
            "binding": {"connection_id": current.dependency_connection_id, "model_id": "model-a", "provider_code": "CUSTOM",
                "adapter_type": "openai_compatible", "endpoint": "https://synthetic.invalid", "enabled": True,
                "verification_status": "verified", "access_mode": "public", "credential_requirement": "none",
                "credential_reference": {"source": "system", "present": False, "nonce_present": False,
                                         "valid_metadata": False, "version": 0, "schema_version": None, "key_version": None},
                "provider_kind": "external_api", "qa_scope_enabled": None, "routing_owner": "provider",
                "daon_fallback_allowed": True, "native_asr_contract": None},
            "catalog": {"schema_version": 1, "connection_id": "connection-a",
                        "models": [{"model_id": "model-a", "effective_capabilities": ["document_parsing"],
                                    "catalog_status": "ready", "allowed": True}]},
            "egress": {"organization": None, "workspace": None, "effective": None},
            "cost": {"state": current.cost_state}}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return ReadinessPolicySnapshot(replace(current, frozen_context_digest=hashlib.sha256(canonical.encode()).hexdigest()), canonical)


def with_synthetic_cost(snapshot, state):
    body = json.loads(snapshot.frozen_context_json)
    body["cost"] = {"state": state}
    return synthetic_snapshot(replace(snapshot.eligibility, cost_state=state),
                              mode=ReadinessModeProof(**body["mode"]), body=body)


def api():
    if importlib.util.find_spec("daon_user_api.document_readiness_postgres") is None:
        raise AssertionError("R2 보호 원장/원자 dispatch adapter 미구현")
    return importlib.import_module("daon_user_api.document_readiness_postgres")


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class SqlPort:
    """보호 함수 결과 대역. 실제 UNIQUE/SECDEF/RLS를 실행하지 않는다."""
    def __init__(self):
        self.calls = []
        self.wait = None
        self.event = None
        self.receipts = []
        self.runs = []
        self.jobs = []
        self.audits = []
        self.fail_audit = False
        self.deleted = False
        self.revoked = False
        self.active = False
        self.clock = NOW
        self.source_state = "waiting_model"
        self.run_states = {}
        self.latest_run_id = "run-a"
        self.discovery_rows = None
        self.wait_by_key = None
        self.read_errors = {}
        self.attempted_waits = []
        self.elapsed = 0.0
        self.query_seconds = 0.0
        self.policy_values = ("internal", 2000, 1)
        self.commits = self.rollbacks = 0

    @contextmanager
    def transaction(self):
        snapshot = copy.deepcopy((self.wait, self.event, self.receipts, self.runs, self.jobs, self.audits,
                                  self.source_state, self.run_states))
        try:
            yield self
            self.commits += 1
        except BaseException:
            (self.wait, self.event, self.receipts, self.runs, self.jobs, self.audits,
             self.source_state, self.run_states) = snapshot
            self.rollbacks += 1
            raise

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self.elapsed += self.query_seconds
        if "clock_timestamp()" in sql and sql.startswith("SELECT clock_timestamp"):
            return Result([(self.clock,)])
        if "pg_advisory_xact_lock" in sql or "set_config" in sql:
            return Result([(None,)])
        if "record_document_role_wait(" in sql:
            self.wait = params[0].obj
            return Result([(self.wait,)])
        if "record_document_readiness_observation(" in sql:
            payload = params[0].obj
            if (len(params) != 2 or json.loads(params[1]) != {k: v for k, v in payload.items() if k != "content_digest"}
                    or hashlib.sha256(params[1].encode("utf-8")).hexdigest() != payload["content_digest"]):
                raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
            if self.event is not None:
                if self.event["event_id"] == payload["event_id"]:
                    if any(self.event[k] != v for k, v in payload.items()):
                        raise DocumentUnderstandingError("IDEMPOTENCY_KEY_REUSED", status=409)
                    return Result([(self.event,)])
            self.event = {**payload, "received_at": self.clock.isoformat(), "observation_sequence": 41}
            return Result([(self.event,)])
        if "discover_document_role_waits(" in sql:
            rows = [] if self.wait is None else [(self.wait["tenant_id"], self.wait["workspace_id"],
                self.wait["actor_id"], self.wait["notebook_id"], self.wait["source_id"],
                self.wait["source_version_id"], self.wait["processing_run_id"],
                self.wait["capability"] or "unknown")]
            if self.discovery_rows is not None:
                rows = self.discovery_rows
            if len(params) in (2, 3):
                allowed = {(p["tenant_id"], p["workspace_id"], p["actor_id"], p["source_version_id"])
                           for p in params[0].obj}
                rows = [r for r in rows if (r[0], r[1], r[2], r[5]) in allowed]
            rows = sorted(rows, key=lambda r: (r[0], r[1], r[6], r[7]))
            if len(params) == 3 and params[1] is not None:
                rows = [r for r in rows if (r[0], r[1], r[6], r[7]) > tuple(params[1])]
            return Result(rows[:params[-1]])
        if "read_document_role_wait(" in sql:
            self.attempted_waits.append(tuple(params))
            if tuple(params) in self.read_errors:
                raise self.read_errors[tuple(params)]
            if self.wait_by_key is not None:
                value = self.wait_by_key.get(tuple(params))
                return Result([] if value is None else [(value,)])
            return Result([] if self.wait is None else [(self.wait,)])
        if "read_latest_document_readiness_observation(" in sql:
            return Result([] if self.event is None else [(self.event,)])
        if "/* readiness-parent */" in sql:
            return Result([] if self.deleted else [({"state": self.source_state, "version": 4,
                "run_id": self.latest_run_id, "run_state": self.run_states.get(self.latest_run_id, "failed"), "source_version_id": "version-a",
                "canonical_json": {"notebook_id": "notebook-a"}, "modality": "document"},)])
        if "/* readiness-active */" in sql:
            return Result([(1,)] if self.active else [])
        if "/* readiness-policy */" in sql:
            return Result([self.policy_values])
        if "/* readiness-history */" in sql:
            return Result([(r,) for r in self.receipts if r["frozen_context_digest"] == params[-1]])
        if "read_document_blocked_parent(" in sql:
            return Result([(any(r["new_run_id"] == params[-1] and r["outcome"] in ("blocked_cost", "blocked_policy")
                               for r in self.receipts),)])
        if "SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access" in sql:
            return Result([(not self.revoked,)])
        if "INSERT INTO public.processing_runs" in sql:
            self.runs.append(params)
            self.run_states[params[2]] = "accepted"
            return Result()
        if "INSERT INTO public.document_processing_jobs" in sql:
            self.jobs.append(params)
            return Result()
        if "transition_canon_state(" in sql:
            state = self.source_state if params[0] == "Source" else self.run_states[params[1]]
            if not transition_allowed(params[0], state, params[3]):
                return Result([(state, params[2], "failed", "CANON_TRANSITION_INVALID")])
            if params[0] == "Source":
                self.source_state = params[3]
            else:
                self.run_states[params[1]] = params[3]
            return Result([(params[3], params[2] + 1, "succeeded", None)])
        if "INSERT INTO public.audit_events" in sql:
            if self.fail_audit:
                raise RuntimeError("synthetic sensitive response must not escape")
            self.audits.append(params)
            return Result()
        if "record_document_reprocess_receipt(" in sql:
            value = params[0].obj
            if value["outcome"] == "suppressed":
                first = next((r for r in self.receipts if r["outcome"] == "suppressed"
                              and r["safe_code"] == value["safe_code"]
                              and r["frozen_context_digest"] == value["frozen_context_digest"]), None)
                if first is not None:
                    return Result([(first,)])
            key_fields = ("tenant_id", "workspace_id", "processing_run_id", "capability_slot", "event_id", "phase")
            existing = next((r for r in self.receipts if all(r[k] == value[k] for k in key_fields)), None)
            if existing:
                if ({k: v for k, v in existing.items() if k != "received_at"}
                        != {k: v for k, v in value.items() if k != "received_at"}):
                    raise DocumentUnderstandingError("IDEMPOTENCY_KEY_REUSED", status=409)
                return Result([(existing,)])
            self.receipts.append(value)
            return Result([(value,)])
        raise AssertionError("대역에 정의되지 않은 SQL: " + sql[:90])


class DocumentReadinessPostgresTests(unittest.TestCase):
    def test_new_auto_run_stores_full_frozen_context_and_original_wait_run(self):
        self.seed()
        result = self.repo.dispatch_ready()[0]
        self.assertEqual(result.outcome, "enqueued")
        proof = self.db.runs[0][4].obj["readiness_context"]
        self.assertIn("frozen_context", proof, "digest만 저장하고 본문이 누락됨")
        self.assertEqual(proof["wait_processing_run_id"], "run-a")
        self.assertEqual(proof["frozen_context"]["scope"], {"tenant_id": "tenant-a", "workspace_id": "workspace-a",
            "actor_id": "actor-a", "notebook_id": "notebook-a", "source_version_id": "version-a"})
        self.assertEqual(proof["frozen_context"]["mode"], {"mode": "auto", "origin": "request"})
        self.assertEqual(proof["frozen_context"]["role"], "document_parsing")
        self.assertEqual(proof["frozen_context"]["cost"], {"state": "allowed"})
        self.assertEqual(hashlib.sha256(self.db.runs[0][5].encode()).hexdigest(), self.db.runs[0][6])
        self.assertEqual(self.db.receipts[0]["new_run_id"], result.new_run_id)
        self.assertEqual(proof["frozen_context_digest"], self.db.receipts[0]["frozen_context_digest"])
        frozen_text = json.dumps(proof["frozen_context"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(frozen_text.encode()).hexdigest(), proof["frozen_context_digest"])

    def test_current_only_policy_port_is_not_a_full_snapshot_and_must_fail_closed(self):
        self.seed()
        def current_only(*args):
            result = self.policy(*args)
            return getattr(result, "eligibility", result)
        with self.assertRaises(DocumentUnderstandingError):
            self.repository(policy=current_only).dispatch_ready()
        self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))

    def test_full_snapshot_must_match_wait_mode_role_scope_and_current_binding(self):
        for changed in ("mode", "origin", "role", "scope", "binding", "binding_missing", "digest"):
            with self.subTest(changed=changed):
                self.setUp()
                self.seed()
                def invalid(*args):
                    snapshot = self.policy(*args)
                    current = snapshot.eligibility
                    body = json.loads(snapshot.frozen_context_json)
                    if changed == "mode": body["mode"]["mode"] = "pinned"
                    if changed == "origin": body["mode"]["origin"] = "legacy_unknown"
                    if changed == "role":
                        current = replace(current, capability="speech_to_text")
                        body["role"] = "speech_to_text"
                    if changed == "scope":
                        current = replace(current, actor_id="other-actor")
                        body["scope"]["actor_id"] = "other-actor"
                    if changed == "binding": body["binding"]["connection_id"] = "other-connection"
                    if changed == "binding_missing": body["binding"] = None
                    result = synthetic_snapshot(current, mode=self.wait.mode, body=body)
                    if changed == "digest":
                        object.__setattr__(result, "eligibility", replace(current, frozen_context_digest="f" * 64))
                    return result
                with self.assertRaises(DocumentUnderstandingError) as caught:
                    self.repository(policy=invalid).dispatch_ready()
                self.assertEqual(caught.exception.status, 503)
                self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))

    def test_absent_reader_fails_closed_and_default_scope_never_reads_policy(self):
        self.seed()
        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repository(policy=None).dispatch_ready()
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))
        self.repo.settings = ReadinessDispatcherSettings()
        self.assertEqual(self.repo.dispatch_ready(), ())
        self.assertEqual(self.policy_calls, [])

    def test_current_execution_permission_denial_is_not_overwritten_by_edit_analyze_grant(self):
        self.seed()
        def denied(*args):
            snapshot = self.policy(*args)
            return synthetic_snapshot(replace(snapshot.eligibility, authorized=False, local_only_allowed=False),
                                      mode=self.wait.mode)
        result = self.repository(policy=denied).dispatch_ready()[0]
        self.assertEqual((result.outcome, result.safe_code, result.new_run_id), ("denied", "READINESS_ACCESS_DENIED", None))
        self.assertEqual((self.db.runs, self.db.jobs, self.db.audits), ([], [], []))
        self.assertEqual(self.db.source_state, "waiting_model")

    def test_new_condition_after_block_preserves_original_wait_ref_and_latest_retry_parent(self):
        self.seed()
        blocked = self.repository(policy=lambda *args: with_synthetic_cost(self.policy(*args), "blocked")).dispatch_ready()[0]
        self.db.latest_run_id = blocked.new_run_id
        self.db.policy_values = ("internal", 3000, 1)
        self.repo.record_observation(replace(self.observation, event_id="rdy-" + "2" * 32))
        result = self.repository().dispatch_ready()[0]
        self.assertEqual((result.outcome, len(self.db.runs), len(self.db.jobs)), ("enqueued", 2, 1))
        payload = self.db.runs[1][4].obj
        self.assertEqual(payload["retry_of_processing_run_id"], blocked.new_run_id)
        self.assertEqual(payload["readiness_context"]["wait_processing_run_id"], "run-a")
        self.assertEqual(payload["trigger_event_id"], "rdy-" + "2" * 32)
        self.assertEqual(payload["readiness_context"]["frozen_context"]["workspace_policy"]["cost_limit_cents"], 3000)
        before = json.dumps((self.db.runs, self.db.jobs, self.db.audits, self.db.receipts), sort_keys=True, default=lambda value: value.obj)
        self.assertEqual(self.repository().dispatch_ready()[0], result)
        self.assertEqual(json.dumps((self.db.runs, self.db.jobs, self.db.audits, self.db.receipts),
                                   sort_keys=True, default=lambda value: value.obj), before)

    def setUp(self):
        self.m = api()
        self.db = SqlPort()
        self.context = DocumentProcessingContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a")
        self.wait = RoleWaitEvidence("tenant-a", "workspace-a", "actor-a", "notebook-a", "source-a",
            "version-a", "run-a", "document_parsing", "connection_availability",
            ReadinessModeProof("auto", "request"), "connection-a", "a" * 64,
            "DOCUMENT_MODEL_UNAVAILABLE", NOW - timedelta(seconds=180), NOW - timedelta(seconds=120))
        self.observation = ValidatedReadinessObservation(1, "rdy-" + "1" * 32, "connection-a", "CUSTOM",
            "document_parsing", "b" * 64, 3, NOW - timedelta(seconds=90), NOW, "model_catalog_refresh",
            "healthy", 999, "c" * 64)
        self.policy_calls = []

        def policy(connection, context, wait, grant):
            self.policy_calls.append((connection, context.actor_id, wait.source_version_id))
            current = CurrentReprocessEligibility("tenant-a", "workspace-a", "actor-a", "notebook-a",
                "version-a", "document_parsing", "a" * 64, "connection-a", "b" * 64,
                3, 41, True, True, True, True, True, True, False, False, False,
                "allowed", "allowed", wait.processing_run_id)
            return synthetic_snapshot(current, mode=wait.mode, policy_values=self.db.policy_values)

        self.policy = policy
        self.auth = patch.object(self.m, "_current_auth_grant", side_effect=self.grant)
        self.special = patch.object(self.m, "_special_source_access_allowed", return_value=True)
        self.organization = patch.object(self.m.OrganizationAccessBridge, "assert_organization_active")
        self.auth.start(); self.special.start(); self.organization.start()
        self.addCleanup(self.auth.stop); self.addCleanup(self.special.stop); self.addCleanup(self.organization.stop)
        self.settings = ReadinessDispatcherSettings(frozenset({("tenant-a", "workspace-a", "actor-a", "version-a")}))
        self.store = SimpleNamespace(_transaction=lambda context: self.db.transaction())
        self.repo = self.repository()

    def grant(self, context, connection, action):
        if self.db.revoked:
            raise self.m.OpenLawAccessError("OPEN_LAW_ACCESS_DENIED")
        return SimpleNamespace(role=Role.EDITOR, policy_version="tenant-1:workspace-1", acl_version=1,
                               membership_version=1, actor_id=context.actor_id, role_scope=RoleScope.WORKSPACE)

    def repository(self, **kwargs):
        return self.m.PostgresDocumentReadinessRepository(self.store, settings=self.settings,
            discovery_transaction=self.db.transaction, current_policy_reader=kwargs.get("policy", self.policy))

    def seed(self):
        with self.db.transaction():
            self.repo.record_wait(self.db, self.context, "run-a", self.wait)
        self.repo.record_observation(self.observation)

    def test_event_wait_and_context_uniqueness_survive_restart(self):
        self.seed()
        first = self.repo.dispatch_ready()
        second = self.repository().dispatch_ready()
        self.assertEqual(first[0].outcome, "enqueued")
        self.assertEqual(second[0].outcome, "enqueued")
        self.assertEqual((len(self.db.runs), len(self.db.jobs), len(self.db.audits)), (1, 1, 1))
        self.assertEqual(self.db.runs[0][0:2], ("tenant-a", "workspace-a"))
        self.assertIn("'readiness_event'", next(sql for sql, _ in self.db.calls if "INSERT INTO public.processing_runs" in sql))
        self.assertTrue(any("retention-source|tenant-a|workspace-a|source-a" in params for _, params in self.db.calls))

    def test_two_consumers_create_one_run_job_and_receipt(self):
        # 같은 영속 결과의 두 adapter 소비. 실제 PG 동시 경합은 별도 NOTRUN.
        self.seed()
        self.repo.dispatch_ready()
        self.repository().dispatch_ready()
        self.assertEqual((len(self.db.runs), len(self.db.jobs), len(self.db.receipts)), (1, 1, 1))
        locks = [params for sql, params in self.db.calls if "pg_advisory_xact_lock" in sql]
        self.assertTrue(any(params[0].startswith("document-readiness-context|") for params in locks))

    def test_other_event_same_consumed_context_records_first_suppression_once(self):
        self.seed()
        self.repo.dispatch_ready()
        self.repo.record_observation(replace(self.observation, event_id="rdy-" + "2" * 32))
        self.assertEqual(self.repo.dispatch_ready(), ())  # 빈 page 다음 poll에 재순회한다.
        suppressed = self.repo.dispatch_ready()[0]
        self.assertEqual((suppressed.outcome, suppressed.safe_code, suppressed.new_run_id),
                         ("suppressed", "READINESS_CONTEXT_SUPPRESSED", None))
        self.db.clock = NOW + timedelta(seconds=1)
        self.repo.record_observation(replace(self.observation, event_id="rdy-" + "3" * 32))
        self.assertEqual(self.repo.dispatch_ready(), ())
        replay = self.repo.dispatch_ready()[0]
        self.assertEqual(replay.received_at, NOW)
        self.assertEqual((len(self.db.runs), len(self.db.jobs), len(self.db.receipts)), (1, 1, 2))

    def test_active_run_suppression_does_not_consume_context_forever(self):
        self.seed()
        self.db.active = True
        self.assertEqual(self.repo.dispatch_ready()[0].safe_code, "READINESS_ACTIVE_RUN_EXISTS")
        self.db.active = False
        self.repo.record_observation(replace(self.observation, event_id="rdy-" + "2" * 32))
        self.assertEqual(self.repo.dispatch_ready(), ())
        self.assertEqual(self.repo.dispatch_ready()[0].outcome, "enqueued")
        self.assertEqual((len(self.db.runs), len(self.db.jobs)), (1, 1))

    def test_atomic_rollback_has_no_partial_ack(self):
        self.seed()
        self.db.fail_audit = True
        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repo.dispatch_ready()
        self.assertEqual(str(caught.exception), "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE")
        self.assertEqual((self.db.runs, self.db.jobs, self.db.audits, self.db.receipts), ([], [], [], []))
        self.assertEqual(self.db.rollbacks, 1)

    def test_blocked_run_uses_real_canon_path_without_job_and_stores_current_snapshots(self):
        self.seed()

        def blocked(*args):
            return with_synthetic_cost(self.policy(*args), "blocked")

        result = self.repository(policy=blocked).dispatch_ready()[0]
        self.assertEqual((result.outcome, len(self.db.runs), len(self.db.jobs), self.db.source_state),
                         ("blocked_cost", 1, 0, "needs_review"))
        transitions = [params for sql, params in self.db.calls
                       if "transition_canon_state(" in sql and params[0] == "Source"]
        self.assertEqual([(p[2], p[3]) for p in transitions], [(4, "processing"), (5, "needs_review")])
        self.assertEqual([p[7] for p in transitions], ["tenant-1:workspace-1", "tenant-1:workspace-1"])
        payload = self.db.runs[0][4].obj
        self.assertEqual(payload["authorization_snapshot"], {"role": "editor", "role_scope": "workspace",
            "membership_version": 1, "acl_version": 1, "policy_version": "tenant-1:workspace-1"})
        self.assertEqual(payload["policy_snapshot"], {"data_area": "internal", "cost_limit_cents": 2000,
                                                     "routing_policy_version": "tenant-1:workspace-1"})

    def test_current_owner_membership_source_policy_rechecked(self):
        self.seed()
        self.db.revoked = True
        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repo.dispatch_ready()
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual((self.db.runs, self.db.jobs), ([], []))
        self.db.revoked = False

        def revoke_after_policy(*args):
            result = self.policy(*args)
            self.db.revoked = True
            return result

        with self.assertRaises(DocumentUnderstandingError):
            self.repository(policy=revoke_after_policy).dispatch_ready()
        self.assertEqual((self.db.runs, self.db.jobs), ([], []))

    def test_explicit_legacy_rows_are_not_rewritten(self):
        self.wait = replace(self.wait, mode=ReadinessModeProof("legacy_unknown", "legacy_unknown"))
        self.seed()
        result = self.repo.dispatch_ready()
        self.assertEqual((result[0].outcome, result[0].safe_code), ("deferred", "READINESS_MODE_UNCONFIRMED"))
        self.assertEqual((self.db.runs, self.db.jobs), ([], []))
        self.assertFalse(any(sql.lstrip().upper().startswith("UPDATE") for sql, _ in self.db.calls))

    def test_app_direct_mutation_and_cross_scope_are_denied(self):
        # 보호 함수 경유/정확 scope parameters를 검사한다. 실제 GRANT/RLS 시험 아님.
        other = replace(self.wait, actor_id="actor-b")
        with self.assertRaises(DocumentUnderstandingError):
            self.repo.record_wait(self.db, self.context, "run-a", other)
        self.assertEqual(self.db.calls, [])
        self.seed()
        self.assertTrue(any("record_document_role_wait(" in sql for sql, _ in self.db.calls))
        self.assertFalse(any("INSERT INTO public.document_role_waits" in sql for sql, _ in self.db.calls))

    def test_observation_server_sequence_time_replay_and_changed_content(self):
        first = self.repo.record_observation(self.observation)
        self.assertEqual((first.observation_sequence, first.received_at), (41, NOW))
        self.db.clock = NOW + timedelta(seconds=1)
        second = self.repo.record_observation(self.observation)
        self.assertEqual((second.observation_sequence, second.received_at), (41, NOW))
        with self.assertRaises(DocumentUnderstandingError):
            self.repo.record_observation(replace(self.observation, status="not_ready"))
        payload = next(params[0].obj for sql, params in self.db.calls if "record_document_readiness_observation(" in sql)
        self.assertNotIn("observation_sequence", payload)
        self.assertNotIn("received_at", payload)

    def test_delete_first_and_disabled_scope_have_no_new_business_write(self):
        self.seed()
        self.db.deleted = True
        with self.assertRaises(DocumentUnderstandingError):
            self.repo.dispatch_ready()
        self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))
        self.repo.settings = ReadinessDispatcherSettings()
        self.assertEqual(self.repo.dispatch_ready(), ())

    def test_new_manual_or_other_latest_run_never_inherits_old_auto_mode(self):
        self.seed()
        self.db.latest_run_id = "new-manual-pinned-run"
        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repo.dispatch_ready()
        self.assertEqual(caught.exception.code, "SOURCE_REPROCESSING_STATE_INVALID")
        self.assertEqual((self.db.runs, self.db.jobs), ([], []))

    def test_server_scope_is_passed_to_discovery_before_batch_limit(self):
        self.seed()
        own = ("tenant-a", "workspace-a", "actor-a", "notebook-a", "source-a", "version-a", "run-a", "document_parsing")
        other = ("tenant-x", "workspace-x", "actor-x", "notebook-x", "source-x", "version-x", "run-x", "document_parsing")
        self.db.discovery_rows = [other] * 33 + [own]
        result = self.repo.dispatch_ready()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].outcome, "enqueued")
        params = next(params for sql, params in self.db.calls if "discover_document_role_waits(" in sql)
        self.assertEqual(params[0].obj, [{"tenant_id": "tenant-a", "workspace_id": "workspace-a",
                                        "actor_id": "actor-a", "source_version_id": "version-a"}])
        self.assertIsNone(params[1])
        self.assertEqual(params[2], 32)

    def fair_page(self, mode=None):
        self.seed()
        self.db.wait_by_key = {}
        self.db.discovery_rows = []
        for i in range(32):
            run = f"run-{i:03d}"
            self.db.discovery_rows.append(("tenant-a", "workspace-a", "actor-a", "notebook-a",
                                           "source-a", "version-a", run, "document_parsing"))
            if mode is not None:
                wait = replace(self.wait, processing_run_id=run, mode=mode)
                self.db.wait_by_key[("tenant-a", "workspace-a", run, "document_parsing")] = self.m._wait_payload(wait)
        self.db.discovery_rows.append(("tenant-a", "workspace-a", "actor-a", "notebook-a",
                                       "source-a", "version-a", "run-a", "document_parsing"))
        self.db.wait_by_key[("tenant-a", "workspace-a", "run-a", "document_parsing")] = self.db.wait

    def test_deleted_first_thirty_two_waits_do_not_starve_thirty_third(self):
        self.fair_page()
        self.assertEqual(self.repo.dispatch_ready(), ())
        result = self.repo.dispatch_ready()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].outcome, "enqueued")
        self.assertEqual((len(self.db.runs), len(self.db.jobs)), (1, 1))
        params = [p for sql, p in self.db.calls if "discover_document_role_waits(" in sql]
        self.assertEqual(params[0][1], None)
        self.assertEqual(params[1][1], ["tenant-a", "workspace-a", "run-031", "document_parsing"])
        self.assertEqual([p[-1] for p in params], [32, 32])
        self.assertEqual(self.repo.dispatch_ready(), ())  # 빈 page에서 재탐색하지 않는다.
        self.assertEqual(self.repo.dispatch_ready(), ())  # 다음 poll부터 첫 page로 wrap.
        self.assertEqual(self.repo.dispatch_ready()[0].outcome, "enqueued")
        self.assertEqual((len(self.db.runs), len(self.db.jobs)), (1, 1))
        restarted = self.repository()
        self.assertEqual(restarted.dispatch_ready(), ())
        self.assertEqual(restarted.dispatch_ready()[0].outcome, "enqueued")
        self.assertEqual((len(self.db.runs), len(self.db.jobs)), (1, 1))

    def test_persistent_pinned_or_deferred_page_does_not_starve_next_candidate(self):
        for mode, expected in ((ReadinessModeProof("pinned", "request"), "READINESS_MODE_MANUAL"),
                               (ReadinessModeProof("legacy_unknown", "legacy_unknown"), "READINESS_MODE_UNCONFIRMED")):
            with self.subTest(mode=mode.mode):
                self.db = SqlPort()
                self.repo = self.repository()
                self.fair_page(mode)
                first = self.repo.dispatch_ready()
                self.assertEqual(len(first), 32)
                self.assertTrue(all(r.safe_code == expected and r.new_run_id is None for r in first))
                self.assertEqual((self.db.runs, self.db.jobs), ([], []))
                second = self.repo.dispatch_ready()
                self.assertEqual(len(second), 1)
                self.assertEqual(second[0].outcome, "enqueued")
                self.assertEqual((len(self.db.runs), len(self.db.jobs)), (1, 1))

    def test_error_advances_only_attempted_row_without_ack_and_restart_rediscovers(self):
        for failure in (DocumentUnderstandingError("CURRENT_ACCESS_DENIED", status=403), RuntimeError("private db failure")):
            with self.subTest(status=getattr(failure, "status", 503)):
                self.db = SqlPort()
                self.repo = self.repository()
                self.fair_page()
                first_key = ("tenant-a", "workspace-a", "run-000", "document_parsing")
                self.db.read_errors[first_key] = failure
                with self.assertRaises(DocumentUnderstandingError) as caught:
                    self.repo.dispatch_ready()
                self.assertEqual(caught.exception.status, getattr(failure, "status", 503))
                self.assertEqual(self.db.attempted_waits, [first_key])
                self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))
                self.assertEqual(self.db.rollbacks, 1)
                second = self.repo.dispatch_ready()
                self.assertEqual(len(second), 1)
                self.assertEqual(second[0].outcome, "enqueued")
                self.assertEqual(len(self.db.attempted_waits), 33)
                self.assertEqual(self.db.attempted_waits[1], ("tenant-a", "workspace-a", "run-001", "document_parsing"))
                params = [p for sql, p in self.db.calls if "discover_document_role_waits(" in sql]
                self.assertEqual(params[1][1], ["tenant-a", "workspace-a", "run-000", "document_parsing"])
                self.assertEqual(self.repo.dispatch_ready(), ())
                with self.assertRaises(DocumentUnderstandingError):
                    self.repo.dispatch_ready()  # wrap도 오류 행을 ack/삭제하지 않는다.
                with self.assertRaises(DocumentUnderstandingError):
                    self.repository().dispatch_ready()
                self.assertEqual((len(self.db.runs), len(self.db.jobs)), (1, 1))

    def test_discovery_cursor_sql_signature_primary_key_order_and_scope_limit(self):
        # SQL 구조 증거이며 PostgreSQL 함수 실행/권한 시험은 아니다.
        folder = Path(__file__).resolve().parents[1] / "migrations" / "versions"
        sql = (folder / "0072_document_readiness.sql").read_text(encoding="utf-8")
        wrapper = (folder / "0072_document_readiness.py").read_text(encoding="utf-8")
        discovery = sql.split("CREATE FUNCTION public.discover_document_role_waits(", 1)[1].split("END $$;", 1)[0]
        self.assertTrue(discovery.startswith("scopes jsonb,after_cursor text[],maximum integer)"))
        self.assertIn("array_ndims(after_cursor) IS DISTINCT FROM 1", discovery)
        self.assertIn("array_lower(after_cursor,1) IS DISTINCT FROM 1", discovery)
        self.assertIn("cardinality(after_cursor)<>4", discovery)
        self.assertIn("v IS NULL", discovery)
        self.assertIn("maximum NOT BETWEEN 1 AND 32", discovery)
        self.assertIn("(x.tenant_id,x.workspace_id,x.actor_id,x.source_version_id))\n AND (after_cursor IS NULL", discovery)
        self.assertIn("(x.tenant_id,x.workspace_id,x.processing_run_id,x.capability_slot)>\n"
                      "      (after_cursor[1],after_cursor[2],after_cursor[3],after_cursor[4])", discovery)
        self.assertIn("ORDER BY x.tenant_id,x.workspace_id,x.processing_run_id,x.capability_slot LIMIT maximum", discovery)
        self.assertNotIn("OFFSET", discovery)
        self.assertEqual(sql.count("'discover_document_role_waits(jsonb,text[],integer)'"), 2)
        self.assertIn("DROP FUNCTION public.discover_document_role_waits(jsonb,text[],integer);", wrapper)
        self.assertNotIn("discover_document_role_waits(jsonb,integer)", sql + wrapper)

    def test_receipt_exact_fifteen_fields_reject_extra_missing_and_wrong_types(self):
        payload = {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "actor_id": "actor-a",
            "notebook_id": "notebook-a", "source_id": "source-a", "source_version_id": "version-a",
            "processing_run_id": "run-a", "capability_slot": "document_parsing", "event_id": "rdy-" + "1" * 32,
            "frozen_context_digest": "a" * 64, "phase": "final", "outcome": "suppressed",
            "safe_code": "READINESS_CONTEXT_SUPPRESSED", "new_run_id": None, "received_at": NOW.isoformat()}
        self.assertEqual(self.repo._receipt(payload).outcome, "suppressed")
        for invalid in ({**payload, "extra": "not allowed"},
                        {k: v for k, v in payload.items() if k != "actor_id"},
                        {**payload, "tenant_id": True}, {**payload, "received_at": False},
                        {**payload, "new_run_id": "must-not-exist-for-suppression"}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DocumentUnderstandingError):
                    self.repo._receipt(invalid)

    def test_policy_change_after_semantic_lock_refuses_old_decision(self):
        self.seed()
        reads = 0

        def changed(*args):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.db.policy_values = ("internal", 3000, 1)
            return self.policy(*args)

        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repository(policy=changed).dispatch_ready()
        self.assertEqual(caught.exception.code, "SOURCE_VERSION_CONFLICT")
        self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))
        self.assertEqual(reads, 2)

    def test_absolute_budget_rolls_back_many_individually_fast_queries(self):
        self.seed()
        self.db.query_seconds = 0.15
        with patch.object(self.m.time, "monotonic", side_effect=lambda: self.db.elapsed):
            with self.assertRaises(DocumentUnderstandingError) as caught:
                self.repo.dispatch_ready()
        self.assertEqual(caught.exception.code, "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE")
        self.assertEqual((self.db.runs, self.db.jobs, self.db.receipts), ([], [], []))
        timeouts = [int(params[0]) for sql, params in self.db.calls
                    if "statement_timeout" in sql]
        self.assertGreater(len(timeouts), 2)
        self.assertLess(timeouts[-1], timeouts[0])

    def test_run_policy_reread_must_match_frozen_body_before_any_business_write(self):
        for changed in (("other-area", 2000, 1), ("internal", 3000, 1)):
            with self.subTest(changed=changed):
                self.setUp()
                self.seed()
                original_execute = self.db.execute

                def execute(sql, params=()):
                    if "/* readiness-policy */" in sql:
                        return Result([changed])
                    return original_execute(sql, params)

                self.db.execute = execute
                with self.assertRaises(DocumentUnderstandingError) as caught:
                    self.repo.dispatch_ready()
                self.assertEqual(caught.exception.code, "SOURCE_VERSION_CONFLICT")
                self.assertEqual((self.db.runs, self.db.jobs, self.db.audits, self.db.receipts),
                                 ([], [], [], []))
                self.assertEqual(self.db.source_state, "waiting_model")

    def test_observation_passes_exact_compact_canonical_body_for_sql_hash_check(self):
        self.repo.record_observation(self.observation)
        params = next(params for sql, params in self.db.calls
                      if "record_document_readiness_observation(" in sql)
        self.assertEqual(len(params), 2)
        self.assertEqual(params[1], '{"adapter_type":"CUSTOM","capability":"document_parsing",'
            '"configuration_version":3,"connection_id":"connection-a","event_id":"rdy-11111111111111111111111111111111",'
            '"model_catalog_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            '"observed_at":"2026-10-19T09:58:30+00:00","probe_kind":"model_catalog_refresh","schema_version":1,"status":"healthy"}')

    def test_same_event_final_different_context_rolls_back_new_business_rows(self):
        self.seed()
        self.repository(policy=lambda *args: with_synthetic_cost(self.policy(*args), "blocked")).dispatch_ready()
        self.db.latest_run_id = self.db.runs[0][2]
        self.db.policy_values = ("internal", 3000, 1)

        def changed(*args):
            return self.policy(*args)

        with self.assertRaises(DocumentUnderstandingError) as caught:
            self.repository(policy=changed).dispatch_ready()
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual((len(self.db.runs), len(self.db.jobs), len(self.db.receipts)), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
