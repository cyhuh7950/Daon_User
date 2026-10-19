"""D1-A 고정 PG/client 읽기 포트. app runtime에는 연결하지 않는다.

server 운영자가 결속한 설정만 받는다. 실제 보호 자격/바이너리/OS 보장이 없으면 거부한다.
직접시험의 connect/spawn/protection 대역은 정식 fallback이 아니다.
"""
from __future__ import annotations

import configparser
import hashlib
import os
import re
import stat
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import psycopg

from .cloud_storage import CloudAccessContext
from .object_queue import ObjectKeyPolicy
from .recovery_capture import _PosixFiles
from .service_snapshot import BoundedSnapshotReader, SnapshotError, SnapshotLease, SnapshotObjectRef, _safe, _hash, _json


def _validate_ancestor_stat(info, uid):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, uid} or info.st_mode & 0o022:
        raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")


@dataclass(frozen=True, repr=False)
class _ProtectedBinding:
    executable: str
    service_file: str
    password_file: str
    pass_fds: tuple
    options: dict


@dataclass(frozen=True, repr=False)
class FixedPgSource:
    source_instance_ref: str
    credential_ref: str
    client_ref: str
    database_name: str
    executable: Path
    executable_sha256: str
    client_major: int
    service_name: str
    service_file: Path
    password_file: Path

    def __post_init__(self):
        for field in ("source_instance_ref", "credential_ref", "client_ref", "database_name", "service_name"):
            _safe(getattr(self, field))
        _hash(self.executable_sha256)
        if type(self.client_major) is not int or self.client_major < 10: raise SnapshotError()
        for path in (self.executable, self.service_file, self.password_file):
            if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts: raise SnapshotError()


class _ArchiveStream(BoundedSnapshotReader):
    def __init__(self, source, port): self._source, self._port = source, port
    def read(self, size):
        if type(size) is not int or not 0 < size <= 4 * 1024 * 1024: raise SnapshotError()
        self._port._check()
        value = self._source.read(size)
        self._port._check()
        return value
    def read_bounded(self, maximum, *, deadline, cancelled):
        # 이 port가 소유한 child의 watchdog가 실제 blocked pipe를 종료한다.
        self._port._read_deadline = min(deadline, self._port._deadline)
        self._port._read_cancelled = cancelled
        def check_bound():
            if time.monotonic() >= self._port._read_deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")
            if cancelled(): raise SnapshotError("SNAPSHOT_CANCELLED")
        check_bound()
        try: return self.read(maximum)
        finally: check_bound()
    def abort_owned(self): self._port.cancel_owned()


class _TimedReadConnection:
    """각 읽기 statement에 남은 시간만 부여한다. 지속 DB 설정은 변경하지 않는다."""
    def __init__(self, connection, port): self._connection, self._port = connection, port
    def execute(self, sql, params=()):
        self._port._source_check()
        milliseconds = max(1, int((self._port._deadline - time.monotonic()) * 1000))
        self._connection.execute("SELECT pg_catalog.set_config('statement_timeout',%s,true)", (str(milliseconds),))
        result = self._connection.execute(sql, params)
        self._port._source_check()
        return result


