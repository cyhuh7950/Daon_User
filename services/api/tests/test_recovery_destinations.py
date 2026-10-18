"""R2 직접 합성 검증. 실제 PG/Object probe·예약 경합 증거가 아니다."""
from __future__ import annotations

import copy
import ctypes
import hashlib
import hmac
import importlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from daon_user_api.recovery import RecoveryContext, RecoveryError


ROOT = Path("D:/tmp/daon-user-s44-d1-eoul2-20261019")
NOW = datetime(2026, 10, 19, 1, tzinfo=timezone.utc)
PURPOSE = b"restore-destination-registry-v1"


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def signed(document, master, *, purpose=PURPOSE):
    value = copy.deepcopy(document)
    value.pop("signature", None)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(purpose).digest(),
               info=purpose + b"/" + value["key_version"].encode()).derive(master)
    value["signature"] = hmac.new(key, encoded(value), hashlib.sha256).hexdigest()
    return encoded(value)


def protect_windows(path, *, broad_write=False):
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    from ctypes import wintypes
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]; kernel.LocalFree.argtypes = [ctypes.c_void_p]
    if not security.OpenProcessToken(wintypes.HANDLE(-1), 8, ctypes.byref(token)): raise RuntimeError("TEST_ACL_FAILED")
    try:
        needed = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)): raise RuntimeError("TEST_ACL_FAILED")
        sid = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(ctypes.c_void_p.from_buffer(buffer), ctypes.byref(sid)): raise RuntimeError("TEST_ACL_FAILED")
        try: sid_text = sid.value
        finally: kernel.LocalFree(sid)
        descriptor = ctypes.c_void_p()
        text = f"D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;{sid_text})" + ("(A;;FA;;;WD)" if broad_write else "")
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(text, 1, ctypes.byref(descriptor), None): raise RuntimeError("TEST_ACL_FAILED")
        try:
            if not security.SetFileSecurityW(str(path), 0x80000004, descriptor): raise RuntimeError("TEST_ACL_FAILED")
        finally: kernel.LocalFree(descriptor)
    finally: kernel.CloseHandle(token)


