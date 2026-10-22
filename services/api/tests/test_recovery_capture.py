"""합성 로컬 파일의 독립 보존 시험. PG/Object 서비스·DB materialization 증거가 아니다."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import secrets
import shutil
import unittest
import tempfile
from dataclasses import replace

from daon_user_api.recovery import BackupObjectInput, RecoveryError


ROOT: Path | None = None


class RecoveryCaptureFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global ROOT
        parent = Path(tempfile.gettempdir()).absolute()
        for ancestor in (parent, *parent.parents):
            if ancestor.is_symlink() or getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400:
                raise AssertionError("TEST_ROOT_LINK_FORBIDDEN")
        cls._owned_temp = tempfile.TemporaryDirectory(prefix="r4-file-")
        cls.addClassCleanup(cls._owned_temp.cleanup)
        ROOT = Path(cls._owned_temp.name)
        if not ROOT.is_absolute() or ROOT.resolve() != ROOT or ROOT.is_symlink():
            raise AssertionError("TEST_ROOT_BOUNDARY_INVALID")

    @classmethod
    def tearDownClass(cls):
        if ROOT.resolve() != ROOT or ROOT.is_symlink():
            raise AssertionError("S44_TEST_CLEANUP_BOUNDARY_INVALID")
        shutil.rmtree(ROOT)
        if ROOT.exists():
            raise AssertionError("S44_TEST_ROOT_REMAINS")

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("daon_user_api.recovery_capture"), "L2-A 독립 capture codec/file port 필요")
        from daon_user_api.recovery_capture import CaptureScope, FileCaptureStore
        self.root = ROOT / secrets.token_hex(8)
        self.root.mkdir()
        self.archive = self.root / "archive"
        self.archive.mkdir()
        self.key = secrets.token_bytes(32)
        self.scope = CaptureScope("capture-a", "tenant-a", "workspace-a", "notebook-a", ("version-a",),
            "a" * 64, "test-schema", "policy-a", "watermark-a")
        self.store = FileCaptureStore(self.archive, master_key=self.key, key_version="key-v1", key_reference="existing-manifest-key")
        self.content = b"S44 synthetic original\x00" * 200000  # 실제 4MiB 조각 경계를 넘긴다.
        self.item = BackupObjectInput("object-a", hashlib.sha256(self.content).hexdigest(), len(self.content))
        self.source = self.root / "synthetic-source"
        self.source.write_bytes(self.content)
        self.metadata = {"notebooks": [{"tenant_id": "tenant-a", "workspace_id": "workspace-a", "notebook_id": "notebook-a", "created_by": "actor-a", "created_at": "2026-10-18T00:00:00+00:00"}],
            "notebook_metadata_versions": [{"tenant_id": "tenant-a", "workspace_id": "workspace-a", "notebook_id": "notebook-a", "version": 1, "title": "합성 자료", "description": None, "is_current": True, "updated_by": "actor-a", "updated_at": "2026-10-18T00:00:00+00:00"}],
            "sources": [{"tenant_id": "tenant-a", "workspace_id": "workspace-a", "record_id": "source-a", "aggregate_id": "source-a", "version": 1, "schema_version": 1, "canonical_json": {"filename": "synthetic.txt"}, "digest_sha256": hashlib.sha256(b'{"filename":"synthetic.txt"}').hexdigest(), "previous_version_id": None, "state": "registered", "created_by": "actor-a", "trace_id": "trace-a", "created_at": "2026-10-18T00:00:00+00:00"}],
            "source_versions": [{"tenant_id": "tenant-a", "workspace_id": "workspace-a", "record_id": "version-a", "aggregate_id": "version-a", "version": 1, "schema_version": 1, "canonical_json": {"object_id": "object-a", "content_digest_sha256": self.item.checksum_sha256, "byte_size": self.item.byte_size}, "digest_sha256": "b" * 64, "previous_version_id": "unselected-provenance-only", "source_id": "source-a", "object_id": "object-a", "created_by": "actor-a", "trace_id": "trace-a", "created_at": "2026-10-18T00:00:00+00:00"}],
            "notebook_bindings": [{"tenant_id": "tenant-a", "workspace_id": "workspace-a", "notebook_id": "notebook-a", "binding_kind": "source", "record_id": "source-a", "version_id": "version-a", "created_by": "actor-a", "created_at": "2026-10-18T00:00:00+00:00"}],
            "object_records": [{"tenant_id": "tenant-a", "workspace_id": "workspace-a", "object_id": "object-a", "area": "source", "digest_sha256": self.item.checksum_sha256, "byte_size": self.item.byte_size, "content_type": "text/plain", "status": "completed"}]}
        from daon_user_api.data_canon import canonical_json_bytes
        self.metadata["source_versions"][0]["digest_sha256"] = hashlib.sha256(canonical_json_bytes(self.metadata["source_versions"][0]["canonical_json"])).hexdigest()

    def capture(self, **kwargs):
        return self.store.capture(self.scope, self.metadata, (self.item,), lambda item: self.source.open("rb"), **kwargs)

    def test_saved_bytes_restore_after_original_deleted_and_restart(self):
        from daon_user_api.recovery_capture import FileCaptureStore
        receipt = self.capture()
        self.source.unlink()
        restarted = FileCaptureStore(self.archive, master_key=self.key, key_version="key-v1", key_reference="existing-manifest-key")
        result = restarted.verify(self.scope, expected_receipt=receipt)
        self.assertEqual(result.metadata, self.metadata)
        target = self.root / "restored"
        restored = restarted.restore(self.scope, target, expected_receipt=receipt)
        self.assertEqual(restored["object-a"].read_bytes(), self.content)
        self.assertEqual(receipt["format_version"], 2)
        self.assertEqual(receipt["objects"][0]["chunks"], 2)
        self.assertEqual(receipt["objects"][0]["plaintext_digest"], self.item.checksum_sha256)
        self.assertEqual(receipt["objects"][0]["byte_size"], len(self.content))
        for path in (self.archive / "capture-a").iterdir():
            self.assertNotIn(b"S44 synthetic original", path.read_bytes())

    def test_source_corruption_never_creates_verified_receipt_or_overwrites_capture(self):
        self.source.write_bytes(b"changed")
        with self.assertRaises(RecoveryError): self.capture()
        self.assertFalse((self.archive / "capture-a" / "receipt.bin").exists())
        with self.assertRaises(RecoveryError): self.capture()

    def test_scope_key_receipt_truncation_substitution_and_order_fail_closed(self):
        from daon_user_api.recovery_capture import FileCaptureStore
        receipt = self.capture()
        with self.assertRaises(RecoveryError): self.store.verify(replace(self.scope, workspace_id="other"))
        wrong = FileCaptureStore(self.archive, master_key=secrets.token_bytes(32), key_version="key-v1", key_reference="existing-manifest-key")
        with self.assertRaises(RecoveryError): wrong.verify(self.scope)
        changed = dict(receipt, selection_fingerprint="f" * 64)
        with self.assertRaises(RecoveryError): self.store.verify(self.scope, expected_receipt=changed)
        path = self.archive / "capture-a" / "data.bin"
        original = path.read_bytes()
        for invalid in (original[:-1], original + b"x", original[:80] + bytes([original[80] ^ 1]) + original[81:]):
            path.write_bytes(invalid)
            with self.assertRaises(RecoveryError): self.store.verify(self.scope)
        path.write_bytes(original)
        # 동일 capture 안의 실제 암호문 frame을 재배열한다.
        import struct
        offset = len(b"DAON-CAPTURE-V2\n")
        end_metadata = offset + 4 + struct.unpack(">I", original[offset:offset + 4])[0]
        end_first = end_metadata + 4 + struct.unpack(">I", original[end_metadata:end_metadata + 4])[0]
        path.write_bytes(original[:end_metadata] + original[end_first:] + original[end_metadata:end_first])
        with self.assertRaises(RecoveryError): self.store.verify(self.scope)

    def test_traversal_existing_destination_hardlinks_and_reparse_rejected(self):
        from daon_user_api.recovery_capture import FileCaptureStore
        with self.assertRaises(RecoveryError): replace(self.scope, capture_id="../outside")
        receipt = self.capture()
        with self.assertRaises(RecoveryError): self.capture()
        target = self.root / "existing"
        target.mkdir()
        (target / "keep").write_bytes(b"unchanged")
        with self.assertRaises(RecoveryError): self.store.restore(self.scope, target, expected_receipt=receipt)
        self.assertEqual((target / "keep").read_bytes(), b"unchanged")
        data = self.archive / "capture-a" / "data.bin"
        os.link(data, self.root / "hardlink")
        with self.assertRaises(RecoveryError): self.store.verify(self.scope)
        # Windows junction는 privileged symlink 생성 없이 실제 reparse 검사를 검증한다.
        linked = self.root / "junction"
        if os.name == "nt":
            import subprocess
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(self.archive)], capture_output=True)
            self.assertEqual(result.returncode, 0, "junction fixture unavailable; success fallback 금지")
        else:
            linked.symlink_to(self.archive, target_is_directory=True)
        try:
            with self.assertRaises(RecoveryError): FileCaptureStore(linked, master_key=self.key, key_version="key-v1", key_reference="existing-manifest-key")
        finally:
            linked.rmdir() if os.name == "nt" else linked.unlink()

    def test_restart_ledger_mismatch_uncertainty_and_metadata_change_never_recapture(self):
        receipt = self.capture()
        self.assertEqual(self.store.reconcile(self.scope, lambda: receipt), "referenced")
        self.assertEqual(self.store.reconcile(self.scope, lambda: None), "unreferenced")
        with self.assertRaises(RecoveryError):
            self.store.reconcile(self.scope, lambda: dict(receipt, metadata_digest="f" * 64))
        def unavailable(): raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        with self.assertRaises(RecoveryError): self.store.reconcile(self.scope, unavailable)
        self.metadata["notebook_metadata_versions"][0]["title"] = "변경된 제목"
        with self.assertRaises(RecoveryError): self.capture()
        self.assertEqual(self.store.verify(self.scope).receipt, receipt)

    def test_limits_missing_relation_nonce_reuse_and_key_version_fail_closed(self):
        from daon_user_api.recovery_capture import FileCaptureStore
        from unittest.mock import patch
        def no_read(_item): raise AssertionError("oversized source must not open")
        oversized = BackupObjectInput("object-a", "a" * 64, 512 * 1024 * 1024 + 1)
        with self.assertRaises(RecoveryError) as caught:
            self.store.capture(self.scope, self.metadata, (oversized,), no_read)
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(RecoveryError): replace(self.scope, source_version_ids=tuple("v-" + str(i) for i in range(101)))
        with self.assertRaises(RecoveryError): self.store.capture(self.scope, {}, (self.item,), no_read)
        with patch("daon_user_api.recovery_capture.MAX_METADATA_BYTES", 100):
            with self.assertRaises(RecoveryError) as caught: self.capture()
            self.assertEqual(caught.exception.status, 413)
        with patch("daon_user_api.recovery_capture.secrets.token_bytes", return_value=b"x" * 12):
            with self.assertRaises(RecoveryError): self.capture()
        self.assertFalse((self.archive / "capture-a" / "receipt.bin").exists())
        second = replace(self.scope, capture_id="capture-b")
        receipt = self.store.capture(second, self.metadata, (self.item,), lambda _: self.source.open("rb"))
        other = FileCaptureStore(self.archive, master_key=self.key, key_version="key-v2", key_reference="existing-manifest-key")
        with self.assertRaises(RecoveryError): other.verify(second, expected_receipt=receipt)

    def test_manifest_v2_codec_binds_exact_receipt_and_preserves_no_plaintext(self):
        from daon_user_api.recovery_capture import decode_manifest, encode_manifest
        receipt = self.capture()
        envelope = encode_manifest(self.scope, receipt, master_key=self.key,
            key_version="key-v1", key_reference="existing-manifest-key")
        self.assertTrue(envelope.startswith(b"DRM2"))
        self.assertNotIn(b'metadata_digest', envelope)
        scope, restored = decode_manifest(envelope, master_key=self.key,
            key_version="key-v1", key_reference="existing-manifest-key")
        self.assertEqual(scope, self.scope)
        self.assertEqual(restored, receipt)
        with self.assertRaises(RecoveryError): decode_manifest(envelope[:-1], master_key=self.key,
            key_version="key-v1", key_reference="existing-manifest-key")

    def test_selected_backup_file_db_failure_replay_and_permission_recheck(self):
        """SQL transaction port는 대역이고 bytes/암호화/파일은 실제다. PG PASS가 아니다."""
        import copy
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import patch
        from daon_user_api.recovery import BackupSelectionInput, RecoveryContext, _ResolvedBackupSelection
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True,
            session_id="session-a", device_id="device-a")
        resolved = _ResolvedBackupSelection(BackupSelectionInput("notebook-a", ("version-a",)),
            "test-schema", "watermark-a", (self.item,), "a" * 64)
        service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(self.key).digest()
        service._manifest_master_key = self.key
        service._capture_store = self.store
        service._clock = lambda: __import__("datetime").datetime(2026, 10, 18, tzinfo=__import__("datetime").timezone.utc)
        service._notification_inbox_enabled = False
        opens = []
        def open_source(ctx, item):
            self.assertEqual(ctx, context); self.assertEqual(item, self.item)
            opens.append(item.object_id)
            return self.source.open("rb")
        service._storage = SimpleNamespace(open_source=open_source,
            verify_source=lambda *args: self.fail("v2 must not read whole source via v1 verify_source"))
        committed, mode, sql_log = {}, ["rollback"], []
        @contextmanager
        def transaction(*args):
            working = copy.deepcopy(committed)
            class Connection:
                def execute(self, sql, params=()):
                    sql_log.append(sql)
                    row = None
                    if sql.startswith("SELECT backup_id, request_fingerprint"):
                        row = working.get("record")
                    elif sql.startswith("INSERT INTO backup_records"):
                        working["record"] = dict(tenant_id=params[0], workspace_id=params[1], backup_id=params[2], actor_id=params[3],
                            trigger_type=params[4], schema_revision=params[5], retention_watermark=params[6], policy_version=params[7],
                            trace_id=params[8], idempotency_key=params[10], request_fingerprint=params[11], created_at=params[12],
                            state="ready", version=4, verified_at=params[12])
                    elif sql.startswith("INSERT INTO backup_manifests"):
                        working["manifest"] = {"manifest_version": params[3], "manifest_digest": params[4], "encrypted_inventory": params[5], "object_count": params[6]}
                    elif sql.startswith("SELECT b.*"):
                        row = {**working.get("record", {}), **working.get("manifest", {})}
                    return SimpleNamespace(fetchone=lambda: row)
            yield Connection()
            if mode[0] == "rollback" and working != committed:
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
            committed.clear(); committed.update(working)
            if mode[0] == "response-lost":
                raise RecoveryError("RESOURCE_UNAVAILABLE", 503)
        service._transaction = transaction
        recheck = lambda: (resolved, self.metadata)
        request_key = "capture-key"
        with patch.object(service, "_append_audit", return_value="audit-synthetic"), \
             patch.object(service, "_select_backup", side_effect=lambda conn, backup_id: (SimpleNamespace(backup_id=backup_id, state="ready"), (self.item,))):
            # 원장 INSERT 전 확정 source 실패는 정확 owner/잠금 미참조 확인 후 부분 파일을 회수한다.
            self.source.write_bytes(b"wrong source")
            with self.assertRaises(RecoveryError) as caught:
                service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=recheck)
            self.assertEqual(caught.exception.status, 409)
            failed = next(self.archive.iterdir())
            self.assertEqual({p.name for p in failed.iterdir()}, {"owner.bin"})
            self.assertFalse(any(sql.startswith("INSERT") for sql in sql_log))
            self.source.write_bytes(self.content)
            with self.assertRaises(RecoveryError):
                service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=recheck)
            self.assertEqual(opens, ["object-a"], "정리 후 같은 key로 원본을 재읽거나 재생성하지 않는다")
            for failure in ("acl", "metadata"):
                checks = []
                def final_failure():
                    checks.append(True)
                    if len(checks) == 2:
                        if failure == "acl": raise RecoveryError("CURRENT_ACCESS_DENIED", 403)
                        changed = copy.deepcopy(self.metadata)
                        changed["notebook_metadata_versions"][0]["title"] = "도중 변경"
                        return resolved, changed
                    return resolved, self.metadata
                with self.assertRaises(RecoveryError) as caught:
                    service.create_selected_backup(context, resolved, self.metadata,
                        idempotency_key="capture-" + failure, recheck=final_failure)
                self.assertEqual(caught.exception.status, 403 if failure == "acl" else 409)
                self.assertEqual(len(checks), 2)
            self.assertTrue(all({p.name for p in folder.iterdir()} == {"owner.bin"} for folder in self.archive.iterdir()))
            request_key = "capture-key-new-explicit"
            opens.clear(); sql_log.clear()
            # DB commit 전 실패: 새 ready 행0, 파일은 보존하며 자동 반복0.
            with self.assertRaises(RecoveryError):
                service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=recheck)
            self.assertEqual(committed, {})
            self.assertEqual(opens, ["object-a"])
            original_verify = self.store.verify
            for reason, status in (("session-expired", 401), ("acl-revoked", 403), ("unbound", 404), ("source-deleted", 404)):
                verified, revoked = [], []
                def during_verify(*args, **kwargs):
                    value = original_verify(*args, **kwargs)
                    verified.append(True)
                    if len(verified) == 2: revoked.append(True)
                    return value
                def live_check():
                    if revoked: raise RecoveryError("CURRENT_ACCESS_DENIED", status)
                    return resolved, self.metadata
                mode[0] = "normal"
                sql_start = len(sql_log)
                with patch.object(self.store, "verify", side_effect=during_verify):
                    with self.assertRaises(RecoveryError) as caught:
                        service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=live_check)
                self.assertEqual(caught.exception.status, status, reason)
                self.assertFalse(any(sql.startswith("INSERT") for sql in sql_log[sql_start:]))
            mode[0] = "response-lost"
            # 명시 재조회/재시도: 기존 검증 사본만 재사용, 원본은 이미 없다.
            self.source.unlink()
            with self.assertRaises(RecoveryError):
                service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=recheck)
            self.assertEqual(committed["manifest"]["manifest_version"], 2)
            mode[0] = "normal"
            self.metadata["notebook_metadata_versions"][0]["title"] = "재시작 뒤 새 제목"
            view = service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=recheck)
            self.assertEqual(view.state, "ready")
            self.assertEqual(opens, ["object-a"])
            self.assertEqual(sum(sql.startswith("INSERT INTO backup_records") for sql in sql_log), 2)
            for reason, status in (("session-expired", 401), ("acl-revoked", 403), ("unbound", 404), ("source-deleted", 404)):
                revoked = []
                def replay_verify(*args, **kwargs):
                    value = original_verify(*args, **kwargs); revoked.append(True)
                    return value
                def replay_check():
                    if revoked: raise RecoveryError("CURRENT_ACCESS_DENIED", status)
                    return resolved, self.metadata
                with patch.object(self.store, "verify", side_effect=replay_verify):
                    with self.assertRaises(RecoveryError) as caught:
                        service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=replay_check)
                self.assertEqual(caught.exception.status, status, reason)
            self.assertEqual(sum(sql.startswith("INSERT INTO backup_records") for sql in sql_log), 2)
            from daon_user_api.recovery_capture import decode_manifest
            scope, receipt = decode_manifest(committed["manifest"]["encrypted_inventory"], master_key=self.key,
                key_version="key-v1", key_reference="existing-manifest-key")
            self.assertEqual(self.store.verify(scope, expected_receipt=receipt).metadata["notebook_metadata_versions"][0]["title"], "합성 자료")
            changed = replace(resolved, fingerprint="f" * 64)
            with self.assertRaises(RecoveryError):
                service.create_selected_backup(context, changed, self.metadata, idempotency_key=request_key, recheck=recheck)
            def revoked(): raise RecoveryError("CURRENT_ACCESS_DENIED", 403)
            with self.assertRaises(RecoveryError) as caught:
                service.create_selected_backup(context, resolved, self.metadata, idempotency_key=request_key, recheck=revoked)
            self.assertEqual(caught.exception.status, 403)

    def test_capture_metadata_projects_one_statement_and_refuses_extra_relations(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from unittest.mock import patch
        from daon_user_api.recovery import BackupSelectionInput, RecoveryContext, _ResolvedBackupSelection
        from daon_user_api.recovery_postgres import PostgresBackupSelectionResolver
        resolved = _ResolvedBackupSelection(BackupSelectionInput("notebook-a", ("version-a",)),
            "test-schema", "watermark-a", (self.item,), "a" * 64)
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        sql_log = []
        class Reader:
            def execute(inner, sql, params=()):
                sql_log.append((sql, params))
                return SimpleNamespace(fetchone=lambda: (self.metadata,))
        @contextmanager
        def read(ctx): yield Reader(), "test-schema"
        resolver = PostgresBackupSelectionResolver(None)
        with patch.object(resolver, "_read", read), patch.object(resolver, "_resolve", return_value=resolved):
            actual = resolver.capture_metadata(context, resolved)
            self.assertEqual(actual, self.metadata)
        self.assertEqual(len(sql_log), 1)
        self.assertNotIn("object_key", sql_log[0][0])
        self.assertNotIn("staging_key", sql_log[0][0])
        self.assertNotIn("SELECT *", sql_log[0][0])
        self.metadata["credentials"] = [{"password": "not-a-real-secret"}]
        with patch.object(resolver, "_read", read), patch.object(resolver, "_resolve", return_value=resolved):
            with self.assertRaises(RecoveryError): resolver.capture_metadata(context, resolved)

    def test_posix_port_is_explicit_and_never_uses_windows_success_fallback(self):
        from daon_user_api.recovery_capture import _PosixFiles
        if os.name == "nt":
            with self.assertRaises(RecoveryError) as caught: _PosixFiles()
            self.assertEqual(caught.exception.status, 503)

    def test_root_substitution_and_receipt_rename_collision_never_overwrite(self):
        from unittest.mock import patch
        original = self.store._fs.rename
        def collision(source, target):
            target.write_bytes(b"existing unverified receipt")
            original(source, target)
        with patch.object(self.store._fs, "rename", side_effect=collision):
            with self.assertRaises(RecoveryError): self.capture()
        self.assertEqual((self.archive / "capture-a" / "receipt.bin").read_bytes(), b"existing unverified receipt")
        moved = self.root / "original-archive"
        self.archive.rename(moved)
        self.archive.mkdir()
        with self.assertRaises(RecoveryError): self.capture()

    def test_malformed_metadata_and_provenance_never_expand_selected_versions(self):
        import copy
        from daon_user_api.data_canon import canonical_json_bytes
        from daon_user_api.recovery_capture import validate_metadata
        self.metadata["source_versions"][0]["previous_version_id"] = "p" * 256
        self.metadata["sources"][0].update(record_id="s" * 256, aggregate_id="a" * 256)
        self.metadata["source_versions"][0].update(source_id="s" * 256, aggregate_id="v" * 256)
        self.metadata["notebook_bindings"][0]["record_id"] = "s" * 256
        # 이전 ID는 정확 provenance만 보존하고 1개 선택 집합을 늘리지 않는다.
        receipt = self.capture()
        self.assertEqual(receipt["source_version_ids"], ["version-a"])
        for table, key, value in (("sources", "record_id", []), ("notebooks", "created_at", "invalid-date"),
                                  ("source_versions", "version", True), ("notebook_bindings", "version_id", "wrong-version")):
            malformed = copy.deepcopy(self.metadata)
            malformed[table][0][key] = value
            with self.assertRaises(RecoveryError): validate_metadata(self.scope, malformed, (self.item,))

    def test_short_read_stream_is_normal_but_eof_excess_and_nonbytes_are_rejected(self):
        import io
        class ShortReader(io.BytesIO):
            def read(inner, count):
                self.assertLessEqual(count, 4 * 1024 * 1024)
                return super().read(min(count, 4093))
        receipt = self.store.capture(self.scope, self.metadata, (self.item,), lambda _: ShortReader(self.content))
        self.assertEqual(self.store.verify(self.scope, expected_receipt=receipt).receipt, receipt)
        for index, content in enumerate((self.content[:-1], self.content + b"x")):
            with self.assertRaises(RecoveryError):
                self.store.capture(replace(self.scope, capture_id="bad-stream-" + str(index)), self.metadata,
                    (self.item,), lambda _: ShortReader(content))
        class BadReader(ShortReader):
            def read(inner, count): return None
        with self.assertRaises(RecoveryError):
            self.store.capture(replace(self.scope, capture_id="nonbytes"), self.metadata, (self.item,), lambda _: BadReader())

    def test_owned_partial_cleanup_requires_exact_owner_and_locked_unreferenced_ledger(self):
        from contextlib import contextmanager
        owner = "synthetic-owner-a"
        self.source.write_bytes(b"wrong original")
        with self.assertRaises(RecoveryError):
            self.store.capture(self.scope, self.metadata, (self.item,), lambda _: self.source.open("rb"), owner_id=owner)
        @contextmanager
        def absent(): yield None
        @contextmanager
        def unknown(): raise RecoveryError("RESOURCE_UNAVAILABLE", 503); yield
        @contextmanager
        def referenced(): yield {"known": "referenced"}
        for guard, supplied_owner in ((unknown, owner), (referenced, owner), (absent, "wrong-owner")):
            with self.assertRaises(RecoveryError):
                self.store.discard_unreferenced(self.scope, owner_id=supplied_owner, ledger_guard=guard)
            self.assertTrue((self.archive / "capture-a" / "owner.bin").exists())
        # 다른 소유 capture/파일은 cleanup 대상에 포함되지 않는다.
        other = self.archive / "other-owner"
        other.mkdir(); (other / "keep").write_bytes(b"unchanged")
        self.store.discard_unreferenced(self.scope, owner_id=owner, ledger_guard=absent)
        self.assertEqual({p.name for p in (self.archive / "capture-a").iterdir()}, {"owner.bin"})
        with self.assertRaises(RecoveryError): self.capture()
        self.assertEqual((other / "keep").read_bytes(), b"unchanged")

    def test_manifest_cannot_be_substituted_for_another_backup_row_or_preview(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from daon_user_api.recovery_capture import encode_manifest
        from daon_user_api.recovery import RecoveryContext, RestoreDestination
        from daon_user_api.recovery_postgres import PostgresRecoveryService
        service = object.__new__(PostgresRecoveryService)
        service._key = hashlib.sha256(self.key).digest()
        token = service._capture_identity("tenant-a", "workspace-a", "actor-a", "synthetic-key")
        scope = replace(self.scope, capture_id="capture-" + token)
        backup_id = "backup-" + token
        receipt = self.store.capture(scope, self.metadata, (self.item,), lambda _: self.source.open("rb"))
        envelope = encode_manifest(scope, receipt, master_key=self.key,
            key_version=self.store.key_version, key_reference=self.store.key_reference)
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        row = {"backup_id": backup_id, "tenant_id": "tenant-a", "workspace_id": "workspace-a",
            "actor_id": "actor-a", "idempotency_key": "synthetic-key", "manifest_version": 2,
            "manifest_digest": hashlib.sha256(envelope).hexdigest(), "encrypted_inventory": envelope,
            "schema_revision": "test-schema", "policy_version": "policy-a", "retention_watermark": "watermark-a",
            "request_fingerprint": "a" * 64, "object_count": 1, "state": "ready", "version": 4,
            "trigger_type": "manual", "created_at": now, "verified_at": now}
        service._manifest_master_key = self.key
        service._capture_store = self.store
        context = RecoveryContext("tenant-a", "workspace-a", "actor-a", "trace-a", "policy-a", True)
        class ReadPort:
            def execute(inner, sql, params=()): return SimpleNamespace(fetchone=lambda: row)
        for key, value in (("backup_id", "backup-unrelated"), ("actor_id", "other-actor"), ("idempotency_key", "other-key"), ("manifest_digest", "f" * 64),
                           ("request_fingerprint", "f" * 64), ("schema_revision", "wrong"),
                           ("policy_version", "wrong"), ("retention_watermark", "wrong"), ("object_count", 2)):
            original = row[key]
            row[key] = value
            with self.subTest(field=key):
                with self.assertRaises(RecoveryError):
                    service._require_independent_capture(ReadPort(), context, row["backup_id"])
                with self.assertRaises(RecoveryError): service._backup_from_row(row)
            row[key] = original
        service._require_independent_capture(ReadPort(), context, backup_id)
        with self.assertRaises(RecoveryError):
            service._require_independent_capture(ReadPort(), context, "backup-unrelated")
        @contextmanager
        def transaction(*args): yield ReadPort()
        service._transaction = transaction
        with self.assertRaises(RecoveryError) as caught:
            service.create_restore_preview(context, backup_id, destination=RestoreDestination(
                "fixture-tenant-b", "fixture-workspace-b", "fixture-database-b", "fixture-bucket-b"),
                idempotency_key="preview-no-materializer", step_up_verified=True)
        self.assertEqual(caught.exception.status, 503)

    def test_runtime_from_env_opt_in_builds_validated_store_and_default_stays_off(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from daon_user_api import runtime
        config = {"DAON_RUNTIME_PROFILE": "test", "DAON_API_DATABASE_PATH": str(self.root / "runtime.sqlite3"),
            "DAON_CLOUD_DATABASE_DSN": "dbname=never_connect", "DAON_RECOVERY_MANIFEST_KEY_FILE": str(self.root / "existing-key-ref"),
            "DAON_RECOVERY_CAPTURE_ROOT": str(self.archive), "DAON_RECOVERY_CAPTURE_KEY_VERSION": "key-v1",
            "DAON_RECOVERY_CAPTURE_KEY_REFERENCE": "DAON_RECOVERY_MANIFEST_KEY_FILE"}
        with patch.dict(os.environ, config, clear=True):
            settings = runtime.RuntimeSettings.from_env()
        self.assertEqual(getattr(settings, "recovery_capture_root", None), self.archive,
            "정상 from_env가 명시 root/version/ref를 소비해야 한다")
        def memory_key(path):
            self.assertEqual(path, self.root / "existing-key-ref")
            return self.key
        with patch.object(Path, "read_bytes", memory_key):
            service = runtime._build_recovery_service(settings, SimpleNamespace())
        try:
            self.assertIsNotNone(service._capture_store)
            self.assertEqual(service._capture_store.root, self.archive)
            self.assertEqual(service._capture_store.key_reference, "DAON_RECOVERY_MANIFEST_KEY_FILE")
            self.assertTrue(service._pool.closed, "DI 구성은 실제 PG 연결을 시작하지 않는다")
        finally:
            service.close()
        self.assertFalse((self.root / "existing-key-ref").exists(), "시험 key는 메모리만 사용한다")
        for changed in ({"DAON_RECOVERY_CAPTURE_KEY_VERSION": ""}, {"DAON_RECOVERY_CAPTURE_KEY_REFERENCE": "OTHER_KEY"},
                        {"DAON_RECOVERY_CAPTURE_ROOT": "relative"}):
            with patch.dict(os.environ, {**config, **changed}, clear=True):
                with self.assertRaises(ValueError): runtime.RuntimeSettings.from_env()
        for removed in ("DAON_RECOVERY_CAPTURE_ROOT", "DAON_RECOVERY_CAPTURE_KEY_VERSION", "DAON_RECOVERY_CAPTURE_KEY_REFERENCE"):
            with patch.dict(os.environ, {key: value for key, value in config.items() if key != removed}, clear=True):
                with self.assertRaises(ValueError): runtime.RuntimeSettings.from_env()
        with patch.dict(os.environ, {"DAON_RUNTIME_PROFILE": "test", "DAON_API_DATABASE_PATH": str(self.root / "default.sqlite3")}, clear=True):
            default = runtime.RuntimeSettings.from_env()
        with patch.object(runtime, "_build_recovery_service", wraps=runtime._build_recovery_service) as factory:
            dependencies = runtime.build_dependencies(default)
        try:
            self.assertEqual(factory.call_count, 1)
            self.assertIsNone(default.recovery_capture_root)
            self.assertEqual(type(dependencies.recovery_service).__name__, "UnavailableRecoveryService")
        finally:
            dependencies.close()


if __name__ == "__main__": unittest.main()
