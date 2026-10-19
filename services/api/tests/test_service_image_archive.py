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


def _archive(*, wrong_layer=False, extra=None, manifests=1, layer=None, tags=(), labels="revision"):
    layer = b"S44 synthetic layer bytes\x00" * 64 if layer is None else layer
    config = {"architecture": "amd64", "os": "linux", "config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()]}}
    if labels == "missing": del config["config"]["Labels"]
    elif labels != "revision": config["config"]["Labels"] = labels
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


# Moby container.Config 선언 순서. 제품 normalizer를 fixture 생성에 재사용하지 않는다.
_ZERO_CONFIG = {"Hostname": "", "Domainname": "", "User": "", "AttachStdin": False,
    "AttachStdout": False, "AttachStderr": False, "Tty": False, "OpenStdin": False,
    "StdinOnce": False, "Env": None, "Cmd": None, "Image": "", "Volumes": None,
    "WorkingDir": "", "Entrypoint": None, "Labels": None}


def _native_archive(*, mutate=None, config_mutate=None, graph_mutate=None):
    def go(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace(
            "&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace(
            "\u2028", "\\u2028").replace("\u2029", "\\u2029").encode()
    def blob(value):
        raw = go(value); name = "blobs/sha256/" + hashlib.sha256(raw).hexdigest()
        files[name] = raw
        return {"mediaType": "application/vnd.oci.image." + ("manifest.v1+json" if "layers" in value else "config.v1+json"),
            "digest": "sha256:" + name.rsplit("/", 1)[1], "size": len(raw)}
    files, layer_descriptors, diff_ids = {}, [], []
    for index in range(2):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as layer:
            item = tarfile.TarInfo("synthetic-" + str(index)); item.size = 3
            layer.addfile(item, io.BytesIO(b"abc"))
        raw = stream.getvalue(); digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        files["blobs/sha256/" + digest[7:]] = raw; diff_ids.append(digest)
        layer_descriptors.append({"mediaType": "application/vnd.oci.image.layer.v1.tar", "size": len(raw), "digest": digest})
    # raw OCI Config intentionally omits Go zero fields, like actual PG/Object.
    config = {"architecture": "amd64", "os": "linux", "created": "2026-10-19T00:00:00Z",
        "config": {"Env": ["SYNTHETIC=<값>&\u2028\u2029"], "Labels": {"z": "last", "org.opencontainers.image.revision": "a" * 40}},
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
        "history": [{"created_by": "synthetic one"}, {"empty_layer": True, "created_by": "metadata only"}, {"created_by": "synthetic two"}]}
    if config_mutate: config_mutate(config)
    config_descriptor = blob(config); image = config_descriptor["digest"]
    previous, chain = None, None
    for index, diff in enumerate(diff_ids):
        chain = diff if chain is None else "sha256:" + hashlib.sha256((chain + " " + diff).encode()).hexdigest()
        container = dict(_ZERO_CONFIG)
        body = {"created": "1970-01-01T00:00:00Z", "container_config": dict(_ZERO_CONFIG)}
        if index == 1:
            container["Env"] = config["config"]["Env"]
            labels = config["config"].get("Labels")
            container["Labels"] = dict(sorted(labels.items())) if type(labels) is dict else labels
            body = {"created": config["created"], "container_config": dict(_ZERO_CONFIG),
                "config": container, "architecture": "amd64", "os": "linux"}
        preimage = {**body, "layer_id": chain}
        if previous is not None: preimage["parent"] = "sha256:" + previous
        identity = hashlib.sha256(go(dict(sorted(preimage.items())))).hexdigest()
        value = {"id": identity, **({"parent": previous} if previous else {}), **body, "os": "linux"}
        blob(value); previous = identity
    image_manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": config_descriptor, "layers": layer_descriptors}
    if graph_mutate: graph_mutate(image_manifest)
    manifest_descriptor = blob(image_manifest)
    files["index.json"] = go({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": [manifest_descriptor]})
    files["oci-layout"] = go({"imageLayoutVersion": "1.0.0"})
    files["manifest.json"] = go([{"Config": "blobs/sha256/" + image[7:], "RepoTags": None,
        "Layers": ["blobs/sha256/" + digest[7:] for digest in diff_ids],
        "LayerSources": {entry["digest"]: entry for entry in layer_descriptors}}])
    if mutate: mutate(files, config, image_manifest)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for name in ("blobs/", "blobs/sha256/"):
            item = tarfile.TarInfo(name); item.type = tarfile.DIRTYPE; target.addfile(item)
        for name, data in files.items():
            item = tarfile.TarInfo(name); item.size = len(data); target.addfile(item, io.BytesIO(data))
    return output.getvalue(), image, config


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

    def test_image_archive_nullable_labels_do_not_relax_application_revision(self):
        for labels in (None, "missing", {}):
            with self.subTest(labels=labels):
                raw, image, config = _archive(labels=labels)
                spec = replace(self.spec, image_digest=image, roles=("postgres",), code_revision=None)
                identity, tags = self.m._validate_tar(io.BytesIO(raw), spec)
                self.assertIsNone(identity["code_revision"])
                self.assertEqual(tags, ())
                for role in ("api", "web"):
                    with self.assertRaises(SnapshotError):
                        self.m._validate_tar(io.BytesIO(raw), replace(spec, roles=(role,), code_revision="a" * 40))
        for labels in ([], "invalid", 1, False, {"invalid": 1}):
            with self.subTest(malformed=labels), self.assertRaises(SnapshotError):
                raw, image, config = _archive(labels=labels)
                self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image, roles=("postgres",), code_revision=None))

    def test_image_archive_fixed_cli_nullable_labels_projection(self):
        commands = []
        expected = ('{{json .Id}}\n{{json .Os}}\n{{json .Architecture}}\n'
            '{{with index .Config "Labels"}}{{json (index . "org.opencontainers.image.revision")}}{{else}}null{{end}}\n'
            '{{json .RootFS.Layers}}')
        @contextmanager
        def protected(): yield ("/proc/self/fd/99", (99,))
        class Command:
            def __init__(inner, child, **kwargs): pass
            def __enter__(inner): return inner
            def __exit__(inner, *args): pass
            def collect(inner, maximum):
                return b'"' + self.image.encode() + b'"\n"linux"\n"amd64"\nnull\n' + json.dumps(self.config["rootfs"]["diff_ids"]).encode()
        def spawn(argv, **kwargs): commands.append(argv); return object()
        cli = self.m.FixedDockerCLI(Path("D:/synthetic/docker") if os.name == "nt" else Path("/synthetic/docker"), "a" * 64,
            _protected=protected, _spawn=spawn, _command=Command)
        result = cli.inspect(self.image, deadline=time.monotonic()+10, cancelled=lambda: False)
        self.assertEqual(commands[0][-2], expected)
        self.assertIsNone(result["code_revision"])

    def test_native_descriptor_archive_capture_restart_and_independent_readback(self):
        raw, image, config = _native_archive()
        spec = replace(self.spec, image_digest=image)
        cli = _Docker(raw, image, config)
        receipt = self.store.capture(spec, cli=cli, cancelled=lambda: False)
        self.assertEqual(receipt.archive_bytes, len(raw))
        self.assertEqual(receipt.archive_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt.layer_digests, tuple(config["rootfs"]["diff_ids"]))
        # 원본 save 소비는 금지하고 실제 보존된 AEAD bytes만 다시 읽는다.
        cli.raw = b""; cli.save = lambda *a, **k: self.fail("source save reused")
        restarted = self.m.ImageArchiveStore(self.root, master_key=self.key)
        self.assertEqual(restarted.verify(spec), receipt)
        proof = restarted.reconstruct(spec, cli=cli, cancelled=lambda: False)
        self.assertTrue(proof.verify_once(spec, proof.reference("native-image")))
        self.assertEqual(cli.loads, 1)

    def test_native_descriptor_graph_mutations_are_rejected_without_fallback(self):
        def change(files, config, image, kind):
            def rewrite(name, value): files[name] = json.dumps(value, separators=(",", ":")).encode()
            index = json.loads(files["index.json"])
            image_name = "blobs/sha256/" + index["manifests"][0]["digest"][7:]
            manifest = json.loads(files["manifest.json"])
            layer_name = manifest[0]["Layers"][0]
            if kind == "layout": rewrite("oci-layout", {"imageLayoutVersion": "1.1.0"})
            elif kind == "multiimage": index["manifests"] *= 2; rewrite("index.json", index)
            elif kind == "platform": index["manifests"][0]["platform"] = {"os": "linux", "architecture": "amd64"}; rewrite("index.json", index)
            elif kind == "index-extra": index["annotations"] = {}; rewrite("index.json", index)
            elif kind == "manifest-size": index["manifests"][0]["size"] += 1; rewrite("index.json", index)
            elif kind == "manifest-hash": files[image_name] += b" "
            elif kind == "manifest-media": index["manifests"][0]["mediaType"] = "application/vnd.oci.image.index.v1+json"; rewrite("index.json", index)
            elif kind == "missing": del files[layer_name]
            elif kind == "layer-hash": files[layer_name] = b"corrupted layer"
            elif kind == "order": manifest[0]["Layers"].reverse(); rewrite("manifest.json", manifest)
            elif kind == "layersources": manifest[0]["LayerSources"].pop(next(iter(manifest[0]["LayerSources"]))); rewrite("manifest.json", manifest)
            elif kind == "source-media": next(iter(manifest[0]["LayerSources"].values()))["mediaType"] += "+gzip"; rewrite("manifest.json", manifest)
            elif kind == "source-size": next(iter(manifest[0]["LayerSources"].values()))["size"] = True; rewrite("manifest.json", manifest)
            elif kind == "source-extra": next(iter(manifest[0]["LayerSources"].values()))["urls"] = ["https://invalid.example/"]; rewrite("manifest.json", manifest)
            elif kind == "config": files[manifest[0]["Config"]] += b" "
            elif kind == "mixed": manifest[0].pop("LayerSources"); rewrite("manifest.json", manifest)
            elif kind == "unknown": files["unexpected.json"] = b"{}"
            elif kind == "repositories": files["repositories"] = b"{}"
        for kind in ("layout", "multiimage", "platform", "index-extra", "manifest-size", "manifest-hash",
                "manifest-media", "missing", "layer-hash", "order", "layersources", "source-media",
                "source-size", "source-extra", "config", "mixed", "unknown", "repositories"):
            with self.subTest(kind=kind), self.assertRaises(SnapshotError):
                raw, image, config = _native_archive(mutate=lambda *args: change(*args, kind))
                self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image))

    def test_native_compat_chain_and_authenticated_config_mutations_are_rejected(self):
        def change(files, config, image, kind):
            # compat blob 자체의 hash를 다시 맞춰도 내용 결속으로 거부한다.
            compat = [name for name, raw in files.items() if raw[:1] == b"{" and "id" in json.loads(raw)]
            name = compat[0] if kind not in {"final-config", "unknown-field"} else compat[-1]
            value = json.loads(files[name]); del files[name]
            if kind == "missing": return
            if kind == "id": value["id"] = "f" * 64
            elif kind == "parent": value["parent"] = "f" * 64
            elif kind == "cycle": value["parent"] = value["id"]
            elif kind == "created": value["created"] = "1970-01-02T00:00:00Z"
            elif kind == "zero-config": value["container_config"]["Tty"] = True
            elif kind == "final-config": value["config"]["WorkingDir"] = "/different"
            elif kind == "unknown-field": value["unknown"] = True
            elif kind == "duplicate-id": value["id"] = json.loads(files[compat[-1]])["id"]
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            files["blobs/sha256/" + hashlib.sha256(raw).hexdigest()] = raw
            if kind == "independent-extra": files[name] = b"{}"
        for kind in ("missing", "id", "parent", "cycle", "created", "zero-config", "final-config",
                "unknown-field", "duplicate-id", "independent-extra"):
            with self.subTest(kind=kind), self.assertRaises(SnapshotError):
                raw, image, config = _native_archive(mutate=lambda *args: change(*args, kind))
                self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image))

    def test_native_known_json_total_limit_and_parser_cancellation(self):
        def inflate(files, *unused):
            for index in range(5):
                raw = json.dumps({"payload": "x" * (900000 + index)}).encode()
                files["blobs/sha256/" + hashlib.sha256(raw).hexdigest()] = raw
        raw, image, config = _native_archive(mutate=inflate)
        with self.assertRaises(SnapshotError) as error:
            self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image, maximum_archive_bytes=6*1048576))
        self.assertEqual(error.exception.code, "SNAPSHOT_LIMIT_EXCEEDED")
        raw, image, config = _native_archive()
        calls = [0]
        def cancelled(): calls[0] += 1; return calls[0] > 8
        with self.assertRaises(SnapshotError) as error:
            self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image), cancelled=cancelled)
        self.assertEqual(error.exception.code, "SNAPSHOT_CANCELLED")

    def test_native_go_container_config_zero_omitempty_and_top_only_id_serialization(self):
        value = self.m._container_config({"Env": [], "Labels": {"z": "<>&\u2028\u2029", "a": "한글"},
            "ArgsEscaped": False, "ExposedPorts": {}, "StopTimeout": 0, "Healthcheck": {}})
        expected = {**_ZERO_CONFIG, "Env": [], "Labels": {"a": "한글", "z": "<>&\u2028\u2029"}}
        # pointer 0 / empty struct는 생략하지 않고 선언 순서에 둔다.
        expected = {**{key: part for key, part in expected.items() if key not in {"Image", "Volumes", "WorkingDir", "Entrypoint", "Labels"}},
            "Healthcheck": {}, "Image": "", "Volumes": None, "WorkingDir": "", "Entrypoint": None,
            "Labels": expected["Labels"], "StopTimeout": 0}
        self.assertEqual(list(value), list(expected))
        self.assertEqual(value, expected)
        raw = self.m._go_json({"z": {"b": "<>&\u2028\u2029", "a": "한글"}, "a": 1})
        self.assertEqual(raw, b'{"z":{"b":"\\u003c\\u003e\\u0026\\u2028\\u2029","a":"\xed\x95\x9c\xea\xb8\x80"},"a":1}')
        for malformed in ({"Unknown": 1}, {"Tty": 0}, {"Cmd": "sh"}, {"Labels": {"x": 1}},
                {"Volumes": {"/data": "bad"}}, {"StopTimeout": True}, {"Healthcheck": {"Timeout": False}}):
            with self.subTest(malformed=malformed), self.assertRaises(SnapshotError): self.m._container_config(malformed)

    def test_native_sparse_labels_and_rehashed_invalid_config_or_graph(self):
        for labels in (None, "missing"):
            def sparse(config):
                if labels == "missing": del config["config"]["Labels"]
                else: config["config"]["Labels"] = labels
            raw, image, config = _native_archive(config_mutate=sparse)
            with self.subTest(sparse=labels):
                identity, tags = self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image, roles=("postgres",), code_revision=None))
                self.assertIsNone(identity["code_revision"])
                self.assertEqual(len(identity["layers"]), 2)
                with self.assertRaises(SnapshotError): self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image))
        configurations = {
            "diffID": lambda c: c["rootfs"]["diff_ids"].__setitem__(0, "sha256:" + "f"*64),
            "rootfs-extra": lambda c: c["rootfs"].__setitem__("unexpected", True),
            "history-missing": lambda c: c.pop("history"),
            "history-count": lambda c: c["history"].pop(),
            "history-bool": lambda c: c["history"][1].__setitem__("empty_layer", 1),
            "history-unknown": lambda c: c["history"][0].__setitem__("unexpected", "x"),
            "unknown-config": lambda c: c["config"].__setitem__("UnknownConfigField", True),
            "unknown-top": lambda c: c.__setitem__("unexpected", True),
            "platform": lambda c: c.__setitem__("architecture", "unsupported"),
        }
        for kind, change in configurations.items():
            with self.subTest(config=kind), self.assertRaises(SnapshotError):
                raw, image, config = _native_archive(config_mutate=change)
                self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image))
        graphs = {
            "schema-bool": lambda g: g.__setitem__("schemaVersion", True),
            "image-media": lambda g: g.__setitem__("mediaType", "application/vnd.oci.image.index.v1+json"),
            "config-size": lambda g: g["config"].__setitem__("size", g["config"]["size"]+1),
            "config-media": lambda g: g["config"].__setitem__("mediaType", "application/vnd.oci.image.layer.v1.tar"),
            "layer-size": lambda g: g["layers"][0].__setitem__("size", g["layers"][0]["size"]+1),
            "layer-gzip": lambda g: g["layers"][0].__setitem__("mediaType", "application/vnd.oci.image.layer.v1.tar+gzip"),
            "layer-digest": lambda g: g["layers"][0].__setitem__("digest", "sha256:"+"f"*64),
            "layer-order": lambda g: g["layers"].reverse(),
            "layer-duplicate": lambda g: g["layers"].__setitem__(1, g["layers"][0]),
            "remote-url": lambda g: g["layers"][0].__setitem__("urls", ["https://invalid.example/"]),
        }
        for kind, change in graphs.items():
            with self.subTest(graph=kind), self.assertRaises(SnapshotError):
                raw, image, config = _native_archive(graph_mutate=change)
                self.m._validate_tar(io.BytesIO(raw), replace(self.spec, image_digest=image))

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

    def test_image_archive_verify_honors_callers_deadline_and_cancel(self):
        self.capture()
        with self.assertRaises(SnapshotError): self.store.verify(self.spec, deadline=time.monotonic()-1)
        with self.assertRaises(SnapshotError): self.store.verify(self.spec, cancelled=lambda: True)
        self.assertEqual(self.store.verify(self.spec, deadline=time.monotonic()+2).archive_bytes, len(self.raw))

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