class RecoveryDestinationTests(unittest.TestCase):
    def setUp(self):
        module = Path(__file__).parents[1] / "src/daon_user_api/recovery_destinations.py"
        self.assertTrue(module.is_file(), "R2 signed registry/reservation implementation missing")
        self.api = importlib.import_module("daon_user_api.recovery_destinations")
        self.assertEqual(ROOT.parent, Path("D:/tmp"))
        for parent in (ROOT.parent, *ROOT.parent.parents):
            self.assertFalse(parent.is_symlink())
            self.assertFalse(getattr(parent.lstat(), "st_file_attributes", 0) & 0x400)
        self.assertFalse(ROOT.exists(), "owned root must be new")
        ROOT.mkdir()
        self.addCleanup(self.cleanup)
        protect_windows(ROOT)
        self.key = secrets.token_bytes(32)
        self.context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a")
        self.request = self.api.ProbeRequest("source-a", "target-a", "database-a", "bucket-a", "owner-a")
        self.observation = self.api.PhysicalObservation(
            self.request, self.api.DatabaseIdentity("10001", 111), self.api.DatabaseIdentity("10001", 222),
            self.api.ObjectIdentity("backend-a", "s44-source"), self.api.ObjectIdentity("backend-a", "s44-target"),
            NOW, True, True, "owner-a", False, False)
        self.entry = dict(destination_id="destination-a", version=1, display_name="격리 대상 A",
                          capture_kind="selected-source-lineage-v3", source_instance_ref="source-a",
                          authorized_tenant_id="tenant-a", authorized_workspace_id="workspace-a", authorized_actor_id="actor-a",
                          target_instance_ref="target-a", database_ref="database-a", bucket_ref="bucket-a",
                          physical_receipt_digest=self.api.physical_receipt_digest(self.observation),
                          owner_operation_id="owner-a", created_at="2026-10-19T00:59:00Z", expires_at="2026-10-19T01:59:00Z",
                          application_enabled=False, outbound_enabled=False)
        self.document = dict(schema_version=1, generation_id="generation-a", entries=[self.entry], key_version="key-v1")
        case = self
        class Probe(self.api.TrustedPhysicalProbe):
            def observe(self, request):
                case.assertEqual(request, case.observation.request)
                return case.observation
        self.probe = Probe()

    def cleanup(self):
        # 정확 본인 신규 root만. 링크/경계 변경은 성공 정리로 보고하지 않는다.
        self.assertEqual(ROOT.parent, Path("D:/tmp"))
        self.assertFalse(ROOT.is_symlink())
        self.assertFalse(getattr(ROOT.lstat(), "st_file_attributes", 0) & 0x400)
        shutil.rmtree(ROOT)
        self.assertFalse(ROOT.exists())

    def registry(self, raw=None):
        path = ROOT / "destinations.json"
        path.write_bytes(signed(self.document, self.key) if raw is None else raw)
        protect_windows(path)
        return self.api.SignedDestinationRegistry(ROOT, {"key-v1": self.key}, "source-a", clock=lambda: NOW)

    def resolve(self, registry=None, **kwargs):
        return (registry or self.registry()).resolve_registered_destination(
            kwargs.get("context", self.context), "destination-a", kwargs.get("version", 1),
            kwargs.get("probe", self.probe))

    def test_destination_signature_scope_version_expiry(self):
        good = self.resolve()
        self.assertEqual(good.summary(), dict(destination_id="destination-a", version=1,
                         display_name="격리 대상 A", expires_at="2026-10-19T01:59:00Z"))
        for field, value in (("version", True), ("version", 0), ("capture_kind", "service-snapshot-v1"),
                             ("source_instance_ref", "source-other"), ("application_enabled", 0),
                             ("outbound_enabled", True), ("expires_at", "2026-10-19T00:59:00Z"),
                             ("expires_at", "2026-10-21T01:00:00Z"), ("created_at", "not-a-time"),
                             ("authorized_actor_id", "actor-other")):
            with self.subTest(field=field, value=value):
                old = self.entry[field]; self.entry[field] = value
                with self.assertRaises(RecoveryError): self.resolve()
                self.entry[field] = old
        for context in (replace(self.context, actor_id="other"), replace(self.context, workspace_id="other"),
                        replace(self.context, tenant_id="other")):
            with self.subTest(context=context), self.assertRaises(RecoveryError): self.resolve(context=context)
        with self.assertRaises(RecoveryError): self.resolve(version=2)
        raw = json.loads(signed(self.document, self.key)); raw["entries"][0]["version"] = 2
        with self.assertRaises(RecoveryError): self.resolve(self.registry(encoded(raw)))
        with self.assertRaises(RecoveryError):
            self.resolve(self.api.SignedDestinationRegistry(ROOT, {"key-v1": secrets.token_bytes(32)}, "source-a", clock=lambda: NOW))
        with self.assertRaises(RecoveryError): self.resolve(self.registry(signed(self.document, self.key, purpose=b"selected-source-lineage-v3")))

    def test_exact_json_and_known_key_and_generation(self):
        raw = signed(self.document, self.key)
        for bad in (raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'),
                    raw.replace(b'"version":1', b'"version":1,"version":1'), b'[]', b'{"schema_version":NaN}'):
            with self.subTest(case=bad[:20]), self.assertRaises(RecoveryError): self.resolve(self.registry(bad))
        for owner, field, value in ((self.document, "extra", "sensitive-canary"),
                                    (self.entry, "endpoint", "sensitive-canary"), (self.document, "key_version", "unknown")):
            owner[field] = value
            with self.assertRaises(RecoveryError): self.resolve()
            owner.pop(field)
            if field == "key_version": owner[field] = "key-v1"
        registry = self.registry(); self.resolve(registry)
        self.entry["display_name"] = "바뀐 이름"
        self.registry()  # 같은 generation의 유효 서명이라도 몰래 수정 불가.
        with self.assertRaises(RecoveryError): self.resolve(registry)

    def test_target_alias_or_unverifiable_identity_rejected(self):
        original = self.observation
        for changed in (replace(original, target_database=original.source_database),
                        replace(original, target_object=original.source_object),
                        replace(original, database_empty=False), replace(original, bucket_empty=False),
                        replace(original, observed_at=NOW-timedelta(minutes=2)),
                        replace(original, owner_operation_id="someone-else"),
                        replace(original, target_database=self.api.DatabaseIdentity("", 222)),
                        replace(original, target_object=self.api.ObjectIdentity("", "s44-target"))):
            with self.subTest(case=changed), self.assertRaises(RecoveryError):
                self.observation = changed; self.resolve()
        self.observation = original
        with self.assertRaises(RecoveryError): self.resolve(probe=None)
        with self.assertRaises(RecoveryError): self.resolve(probe=lambda request: original)
        before = self.api.physical_receipt_digest(original)
        self.assertEqual(before, self.api.physical_receipt_digest(replace(original, observed_at=NOW+timedelta(seconds=1))))
        self.assertNotEqual(before, self.api.physical_receipt_digest(replace(original, target_database=self.api.DatabaseIdentity("10002", 222))))

    def test_alias_entries_and_probe_finish_clock_are_rechecked(self):
        self.document["entries"].append({**self.entry, "destination_id": "alias-b", "database_ref": "alias-db", "bucket_ref": "alias-bucket"})
        case = self
        class Aliases(self.api.TrustedPhysicalProbe):
            def observe(self, request): return replace(case.observation, request=request)
        with self.assertRaises(RecoveryError): self.resolve(probe=Aliases())
        self.document["entries"].pop()
        tick = [NOW]
        class Slow(self.api.TrustedPhysicalProbe):
            def observe(self, request):
                tick[0] += timedelta(seconds=1)
                return replace(case.observation, observed_at=tick[0])
        registry = self.registry(); registry._clock = lambda: tick[0]
        self.assertEqual(self.resolve(registry, probe=Slow()).summary()["version"], 1)
        tick[0] = NOW+timedelta(minutes=59)-timedelta(microseconds=1)
        with self.assertRaises(RecoveryError): self.resolve(registry, probe=Slow())

    def test_registry_never_exposes_service_snapshot_or_raw_refs(self):
        other = {**self.entry, "destination_id": "destination-other", "authorized_actor_id": "other"}
        service = {**self.entry, "destination_id": "destination-service", "capture_kind": "service-snapshot-v1"}
        self.document["entries"] += [other, service]
        registry = self.registry()
        result = registry.summaries(self.context, self.probe)
        self.assertEqual(result, [self.resolve(registry).summary()])
        rendered = json.dumps(result, ensure_ascii=False) + repr(self.resolve(registry))
        for hidden in ("database-a", "bucket-a", "source-a", "target-a", "owner-a", "physical_receipt_digest", "key-v1"):
            self.assertNotIn(hidden, rendered)

    def test_registry_file_links_and_missing_and_oversize_rejected(self):
        registry = self.registry()
        (ROOT / "destinations.json").unlink()
        with self.assertRaises(RecoveryError): self.resolve(registry)
        (ROOT / "destinations.json").write_bytes(b" "*(1024*1024+1))
        with self.assertRaises(RecoveryError): self.resolve(registry)
        (ROOT / "destinations.json").unlink()
        (ROOT / "other.json").write_bytes(signed(self.document, self.key))
        os.link(ROOT / "other.json", ROOT / "destinations.json")
        with self.assertRaises(RecoveryError): self.resolve(registry)

    def test_registry_unprotected_file_and_root_rejected(self):
        registry = self.registry()
        protect_windows(ROOT / "destinations.json", broad_write=True)
        if os.name != "nt": (ROOT / "destinations.json").chmod(0o666)
        with self.assertRaises(RecoveryError): self.resolve(registry)
        self.registry()
        protect_windows(ROOT, broad_write=True)
        if os.name != "nt": ROOT.chmod(0o777)
        try:
            with self.assertRaises(RecoveryError): self.resolve(registry)
        finally: protect_windows(ROOT)

    def test_destination_request_reservation_conflict_and_exact_replay(self):
        destination = self.resolve()
        ledger = ReservationSqlDouble(self)
        self.api.reserve_destination(ledger, self.context, destination, request_id="request-a", backup_id="backup-a", manifest_digest="a"*64, clock=lambda: NOW)
        self.api.reserve_destination(ledger, self.context, destination, request_id="request-a", backup_id="backup-a", manifest_digest="a"*64, clock=lambda: NOW)
        self.assertEqual(len(ledger.rows), 1)
        for kwargs in (dict(request_id="request-b"), dict(backup_id="backup-b"), dict(manifest_digest="b"*64)):
            args = dict(request_id="request-a", backup_id="backup-a", manifest_digest="a"*64); args.update(kwargs)
            with self.assertRaises(RecoveryError): self.api.reserve_destination(ledger, self.context, destination, **args, clock=lambda: NOW)

    def test_other_generation_same_database_or_bucket_conflicts(self):
        ledger = ReservationSqlDouble(self)
        first = self.resolve()
        self.api.reserve_destination(ledger, self.context, first, request_id="request-a", backup_id="backup-a", manifest_digest="a"*64, clock=lambda: NOW)
        original = self.observation
        for db, obj in ((original.target_database, self.api.ObjectIdentity("backend-b", "s44-target-b")),
                        (self.api.DatabaseIdentity("20001", 444), original.target_object)):
            self.document["generation_id"] = "generation-b"
            self.observation = replace(original, target_database=db, target_object=obj)
            self.entry["physical_receipt_digest"] = self.api.physical_receipt_digest(self.observation)
            with self.subTest(db=db), self.assertRaises(RecoveryError):
                self.api.reserve_destination(ledger, self.context, self.resolve(), request_id="request-b", backup_id="backup-a", manifest_digest="a"*64, clock=lambda: NOW)

    def test_sql_draft_exact_additive_invariants(self):
        directory = Path(__file__).parents[1] / "migrations/versions"
        sql_path = directory / "0070_registered_restore_destinations.sql"
        self.assertTrue(sql_path.is_file(), "R2 additive SQL draft missing")
        sql = sql_path.read_text(encoding="utf-8")
        self.assertEqual(sql.count("CREATE TABLE public.registered_restore_"), 3)
        for text in ("UNIQUE(target_database_fingerprint)", "UNIQUE(target_bucket_fingerprint)",
                     "UNIQUE(tenant_id,workspace_id,request_id)", "manifest_version=3", "FOR UPDATE",
                     "NEW.closure_fingerprint IS DISTINCT FROM backup.request_fingerprint",
                     "FORCE ROW LEVEL SECURITY", "SECURITY DEFINER SET search_path=pg_catalog,pg_temp",
                     "BEFORE INSERT ON public.restore_previews", "REGISTERED_RESTORE_MODE_CONFLICT",
                     "REGISTERED_RESTORE_PHASE_CONFLICT", "has_table_privilege", "has_column_privilege"):
            self.assertIn(text, sql)
        self.assertNotIn("CREATE OR REPLACE", sql)
        self.assertFalse("backup.actor_id IS DISTINCT FROM NEW.actor_id" in sql,
                         "현재 복원자와 원 backup 생성자의 동일성을 새 권한 정책으로 요구하면 안 됨")
        for table in ("backup_records", "backup_manifests", "restore_requests", "restore_previews"):
            self.assertNotIn("ALTER TABLE public."+table, sql)
            self.assertNotIn("GRANT UPDATE ON public."+table, sql)
        migration = (directory / "0070_registered_restore_destinations.py").read_text(encoding="utf-8")
        self.assertIn('down_revision = "0069"', migration)
        self.assertIn("REGISTERED_RESTORE_FORWARD_RECOVERY_REQUIRED", migration)
        self.assertIn("ACCESS EXCLUSIVE", migration)
        # POLICY_MANAGE 자체에는 permission 추가분이 없으며 정식 role matrix와 일치.
        from daon_user_api.authorization import Action, ROLE_ACTION_MATRIX
        permitted = sorted(role.value for role, actions in ROLE_ACTION_MATRIX.items() if Action.POLICY_MANAGE in actions)
        self.assertEqual(permitted, ["organization_admin", "personal_owner", "workspace_admin"])