class PostgresSnapshotPort:
    def __init__(self, config, *, connect=None, spawn=subprocess.Popen, validate_protected=None,
                 object_opener=None, runtime_inventory=(), runtime_opener=None, runtime_verifier=None, streams=None):
        if not isinstance(config, FixedPgSource): raise SnapshotError()
        self.config = config
        self._connect = connect or self._connect_service
        self._spawn = spawn
        self._validate_protected = validate_protected or self._protected_files
        self._objects, self._runtime_open = object_opener, runtime_opener
        self._runtime_inventory, self._runtime_verifier = runtime_inventory, runtime_verifier
        if streams is not None:
            from .service_snapshot_streams import FixedSnapshotStreams
            if (not isinstance(streams, FixedSnapshotStreams) or object_opener is not None or runtime_opener is not None
                    or streams.source_instance_ref not in {None, config.source_instance_ref}
                    or any(item.ref not in runtime_inventory for item in streams.runtime_files)): raise SnapshotError()
        self._streams = streams
        self._active = None
        self._child = None
        self._cancel = threading.Event()
        self._guard = threading.Lock()
        self._options = None
        self._binding = None

    @contextmanager
    def _protected_files(self, config):
        # Linux dir_fd/NOFOLLOW + owner-only credential 보장. 다른 OS는 검증을 가장하지 않는다.
        if os.name != "posix": raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
        import fcntl
        if not all(hasattr(os, name) for name in ("memfd_create", "MFD_ALLOW_SEALING", "MFD_CLOEXEC")):
            raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
        if not all(hasattr(fcntl, name) for name in ("F_ADD_SEALS", "F_GET_SEALS", "F_SEAL_SEAL", "F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK")):
            raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
        files = _PosixFiles()
        with ExitStack() as stack:
            opened = []
            for path in (config.executable, config.service_file, config.password_file):
                # root부터 직접 부모까지 모두 검증하고 열린 directory identity를 유지한다.
                for parent in reversed((path.parent, *path.parent.parents)):
                    parent_fd = stack.enter_context(files.pin(parent))
                    _validate_ancestor_stat(os.fstat(parent_fd), os.geteuid())
                handle = stack.enter_context(files.file(path))
                info = os.fstat(handle.fileno())
                if (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022
                        or (path != config.executable and info.st_mode & 0o077)):
                    raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
                opened.append(handle)
            def sealed_copy(handle, maximum, name, executable=False):
                before = os.fstat(handle.fileno())
                fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
                stack.callback(os.close, fd)
                count, digest = 0, hashlib.sha256()
                handle.seek(0)
                while chunk := handle.read(min(4 * 1024 * 1024, maximum - count + 1)):
                    count += len(chunk)
                    if count > maximum: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0: raise SnapshotError()
                        view = view[written:]
                after = os.fstat(handle.fileno())
                signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                if signature(before) != signature(after) or count != before.st_size: raise SnapshotError()
                if executable and digest.hexdigest() != config.executable_sha256: raise SnapshotError("SNAPSHOT_CLIENT_INVALID")
                os.fchmod(fd, 0o500 if executable else 0o600)
                seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK
                fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
                if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals != seals: raise SnapshotError()
                os.lseek(fd, 0, os.SEEK_SET)
                reference = "/proc/self/fd/" + str(fd)
                consumed = os.stat(reference)
                actual = os.fstat(fd)
                if (consumed.st_dev, consumed.st_ino) != (actual.st_dev, actual.st_ino): raise SnapshotError()
                return fd, reference
            text = opened[1].read(16385)
            if len(text) > 16384: raise SnapshotError()
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.read_string(text.decode("utf-8"))
            if parser.defaults() or config.service_name not in parser: raise SnapshotError()
            options = dict(parser[config.service_name])
            allowed = {"host", "hostaddr", "port", "dbname", "user", "sslmode", "sslrootcert", "connect_timeout"}
            if (not set(options) <= allowed or options.get("dbname") != config.database_name
                    or not options.get("user") or not (options.get("host") or options.get("hostaddr"))): raise SnapshotError()
            # 추가 TLS 파일 ref 보호 계약은 없으므로 pathname을 조용히 신뢰하지 않는다.
            if "sslrootcert" in options: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
            client_fd, client_path = sealed_copy(opened[0], 32 * 1024 * 1024, "daon-d1-client", True)
            service_fd, service_path = sealed_copy(opened[1], 16384, "daon-d1-service-ref")
            pass_fd, pass_path = sealed_copy(opened[2], 65536, "daon-d1-password-ref")
            # parse한 service 내용과 봉인한 내용의 동일성도 확인한다.
            if os.pread(service_fd, 16385, 0) != text: raise SnapshotError()
            yield _ProtectedBinding(client_path, service_path, pass_path,
                (client_fd, service_fd, pass_fd), {**options, "passfile": pass_path})

    def _connect_service(self, config):
        if self._options is None: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
        options = {**self._options, "connect_timeout": self._connect_timeout}
        return psycopg.connect(**options, autocommit=True)

    @staticmethod
    def _catalog(connection, maximum):
        # password/rolpassword/conninfo/FDW options은 조회하지 않는다. 기존 보호 정의만 보존한다.
        # 현재 DB의 보호 자산만 roots로 삼는다. 다른 DB 사용자/하위 sibling 회원을
        # 열거하지 않고 필요한 role의 상위 membership와 grantor 의존성만 닫는다.
        required = [row[0] for row in connection.execute("""
            WITH RECURSIVE assets(owner,acl) AS (
                SELECT datdba,datacl FROM pg_catalog.pg_database WHERE datname=current_database()
                UNION ALL SELECT nspowner,nspacl FROM pg_catalog.pg_namespace
                    WHERE nspname NOT IN ('pg_catalog','information_schema') AND nspname !~ '^pg_(toast|temp)'
                UNION ALL SELECT c.relowner,c.relacl FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp)'
                UNION ALL SELECT p.proowner,p.proacl FROM pg_catalog.pg_proc p
                    JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp)'
                UNION ALL SELECT defaclrole,defaclacl FROM pg_catalog.pg_default_acl
                UNION ALL SELECT c.relowner,a.attacl FROM pg_catalog.pg_attribute a
                    JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
                    AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp)'
            ), roots(roleid) AS (
                SELECT owner FROM assets
                UNION SELECT e.grantee FROM assets a CROSS JOIN LATERAL pg_catalog.aclexplode(a.acl) e
                UNION SELECT e.grantor FROM assets a CROSS JOIN LATERAL pg_catalog.aclexplode(a.acl) e
                UNION SELECT unnest(p.polroles) FROM pg_catalog.pg_policy p
                    JOIN pg_catalog.pg_class c ON c.oid=p.polrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp)'
                UNION SELECT s.refobjid FROM pg_catalog.pg_shdepend s
                    WHERE s.refclassid='pg_catalog.pg_authid'::regclass
                    AND s.dbid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
            ), required(roleid) AS (
                SELECT roleid FROM roots WHERE roleid<>0
                UNION SELECT dependency.roleid FROM required r
                    JOIN pg_catalog.pg_auth_members m ON m.member=r.roleid
                    CROSS JOIN LATERAL (VALUES (m.roleid),(m.grantor)) dependency(roleid)
                    WHERE dependency.roleid<>0
            ) SELECT roleid FROM required ORDER BY roleid
            """)]
        if not required or any(type(oid) is not int or oid <= 0 for oid in required) or len(required) != len(set(required)):
            raise SnapshotError("SNAPSHOT_CATALOG_ROLE_UNRESOLVED")
        queries = {
            "roles": "SELECT r.oid,r.rolname,r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,r.rolreplication,r.rolbypassrls,r.rolconnlimit,r.rolvaliduntil::text,r.rolconfig FROM pg_catalog.pg_roles r WHERE r.oid=ANY(%s) ORDER BY r.rolname",
            "memberships": "SELECT parent.rolname,member.rolname,grantor.rolname,m.admin_option,to_jsonb(m)->'inherit_option',to_jsonb(m)->'set_option' FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid JOIN pg_catalog.pg_roles member ON member.oid=m.member JOIN pg_catalog.pg_roles grantor ON grantor.oid=m.grantor WHERE m.member=ANY(%s) ORDER BY 1,2,3",
            "relations": "SELECT n.nspname,c.relname,c.relkind,r.rolname,c.relrowsecurity,c.relforcerowsecurity,COALESCE(c.relacl,acldefault((CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END)::\"char\",c.relowner))::text,COALESCE((SELECT json_agg(row(p.polname,p.polcmd,p.polpermissive,(SELECT array_agg(CASE WHEN role_id=0 THEN 'PUBLIC' ELSE (SELECT rolname FROM pg_roles WHERE oid=role_id) END ORDER BY role_id) FROM unnest(p.polroles) role_id),pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)) ORDER BY p.polname) FROM pg_policy p WHERE p.polrelid=c.oid),'[]'::json) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_toast' AND c.relkind IN ('r','p','v','m','S','f') ORDER BY 1,2",
            "functions": "SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),r.rolname,p.prosecdef,p.proconfig,p.proacl::text,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prokind IN ('f','p') ORDER BY 1,2,3",
        }
        result, size, resolved = {}, 0, set()
        for name, sql in queries.items():
            result[name] = []
            for row in connection.execute(sql, (required,) if name in {"roles", "memberships"} else ()):
                value = list(row)
                if name == "roles":
                    if value[0] not in required or value[0] in resolved: raise SnapshotError("SNAPSHOT_CATALOG_ROLE_UNRESOLVED")
                    resolved.add(value.pop(0))
                if name == "roles" and value[-1] is not None:
                    allowed = {"search_path", "row_security", "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout", "default_transaction_read_only"}
                    if any(not isinstance(setting, str) or setting.split("=", 1)[0] not in allowed for setting in value[-1]):
                        raise SnapshotError("SNAPSHOT_CATALOG_SETTING_UNSUPPORTED")
                size += len(_json(value))
                if size > maximum: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                result[name].append(value)
        if resolved != set(required): raise SnapshotError("SNAPSHOT_CATALOG_ROLE_UNRESOLVED")
        extra = (
            ("schema", "SELECT n.nspname,r.rolname,COALESCE(n.nspacl,acldefault('n',n.nspowner))::text FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp)' ORDER BY 1"),
            ("database", "SELECT d.datname,r.rolname,COALESCE(d.datacl,acldefault('d',d.datdba))::text FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba WHERE d.datname=current_database()"),
            ("default_acl", "SELECT r.rolname,n.nspname,a.defaclobjtype,a.defaclacl::text FROM pg_default_acl a JOIN pg_roles r ON r.oid=a.defaclrole LEFT JOIN pg_namespace n ON n.oid=a.defaclnamespace ORDER BY 1,2,3"),
            ("column_acl", "SELECT n.nspname,c.relname,a.attname,a.attacl::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3"),
        )
        for label, sql in extra:
            for row in connection.execute(sql):
                value = [label, *row]; size += len(_json(value))
                if size > maximum: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                result["relations"].append(value)
        return result

    @contextmanager
    def open_snapshot(self, spec, *, cancelled=lambda: False):
        if (spec.source_instance_ref, spec.credential_ref, spec.client_ref) != (
                self.config.source_instance_ref, self.config.credential_ref, self.config.client_ref): raise SnapshotError()
        if not self._guard.acquire(blocking=False): raise SnapshotError("SNAPSHOT_BUSY")
        connection = None
        protection_lifetime = ExitStack()
        self._cancel.clear()
        self._external_cancel = cancelled
        self._deadline = time.monotonic() + spec.limits.duration_seconds
        self._connect_timeout = min(5, spec.limits.duration_seconds)
        try:
            protected = self._validate_protected(self.config)
            if protected is None: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
            # 연결/child를 먼저 닫고 보호 FD를 마지막으로 해제한다.
            with nullcontext(protection_lifetime.enter_context(protected)) as binding:
                if (binding is None or not all(hasattr(binding, name) for name in
                        ("executable", "service_file", "password_file", "pass_fds", "options"))
                        or not binding.pass_fds): raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
                self._binding, self._options = binding, binding.options
                connection = self._connect(self.config)
                connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL search_path = pg_catalog")
                reader = _TimedReadConnection(connection, self)
                row = reader.execute("SELECT current_database(),current_setting('server_version_num')::int,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                if (row is None or row[0] != self.config.database_name or row[1] // 10000 != self.config.client_major
                        or (row[2] is not True and row[3] is not True)):
                    raise SnapshotError("SNAPSHOT_FULL_READ_PRIVILEGE_REQUIRED")
                snapshot = reader.execute("SELECT pg_export_snapshot()").fetchone()[0]
                if not isinstance(snapshot, str) or re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[1-9][0-9]*", snapshot) is None: raise SnapshotError()
                revision = reader.execute("SELECT version_num FROM public.alembic_version").fetchone()
                if revision is None: raise SnapshotError()
                objects, total = [], 0
                for row in reader.execute("SELECT tenant_id,workspace_id,area,object_id,status,digest_sha256,byte_size,object_key FROM public.object_records ORDER BY tenant_id,workspace_id,area,object_id"):
                    self._source_check()
                    tenant, workspace, area, object_id, state, digest, size, key = row
                    if state != "completed": raise SnapshotError("SNAPSHOT_STAGING_UNSUPPORTED")
                    context = CloudAccessContext(tenant, workspace, "snapshot-operator", "object.read")
                    if key != ObjectKeyPolicy().final_key(context, area, object_id): raise SnapshotError()
                    item = SnapshotObjectRef(tenant, workspace, area, object_id, state, digest, size)
                    objects.append(item); total += size
                    if total > spec.limits.object_bytes or len(objects) * 256 > spec.limits.metadata_bytes: raise SnapshotError("SNAPSHOT_LIMIT_EXCEEDED")
                catalog = self._catalog(reader, spec.limits.metadata_bytes)
                lease = SnapshotLease(snapshot, str(revision[0]), tuple(objects), self._runtime_inventory, catalog)
                self._source_check()
                self._active = lease
                yield lease
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_SOURCE_UNAVAILABLE") from None
        finally:
            self._active = None
            try:
                self.cancel_owned()
                if connection is not None:
                    try:
                        try: connection.rollback()
                        finally: connection.close()
                    except Exception:
                        raise SnapshotError("SNAPSHOT_SOURCE_CLEANUP_UNCERTAIN") from None
            finally:
                try: protection_lifetime.close()
                finally:
                    self._binding, self._options = None, None
                    self._guard.release()

    def _source_check(self):
        if self._cancel.is_set() or self._external_cancel(): raise SnapshotError("SNAPSHOT_CANCELLED")
        if time.monotonic() >= self._deadline: raise SnapshotError("SNAPSHOT_TIMEOUT")

    def _check(self):
        if self._active is None: raise SnapshotError("SNAPSHOT_CANCELLED")
        self._source_check()

    def cancel_owned(self):
        self._cancel.set()
        child = self._child
        if child is not None and child.poll() is None:
            try:
                child.terminate()
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=2)
            except Exception: raise SnapshotError("SNAPSHOT_CHILD_CLEANUP_UNCERTAIN") from None

    @contextmanager
    def dump_archive(self, lease):
        if lease is not self._active: raise SnapshotError()
        self._check()
        if self._binding is None: raise SnapshotError("SNAPSHOT_PROTECTED_REFERENCE_UNVERIFIED")
        argv = [self._binding.executable, "--format=custom", "--no-password",
            "--snapshot=" + lease.snapshot_id, "--dbname=service=" + self.config.service_name]
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
        env.update(PGSERVICEFILE=self._binding.service_file, PGPASSFILE=self._binding.password_file,
            PGCONNECT_TIMEOUT="5", PGAPPNAME="daon-service-snapshot")
        child, drain, watchdog = None, None, None
        self._read_deadline, self._read_cancelled = self._deadline, self._external_cancel
        done, drain_failed = threading.Event(), threading.Event()
        try:
            child = self._spawn(argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, pass_fds=self._binding.pass_fds,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self._child = child
            def discard_stderr():
                try:
                    # 고정4KiB만 보유하며 나머지는 즉시 폐기한다. 오류 원문은 저장/반환하지 않는다.
                    while child.stderr.read(4096): pass
                except Exception: drain_failed.set()
            def supervise():
                while not done.wait(.05):
                    if self._cancel.is_set() or self._external_cancel() or self._read_cancelled() or time.monotonic() >= min(self._deadline, self._read_deadline):
                        try: self.cancel_owned()
                        except SnapshotError: drain_failed.set()
                        return
            drain = threading.Thread(target=discard_stderr, daemon=True)
            watchdog = threading.Thread(target=supervise, daemon=True)
            drain.start(); watchdog.start()
            yield _ArchiveStream(child.stdout, self)
            self._check()
            code = child.wait(timeout=max(.01, self._deadline - time.monotonic()))
            drain.join(timeout=2)
            if code != 0 or drain.is_alive() or drain_failed.is_set(): raise SnapshotError("SNAPSHOT_DUMP_FAILED")
        except SnapshotError: raise
        except Exception: raise SnapshotError("SNAPSHOT_DUMP_FAILED") from None
        finally:
            done.set()
            try:
                if child is not None:
                    if child.poll() is None: self.cancel_owned()
                    child.stdout.close(); child.stderr.close()
            finally:
                if drain is not None: drain.join(timeout=2)
                if watchdog is not None: watchdog.join(timeout=2)
                self._child = None

    @contextmanager
    def open_object(self, ref):
        self._check()
        if self._streams is not None:
            if ref not in self._active.object_inventory: raise SnapshotError("SNAPSHOT_OBJECT_PORT_UNAVAILABLE")
            with self._streams.open_object(ref, deadline=self._deadline,
                    cancelled=lambda: self._cancel.is_set() or self._external_cancel()) as response: yield response
            return
        if self._objects is None or ref not in self._active.object_inventory: raise SnapshotError("SNAPSHOT_OBJECT_PORT_UNAVAILABLE")
        with self._objects(ref) as response: yield response

    @contextmanager
    def open_runtime(self, ref):
        self._check()
        if self._streams is not None:
            if ref not in self._active.runtime_inventory or ref.mode != "bytes": raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
            with self._streams.open_runtime(ref, deadline=self._deadline,
                    cancelled=lambda: self._cancel.is_set() or self._external_cancel()) as response: yield response
            return
        if self._runtime_open is None or ref not in self._active.runtime_inventory or ref.mode != "bytes": raise SnapshotError("SNAPSHOT_RUNTIME_UNPROVEN")
        with self._runtime_open(ref) as response: yield response

    def verify_runtime_reference(self, spec, ref):
        self._check()
        return (ref in self._active.runtime_inventory and ref.mode == "reconstructible"
            and self._runtime_verifier is not None and self._runtime_verifier(spec, ref) is True)
