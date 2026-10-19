"""R1 순수 타입/판정 시험: DB 인가·원자 재큐·HTTP 멱등 시험이 아니다."""
from __future__ import annotations

import importlib
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from daon_user_api.document_understanding_adapter import DocumentUnderstandingError


NOW = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)


def api():
    if importlib.util.find_spec("daon_user_api.document_readiness") is None:
        raise AssertionError("R1 닫힌 타입/순수 readiness 판정 미구현")
    return importlib.import_module("daon_user_api.document_readiness")


class DocumentReadinessTests(unittest.TestCase):
    def setUp(self):
        self.m = api()
        self.wait = self.m.RoleWaitEvidence(
            tenant_id="tenant-a", workspace_id="workspace-a", actor_id="actor-a",
            notebook_id="notebook-a", source_id="source-a", source_version_id="version-a",
            processing_run_id="run-a", capability="document_parsing",
            failure_kind="connection_availability",
            mode=self.m.ReadinessModeProof("auto", "request"),
            dependency_connection_id="connection-a", frozen_context_digest="a" * 64,
            safe_error_code="DOCUMENT_MODEL_UNAVAILABLE",
            failed_at=NOW - timedelta(seconds=180), not_before=NOW - timedelta(seconds=120),
        )
        self.observation = self.m.ValidatedReadinessObservation(
            schema_version=1, event_id="rdy-" + "1" * 32, connection_id="connection-a",
            adapter_type="CUSTOM", capability="document_parsing",
            model_catalog_digest="b" * 64, configuration_version=3,
            observed_at=NOW - timedelta(seconds=90), received_at=NOW - timedelta(seconds=89),
            probe_kind="model_catalog_refresh", status="healthy",
            observation_sequence=7, content_digest="c" * 64,
        )
        self.current = self.m.CurrentReprocessEligibility(
            tenant_id="tenant-a", workspace_id="workspace-a", actor_id="actor-a",
            notebook_id="notebook-a", source_version_id="version-a",
            capability="document_parsing", frozen_context_digest="a" * 64,
            dependency_connection_id="connection-a", model_catalog_digest="b" * 64,
            configuration_version=3, latest_observation_sequence=7,
            scope_enabled=True, authorized=True, source_current=True, policy_allowed=True,
            local_only_allowed=True, candidate_available=True, active_run=False,
            context_already_decided=False, cost_blocked_context_exists=False,
            cost_state="allowed", egress_state="allowed", prior_run_id="run-a",
        )

    def decision(self, want, *, wait=None, observation=None, current=None, now=NOW):
        result = self.m.decide_reprocess(
            self.wait if wait is None else wait,
            self.observation if observation is None else observation,
            self.current if current is None else current, db_now=now,
        )
        self.assertEqual((result.action, result.safe_code, result.create_run), want)

    def invalid(self, callable_, *args, **kwargs):
        with self.assertRaises(DocumentUnderstandingError) as error:
            callable_(*args, **kwargs)
        self.assertEqual(error.exception.code, "DOCUMENT_PROCESSING_CONTEXT_INVALID")
        self.assertEqual(str(error.exception), "DOCUMENT_PROCESSING_CONTEXT_INVALID")

    def test_missing_mode_and_unknown_role_never_autoqueue(self):
        self.decision(("defer", "READINESS_MODE_UNCONFIRMED", False), wait=replace(
            self.wait, mode=self.m.ReadinessModeProof("legacy_unknown", "legacy_unknown")))
        self.decision(("defer", "READINESS_ROLE_UNKNOWN", False), wait=replace(self.wait, capability=None))
        self.decision(("defer", "READINESS_ROLE_UNKNOWN", False), current=replace(self.current, capability=None))
        for mode, origin in (("auto", "legacy_unknown"), ("legacy_unknown", "request"),
                             (None, "request"), ("unknown", "request"), ("auto", None)):
            with self.subTest(mode=mode, origin=origin):
                self.invalid(self.m.ReadinessModeProof, mode, origin)

    def test_event_is_real_matching_dependency_not_enable_flag(self):
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True))
        absent = self.m.decide_reprocess(self.wait, None, self.current, db_now=NOW)
        self.assertEqual((absent.action, absent.safe_code, absent.create_run),
                         ("defer", "READINESS_NOT_READY", False))
        for updates in ({"connection_id": "connection-b"}, {"configuration_version": 4},
                        {"model_catalog_digest": "d" * 64}, {"observation_sequence": 6},
                        {"capability": "text_generation"}):
            with self.subTest(updates=updates):
                self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False),
                              observation=replace(self.observation, **updates))
        for updates in ({"dependency_connection_id": None}, {"model_catalog_digest": None},
                        {"configuration_version": 0}, {"latest_observation_sequence": 0}):
            with self.subTest(current=updates):
                self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False),
                              current=replace(self.current, **updates))
        for status in ("not_ready", "unavailable"):
            self.decision(("defer", "READINESS_NOT_READY", False),
                          observation=replace(self.observation, status=status))
        self.decision(("defer", "READINESS_NOT_READY", False),
                      current=replace(self.current, candidate_available=False))
        self.invalid(self.m.decide_reprocess, self.wait, True, self.current, db_now=NOW)

    def test_connection_only_signal_matches_recorded_dependency_and_failure_kind(self):
        signal = replace(self.observation, capability=None, probe_kind="provider_connection_check")
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True), observation=signal)
        self.assertIsNone(signal.capability)  # NULL 계기는 역할-ready 증거로 승격되지 않는다.
        for kind in ("role_runtime", "non_availability", "unknown"):
            self.decision(("defer", "READINESS_NOT_READY", False),
                          observation=signal, wait=replace(self.wait, failure_kind=kind))
        for dependency in (None, "connection-b"):
            self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False),
                          observation=signal, wait=replace(self.wait, dependency_connection_id=dependency))
        self.decision(("defer", "READINESS_ROLE_UNKNOWN", False), observation=signal,
                      wait=replace(self.wait, capability=None))

    def test_unknown_and_non_availability_never_create_automatic_run(self):
        for kind in ("unknown", "non_availability"):
            with self.subTest(kind=kind):
                self.decision(("defer", "READINESS_NOT_READY", False),
                              wait=replace(self.wait, failure_kind=kind))
                self.decision(("defer", "READINESS_NOT_READY", False),
                              wait=replace(self.wait, failure_kind=kind),
                              current=replace(self.current, cost_state="blocked"))
        runtime = replace(self.wait, failure_kind="role_runtime")
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True), wait=runtime)
        self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False), wait=runtime,
                      observation=replace(self.observation, capability="text_generation"))
        self.decision(("defer", "READINESS_NOT_READY", False), wait=runtime,
                      observation=replace(self.observation, capability=None,
                                          probe_kind="provider_connection_check"))

    def test_probe_kind_only_accepts_its_actually_confirmed_capability(self):
        for role in ("text_generation", "image_understanding", "document_parsing", "speech_to_text"):
            with self.subTest(probe="provider_connection_check", role=role):
                self.invalid(replace, self.observation, probe_kind="provider_connection_check", capability=role)
        for role in (None, "text_generation", "image_understanding", "document_parsing"):
            with self.subTest(probe="native_asr_ready", role=role):
                self.invalid(replace, self.observation, probe_kind="native_asr_ready", capability=role)
        connection = replace(self.observation, probe_kind="provider_connection_check", capability=None)
        self.assertIsNone(connection.capability)
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True), observation=connection)
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True),
                      wait=replace(self.wait, capability="speech_to_text"),
                      observation=replace(self.observation, probe_kind="native_asr_ready", capability="speech_to_text"),
                      current=replace(self.current, capability="speech_to_text"))
        for role in ("text_generation", "image_understanding", "document_parsing", "speech_to_text", None):
            with self.subTest(probe="model_catalog_refresh", role=role):
                catalog = replace(self.observation, capability=role)
                self.assertEqual(catalog.probe_kind, "model_catalog_refresh")
                self.assertEqual(catalog.capability, role)

    def test_role_observation_matches_current_binding_not_historical_dependency(self):
        current_b = replace(self.current, dependency_connection_id="connection-b",
                            frozen_context_digest="d" * 64)
        observed_b = replace(self.observation, connection_id="connection-b")
        for old_dependency in ("connection-a", None):
            with self.subTest(old_dependency=old_dependency):
                self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True),
                              wait=replace(self.wait, dependency_connection_id=old_dependency),
                              observation=observed_b, current=current_b)
                self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False),
                              wait=replace(self.wait, dependency_connection_id=old_dependency),
                              observation=replace(observed_b, capability=None,
                                                  probe_kind="provider_connection_check"), current=current_b)
        self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False),
                      observation=observed_b, current=replace(current_b, dependency_connection_id=None))
        self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False), current=current_b)
        self.decision(("defer", "READINESS_NOT_READY", False), observation=observed_b,
                      current=replace(current_b, candidate_available=False))
        self.decision(("suppress", "READINESS_CONTEXT_SUPPRESSED", False), observation=observed_b,
                      current=replace(current_b, context_already_decided=True))
        for updates in ({"configuration_version": 4}, {"model_catalog_digest": "e" * 64},
                        {"observation_sequence": 6}, {"capability": "text_generation"}):
            with self.subTest(mismatch=updates):
                self.decision(("defer", "READINESS_DEPENDENCY_CHANGED", False),
                              observation=replace(observed_b, **updates), current=current_b)
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True),
                      wait=replace(self.wait, dependency_connection_id="connection-b"),
                      observation=replace(observed_b, capability=None,
                                          probe_kind="provider_connection_check"), current=current_b)

    def test_current_access_or_source_binding_revoke_blocks(self):
        self.decision(("deny", "READINESS_ACCESS_DENIED", False),
                      current=replace(self.current, authorized=False))
        self.decision(("deny", "READINESS_SOURCE_CHANGED", False),
                      current=replace(self.current, source_current=False))
        self.decision(("deny", "READINESS_SCOPE_NOT_CONFIGURED", False),
                      current=replace(self.current, scope_enabled=False, authorized=False))
        for field in ("tenant_id", "workspace_id", "actor_id", "notebook_id",
                      "source_version_id", "prior_run_id"):
            with self.subTest(field=field):
                self.invalid(self.m.decide_reprocess, self.wait, self.observation,
                             replace(self.current, **{field: "different"}), db_now=NOW)

    def test_one_auto_run_per_frozen_context_across_events(self):
        decided = replace(self.current, context_already_decided=True)
        for event in ("rdy-" + "1" * 32, "rdy-" + "2" * 32):
            self.decision(("suppress", "READINESS_CONTEXT_SUPPRESSED", False), current=decided,
                          observation=replace(self.observation, event_id=event))
        self.decision(("suppress", "READINESS_ACTIVE_RUN_EXISTS", False),
                      current=replace(self.current, active_run=True))
        self.decision(("suppress", "READINESS_CONTEXT_SUPPRESSED", False),
                      current=replace(decided, active_run=True))

    def test_cost_blocked_context_never_queues_and_real_policy_change_is_distinct(self):
        self.decision(("suppress", "READINESS_CONTEXT_SUPPRESSED", False),
                      current=replace(self.current, cost_blocked_context_exists=True))
        self.decision(("block_cost", "COST_LIMIT_EXCEEDED", True),
                      current=replace(self.current, cost_state="blocked"))
        self.decision(("defer", "READINESS_COST_UNCONFIRMED", False),
                      current=replace(self.current, cost_state="unknown"))
        self.decision(("block_policy", "READINESS_POLICY_BLOCKED", True),
                      current=replace(self.current, policy_allowed=False))
        self.decision(("block_policy", "READINESS_POLICY_BLOCKED", True),
                      current=replace(self.current, egress_state="blocked"))
        self.decision(("defer", "READINESS_APPROVAL_MISSING", False),
                      current=replace(self.current, egress_state="approval_missing"))
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True),
                      current=replace(self.current, frozen_context_digest="d" * 64))

    def test_backoff_expiry_out_of_order_and_restart(self):
        self.decision(("defer", "READINESS_BACKOFF_PENDING", False),
                      current=replace(self.current, policy_allowed=False),
                      wait=replace(self.wait, not_before=NOW + timedelta(seconds=1)))
        self.decision(("defer", "READINESS_BACKOFF_PENDING", False), observation=replace(
            self.observation, observed_at=NOW - timedelta(seconds=59), received_at=NOW))
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True), observation=replace(
            self.observation, observed_at=NOW - timedelta(seconds=60), received_at=NOW),
            wait=replace(self.wait, not_before=NOW))
        self.decision(("expire", "READINESS_OBSERVATION_EXPIRED", False), now=NOW + timedelta(seconds=211))
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True), now=NOW + timedelta(seconds=210))
        for updates in ({"observed_at": self.wait.failed_at, "received_at": self.wait.failed_at},
                        {"observed_at": NOW + timedelta(seconds=6), "received_at": NOW},
                        {"received_at": NOW + timedelta(seconds=6)},
                        {"received_at": self.observation.observed_at - timedelta(seconds=6)}):
            self.decision(("defer", "READINESS_NOT_READY", False),
                          observation=replace(self.observation, **updates))
        self.decision(("defer", "READINESS_BACKOFF_PENDING", False), observation=replace(
            self.observation, observed_at=NOW + timedelta(seconds=5), received_at=NOW))
        # 재시작을 모사한 새 객체도 같은 DB 시각/저장 입력이면 같은 고정 결론이다.
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True),
                      wait=replace(self.wait), observation=replace(self.observation), current=replace(self.current))

    def test_explicit_mode_preserves_selection_and_legacy_unknown_is_not_auto(self):
        # HTTP key409/wire 불변은 R3 이후 시험이며 여기서 검증했다고 주장하지 않는다.
        self.decision(("suppress", "READINESS_MODE_MANUAL", False),
                      wait=replace(self.wait, mode=self.m.ReadinessModeProof("pinned", "request")))
        local = replace(self.wait, mode=self.m.ReadinessModeProof("local_only", "request"))
        self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True), wait=local)
        self.decision(("block_policy", "READINESS_POLICY_BLOCKED", True), wait=local,
                      current=replace(self.current, local_only_allowed=False))
        for role in ("text_generation", "image_understanding", "document_parsing", "speech_to_text"):
            self.decision(("enqueue", "READY_REPROCESS_ALLOWED", True),
                          wait=replace(self.wait, capability=role),
                          observation=replace(self.observation, capability=role),
                          current=replace(self.current, capability=role))
        self.assertEqual(local.dependency_connection_id, "connection-a")
        self.assertEqual(local.mode.mode, "local_only")

    def test_scope_setting_defaults_empty_and_rejects_wildcard_duplicate_or_extra_fields(self):
        name = "DAON_DOCUMENT_READINESS_SCOPES_JSON"
        scope = {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "actor_id": "actor-a",
                 "source_version_id": "version-a"}
        with patch.dict("os.environ", {}, clear=True):
            settings = self.m.ReadinessDispatcherSettings.from_env()
        self.assertEqual(settings.scopes, frozenset())
        self.assertEqual((settings.backoff_seconds, settings.event_ttl_seconds,
                          settings.future_tolerance_seconds, settings.max_batch, settings.max_seconds),
                         (60, 300, 5, 32, 2))
        with patch.dict("os.environ", {name: json.dumps([scope]), "UNRELATED": "ignored"}, clear=True):
            settings = self.m.ReadinessDispatcherSettings.from_env()
        self.assertEqual(settings.scopes, frozenset({("tenant-a", "workspace-a", "actor-a", "version-a")}))
        for raw in ("", "null", "{}", "[true]", "[NaN]", json.dumps([scope, scope]),
                    json.dumps([{**scope, "actor_id": "*"}]), json.dumps([{**scope, "extra": True}]),
                    json.dumps([{k: v for k, v in scope.items() if k != "actor_id"}]),
                    '[{"tenant_id":"x","tenant_id":"y","workspace_id":"w","actor_id":"a","source_version_id":"v"}]',
                    json.dumps([{**scope, "source_version_id": str(i)} for i in range(101)])):
            with self.subTest(raw=raw), patch.dict("os.environ", {name: raw}, clear=True):
                self.invalid(self.m.ReadinessDispatcherSettings.from_env)
        with patch.dict("os.environ", {name: json.dumps([
            {**scope, "source_version_id": str(i)} for i in range(100)])}, clear=True):
            self.assertEqual(len(self.m.ReadinessDispatcherSettings.from_env().scopes), 100)
        for scopes in ([], {("t", "w", "a", "v")}, frozenset({("t", "w", "a")}),
                       frozenset({("t", "w", "*", "v")})):
            self.invalid(self.m.ReadinessDispatcherSettings, scopes=scopes)
        for field in ("backoff_seconds", "event_ttl_seconds", "future_tolerance_seconds", "max_batch", "max_seconds"):
            self.invalid(self.m.ReadinessDispatcherSettings, **{field: True})

    def test_closed_types_reject_bad_utc_digest_ids_enums_and_integer_bools(self):
        for obj, fields in ((self.wait, ("frozen_context_digest",)),
                            (self.current, ("frozen_context_digest", "model_catalog_digest")),
                            (self.observation, ("model_catalog_digest", "content_digest"))):
            for field in fields:
                for value in ("A" * 64, "a" * 63, "g" * 64, True):
                    self.invalid(replace, obj, **{field: value})
        for field in ("observed_at", "received_at"):
            for value in (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=9))), "now", None):
                self.invalid(replace, self.observation, **{field: value})
        for field in ("failed_at", "not_before"):
            self.invalid(replace, self.wait, **{field: NOW.replace(tzinfo=None)})
        self.invalid(replace, self.wait, not_before=self.wait.failed_at - timedelta(seconds=1))
        for field in ("schema_version", "configuration_version", "observation_sequence"):
            for value in (True, 0, -1, 1.0, "1"):
                self.invalid(replace, self.observation, **{field: value})
        self.invalid(replace, self.observation, schema_version=2)
        for field in ("configuration_version", "latest_observation_sequence"):
            for value in (True, -1, 1.0, "1"):
                self.invalid(replace, self.current, **{field: value})
        for field in ("scope_enabled", "authorized", "source_current", "policy_allowed", "local_only_allowed",
                      "candidate_available", "active_run", "context_already_decided", "cost_blocked_context_exists"):
            self.invalid(replace, self.current, **{field: 1})
        for obj, updates in ((self.wait, {"mode": {"mode": "auto", "origin": "request"}}),
                             (self.wait, {"failure_kind": "timeout"}), (self.wait, {"safe_error_code": "raw response"}),
                             (self.wait, {"capability": "file_type"}),
                             (self.current, {"capability": "file_type"}),
                             (self.observation, {"capability": "file_type"}),
                             (self.observation, {"event_id": "rdy-../x"}),
                             (self.observation, {"probe_kind": "enabled"}),
                             (self.observation, {"status": "ready"}),
                             (self.current, {"cost_state": "free"}), (self.current, {"egress_state": "yes"})):
            self.invalid(replace, obj, **updates)
        for obj, fields in ((self.wait, ("tenant_id", "workspace_id", "actor_id", "notebook_id", "source_id",
                                       "source_version_id", "processing_run_id", "dependency_connection_id")),
                            (self.current, ("tenant_id", "workspace_id", "actor_id", "notebook_id",
                                           "source_version_id", "prior_run_id", "dependency_connection_id")),
                            (self.observation, ("connection_id", "adapter_type"))):
            for field in fields:
                self.invalid(replace, obj, **{field: "../secret"})
        self.assertEqual(replace(self.wait, source_id="s" * 256).source_id, "s" * 256)
        self.invalid(replace, self.wait, source_id="s" * 257)
        for obj in (self.wait, self.observation, self.current, self.m.ReadinessModeProof("auto", "request")):
            with self.assertRaises(TypeError):
                replace(obj, unexpected="extra")
            with self.assertRaises((FrozenInstanceError, AttributeError, TypeError)):
                obj.extra = True
        self.invalid(self.m.decide_reprocess, self.wait, self.observation, self.current,
                     db_now=NOW.replace(tzinfo=None))

    def test_decision_type_rejects_invented_codes_and_inconsistent_run_flags(self):
        for args in (("enqueued", "READY_REPROCESS_ALLOWED", True),
                     ("enqueue", "RAW_REMOTE_ERROR", True),
                     ("enqueue", "READY_REPROCESS_ALLOWED", False),
                     ("suppress", "READINESS_CONTEXT_SUPPRESSED", True),
                     ("deny", "READY_REPROCESS_ALLOWED", False),
                     ("defer", "READINESS_NOT_READY", 0)):
            self.invalid(self.m.ReprocessDecision, *args)


if __name__ == "__main__":
    unittest.main()