class ReservationSqlDouble:
    """SQL/params를 확인하는 대역. UNIQUE 실경합/RLS 검증이 아니다."""
    def __init__(self, case): self.case = case; self.rows = []; self.result = None

    def execute(self, statement, params):
        sql = " ".join(statement.split()).lower()
        self.case.assertIn("public.registered_restore_target_reservations", sql)
        if sql.startswith("insert"):
            self.case.assertIn("on conflict do nothing", sql)
            row = tuple(params)
            conflict = any(r[0:2] == row[0:2] or r[11] == row[11] or r[12] == row[12] or r[2:5] == row[2:5] for r in self.rows)
            if not conflict: self.rows.append(row)
            self.result = None
        else:
            self.case.assertIn("where registry_generation_id = %s and destination_id = %s", sql)
            self.result = next((r for r in self.rows if r[:2] == tuple(params)), None)
        return self

    def fetchone(self): return self.result


class ConcretePhysicalProbeTests(unittest.TestCase):
    """실 SQL/서명/HTTP 파서 제품 + 연결/응답 대역. 실제 PG/MinIO 호출0."""
    def setUp(self):
        self.api=importlib.import_module("daon_user_api.recovery_destinations")
        self.assertIsNotNone(getattr(self.api,"PostgresMinioPhysicalProbe",None),"concrete physical probe missing")
        from daon_user_api.object_queue import MinioObjectStorageAdapter
        self.source=PhysicalSqlDouble(self,"source-db",111)
        self.target=PhysicalSqlDouble(self,"target-db",222)
        self.storage=MinioObjectStorageAdapter(endpoint="source.invalid:9000",bucket="s44-source",
            access_key=secrets.token_hex(12),secret_key=secrets.token_hex(24),secure=False)
        self.other=MinioObjectStorageAdapter(endpoint="target.invalid:9000",bucket="s44-target",
            access_key=secrets.token_hex(12),secret_key=secrets.token_hex(24),secure=False)
        self.http=ProbeHttpDouble(self)
        foundation=self.api.ProbeFoundation("tenant-a","workspace-a","actor-a","0070")
        self.instances={
            "source-a":self.api.PhysicalProbeInstance("database-source","bucket-source","source-db",self.source.connection,self.storage,"us-east-1"),
            "target-a":self.api.PhysicalProbeInstance("database-a","bucket-a","target-db",self.target.connection,self.other,"us-east-1",
                foundation,"provision-a",False,False)}
        self.probe=self.api.PostgresMinioPhysicalProbe(self.instances,clock=lambda:NOW,_pool_factory=self.http.pool)
        self.request=self.api.ProbeRequest("source-a","target-a","database-a","bucket-a","owner-a")

    def test_concrete_identity_signed_owner_and_global_empty(self):
        value=self.probe.observe(self.request)
        self.assertEqual((value.source_database.system_identifier,value.source_database.database_oid),("10001",111))
        self.assertEqual(value.target_database.database_oid,222)
        self.assertEqual(value.target_object.backend_identifier,"11111111-1111-4111-8111-111111111111")
        self.assertEqual(value.target_object.bucket,"s44-target")
        self.assertTrue(value.database_empty and value.bucket_empty)
        self.assertFalse(value.application_enabled or value.outbound_enabled)
        self.assertEqual(value.owner_operation_id,"owner-a")
        self.assertNotIn("secret-canary",repr(value))
        self.assertEqual(self.http.admin_calls,2)
        self.assertTrue(self.http.responses and all(r.closed and r.released for r in self.http.responses))
        self.assertEqual(self.source.writes+self.target.writes,0)

    def test_alias_database_or_backend_bucket_is_rejected(self):
        self.target.oid=111
        with self.assertRaises(RecoveryError): self.probe.observe(self.request)
        self.target.oid=222
        self.other._bucket="s44-source"
        with self.assertRaises(RecoveryError): self.probe.observe(self.request)

    def test_global_visibility_owner_unknown_schema_and_ref_rejected(self):
        for change in ("visibility","comment","schema","tag","ref","db-name"):
            with self.subTest(change=change):
                self.target.superuser=change!="visibility"
                self.target.comment="wrong-owner" if change=="comment" else "daon-restore-owner:owner-a"
                self.target.schemas=["public","unrelated"] if change=="schema" else ["public"]
                self.target.database_name="foreign-db" if change=="db-name" else "target-db"
                self.http.wrong_tag=change=="tag"
                request=replace(self.request,bucket_ref="unknown-ref") if change=="ref" else self.request
                with self.assertRaises(RecoveryError): self.probe.observe(request)

    def test_nonempty_business_version_delete_marker_and_multipart_are_not_empty(self):
        for table in ("sources","object_records","unknown_business"):
            with self.subTest(table=table):
                self.target.nonempty={table}
                value=self.probe.observe(self.request)
                self.assertFalse(value.database_empty)
                self.assertTrue(value.bucket_empty)
        self.target.nonempty=set()
        self.target.foundation_foreign=True
        self.assertFalse(self.probe.observe(self.request).database_empty)
        self.target.foundation_foreign=False
        for kind in ("Version","DeleteMarker","Upload"):
            with self.subTest(kind=kind):
                self.http.object_kind=kind
                self.assertFalse(self.probe.observe(self.request).bucket_empty)
        self.http.object_kind=None; self.http.mode="truncated"
        self.assertFalse(self.probe.observe(self.request).bucket_empty)
        # 재진입에서 false를 그대로 반환한다. empty 위장/registry 재등록이 아니다.
        self.target.nonempty={"sources"}
        self.assertFalse(self.probe.observe(self.request).database_empty)

    def test_http_error_redirect_size_timeout_and_raw_secret_never_reflect(self):
        for mode in ("redirect","denied","oversize","timeout","invalid-id","duplicate","doctype"):
            with self.subTest(mode=mode):
                self.http.mode=mode
                with self.assertRaises(RecoveryError) as caught: self.probe.observe(self.request)
                self.assertNotIn("secret-canary",str(caught.exception))
                self.assertNotIn("target.invalid",str(caught.exception))
                self.assertTrue(all(r.closed and r.released for r in self.http.responses))
        self.assertEqual(self.http.error_body_reads,0)

    def test_static_metadata_foundation_and_visibility_are_measured(self):
        for change in ("rules","revision","authmetadata","global-denied","relation","enabled"):
            with self.subTest(change=change):
                self.target.bad_static=change if change in {"rules","revision","authmetadata"} else None
                self.target.global_denied=change=="global-denied"
                self.target.foreign_relation=change=="relation"
                self.instances["target-a"]=replace(self.instances["target-a"],application_enabled=change=="enabled")
                probe=self.api.PostgresMinioPhysicalProbe(self.instances,clock=lambda:NOW,_pool_factory=self.http.pool)
                if change in {"global-denied","relation","enabled"}:
                    with self.assertRaises(RecoveryError): probe.observe(self.request)
                else: self.assertFalse(probe.observe(self.request).database_empty)

    def test_actual_sigv4_and_tls_static_credentials_boundary(self):
        # wire 서버 대역은 별도 HMAC 계산으로 실제 signature를 검사한다.
        sign=self.api.sign_v4_s3
        def wrong_signature(**kwargs):
            result=sign(**kwargs)
            result["Authorization"]=result["Authorization"][:-64]+"0"*64
            return result
        with patch.object(self.api,"sign_v4_s3",wrong_signature):
            with self.assertRaises(RecoveryError): self.probe.observe(self.request)
        self.assertEqual(self.probe.observe(self.request).target_database.database_oid,222)
        original=self.other._client._provider
        class UnexpectedProvider:
            def retrieve(self): raise AssertionError("dynamic credentials must not be refreshed")
        self.other._client._provider=UnexpectedProvider()
        with self.assertRaises(RecoveryError): self.probe.observe(self.request)
        self.other._client._provider=original
        from minio import Minio
        self.other._client=Minio("target.invalid:9000",credentials=original,secure=True)
        self.other._client._http.connection_pool_kw["cert_reqs"]="CERT_NONE"
        with self.assertRaises(RecoveryError): self.probe.observe(self.request)

    def test_unknown_refs_identity_missing_and_duplicate_bucket_marker_fail_closed(self):
        for field,value in (("source_instance_ref","missing"),("target_instance_ref","missing"),("database_ref","missing")):
            with self.subTest(field=field),self.assertRaises(RecoveryError):
                self.probe.observe(replace(self.request,**{field:value}))
        self.target.oid=0
        with self.assertRaises(RecoveryError): self.probe.observe(self.request)
        self.target.oid=222
        for mode in ("duplicate-tag","wrong-bucket","invalid-xml","missing-info","nil-id","utf16-doctype"):
            with self.subTest(mode=mode),self.assertRaises(RecoveryError):
                self.http.mode=mode; self.probe.observe(self.request)


