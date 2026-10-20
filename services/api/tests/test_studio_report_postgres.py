from __future__ import annotations

import unittest
import json
import re
from unittest.mock import patch

from daon_user_api.cloud_storage import CloudDatabaseError
from daon_user_api.studio_report import (
    StudioReportContext, StudioReportCreateRequest, StudioReportError, StudioReportService,
)
from daon_user_api.studio_report_postgres import PostgresStudioReportRepository
from daon_user_api.license import LicenseError
from daon_user_api.open_law_source_access import OpenLawAccessError


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
        if sql.startswith('SELECT canonical_json FROM source_versions'):return FakeResult(({},))
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
        if sql.startswith('SELECT canonical_json FROM source_versions'):return FakeResult(({},))
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


class OutputListConnection:
    """SQL-shaped read seam; this is not a PostgreSQL execution claim."""

    def __init__(self, outputs, *, law_versions=()):
        self.outputs = outputs
        self.law_versions = law_versions
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if sql.startswith('SELECT canonical_json FROM source_versions'):
            payload = ({'origin_kind': 'mcp_open_law', 'connector_id': 'mcp-open-law-go-kr',
                        'snapshot_id': 'snapshot-law'} if params[-1] in self.law_versions else {})
            return FakeResult((payload,))
        if 'FROM output_versions ov' in sql or 'FROM studio_outputs so' in sql:
            # Without a bounded latest-version selection, expose all historical
            # references so the real repository's grouping cannot hide the bug.
            latest_only = 'JOIN LATERAL' in sql and re.search(
                r'ORDER BY \w+\.content_version DESC,\w+\.record_id DESC LIMIT 1', sql,
            )
            rows = []
            for output_id, parent, versions in self.outputs:
                chosen = sorted(versions, key=lambda item: (item[0], item[1]), reverse=True)
                if latest_only:
                    chosen = chosen[:1]
                for _number, version_id, payload, references in chosen:
                    for reference in references or (None,):
                        rows.append((payload, 'draft', output_id, version_id, reference, parent))
            return FakeResult(rows=rows)
        raise AssertionError(f'Unexpected read: {sql}')


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

    @staticmethod
    def output_version(number=1, *, modern=False, extra_reference_fields=False):
        reference = {'citation_id': f'citation-{number}', 'source_id': 'source-1',
                     'source_version_id': f'sv-{number}', 'evidence_span_id': f'span-{number}', 'page': 1}
        if extra_reference_fields:
            reference.update(origin='raw_source', context_item_id='source-1',
                             locator={'kind': 'section', 'value': f'span-{number}'})
        payload = {'output_type': 'evidence_report', 'purpose': '버전별 목적',
                   'content': '원문 그대로\n"근거"', 'run_id': 'run-1', 'run_result_id': 'result-1'}
        if modern:
            payload['content'] = {'title': '구조화 원문', 'sections': [
                {'heading': '본문', 'text': '한글 "인용"\n둘째 줄', 'amount': 57000,
                 'confirmed': False, 'optional': None}], 'references': [reference.copy()]}
        else:
            payload['title'] = '과거 버전 제목'
        return number, f'version-{number}', payload, [reference]

    def test_list_outputs_reads_parent_metadata_for_mixed_legacy_and_modern(self):
        legacy = self.output_version()
        modern = self.output_version(2, modern=True, extra_reference_fields=True)
        connection = OutputListConnection([
            ('output-legacy', {'output_type': 'evidence_report', 'title': '부모 제목 1', 'purpose': '부모 목적 1'}, [legacy]),
            ('output-modern', {'output_type': 'evidence_report', 'title': '부모 제목 2', 'purpose': '부모 목적 2'}, [modern]),
        ])
        outputs = PostgresStudioReportRepository(FakeCloud(connection)).list_outputs(self.context)
        self.assertEqual([(item.studio_output_id, item.output_type, item.title, item.purpose) for item in outputs], [
            ('output-legacy', 'evidence_report', '부모 제목 1', '부모 목적 1'),
            ('output-modern', 'evidence_report', '부모 제목 2', '부모 목적 2'),
        ])
        self.assertEqual(outputs[0].content, '원문 그대로\n"근거"')
        self.assertEqual(json.loads(outputs[1].content), modern[2]['content'])
        self.assertEqual([(item.citation_id, item.source_id, item.source_version_id,
                           item.evidence_span_id, item.page) for item in outputs[1].citations],
                         [('citation-2', 'source-1', 'sv-2', 'span-2', 1)])

    def test_list_outputs_returns_latest_version_and_only_its_references(self):
        connection = OutputListConnection([
            ('output-1', {'output_type': 'evidence_report', 'title': '현재 제목', 'purpose': '현재 목적'},
             [self.output_version(1), self.output_version(2)]),
        ])
        outputs = PostgresStudioReportRepository(FakeCloud(connection)).list_outputs(self.context)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].output_version_id, 'version-2')
        self.assertEqual([item.citation_id for item in outputs[0].citations], ['citation-2'])
        self.assertEqual([params[-1] for sql, params in connection.statements
                          if sql.startswith('SELECT canonical_json FROM source_versions')], ['sv-2'])

    def test_list_outputs_keeps_scope_and_denies_whole_version_on_law_access_failure(self):
        for enabled in ('false', 'true'):
            with self.subTest(enabled=enabled), patch.dict('os.environ', {
                'DAON_OPEN_LAW_ENABLED': enabled,
                'DAON_OPEN_LAW_QA_WORKSPACE_ID': self.context.workspace_id,
            }), patch('daon_user_api.open_law_source_access.current_open_law_policy',
                      side_effect=OpenLawAccessError('OPEN_LAW_ACCESS_DENIED')):
                version = self.output_version(2)
                version[3].append(self.output_version(3)[3][0])
                parent = {'output_type': 'evidence_report', 'title': '허용 제목', 'purpose': '허용 목적'}
                connection = OutputListConnection([
                    ('output-visible', parent, [self.output_version(1)]),
                    ('output-blocked', parent, [self.output_version(1), version]),
                ], law_versions=('sv-2',))
                outputs = PostgresStudioReportRepository(FakeCloud(connection)).list_outputs(self.context)
                self.assertEqual([item.studio_output_id for item in outputs], ['output-visible'])
                sql, params = connection.statements[0]
                self.assertIn('nb.notebook_id=%s', sql)
                self.assertIn("nb.binding_kind='studio_output'", sql)
                self.assertIn('so.tenant_id=%s', sql)
                self.assertIn('so.workspace_id=%s', sql)
                self.assertEqual(params, ('notebook-1', 'tenant-1', 'workspace-1'))
                self.assertTrue(all(statement.lstrip().startswith('SELECT') for statement, _ in connection.statements))

    def test_list_outputs_requires_notebook_and_does_not_skip_malformed_parent(self):
        from dataclasses import replace
        connection = OutputListConnection([])
        with self.assertRaisesRegex(StudioReportError, '^NOTEBOOK_SCOPE_REQUIRED$'):
            PostgresStudioReportRepository(FakeCloud(connection)).list_outputs(replace(self.context, notebook_id=None))
        self.assertEqual(connection.statements, [])
        connection = OutputListConnection([
            ('output-bad', {'output_type': 'evidence_report', 'purpose': 'missing title'}, [self.output_version()]),
        ])
        with self.assertRaisesRegex(StudioReportError, '^STUDIO_RESULT_INVALID$'):
            PostgresStudioReportRepository(FakeCloud(connection)).list_outputs(self.context)

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

    def test_registered_knowledge_projection_requires_verified_catalog_flag_and_ready_source(self) -> None:
        class ProjectionConnection(FakeConnection):
            def execute(self, sql, params=()):
                if sql.startswith('SELECT canonical_json FROM source_versions'):return FakeResult(({},))
                self.statements.append((sql, params))
                rows = self.source_rows if "AS approved_knowledge" in sql else [row[:6] for row in self.source_rows]
                return FakeResult(rows=rows)
        connection = ProjectionConnection(source_rows=(
            ("source-k", "version-k", "knowledge.json", "ready", "accepted", "pending", True),
            ("source-no-registration", "version-2", "knowledge.json", "ready", "accepted", "pending", False),
            ("source-not-ready", "version-3", "knowledge.json", "processing", "accepted", "pending", True),
            ("source-delivered", "version-4", "knowledge.json", "ready", "accepted", "pending", True),
            ("source-no-approval", "version-5", "knowledge.json", "ready", "accepted", "pending", False),
            ("source-untrusted-flag", "version-6", "knowledge.json", "ready", "accepted", "pending", "true"),
            ("source-legacy", "version-7", "knowledge.json", "ready", "accepted", "pending"),
        ))
        sources = PostgresStudioReportRepository(FakeCloud(connection)).list_sources(self.context)
        self.assertEqual((sources[0].processing_state, sources[0].job_state), ("completed", "completed"))
        self.assertEqual((sources[1].processing_state, sources[1].job_state), ("accepted", "pending"))
        self.assertEqual((sources[2].processing_state, sources[2].job_state), ("accepted", "pending"))
        self.assertEqual((sources[3].processing_state, sources[3].job_state), ("completed", "completed"))
        self.assertEqual((sources[4].processing_state, sources[4].job_state), ("accepted", "pending"))
        self.assertEqual((sources[5].processing_state, sources[5].job_state), ("accepted", "pending"))
        self.assertEqual((sources[6].processing_state, sources[6].job_state), ("accepted", "pending"))
        sql = connection.statements[0][0]
        for predicate in ("kr.state='registered'", "kr.registered_source_version_id=sv.record_id", "ov.state IN ('approved','delivered')",
            "approval.output_version_id=ov.record_id", "approval.decision='approved'",
            "bound.notebook_id=nb.notebook_id", "bound.binding_kind='output_version'", "index_versions", "evidence_spans",
            "approved_knowledge_snapshot", "pr.record_id IS NULL", "oq.state IS NULL"):
            self.assertIn(predicate, sql)

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
