from __future__ import annotations

import unittest
import json

from daon_user_api.cloud_storage import CloudDatabaseError
from daon_user_api.studio_report import (
    StudioReportContext, StudioReportCreateRequest, StudioReportError, StudioReportService,
)
from daon_user_api.studio_report_postgres import PostgresStudioReportRepository
from daon_user_api.license import LicenseError


class FakeResult:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = rows

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.row

    def fetchall(self):  # type: ignore[no-untyped-def]
        return self.rows


class FakeConnection:
    def __init__(self, *, citations=True, insufficient=False, source_rows=(), replay=None, audit_failure=False):
        self.statements = []
        self.citations = citations
        self.insufficient = insufficient
        self.source_rows = source_rows
        self.replay = replay
        self.audit_failure = audit_failure

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        self.statements.append((sql, params))
        if "FROM idempotency_records" in sql:
            return FakeResult(self.replay)
        if "FROM runs r JOIN run_results" in sql:
            return FakeResult((
                {"source_id": "source-1", "source_version_id": "source-version-1"},
                {"answer": "근거 답변", "insufficient": self.insufficient}, "ready",
            ))
        if "FROM citations c" in sql:
            rows = (("citation-1", "source-version-1", "span-1", {"source_id": "source-1", "page": 2}),) if self.citations else ()
            return FakeResult(rows=rows)
        if "FROM sources s" in sql:
            return FakeResult(rows=self.source_rows)
        if "transition_canon_state" in sql:
            return FakeResult((params[3], 2, "succeeded", None))
        if "INSERT INTO audit_events" in sql and self.audit_failure:
            raise CloudDatabaseError("AUDIT_WRITE_FAILED", retryable=False)
        return FakeResult()


class SourceLineageConnection:
    """SQL fixture that exposes cross-run state synthesis when lineage predicates are absent."""

    def __init__(self):
        self.statement = ""

    def execute(self, sql, _params=()):  # type: ignore[no-untyped-def]
        self.statement = sql
        deterministic_run = "ORDER BY created_at DESC,record_id DESC" in sql
        same_run_job = "processing_run_id=pr.record_id" in sql
        if deterministic_run and same_run_job:
            return FakeResult(rows=((
                "source-1", "source-version-1", "report.pdf", "ready", "processing", "pending",
            ),))
        return FakeResult(rows=((
            "source-1", "source-version-1", "report.pdf", "ready", "completed", "completed",
        ),))


class StatefulIdempotencyConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.idempotency_result = None

    def execute(self, sql, params=()):  # type: ignore[no-untyped-def]
        if "FROM idempotency_records" in sql:
            self.statements.append((sql, params))
            return FakeResult(self.idempotency_result)
        result = super().execute(sql, params)
        if "INSERT INTO idempotency_records" in sql:
            self.idempotency_result = (params[5], json.loads(params[6]))
        return result


class FakeTransaction:
    def __init__(self, cloud):
        self.cloud = cloud

    def __enter__(self):
        return self.cloud.connection

    def __exit__(self, exc_type, *_args):
        self.cloud.committed = exc_type is None
        self.cloud.rolled_back = exc_type is not None
        return False


class FakeCloud:
    def __init__(self, connection):
        self.connection = connection
        self.committed = False
        self.rolled_back = False

    def _transaction(self, _context):
        return FakeTransaction(self)