class ProbeCursor:
    def __init__(self,rows): self.rows=list(rows)
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)
    def __iter__(self): return iter(self.rows)


class PhysicalSqlDouble:
    def __init__(self,case,database_name,oid):
        self.case,self.database_name,self.oid=case,database_name,oid
        self.superuser=True; self.comment="daon-restore-owner:owner-a"; self.schemas=["public"]
        self.nonempty=set(); self.foundation_foreign=False; self.bad_static=None; self.global_denied=False
        self.foreign_relation=False; self.writes=0
    @contextmanager
    def connection(self): yield self
    @contextmanager
    def transaction(self): yield
    def execute(self,statement,params=()):
        from daon_user_api.data_canon import _TRANSITIONS
        from daon_user_api.authorization import AUTHORIZATION_SCHEMA_VERSION
        sql=statement if isinstance(statement,str) else statement.as_string()
        if sql.startswith("SET TRANSACTION"):
            self.case.assertIn("READ ONLY",sql); return ProbeCursor([])
        self.case.assertTrue(sql.startswith("SELECT"),"probe must not write")
        if "pg_control_system()" in sql:
            return ProbeCursor([(self.database_name,"10001",self.oid,self.comment,self.superuser,False)])
        if "FROM pg_catalog.pg_namespace" in sql: return ProbeCursor([(n,) for n in self.schemas])
        if "FROM pg_catalog.pg_class" in sql:
            names=("alembic_version","canon_transition_rules","identity_auth_schema_metadata","tenants","workspaces","user_accounts",
                "memberships","identity_auth_tenant_roles","sources","source_versions","object_records","unknown_business")
            return ProbeCursor([("public",n,"f" if self.foreign_relation and n=="unknown_business" else "r") for n in names])
        if "FROM public.alembic_version" in sql:
            return ProbeCursor([("0069" if self.bad_static=="revision" else "0070",)])
        if "FROM public.canon_transition_rules" in sql:
            rows=sorted((entity,*edge) for entity,edges in _TRANSITIONS.items() for edge in edges)
            return ProbeCursor(rows[:-1] if self.bad_static=="rules" else rows)
        if "FROM public.identity_auth_schema_metadata" in sql:
            return ProbeCursor([(1,0 if self.bad_static=="authmetadata" else AUTHORIZATION_SCHEMA_VERSION)])
        if self.global_denied: raise PermissionError("secret-canary")
        if sql.startswith("SELECT count(*)"):
            self.case.assertNotIn(" WHERE ",sql.split("FROM ")[-1])
            self.case.assertTrue(params and params[0]=="tenant-a")
            return ProbeCursor([(1,int(self.foundation_foreign))])
        if sql.startswith("SELECT EXISTS"):
            return ProbeCursor([(any('"'+n+'"' in sql for n in self.nonempty),)])
        raise AssertionError("UNEXPECTED_PROBE_SQL")


