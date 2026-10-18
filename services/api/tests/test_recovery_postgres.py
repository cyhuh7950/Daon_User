from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import unittest
import copy
import tempfile
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
    ObjectKeyPolicy,
    PostgresObjectQueueStore,
)
from daon_user_api.recovery import BackupObjectInput, BackupSelectionInput, RecoveryContext, RecoveryError, RestoreDestination
from daon_user_api.recovery_capture import FileCaptureStore, decode_manifest
from daon_user_api.runtime import RuntimeSettings, build_dependencies
from daon_user_api.recovery_postgres import (
    MinioRecoveryStorageAdapter,
    PostgresRecoveryService,
)


class RecoveryLineageConsumerTests(unittest.TestCase):
    """SQL commit 포트만 대역이다. v3 파일/AEAD/source bytes는 실제로 실행한다."""
    def setUp(self):
        from test_recovery_lineage import Ledger, recanonical
        from daon_user_api.recovery_lineage_capture import FileLineageCaptureStore
        parent = Path(os.environ.get("TMP", ""))
        self.assertIn(parent, {Path("D:/tmp/daon-user-s44-d1-eoul2-20261019"), Path("D:/tmp/daon-user-s44-d1-main-20261019")})
        self.tmp = tempfile.TemporaryDirectory(prefix="r1c-", dir=parent); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); archive = self.root / "archive"; archive.mkdir()
        self.ledger = Ledger(); self.context = self.ledger.context
        self.content = {"object-a": b"prior-source" * 20, "object-b": b"root-source" * 40}
        for row in self.ledger.tables["object_records"]:
            raw = self.content[row["object_id"]]; row.update(byte_size=len(raw), digest_sha256=hashlib.sha256(raw).hexdigest())
            (self.root / row["object_id"]).write_bytes(raw)
        for row in self.ledger.tables["source_versions"]:
            obj = next(x for x in self.ledger.tables["object_records"] if x["object_id"] == row["object_id"])
            recanonical(row, dict(object_id=obj["object_id"], byte_size=obj["byte_size"], content_digest_sha256=obj["digest_sha256"]))
        self.selection = self.ledger.resolve(); self.key = secrets.token_bytes(32)
        service = self.service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(self.key).digest(); service._manifest_master_key = self.key
        service._capture_store = None; service._notification_inbox_enabled = False
        service._lineage_capture_store = FileLineageCaptureStore(archive, master_key=self.key, key_version="v3-test", key_reference="memory-only")
        service._clock = lambda: datetime.now(timezone.utc)
        self.opened, self.sql, self.committed, self.mode = [], [], {}, None
        def source(context, item):
            self.assertEqual(context, self.context); self.opened.append(item.object_id)
            return (self.root / item.object_id).open("rb")
        service._storage = SimpleNamespace(open_source=source)
        @contextmanager
        def transaction(tenant, workspace, actor):
            self.assertEqual((tenant, workspace, actor), (self.context.tenant_id,self.context.workspace_id,self.context.actor_id))
            working = copy.deepcopy(self.committed)
            class Connection:
                def execute(inner, sql, params=()):
                    self.sql.append((sql, params)); row = None; rows = []
                    if sql.startswith("SELECT backup_id, request_fingerprint"):
                        row = next((r for r in working.values() if r.get("actor_id") == params[0] and r.get("idempotency_key") == params[1]), None)
                    elif sql.startswith("INSERT INTO backup_records"):
                        working[params[2]] = dict(tenant_id=params[0],workspace_id=params[1],backup_id=params[2],actor_id=params[3],trigger_type=params[4],
                            schema_revision=params[5],retention_watermark=params[6],policy_version=params[7],trace_id=params[8],idempotency_key=params[10],request_fingerprint=params[11],
                            created_at=params[12],verified_at=None,state="queued",version=1)
                    elif sql.startswith("INSERT INTO backup_manifests"):
                        working[params[2]].update(manifest_version=params[3],manifest_digest=params[4],encrypted_inventory=params[5],object_count=params[6])
                    elif sql.startswith("UPDATE backup_records"):
                        if "state='ready'" in sql:
                            working[params[1]].update(state="ready",verified_at=params[0]); working[params[1]]["version"]+=1
                        else: working[params[1]]["state"]=params[0]; working[params[1]]["version"]+=1
                    elif sql.startswith("SELECT b.*"):
                        rows = list(working.values()) if not params else [r for r in working.values() if r["backup_id"] == params[0]]
                        row = rows[0] if rows else None
                    elif sql.startswith("SELECT metadata->>"): row = None
                    return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: rows)
            yield Connection()
            if self.mode == "rollback": raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
            self.committed.clear(); self.committed.update(working)
            if self.mode == "response-lost": raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        service._transaction = transaction

    def create(self, **kwargs):
        return self.service.create_lineage_backup(self.context, self.selection, idempotency_key="lineage-request",
            recheck=self.ledger.resolve, **kwargs)

    def test_r1c_v3_capture_manifest_list_get_restart_replay_and_kind_conflict(self):
        method = getattr(self.service, "create_lineage_backup", None)
        self.assertTrue(callable(method), "실제 v3 보존/원장 consumer 필요")
        view = self.create()
        self.assertEqual((view.state,view.object_count), ("ready",2))
        row = self.committed[view.backup_id]
        self.assertEqual(row["manifest_version"],3); self.assertTrue(bytes(row["encrypted_inventory"]).startswith(b"DRM3"))
        self.assertEqual(self.service.get_backup(self.context,view.backup_id),view)
        self.assertEqual(self.service.list_backups(self.context),(view,))
        for identity in self.content: (self.root/identity).unlink()
        self.assertEqual(self.create(),view)
        self.assertEqual(self.opened,["object-a","object-b"])
        inserts=[sql for sql,_ in self.sql if sql.startswith("INSERT INTO backup_records")]
        self.assertEqual(len(inserts),1)
        with self.assertRaises(RecoveryError): self.service._capture_from_row({**row,"manifest_version":2})
        self.ledger.tables["notebook_metadata_versions"][0]["title"]="current metadata changed"
        self.selection=self.ledger.resolve()
        with self.assertRaises(RecoveryError) as caught: self.create()
        self.assertEqual(caught.exception.status,409)
        with self.assertRaises(RecoveryError) as caught:
            with self.service._transaction(self.context.tenant_id,self.context.workspace_id,self.context.actor_id) as conn:
                self.service._require_independent_capture(conn,self.context,view.backup_id,preview=True)
        self.assertEqual(caught.exception.status,503)

    def test_r1c_final_revocation_and_commit_uncertainty_preserve_exact_artifact(self):
        self.assertTrue(callable(getattr(self.service,"create_lineage_backup",None)))
        store=self.service._lineage_capture_store; original=store.verify; revoked=[]
        def verify(*args,**kwargs):
            value=original(*args,**kwargs); revoked.append(True); return value
        def recheck():
            if revoked: raise RecoveryError("CURRENT_ACCESS_DENIED",403)
            return self.ledger.resolve()
        with patch.object(store,"verify",side_effect=verify), self.assertRaises(RecoveryError) as caught:
            self.service.create_lineage_backup(self.context,self.selection,idempotency_key="revoked-request",recheck=recheck)
        self.assertEqual(caught.exception.status,403); self.assertEqual(self.committed,{})
        folder=next(p for p in store.root.iterdir() if p.is_dir())
        self.assertEqual({p.name for p in folder.iterdir()},{"owner.bin"})
        self.mode="response-lost"
        with self.assertRaises(RecoveryError) as caught: self.create()
        self.assertEqual(caught.exception.status,503); self.assertEqual(len(self.committed),1)
        opened=list(self.opened); self.mode=None
        from daon_user_api.recovery_lineage_capture import FileLineageCaptureStore
        self.service._lineage_capture_store=FileLineageCaptureStore(store.root,master_key=self.key,key_version="v3-test",key_reference="memory-only")
        self.assertEqual(self.create().state,"ready"); self.assertEqual(self.opened,opened)


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
    def test_r1c_lineage_adapter_consumes_real_resolver_and_current_acl_workflow(self):
        from test_recovery_lineage import Ledger
        ledger=Ledger(); controls=[]
        class Read:
            info=ledger.info
            def execute(inner,sql,params=()):
                if sql=="SET LOCAL ROLE daon_app":
                    controls.append(sql); return SimpleNamespace(fetchone=lambda:None)
                return ledger.execute(sql,params)
        @contextmanager
        def transaction(context):
            self.assertEqual((context.tenant_id,context.workspace_id,context.actor_id,context.capability),
                (ledger.context.tenant_id,ledger.context.workspace_id,ledger.context.actor_id,"recovery"))
            yield Read()
        resolver=recovery_postgres.PostgresBackupSelectionResolver(SimpleNamespace(readiness=lambda:SimpleNamespace(ready=True,schema_revision="0069"),_transaction=transaction))
        resolved=resolver.resolve_lineage(ledger.context,ledger.selection)
        self.assertEqual(resolved.source_version_count,2)
        self.assertEqual(controls,["SET LOCAL ROLE daon_app"])
        ledger.revoked.add("prior-version")
        with self.assertRaises(RecoveryError) as caught: resolver.resolve_lineage(ledger.context,ledger.selection)
        self.assertEqual(caught.exception.status,404)
        ledger.revoked.clear(); ledger.auth.auth_role="organization_admin"; ledger.workflow_state="suspended"
        with self.assertRaises(RecoveryError) as caught: resolver.resolve_lineage(ledger.context,ledger.selection)
        self.assertEqual(caught.exception.status,403)
        self.assertFalse(any(sql.startswith(("INSERT","UPDATE","DELETE")) for sql,_ in ledger.log))

    """실제 resolver의 SQL 읽기 포트만 대역. PG/RLS·capture 성공 증거가 아니다."""
    def test_l2_inventory_only_durable_create_refuses_before_storage_or_database(self):
        from daon_user_api.recovery import RecoveryError
        service = object.__new__(PostgresRecoveryService)
        service._storage = SimpleNamespace(verify_source=lambda *args: self.fail("inventory-only source read forbidden"))
        service._transaction = lambda *args: self.fail("inventory-only ledger write forbidden")
        with self.assertRaises(RecoveryError) as caught:
            service.create_backup(RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True),
                trigger="manual", schema_revision="schema-a", retention_watermark="watermark-a",
                objects=(BackupObjectInput("object-a", "a" * 64, 7),), idempotency_key="legacy-create")
        self.assertEqual((caught.exception.status, caught.exception.retryable), (503, False))

    def test_l2_stream_port_uses_scoped_key_and_always_releases_response(self):
        import io
        response = io.BytesIO(b"synthetic source")
        released = []
        response.release_conn = lambda: released.append(True)
        keys = []
        class Client:
            def get_object(self, bucket, key):
                self_bucket = bucket
                keys.append((self_bucket, key))
                return response
        adapter = MinioRecoveryStorageAdapter(SimpleNamespace(_client=Client(), _bucket="existing-bucket"))
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        with adapter.open_source(context, BackupObjectInput("a" * 32, "a" * 64, 16)) as stream:
            self.assertEqual(stream.read(4), b"synt")
        self.assertTrue(response.closed)
        self.assertEqual(released, [True])
        self.assertEqual(keys, [("existing-bucket", "tenant-a/workspace-a/source/" + "a" * 32)])
        class BrokenClose:
            def close(self): raise RuntimeError("synthetic close failure")
            def release_conn(self): released.append(True)
        adapter._storage._client.get_object = lambda *args: BrokenClose()
        from daon_user_api.recovery import RecoveryError
        with self.assertRaises(RecoveryError):
            with adapter.open_source(context, BackupObjectInput("a" * 32, "a" * 64, 16)): pass
        self.assertEqual(released, [True, True])

    def test_l2_v1_get_decode_preserved_but_old_execute_and_preview_replay_cannot_bypass(self):
        service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(secrets.token_bytes(32)).digest()
        service._capture_store = None
        item = BackupObjectInput("object-a", "a" * 64, 7)
        old = service._encode_objects((item,))
        self.assertEqual(service._decode_objects(old), (item,))
        queries = []
        class Connection:
            def execute(self, sql, params=()):
                queries.append(sql)
                return SimpleNamespace(fetchone=lambda: {"manifest_version": 1, "encrypted_inventory": old})
        @contextmanager
        def transaction(*args): yield Connection()
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        service._transaction = transaction
        request = SimpleNamespace(backup_id="backup-old")
        with patch.object(service, "_select_restore", return_value=request), \
             patch.object(service, "_operation_replay", return_value="restore-old") as replay:
            from daon_user_api.recovery import RecoveryError
            with self.assertRaises(RecoveryError) as caught:
                service.execute_restore(context, "restore-old", expected_version=2, preview_version=1,
                    idempotency_key="old-successful-key", step_up_verified=True)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(replay.call_count, 0)
        queries.clear()
        with patch.object(service, "_select_restore", return_value=request):
            with self.assertRaises(RecoveryError) as caught:
                service.create_restore_preview(context, "backup-old", destination=RestoreDestination(
                    "fixture-tenant-b", "fixture-workspace-b", "fixture-database-b", "fixture-bucket-b"),
                    idempotency_key="old-preview-key", step_up_verified=True)
            self.assertEqual(caught.exception.status, 409)
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in queries))

    def test_l2_v2_inventory_decode_and_missing_materializer_refuse_execute(self):
        from daon_user_api.recovery_capture import CaptureScope, encode_manifest
        from daon_user_api.recovery import RecoveryError
        key = secrets.token_bytes(32)
        service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(key).digest()
        token = service._capture_identity("tenant-a", "workspace-a", "actor-a", "synthetic-key")
        scope = CaptureScope("capture-" + token, "tenant-a", "workspace-a", "notebook-a", ("version-a",),
            "a" * 64, "schema-a", "policy-a", "watermark-a")
        receipt = {**scope.binding(), "key_version": "key-v1", "key_reference": "existing-manifest-key",
            "objects": [{"object_id": "object-a", "plaintext_digest": "a" * 64, "byte_size": 7}]}
        envelope = encode_manifest(scope, receipt, master_key=key, key_version="key-v1", key_reference="existing-manifest-key")
        service._manifest_master_key = key
        service._capture_store = SimpleNamespace(key_version="key-v1", key_reference="existing-manifest-key", verify=lambda *args, **kwargs: None)
        self.assertEqual(service._decode_objects(envelope), (BackupObjectInput("object-a", "a" * 64, 7),))
        now = datetime.now(timezone.utc)
        row = {"encrypted_inventory": envelope, "manifest_digest": hashlib.sha256(envelope).hexdigest(),
            "backup_id": "backup-" + token, "actor_id": "actor-a", "idempotency_key": "synthetic-key",
            "manifest_version": 2, "tenant_id": "tenant-a", "workspace_id": "workspace-a", "state": "ready",
            "version": 4, "trigger_type": "manual", "created_at": now, "verified_at": now, "schema_revision": "schema-a",
            "retention_watermark": "watermark-a", "policy_version": "policy-a", "request_fingerprint": "a" * 64, "object_count": 1}
        service._backup_from_row(row)
        for key, value in (("tenant_id", "other-tenant"), ("request_fingerprint", "f" * 64),
                           ("manifest_digest", "f" * 64), ("object_count", 2)):
            with self.assertRaises(RecoveryError): service._backup_from_row({**row, key: value})
        @contextmanager
        def transaction(*args):
            yield SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: row))
        service._transaction = transaction
        with patch.object(service, "_select_restore", return_value=SimpleNamespace(backup_id="backup-" + token)), \
             patch.object(service, "_operation_replay", return_value="restore-old") as replay:
            with self.assertRaises(RecoveryError) as caught:
                service.execute_restore(RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True),
                    "restore-v2", expected_version=2, preview_version=1, idempotency_key="v2-execute", step_up_verified=True)
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(replay.call_count, 0)

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
    """실제 PG/MinIO + 로컬 보호 파일. 원본은 합성 namespace뿐이다.

    DB는 main이 만든 정확 전용 DB에만 쓴다. append-only 원장은 main이 DB째 회수한다.
    L2-A bytes 복호화는 L3 materialization/restore.completed 증거가 아니다.
    """
    def _connect(self):
        try:
            connection = psycopg.connect(_SelectionPrivateDsn(DSN), connect_timeout=3)
            if connection.execute("SELECT current_database()").fetchone()[0] != "daon_user_s44_l2a_qa_20261018":
                connection.close()
                raise AssertionError("S44_L2A_DEDICATED_DATABASE_REQUIRED")
            return connection
        except psycopg.Error:
            raise AssertionError("S44_L2A_ISOLATED_DATABASE_CONNECTION_FAILED") from None

    def _scope(self, connection):
        for key, value in (("tenant_id", self.tenant_id), ("workspace_id", self.workspace_id),
                           ("actor_id", self.actor_id), ("notebook_id", self.notebook)):
            connection.execute("SELECT set_config(%s,%s,true)", ("app." + key, value))

    def setUp(self) -> None:
        assert DSN and ENDPOINT and BUCKET and ACCESS_KEY and SECRET_KEY and MANIFEST_KEY
        # 어떤 DML/Object 쓰기보다 먼저 독립 DB 경계를 검사한다.
        with self._connect():
            pass
        self.file_root = Path(os.environ.get("DAON_RECOVERY_CAPTURE_TEST_ROOT", "D:/tmp/daon-user-s44-l2a-eoul2-20261018"))
        if not self.file_root.is_absolute() or self.file_root.name not in {
                "daon-user-s44-l2a-eoul2-20261018", "daon-user-s44-l2a-main-20261018"}:
            raise AssertionError("S44_EXACT_TEST_ROOT_REQUIRED")
        if os.name == "nt" and self.file_root.parent != Path("D:/tmp"):
            raise AssertionError("S44_EXACT_TEST_ROOT_REQUIRED")
        if self.file_root.exists() or self.file_root.is_symlink():
            raise AssertionError("S44_TEST_ROOT_MUST_BE_NEW")
        for parent in (self.file_root.parent, *self.file_root.parent.parents):
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
                raise AssertionError("S44_TEST_ROOT_LINK_FORBIDDEN")
        self.file_root.mkdir()
        self.addCleanup(self._cleanup_files)
        self.archive = self.file_root / "archive"
        self.archive.mkdir()
        suffix = secrets.token_hex(6)
        self.suffix = suffix
        self.tenant_id = f"fixture-tenant-{suffix}"
        self.workspace_id = f"fixture-workspace-{suffix}"
        self.actor_id = f"actor-{suffix}"
        self.notebook = f"notebook-{suffix}"
        self.version_id = f"version-{suffix}"
        self.source_id = f"source-{suffix}"
        self.object_id = secrets.token_hex(16)
        self.content = f"R1-M5-07-C02:{suffix}".encode()
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.context = RecoveryContext(
            self.tenant_id,
            self.workspace_id,
            self.actor_id,
            f"trace-{suffix}",
            "runtime-policy-v1",
            organization_admin=True,
            session_id=f"session-{suffix}", device_id=f"device-{suffix}",
        )
        self.cloud = PostgresCloudStore(DSN)
        self.addCleanup(self.cloud.close)
        self.queue = PostgresObjectQueueStore(DSN)
        self.addCleanup(self.queue.close)
        self.object_storage = MinioObjectStorageAdapter(
            endpoint=ENDPOINT,
            bucket=BUCKET,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        self.object_keys = set()
        self.addCleanup(self._cleanup_objects)
        scope = CloudAccessContext(
            self.tenant_id, self.workspace_id, self.actor_id, "object.write"
        )
        self.cloud.seed_scope(scope)
        self.object_keys.update((ObjectKeyPolicy().final_key(scope, "source", self.object_id),
                                 ObjectKeyPolicy().staging_key(scope, "source", self.object_id)))
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
        self.opens, self.bytes_read = [], [0]
        test = self
        class ObservedMinioStorage(MinioRecoveryStorageAdapter):
            @contextmanager
            def open_source(self, context, item):
                test.opens.append(item.object_id)
                with super().open_source(context, item) as response:
                    class ReadProbe:
                        def read(self, count):
                            value = response.read(count)
                            test.bytes_read[0] += len(value)
                            return value
                    yield ReadProbe()
            def verify_source(self, *args):
                raise AssertionError("V2_MUST_STREAM_NOT_WHOLE_SOURCE_GET")
        self.storage = ObservedMinioStorage(self.object_storage)
        self.capture_store = FileCaptureStore(self.archive, master_key=MANIFEST_KEY,
            key_version="integration-v2", key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
        from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
        with self._connect() as connection:
            ctx = self.context
            connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s44-l2a',%s,'active')", (ctx.actor_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_tenants(tenant_id) VALUES(%s)", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'organization_admin')", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())", (ctx.workspace_id, ctx.tenant_id))
            connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())", (ctx.tenant_id, ctx.actor_id))
            connection.execute("INSERT INTO identity_auth_tenant_policies VALUES(%s,1,now())", (ctx.tenant_id,))
            connection.execute("INSERT INTO identity_auth_workspace_policies VALUES(%s,%s,1,now())", (ctx.tenant_id, ctx.workspace_id))
            self._scope(connection)
            connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, ctx.actor_id))
            connection.execute("INSERT INTO notebook_metadata_versions(tenant_id,workspace_id,notebook_id,version,title,is_current,updated_by,updated_at) VALUES(%s,%s,%s,1,'S44 synthetic source',true,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, ctx.actor_id))
            Canon._insert(connection, ctx, "sources", self.source_id, {"filename": "S44-synthetic.txt"}, state="registered")
            Canon._insert(connection, ctx, "source_versions", self.version_id,
                {"object_id": self.object_id, "content_digest_sha256": self.digest, "byte_size": len(self.content)},
                extra_columns=("source_id", "object_id"), extra_values=(self.source_id, self.object_id))
            connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())",
                (ctx.tenant_id, ctx.workspace_id, self.notebook, self.source_id, self.version_id, ctx.actor_id))
        self.resolver = recovery_postgres.PostgresBackupSelectionResolver(self.cloud)
        self.selection = BackupSelectionInput(self.notebook, (self.version_id,))

    def _cleanup_objects(self):
        # 이 시험이 제출한 정확 합성 key만 회수한다. bucket/DB 전체 삭제는 main 소유다.
        for key in sorted(self.object_keys):
            self.object_storage.delete(key)

    def _cleanup_files(self):
        if self.file_root.resolve() != self.file_root or self.file_root.is_symlink():
            raise AssertionError("S44_TEST_CLEANUP_BOUNDARY_INVALID")
        shutil.rmtree(self.file_root)
        self.assertFalse(self.file_root.exists())

    def _service(self) -> PostgresRecoveryService:
        assert DSN and MANIFEST_KEY
        service = PostgresRecoveryService(
            DSN,
            self.storage,
            manifest_key=MANIFEST_KEY,
            clock=lambda: datetime.now(timezone.utc),
            capture_store=self.capture_store,
        )
        self.addCleanup(service.close)
        return service

    def _create_selected(self, service, key):
        def current():
            resolved = self.resolver.resolve(self.context, self.selection, idempotency_key=key)
            return resolved, self.resolver.capture_metadata(self.context, resolved)
        resolved, metadata = current()
        return service.create_selected_backup(self.context, resolved, metadata,
            idempotency_key=key, recheck=current)

    def test_v2_capture_survives_source_removal_restart_but_materializer_is_unavailable(self) -> None:
        first = self._service()
        backup = self._create_selected(first, f"backup-{self.object_id}")
        self.assertEqual(backup.state, "ready")
        self.assertEqual(self.opens, [self.object_id])
        self.assertEqual(self.bytes_read[0], len(self.content))
        with self._connect() as connection:
            self._scope(connection)
            manifest = connection.execute("SELECT manifest_version,encrypted_inventory FROM backup_manifests WHERE backup_id=%s", (backup.backup_id,)).fetchone()
        self.assertEqual(manifest[0], 2)
        scope, receipt = decode_manifest(bytes(manifest[1]), master_key=MANIFEST_KEY,
            key_version="integration-v2", key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
        self.object_storage.delete(ObjectKeyPolicy().final_key(self.context, "source", self.object_id))
        first.close()
        self.capture_store = FileCaptureStore(self.archive, master_key=MANIFEST_KEY,
            key_version="integration-v2", key_reference="DAON_RECOVERY_MANIFEST_KEY_FILE")
        restarted = self._service()
        persisted = restarted.get_backup(self.context, backup.backup_id)
        self.assertEqual(persisted.manifest_digest, backup.manifest_digest)
        result = self.capture_store.restore(scope, self.file_root / "restored", expected_receipt=receipt)
        self.assertEqual(result[self.object_id].read_bytes(), self.content)
        self.assertEqual(self.capture_store.verify(scope).metadata["source_versions"][0]["record_id"], self.version_id)
        # 등록 target/materializer가 없는 L2-A에서 preview_ready/completed를 만들지 않는다.
        with self.assertRaises(RecoveryError) as caught:
            restarted.create_restore_preview(self.context, backup.backup_id, destination=RestoreDestination(
                f"fixture-target-tenant-{self.object_id[:8]}",
                f"fixture-target-workspace-{self.object_id[:8]}",
                f"fixture-target-database-{self.object_id[:8]}",
                f"fixture-target-bucket-{self.object_id[:8]}",
            ), idempotency_key=f"preview-{self.object_id}", step_up_verified=True)
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.opens, [self.object_id], "원본 부재 이후에는 보존본만 읽어야 한다")
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM restore_requests WHERE tenant_id=%s AND workspace_id=%s",
                (self.tenant_id, self.workspace_id)).fetchone()[0], 0)

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

    def test_real_minio_missing_and_corrupt_reads_never_create_ready(self) -> None:
        service = self._service()
        key = ObjectKeyPolicy().final_key(self.context, "source", self.object_id)
        self.object_storage.delete(key)
        with self.assertRaises(RecoveryError):
            self._create_selected(service, f"missing-{self.object_id}")
        self.assertEqual(self.opens, [self.object_id], "입구 503가 아니라 실제 MinIO open 시도여야 한다")
        self.assertEqual(self.bytes_read[0], 0)
        corrupt = b"x" * len(self.content)
        self.object_storage._client.put_object(BUCKET, key, io.BytesIO(corrupt), len(corrupt), content_type="application/octet-stream")
        with self.assertRaises(RecoveryError):
            self._create_selected(service, f"corrupt-{self.object_id}")
        self.assertEqual(self.opens, [self.object_id, self.object_id])
        self.assertEqual(self.bytes_read[0], len(corrupt), "손상 bytes를 실제 읽은 뒤 거부해야 한다")
        with self._connect() as connection:
            self._scope(connection)
            self.assertEqual(connection.execute("SELECT count(*) FROM backup_records WHERE tenant_id=%s AND workspace_id=%s",
                (self.tenant_id, self.workspace_id)).fetchone()[0], 0)
        self.assertEqual(list(self.archive.glob("*/receipt.bin")), [])

    def test_historical_v1_get_is_preserved_but_independent_preview_is_409(self):
        service = self._service()
        backup_id = "historical-v1-" + self.suffix
        envelope = service._encode_objects((BackupObjectInput(self.object_id, self.digest, len(self.content)),))
        # 과거 원장을 명시 seed한다. create_backup/v2 capture 성공을 가장하지 않는다.
        with service._transaction(self.tenant_id, self.workspace_id, self.actor_id) as connection:
            now = datetime.now(timezone.utc)
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]
            audit = service._append_audit(connection, self.context, "backup.requested", "backup", backup_id)
            connection.execute("INSERT INTO backup_record_locator(tenant_id,backup_id,workspace_id,created_at) VALUES(%s,%s,%s,%s)",
                (self.tenant_id, backup_id, self.workspace_id, now))
            connection.execute("INSERT INTO backup_records(tenant_id,workspace_id,backup_id,actor_id,trigger_type,state,version,schema_revision,retention_watermark,policy_version,trace_id,audit_event_id,idempotency_key,request_fingerprint,created_at) VALUES(%s,%s,%s,%s,'manual','queued',1,%s,'historical-watermark',%s,%s,%s,%s,%s,%s)",
                (self.tenant_id, self.workspace_id, backup_id, self.actor_id, revision, self.context.policy_version, self.context.trace_id, audit, backup_id, "a" * 64, now))
            connection.execute("INSERT INTO backup_manifests(tenant_id,workspace_id,backup_id,manifest_version,manifest_digest,encrypted_inventory,object_count,created_at) VALUES(%s,%s,%s,1,%s,%s,1,%s)",
                (self.tenant_id, self.workspace_id, backup_id, hashlib.sha256(envelope).hexdigest(), envelope, now))
            for state in ("capturing", "verifying", "ready"):
                connection.execute("UPDATE backup_records SET state=%s,version=version+1,verified_at=%s WHERE backup_id=%s", (state, now, backup_id))
        self.assertEqual(service.get_backup(self.context, backup_id).state, "ready")
        with self.assertRaises(RecoveryError) as caught:
            service.create_restore_preview(self.context, backup_id,
                destination=RestoreDestination("fixture-tenant", "fixture-workspace", "fixture-db", "fixture-bucket"),
                idempotency_key="historical-preview-" + self.suffix, step_up_verified=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.opens, [])

    def test_backup_and_restore_survive_service_restart_and_touch_real_stores(self) -> None:
        # 원업무 L3 합격 기준 보존: 현재 v1 준비 입구/실 materializer가 없어 NOT RUN이다.
        # 이 시험을 409/503 기대나 fake v2로 바꾸어 녹색화하지 않는다.
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
        self.assertEqual(self.storage.read_fixture(restored.preview.destination, self.object_id), self.content)
        with self._connect() as connection:
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (restored.preview.destination.tenant_id,))
            connection.execute("SELECT set_config('app.workspace_id', %s, true)", (restored.preview.destination.workspace_id,))
            target = connection.execute("SELECT digest_sha256,byte_size,status FROM object_records WHERE object_id=%s", (self.object_id,)).fetchone()
        self.assertEqual(target, (self.digest, len(self.content), "completed"))
        wrong_workspace = replace(self.context, workspace_id=f"wrong-{self.workspace_id}")
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.get_backup(wrong_workspace, backup.backup_id)
        with self.assertRaisesRegex(Exception, "BACKUP_UNAVAILABLE"):
            restarted.locate_backup_workspace(f"wrong-{self.tenant_id}", backup.backup_id)
        restarted.close()


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
