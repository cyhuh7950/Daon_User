"""운영자 고정 참조의 단발 B2 조립. HTTP/서비스 제어/설정 생성 기능은 없다."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import struct
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from daon_user_api.recovery_capture import _PosixFiles
from daon_user_api.service_snapshot import (ServiceSnapshotSpec, SnapshotLimits, SnapshotRuntimeRef,
    ServiceSnapshotExecutor, SnapshotError, KIND, _json, _safe, _hash)
from daon_user_api.service_snapshot_postgres import FixedPgSource, PostgresSnapshotPort, _validate_ancestor_stat
from daon_user_api.service_snapshot_streams import FixedRuntimeFile, FixedSnapshotStreams


_ROLES = frozenset({"api", "web", "postgres", "object_storage", "asr"})
_ROOT = Path(__file__).resolve().parents[2]


def _exact(value, names):
    if type(value) is not dict or set(value) != set(names): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")


def _positive(value):
    if type(value) is not int or value <= 0: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    return value


def _path(value):
    if (type(value) is not str or not value or len(value) > 4096 or any(ord(c) < 32 for c in value)
            or not Path(value).is_absolute() or ".." in Path(value).parts): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    return Path(value)


def _revision(value):
    if type(value) is not str or re.fullmatch("[0-9a-f]{40}", value) is None: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    return value


def _container_ref(value):
    if type(value) is not str or len(value) > 255 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is None:
        raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    return value


def _mounts(value, *, observed=False):
    if type(value) is not list or len(value) > 32: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    result = []
    for row in value:
        if observed:
            if type(row) is not dict or type(row.get("RW")) is not bool: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            row = {"type": row.get("Type"), "name": row.get("Name"), "source": row.get("Source"),
                "destination": row.get("Destination"), "read_only": not row["RW"]}
        _exact(row, ("type", "name", "source", "destination", "read_only"))
        if row["type"] not in {"bind", "volume", "tmpfs"} or type(row["read_only"]) is not bool: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        if row["name"] is not None: _container_ref(row["name"])
        if row["type"] != "tmpfs" or row["source"] not in (None, ""): _path(row["source"])
        destination = row["destination"]
        if (type(destination) is not str or not 0 < len(destination) <= 4096 or "\\" in destination
                or any(ord(char) < 32 for char in destination) or destination.startswith("//")
                or not PurePosixPath(destination).is_absolute() or ".." in PurePosixPath(destination).parts):
            raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        result.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    if len(set(result)) != len(result): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    return tuple(sorted(result))


def _source_binding(value):
    _exact(value, ("containers", "database_fingerprint", "object_fingerprint"))
    _hash(value["database_fingerprint"]); _hash(value["object_fingerprint"])
    rows = value["containers"]
    if type(rows) is not list or not 1 <= len(rows) <= 64: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
    ids = set()
    for row in rows:
        _exact(row, ("container_id", "compose_project", "compose_service", "mounts"))
        if (type(row["container_id"]) is not str or re.fullmatch("[0-9a-f]{64}", row["container_id"]) is None
                or row["container_id"] in ids): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        ids.add(row["container_id"])
        for key in ("compose_project", "compose_service"):
            if row[key] is not None: _container_ref(row[key])
        _mounts(row["mounts"])


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        result[key] = value
    return result


@dataclass(frozen=True, repr=False)
class OperatorConfig:
    source: FixedPgSource
    spec: ServiceSnapshotSpec
    capture: dict
    object_storage: dict
    runtime_files: tuple
    images: tuple
    docker: dict


def parse_config(raw):
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= 1048576: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(SnapshotError("SNAPSHOT_CONFIG_INVALID")))
        _exact(value, ("schema_version", "source", "spec", "capture", "object_storage", "runtime_files", "image_archives", "docker"))
        if type(value["schema_version"]) is not int or value["schema_version"] != 1: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        src, spec = dict(value["source"]), dict(value["spec"])
        _exact(src, (f.name for f in fields(FixedPgSource)))
        _exact(spec, (f.name for f in fields(ServiceSnapshotSpec)))
        for name in ("executable", "service_file", "password_file"): src[name] = _path(src[name])
        _positive(src["client_major"])
        source = FixedPgSource(**src)
        for name in ("database_name", "service_name"): _safe(getattr(source, name))
        _exact(spec["limits"], (f.name for f in fields(SnapshotLimits)))
        spec["limits"] = SnapshotLimits(**spec["limits"])
        allowlist = spec["runtime_allowlist"]
        if type(allowlist) is not list or not 1 <= len(allowlist) <= 64: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        spec["runtime_allowlist"] = tuple(allowlist)
        _revision(spec["code_revision"])
        expected = ServiceSnapshotSpec(**spec)
        if (expected.source_instance_ref, expected.credential_ref, expected.client_ref) != (
                source.source_instance_ref, source.credential_ref, source.client_ref): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        capture, objects, docker = (dict(value[name]) for name in ("capture", "object_storage", "docker"))
        _exact(capture, ("root", "master_key_file"))
        _exact(objects, ("endpoint", "bucket", "region", "secure", "access_key_file", "secret_key_file"))
        _exact(docker, ("executable", "executable_sha256", "source_binding"))
        _source_binding(docker["source_binding"])
        for name in capture: capture[name] = _path(capture[name])
        for name in ("access_key_file", "secret_key_file"): objects[name] = _path(objects[name])
        docker["executable"] = _path(docker["executable"]); _hash(docker["executable_sha256"])
        if (type(objects["secure"]) is not bool or type(objects["endpoint"]) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*:[1-9][0-9]{0,4}", objects["endpoint"]) is None
                or int(objects["endpoint"].rsplit(":", 1)[1]) > 65535
                or re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", objects["bucket"]) is None): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        _safe(objects["region"])
        runtime, images = value["runtime_files"], value["image_archives"]
        for rows in (runtime, images):
            if type(rows) is not list or not 1 <= len(rows) <= 64: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        runtime = tuple(dict(row) for row in runtime)
        for row in runtime:
            _exact(row, ("ref_id", "path", "digest_sha256", "byte_size"))
            _safe(row["ref_id"]); _hash(row["digest_sha256"]); _positive(row["byte_size"]); row["path"] = _path(row["path"])
        images = tuple(dict(row) for row in images)
        roles, container_refs, image_ids = set(), set(), set()
        for row in images:
            _exact(row, ("ref_id", "image_digest", "code_revision", "image_role", "archive_id", "archive_root", "maximum_archive_bytes", "duration_seconds", "container_refs"))
            for name in ("ref_id", "archive_id"): _safe(row[name])
            if type(row["image_digest"]) is not str or not row["image_digest"].startswith("sha256:"): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
            _hash(row["image_digest"][7:]); row["archive_root"] = _path(row["archive_root"])
            _positive(row["maximum_archive_bytes"]); _positive(row["duration_seconds"])
            refs = row["container_refs"]
            if type(refs) is not list or not 1 <= len(refs) <= 16: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
            for ref in refs:
                _container_ref(ref)
                if ref in container_refs: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
                container_refs.add(ref)
            if row["image_digest"] in image_ids: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
            image_ids.add(row["image_digest"])
            role = row["image_role"]
            if role not in _ROLES or role in roles: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
            roles.add(role)
            if role in {"api", "web"}: _revision(row["code_revision"])
            elif row["code_revision"] is not None: raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
            if role == "api" and (row["code_revision"], row["image_digest"]) != (expected.code_revision, expected.image_digest):
                raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        refs = [row["ref_id"] for row in (*runtime, *images)]
        if roles != _ROLES or len(set(refs)) != len(refs) or set(refs) != set(expected.runtime_allowlist): raise SnapshotError("SNAPSHOT_CONFIG_INVALID")
        return OperatorConfig(source, expected, capture, objects, runtime, images, docker)
    except SnapshotError: raise
    except Exception: raise SnapshotError("SNAPSHOT_CONFIG_INVALID") from None


def validate_private_stat(info, uid):
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != uid or info.st_nlink != 1:
        raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")


class OwnedResources(ExitStack):
    """여기에 직접 등록한 소유 자원만 회수한다. 공유 pool/프로세스는 등록하지 않는다."""


class _NewCaptureCompletion:
    """신규 capture 반환 뒤 guard 종료 실패만 격리한다. 기존 확정본에는 쓰지 않는다."""
    def __init__(self, config, capture_id, key):
        self.config, self.capture_id, self.key = config, capture_id, key
        self.new = None

    def __enter__(self): return self

    def remember_new(self, executor, receipt): self.new = executor, receipt

    def __exit__(self, kind, error, traceback):
        if kind is None or self.new is None: return False
        try:
            executor, receipt = self.new
            spec = self.config.spec
            canonical_spec = _json(asdict(spec))
            expected_body = _json(asdict(receipt))
            if (receipt.kind != KIND or receipt.capture_id != self.capture_id
                    or receipt.spec_digest != hashlib.sha256(canonical_spec).hexdigest()
                    or _json(receipt.spec) != canonical_spec or receipt.image_digest != spec.image_digest
                    or len(expected_body) > spec.limits.metadata_bytes):
                raise SnapshotError()
            _hash(receipt.owner_id)
            receipt_size = len(expected_body) + 28
            verifier = ServiceSnapshotExecutor(executor.root, master_key=self.key, ports=None)
            if verifier._root_identity != executor._root_identity: raise SnapshotError()
            folder = executor.root / self.capture_id
            with verifier._pinned(), verifier._files.pin(folder):
                before = verifier._read(folder / "receipt.bin", receipt_size)
                if len(before) != receipt_size: raise SnapshotError()
                actual_body = verifier._open(spec, self.capture_id, "receipt", before, [KIND,self.capture_id,"receipt"], set())
                if actual_body != expected_body: raise SnapshotError()
                owner_blob = verifier._read(folder / "owner.bin", 60)
                if len(owner_blob) != 60: raise SnapshotError()
                owner = verifier._open(spec, self.capture_id, "owner", owner_blob, [KIND,"owner"], set())
                if len(owner) != 32 or owner.hex() != receipt.owner_id:
                    raise SnapshotError()
                if verifier._read(folder / "receipt.bin", receipt_size) != before: raise SnapshotError()
                if verifier._read(folder / "owner.bin", 60) != owner_blob: raise SnapshotError()
                verifier._files.rename(folder / "receipt.bin", folder / "receipt.uncertain")
        except BaseException:
            raise SnapshotError("SNAPSHOT_OPERATOR_CLEANUP_UNCERTAIN") from None
        # 소유 bytes/owner/인증 receipt 모두 보존한다. operator lock도 불확정 보존.
        raise SnapshotError("SNAPSHOT_OPERATOR_CLEANUP_UNCERTAIN") from None


class ProtectedInputs:
    def __init__(self):
        if os.name != "posix" or not sys.platform.startswith("linux"): raise SnapshotError("SNAPSHOT_PLATFORM_UNSUPPORTED")
        self.files = _PosixFiles()

    @contextmanager
    def pin(self, path):
        with ExitStack() as lifetime:
            for ancestor in reversed((path, *path.parents)):
                fd = lifetime.enter_context(self.files.pin(ancestor)); _validate_ancestor_stat(os.fstat(fd), os.geteuid())
            yield fd

    def read(self, path, maximum):
        try:
            with self.pin(path.parent), self.files.file(path) as source:
                validate_private_stat(os.fstat(source.fileno()), os.geteuid())
                raw = source.read(maximum + 1)
                if not raw or len(raw) > maximum: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
                return raw
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED") from None

    @contextmanager
    def root(self, path):
        with self.pin(path) as fd:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
            yield fd


def require_image_proofs(*, config, proofs):
    if type(proofs) is not dict or set(proofs) != {row["ref_id"] for row in config.images}:
        raise SnapshotError("SNAPSHOT_IMAGE_UNPROVEN")
    from daon_user_api.service_image_archive import LoadedImageProof
    if any(type(proof) is not LoadedImageProof for proof in proofs.values()): raise SnapshotError("SNAPSHOT_IMAGE_UNPROVEN")


def require_runtime_names(actual, expected):
    if actual != expected or any(name.endswith(("-wal", "-journal", "-shm")) for name in actual):
        raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")


def require_quick_check(rows):
    if rows != [("ok",)]: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")


class ExplicitContainerProbe:
    """승인된 ref만 조회한다. 전역 목록으로 역할/대상을 선택하지 않는다."""
    def __init__(self, cli): self.cli = cli

    def inspect_container(self, ref, *, deadline, cancelled):
        _container_ref(ref)
        projection = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
            '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
            '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
            '"running":{{json .State.Running}},"exit_code":{{json .State.ExitCode}},"pid":{{json .State.Pid}},'
            '"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},'
            '"paused":{{json .State.Paused}},"dead":{{json .State.Dead}},"restarting":{{json .State.Restarting}},'
            '"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"ports":{{json .NetworkSettings.Ports}}}')
        try:
            with self.cli._run(["container", "inspect", "--format", projection, ref], deadline=deadline, cancelled=cancelled) as command:
                return json.loads(command.collect(1048576).decode("utf-8"), object_pairs_hook=_pairs)
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE") from None


class PinnedContainers:
    """불변ID/관측 수명만 증명한다. 승인 source_binding 대조를 대신하지 않는다."""
    def __init__(self, config, probe, *, cancelled=lambda: False):
        self.config, self.probe, self.cancelled = config, probe, cancelled
        self.deadline = time.monotonic() + config.spec.limits.duration_seconds
        self._pins = None

    def _observe(self, ref, image):
        if self.cancelled() or time.monotonic() >= self.deadline: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
        try:
            row = self.probe.inspect_container(ref, deadline=self.deadline, cancelled=self.cancelled)
            _exact(row, ("id", "name", "image", "service", "project", "running", "exit_code", "pid", "started_at", "finished_at",
                "paused", "dead", "restarting", "mounts", "networks", "ports"))
            if (type(row["id"]) is not str or re.fullmatch("[0-9a-f]{64}", row["id"]) is None
                    or row["image"] != image or type(row["name"]) is not str or not row["name"].startswith("/")):
                raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            _container_ref(row["name"][1:])
            if ref != row["id"] and row["name"] != "/" + ref: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            approved = [item for item in self.config.docker["source_binding"]["containers"] if item["container_id"] == row["id"]]
            if (len(approved) != 1 or row["project"] != approved[0]["compose_project"] or row["service"] != approved[0]["compose_service"]
                    or _mounts(row["mounts"], observed=True) != _mounts(approved[0]["mounts"])):
                raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            for key in ("running", "paused", "dead", "restarting"):
                if type(row[key]) is not bool: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            if row["paused"] or row["dead"] or row["restarting"]: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            if (type(row["exit_code"]) is not int or type(row["pid"]) is not int or row["pid"] < 0
                    or type(row["mounts"]) is not list or len(row["mounts"]) > 64
                    or type(row["networks"]) is not dict or type(row["ports"]) is not dict):
                raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            for key in ("started_at", "finished_at"):
                if type(row[key]) is not str or not row[key] or len(row[key]) > 255: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            if row["running"]:
                if row["pid"] <= 0: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            elif image != self.config.spec.image_digest or row["pid"] != 0 or row["exit_code"] != 0:
                raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            # 문자열화는 자기기준 신뢰가 아니라 이후 변경 검출용 불변 관측 사본이다.
            mounts = sorted(row["mounts"], key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False))
            witness = json.dumps({**row, "mounts": mounts}, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(witness.encode("utf-8")) > 1048576: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            return row["id"], witness
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE") from None

    def pin(self):
        if self._pins is not None: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
        pins, seen = [], set()
        for image in self.config.images:
            for ref in image["container_refs"]:
                identity, witness = self._observe(ref, image["image_digest"])
                if identity in seen: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
                seen.add(identity); pins.append((identity, image["image_digest"], witness))
        if seen != {item["container_id"] for item in self.config.docker["source_binding"]["containers"]}:
            raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
        self._pins = tuple(pins)

    def recheck(self):
        if self._pins is None: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
        for identity, image, witness in self._pins:
            if self._observe(identity, image) != (identity, witness): raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")

    def runtime_target(self):
        if self._pins is None: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        api_ids = {identity for identity, image, _ in self._pins if image == self.config.spec.image_digest}
        matches = []
        for container in self.config.docker["source_binding"]["containers"]:
            if container["container_id"] not in api_ids: continue
            for mount in container["mounts"]:
                if mount["type"] not in {"bind", "volume"}: continue
                source = _path(mount["source"])
                if all(_inside(row["path"], source) for row in self.config.runtime_files):
                    matches.append((container["container_id"], {"Type": mount["type"], "Name": mount["name"],
                        "Source": mount["source"], "Destination": mount["destination"], "RW": not mount["read_only"]}))
        if len(matches) != 1: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        return matches[0]


@dataclass(frozen=True, repr=False)
class _PhysicalWork:
    config: OperatorConfig
    storage: object
    deadline: float


@dataclass(frozen=True, repr=False)
class _RuntimeWork:
    rows: tuple
    mount: dict
    deadline: float


def _physical_measure(work):
    from daon_user_api.recovery_destinations import PhysicalProbeInstance, PostgresMinioPhysicalProbe
    import psycopg
    # libpq의 미지정 변수 승계를 소유 child 안에서만 제거한다. parent 환경 변경0.
    for name in tuple(os.environ):
        if name.startswith("PG"): del os.environ[name]
    port = PostgresSnapshotPort(work.config.source)
    @contextmanager
    def connection():
        with port._protected_files(work.config.source) as binding:
            remaining = max(1, min(5, int(work.deadline - time.monotonic())))
            with psycopg.connect(**binding.options, connect_timeout=remaining, autocommit=True) as value:
                value.execute("SELECT pg_catalog.set_config('statement_timeout',%s,false)", (str(remaining*1000),))
                yield value
    instance = PhysicalProbeInstance("source-database", "source-bucket", work.config.source.database_name,
        connection, work.storage, work.config.object_storage["region"])
    probe = PostgresMinioPhysicalProbe({work.config.source.source_instance_ref: instance})
    database, _ = probe._database(instance)
    objects, _ = probe._object(instance)
    return {"database_fingerprint": database.fingerprint(), "object_fingerprint": objects.fingerprint()}


class _PhysicalIO:
    """기존 bounded frame/reap port를 재사용하는 고정 읽기 작업. 임의 callback/명령 입력0."""
    def __new__(cls):
        from daon_user_api.service_snapshot_streams import _ForkIO
        class IO(_ForkIO):
            @staticmethod
            def start(work):
                from daon_user_api.service_snapshot_streams import _close_inherited_fds, _send
                read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
                try: pid = os.fork()
                except BaseException:
                    os.close(read_fd); os.close(write_fd); raise
                if pid == 0:
                    code = 1
                    try:
                        _close_inherited_fds({write_fd}); os.set_blocking(write_fd, True)
                        if type(work) is _PhysicalWork: result = _physical_measure(work)
                        elif type(work) is _RuntimeWork: result = _runtime_measure(work)
                        else: raise SnapshotError()
                        raw = json.dumps(result, separators=(",", ":")).encode()
                        if len(raw) > 65531: raise SnapshotError()
                        _send(write_fd, b"D", raw); _send(write_fd, b"E"); code = 0
                    except BaseException:
                        try: _send(write_fd, b"X")
                        except BaseException: pass
                    finally:
                        try: os.close(write_fd)
                        finally: os._exit(code)
                os.close(write_fd)
                return pid, read_fd
        return IO()


class SourceIdentityProbe:
    def __init__(self, config, storage, *, _io=None):
        self.config, self.storage = config, storage
        self.io, self.uncertain = _io or _PhysicalIO(), False

    def recheck(self, *, deadline, cancelled):
        from daon_user_api.service_snapshot_streams import _OwnedReader
        if self.uncertain or not self.io.supported(): raise SnapshotError("SNAPSHOT_STREAM_PLATFORM_UNSUPPORTED")
        reader = _OwnedReader(_PhysicalWork(self.config, self.storage, deadline), self.io, time.monotonic, deadline, cancelled)
        try:
            raw = bytearray()
            while True:
                value = reader.read_bounded(min(1025-len(raw), 1024), deadline=deadline, cancelled=cancelled)
                if not value: break
                raw.extend(value)
                if len(raw) > 1024: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            actual = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_pairs)
            _exact(actual, ("database_fingerprint", "object_fingerprint"))
            expected = self.config.docker["source_binding"]
            if any(actual[key] != expected[key] for key in actual): raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE") from None
        finally:
            try: reader.abort_owned()
            except SnapshotError:
                self.uncertain = True; raise


def _inside(path, directory):
    try: path.relative_to(directory); return True
    except ValueError: return False


class _RuntimeOSProbe:
    """첫 지원은 host PID/proc 전역을 읽을 수 있는 Linux 운영자만이다."""
    def __init__(self): self.inputs = ProtectedInputs()

    def _namespace(self):
        if os.geteuid() != 0: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        init = Path("/proc/1/comm").read_text().strip()
        if init not in {"systemd", "init"}: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        if os.stat("/")[:2] != os.stat("/proc/1/root")[:2]: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        namespace = (os.readlink("/proc/self/ns/pid"), os.readlink("/proc/1/ns/pid"), os.readlink("/proc/self/ns/mnt"))
        if namespace[0] != namespace[1] or namespace[2] != os.readlink("/proc/1/ns/mnt"):
            raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        return namespace

    @staticmethod
    def _no_writers(directory, identities):
        if not identities: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        mapped = {(os.major(device), os.minor(device), inode) for device, inode in identities}
        for process in Path("/proc").iterdir():
            if not process.name.isdecimal(): continue
            try:
                for descriptor in (process / "fd").iterdir():
                    try:
                        target = os.readlink(descriptor)
                        actual = os.stat(descriptor)  # /proc fd를 따라 실물 dev/inode를 본다. bind 이름과 독립.
                        same_file = (actual.st_dev, actual.st_ino) in identities
                        same_directory = target.startswith("/") and _inside(Path(target.removesuffix(" (deleted)")), directory)
                        if not same_file and not same_directory: continue
                        info = (process / "fdinfo" / descriptor.name).read_text()
                        flags = [line.split(":", 1)[1].strip() for line in info.splitlines() if line.startswith("flags:")]
                        if len(flags) != 1 or int(flags[0], 8) & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}:
                            raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                    except FileNotFoundError:
                        if process.exists(): raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN") from None
                for line in (process / "maps").read_text().splitlines():
                    parts = line.split(maxsplit=5)
                    if len(parts) < 5 or re.fullmatch(r"[r-][w-][x-][ps]",parts[1]) is None:
                        raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                    if "w" not in parts[1] or "s" not in parts[1]: continue
                    if re.fullmatch(r"[0-9a-fA-F]+:[0-9a-fA-F]+",parts[3]) is None or not parts[4].isdecimal():
                        raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                    major,minor = (int(value,16) for value in parts[3].split(":"))
                    same_file = (major,minor,int(parts[4])) in mapped
                    same_directory = len(parts)==6 and parts[5].startswith("/") and _inside(Path(parts[5].removesuffix(" (deleted)")), directory)
                    if same_file or same_directory:
                        raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            except FileNotFoundError:
                if process.exists(): raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN") from None
            except SnapshotError: raise
            except Exception: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN") from None

    @staticmethod
    def _names(directory):
        names, visited = set(), 0
        for base, folders, files in os.walk(directory, followlinks=False):
            visited += 1
            if visited > 128: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            for name in (*folders, *files):
                info = (Path(base) / name).lstat()
                if stat.S_ISLNK(info.st_mode) or (not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                    raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            for name in files:
                names.add(str((Path(base) / name).relative_to(directory)))
                if len(names) > 64: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        return names

    @contextmanager
    def hold(self, rows, mount):
        directory = Path(mount["Source"])
        namespace = self._namespace()
        expected = {str(row["path"].relative_to(directory)) for row in rows}
        handles = []
        with ExitStack() as lifetime:
            for row in rows:
                lifetime.enter_context(self.inputs.pin(row["path"].parent))
                source = lifetime.enter_context(self.inputs.files.file(row["path"]))
                validate_private_stat(os.fstat(source.fileno()), os.geteuid())
                handles.append((row, source))
            def measure():
                try:
                    if self._namespace() != namespace: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                    identities = {(os.fstat(source.fileno()).st_dev, os.fstat(source.fileno()).st_ino) for _,source in handles}
                    self._no_writers(directory, identities)
                    require_runtime_names(self._names(directory), expected)
                    observed = []
                    for row, source in handles:
                        info = os.fstat(source.fileno()); current = os.stat(row["path"], follow_symlinks=False)
                        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                        if identity(info) != identity(current) or info.st_size != row["byte_size"]: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                        digest, total = hashlib.sha256(), 0
                        source.seek(0); header = source.read(16); source.seek(0)
                        while value := source.read(65536):
                            total += len(value)
                            if total > row["byte_size"]: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                            digest.update(value)
                        if digest.hexdigest() != row["digest_sha256"] or identity(info) != identity(os.fstat(source.fileno())):
                            raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                        if header == b"SQLite format 3\0":
                            # immutable + 무 sidecar + 정지/전역writer 부재 증명 안에서만 읽는다.
                            database = sqlite3.connect(f"file:/proc/self/fd/{source.fileno()}?mode=ro&immutable=1", uri=True)
                            try: require_quick_check(database.execute("PRAGMA quick_check").fetchmany(2))
                            finally: database.close()
                        elif row["path"].suffix.lower() in {".db", ".sqlite", ".sqlite3"}: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                        observed.append((row["ref_id"], *identity(info), digest.hexdigest()))
                    require_runtime_names(self._names(directory), expected)
                    self._no_writers(directory, identities)
                    return tuple(observed)
                except SnapshotError: raise
                except Exception: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN") from None
            yield measure


def _runtime_measure(work):
    # namespace/全proc/fd/file/SQLite 접근과 close는 모두 소유 child 안에서 끝난다.
    probe = _RuntimeOSProbe()
    with probe.hold(work.rows, work.mount) as measure:
        files = measure()
        return {"namespace": probe._namespace(), "files": files}


class BoundedRuntimeProbe:
    """고정 _RuntimeWork만 실행한다. parent는 익명 IPC/총deadline/cancel/reap만 소유한다."""
    def __init__(self, *, deadline, cancelled, _io=None, _clock=time.monotonic):
        self.deadline, self.cancelled, self.clock = deadline, cancelled, _clock
        self.io, self.uncertain = _io or _PhysicalIO(), False

    @contextmanager
    def hold(self, rows, mount):
        if self.uncertain or not self.io.supported(): raise SnapshotError("SNAPSHOT_STREAM_PLATFORM_UNSUPPORTED")
        if type(rows) is not tuple or not 1 <= len(rows) <= 64: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        work = _RuntimeWork(tuple(dict(row) for row in rows), dict(mount), self.deadline)
        def measure():
            from daon_user_api.service_snapshot_streams import _OwnedReader
            if self.uncertain: raise SnapshotError("SNAPSHOT_WORKER_CLEANUP_UNCERTAIN")
            reader = _OwnedReader(work, self.io, self.clock, self.deadline, self.cancelled)
            try:
                raw = bytearray()
                while True:
                    value = reader.read_bounded(min(65532-len(raw), 65531), deadline=self.deadline, cancelled=self.cancelled)
                    if not value: break
                    raw.extend(value)
                    if len(raw) > 65531: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                actual = json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_pairs)
                _exact(actual, ("namespace", "files"))
                namespace, files = actual["namespace"], actual["files"]
                if (type(namespace) is not list or len(namespace) != 3
                        or any(type(item) is not str or not 0 < len(item) <= 255 for item in namespace)
                        or type(files) is not list or len(files) != len(rows)):
                    raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                for file, expected in zip(files, rows, strict=True):
                    if (type(file) is not list or len(file) != 7 or file[0] != expected["ref_id"]
                            or any(type(value) is not int or value < 0 for value in file[1:6])
                            or file[2] <= 0 or file[3] != expected["byte_size"] or file[6] != expected["digest_sha256"]):
                        raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
                return tuple(namespace), tuple(tuple(file) for file in files)
            except SnapshotError: raise
            except Exception: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN") from None
            finally:
                try: reader.abort_owned()
                except SnapshotError:
                    self.uncertain = True; raise
        yield measure


class QuiescentRuntime:
    def __init__(self, config, cli, *, pinned=None, _probe=None, cancelled=lambda: False, deadline=None):
        self.config, self.cli, self.cancelled = config, cli, cancelled
        self.deadline = min(deadline if deadline is not None else float("inf"), time.monotonic() + config.spec.limits.duration_seconds)
        self.probe = _probe or BoundedRuntimeProbe(deadline=self.deadline, cancelled=cancelled)
        self.pins = pinned or PinnedContainers(config, ExplicitContainerProbe(cli), cancelled=cancelled)
        self._lifetime = ExitStack()

    def _observe(self):
        if self.cancelled() or time.monotonic() >= self.deadline: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        try:
            identity, expected_mount = self._target
            observed = self.pins._observe(identity, self.config.spec.image_digest)
            original = next((item for item in self.pins._pins if item[0] == identity), None)
            if original is None or observed != (original[0], original[2]): raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            api = json.loads(observed[1])
            begin = datetime.fromisoformat(api["started_at"].replace("Z", "+00:00"))
            finish = datetime.fromisoformat(api["finished_at"].replace("Z", "+00:00"))
            if (api["image"] != self.config.spec.image_digest or api["running"] is not False or type(api["exit_code"]) is not int
                    or api["exit_code"] != 0 or api["pid"] != 0 or finish.year < 2000 or finish <= begin
                    or finish > datetime.now(timezone.utc) or begin.utcoffset().total_seconds() != 0 or finish.utcoffset().total_seconds() != 0):
                raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            mounts = [row for row in api["mounts"] if _mounts([row], observed=True) == _mounts([expected_mount], observed=True)]
            if len(mounts) != 1: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            mount = mounts[0]; directory = _path(mount["Source"])
            if not all(_inside(row["path"], directory) for row in self.config.runtime_files): raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            # 전역 목록은 다른 활성 writer 유무만 본다. 대상은 위에서 승인 ID로 확정했다.
            for container in self.cli.containers(deadline=self.deadline, cancelled=self.cancelled):
                if container["id"] == api["id"] or container["running"] is not True: continue
                for item in container["mounts"]:
                    if item.get("RW") is not True: continue
                    source = _path(item["Source"])
                    if (_inside(source, directory) or _inside(directory, source)
                            or mount.get("Name") and item.get("Name") == mount["Name"]): raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            return (api["id"], api["image"], api["started_at"], api["finished_at"], dict(mount))
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN") from None

    def __enter__(self):
        try:
            if self.pins._pins is None: self.pins.pin()
            self._target = self.pins.runtime_target()
            if any(identity != self._target[0] and json.loads(witness)["running"] is not True for identity, _, witness in self.pins._pins):
                raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            self.before = self._observe()
            self.measure = self._lifetime.enter_context(self.probe.hold(self.config.runtime_files, self.before[-1]))
            self.evidence = self.measure()
            return self
        except BaseException:
            self._lifetime.close(); raise

    def recheck(self):
        if self._observe() != self.before or self.measure() != self.evidence: raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")

    def __exit__(self, kind, error, traceback):
        try:
            if kind is None: self.recheck()
        finally: self._lifetime.close()


def current_schema_revision():
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory(str(_ROOT / "services/api/migrations")).get_heads()
    if len(heads) != 1: raise SnapshotError("SNAPSHOT_SCHEMA_UNPROVEN")
    return _safe(heads[0])


class CheckedPostgresSnapshotPort(PostgresSnapshotPort):
    def __init__(self, *args, expected_schema, expected_database, **options):
        super().__init__(*args, **options)
        self.expected_schema, self.expected_database = expected_schema, expected_database

    def _connect_service(self, config):
        from daon_user_api.recovery_destinations import DatabaseIdentity
        from daon_user_api.service_snapshot_postgres import _TimedReadConnection
        connection = super()._connect_service(config)
        try:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                row = _TimedReadConnection(connection, self).execute("SELECT current_database(),c.system_identifier::text,d.oid::bigint "
                    "FROM pg_catalog.pg_control_system() c CROSS JOIN pg_catalog.pg_database d WHERE d.datname=current_database()").fetchone()
                if (row is None or len(row) != 3 or row[0] != config.database_name
                        or DatabaseIdentity(row[1], row[2]).fingerprint() != self.expected_database):
                    raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE")
            return connection
        except BaseException:
            try: connection.close()
            except BaseException: raise SnapshotError("SNAPSHOT_OPERATOR_CLEANUP_UNCERTAIN") from None
            raise

    @contextmanager
    def open_snapshot(self, spec, *, cancelled=lambda: False):
        with super().open_snapshot(spec, cancelled=cancelled) as lease:
            if lease.schema_revision != self.expected_schema: raise SnapshotError("SNAPSHOT_SCHEMA_UNPROVEN")
            yield lease


class CheckedSnapshotExecutor(ServiceSnapshotExecutor):
    def __init__(self, *args, final_check, **options):
        super().__init__(*args, **options)
        self.final_check = final_check

    def _verify_data(self, *args, **options):
        result = super()._verify_data(*args, **options)
        self.final_check()  # 큰 파일 재검증 뒤, 최종 인증 receipt 작성 전.
        return result


def _secret_text(inputs, path):
    try:
        value = inputs.read(path, 4096).decode("utf-8")
        if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value): raise SnapshotError()
        return value
    except Exception: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED") from None


def _object_storage(config, inputs):
    from minio import Minio
    from daon_user_api.object_queue import MinioObjectStorageAdapter
    objects = config.object_storage
    access, secret = (_secret_text(inputs, objects[key]) for key in ("access_key_file", "secret_key_file"))
    storage = MinioObjectStorageAdapter(endpoint=objects["endpoint"], bucket=objects["bucket"], access_key=access,
        secret_key=secret, secure=objects["secure"])
    storage._client._http.clear()  # 미사용 생성 pool만 닫고 명시 region인 전용 client로 교체.
    storage._client = Minio(objects["endpoint"], access_key=access, secret_key=secret,
        secure=objects["secure"], region=objects["region"])
    return storage


@contextmanager
def _operator_lock(inputs, root):
    lock, owner, uncertain = root / "operator-snapshot.lock", secrets.token_bytes(32), False
    try:
        with inputs.files.file(lock, create=True) as target: target.write(owner)
    except Exception: raise SnapshotError("SNAPSHOT_BUSY") from None
    try: yield
    except BaseException as error:
        uncertain = not isinstance(error, Exception) or isinstance(error, SnapshotError) and error.code.endswith("CLEANUP_UNCERTAIN")
        raise
    finally:
        if not uncertain:
            try:
                with inputs.files.file(lock) as source:
                    if source.read(33) != owner: raise SnapshotError("SNAPSHOT_OPERATOR_CLEANUP_UNCERTAIN")
                inputs.files.unlink(lock)
            except Exception: raise SnapshotError("SNAPSHOT_OPERATOR_CLEANUP_UNCERTAIN") from None


def _roots(config):
    roots = {config.capture["root"], *(row["archive_root"] for row in config.images)}
    read_paths = [row["path"] for row in config.runtime_files]
    read_paths += [config.source.executable, config.source.service_file, config.source.password_file,
        config.capture["master_key_file"], config.docker["executable"], config.object_storage["access_key_file"], config.object_storage["secret_key_file"]]
    read_paths += [Path(row["source"]) for container in config.docker["source_binding"]["containers"]
        for row in container["mounts"] if row["source"] not in (None, "")]
    if any(_inside(path, root) or _inside(root, path) for root in roots for path in read_paths):
        raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
    return roots


def capture_service(config, capture_id, *, inputs=None, cancelled=lambda: False):
    """기존 보호 설정만 소비한다. 실제 실행/기존 root/자격 준비는 별도 운영 승인이다."""
    from daon_user_api.service_image_archive import FixedDockerCLI, ImageArchiveSpec, ImageArchiveStore
    _safe(capture_id)
    inputs = inputs or ProtectedInputs()
    deadline = time.monotonic() + config.spec.limits.duration_seconds
    def check():
        if cancelled(): raise SnapshotError("SNAPSHOT_CANCELLED")
        if time.monotonic() >= deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")
    with OwnedResources() as owned:
        for root in sorted(_roots(config), key=str): owned.enter_context(inputs.root(root))
        owned.enter_context(_operator_lock(inputs, config.capture["root"]))
        # PG* 미지정 환경이 정상 exporter 연결을 바꾸지 못한다. 전역 환경 수정0.
        if any(name.startswith("PG") for name in os.environ): raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
        master = inputs.read(config.capture["master_key_file"], 4096)
        if len(master) < 32: raise SnapshotError("SNAPSHOT_KEY_UNAVAILABLE")
        storage = _object_storage(config, inputs)
        owned.callback(storage._client._http.clear)
        cli = FixedDockerCLI(config.docker["executable"], config.docker["executable_sha256"])
        pins = PinnedContainers(config, ExplicitContainerProbe(cli), cancelled=lambda: cancelled() or time.monotonic() >= deadline)
        pins.pin()
        completion = owned.enter_context(_NewCaptureCompletion(config, capture_id, master))
        runtime = owned.enter_context(QuiescentRuntime(config, cli, pinned=pins, deadline=deadline,
            cancelled=lambda: cancelled() or time.monotonic() >= deadline))
        if runtime.before[0] not in {identity for identity, image, _ in pins._pins if image == config.spec.image_digest}:
            raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        physical = SourceIdentityProbe(config, storage)
        physical.recheck(deadline=deadline, cancelled=cancelled)
        image_specs, stores, proofs, image_receipts = {}, {}, {}, {}
        for row in config.images:
            check(); pins.recheck(); runtime.recheck()
            image_spec = ImageArchiveSpec(config.spec.source_instance_ref, row["archive_id"], (row["image_role"],),
                row["image_digest"], row["code_revision"], config.spec.key_version, config.spec.key_reference,
                row["maximum_archive_bytes"], row["duration_seconds"])  # AAD 일부이므로 남은 시간으로 변경0.
            store = ImageArchiveStore(row["archive_root"], master_key=master)
            if store._files.exists(row["archive_root"] / row["archive_id"]):
                image_receipt = store.verify(image_spec, deadline=deadline, cancelled=cancelled)
            else: image_receipt = store.capture(image_spec, cli=cli, cancelled=lambda: cancelled() or time.monotonic() >= deadline)
            check(); pins.recheck(); runtime.recheck()
            image_specs[row["ref_id"]], stores[row["ref_id"]] = image_spec, store
            image_receipts[row["ref_id"]] = image_receipt
        for ref, store in stores.items():
            check()
            proofs[ref] = store.reconstruct(image_specs[ref], cli=cli, cancelled=lambda: cancelled() or time.monotonic() >= deadline)
        require_image_proofs(config=config, proofs=proofs)
        files = tuple(FixedRuntimeFile(SnapshotRuntimeRef(row["ref_id"], "bytes", row["digest_sha256"], row["byte_size"]), row["path"])
            for row in config.runtime_files)
        runtime_refs = tuple(item.ref for item in files) + tuple(proofs[ref].reference(ref) for ref in image_specs)
        streams = FixedSnapshotStreams.from_storage(storage, source_instance_ref=config.spec.source_instance_ref,
            region=config.object_storage["region"], runtime_files=files)
        def verifier(spec, ref):
            check()
            if spec != config.spec or ref.ref_id not in proofs: raise SnapshotError("SNAPSHOT_IMAGE_UNPROVEN")
            return proofs[ref.ref_id].verify_once(image_specs[ref.ref_id], ref)
        ports = CheckedPostgresSnapshotPort(config.source, expected_schema=current_schema_revision(),
            expected_database=config.docker["source_binding"]["database_fingerprint"], streams=streams,
            runtime_inventory=runtime_refs, runtime_verifier=verifier)
        def final_check():
            for ref, store in stores.items():
                check()
                if store.verify(image_specs[ref], deadline=deadline, cancelled=cancelled) != image_receipts[ref]:
                    raise SnapshotError("SNAPSHOT_IMAGE_CHANGED")
            check(); pins.recheck(); runtime.recheck()
            physical.recheck(deadline=deadline, cancelled=cancelled)
            check(); pins.recheck(); runtime.recheck()
        executor = CheckedSnapshotExecutor(config.capture["root"], master_key=master, ports=ports, final_check=final_check)
        if executor._files.exists(executor.root / capture_id):
            return executor.verify(config.spec, capture_id)  # 재생성/덮어쓰기0, 확정본만 검증.
        receipt = executor.capture(config.spec, capture_id=capture_id, cancelled=lambda: cancelled() or time.monotonic() >= deadline)
        completion.remember_new(executor, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description="보호 설정의 운영자 전용 단발 보존")
    parser.add_argument("config"); parser.add_argument("capture_id")
    args = parser.parse_args()
    try:
        _safe(args.capture_id)
        inputs = ProtectedInputs()
        config = parse_config(inputs.read(_path(args.config), 1048576))
        capture_service(config, args.capture_id, inputs=inputs)
        print("SERVICE_SNAPSHOT_VERIFIED")
        return 0
    except Exception:
        print("SERVICE_SNAPSHOT_UNAVAILABLE", file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