class ProbeResponse:
    def __init__(self,http,body,status=200):
        self.http,self.body,self.status=http,io.BytesIO(body),status
        self.closed=self.released=False
    def read1(self,size,**kwargs):
        if self.status!=200:
            self.http.error_body_reads+=1; raise AssertionError("ERROR_BODY_FORBIDDEN")
        return self.body.read(min(size,333))
    def close(self): self.closed=True
    def release_conn(self): self.released=True


class ProbeHttpDouble:
    def __init__(self,case):
        self.case=case; self.mode=None; self.wrong_tag=False; self.object_kind=None
        self.responses=[]; self.error_body_reads=0; self.admin_calls=0
    def pool(self,**kwargs):
        self.case.assertIs(kwargs.get("retries"),False)
        self.case.assertIsNotNone(kwargs.get("timeout"))
        return self
    def clear(self): pass
    def urlopen(self,method,url,**kwargs):
        self.case.assertEqual(kwargs.get("retries"),False); self.case.assertEqual(kwargs.get("redirect"),False)
        self.case.assertEqual(kwargs.get("preload_content"),False)
        self.case.assertEqual(kwargs.get("decode_content"),False)
        parsed=urlsplit(url); query=parse_qs(parsed.query,keep_blank_values=True)
        self.case.assertIn(parsed.hostname,{"source.invalid","target.invalid"})
        headers=kwargs["headers"]
        self.case.assertTrue(headers.get("Authorization","").startswith("AWS4-HMAC-SHA256 Credential="))
        self.case.assertEqual(headers["Host"],parsed.netloc)
        self.case.assertIn("x-amz-date",headers)
        # SDK signer를 기대값으로 재사용하지 않고 AWS4 canonical request에서 독립 계산.
        auth=headers["Authorization"]; values=dict(part.strip().split("=",1) for part in auth.split(" ",1)[1].split(","))
        scope=values["Credential"].split("/",1)[1]
        names=values["SignedHeaders"].split(";"); lowered={k.lower():str(v).strip() for k,v in headers.items()}
        canonical_headers="\n".join(name+":"+lowered[name] for name in names)
        canonical=f"{method}\n{parsed.path}\n{parsed.query}\n{canonical_headers}\n\n{';'.join(names)}\n{hashlib.sha256(b'').hexdigest()}"
        to_sign=f"AWS4-HMAC-SHA256\n{headers['x-amz-date']}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        storage=self.case.storage if parsed.hostname=="source.invalid" else self.case.other
        credentials=storage._client._provider.retrieve()
        key=("AWS4"+credentials.secret_key).encode()
        for value in scope.split("/"): key=hmac.new(key,value.encode(),hashlib.sha256).digest()
        expected=hmac.new(key,to_sign.encode(),hashlib.sha256).hexdigest()
        self.case.assertTrue(hmac.compare_digest(expected,values["Signature"]),"invalid request signature")
        if self.mode=="timeout": raise TimeoutError("secret-canary")
        status=302 if self.mode=="redirect" else 403 if self.mode=="denied" else 200
        if parsed.path=="/minio/admin/v3/info":
            self.admin_calls+=1
            body=encoded(dict(deploymentID="invalid" if self.mode=="invalid-id" else "11111111-1111-4111-8111-111111111111",servers=["secret-canary"]))
            if self.mode=="oversize": body=b" "*(1024*1024+1)
            if self.mode=="duplicate": body=b'{"deploymentID":"one","deploymentID":"two"}'
            if self.mode=="missing-info": body=b'{}'
            if self.mode=="nil-id": body=b'{"deploymentID":"00000000-0000-0000-0000-000000000000"}'
        elif method=="HEAD": body=b""
        elif "tagging" in query:
            owner="wrong" if self.wrong_tag else "owner-a"
            body=f'<Tagging xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><TagSet><Tag><Key>daon-restore-owner-operation</Key><Value>{owner}</Value></Tag></TagSet></Tagging>'.encode()
            if self.mode=="duplicate-tag": body=body.replace(b"</TagSet>",b"<Tag><Key>daon-restore-owner-operation</Key><Value>owner-a</Value></Tag></TagSet>")
            if self.mode=="utf16-doctype": body=('<!DOCTYPE Tagging [<!ENTITY owner "owner-a">]>'+body.decode().replace(">owner-a<",">&owner;<")).encode("utf-16")
        else:
            versions="versions" in query; root="ListVersionsResult" if versions else "ListMultipartUploadsResult"
            kind=self.object_kind
            child=f"<{kind}><Key>not-returned</Key></{kind}>" if kind and ((versions and kind!="Upload") or (not versions and kind=="Upload")) else ""
            name="Name" if versions else "Bucket"
            body=f'<{root} xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><{name}>s44-target</{name}><IsTruncated>{str(self.mode=="truncated").lower()}</IsTruncated>{child}</{root}>'.encode()
            if self.mode=="wrong-bucket": body=body.replace(b"s44-target",b"foreign-bucket")
            if self.mode=="invalid-xml": body=b"<invalid"
            if self.mode=="doctype": body=b'<!DOCTYPE secret-canary [<!ENTITY x "x">]>'+body
        response=ProbeResponse(self,body,status); self.responses.append(response); return response


if __name__ == "__main__": unittest.main()
