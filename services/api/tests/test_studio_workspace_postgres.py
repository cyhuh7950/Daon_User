from __future__ import annotations

import unittest
import os
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg

from daon_user_api.studio_workspace import (
    StudioContext, StudioGenerationRequest, StudioWorkspaceService, build_structured_output,
)
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
from daon_user_api.object_queue import StagedObject, StoredObject
from daon_user_api.license import LicenseError


class Result:
    def __init__(self, row=None, rows=()): self.row, self.rows = row, rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Connection:
    def __init__(self): self.statements = []
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM workspace_policies" in sql:
            common = {"active": True, "current": True, "workspace_id": "workspace-1", "version": 3}
            deny = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            return Result(({**common, "data_area": "cloud_sync", "authority_policy": "workspace_admin"}, {**common, "ruleset_version_id": "ruleset-v3"}, {**common, "profile": "trusted-source-v2"}, {**common, "scope": "workspace"}, {**common, "decision": "deny_external", "organization_policy_version_id": "org-policy-1", "organization_binding_id": "org-binding-1", "workspace_policy_version_id": "ws-policy-1", "workspace_binding_id": "ws-binding-1", "organization_policy": deny, "workspace_policy": deny}))
        if "FROM runs r JOIN egress_decisions" in sql:
            effective = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            frozen_base = {"organization_policy_version_id": "org-policy-1", "organization_binding_id": "org-binding-1", "workspace_policy_version_id": "ws-policy-1", "workspace_binding_id": "ws-binding-1", **effective}
            frozen = {**frozen_base, "fingerprint": "sha256:" + hashlib.sha256(json.dumps(frozen_base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            return Result(({"frozen_routing_context": frozen, "source_id": "source-1", "source_version_id": "source-version-1"}, "egress-1", {"run_id": "run-1", "frozen_routing_context": frozen}, "routing-1", {"run_id": "run-1", "egress_decision_id": "egress-1", "selected_deployment_id": "deployment-record-1"}))
        if "FROM model_deployments md" in sql:
            return Result((
                "deployment-record-1",
                {"configured_profile_id": "provider-upstage", "provider_code": "UPSTAGE", "binding_version": 5},
                {"configured_deployment_id": "deployment-text", "model_id": "solar-pro4", "binding_version": 5},
                {"provider_code": "UPSTAGE", "model_id": "solar-pro4"},
            ))
        if "FROM idempotency_records" in sql: return Result()
        if "SELECT count(*) FROM notebook_bindings" in sql:
            return Result((len(set(params[3])),))
        if "JOIN notebook_bindings nb" in sql and "nb.record_id=r.conversation_id" in sql:
            return Result((1,))
        if "FROM runs r" in sql: return Result(({"answer": "근거 답변", "insufficient": False}, 1, True, True))
        if "FROM citations" in sql: return Result(rows=(("citation-1", "source-version-1", "span-1", {"page": 2}),))
        if "transition_canon_state" in sql: return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class Transaction:
    def __init__(self, connection): self.connection = connection
    def __enter__(self): return self.connection
    def __exit__(self, *_args): return False


class Cloud:
    def __init__(self): self.connection = Connection()
    def _transaction(self, _context): return Transaction(self.connection)


class MixedContextConnection(Connection):
    def execute(self, sql, params=()):
        if "FROM runs r" in sql and "JOIN run_results" in sql:
            self.statements.append((sql, params))
            return Result(({"answer": "혼합 근거 답변", "insufficient": False}, 2, True, True))
        if "FROM citations" in sql:
            self.statements.append((sql, params))
            return Result(rows=(
                ("citation-daon", "source-version-daon", "span-daon", {"page": 1, "origin": "daon_knowledge", "locator": {"kind": "section", "value": "summary"}}),
                ("citation-raw", "source-version-1", "span-raw", {"page": 2, "origin": "raw_source", "locator": {"kind": "page", "value": "2"}}),
            ))
        return super().execute(sql, params)


class SourceOnlyConnection(Connection):
    def execute(self, sql, params=()):
        if "FROM source_versions sv JOIN sources s" in sql:
            self.statements.append((sql, params))
            return Result(rows=(
                ("source-version-1", "source-1", "span-1", {"text": "Source 원문", "page": 1}, {"filename": "guide.pdf"}),
            ))
        return super().execute(sql, params)


class VersionHistoryConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM output_versions ov" in sql and "ORDER BY ov.content_version DESC" in sql:
            return Result(rows=(
                ("version-2", 2, "version-1", "approved", {
                    "revision_type": "user_edit", "change_reason": "문구 정정",
                    "content": {"body": "승인 본문"}, "output_format": "pdf",
                }, "settings-2", [
                    {"citation_id": "citation-daon", "source_version_id": "source-version-daon", "evidence_span_id": "span-daon", "origin": "daon_knowledge", "locator": {"kind": "section", "value": "summary"}},
                    {"citation_id": "citation-raw", "source_version_id": "source-version-1", "evidence_span_id": "span-raw", "origin": "raw_source", "locator": {"kind": "page", "value": "2"}},
                ], "review-1", "approval-request-1", "approval-1", None, None),
                ("version-1", 1, None, "draft", {
                    "revision_type": "initial", "change_reason": "initial_generation",
                    "content": {"body": "초안"}, "output_format": "pdf",
                }, "settings-1", [], None, None, None, None, None),
            ))
        return super().execute(sql, params)


class ApprovalTransitionConnection(Connection):
    def __init__(self):
        super().__init__(); self.transition_ids = set()

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM idempotency_records" in sql: return Result()
        if "SELECT state,version FROM output_versions" in sql: return Result(("in_review", 4))
        if "SELECT state,version FROM approval_requests" in sql: return Result(("pending", 1))
        if "transition_canon_state" in sql:
            if params[4] in self.transition_ids: return Result((params[3], params[2], "failed", "TRANSITION_ID_CONFLICT"))
            self.transition_ids.add(params[4])
            return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class ExportConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM output_versions ov" in sql:
            return Result(({
                "output_type": "comparison_table", "purpose": "비교표", "source_version_ids": ["source-version-1"],
                "content": {"rows": [
                    {"key": "A", "baseline": "1", "current": "1", "state": "same", "evidence": ["citation-1", "citation-2"]},
                    {"key": "B", "baseline": "1", "current": "2", "state": "changed", "evidence": ["citation-3", "citation-4"]},
                ]},
            }, datetime(2026, 8, 13, tzinfo=timezone.utc), 4, "approved"))
        return super().execute(sql, params)


class KnowledgeRegistrationConnection(Connection):
    def __init__(self):
        super().__init__(); self.source_version_inserted = False

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM idempotency_records" in sql: return Result()
        if "SELECT state,version FROM output_versions" in sql: return Result(("approved", 2))
        if "SELECT canonical_json FROM output_versions" in sql:
            return Result(({"source_version_ids": ["source-version-1"], "content": {
                "title": "승인 지식", "sections": [{"heading": "핵심", "body": "검증된 지식 본문"}],
            }},))
        if "FROM source_versions" in sql and "derived_output_version_id" in sql: return Result()
        if sql.startswith("INSERT INTO source_versions"):
            self.source_version_inserted = True
        if sql.startswith("INSERT INTO knowledge_registrations") and not self.source_version_inserted:
            raise AssertionError("registered_source_version_id FK target must exist before registration")
        if "transition_canon_state" in sql: return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class RevisionConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM workspace_policies" in sql:
            return super().execute(sql, params)
        if "FROM runs r JOIN egress_decisions" in sql:
            return super().execute(sql, params)
        if "FROM model_deployments md" in sql:
            return super().execute(sql, params)
        if "FROM runs r" in sql:
            return Result(({"answer": "새 생성 답변", "insufficient": False}, 1, True, True))
        if "FROM idempotency_records" in sql: return Result()
        if "FROM output_versions WHERE" in sql:
            return Result(("aggregate-1", 4, "settings-old", {"purpose": "기존", "audience": "기존 독자", "output_type": "evidence_report", "source_id": "source-1", "source_version_ids": ["source-version-1"], "run_id": "run-1", "run_result_id": "result-1", "ruleset_version_id": "ruleset-v3", "length": "short", "structure": "summary", "output_format": "pdf", "review_condition": "review_required", "content": {"body": "기존"}}, "revision_requested"))
        if "FROM run_results" in sql: return Result(({"answer": "새 생성 답변", "insufficient": False},))
        if "FROM citations" in sql: return Result(rows=(("citation-1", "source-version-1", "span-1", {"page": 7}),))
        if "FROM evidence_references" in sql:
            return Result(rows=(("evidence-old", "source-version-1", "span-1", {"citation_id": "citation-1", "origin": "raw_source", "locator": {"kind": "page", "value": "7"}}),))
        if "transition_canon_state" in sql: return Result((params[3], int(params[2]) + 1, "succeeded", None))
        return Result()


class PolicyConnection(Connection):
    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM workspace_policies" in sql:
            common = {"active": True, "current": True, "workspace_id": "workspace-1", "version": 3}
            deny = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            return Result(({**common, "data_area": "cloud_sync", "authority_policy": "workspace_admin"}, {**common, "ruleset_version_id": "ruleset-v3"}, {**common, "profile": "trusted-source-v2"}, {**common, "scope": "workspace"}, {**common, "decision": "deny_external", "organization_policy_version_id": "org-policy-1", "organization_binding_id": "org-binding-1", "workspace_policy_version_id": "ws-policy-1", "workspace_binding_id": "ws-binding-1", "organization_policy": deny, "workspace_policy": deny}))
        return Result()


class ObjectStorage:
    def __init__(self): self.content = b""; self.final_key = ""
    def put_staged(self, key, content, content_type, digest):
        self.content = content
        return StagedObject(key, digest, len(content), content_type, "etag-stage", None)
    def promote(self, staged, final_key, **_kwargs):
        self.final_key = final_key
        return StoredObject(final_key, staged.digest_sha256, staged.byte_size, staged.content_type, "etag", None)
    def get(self, key):
        assert key == self.final_key
        return self.content


class StudioWorkspacePostgresContractTests(unittest.TestCase):
    def test_transactional_license_capability_requires_postgres_enforcer(self) -> None:
        cloud = Cloud()
        plain = StudioWorkspaceService(PostgresStudioWorkspaceRepository(cloud))
        enforced = StudioWorkspaceService(PostgresStudioWorkspaceRepository(
            cloud, creation_enforcer=lambda *_args: None,
        ))

        self.assertFalse(plain.creation_license_authoritative)
        self.assertTrue(enforced.creation_license_authoritative)

    def request(self):
        return StudioGenerationRequest(
            output_type="evidence_report", source_id="source-1", source_version_ids=("source-version-1",),
            run_id="run-1", run_result_id="result-1", purpose="목적", audience="독자", ruleset_version_id="ruleset-v3",
            length="short", structure="summary", output_format="pdf", review_condition="review_required",
        )

    def test_generation_transaction_declares_all_canon_lineage_and_idempotency(self) -> None:
        sql = PostgresStudioWorkspaceRepository.generation_contract_sql()
        for token in (
            "generation_settings_snapshots", "generation_requests", "studio_outputs", "output_versions",
            "evidence_references", "audit_events", "idempotency_records", "tenant_id", "workspace_id",
        ):
            self.assertIn(token, sql)
        self.assertNotIn("UPDATE output_versions", sql.upper())

    def test_repository_rejects_workspace_mismatch_before_database_write(self) -> None:
        repository = PostgresStudioWorkspaceRepository(None)
        with self.assertRaisesRegex(Exception, "STUDIO_DATABASE_UNAVAILABLE"):
            repository.create_generation(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"), self.request(),
                "generation-key-0001",
            )

    def test_generation_writes_complete_canon_transaction_and_grounded_content(self) -> None:
        cloud = Cloud()
        output, replayed = PostgresStudioWorkspaceRepository(cloud).create_generation(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            self.request(), "generation-key-0001",
        )
        self.assertFalse(replayed)
        self.assertEqual(output["output_type"], "evidence_report")
        self.assertEqual("근거 답변", output["content"]["body"])
        sql = "\n".join(item[0] for item in cloud.connection.statements)
        for table in ("generation_settings_snapshots", "generation_requests", "studio_outputs", "output_versions", "evidence_references", "audit_events", "idempotency_records"):
            self.assertIn(f"INSERT INTO {table}", sql)
        self.assertIn("server_policy_projection", repr(cloud.connection.statements))
        self.assertIn("originating_run", repr(cloud.connection.statements))
        self.assertIn("model_selection", repr(cloud.connection.statements))
        self.assertIn("deployment-text", repr(cloud.connection.statements))
        settings_insert = next(
            params for sql, params in cloud.connection.statements
            if sql.startswith("INSERT INTO generation_settings_snapshots")
        )
        self.assertIn('"model_selection"', str(settings_insert))

    def test_generation_enforces_feature_and_both_resources_inside_creation_transaction(self) -> None:
        cloud = Cloud()
        calls = []
        def deny(connection, tenant_id, action, increments):
            calls.append((connection, tenant_id, action, increments))
            raise LicenseError("LICENSE_RESOURCE_LIMIT_REACHED", 409)
        with self.assertRaisesRegex(LicenseError, "LICENSE_RESOURCE_LIMIT_REACHED"):
            PostgresStudioWorkspaceRepository(cloud, creation_enforcer=deny).create_generation(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
                self.request(), "generation-key-license-limit-0001",
            )
        self.assertEqual(calls, [(cloud.connection, "tenant-1", "studio.generate", {
            "generation_runs": 1, "studio_outputs": 1,
        })])
        self.assertFalse(any("INSERT INTO" in sql for sql, _ in cloud.connection.statements))

    def test_generation_binds_daon_and_raw_citations_from_one_question_run(self) -> None:
        cloud = Cloud(); cloud.connection = MixedContextConnection()
        request = StudioGenerationRequest(
            output_type="evidence_report", source_id="source-1",
            source_version_ids=("source-version-daon", "source-version-1"),
            run_id="run-1", run_result_id="result-1", purpose="혼합 보고", audience="운영자",
            ruleset_version_id="ruleset-v3", length="short", structure="summary",
            output_format="pdf", review_condition="review_required",
        )
        output, replayed = PostgresStudioWorkspaceRepository(cloud).create_generation(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            request, "mixed-generation-key-0001",
        )
        self.assertFalse(replayed)
        self.assertEqual({item["source_version_id"] for item in output["citations"]}, {"source-version-daon", "source-version-1"})
        self.assertEqual("혼합 근거 답변", output["content"]["body"])

    def test_source_only_normalizes_workspace_policy_without_grounded_run(self) -> None:
        cloud = Cloud(); cloud.connection = SourceOnlyConnection()
        request = StudioGenerationRequest(
            output_type="evidence_report", source_id="source-1", source_version_ids=("source-version-1",),
            run_id=None, run_result_id=None, purpose="Source 보고", audience="운영자", ruleset_version_id=None,
            length="short", structure="summary", output_format="pdf", review_condition="review_required",
            source_only=True,
        )
        output, replayed = PostgresStudioWorkspaceRepository(cloud).create_generation(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            request, "source-only-policy-key-0001",
        )
        self.assertFalse(replayed)
        self.assertEqual(output["content"]["body"], "Source 원문")
        settings = next(params for sql, params in cloud.connection.statements if sql.startswith("INSERT INTO generation_settings_snapshots"))
        self.assertIn("ruleset-v3", str(settings))

    def test_version_history_restores_citations_and_lifecycle_links(self) -> None:
        cloud = Cloud(); cloud.connection = VersionHistoryConnection()
        versions = PostgresStudioWorkspaceRepository(cloud).list_versions(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            "output-1",
        )
        self.assertEqual([item["output_version_id"] for item in versions], ["version-2", "version-1"])
        self.assertEqual({item["origin"] for item in versions[0]["citations"]}, {"raw_source", "daon_knowledge"})
        self.assertEqual(versions[0]["review_request_id"], "review-1")
        self.assertEqual(versions[0]["approval_request_id"], "approval-request-1")
        self.assertEqual(versions[0]["approval_id"], "approval-1")

    def test_approval_uses_distinct_transition_ids_for_request_and_output(self) -> None:
        cloud = Cloud(); cloud.connection = ApprovalTransitionConnection()
        result, replayed = PostgresStudioWorkspaceRepository(cloud).record_action(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            "approval", {"output_version_id": "version-1", "approval_request_id": "approval-request-1", "decision": "approved", "step_up_verified": True},
            "approval-transition-key-0001",
        )
        self.assertFalse(replayed)
        self.assertEqual(result["action"], "approval")
        self.assertEqual(len(cloud.connection.transition_ids), 2)

    def test_generation_rejects_missing_frozen_model_selection_before_writes(self) -> None:
        cloud = Cloud()
        original = cloud.connection.execute
        def execute(sql, params=()):
            if "FROM model_deployments md" in sql:
                return Result()
            return original(sql, params)
        cloud.connection.execute = execute
        with self.assertRaisesRegex(Exception, "ORIGINATING_RUN_MODEL_UNAVAILABLE"):
            PostgresStudioWorkspaceRepository(cloud).create_generation(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
                self.request(), "generation-key-model-0001",
            )
        self.assertFalse(any(
            sql.startswith("INSERT INTO generation_settings_snapshots")
            for sql, _ in cloud.connection.statements
        ))

    def test_generation_rejects_missing_originating_run_decisions_before_writes(self) -> None:
        cloud = Cloud()
        original = cloud.connection.execute
        def execute(sql, params=()):
            if "FROM runs r JOIN egress_decisions" in sql:
                return Result()
            return original(sql, params)
        cloud.connection.execute = execute
        with self.assertRaisesRegex(Exception, "ORIGINATING_RUN_POLICY_UNAVAILABLE"):
            PostgresStudioWorkspaceRepository(cloud).create_generation(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
                self.request(), "generation-key-0001",
            )
        self.assertFalse(any(sql.startswith("INSERT INTO generation_settings_snapshots") for sql, _ in cloud.connection.statements))

    def test_approved_export_is_persisted_and_checksum_verified_in_object_storage(self) -> None:
        cloud = Cloud(); cloud.connection = ExportConnection(); storage = ObjectStorage()
        exported = PostgresStudioWorkspaceRepository(cloud, storage).export_output(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            "output-1", "version-1", "xlsx",
        )
        self.assertTrue(exported.content.startswith(b"PK"))
        self.assertIn("/output/", storage.final_key)
        self.assertEqual(exported.checksum_sha256, __import__("hashlib").sha256(storage.content).hexdigest())
        export_sql = next(sql for sql, _params in cloud.connection.statements if sql.startswith("SELECT ov.canonical_json"))
        self.assertIn("notebook_bindings", export_sql)

    def test_knowledge_registration_creates_source_version_before_fk_reference(self) -> None:
        cloud = Cloud(); cloud.connection = KnowledgeRegistrationConnection()
        result, replayed = PostgresStudioWorkspaceRepository(cloud).record_action(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            "knowledge_registration", {"output_version_id": "version-1", "explicit": True},
            "knowledge-key-0001",
        )
        self.assertFalse(replayed)
        self.assertTrue(result["searchable"])
        evidence_insert = next(
            params for sql, params in cloud.connection.statements
            if sql.startswith("INSERT INTO evidence_spans")
        )
        index_insert = next(
            params for sql, params in cloud.connection.statements
            if sql.startswith("INSERT INTO index_versions")
        )
        self.assertEqual(evidence_insert[6].obj["kind"], "approved_knowledge_snapshot")
        self.assertIn("검증된 지식 본문", evidence_insert[6].obj["text"])
        self.assertEqual(index_insert[6].obj["chunks"][0]["text"], evidence_insert[6].obj["text"])

    def test_ai_and_settings_revisions_create_new_request_snapshot_and_resubmit_rejection(self) -> None:
        for revision_type in ("ai_regeneration", "settings_change"):
            cloud = Cloud(); cloud.connection = RevisionConnection()
            result, replayed = PostgresStudioWorkspaceRepository(cloud).create_version(
                StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"), "output-1",
                {"previous_version_id": "version-1", "revision_type": revision_type, "change_reason": "재작업", "content": "새 내용", **({"settings": {"purpose": "새 목적", "audience": "새 독자", "source_version_ids": ["source-version-1"], "ruleset_version_id": "ruleset-v3", "length": "long", "structure": "detail", "output_format": "docx", "review_condition": "review_required"}} if revision_type == "settings_change" else {})},
                f"revision-key-{revision_type}",
            )
            self.assertFalse(replayed)
            sql = "\n".join(statement for statement, _ in cloud.connection.statements)
            self.assertIn("INSERT INTO generation_settings_snapshots", sql)
            self.assertIn("INSERT INTO generation_requests", sql)
            self.assertIn("INSERT INTO evidence_references", sql)
            self.assertIn("FROM runs r", sql)
            self.assertTrue(result["resubmission_of_rejected_version"])
            self.assertEqual(result["content"]["body"], "새 생성 답변")
            if revision_type == "settings_change": self.assertIn("새 목적", repr(cloud.connection.statements))

    def test_user_edit_version_copies_previous_evidence_references(self) -> None:
        cloud = Cloud(); cloud.connection = RevisionConnection()
        result, replayed = PostgresStudioWorkspaceRepository(cloud).create_version(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1"),
            "output-1", {"previous_version_id": "version-1", "revision_type": "user_edit", "change_reason": "문구 정정", "content": "새 내용"},
            "revision-evidence-key-0001",
        )
        self.assertFalse(replayed)
        evidence_inserts = [(sql, params) for sql, params in cloud.connection.statements if sql.startswith("INSERT INTO evidence_references")]
        self.assertEqual(len(evidence_inserts), 1)
        self.assertEqual(evidence_inserts[0][1][-3:], (result["output_version_id"], "source-version-1", "span-1"))

    def test_version_and_action_lock_scope_before_idempotency_replay(self) -> None:
        context = StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")

        revision_cloud = Cloud(); revision_cloud.connection = RevisionConnection()
        PostgresStudioWorkspaceRepository(revision_cloud).create_version(
            context, "output-1",
            {"previous_version_id": "version-1", "revision_type": "user_edit", "change_reason": "문구 정정", "content": "새 내용"},
            "revision-lock-key-0001",
        )
        revision_sql = [statement for statement, _ in revision_cloud.connection.statements]
        self.assertLess(
            next(index for index, sql in enumerate(revision_sql) if "pg_advisory_xact_lock" in sql),
            next(index for index, sql in enumerate(revision_sql) if "FROM idempotency_records" in sql),
        )

        action_cloud = Cloud(); action_cloud.connection = KnowledgeRegistrationConnection()
        PostgresStudioWorkspaceRepository(action_cloud).record_action(
            context, "knowledge_registration", {"output_version_id": "version-1", "explicit": True},
            "action-lock-key-0001",
        )
        action_sql = [statement for statement, _ in action_cloud.connection.statements]
        self.assertLess(
            next(index for index, sql in enumerate(action_sql) if "pg_advisory_xact_lock" in sql),
            next(index for index, sql in enumerate(action_sql) if "FROM idempotency_records" in sql),
        )

    def test_policy_projection_returns_six_server_validated_locks(self) -> None:
        projection = PostgresStudioWorkspaceRepository._policy_projection(
            PolicyConnection(), StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
        )
        locks = {lock["field"]: lock["value"] for lock in projection["locks"]}
        self.assertEqual(set(locks), {"rulesetVersionId", "reviewCondition", "authorityPolicy", "weightProfile", "dataArea", "egressPolicy"})
        self.assertEqual(locks["rulesetVersionId"], "ruleset-v3")

    def test_policy_projection_types_jsonb_workspace_bind_as_text(self) -> None:
        connection = PolicyConnection()

        PostgresStudioWorkspaceRepository._policy_projection(
            connection, StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1"),
        )

        projection_sql = connection.statements[0][0]
        self.assertIn("'workspace_id',%s::text", projection_sql)
        self.assertEqual(projection_sql.count("%s::text"), 1)

    def test_default_policy_returns_empty_outputs_and_six_locks(self) -> None:
        result = PostgresStudioWorkspaceRepository(Cloud()).list_outputs(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")
        )

        self.assertEqual(result["outputs"], ())
        self.assertEqual(len(result["studio_locks"]), 6)

    def test_list_outputs_uses_selected_lateral_columns_in_deletion_filter(self) -> None:
        cloud = Cloud()
        PostgresStudioWorkspaceRepository(cloud).list_outputs(
            StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")
        )
        list_sql = next(sql for sql, _params in cloud.connection.statements if "FROM studio_outputs so" in sql)
        self.assertIn("er.tenant_id=so.tenant_id", list_sql)
        self.assertIn("er.workspace_id=so.workspace_id", list_sql)
        self.assertNotIn("er.tenant_id=ov.tenant_id", list_sql)
        self.assertNotIn("er.workspace_id=ov.workspace_id", list_sql)

    def test_policy_projection_fails_closed_for_missing_inactive_stale_or_wrong_scope(self) -> None:
        context = StudioContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")
        for index, patch in ((0, None), (1, {"active": False}), (2, {"current": False}), (3, {"workspace_id": "workspace-other"}), (4, {"workspace_policy_version_id": ""})):
            connection = PolicyConnection(); original = connection.execute
            def execute(sql, params=(), *, index=index, patch=patch):
                result = original(sql, params)
                if "FROM workspace_policies" not in sql: return result
                values = list(result.row)
                if patch is None: values[index] = None
                else: values[index] = {**values[index], **patch}
                return Result(tuple(values))
            connection.execute = execute
            with self.assertRaisesRegex(Exception, "POLICY_PROJECTION_UNAVAILABLE"):
                PostgresStudioWorkspaceRepository._policy_projection(connection, context, run_id="run-1")

        for index, field in ((0, "data_area"), (0, "authority_policy"), (1, "ruleset_version_id"), (2, "profile"), (3, "scope")):
            connection = PolicyConnection(); original = connection.execute
            def execute(sql, params=(), *, index=index, field=field):
                result = original(sql, params)
                if "FROM workspace_policies" not in sql: return result
                values = list(result.row); values[index] = {**values[index], field: ""}
                return Result(tuple(values))
            connection.execute = execute
            with self.assertRaisesRegex(Exception, "POLICY_PROJECTION_UNAVAILABLE"):
                PostgresStudioWorkspaceRepository._policy_projection(connection, context, run_id="run-1")


@unittest.skipUnless(os.environ.get("DAON_STUDIO_TEST_DSN"), "dedicated PostgreSQL test DSN required")
class StudioWorkspaceRealPostgresIntegrationTests(unittest.TestCase):
    dsn = os.environ.get("DAON_STUDIO_TEST_DSN", "")

    @staticmethod
    def _canonical(connection, table, record_id, payload, *, state=None, extra=()):
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        columns = ["tenant_id", "workspace_id", "record_id", "aggregate_id", "version", "schema_version", "canonical_json", "canonical_text", "digest_sha256", "created_by", "trace_id"]
        values = ["tenant-it", "workspace-it", record_id, record_id, 1, 1, psycopg.types.json.Jsonb(payload), text, hashlib.sha256(text.encode()).hexdigest(), "actor-it", "trace-it"]
        if state is not None:
            columns.append("state"); values.append(state)
        for column, value in extra:
            columns.append(column); values.append(value)
        connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s'] * len(values))})", values)

    def setUp(self):
        with psycopg.connect(self.dsn) as connection:
            connection.execute("INSERT INTO tenants (tenant_id,display_name) VALUES ('tenant-it','IT tenant'),('tenant-other','Other tenant')")
            connection.execute("INSERT INTO workspaces (tenant_id,workspace_id,display_name) VALUES ('tenant-it','workspace-it','IT workspace'),('tenant-other','workspace-other','Other workspace')")
            deny = {"mode": "deny_external", "allowed_provider_kinds": [], "allowed_destinations": [], "classification": "restricted", "max_bytes": 0, "masking_required": True, "redaction_required": True, "required_approver": "organization_admin"}
            deny_text = json.dumps(deny, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            deny_digest = hashlib.sha256(deny_text.encode()).hexdigest()
            connection.execute(
                "INSERT INTO egress_policy_versions (tenant_id,organization_id,workspace_id,policy_version_id,scope_type,policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id) VALUES "
                "('tenant-it','tenant-it',NULL,'org-policy-it','organization',1,'active',%s,%s,%s,'actor-it','trace-it'),"
                "('tenant-it','tenant-it','workspace-it','workspace-policy-it','workspace',1,'active',%s,%s,%s,'actor-it','trace-it')",
                (psycopg.types.json.Jsonb(deny), deny_text, deny_digest, psycopg.types.json.Jsonb(deny), deny_text, deny_digest),
            )
            connection.execute(
                "INSERT INTO egress_policy_bindings (tenant_id,organization_id,workspace_id,binding_id,scope_type,policy_version_id,binding_version,active,current,created_by,trace_id) VALUES "
                "('tenant-it','tenant-it',NULL,'org-binding-it','organization','org-policy-it',1,true,true,'actor-it','trace-it'),"
                "('tenant-it','tenant-it','workspace-it','workspace-binding-it','workspace','workspace-policy-it',1,true,true,'actor-it','trace-it')"
            )
            connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
            connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
            connection.execute("SELECT set_config('app.actor_id','actor-it',true)")
            self._canonical(connection, "generation_settings_snapshots", "settings-it", {"purpose": "통합"})
            self._canonical(connection, "generation_requests", "generation-it", {"purpose": "통합"}, state="configuring", extra=(("generation_settings_snapshot_id", "settings-it"),))
            connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-it',1,'confirmed','transition-generation-confirmed','IT','trace-it','policy-it')")
            connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-it',2,'submitted','transition-generation-submitted','IT','trace-it','policy-it')")
            self._canonical(connection, "studio_outputs", "output-it", {"output_type": "evidence_report", "title": "통합 산출물", "purpose": "통합 보고서"}, extra=(("generation_request_id", "generation-it"),))
            self._canonical(connection, "sources", "source-original", {"kind": "upload"}, state="registered")
            source_version = 1
            for target in ("security_check", "processing", "indexing", "ready"):
                connection.execute("SELECT * FROM transition_canon_state('Source','source-original',%s,%s,%s,'IT','trace-it','policy-it')", (source_version, target, f"transition-source-{target}"))
                source_version += 1
            self._canonical(connection, "source_versions", "source-version-original", {"kind": "upload"}, extra=(("source_id", "source-original"),))
            self._canonical(connection, "output_versions", "output-version-it", {
                "source_version_ids": ["source-version-original"],
                "output_type": "evidence_report", "purpose": "통합 보고서", "audience": "검토자",
                "source_id": "source-original", "run_id": "run-it", "run_result_id": "result-it",
                "ruleset_version_id": None, "length": "short", "structure": "summary",
                "output_format": "pdf", "review_condition": "review_required",
                "revision_type": "initial", "change_reason": "initial_generation",
                "content": {
                    "title": "승인된 Daon 지식",
                    "sections": [{"heading": "핵심", "body": "실제 PostgreSQL에 저장된 일반 텍스트 지식"}],
                },
            }, state="generating", extra=(("studio_output_id", "output-it"), ("generation_settings_snapshot_id", "settings-it")))
            output_version = 1
            for target in ("draft", "review_requested", "in_review", "approved"):
                connection.execute("SELECT * FROM transition_canon_state('OutputVersion','output-version-it',%s,%s,%s,'IT','trace-it','policy-it')", (output_version, target, f"transition-output-{target}"))
                output_version += 1
            self._canonical(connection, "evidence_spans", "span-original", {"text": "원본 근거", "page": 2}, extra=(("source_version_id", "source-version-original"),))
            self._canonical(connection, "evidence_references", "evidence-original", {
                "citation_id": "citation-original", "source_version_id": "source-version-original",
                "evidence_span_id": "span-original", "origin": "raw_source", "locator": {"kind": "page", "value": "2"},
            }, extra=(("output_version_id", "output-version-it"), ("source_version_id", "source-version-original"), ("evidence_span_id", "span-original")))

    def test_rls_fk_transaction_and_registration_order(self):
        with psycopg.connect(self.dsn) as connection:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    self._canonical(connection, "knowledge_registrations", "bad-registration", {}, state="requested", extra=(("output_version_id", "output-version-it"), ("registered_source_version_id", "missing-source-version")))
            with connection.transaction():
                connection.execute("SET LOCAL ROLE daon_app")
                connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
                connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
                visible = connection.execute("SELECT workspace_id FROM workspaces ORDER BY workspace_id").fetchall()
                self.assertEqual(visible, [("workspace-it",)])
            with self.assertRaises(RuntimeError):
                with connection.transaction():
                    connection.execute("INSERT INTO tenants (tenant_id,display_name) VALUES ('rollback-it','Rollback')")
                    raise RuntimeError("force rollback")
            self.assertIsNone(connection.execute("SELECT 1 FROM tenants WHERE tenant_id='rollback-it'").fetchone())

        from daon_user_api.cloud_storage import PostgresCloudStore
        store = PostgresCloudStore(self.dsn)
        try:
            result, replayed = PostgresStudioWorkspaceRepository(store).record_action(
                StudioContext("tenant-it", "workspace-it", "actor-it", "trace-it", "policy-it"),
                "knowledge_registration", {"output_version_id": "output-version-it", "explicit": True},
                "knowledge-real-pg-0001",
            )
            self.assertFalse(replayed)
            self.assertTrue(result["searchable"])
            with psycopg.connect(self.dsn) as connection:
                self.assertIsNotNone(connection.execute("SELECT 1 FROM source_versions WHERE record_id=%s", (result["registered_source_version_id"],)).fetchone())
                self.assertIsNotNone(connection.execute("SELECT 1 FROM knowledge_registrations WHERE registered_source_version_id=%s AND state='registered'", (result["registered_source_version_id"],)).fetchone())
                registered_source_id = connection.execute(
                    "SELECT source_id FROM source_versions WHERE record_id=%s",
                    (result["registered_source_version_id"],),
                ).fetchone()[0]
                registration_id = connection.execute(
                    "SELECT record_id FROM knowledge_registrations WHERE registered_source_version_id=%s",
                    (result["registered_source_version_id"],),
                ).fetchone()[0]
                evidence_span_id, evidence_payload = connection.execute(
                    "SELECT record_id,canonical_json FROM evidence_spans WHERE source_version_id=%s",
                    (result["registered_source_version_id"],),
                ).fetchone()
                self.assertIn("실제 PostgreSQL에 저장된 일반 텍스트 지식", evidence_payload["text"])
                index_payload = connection.execute(
                    "SELECT canonical_json FROM index_versions WHERE source_version_id=%s",
                    (result["registered_source_version_id"],),
                ).fetchone()[0]
                self.assertEqual(index_payload["chunks"][0]["evidence_span_id"], evidence_span_id)
                self._canonical(connection, "runs", "run-knowledge-citation", {"source_id": registered_source_id}, state="accepted")
                self._canonical(
                    connection, "run_results", "result-knowledge-citation",
                    {"answer": "실제 지식", "insufficient": False},
                    extra=(("run_id", "run-knowledge-citation"),),
                )
                self._canonical(
                    connection, "citations", "citation-knowledge-it", {
                        "run_result_id": "result-knowledge-citation",
                        "source_id": registered_source_id,
                        "source_version_id": result["registered_source_version_id"],
                        "evidence_span_id": evidence_span_id,
                        "page": 1,
                        "origin": "daon_knowledge",
                        "context_item_id": registration_id,
                        "locator": {"kind": "section", "value": evidence_span_id},
                    },
                    extra=(
                        ("run_result_id", "result-knowledge-citation"),
                        ("source_version_id", result["registered_source_version_id"]),
                        ("evidence_span_id", evidence_span_id),
                    ),
                )
            from daon_user_api.question_answering_postgres import (
                PostgresQuestionAnsweringRepository, QuestionContext,
            )
            text_content, locator = PostgresQuestionAnsweringRepository(
                store, object(),  # type: ignore[arg-type]
            ).read_citation_content(
                QuestionContext("tenant-it", "workspace-it", "actor-it", "trace-it", "policy-it"),
                "citation-knowledge-it",
            )
            self.assertEqual(text_content.media_type, "text/plain; charset=utf-8")
            self.assertIn("실제 PostgreSQL에 저장된 일반 텍스트 지식", text_content.content.decode())
            self.assertEqual(locator, {"kind": "section", "value": evidence_span_id})
        finally:
            store.close()

        def create_same_revision():
            concurrent_store = PostgresCloudStore(self.dsn)
            try:
                return PostgresStudioWorkspaceRepository(concurrent_store).create_version(
                    StudioContext("tenant-it", "workspace-it", "actor-it", "trace-it", "policy-it"),
                    "output-it",
                    {"previous_version_id": "output-version-it", "revision_type": "user_edit", "change_reason": "동시 재시도", "content": "동일 내용"},
                    "revision-concurrent-key-0001",
                )
            finally:
                concurrent_store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: create_same_revision(), range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(replayed for _result, replayed in results), [False, True])
        with psycopg.connect(self.dsn) as connection:
            version_count = connection.execute(
                "SELECT count(*) FROM output_versions WHERE tenant_id='tenant-it' AND workspace_id='workspace-it' AND studio_output_id='output-it'"
            ).fetchone()[0]
            idempotency_count = connection.execute(
                "SELECT count(*) FROM idempotency_records WHERE tenant_id='tenant-it' AND workspace_id='workspace-it' AND operation='studio.version.create' AND idempotency_key='revision-concurrent-key-0001'"
            ).fetchone()[0]
        self.assertEqual(version_count, 2)
        self.assertEqual(idempotency_count, 1)

        latest_version_id = results[0][0]["output_version_id"]
        lifecycle_store = PostgresCloudStore(self.dsn)
        object_storage = ObjectStorage()
        try:
            repository = PostgresStudioWorkspaceRepository(lifecycle_store, object_storage)
            lifecycle_context = StudioContext("tenant-it", "workspace-it", "actor-it", "trace-it", "policy-it")
            review, _ = repository.record_action(
                lifecycle_context, "review", {"output_version_id": latest_version_id}, "review-real-pg-0001",
            )
            approval_request, _ = repository.record_action(
                lifecycle_context, "approval_request", {"output_version_id": latest_version_id, "review_request_id": review["record_id"]}, "approval-request-pg-0001",
            )
            approval, _ = repository.record_action(
                lifecycle_context, "approval", {"output_version_id": latest_version_id, "approval_request_id": approval_request["record_id"], "decision": "approved", "step_up_verified": True}, "approval-real-pg-0001",
            )
            versions = repository.list_versions(lifecycle_context, "output-it")
            self.assertEqual(versions[0]["output_version_id"], latest_version_id)
            self.assertEqual(versions[0]["status"], "approved")
            self.assertEqual(versions[0]["review_request_id"], review["record_id"])
            self.assertEqual(versions[0]["approval_request_id"], approval_request["record_id"])
            self.assertEqual(versions[0]["approval_id"], approval["record_id"])
            self.assertEqual(len(versions[0]["citations"]), 1)
            exported = repository.export_output(lifecycle_context, "output-it", latest_version_id, "pdf")
            self.assertTrue(exported.content.startswith(b"%PDF-"))
            self.assertEqual(hashlib.sha256(exported.content).hexdigest(), exported.checksum_sha256)

            compliance_request = StudioGenerationRequest(
                output_type="compliance_checklist", source_id="source-original",
                source_version_ids=("source-version-original",), run_id="run-compliance-it",
                run_result_id="result-compliance-it", purpose="제약·준수 점검", audience="검토자",
                ruleset_version_id="ruleset-v3", length="standard", structure="checklist",
                output_format="xlsx", review_condition="approval_required",
            )
            compliance_content = build_structured_output(
                compliance_request, "원본과 승인 지식의 준수 여부를 확인한다.",
                [{"citation_id": "citation-original", "source_version_id": "source-version-original", "evidence_span_id": "span-original", "page": 2}],
                "generation-compliance-it",
            )
            with psycopg.connect(self.dsn) as connection:
                connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
                connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
                connection.execute("SELECT set_config('app.actor_id','actor-it',true)")
                self._canonical(connection, "generation_settings_snapshots", "settings-compliance-it", {"output_format": "xlsx", "ruleset_version_id": "ruleset-v3"})
                self._canonical(connection, "generation_requests", "generation-compliance-it", {"output_type": "compliance_checklist"}, state="configuring", extra=(("generation_settings_snapshot_id", "settings-compliance-it"),))
                generation_version = connection.execute("SELECT version FROM generation_requests WHERE record_id='generation-compliance-it'").fetchone()[0]
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-compliance-it',%s,'confirmed','transition-compliance-confirmed','IT','trace-it','policy-it')", (generation_version,))
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-compliance-it',%s,'submitted','transition-compliance-submitted','IT','trace-it','policy-it')", (generation_version + 1,))
                self._canonical(connection, "studio_outputs", "output-compliance-it", {"output_type": "compliance_checklist", "title": "제약·준수 점검표", "purpose": "제약·준수 점검"}, extra=(("generation_request_id", "generation-compliance-it"),))
                compliance_payload = {
                    "source_version_ids": ["source-version-original"], "output_type": "compliance_checklist",
                    "purpose": "제약·준수 점검", "audience": "검토자", "source_id": "source-original",
                    "run_id": "run-compliance-it", "run_result_id": "result-compliance-it",
                    "ruleset_version_id": "ruleset-v3", "length": "standard", "structure": "checklist",
                    "output_format": "xlsx", "review_condition": "approval_required",
                    "revision_type": "initial", "change_reason": "initial_generation", "content": compliance_content,
                }
                self._canonical(connection, "output_versions", "output-version-compliance-it", compliance_payload, state="generating", extra=(("studio_output_id", "output-compliance-it"), ("generation_settings_snapshot_id", "settings-compliance-it")))
                output_version = connection.execute("SELECT version FROM output_versions WHERE record_id='output-version-compliance-it'").fetchone()[0]
                for index, target in enumerate(("draft", "review_requested", "in_review", "approved")):
                    connection.execute("SELECT * FROM transition_canon_state('OutputVersion','output-version-compliance-it',%s,%s,%s,'IT','trace-it','policy-it')", (output_version + index, target, f"transition-compliance-{target}"))
                self._canonical(connection, "evidence_references", "evidence-compliance-it", {
                    "citation_id": "citation-original", "source_version_id": "source-version-original",
                    "evidence_span_id": "span-original", "origin": "raw_source", "locator": {"kind": "page", "value": "2"},
                }, extra=(("output_version_id", "output-version-compliance-it"), ("source_version_id", "source-version-original"), ("evidence_span_id", "span-original")))
            compliance_versions = repository.list_versions(lifecycle_context, "output-compliance-it")
            self.assertEqual(compliance_versions[0]["content"]["items"][0]["judgement"], "needs_review")
            self.assertEqual(compliance_versions[0]["content"]["lineage"]["ruleset_id"], "ruleset-v3")
            compliance_export = repository.export_output(lifecycle_context, "output-compliance-it", "output-version-compliance-it", "xlsx")
            self.assertTrue(compliance_export.content.startswith(b"PK"))
            self.assertEqual(hashlib.sha256(compliance_export.content).hexdigest(), compliance_export.checksum_sha256)

            comparison_request = StudioGenerationRequest(
                output_type="comparison_table", source_id="source-original",
                source_version_ids=("source-version-original",), run_id="run-comparison-it",
                run_result_id="result-comparison-it", purpose="근거 비교", audience="검토자",
                ruleset_version_id=None, length="standard", structure="table",
                output_format="xlsx", review_condition="review_required",
            )
            comparison_content = build_structured_output(
                comparison_request, "현재 근거의 핵심 값",
                [{"citation_id": "citation-original", "source_version_id": "source-version-original", "evidence_span_id": "span-original", "page": 2}],
                "generation-comparison-it",
            )
            with psycopg.connect(self.dsn) as connection:
                connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
                connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
                connection.execute("SELECT set_config('app.actor_id','actor-it',true)")
                self._canonical(connection, "generation_settings_snapshots", "settings-comparison-it", {"output_format": "xlsx"})
                self._canonical(connection, "generation_requests", "generation-comparison-it", {"output_type": "comparison_table"}, state="configuring", extra=(("generation_settings_snapshot_id", "settings-comparison-it"),))
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-comparison-it',1,'confirmed','transition-comparison-confirmed','IT','trace-it','policy-it')")
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-comparison-it',2,'submitted','transition-comparison-submitted','IT','trace-it','policy-it')")
                self._canonical(connection, "studio_outputs", "output-comparison-it", {"output_type": "comparison_table", "title": "비교·데이터 표", "purpose": "근거 비교"}, extra=(("generation_request_id", "generation-comparison-it"),))
                comparison_payload = {
                    "source_version_ids": ["source-version-original"], "output_type": "comparison_table",
                    "purpose": "근거 비교", "audience": "검토자", "source_id": "source-original",
                    "run_id": "run-comparison-it", "run_result_id": "result-comparison-it",
                    "ruleset_version_id": None, "length": "standard", "structure": "table",
                    "output_format": "xlsx", "review_condition": "review_required",
                    "revision_type": "initial", "change_reason": "initial_generation", "content": comparison_content,
                }
                self._canonical(connection, "output_versions", "output-version-comparison-it", comparison_payload, state="generating", extra=(("studio_output_id", "output-comparison-it"), ("generation_settings_snapshot_id", "settings-comparison-it")))
                for index, target in enumerate(("draft", "review_requested", "in_review", "approved"), 1):
                    connection.execute("SELECT * FROM transition_canon_state('OutputVersion','output-version-comparison-it',%s,%s,%s,'IT','trace-it','policy-it')", (index, target, f"transition-comparison-{target}"))
                self._canonical(connection, "evidence_references", "evidence-comparison-it", {
                    "citation_id": "citation-original", "source_version_id": "source-version-original",
                    "evidence_span_id": "span-original", "origin": "raw_source", "locator": {"kind": "page", "value": "2"},
                }, extra=(("output_version_id", "output-version-comparison-it"), ("source_version_id", "source-version-original"), ("evidence_span_id", "span-original")))
            comparison_versions = repository.list_versions(lifecycle_context, "output-comparison-it")
            self.assertEqual(comparison_versions[0]["content"]["rows"][0]["state"], "changed")
            self.assertEqual(comparison_versions[0]["content"]["rows"][0]["evidence"], ["citation-original page 2", "citation-original page 2"])
            comparison_export = repository.export_output(lifecycle_context, "output-comparison-it", "output-version-comparison-it", "xlsx")
            self.assertTrue(comparison_export.content.startswith(b"PK"))
            self.assertEqual(hashlib.sha256(comparison_export.content).hexdigest(), comparison_export.checksum_sha256)

            map_request = StudioGenerationRequest(
                output_type="knowledge_map", source_id="source-original",
                source_version_ids=("source-version-original",), run_id="run-map-it",
                run_result_id="result-map-it", purpose="지식 구조", audience="검토자",
                ruleset_version_id=None, length="standard", structure="graph",
                output_format="json", review_condition="review_required",
            )
            map_content = build_structured_output(
                map_request, "근거의 구조를 연결한다.",
                [{"citation_id": "citation-original", "source_version_id": "source-version-original", "evidence_span_id": "span-original", "page": 2}],
                "generation-map-it",
            )
            with psycopg.connect(self.dsn) as connection:
                connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
                connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
                connection.execute("SELECT set_config('app.actor_id','actor-it',true)")
                self._canonical(connection, "generation_settings_snapshots", "settings-map-it", {"output_format": "json"})
                self._canonical(connection, "generation_requests", "generation-map-it", {"output_type": "knowledge_map"}, state="configuring", extra=(("generation_settings_snapshot_id", "settings-map-it"),))
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-map-it',1,'confirmed','transition-map-confirmed','IT','trace-it','policy-it')")
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-map-it',2,'submitted','transition-map-submitted','IT','trace-it','policy-it')")
                self._canonical(connection, "studio_outputs", "output-map-it", {"output_type": "knowledge_map", "title": "지식 구조도", "purpose": "지식 구조"}, extra=(("generation_request_id", "generation-map-it"),))
                map_payload = {
                    "source_version_ids": ["source-version-original"], "output_type": "knowledge_map",
                    "purpose": "지식 구조", "audience": "검토자", "source_id": "source-original",
                    "run_id": "run-map-it", "run_result_id": "result-map-it",
                    "ruleset_version_id": None, "length": "standard", "structure": "graph",
                    "output_format": "json", "review_condition": "review_required",
                    "revision_type": "initial", "change_reason": "initial_generation", "content": map_content,
                }
                self._canonical(connection, "output_versions", "output-version-map-it", map_payload, state="generating", extra=(("studio_output_id", "output-map-it"), ("generation_settings_snapshot_id", "settings-map-it")))
                for index, target in enumerate(("draft", "review_requested", "in_review", "approved"), 1):
                    connection.execute("SELECT * FROM transition_canon_state('OutputVersion','output-version-map-it',%s,%s,%s,'IT','trace-it','policy-it')", (index, target, f"transition-map-{target}"))
                self._canonical(connection, "evidence_references", "evidence-map-it", {
                    "citation_id": "citation-original", "source_version_id": "source-version-original",
                    "evidence_span_id": "span-original", "origin": "raw_source", "locator": {"kind": "page", "value": "2"},
                }, extra=(("output_version_id", "output-version-map-it"), ("source_version_id", "source-version-original"), ("evidence_span_id", "span-original")))
            map_versions = repository.list_versions(lifecycle_context, "output-map-it")
            self.assertEqual(map_versions[0]["content"]["nodes"][0]["confidence"], "verified")
            self.assertEqual(map_versions[0]["content"]["nodes"][0]["evidence"], "page 2")
            map_export = repository.export_output(lifecycle_context, "output-map-it", "output-version-map-it", "json")
            self.assertTrue(map_export.content.startswith(b'{"title"'))
            self.assertEqual(hashlib.sha256(map_export.content).hexdigest(), map_export.checksum_sha256)

            draft_request = StudioGenerationRequest(
                output_type="business_draft", source_id="source-original",
                source_version_ids=("source-version-original",), run_id="run-draft-it",
                run_result_id="result-draft-it", purpose="업무 문서", audience="검토자",
                ruleset_version_id=None, length="standard", structure="letter",
                output_format="docx", review_condition="review_required",
            )
            draft_content = build_structured_output(
                draft_request, "원본 근거를 반영한 업무 문서 본문",
                [{"citation_id": "citation-original", "source_version_id": "source-version-original", "evidence_span_id": "span-original", "page": 2}],
                "generation-draft-it",
            )
            with psycopg.connect(self.dsn) as connection:
                connection.execute("SELECT set_config('app.tenant_id','tenant-it',true)")
                connection.execute("SELECT set_config('app.workspace_id','workspace-it',true)")
                connection.execute("SELECT set_config('app.actor_id','actor-it',true)")
                self._canonical(connection, "generation_settings_snapshots", "settings-draft-it", {"output_format": "docx"})
                self._canonical(connection, "generation_requests", "generation-draft-it", {"output_type": "business_draft"}, state="configuring", extra=(("generation_settings_snapshot_id", "settings-draft-it"),))
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-draft-it',1,'confirmed','transition-draft-confirmed','IT','trace-it','policy-it')")
                connection.execute("SELECT * FROM transition_canon_state('GenerationRequest','generation-draft-it',2,'submitted','transition-draft-submitted','IT','trace-it','policy-it')")
                self._canonical(connection, "studio_outputs", "output-draft-it", {"output_type": "business_draft", "title": "업무 문서 초안", "purpose": "업무 문서"}, extra=(("generation_request_id", "generation-draft-it"),))
                draft_payload = {
                    "source_version_ids": ["source-version-original"], "output_type": "business_draft",
                    "purpose": "업무 문서", "audience": "검토자", "source_id": "source-original",
                    "run_id": "run-draft-it", "run_result_id": "result-draft-it",
                    "ruleset_version_id": None, "length": "standard", "structure": "letter",
                    "output_format": "docx", "review_condition": "review_required",
                    "revision_type": "initial", "change_reason": "initial_generation", "content": draft_content,
                }
                self._canonical(connection, "output_versions", "output-version-draft-it", draft_payload, state="generating", extra=(("studio_output_id", "output-draft-it"), ("generation_settings_snapshot_id", "settings-draft-it")))
                for index, target in enumerate(("draft", "review_requested", "in_review", "approved"), 1):
                    connection.execute("SELECT * FROM transition_canon_state('OutputVersion','output-version-draft-it',%s,%s,%s,'IT','trace-it','policy-it')", (index, target, f"transition-draft-{target}"))
                self._canonical(connection, "evidence_references", "evidence-draft-it", {
                    "citation_id": "citation-original", "source_version_id": "source-version-original",
                    "evidence_span_id": "span-original", "origin": "raw_source", "locator": {"kind": "page", "value": "2"},
                }, extra=(("output_version_id", "output-version-draft-it"), ("source_version_id", "source-version-original"), ("evidence_span_id", "span-original")))
            draft_versions = repository.list_versions(lifecycle_context, "output-draft-it")
            self.assertEqual(draft_versions[0]["content"]["review_state"], "draft")
            self.assertEqual(draft_versions[0]["content"]["sections"][0]["evidence"], ["citation-original page 2"])
            draft_export = repository.export_output(lifecycle_context, "output-draft-it", "output-version-draft-it", "docx")
            self.assertTrue(draft_export.content.startswith(b"PK"))
            self.assertEqual(hashlib.sha256(draft_export.content).hexdigest(), draft_export.checksum_sha256)
            library = repository.list_outputs(lifecycle_context)
            self.assertEqual(
                {item["output_type"] for item in library["outputs"]},
                {"evidence_report", "compliance_checklist", "comparison_table", "knowledge_map", "business_draft"},
            )
            self.assertTrue(all(item["output_version_id"] for item in library["outputs"]))
            self.assertEqual(len(library["studio_locks"]), 6)
        finally:
            lifecycle_store.close()

        run_snapshot_payload = {
            "source_version_ids": ["source-version-original"], "knowledge_scope_id": "scope-a2",
            "authority": ["source"], "weights_requested": {}, "weights_effective": {}, "weight_clamps": [],
            "ruleset_snapshot_ids": [], "routing_policy_version_id": "routing-policy-a2",
            "candidate_order": [], "data_area": "workspace", "data_classification": "workspace_private",
            "egress_decision_id": None, "user_policy_version": "policy-it",
            "organization_policy_version": "policy-it", "cost_limit": 0, "currency": "USD",
            "prompt_version": "a2-prompt-v1", "tool_version": "a2-tool-v1",
        }
        with psycopg.connect(self.dsn) as connection:
            self._canonical(connection, "knowledge_scopes", "scope-a2", {"mode": "single_source", "source_version_ids": ["source-version-original"]})
            self._canonical(connection, "scope_snapshots", "scope-snapshot-a2", {"knowledge_scope_id": "scope-a2"}, extra=(("knowledge_scope_id", "scope-a2"),))
            self._canonical(connection, "routing_policy_versions", "routing-policy-a2", {"candidate_order": []})
            self._canonical(connection, "runs", "run-a2", {"source_id": "source-original"}, state="accepted")
            self._canonical(connection, "run_snapshots", "run-snapshot-a2", run_snapshot_payload, extra=(("run_id", "run-a2"), ("scope_snapshot_id", "scope-snapshot-a2"), ("routing_policy_version_id", "routing-policy-a2")))
        with psycopg.connect(self.dsn) as connection:
            with self.assertRaises(psycopg.Error):
                with connection.transaction():
                    connection.execute("UPDATE run_snapshots SET canonical_text='{}' WHERE record_id='run-snapshot-a2'")
            self.assertEqual(connection.execute("SELECT count(*) FROM run_snapshots WHERE record_id='run-snapshot-a2'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
