"""작은 Docker-save 합성 tar/CLI 대역 + 실제 AEAD/파일. 실제 Docker 실행 증거 아님."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import tarfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace

from daon_user_api.service_snapshot import BoundedSnapshotReader, SnapshotError

ROOT = Path(os.environ.get("DAON_SERVICE_SNAPSHOT_TEST_ROOT", "D:/tmp/daon-user-s44-d1-eoul2-20261019"))


def _archive(*, wrong_layer=False, extra=None, manifests=1, layer=None, tags=()):
    layer = b"S44 synthetic layer bytes\x00" * 64 if layer is None else layer
    config = {"architecture": "amd64", "os": "linux", "config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]}}
    raw = json.dumps(config, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {"Config": digest + ".json", "RepoTags": list(tags), "Layers": ["layer/layer.tar"]}
    members = [(digest + ".json", raw), ("layer/layer.tar", b"corrupt" if wrong_layer else layer),
        ("manifest.json", json.dumps([manifest] * manifests).encode())]
    if extra is not None: members.append(extra)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for entry in members:
            name, data, *kind = entry
            info = tarfile.TarInfo(name); info.size = len(data)
            if kind: info.type = kind[0]; info.linkname = "linked-target"
            target.addfile(info, io.BytesIO(data))
    return output.getvalue(), "sha256:" + digest, config


class _Reader(BoundedSnapshotReader):
    def __init__(self, data, event, *, tick=None): self.source, self.events, self.tick = io.BytesIO(data), event, tick
    def read_bounded(self, maximum, *, deadline, cancelled):
        if self.tick: self.tick()
        if cancelled(): raise SnapshotError("SNAPSHOT_CANCELLED")
        if time.monotonic() >= deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")
        return self.source.read(min(maximum, 113))
    def abort_owned(self): self.events.append("owned-reader-close"); self.source.close()


class _Docker:
    def __init__(self, raw, image, config):
        self.raw, self.image, self.config = raw, image, config
        self.events, self.loads, self.inspects = [], 0, 0
        self.tags = {"unrelated:fixed": "sha256:" + "f" * 64}
        self.change = None
    def inspect(self, image, *, deadline, cancelled):
        self.inspects += 1
        if self.change: return self.change(self.inspects)
        return {"id": self.image, "os": "linux", "architecture": "amd64", "code_revision": "a" * 40,
            "layers": list(self.config["rootfs"]["diff_ids"])}
    def tag_inventory(self, *, deadline, cancelled): return dict(self.tags)
    @contextmanager
    def save(self, image, *, deadline, cancelled):
        self.events.append("save"); reader = _Reader(self.raw, self.events)
        try: yield reader
        finally: reader.abort_owned()
    def load(self, source, *, deadline, cancelled):
        self.loads += 1
        while source.read(65536): pass
        self.events.append("load-finished")


class ServiceImageArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        allowed = {"daon-user-s44-d1-eoul2-20261019", "daon-user-s44-d1-main-20261019"}
        if (not ROOT.is_absolute() or ROOT.name not in allowed or (os.name == "nt" and ROOT.parent != Path("D:/tmp"))
                or ROOT.exists() or ROOT.is_symlink()): raise AssertionError("D1_EXACT_NEW_TEST_ROOT_REQUIRED")
        for parent in (ROOT.parent, *ROOT.parent.parents):
            if parent.is_symlink() or getattr(parent.lstat(), "st_file_attributes", 0) & 0x400: raise AssertionError("D1_TEST_ROOT_LINK_FORBIDDEN")
        ROOT.mkdir(mode=0o700)

    @classmethod
    def tearDownClass(cls):
        if ROOT.resolve() != ROOT or ROOT.is_symlink(): raise AssertionError("D1_TEST_CLEANUP_BOUNDARY_INVALID")
        shutil.rmtree(ROOT)
        if ROOT.exists(): raise AssertionError("D1_TEST_ROOT_REMAINS")

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("daon_user_api.service_image_archive"), "B2-I 독립 image 보존 producer 미구현")
        from daon_user_api import service_image_archive as m
        self.m = m; self.root = ROOT / secrets.token_hex(8); self.root.mkdir(mode=0o700); self.key = secrets.token_bytes(32)
        self.raw, self.image, self.config = _archive()
        self.spec = m.ImageArchiveSpec("source-a", "archive-a", ("api",), self.image, "a" * 40, "v1", "existing-ref", 1048576, 30)
        self.cli = _Docker(self.raw, self.image, self.config)
        self.store = m.ImageArchiveStore(self.root, master_key=self.key)

    def capture(self): return self.store.capture(self.spec, cli=self.cli, cancelled=lambda: False)

    def test_image_archive_missing_or_tampered_chunk_has_no_receipt(self):
        receipt = self.capture()
        self.assertEqual(receipt.archive_bytes, len(self.raw))
        self.assertEqual(receipt.archive_sha256, hashlib.sha256(self.raw).hexdigest())
        data = self.root / "archive-a" / "data.bin"; original = data.read_bytes()
        for mutated in (original[:-1], original[:40] + bytes([original[40] ^ 1]) + original[41:]):
            data.write_bytes(mutated)
            with self.assertRaises(SnapshotError): self.store.verify(self.spec)
        data.write_bytes(original)
        for spec in (replace(self.spec, key_version="v2"), replace(self.spec, source_instance_ref="other"), replace(self.spec, roles=("web",))):
            with self.assertRaises(SnapshotError): self.store.verify(spec)
        with self.assertRaises(SnapshotError): self.m.ImageArchiveStore(self.root, master_key=secrets.token_bytes(32)).verify(self.spec)
        (self.root / "archive-a" / "receipt.bin").unlink()
        with self.assertRaises(SnapshotError): self.store.verify(self.spec)

    def test_image_archive_wrong_image_code_or_layer_rejected(self):
        for kind in ("image", "code", "layer", "manifest", "duplicate", "escape"):
            with self.subTest(kind=kind):
                raw, image, config = _archive(wrong_layer=kind == "layer", manifests=2 if kind == "manifest" else 1,
                    extra=("../escape", b"x") if kind == "escape" else ("manifest.json", b"[]") if kind == "duplicate" else None)
                folder = self.root / kind; folder.mkdir(mode=0o700); cli = _Docker(raw, image, config)
                spec = replace(self.spec, image_digest="sha256:" + "c" * 64) if kind == "image" else replace(self.spec, code_revision="b" * 40) if kind == "code" else self.spec
                with self.assertRaises(SnapshotError): self.m.ImageArchiveStore(folder, master_key=self.key).capture(spec, cli=cli, cancelled=lambda: False)
                self.assertFalse((folder / "archive-a" / "receipt.bin").exists())

    def test_image_archive_reference_without_bytes_or_real_load_is_unproven(self):
        with self.assertRaises(SnapshotError): self.store.reconstruct(self.spec, cli=self.cli, cancelled=lambda: False)
        self.capture()
        self.cli.load = lambda *args, **kwargs: True  # 캐시만 보고 입력을 소비하지 않는 fake proof 거부.
        with self.assertRaises(SnapshotError): self.store.reconstruct(self.spec, cli=self.cli, cancelled=lambda: False)
        self.cli.load = _Docker.load.__get__(self.cli)
        proof = self.store.reconstruct(self.spec, cli=self.cli, cancelled=lambda: False)
        self.assertEqual(self.cli.loads, 1)
        ref = proof.reference("image-api")
        self.assertEqual((ref.mode, ref.byte_size), ("reconstructible", 0))
        self.assertTrue(proof.verify_once(self.spec, ref))
        with self.assertRaises(SnapshotError): proof.verify_once(self.spec, ref)

    def test_image_archive_changed_during_observation_is_rejected(self):
        original = self.cli.inspect(self.image, deadline=time.monotonic() + 30, cancelled=lambda: False)
        self.cli.inspects = 0
        self.cli.change = lambda count: original if count == 1 else {**original, "id": "sha256:" + "f" * 64}
        with self.assertRaises(SnapshotError): self.capture()
        self.assertFalse((self.root / "archive-a" / "receipt.bin").exists())

    def test_image_archive_deadline_cancel_reaps_only_owned_child(self):
        with self.assertRaises(SnapshotError): self.store.capture(self.spec, cli=self.cli, cancelled=lambda: True)
        self.assertNotIn("save", self.cli.events)
        @contextmanager
        def blocked(*args, **kwargs):
            reader = _Reader(self.raw, self.cli.events, tick=lambda: (_ for _ in ()).throw(SnapshotError("SNAPSHOT_TIMEOUT")))
            try: yield reader
            finally: reader.abort_owned()
        self.cli.save = blocked
        with self.assertRaises(SnapshotError): self.capture()
        self.assertIn("owned-reader-close", self.cli.events)
        self.assertFalse((self.root / "archive-a" / "receipt.bin").exists())

    def test_image_archive_never_writes_plaintext_or_changes_unrelated_image(self):
        self.capture(); self.store.reconstruct(self.spec, cli=self.cli, cancelled=lambda: False)
        self.assertEqual(self.cli.tags, {"unrelated:fixed": "sha256:" + "f" * 64})
        names = []
        for file in self.root.rglob("*"):
            if file.is_file():
                names.append(file.name)
                self.assertNotIn(b"S44 synthetic layer bytes", file.read_bytes())
        self.assertEqual(set(names), {"owner.bin", "data.bin", "receipt.bin"})

    def test_image_archive_missing_tag_restores_only_approved_tag_and_conflict_refuses_before_load(self):
        raw, image, config = _archive(tags=("approved:tag",))
        cli = _Docker(raw, image, config); cli.tags["approved:tag"] = image
        self.store.capture(self.spec, cli=cli, cancelled=lambda: False)
        del cli.tags["approved:tag"]  # daemon/cache가 소실된 뒤 독립 bytes로 복원.
        def load(source, **options):
            _Docker.load(cli, source, **options)
            cli.tags["approved:tag"] = image
        cli.load = load
        proof = self.store.reconstruct(self.spec, cli=cli, cancelled=lambda: False)
        self.assertTrue(proof.verify_once(self.spec, proof.reference("image-api")))
        self.assertEqual(cli.tags, {"unrelated:fixed": "sha256:" + "f" * 64, "approved:tag": image})
        self.assertEqual(cli.loads, 1)
        cli.tags["approved:tag"] = "sha256:" + "c" * 64
        with self.assertRaises(SnapshotError): self.store.reconstruct(self.spec, cli=cli, cancelled=lambda: False)
        self.assertEqual(cli.loads, 1, "다른 image의 태그 점유는 load 전에 거부")
        del cli.tags["approved:tag"]
        def changed_load(source, **options):
            load(source, **options); cli.tags["unrelated:fixed"] = image
        cli.load = changed_load
        with self.assertRaises(SnapshotError): self.store.reconstruct(self.spec, cli=cli, cancelled=lambda: False)

    def test_image_archive_concrete_command_timeout_cancel_and_cleanup_uncertain(self):
        from types import SimpleNamespace
        events = []
        class Child:
            def __init__(inner): inner.exited = False; inner.stdin = inner.stdout = inner.stderr = SimpleNamespace(close=lambda: events.append("pipe-close"))
            def poll(inner): return 0 if inner.exited else None
            def terminate(inner): events.append("owned-term")
            def kill(inner): events.append("owned-kill")
            def wait(inner, timeout):
                events.append("owned-wait")
                if not inner.exited: raise __import__("subprocess").TimeoutExpired("fixed", timeout)
                return 0
        class IO:
            def prepare(inner, child): pass
            def ready(inner, child, channels, write, timeout): return []
            def read(inner, child, channel, count): raise AssertionError("stalled pipe read0")
        child = Child()
        with self.assertRaises(SnapshotError) as failed:
            with self.m.OwnedDockerCommand(child, deadline=time.monotonic() - 1, cancelled=lambda: False, _io=IO()) as command:
                command.read_bounded(1, deadline=time.monotonic() - 1, cancelled=lambda: False)
        self.assertEqual(failed.exception.code, "SNAPSHOT_IMAGE_CHILD_CLEANUP_UNCERTAIN")
        self.assertEqual(events[:4], ["owned-term", "owned-wait", "owned-kill", "owned-wait"])
        self.assertEqual(events.count("pipe-close"), 3)
        events.clear(); child = Child(); child.exited = True
        with self.assertRaises(SnapshotError) as cancelled:
            with self.m.OwnedDockerCommand(child, deadline=time.monotonic() + 10, cancelled=lambda: True, _io=IO()) as command:
                command.read_bounded(1, deadline=time.monotonic() + 10, cancelled=lambda: False)
        self.assertEqual(cancelled.exception.code, "SNAPSHOT_CANCELLED")
        self.assertNotIn("owned-term", events)

    def test_image_archive_concrete_fixed_cli_uses_owned_pipes_and_no_secret_environment(self):
        commands = []
        @contextmanager
        def protected(): yield ("/proc/self/fd/99", (99,))
        class Command:
            def __init__(inner, child, **kwargs): pass
            def __enter__(inner): return inner
            def __exit__(inner, *args): pass
            def collect(inner, maximum): return b'"' + self.image.encode() + b'"\n"linux"\n"amd64"\n"' + b'a' * 40 + b'"\n' + json.dumps(self.config["rootfs"]["diff_ids"]).encode()
        def spawn(argv, **kwargs): commands.append((argv, kwargs)); return object()
        cli = self.m.FixedDockerCLI(Path("D:/synthetic/docker") if os.name == "nt" else Path("/synthetic/docker"), "a" * 64,
            _protected=protected, _spawn=spawn, _command=Command)
        result = cli.inspect(self.image, deadline=time.monotonic() + 10, cancelled=lambda: False)
        self.assertEqual(result["id"], self.image)
        argv, options = commands[0]
        self.assertEqual(argv[:4], ["/proc/self/fd/99", "--host=unix:///var/run/docker.sock", "image", "inspect"])
        self.assertEqual(argv[-1], self.image)
        self.assertFalse(options["shell"])
        self.assertEqual(options["pass_fds"], (99,))
        self.assertEqual(options["env"], {})
        self.assertEqual(options["stderr"], __import__("subprocess").PIPE)

    def test_image_archive_sequence_nonce_and_chunk_cap_are_authenticated(self):
        import struct
        raw, image, config = _archive(layer=b"synthetic-chunk" * 330000)
        spec = replace(self.spec, image_digest=image, maximum_archive_bytes=8 * 1024 * 1024)
        receipt = self.store.capture(spec, cli=_Docker(raw, image, config), cancelled=lambda: False)
        self.assertEqual(receipt.chunks, 2)
        data = self.root / "archive-a/data.bin"; original = data.read_bytes(); offset = len(self.m.MAGIC)
        first_size = struct.unpack(">I", original[offset:offset + 4])[0]
        self.assertEqual(first_size, 4 * 1024 * 1024 + 28)
        first = original[offset:offset + 4 + first_size]; second = original[offset + 4 + first_size:]
        for changed in (original[:offset] + second + first,
                original[:offset] + first + second[:4] + first[4:16] + second[16:]):
            data.write_bytes(changed)
            with self.assertRaises(SnapshotError): self.store.verify(spec)

    def test_image_archive_rejects_unsafe_member_types_compression_and_limits(self):
        import gzip
        for extra in (("/absolute", b""), ("bad\\name", b""), ("link", b"", tarfile.SYMTYPE),
                ("hardlink", b"", tarfile.LNKTYPE), ("device", b"", tarfile.CHRTYPE)):
            raw, image, config = _archive(extra=extra)
            with self.subTest(member=extra[0]), self.assertRaises(SnapshotError):
                self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image))
        with self.assertRaises(SnapshotError): self.m._validate_tar(io.BytesIO(gzip.compress(self.raw)), self.spec)
        with self.assertRaises(SnapshotError): self.store.capture(replace(self.spec, maximum_archive_bytes=512), cli=self.cli, cancelled=lambda: False)

    def test_image_archive_concrete_load_pumps_bounded_stdin_and_drains_stderr(self):
        from types import SimpleNamespace
        writes, reads, closed = [], [], []
        class Pipe:
            def __init__(inner, name): inner.name = name
            def close(inner): closed.append(inner.name)
        child = SimpleNamespace(stdin=Pipe("stdin"), stdout=Pipe("stdout"), stderr=Pipe("stderr"))
        child.poll = lambda: 0 if "stdin" in closed else None
        child.terminate = lambda: self.fail("정상 완료 child terminate0")
        class IO:
            def __init__(inner): inner.out = io.BytesIO(b"loaded\n"); inner.err = io.BytesIO(b"PRIVATE_STDERR_DO_NOT_REFLECT" * 1000)
            def prepare(inner, child): pass
            def ready(inner, child, channels, write, timeout): return [*channels, *(["stdin"] if write else [])]
            def read(inner, child, channel, count): reads.append((channel, count)); return (inner.out if channel == "stdout" else inner.err).read(count)
            def write(inner, child, value): writes.append(bytes(value[:17])); return len(writes[-1])
        payload = b"s44 synthetic archive input" * 4096
        with self.m.OwnedDockerCommand(child, deadline=time.monotonic() + 30, cancelled=lambda: False, _io=IO()) as command:
            command.send(io.BytesIO(payload))
        self.assertEqual(b"".join(writes), payload)
        self.assertTrue(all(count <= (4096 if channel == "stderr" else 65536) for channel, count in reads))
        self.assertEqual(closed, ["stdin", "stdout", "stderr"])

    def test_image_archive_private_root_and_file_modes_are_enforced(self):
        from types import SimpleNamespace
        for root, mode, uid, links in ((True, 0o40755, 7, 1), (True, 0o40700, 8, 1),
                (False, 0o100644, 7, 1), (False, 0o100600, 8, 1), (False, 0o100600, 7, 2), (False, 0o120600, 7, 1)):
            with self.subTest(root=root, mode=mode), self.assertRaises(SnapshotError):
                self.m._private_stat(SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links), 7, root=root)

    def test_image_archive_pipe_prepare_failure_closes_the_owned_child(self):
        from types import SimpleNamespace
        closed = []
        child = SimpleNamespace(stdin=SimpleNamespace(close=lambda: closed.append("stdin")),
            stdout=SimpleNamespace(close=lambda: closed.append("stdout")), stderr=SimpleNamespace(close=lambda: closed.append("stderr")), poll=lambda: 0)
        class IO:
            def prepare(inner, child): raise ValueError("PRIVATE_SETUP_ERROR_DO_NOT_REFLECT")
        with self.assertRaises(SnapshotError) as failed:
            self.m.OwnedDockerCommand(child, deadline=time.monotonic() + 10, cancelled=lambda: False, _io=IO())
        self.assertNotIn("PRIVATE", str(failed.exception))
        self.assertEqual(closed, ["stdin", "stdout", "stderr"])


if __name__ == "__main__": unittest.main()
