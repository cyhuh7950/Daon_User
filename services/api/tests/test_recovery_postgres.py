from __future__ import annotations

import hashlib
import os
import secrets
import unittest
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from daon_user_api import recovery_postgres
from daon_user_api.data_canon import canonical_json_bytes

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.object_queue import (
    DurableObjectWorker,
    MinioObjectStorageAdapter,
    ObjectQueueCoordinator,
    PostgresObjectQueueStore,
)
from daon_user_api.recovery import BackupObjectInput, RecoveryContext, RestoreDestination
from daon_user_api.runtime import RuntimeSettings, build_dependencies
from daon_user_api.recovery_postgres import (
    MinioRecoveryStorageAdapter,
    PostgresRecoveryService,
)


def _environment_or_file(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    file_name = os.environ.get(f"{name}_FILE")
    if not file_name:
        return None
    return Path(file_name).read_text(encoding="utf-8").strip()


def _environment_or_bytes(name: str) -> bytes | None:
    value = os.environ.get(name)
    if value:
        return value.encode("utf-8")
    file_name = os.environ.get(f"{name}_FILE")
    if not file_name:
        return None
    return Path(file_name).read_bytes()


DSN = _environment_or_file("DAON_RECOVERY_INTEGRATION_DSN")
ENDPOINT = os.environ.get("DAON_RECOVERY_INTEGRATION_OBJECT_ENDPOINT")
BUCKET = os.environ.get("DAON_RECOVERY_INTEGRATION_BUCKET")
ACCESS_KEY = _environment_or_file("DAON_RECOVERY_INTEGRATION_ACCESS_KEY")
SECRET_KEY = _environment_or_file("DAON_RECOVERY_INTEGRATION_SECRET_KEY")
MANIFEST_KEY = _environment_or_bytes("DAON_RECOVERY_INTEGRATION_MANIFEST_KEY")
CONFIGURED = all((DSN, ENDPOINT, BUCKET, ACCESS_KEY, SECRET_KEY, MANIFEST_KEY))


class RecoverySelectionResolverTests(unittest.TestCase):
    """실제 resolver의 SQL 읽기 포트만 대역. PG/RLS·capture 성공 증거가 아니다."""
    def test_l1_legacy_counts_only_current_candidates_and_never_hides_database_failure(self) -> None:
        from daon_user_api.recovery import BackupSelectionInput, RecoveryError
        ctx = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        payload = {"object_id": "object-a", "content_digest_sha256": "a" * 64, "byte_size": 7}
        digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        for mode in ("private", "unbound", "notebook-deleted", "source-deleted", "both-valid", "database-failure", "canonical-mismatch"):
            class Reader:
                def execute(self, query, params=()):
                    values = []
                    if query.startswith("SELECT b.notebook_id"):
                        values = [("notebook-a", "version-a", "object-a"), ("notebook-b", "version-b", "object-a")]
                    elif query.startswith("SELECT sv.record_id"):
                        if params[2] == "notebook-b" and mode == "database-failure":
                            raise psycopg.OperationalError("synthetic DB failure, no connection data")
                        if params[2] == "notebook-a" or mode in ("private", "both-valid", "canonical-mismatch"):
                            suffix = "a" if params[2] == "notebook-a" else "b"
                            actual_digest = "f" * 64 if suffix == "b" and mode == "canonical-mismatch" else digest
                            values = [("version-" + suffix, "source-" + suffix, "object-a", payload, actual_digest, "a" * 64, 7)]
                    elif "FROM alembic_version" in query:
                        values = [("test-revision",)]
                    return SimpleNamespace(fetchone=lambda: values[0] if values else None, fetchall=lambda: list(values))
            class Store:
                def readiness(self): return SimpleNamespace(ready=True, schema_revision="test-revision")
                @contextmanager
                def _transaction(self, _context): yield Reader()
            resolver = recovery_postgres.PostgresBackupSelectionResolver(Store())
            with self.subTest(mode=mode), patch.object(recovery_postgres, "_current_auth_grant"), \
                 patch.object(recovery_postgres, "_source_access_allowed", side_effect=lambda connection, context, version: not (mode == "private" and version == "version-b")), \
                 patch.object(recovery_postgres, "_special_source_access_allowed", return_value=True):
                expected = resolver.resolve(ctx, BackupSelectionInput("notebook-a", ("version-a",)))
                arguments = dict(schema_revision=expected.schema_revision, retention_watermark=expected.retention_watermark, objects=expected.objects)
                if mode in ("both-valid", "database-failure", "canonical-mismatch"):
                    with self.assertRaises(RecoveryError) as caught: resolver.resolve_legacy(ctx, **arguments)
                    self.assertEqual(caught.exception.status, {"both-valid": 404, "database-failure": 503, "canonical-mismatch": 409}[mode])
                else:
                    actual = resolver.resolve_legacy(ctx, **arguments)
                    self.assertEqual(actual.selection.notebook_id, "notebook-a")
                    self.assertEqual(actual.fingerprint, expected.fingerprint)

    def test_l1_resolver_canonical_metadata_acl_recheck_and_capture_zero(self) -> None:
        resolver_type = getattr(recovery_postgres, "PostgresBackupSelectionResolver", None)
        self.assertTrue(callable(resolver_type), "실제 SELECT 기반 metadata resolver 필요")
        from daon_user_api.recovery import BackupSelectionInput, RecoveryError
        ctx = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True,
                              session_id="session-a", device_id="device-a")
        payload = {"object_id": "object-a", "content_digest_sha256": "a" * 64, "byte_size": 7}
        canonical_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        row = ("version-a", "source-a", "object-a", payload, canonical_digest, "a" * 64, 7)
        class Reader:
            def __init__(self): self.rows = [row]; self.log = []; self.retention = []; self.replay = None
            def execute(self, query, params=()):
                self.log.append((query, params))
                if query.startswith("SELECT sv.record_id"): values = self.rows
                elif "FROM alembic_version" in query: values = [("test-revision",)]
                elif "FROM deletion_requests" in query: values = self.retention
                elif "FROM legal_holds" in query: values = []
                elif "FROM backup_records" in query: values = [] if self.replay is None else [(self.replay,)]
                else: values = []
                return SimpleNamespace(fetchone=lambda: values[0] if values else None, fetchall=lambda: list(values))
        reader = Reader()
        class Store:
            def readiness(self): return SimpleNamespace(ready=True, schema_revision="test-revision")
            @contextmanager
            def _transaction(self, _context): yield reader
        resolver = resolver_type(Store())
        selection = BackupSelectionInput("notebook-a", ("version-a",))
        with patch.object(recovery_postgres, "_current_auth_grant"), \
             patch.object(recovery_postgres, "_source_access_allowed", return_value=True) as acl, \
             patch.object(recovery_postgres, "_special_source_access_allowed", return_value=True):
            result = resolver.resolve(ctx, selection, idempotency_key="request-a")
            self.assertEqual((result.objects[0].object_id, result.objects[0].byte_size, result.schema_revision), ("object-a", 7, "test-revision"))
            self.assertGreaterEqual(acl.call_count, 2)
            reader.replay = "f" * 64
            with self.assertRaises(RecoveryError) as caught: resolver.resolve(ctx, selection, idempotency_key="request-a")
            self.assertEqual(caught.exception.status, 409)
            reader.replay = None
            acl.side_effect = [True, False]
            with self.assertRaises(RecoveryError) as caught: resolver.resolve(ctx, selection)
            self.assertEqual(caught.exception.status, 404)
            acl.side_effect = None
            for changed in ([], [(*row[:4], "f" * 64, *row[5:])], [(*row[:5], "b" * 64, 7)], [(*row[:6], 8)]):
                reader.rows = changed
                with self.assertRaises(RecoveryError): resolver.resolve(ctx, selection)
            queries = [q for q, _ in reader.log]
            self.assertFalse(any(q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
            metadata_sql = next(q for q in queries if q.startswith("SELECT sv.record_id"))
            for expected in ("notebook_bindings", "notebook_source_unbindings", "source_active=false", "o.status='completed'"):
                self.assertIn(expected, metadata_sql)
            self.assertNotIn("index_versions", metadata_sql)
            self.assertNotIn("s.state='ready'", metadata_sql)


class _SelectionPrivateDsn(str):
    def __repr__(self): return "<private S44 isolated PG DSN>"


@unittest.skipUnless(os.environ.get("DAON_RECOVERY_SELECTION_INTEGRATION_DSN") or os.environ.get("DAON_RECOVERY_SELECTION_INTEGRATION_DSN_FILE"),
                     "NOT RUN: dedicated S44 metadata PostgreSQL DSN required (MinIO not required)")
class RecoverySelectionPostgresIntegrationTests(unittest.TestCase):
    """전용 PG의 실제 RLS/metadata 조회. Object bytes/capture/전체 복구 성공이 아니다.

    fixture는 append-only 표이므로 guard를 끄거나 DELETE하지 않는다.
    연결은 매 시험 닫고 전용 DB 전체 정리는 DB를 만든 main이 수행한다.
    """
    def _connect(self):
        try:
            connection = psycopg.connect(self.dsn, connect_timeout=3)
            name = connection.execute("SELECT current_database()").fetchone()[0]
            if name != "daon_user_s44_l1_qa_20261018":
                connection.close()
                raise AssertionError("S44_DEDICATED_DATABASE_REQUIRED")
            return connection
        except psycopg.Error:
            raise AssertionError("S44_ISOLATED_DATABASE_CONNECTION_FAILED") from None

    def setUp(self):
        from daon_user_api.recovery import BackupSelectionInput
        self.dsn = _SelectionPrivateDsn(_environment_or_file("DAON_RECOVERY_SELECTION_INTEGRATION_DSN"))
        self.suffix = secrets.token_hex(8)
        self.context = RecoveryContext("s44-l1-tenant-" + self.suffix, "s44-l1-workspace-" + self.suffix,
            "s44-l1-actor-" + self.suffix, "s44-l1-trace-" + self.suffix, "runtime-policy-v1", True,
            session_id="s44-session-" + self.suffix, device_id="s44-device-" + self.suffix)
        self.notebook = "s44-l1-notebook-" + self.suffix
        ctx = self.context
        with self._connect() as connection:
            # 이 fixture DML 전에 정확 DB명을 이미 확인했다. 기존 schema/roles/grants 변경은 없다.
            connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES(%s,'S44 isolated metadata')", (ctx.tenant_id,))
            connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S44 isolated metadata')", (ctx.tenant_id, ctx.workspace_id))
            connection.execute("INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s44-isolated',%s,'active')", (ctx.actor_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_tenants(tenant_id) VALUES(%s)", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'organization_admin')", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())", (ctx.workspace_id, ctx.tenant_id))
            connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_tenant_policies VALUES(%s,1,now())", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_auth_workspace_policies VALUES(%s,%s,1,now())", (ctx.tenant_id, ctx.workspace_id))
            self._scope(connection)
            connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, ctx.actor_id))
            self.revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        self.store = PostgresCloudStore(self.dsn)
        self.resolver = recovery_postgres.PostgresBackupSelectionResolver(self.store)
        self.addCleanup(self.store.close)
        self.version, self.source, self.object_id = self._source("a")
        self.selection = BackupSelectionInput(self.notebook, (self.version,))

    def _scope(self, connection):
        for key, value in (("tenant_id", self.context.tenant_id), ("workspace_id", self.context.workspace_id),
                           ("actor_id", self.context.actor_id), ("notebook_id", self.notebook)):
            connection.execute("SELECT set_config(%s,%s,true)", ("app." + key, value))

    def _source(self, label, *, size=7, canonical_size=None, canonical_hash=None, origin=None, object_state="completed"):
        from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
        ctx = self.context
        source, version, obj = [f"s44-{kind}-{label}-{self.suffix}" for kind in ("source", "version", "object")]
        payload = {"filename": "S44 isolated metadata.txt", "object_id": obj,
                   "byte_size": size if canonical_size is None else canonical_size,
                   "content_digest_sha256": canonical_hash or "a" * 64}
        if origin is not None:
            payload.update(origin_kind=origin, text_sha256="a" * 64, raw_sha256="b" * 64,
                snapshot_id="s44-missing-snapshot-" + self.suffix, connector_id="mcp-open-law-go-kr")
        with self._connect() as connection:
            self._scope(connection)
            time_columns = ["created_at"]
            if connection.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='object_records' AND column_name='updated_at'").fetchone() is not None:
                time_columns.append("updated_at")
            connection.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,"
                + ",".join(time_columns) + ") VALUES(%s,%s,%s,'source',%s,%s,%s,%s,'text/plain',%s,%s,%s,%s,%s,"
                + ",".join("now()" for _ in time_columns) + ")",
                (ctx.tenant_id, ctx.workspace_id, obj, "s44-isolated-staging/" + obj, "s44-isolated-source/" + obj,
                 "a" * 64, size, object_state, ctx.actor_id, ctx.trace_id, obj, "f" * 64))
            # registered + index 없음도 현재 접근 가능한 원본이면 허용해야 한다.
            Canon._insert(connection, ctx, "sources", source, {"filename": "S44 isolated metadata.txt"}, state="registered")
            Canon._insert(connection, ctx, "source_versions", version, payload,
                extra_columns=("source_id", "object_id"), extra_values=(source, obj))
            connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                "VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (ctx.tenant_id, ctx.workspace_id, self.notebook, source, version, ctx.actor_id))
        return version, source, obj

    def _denied(self, context=None, selection=None):
        from daon_user_api.recovery import RecoveryError
        with self.assertRaises(RecoveryError) as caught:
            self.resolver.resolve(context or self.context, selection or self.selection)
        self.assertIn(caught.exception.status, (403, 404, 409))

    def _deny_source(self, version):
        ctx = self.context
        with self._connect() as connection:
            connection.execute("INSERT INTO identity_auth_source_access VALUES(%s,%s,%s,%s,0,1,now())",
                (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, version))

    def test_l1_real_pg_metadata_legacy_order_size_and_fingerprint(self):
        from daon_user_api.recovery import BackupSelectionInput, RecoveryError
        second, _, _ = self._source("b", size=512 * 1024 * 1024 - 7)
        selected = BackupSelectionInput(self.notebook, (second, self.version))
        result = self.resolver.resolve(self.context, selected)
        reordered = self.resolver.resolve(self.context, BackupSelectionInput(self.notebook, tuple(reversed(selected.source_version_ids))))
        self.assertEqual(result.fingerprint, reordered.fingerprint)
        self.assertEqual(result.schema_revision, self.revision)
        self.assertEqual(sum(item.byte_size for item in result.objects), 512 * 1024 * 1024)
        legacy = self.resolver.resolve_legacy(self.context, schema_revision=result.schema_revision,
            retention_watermark=result.retention_watermark, objects=tuple(reversed(result.objects)))
        self.assertEqual(legacy.fingerprint, result.fingerprint)
        with self.assertRaises(RecoveryError):
            self.resolver.resolve_legacy(self.context, schema_revision="forged", retention_watermark=result.retention_watermark, objects=result.objects)
        oversized, _, _ = self._source("oversized", size=512 * 1024 * 1024)
        with self.assertRaises(RecoveryError) as caught:
            self.resolver.resolve(self.context, BackupSelectionInput(self.notebook, (self.version, oversized)))
        self.assertEqual(caught.exception.status, 413)
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM backup_records WHERE tenant_id=%s AND workspace_id=%s",
                (self.context.tenant_id, self.context.workspace_id)).fetchone()[0], 0)

    def test_l1_real_pg_scope_acl_unbinding_and_inflight_revocation(self):
        from daon_user_api.recovery import BackupSelectionInput
        self._denied(context=replace(self.context, tenant_id="s44-other-tenant"))
        self._denied(context=replace(self.context, workspace_id="s44-other-workspace"))
        self._denied(selection=BackupSelectionInput("s44-other-notebook", (self.version,)))
        self._denied(selection=BackupSelectionInput(self.notebook, ("s44-missing-version",)))
        original = self.resolver._metadata
        changed = False
        def revoke_after_read(connection, context, selection):
            nonlocal changed
            value = original(connection, context, selection)
            if not changed:
                changed = True; self._deny_source(self.version)
            return value
        # 접근 판정/결과는 대역이 아니다. 실제 조회 사이에 격리 ACL 행만 INSERT한다.
        with patch.object(self.resolver, "_metadata", side_effect=revoke_after_read):
            self._denied()
        self._denied()
        version, source, _ = self._source("unbind")
        with self._connect() as connection:
            self._scope(connection)
            connection.execute("INSERT INTO notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id,binding_version,actor_id,occurred_at) "
                "VALUES(%s,%s,%s,%s,%s,2,%s,now())", (self.context.tenant_id, self.context.workspace_id,
                self.notebook, source, version, self.context.actor_id))
        self._denied(selection=BackupSelectionInput(self.notebook, (version,)))

    def test_l1_real_pg_legacy_counts_only_current_accessible_bindings(self):
        from daon_user_api.recovery import RecoveryError
        from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
        ctx = self.context

        def add_candidate(mode):
            notebook, source, version, request = [f"s44-{kind}-{mode}-{self.suffix}"
                for kind in ("notebook", "source", "version", "request")]
            with self._connect() as connection:
                self._scope(connection)
                connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                    (ctx.tenant_id, ctx.workspace_id, notebook, ctx.actor_id))
                Canon._insert(connection, ctx, "sources", source, {"filename": "S44 isolated candidate.txt"}, state="registered")
                Canon._insert(connection, ctx, "source_versions", version,
                    {"filename": "S44 isolated candidate.txt", "object_id": self.object_id,
                     "byte_size": 7, "content_digest_sha256": "a" * 64},
                    extra_columns=("source_id", "object_id"), extra_values=(source, self.object_id))
                connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                    "VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (ctx.tenant_id, ctx.workspace_id, notebook, source, version, ctx.actor_id))
                if mode == "private":
                    connection.execute("INSERT INTO identity_auth_source_access VALUES(%s,%s,%s,%s,0,1,now())",
                        (ctx.tenant_id, ctx.workspace_id, ctx.actor_id, version))
                elif mode == "unbound":
                    connection.execute("INSERT INTO notebook_source_unbindings(tenant_id,workspace_id,notebook_id,source_id,source_version_id,binding_version,actor_id,occurred_at) "
                        "VALUES(%s,%s,%s,%s,%s,2,%s,now())", (ctx.tenant_id, ctx.workspace_id, notebook, source, version, ctx.actor_id))
                elif mode == "notebook-deleted":
                    connection.execute("INSERT INTO notebook_deletion_requests(request_id,tenant_id,workspace_id,notebook_id,actor_id,title_fingerprint,state,current_step,requested_at,idempotency_key,expected_version,request_fingerprint) "
                        "VALUES(%s,%s,%s,%s,%s,%s,'accepted','requested',now(),%s,1,%s)",
                        (request, ctx.tenant_id, ctx.workspace_id, notebook, ctx.actor_id, "a" * 64, request, "f" * 64))
                elif mode == "source-deleted":
                    connection.execute("INSERT INTO deletion_request_locator(tenant_id,request_id,workspace_id,source_id,created_at) VALUES(%s,%s,%s,%s,now())",
                        (ctx.tenant_id, request, ctx.workspace_id, source))
                    connection.execute("INSERT INTO deletion_requests(tenant_id,workspace_id,request_id,source_id,actor_id,state,version,source_active,purge_started,grace_until,policy_version,idempotency_key,request_fingerprint,trace_id,created_at,updated_at) "
                        "VALUES(%s,%s,%s,%s,%s,'purged',1,false,true,now(),%s,%s,%s,%s,now(),now())",
                        (ctx.tenant_id, ctx.workspace_id, request, source, ctx.actor_id, ctx.policy_version, request, "f" * 64, ctx.trace_id))

        # 모든 후보는 실제 동일 Object에 결속하지만 B 네 개는 현재 접근/결속 검증에서 제외된다.
        for mode in ("private", "unbound", "notebook-deleted", "source-deleted"):
            add_candidate(mode)
        expected = self.resolver.resolve(ctx, self.selection)
        arguments = dict(schema_revision=expected.schema_revision, retention_watermark=expected.retention_watermark,
                         objects=expected.objects, idempotency_key="s44-legacy-" + self.suffix)
        actual = self.resolver.resolve_legacy(ctx, **arguments)
        self.assertEqual(actual.selection.notebook_id, self.notebook)
        self.assertEqual(actual.fingerprint, expected.fingerprint)
        add_candidate("valid")
        with self.assertRaises(RecoveryError) as caught:
            self.resolver.resolve_legacy(ctx, **arguments)
        self.assertEqual((caught.exception.code, caught.exception.status), ("BACKUP_UNAVAILABLE", 404))
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM backup_records WHERE tenant_id=%s AND workspace_id=%s",
                (ctx.tenant_id, ctx.workspace_id)).fetchone()[0], 0)

    def test_l1_real_pg_object_canonical_mismatch_and_special_source_acl(self):
        from daon_user_api.recovery import BackupSelectionInput
        for label, options in (("hash", {"canonical_hash": "b" * 64}), ("size", {"canonical_size": 8}),
                               ("pending", {"object_state": "pending"}), ("web", {"origin": "website"}),
                               ("law", {"origin": "mcp_open_law"})):
            version, _, _ = self._source(label, **options)
            self._denied(selection=BackupSelectionInput(self.notebook, (version,)))



