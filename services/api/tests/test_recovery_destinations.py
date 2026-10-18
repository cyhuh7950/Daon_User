"""R2 직접 합성 검증. 실제 PG/Object probe·예약 경합 증거가 아니다."""
from __future__ import annotations

import copy
import ctypes
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import secrets
import shutil
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

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


if __name__ == "__main__": unittest.main()
