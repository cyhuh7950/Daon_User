"""D1 B1 합성 stream/실파일만 검증한다. 실제 PG/MinIO/DB 복원이 아니다."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
from pathlib import Path
import secrets
import shutil
import unittest
from contextlib import contextmanager
from dataclasses import replace


ROOT = Path(os.environ.get("DAON_SERVICE_SNAPSHOT_TEST_ROOT", "D:/tmp/daon-user-s44-d1-eoul2-20261019"))


class ServiceSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        allowed = {"daon-user-s44-d1-eoul2-20261019", "daon-user-s44-d1-main-20261019"}
        if not ROOT.is_absolute() or ROOT.name not in allowed or (os.name == "nt" and ROOT.parent != Path("D:/tmp")):
            raise AssertionError("D1_EXACT_TEST_ROOT_REQUIRED")
        if ROOT.exists() or ROOT.is_symlink():
            raise AssertionError("D1_TEST_ROOT_MUST_BE_NEW")
        for parent in (ROOT.parent, *ROOT.parent.parents):
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
                raise AssertionError("D1_TEST_ROOT_LINK_FORBIDDEN")
        ROOT.mkdir()

    @classmethod
    def tearDownClass(cls):
        if ROOT.resolve() != ROOT or ROOT.is_symlink():
            raise AssertionError("D1_TEST_CLEANUP_BOUNDARY_INVALID")
        shutil.rmtree(ROOT)
        if ROOT.exists():
            raise AssertionError("D1_TEST_ROOT_REMAINS")

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("daon_user_api.service_snapshot"), "B1 전용 snapshot codec/file port 미구현")
        from daon_user_api import service_snapshot as module
        self.m = module
        self.root = ROOT / secrets.token_hex(8)
        self.root.mkdir()
        self.key = secrets.token_bytes(32)
        self.spec = module.ServiceSnapshotSpec("source-ref", "credential-ref", "client-ref", "root-ref",
            "code-revision", "sha256:" + "a" * 64, ("runtime-a", "rebuildable-a"), "key-v1", "existing-key-ref",
            module.SnapshotLimits(12 * 1024 * 1024, 1024, 32768, 300))
        self.archive = b"D1 SYNTHETIC ARCHIVE\x00" * 230000
        self.object_bytes = b"synthetic object"
        self.runtime_bytes = b"synthetic runtime"
        self.item = module.SnapshotObjectRef("tenant-a", "workspace-a", "source", "a" * 32,
            "completed", hashlib.sha256(self.object_bytes).hexdigest(), len(self.object_bytes))
        self.runtime = (module.SnapshotRuntimeRef("runtime-a", "bytes", hashlib.sha256(self.runtime_bytes).hexdigest(), len(self.runtime_bytes)),
            module.SnapshotRuntimeRef("rebuildable-a", "reconstructible", "b" * 64, 0))
        self.original = self.root / "synthetic-originals"
        self.original.mkdir()
        for name, value in (("database", self.archive), ("object", self.object_bytes), ("runtime", self.runtime_bytes)):
            (self.original / name).write_bytes(value)
        self.events = []
        test = self
        class FileReader(module.BoundedSnapshotReader):
            """합성 regular file 직접시험 포트. 네트워크 timeout 증거가 아니다."""
            def __init__(inner, source): inner.source = source
            def read_bounded(inner, maximum, *, deadline, cancelled):
                import time
                if cancelled(): raise module.SnapshotError("SNAPSHOT_CANCELLED")
                if time.monotonic() >= deadline: raise module.SnapshotError("SNAPSHOT_TIMEOUT")
                return inner.source.read(maximum)
            def abort_owned(inner): inner.source.close()
        self.file_reader = FileReader
        class Ports:
            @contextmanager
            def open_snapshot(inner, spec, *, cancelled=lambda: False):
                test.assertEqual(spec, test.spec)
                test.events.append("lease-open")
                try:
                    yield module.SnapshotLease("snapshot-a", "schema-a", (test.item,), test.runtime,
                        {"roles": [], "memberships": [], "relations": [], "functions": []})
                finally:
                    test.events.append("lease-close")
            @contextmanager
            def dump_archive(inner, lease):
                test.events.append("archive-open")
                stream = (test.original / "database").open("rb")
                try: yield FileReader(stream)
                finally:
                    stream.close(); test.events.append("archive-close")
            @contextmanager
            def open_object(inner, ref):
                test.assertEqual(ref, test.item)
                test.events.append("object-open")
                with (test.original / "object").open("rb") as stream: yield FileReader(stream)
            @contextmanager
            def open_runtime(inner, ref):
                test.assertEqual(ref.ref_id, "runtime-a")
                test.events.append("runtime-open")
                with (test.original / "runtime").open("rb") as stream: yield FileReader(stream)
            def verify_runtime_reference(inner, spec, ref):
                return ref.ref_id == "rebuildable-a" and ref.digest == "b" * 64
        self.ports = Ports()
        self.executor = module.ServiceSnapshotExecutor(self.root, master_key=self.key, ports=self.ports)

    def capture(self, **kwargs):
        return self.executor.capture(self.spec, capture_id="capture-a", cancelled=lambda: False, **kwargs)

    def test_snapshot_requires_all_refs_and_positive_limits(self):
        for field in ("source_instance_ref", "credential_ref", "client_ref", "root_ref", "code_revision", "key_version", "key_reference"):
            with self.subTest(field=field), self.assertRaises(self.m.SnapshotError): replace(self.spec, **{field: ""})
        for field in ("archive_bytes", "object_bytes", "metadata_bytes", "duration_seconds"):
            for value in (0, -1, True):
                with self.subTest(field=field, value=value), self.assertRaises(self.m.SnapshotError):
                    replace(self.spec.limits, **{field: value})

    def test_database_object_and_runtime_share_one_capture(self):
        receipt = self.capture()
        self.assertEqual(receipt.kind, "service-snapshot-v1")
        self.assertEqual(receipt.snapshot_id, "snapshot-a")
        self.assertEqual(receipt.schema_revision, "schema-a")
        self.assertEqual(receipt.image_digest, "sha256:" + "a" * 64)
        self.assertEqual(receipt.streams[0]["byte_size"], len(self.archive))
        self.assertEqual(receipt.streams[0]["chunks"], 2)
        self.assertEqual(self.events[-1], "lease-close")
        # 원본 접근 포트 없이 새 인스턴스로, 보존본만 실제 재읽기/복호화한다.
        self.archive = self.object_bytes = self.runtime_bytes = b""
        for file in self.original.iterdir(): file.unlink()
        restarted = self.m.ServiceSnapshotExecutor(self.root, master_key=self.key, ports=None)
        self.assertEqual(restarted.verify(self.spec, "capture-a"), receipt)
        sink = io.BytesIO()
        restarted.read_stream(self.spec, "capture-a", "runtime:runtime-a", sink)
        self.assertEqual(sink.getvalue(), b"synthetic runtime")

    def test_archive_object_or_runtime_failure_never_verified(self):
        for stage in ("dump_archive", "open_object", "open_runtime"):
            from unittest.mock import patch
            @contextmanager
            def broken(*args): raise OSError("synthetic private failure"); yield
            with patch.object(self.ports, stage, side_effect=broken):
                with self.assertRaises(self.m.SnapshotError):
                    self.executor.capture(self.spec, capture_id="failed-" + stage, cancelled=lambda: False)
            folder = self.root / ("failed-" + stage)
            self.assertFalse((folder / "receipt.bin").exists())
            self.assertEqual({p.name for p in folder.iterdir()}, {"owner.bin"})

    def test_unknown_length_archive_final_receipt_is_required(self):
        self.capture()
        (self.root / "capture-a" / "receipt.bin").unlink()
        with self.assertRaises(self.m.SnapshotError): self.executor.verify(self.spec, "capture-a")

    def test_staging_state_without_recovery_proof_never_verified(self):
        self.item = replace(self.item, state="pending")
        with self.assertRaises(self.m.SnapshotError): self.capture()
        self.assertNotIn("archive-open", self.events)

    def test_archive_bytes_never_written_as_plaintext(self):
        self.capture()
        for file in (self.root / "capture-a").iterdir():
            self.assertNotIn(b"D1 SYNTHETIC ARCHIVE", file.read_bytes())
            self.assertNotIn(b"synthetic object", file.read_bytes())
            self.assertNotIn(b"synthetic runtime", file.read_bytes())

    def test_foreign_capture_nonce_order_truncation_rejected(self):
        self.capture()
        path = self.root / "capture-a" / "data.bin"
        raw = path.read_bytes()
        for changed in (raw[:-1], raw + b"x", raw[:80] + bytes([raw[80] ^ 1]) + raw[81:]):
            path.write_bytes(changed)
            with self.assertRaises(self.m.SnapshotError): self.executor.verify(self.spec, "capture-a")
        path.write_bytes(raw)
        with self.assertRaises(self.m.SnapshotError): self.executor.verify(replace(self.spec, source_instance_ref="other"), "capture-a")
        other = self.m.ServiceSnapshotExecutor(self.root, master_key=secrets.token_bytes(32), ports=None)
        with self.assertRaises(self.m.SnapshotError): other.verify(self.spec, "capture-a")
        from unittest.mock import patch
        with patch("daon_user_api.service_snapshot.secrets.token_bytes", return_value=b"x" * 12):
            with self.assertRaises(self.m.SnapshotError):
                self.executor.capture(self.spec, capture_id="nonce-reuse", cancelled=lambda: False)

    def test_restart_uncertain_result_kept_without_overwrite(self):
        receipt = self.capture()
        before = {p.name: p.read_bytes() for p in (self.root / "capture-a").iterdir()}
        with self.assertRaises(self.m.SnapshotError): self.capture()
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "capture-a").iterdir()}, before)
        def uncertain(): raise OSError("private ledger unavailable")
        with self.assertRaises(self.m.SnapshotError): self.executor.reconcile(self.spec, "capture-a", uncertain)
        self.assertEqual(self.executor.verify(self.spec, "capture-a"), receipt)

    def test_cancel_closes_owned_streams_and_preserves_unrelated_files(self):
        keep = self.root / "unrelated"
        keep.write_bytes(b"keep")
        def cancelled(): return "archive-open" in self.events
        with self.assertRaises(self.m.SnapshotError):
            self.executor.capture(self.spec, capture_id="cancelled-a", cancelled=cancelled)
        self.assertIn("archive-close", self.events)
        self.assertEqual(self.events[-1], "lease-close")
        self.assertEqual(keep.read_bytes(), b"keep")

    def test_limits_short_reads_timeout_and_single_execution(self):
        from unittest.mock import patch
        short = io.BytesIO(b"fragmented archive")
        class Fragments:
            def read(self, count): return short.read(min(count, 3))
        @contextmanager
        def fragmented(lease):
            yield self.file_reader(Fragments())
        with patch.object(self.ports, "dump_archive", side_effect=fragmented):
            receipt = self.capture()
        self.assertEqual(receipt.streams[0]["byte_size"], 18)
        smaller = replace(self.spec, limits=replace(self.spec.limits, archive_bytes=10))
        self.spec = smaller
        with self.assertRaises(self.m.SnapshotError):
            self.executor.capture(smaller, capture_id="over-limit", cancelled=lambda: False)
        self.assertFalse((self.root / "over-limit" / "receipt.bin").exists())
        self.spec = replace(smaller, limits=replace(smaller.limits, archive_bytes=12 * 1024 * 1024))
        self.executor._clock = lambda: 1000 if "archive-open" in self.events else 0
        self.events.clear()
        with self.assertRaises(self.m.SnapshotError):
            self.executor.capture(self.spec, capture_id="timeout-a", cancelled=lambda: False)
        import time
        self.executor._clock = time.monotonic
        self.events.clear()
        second = self.m.ServiceSnapshotExecutor(self.root, master_key=self.key, ports=self.ports)
        original = self.ports.open_runtime
        @contextmanager
        def competing(ref):
            with self.assertRaises(self.m.SnapshotError):
                second.capture(self.spec, capture_id="other-concurrent", cancelled=lambda: False)
            with original(ref) as value: yield value
        with patch.object(self.ports, "open_runtime", side_effect=competing):
            self.executor.capture(self.spec, capture_id="single-a", cancelled=lambda: False)
        self.assertFalse((self.root / "other-concurrent").exists())

    def test_unproven_runtime_reference_never_verified(self):
        from unittest.mock import patch
        with patch.object(self.ports, "verify_runtime_reference", return_value=False):
            with self.assertRaises(self.m.SnapshotError): self.capture()
        self.assertFalse((self.root / "capture-a" / "receipt.bin").exists())

    def test_existing_output_area_and_unexpected_port_error_are_handled_safely(self):
        self.item = replace(self.item, area="output")
        receipt = self.capture()
        self.assertIn(":output:", receipt.streams[1]["label"])
        from unittest.mock import patch
        @contextmanager
        def unexpected(*args): raise RuntimeError("synthetic private port detail"); yield
        with patch.object(self.ports, "open_object", side_effect=unexpected):
            with self.assertRaises(self.m.SnapshotError) as caught:
                self.executor.capture(self.spec, capture_id="unexpected-a", cancelled=lambda: False)
        self.assertNotIn("private", str(caught.exception))

    def test_verified_owned_cleanup_requires_unreferenced_guard_and_preserves_uncertainty(self):
        receipt = self.capture()
        self.assertTrue(callable(getattr(self.executor, "discard_unreferenced", None)), "검증된 사본의 명시 소유 정리 port 필요")
        @contextmanager
        def absent(): yield None
        @contextmanager
        def referenced(): yield receipt
        @contextmanager
        def uncertain(): raise OSError("synthetic private reference failure"); yield
        for owner, guard in (("0" * 64, absent), (receipt.owner_id, referenced), (receipt.owner_id, uncertain)):
            with self.assertRaises(self.m.SnapshotError):
                self.executor.discard_unreferenced(self.spec, "capture-a", owner_id=owner, reference_guard=guard)
            self.assertEqual(self.executor.verify(self.spec, "capture-a"), receipt)
        self.executor.discard_unreferenced(self.spec, "capture-a", owner_id=receipt.owner_id, reference_guard=absent)
        self.assertEqual({p.name for p in (self.root / "capture-a").iterdir()}, {"owner.bin"})
        with self.assertRaises(self.m.SnapshotError): self.capture()

    def test_unbounded_object_read_is_refused_without_calling_read(self):
        from unittest.mock import patch
        calls = []
        class Unbounded:
            def read(inner, count):
                calls.append(count)
                return self.object_bytes
        @contextmanager
        def opener(ref): yield Unbounded()
        with patch.object(self.ports, "open_object", side_effect=opener):
            with self.assertRaises(self.m.SnapshotError) as caught: self.capture()
        self.assertEqual(caught.exception.code, "SNAPSHOT_BOUNDED_READ_REQUIRED")
        self.assertEqual(calls, [])

    def test_bounded_runtime_stream_receives_deadline_cancel_and_aborts_only_itself(self):
        from unittest.mock import patch
        import threading
        import time
        stopped, calls = [], []
        entered = [False]
        before = {thread.ident for thread in threading.enumerate()}
        module = self.m
        class Cooperative(module.BoundedSnapshotReader):
            def read_bounded(inner, maximum, *, deadline, cancelled):
                calls.append((maximum, deadline))
                entered[0] = True
                while not cancelled():
                    if time.monotonic() >= deadline: raise module.SnapshotError("SNAPSHOT_TIMEOUT")
                    time.sleep(.001)
                raise module.SnapshotError("SNAPSHOT_CANCELLED")
            def abort_owned(inner): stopped.append("runtime-owned")
        @contextmanager
        def opener(ref): yield Cooperative()
        with patch.object(self.ports, "open_runtime", side_effect=opener):
            with self.assertRaises(module.SnapshotError) as caught:
                self.executor.capture(self.spec, capture_id="bounded-cancel", cancelled=lambda: entered[0])
        self.assertEqual(caught.exception.code, "SNAPSHOT_CANCELLED")
        self.assertEqual(stopped, ["runtime-owned"])
        self.assertTrue(0 < calls[0][0] <= module.CHUNK)
        self.assertGreater(calls[0][1], time.monotonic())
        self.assertEqual({thread.ident for thread in threading.enumerate()}, before)
        self.assertFalse((self.root / "bounded-cancel" / "receipt.bin").exists())

    def test_b2_worker_cleanup_uncertain_preserves_artifact_and_single_execution_lock(self):
        from unittest.mock import patch
        original = self.ports.open_object
        @contextmanager
        def uncertain(ref):
            with original(ref) as stream: yield stream
            raise self.m.SnapshotError("SNAPSHOT_WORKER_CLEANUP_UNCERTAIN")
        with patch.object(self.ports, "open_object", side_effect=uncertain):
            with self.assertRaises(self.m.SnapshotError) as caught: self.capture()
        self.assertEqual(caught.exception.code, "SNAPSHOT_WORKER_CLEANUP_UNCERTAIN")
        self.assertTrue((self.root / "capture-a" / "data.bin").exists())
        self.assertTrue((self.root / "service-snapshot.lock").exists())
        self.assertFalse((self.root / "capture-a" / "receipt.bin").exists())
        preserved = (self.root / "capture-a" / "data.bin").read_bytes()
        with self.assertRaises(self.m.SnapshotError): self.capture()
        self.assertEqual((self.root / "capture-a" / "data.bin").read_bytes(), preserved)
        with self.assertRaises(self.m.SnapshotError): self.executor.verify(self.spec, "capture-a")

    def test_b2_concrete_runtime_frames_are_verified_by_executor_hash_and_size(self):
        import struct
        import time
        from unittest.mock import patch
        from daon_user_api.service_snapshot_streams import FixedRuntimeFile, FixedSnapshotStreams
        class ProcessDouble:
            def __init__(inner, data):
                inner.payload = bytearray(b"D" + struct.pack(">I", len(data)) + data + b"E\0\0\0\0")
                inner.started, inner.closed = 0, False
            def supported(inner): return True
            def start(inner, work): inner.started += 1; return 41001, 42
            def readable(inner, fd, timeout): return True
            def receive(inner, fd, count):
                value = bytes(inner.payload[:count]); del inner.payload[:count]; return value
            def poll(inner, pid): return not inner.payload, 0
            def terminate(inner, pid): inner.payload.clear()
            def kill(inner, pid): inner.payload.clear()
            def close(inner, fd): inner.closed = True
            def pause(inner, duration): pass
        for suffix, data in (("normal", self.runtime_bytes), ("corrupt", b"x" * len(self.runtime_bytes)),
                ("truncated", self.runtime_bytes[:-1])):
            process = ProcessDouble(data)
            factory = FixedSnapshotStreams(runtime_files=(FixedRuntimeFile(self.runtime[0], self.original / "runtime"),), _io=process)
            @contextmanager
            def concrete(ref):
                with factory.open_runtime(ref, deadline=time.monotonic() + 3., cancelled=lambda: False) as reader: yield reader
            with patch.object(self.ports, "open_runtime", side_effect=concrete):
                if suffix == "normal":
                    receipt = self.executor.capture(self.spec, capture_id="concrete-" + suffix, cancelled=lambda: False)
                    self.assertEqual(self.executor.verify(self.spec, "concrete-" + suffix), receipt)
                else:
                    with self.assertRaises(self.m.SnapshotError):
                        self.executor.capture(self.spec, capture_id="concrete-" + suffix, cancelled=lambda: False)
                    self.assertFalse((self.root / ("concrete-" + suffix) / "receipt.bin").exists())
                    self.assertFalse((self.root / "service-snapshot.lock").exists())
            self.assertEqual(process.started, 1)
            self.assertTrue(process.closed)

    @unittest.skipUnless(os.name == "posix" and __import__("sys").platform.startswith("linux"),
        "Linux fork/NOFOLLOW 실제 실행 전용: Windows 대역 PASS와 구분")
    def test_b2_linux_native_fixed_runtime_child_reaped_and_link_refused(self):
        import time
        from daon_user_api.service_snapshot_streams import FixedRuntimeFile, FixedSnapshotStreams
        ref = self.runtime[0]
        factory = FixedSnapshotStreams(runtime_files=(FixedRuntimeFile(ref, self.original / "runtime"),))
        for _ in range(2):
            with factory.open_runtime(ref, deadline=time.monotonic() + 3., cancelled=lambda: False) as reader:
                received = bytearray()
                while part := reader.read_bounded(4096, deadline=time.monotonic() + 2., cancelled=lambda: False):
                    received.extend(part)
                self.assertEqual(bytes(received), self.runtime_bytes)
                self.assertTrue(reader.exited)
                self.assertEqual(reader.status, 0)
            self.assertTrue(reader.closed)
        linked = self.original / "linked-runtime"
        os.link(self.original / "runtime", linked)
        try:
            with self.assertRaises(self.m.SnapshotError):
                with factory.open_runtime(ref, deadline=time.monotonic() + 2., cancelled=lambda: False) as reader:
                    reader.read_bounded(4096, deadline=time.monotonic() + 2., cancelled=lambda: False)
            self.assertTrue(reader.exited)
            self.assertTrue(reader.closed)
        finally: linked.unlink()


if __name__ == "__main__": unittest.main()