@unittest.skipUnless(CONFIGURED, "R1-M5-07 PostgreSQL/MinIO integration environment required")
class PostgresRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DSN and ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY and MANIFEST_KEY
        suffix = secrets.token_hex(6)
        self.tenant_id = f"fixture-tenant-{suffix}"
        self.workspace_id = f"fixture-workspace-{suffix}"
        self.actor_id = f"actor-{suffix}"
        self.object_id = secrets.token_hex(16)
        self.content = f"R1-M5-07-C02:{suffix}".encode()
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.context = RecoveryContext(
            self.tenant_id,
            self.workspace_id,
            self.actor_id,
            f"trace-{suffix}",
            "policy-c02",
            organization_admin=True,
        )
        self.cloud = PostgresCloudStore(DSN)
        self.queue = PostgresObjectQueueStore(DSN)
        self.object_storage = MinioObjectStorageAdapter(
            endpoint=ENDPOINT,
            bucket=BUCKET,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        scope = CloudAccessContext(
            self.tenant_id, self.workspace_id, self.actor_id, "object.write"
        )
        self.cloud.seed_scope(scope)
        coordinator = ObjectQueueCoordinator(
            self.queue, self.object_storage, id_factory=lambda: self.object_id
        )
        coordinator.submit(
            scope,
            area="source",
            content=self.content,
            content_type="application/octet-stream",
            idempotency_key=f"seed-{suffix}",
            trace_id=f"trace-seed-{suffix}",
        )
        worker = DurableObjectWorker(self.queue, self.object_storage, f"worker-{suffix}")
        completed = worker.run_once(scope, now=datetime.now(timezone.utc))
        self.assertIsNotNone(completed)
        self.assertEqual(completed.state, "completed")
        self.storage = MinioRecoveryStorageAdapter(self.object_storage)

    def tearDown(self) -> None:
        self.queue.close()
        self.cloud.close()

    def _service(self) -> PostgresRecoveryService:
        assert DSN and MANIFEST_KEY
        return PostgresRecoveryService(
            DSN,
            self.storage,
            manifest_key=MANIFEST_KEY,
            clock=lambda: datetime.now(timezone.utc),
        )

    def test_backup_and_restore_survive_service_restart_and_touch_real_stores(self) -> None:
        first = self._service()
        backup = first.create_backup(
            self.context,
            trigger="manual",
            schema_revision="0006",
            retention_watermark="watermark-c02",
            objects=(BackupObjectInput(self.object_id, self.digest, len(self.content)),),
            idempotency_key=f"backup-{self.object_id}",
        )
        self.assertEqual(backup.state, "ready")
        first.close()

        restarted = self._service()
        persisted = restarted.get_backup(self.context, backup.backup_id)
        self.assertEqual(persisted.manifest_digest, backup.manifest_digest)
        preview = restarted.create_restore_preview(
            self.context,
            backup.backup_id,
            destination=RestoreDestination(
                f"fixture-target-tenant-{self.object_id[:8]}",
                f"fixture-target-workspace-{self.object_id[:8]}",
                f"fixture-target-database-{self.object_id[:8]}",
                f"fixture-target-bucket-{self.object_id[:8]}",
            ),
            idempotency_key=f"preview-{self.object_id}",
            step_up_verified=True,
        )
        restored = restarted.execute_restore(
            self.context,
            preview.request_id,
            expected_version=preview.version,
            preview_version=preview.preview.version,
            idempotency_key=f"execute-{self.object_id}",
            step_up_verified=True,
        )
        self.assertEqual(restored.state, "completed")
        self.assertEqual(
            self.storage.read_fixture(restored.preview.destination, self.object_id),
            self.content,
        )
        with psycopg.connect(DSN) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (restored.preview.destination.tenant_id,),
            )
            connection.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (restored.preview.destination.workspace_id,),
            )
            target = connection.execute(
                "SELECT digest_sha256,byte_size,status FROM object_records WHERE object_id=%s",
                (self.object_id,),
            ).fetchone()
        self.assertEqual(target, (self.digest, len(self.content), "completed"))

        wrong_workspace = RecoveryContext(
            self.tenant_id,
            f"wrong-{self.workspace_id}",
            self.actor_id,
            self.context.trace_id,
            self.context.policy_version,
            organization_admin=True,
        )
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.get_backup(wrong_workspace, backup.backup_id)
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.locate_backup_workspace(
                f"wrong-{self.tenant_id}", backup.backup_id
            )
        restarted.close()

    def test_missing_and_corrupt_source_objects_fail_closed(self) -> None:
        service = self._service()
        try:
            with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                service.create_backup(
                    self.context,
                    trigger="manual",
                    schema_revision="0006",
                    retention_watermark="watermark-c02",
                    objects=(
                        BackupObjectInput(
                            secrets.token_hex(16), self.digest, len(self.content)
                        ),
                    ),
                    idempotency_key=f"missing-{self.object_id}",
                )
            with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                service.create_backup(
                    self.context,
                    trigger="manual",
                    schema_revision="0006",
                    retention_watermark="watermark-c02",
                    objects=(
                        BackupObjectInput(
                            self.object_id, "0" * 64, len(self.content)
                        ),
                    ),
                    idempotency_key=f"corrupt-{self.object_id}",
                )
        finally:
            service.close()


class RecoveryRuntimeFailCloseTests(unittest.TestCase):
    def test_build_dependencies_never_installs_reference_recovery_fallback(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            dependencies = build_dependencies(RuntimeSettings.for_test(
                database_path=Path(directory) / "runtime.sqlite3",
                policy_version="policy-c02",
            ))
            try:
                self.assertIsNotNone(dependencies.recovery_service)
                self.assertNotEqual(
                    type(dependencies.recovery_service).__name__, "RecoveryService"
                )
                with self.assertRaisesRegex(Exception, "RESOURCE_UNAVAILABLE"):
                    dependencies.recovery_service.create_backup(  # type: ignore[union-attr]
                        RecoveryContext(
                            "fixture-tenant", "fixture-workspace", "fixture-actor",
                            "fixture-trace", "policy-c02", organization_admin=True,
                        ),
                        trigger="manual", schema_revision="0006",
                        retention_watermark="fixture-watermark",
                        objects=(BackupObjectInput("a" * 32, "b" * 64, 1),),
                        idempotency_key="fixture-idempotency",
                    )
            finally:
                dependencies.close()


if __name__ == "__main__":
    unittest.main()
