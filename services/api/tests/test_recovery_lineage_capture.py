"""R1-B Windows 합성 실파일/AEAD 시험. 실제 PG/MinIO·DB materialization 증거 아님."""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import io
import json
import os
import secrets
import shutil
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from daon_user_api.recovery import RecoveryError
from test_recovery_lineage import Ledger, recanonical


ROOT = Path(os.environ.get("DAON_LINEAGE_CAPTURE_TEST_ROOT", "D:/tmp/daon-user-s44-d1-eoul2-20261019"))


class LineageCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = {"daon-user-s44-d1-eoul2-20261019", "daon-user-s44-d1-main-20261019"}
        if (not ROOT.is_absolute() or ROOT.parent != Path("D:/tmp") or ROOT.name not in names
                or ROOT.exists() or ROOT.is_symlink()):
            raise AssertionError("R1B_EXACT_NEW_TEST_ROOT_REQUIRED")
        for parent in (ROOT.parent, *ROOT.parent.parents):
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
                raise AssertionError("R1B_TEST_ROOT_LINK_FORBIDDEN")
        ROOT.mkdir()

    @classmethod
    def tearDownClass(cls):
        if ROOT.resolve() != ROOT or ROOT.is_symlink(): raise AssertionError("R1B_TEST_CLEANUP_BOUNDARY")
        shutil.rmtree(ROOT)
        if ROOT.exists(): raise AssertionError("R1B_TEST_ROOT_REMAINS")

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("daon_user_api.recovery_lineage_capture"), "R1-B 독립 v3 보존 포트 미구현")
        self.m = importlib.import_module("daon_user_api.recovery_lineage_capture")
        self.root = ROOT / secrets.token_hex(8); self.root.mkdir()
        self.original = self.root / "original"; self.original.mkdir()
        self.artifacts = self.root / "artifacts"; self.artifacts.mkdir()
        self.key = secrets.token_bytes(32)
        self.ledger = Ledger()
        self.content = {"object-a": b"R1B prior original\n" * 37, "object-b": b"r" * (4 * 1024 * 1024 + 7)}
        self.opened = []
        for row in self.ledger.tables["object_records"]:
            content = self.content[row["object_id"]]
            row["byte_size"], row["digest_sha256"] = len(content), hashlib.sha256(content).hexdigest()
            (self.original / row["object_id"]).write_bytes(content)
        for row in self.ledger.tables["source_versions"]:
            obj = next(x for x in self.ledger.tables["object_records"] if x["object_id"] == row["object_id"])
            recanonical(row, {"object_id": obj["object_id"], "byte_size": obj["byte_size"], "content_digest_sha256": obj["digest_sha256"]})
        self.selection = self.ledger.resolve()
        self.scope = self.m.LineageCaptureScope.from_selection(self.ledger.context, self.selection, capture_id="capture-a")
        self.store = self.m.FileLineageCaptureStore(self.artifacts, master_key=self.key, key_version="test-v3", key_reference="memory-only")

    @contextmanager
    def open_source(self, ref):
        self.opened.append(ref.object_id)
        with (self.original / ref.object_id).open("rb") as handle: yield handle

    def capture(self, **overrides):
        arguments = dict(closure_fingerprint=self.selection.fingerprint, include_prior_versions=True,
            recheck=self.ledger.resolve, owner_id="writer-owned")
        arguments.update(overrides)
        return self.store.capture(self.scope, self.selection, self.open_source, **arguments)

    def test_v3_original_absent_restart_readback_and_manifest(self):
        receipt = self.capture()
        for path in self.original.iterdir(): path.unlink()
        restarted = self.m.FileLineageCaptureStore(self.artifacts, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        verified = restarted.verify(self.scope, expected_receipt=receipt)
        self.assertEqual(verified.metadata, self.selection.metadata())
        self.assertEqual(receipt["root_source_version_ids"], ["root-version"])
        self.assertEqual(receipt["dependency_source_version_ids"], ["prior-version"])
        self.assertEqual((receipt["source_version_count"], receipt["source_count"], receipt["object_count"]), (2, 1, 2))
        for identity, expected in self.content.items():
            sink = io.BytesIO(); restarted.read_object(self.scope, identity, sink, expected_receipt=receipt)
            self.assertEqual(sink.getvalue(), expected)
        self.assertEqual(self.opened, ["object-a", "object-b"])
        envelope = self.m.encode_manifest(self.scope, receipt, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        self.assertTrue(envelope.startswith(b"DRM3"))
        self.assertEqual(self.m.decode_manifest(envelope, master_key=self.key, key_version="test-v3", key_reference="memory-only"), (self.scope, receipt))
        from daon_user_api.recovery_capture import decode_manifest
        with self.assertRaises(RecoveryError): decode_manifest(envelope, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        for path in (self.artifacts / "lineage-v3-capture-a").iterdir():
            self.assertNotIn(b"prior-version", path.read_bytes())
        self.assertEqual(verified.metadata["sources"][0]["version"], 5)
        self.assertEqual(len(verified.metadata["canon_state_transitions"]), 4)

    def test_v3_scope_key_purpose_and_ciphertext_tamper_refused(self):
        receipt = self.capture()
        for scope in (replace(self.scope, actor_id="actor-other"), replace(self.scope, workspace_id="workspace-other"),
                replace(self.scope, policy_version="policy-other"), replace(self.scope, closure_fingerprint="f" * 64)):
            with self.assertRaises(RecoveryError): self.store.verify(scope)
        for key, version, reference in ((secrets.token_bytes(32), "test-v3", "memory-only"),
                (self.key, "other-version", "memory-only"), (self.key, "test-v3", "other-reference")):
            with self.assertRaises(RecoveryError):
                self.m.FileLineageCaptureStore(self.artifacts, master_key=key, key_version=version, key_reference=reference).verify(self.scope)
        path = self.artifacts / "lineage-v3-capture-a" / "data.bin"; saved = path.read_bytes()
        for changed in (saved[:-1], saved + b"x", saved[:100] + bytes([saved[100] ^ 1]) + saved[101:]):
            path.write_bytes(changed)
            with self.assertRaises(RecoveryError): self.store.verify(self.scope, expected_receipt=receipt)
        path.write_bytes(saved)
        envelope = self.m.encode_manifest(self.scope, receipt, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        for changed in (b"DRM2" + envelope[4:], envelope[:-1], envelope + b"x"):
            with self.assertRaises(RecoveryError): self.m.decode_manifest(changed, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        # 파일 receipt를 DB-manifest 목적으로 재사용할 수 없다.
        blob = (path.parent / "receipt.bin").read_bytes()
        with self.assertRaises(RecoveryError): self.m.decode_manifest(blob, master_key=self.key, key_version="test-v3", key_reference="memory-only")

    def test_v3_exact_metadata_closure_and_limits_fail_closed(self):
        for defect in ("unknown", "locator", "missing", "duplicate-object", "duplicate-version", "history", "canon", "binding", "foreign", "versions-limit", "bytes-limit", "metadata-limit"):
            with self.subTest(defect=defect):
                meta = copy.deepcopy(self.selection.metadata())
                if defect == "unknown": meta["credentials"] = []
                elif defect == "locator": meta["object_records"][0]["object_key"] = "private/raw/path"
                elif defect == "missing": meta["source_versions"].pop(0)
                elif defect == "duplicate-object": meta["object_records"].append(copy.deepcopy(meta["object_records"][0]))
                elif defect == "duplicate-version": meta["source_versions"].append(copy.deepcopy(meta["source_versions"][0]))
                elif defect == "history": meta["canon_transition_attempts"].pop()
                elif defect == "canon": meta["sources"][0]["digest_sha256"] = "f" * 64
                elif defect == "binding": meta["notebook_bindings"][0]["version_id"] = "prior-version"
                elif defect == "foreign": meta["sources"][0]["workspace_id"] = "hidden-workspace"
                elif defect == "versions-limit": meta["source_versions"] *= 51
                elif defect == "bytes-limit": meta["object_records"][0]["byte_size"] = 512 * 1024 * 1024 + 1
                else: meta["notebook_metadata_versions"][0]["description"] = "x" * (32 * 1024 * 1024)
                forged = replace(self.selection, _encoded=json.dumps(meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
                with self.assertRaises(RecoveryError) as caught:
                    self.store.capture(self.scope, forged, self.open_source, closure_fingerprint=self.scope.closure_fingerprint,
                        include_prior_versions=True, recheck=lambda: forged)
                self.assertEqual(caught.exception.status, 413 if defect.endswith("limit") else 409)
                self.assertNotIn("hidden-workspace", str(caught.exception))
                self.assertFalse((self.artifacts / "lineage-v3-capture-a").exists())

    def test_v3_shared_object_preserves_two_versions_but_captures_bytes_once(self):
        first = self.ledger.tables["source_versions"][0]
        second = self.ledger.tables["source_versions"][1]
        second["object_id"] = first["object_id"]
        recanonical(second, copy.deepcopy(first["canonical_json"]))
        self.ledger.tables["object_records"] = self.ledger.tables["object_records"][:1]
        self.selection = self.ledger.resolve()
        self.scope = self.m.LineageCaptureScope.from_selection(self.ledger.context, self.selection, capture_id="capture-a")
        receipt = self.capture()
        self.assertEqual(self.opened, ["object-a"])
        self.assertEqual(receipt["object_count"], 1)
        self.assertEqual(receipt["total_bytes"], len(self.content["object-a"]))
        self.assertEqual(len(self.store.verify(self.scope).metadata["source_versions"]), 2)

    def test_v3_short_reads_partial_failure_consent_and_final_recheck(self):
        for change in ({"include_prior_versions": False}, {"closure_fingerprint": "f" * 64}, {"recheck": None}):
            with self.assertRaises(RecoveryError): self.capture(**change)
        self.assertFalse((self.artifacts / "lineage-v3-capture-a").exists())
        @contextmanager
        def short(ref):
            data = io.BytesIO(self.content[ref.object_id])
            yield type("ShortReader", (), {"read": lambda inner, n: data.read(min(n, 127))})()
        receipt = self.store.capture(self.scope, self.selection, short, closure_fingerprint=self.selection.fingerprint,
            include_prior_versions=True, recheck=self.ledger.resolve)
        self.assertEqual(self.store.verify(self.scope).receipt, receipt)
        changed_scope = replace(self.scope, capture_id="late-revoked")
        checks = [0]
        def revoked():
            checks[0] += 1
            if checks[0] == 2: self.ledger.revoked.add("prior-version")
            return self.ledger.resolve()
        with self.assertRaises(RecoveryError):
            self.store.capture(changed_scope, self.selection, self.open_source, closure_fingerprint=self.selection.fingerprint,
                include_prior_versions=True, recheck=revoked)
        folder = self.artifacts / "lineage-v3-late-revoked"
        self.assertEqual({x.name for x in folder.iterdir()}, {"owner.bin"})
        self.assertFalse((self.artifacts / "lineage-v3.lock").exists())

    def test_v3_owner_cleanup_uncertain_reference_and_no_overwrite(self):
        receipt = self.capture()
        path = self.artifacts / "lineage-v3-capture-a" / "data.bin"; saved = path.read_bytes()
        with self.assertRaises(RecoveryError): self.capture()
        self.assertEqual(path.read_bytes(), saved)
        def unavailable(): raise RuntimeError("S44_PRIVATE_LEDGER_DETAIL")
        with self.assertRaises(RecoveryError) as caught: self.store.reconcile(self.scope, unavailable)
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(path.read_bytes(), saved)
        @contextmanager
        def referenced(): yield receipt
        with self.assertRaises(RecoveryError): self.store.discard_unreferenced(self.scope, owner_id="writer-owned", reference_guard=referenced)
        @contextmanager
        def unreferenced(): yield None
        with self.assertRaises(RecoveryError): self.store.discard_unreferenced(self.scope, owner_id="wrong-owner", reference_guard=unreferenced)
        self.store.discard_unreferenced(self.scope, owner_id="writer-owned", reference_guard=unreferenced)
        self.assertEqual({p.name for p in path.parent.iterdir()}, {"owner.bin"})
        scope = replace(self.scope, capture_id="uncertain")
        @contextmanager
        def uncertain(ref):
            with self.open_source(ref) as stream: yield stream
            raise RecoveryError("BACKUP_CAPTURE_CLEANUP_UNCERTAIN", 503)
        with self.assertRaises(RecoveryError):
            self.store.capture(scope, self.selection, uncertain, closure_fingerprint=self.selection.fingerprint,
                include_prior_versions=True, recheck=self.ledger.resolve)
        self.assertTrue((self.artifacts / "lineage-v3-uncertain" / "data.bin").exists())
        self.assertTrue((self.artifacts / "lineage-v3.lock").exists())
        self.assertFalse((self.artifacts / "lineage-v3-uncertain" / "receipt.bin").exists())

    def test_v3_root_identity_links_and_owned_outside_file_guard(self):
        from unittest.mock import patch
        with self.assertRaises(RecoveryError):
            self.m.FileLineageCaptureStore(Path("relative-root"), master_key=self.key, key_version="test-v3", key_reference="memory-only")
        with patch.object(self.store, "_root_identity", (0, 0)):
            with self.assertRaises(RecoveryError): self.capture()
        receipt = self.capture()
        folder = self.artifacts / "lineage-v3-capture-a"
        unrelated = folder / "unrelated.bin"; unrelated.write_bytes(b"owned outside capture inventory")
        @contextmanager
        def unreferenced(): yield None
        with self.assertRaises(RecoveryError): self.store.discard_unreferenced(self.scope, owner_id="writer-owned", reference_guard=unreferenced)
        self.assertEqual(unrelated.read_bytes(), b"owned outside capture inventory")
        self.assertEqual(self.store.verify(self.scope, expected_receipt=receipt).receipt, receipt)
        link = self.root / "hard-link.bin"; os.link(folder / "data.bin", link)
        with self.assertRaises(RecoveryError): self.store.verify(self.scope)
        link.unlink()

    def test_v3_receipt_mismatch_and_late_corruption_release_no_plaintext(self):
        receipt = self.capture()
        for key, value in (("metadata_digest", "f" * 64), ("data_size", receipt["data_size"]+1),
                ("source_version_count", 1), ("root_source_version_ids", ["other-version"]),
                ("key_reference", "other-reference")):
            with self.subTest(field=key):
                sink = io.BytesIO()
                with self.assertRaises(RecoveryError):
                    self.store.read_object(self.scope, "object-a", sink, expected_receipt={**receipt, key: value})
                self.assertEqual(sink.getvalue(), b"")
        path = self.artifacts / "lineage-v3-capture-a" / "data.bin"
        saved = path.read_bytes(); path.write_bytes(saved[:-1] + bytes([saved[-1] ^ 1]))
        sink = io.BytesIO()
        with self.assertRaises(RecoveryError): self.store.read_object(self.scope, "object-a", sink)
        self.assertEqual(sink.getvalue(), b"")
        path.write_bytes(saved)
        sink = io.BytesIO()
        with self.assertRaises(RecoveryError): self.store.read_object(self.scope, "foreign-object", sink)
        self.assertEqual(sink.getvalue(), b"")

    def test_v3_source_hash_size_or_malformed_failure_cleans_only_new_partial(self):
        for index, defect in enumerate(("hash", "short", "extra", "type", "oversize")):
            with self.subTest(defect=defect):
                scope = replace(self.scope, capture_id=f"partial-{index}")
                @contextmanager
                def broken(ref):
                    content = self.content[ref.object_id]
                    if defect == "hash": content = b"!" * len(content)
                    elif defect == "short": content = content[:-1]
                    elif defect == "extra": content += b"!"
                    reader = io.BytesIO(content)
                    if defect == "type": reader = type("InvalidReader", (), {"read": lambda inner, n: None})()
                    elif defect == "oversize": reader = type("InvalidReader", (), {"read": lambda inner, n: b"x" * (n+1)})()
                    yield reader
                with self.assertRaises(RecoveryError):
                    self.store.capture(scope, self.selection, broken, closure_fingerprint=self.selection.fingerprint,
                        include_prior_versions=True, recheck=self.ledger.resolve, owner_id="writer-owned")
                folder = self.artifacts / ("lineage-v3-" + scope.capture_id)
                self.assertEqual({p.name for p in folder.iterdir()}, {"owner.bin"})
                self.assertFalse((self.artifacts / "lineage-v3.lock").exists())

    def test_v3_rootlock_cleanup_failure_is_private_and_preserves_evidence(self):
        from unittest.mock import patch
        original = self.store._fs.unlink
        def failed(path):
            if path.name == "lineage-v3.lock": raise OSError("R1B_PRIVATE_PATH_DETAIL")
            return original(path)
        with patch.object(self.store._fs, "unlink", side_effect=failed):
            with self.assertRaises(RecoveryError) as caught: self.capture()
        self.assertEqual(caught.exception.code, "BACKUP_CAPTURE_CLEANUP_UNCERTAIN")
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue((self.artifacts / "lineage-v3.lock").exists())
        self.assertTrue((self.artifacts / "lineage-v3-capture-a" / "data.bin").exists())

    def test_legacy_v2_real_file_semantics_not_promoted_to_v3(self):
        from daon_user_api.recovery import BackupObjectInput
        from daon_user_api.recovery_capture import CaptureScope, FileCaptureStore, encode_manifest, decode_manifest
        original = self.selection.metadata()
        selected = {key: copy.deepcopy(value) for key, value in original.items()
            if key not in {"canon_state_transitions", "canon_transition_attempts"}}
        selected["source_versions"] = [r for r in selected["source_versions"] if r["record_id"] == "root-version"]
        selected["object_records"] = [r for r in selected["object_records"] if r["object_id"] == "object-b"]
        for row in selected["sources"] + selected["source_versions"]: del row["canonical_text"]
        folder = self.root / "legacy-v2"; folder.mkdir()
        scope = CaptureScope("legacy-a", self.scope.tenant_id, self.scope.workspace_id, self.scope.notebook_id,
            ("root-version",), "a" * 64, self.scope.schema_revision, self.scope.policy_version, self.scope.retention_watermark)
        store = FileCaptureStore(folder, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        obj = selected["object_records"][0]
        receipt = store.capture(scope, selected, (BackupObjectInput(obj["object_id"], obj["digest_sha256"], obj["byte_size"]),), self.open_source)
        (self.original / "object-b").unlink()
        restarted = FileCaptureStore(folder, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        self.assertEqual(restarted.verify(scope).metadata, selected)
        self.assertEqual(receipt["format_version"], 2)
        self.assertEqual(len(receipt["objects"]), 1)
        self.assertEqual(selected["source_versions"][0]["previous_version_id"], "prior-version")
        envelope = encode_manifest(scope, receipt, master_key=self.key, key_version="test-v3", key_reference="memory-only")
        self.assertEqual(decode_manifest(envelope, master_key=self.key, key_version="test-v3", key_reference="memory-only"), (scope, receipt))
        with self.assertRaises(RecoveryError):
            self.m.decode_manifest(envelope, master_key=self.key, key_version="test-v3", key_reference="memory-only")

    def test_v3_baseexception_keeps_partial_owner_and_lock_and_propagates_original(self):
        class SyntheticCancellation(BaseException): pass
        for index, exception in enumerate((KeyboardInterrupt(), SystemExit(17), SyntheticCancellation())):
            with self.subTest(interruption=type(exception).__name__):
                root = self.root / f"interrupted-{index}"; root.mkdir()
                store = self.m.FileLineageCaptureStore(root, master_key=self.key, key_version="test-v3", key_reference="memory-only")
                calls = []
                @contextmanager
                def interrupted(ref):
                    calls.append(ref.object_id)
                    if len(calls) == 2: raise exception
                    with self.open_source(ref) as source: yield source
                with self.assertRaises(type(exception)) as caught:
                    store.capture(self.scope, self.selection, interrupted, closure_fingerprint=self.selection.fingerprint,
                        include_prior_versions=True, recheck=self.ledger.resolve, owner_id="writer-owned")
                self.assertIs(caught.exception, exception)
                self.assertEqual(calls, ["object-a", "object-b"])
                folder = root / "lineage-v3-capture-a"
                self.assertTrue((folder / "data.bin").exists())
                self.assertTrue((folder / "owner.bin").exists())
                self.assertFalse((folder / "receipt.bin").exists())
                self.assertTrue((root / "lineage-v3.lock").exists())

    def test_v3_cleanup_baseexception_keeps_partial_lock_and_original_interruption(self):
        from unittest.mock import patch
        for index, exception in enumerate((KeyboardInterrupt(), SystemExit(19))):
            with self.subTest(interruption=type(exception).__name__):
                root = self.root / f"cleanup-interrupted-{index}"; root.mkdir()
                store = self.m.FileLineageCaptureStore(root, master_key=self.key, key_version="test-v3", key_reference="memory-only")
                original = store._fs.unlink
                def interrupt(path):
                    if path.name == "data.bin": raise exception
                    return original(path)
                @contextmanager
                def broken(ref): yield io.BytesIO(b"!" * len(self.content[ref.object_id]))
                with patch.object(store._fs, "unlink", side_effect=interrupt):
                    with self.assertRaises(type(exception)) as caught:
                        store.capture(self.scope, self.selection, broken, closure_fingerprint=self.selection.fingerprint,
                            include_prior_versions=True, recheck=self.ledger.resolve, owner_id="writer-owned")
                self.assertIs(caught.exception, exception)
                folder = root / "lineage-v3-capture-a"
                self.assertTrue((folder / "data.bin").exists())
                self.assertTrue((folder / "owner.bin").exists())
                self.assertFalse((folder / "receipt.bin").exists())
                self.assertTrue((root / "lineage-v3.lock").exists())


if __name__ == "__main__": unittest.main()