class StudioReportPostgresTests(unittest.TestCase):
    def test_transactional_license_capability_requires_postgres_enforcer(self) -> None:
        cloud = FakeCloud(FakeConnection())
        plain = StudioReportService(PostgresStudioReportRepository(cloud))
        enforced = StudioReportService(PostgresStudioReportRepository(
            cloud, creation_enforcer=lambda *_args: None,
        ))

        self.assertFalse(plain.creation_license_authoritative)
        self.assertTrue(enforced.creation_license_authoritative)

    def setUp(self) -> None:
        self.context = StudioReportContext("tenant-1", "workspace-1", "actor-1", "trace-1", "policy-1", "notebook-1")
        self.request = StudioReportCreateRequest(
            "source-1", "source-version-1", "run-1", "result-1", "승인 검토 보고서", "근거 기반 요약",
        )

    def test_create_uses_one_canon_transaction_and_records_complete_lineage(self) -> None:
        connection = FakeConnection()
        output, replayed = PostgresStudioReportRepository(FakeCloud(connection)).create_report(
            self.context, self.request, "report-1",
        )
        self.assertFalse(replayed)
        self.assertIn("# 승인 검토 보고서", output.content)
        self.assertIn("## 요약\n근거 답변", output.content)
        self.assertIn("## 본문\n근거 답변", output.content)
        self.assertIn("## 결론", output.content)
        sql = "\n".join(statement for statement, _ in connection.statements)
        for table in [
            "generation_settings_snapshots", "generation_requests", "studio_outputs",
            "output_versions", "evidence_references", "audit_events", "idempotency_records",
        ]:
            self.assertIn(f"INSERT INTO {table}", sql)
        self.assertIn("solar-pro4", str(connection.statements))

    def test_report_enforces_generation_and_output_quota_in_the_same_transaction(self) -> None:
        connection = FakeConnection()
        cloud = FakeCloud(connection)
        calls = []
        def deny(active_connection, tenant_id, action, increments):
            calls.append((active_connection, tenant_id, action, increments))
            raise LicenseError("LICENSE_RESOURCE_LIMIT_REACHED", 409)
        with self.assertRaisesRegex(LicenseError, "LICENSE_RESOURCE_LIMIT_REACHED"):
            PostgresStudioReportRepository(
                cloud, creation_enforcer=deny,
            ).create_report(self.context, self.request, "report-license-limit-01")
        self.assertEqual(calls, [(connection, "tenant-1", "studio.generate", {
            "generation_runs": 1, "studio_outputs": 1,
        })])
        self.assertTrue(cloud.rolled_back)
        self.assertFalse(any("INSERT INTO" in sql for sql, _ in connection.statements))

    def test_zero_citations_and_insufficient_are_evidence_required(self) -> None:
        for connection in [FakeConnection(citations=False), FakeConnection(insufficient=True)]:
            with self.assertRaisesRegex(StudioReportError, "EVIDENCE_REQUIRED"):
                PostgresStudioReportRepository(FakeCloud(connection)).create_report(
                    self.context, self.request, "report-1",
                )

    def test_source_list_reads_the_durable_processing_job_schema(self) -> None:
        connection = FakeConnection(source_rows=((
            "source-1", "source-version-1", "report.pdf", "ready", "completed", "completed",
        ),))
        sources = PostgresStudioReportRepository(FakeCloud(connection)).list_sources(self.context)
        self.assertEqual(sources[0].job_state, "completed")
        sql = connection.statements[0][0]
        self.assertIn("document_processing_jobs", sql)
        self.assertNotIn("object_queue", sql)
        self.assertIn("tenant_id=sv.tenant_id", sql)
        self.assertIn("workspace_id=sv.workspace_id", sql)
        self.assertIn("JOIN notebook_bindings", sql)
        self.assertIn("binding_kind='source'", sql)
        self.assertEqual(connection.statements[0][1], ("notebook-1",))

    def test_source_list_never_combines_processing_and_job_state_from_different_runs(self) -> None:
        connection = SourceLineageConnection()
        source = PostgresStudioReportRepository(FakeCloud(connection)).list_sources(self.context)[0]
        self.assertEqual((source.processing_state, source.job_state), ("processing", "pending"))
        self.assertIn("ORDER BY created_at DESC,record_id DESC", connection.statement)
        self.assertIn("processing_run_id=pr.record_id", connection.statement)

    def test_idempotency_replay_has_zero_generation_transition_insert_audit_or_provider_side_effects(self) -> None:
        replay = ({
            "notebook_id": self.context.notebook_id,
            "source_id": self.request.source_id, "source_version_id": self.request.source_version_id,
            "run_id": self.request.run_id, "run_result_id": self.request.run_result_id,
            "title": self.request.title, "purpose": self.request.purpose,
        })
        import hashlib
        from daon_user_api.data_canon import canonical_json_bytes
        fingerprint = hashlib.sha256(canonical_json_bytes(replay)).hexdigest()
        stored = {
            "studio_output_id": "output-1", "output_version_id": "version-1",
            "output_type": "evidence_report", "title": self.request.title,
            "purpose": self.request.purpose, "status": "draft", "content": "근거 보고서",
            "run_id": self.request.run_id, "run_result_id": self.request.run_result_id,
            "citations": [{
                "citation_id": "citation-1", "source_id": self.request.source_id,
                "source_version_id": self.request.source_version_id,
                "evidence_span_id": "span-1", "page": 2,
            }],
        }
        connection = FakeConnection(replay=(fingerprint, stored))
        provider_calls = []
        output, replayed = PostgresStudioReportRepository(
            FakeCloud(connection), generation_provider=lambda request, answer: provider_calls.append((request, answer)),
        ).create_report(self.context, self.request, "report-replay-01")
        self.assertTrue(replayed)
        self.assertEqual(output.studio_output_id, "output-1")
        sql = "\n".join(statement for statement, _ in connection.statements)
        self.assertNotIn("transition_canon_state", sql)
        self.assertNotIn("INSERT INTO", sql)
        self.assertNotIn("audit_events", sql)
        self.assertEqual(provider_calls, [])

    def test_audit_failure_rolls_back_the_entire_report_transaction(self) -> None:
        cloud = FakeCloud(FakeConnection(audit_failure=True))
        with self.assertRaisesRegex(StudioReportError, "STUDIO_DATABASE_UNAVAILABLE"):
            PostgresStudioReportRepository(cloud).create_report(
                self.context, self.request, "report-audit-fail-01",
            )
        self.assertTrue(cloud.rolled_back)
        self.assertFalse(cloud.committed)
        sql = "\n".join(statement for statement, _ in cloud.connection.statements)
        self.assertIn("INSERT INTO audit_events", sql)
        self.assertNotIn("INSERT INTO idempotency_records", sql)

    def test_same_scope_replay_returns_same_ids_without_new_side_effects(self) -> None:
        connection = StatefulIdempotencyConnection()
        provider_calls = []
        repository = PostgresStudioReportRepository(
            FakeCloud(connection),
            generation_provider=lambda request, answer: provider_calls.append((request, answer)) or "근거 보고서",
        )
        first, first_replayed = repository.create_report(
            self.context, self.request, "same-scope-key-01",
        )
        mutation_count = sum(
            "INSERT INTO" in sql or "transition_canon_state" in sql
            for sql, _ in connection.statements
        )
        replay, replayed = repository.create_report(
            self.context, self.request, "same-scope-key-01",
        )
        self.assertFalse(first_replayed)
        self.assertTrue(replayed)
        self.assertEqual(
            (replay.studio_output_id, replay.output_version_id),
            (first.studio_output_id, first.output_version_id),
        )
        self.assertEqual(sum(
            "INSERT INTO" in sql or "transition_canon_state" in sql
            for sql, _ in connection.statements
        ), mutation_count)
        self.assertEqual(len(provider_calls), 1)

    def test_same_key_in_different_actor_or_workspace_produces_independent_ids(self) -> None:
        connection = FakeConnection()
        repository = PostgresStudioReportRepository(FakeCloud(connection))
        contexts = (
            self.context,
            StudioReportContext("tenant-1", "workspace-1", "actor-2", "trace-2", "policy-1", "notebook-1"),
            StudioReportContext("tenant-1", "workspace-2", "actor-1", "trace-3", "policy-1", "notebook-2"),
        )
        outputs = [
            repository.create_report(context, self.request, "shared-scope-key-01")[0]
            for context in contexts
        ]
        self.assertEqual(len({output.studio_output_id for output in outputs}), 3)
        self.assertEqual(len({output.output_version_id for output in outputs}), 3)
        for table in (
            "generation_settings_snapshots", "generation_requests", "studio_outputs",
            "output_versions", "evidence_references",
        ):
            ids = [params[2] for sql, params in connection.statements if f"INSERT INTO {table}" in sql]
            self.assertEqual(len(ids), 3)
            self.assertEqual(len(set(ids)), 3, table)
        audit_ids = [params[0] for sql, params in connection.statements if "INSERT INTO audit_events" in sql]
        self.assertEqual(len(audit_ids), 3)
        self.assertEqual(len(set(audit_ids)), 3)
        transition_ids = [params[4] for sql, params in connection.statements if "transition_canon_state" in sql]
        self.assertEqual(len(transition_ids), 9)
        self.assertEqual(len(set(transition_ids)), 9)
        lookup_params = [params for sql, params in connection.statements if "FROM idempotency_records" in sql]
        self.assertTrue(all(params[:2] == (context.tenant_id, context.workspace_id)
                            for params, context in zip(lookup_params, contexts, strict=True)))


if __name__ == "__main__":
    unittest.main()
