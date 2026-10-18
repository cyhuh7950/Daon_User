"""D1 B2 직접 SQL/프로세스 포트 시험. 실제 pg_dump/PG 실행이 아니다."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from daon_user_api.service_snapshot import (
    ServiceSnapshotSpec, SnapshotLimits, SnapshotRuntimeRef, SnapshotError,
)


class ServiceSnapshotPostgresTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("daon_user_api.service_snapshot_postgres"), "B2 고정 PG/client port 미구현")
        from daon_user_api import service_snapshot_postgres as m
        self.m = m
        self.spec = ServiceSnapshotSpec("source-ref", "credential-ref", "client-ref", "root-ref", "code-a",
            "sha256:" + "a" * 64, (), "v1", "existing-ref", SnapshotLimits(4096, 4096, 32768, 300))
        self.config = m.FixedPgSource("source-ref", "credential-ref", "client-ref", "daon_user_d1_synthetic",
            Path("D:/not-created/pg_dump.exe"), "b" * 64, 17, "d1-source",
            Path("D:/not-created/protected-services.conf"), Path("D:/not-created/protected-passfile"))
        self.log = []
        self.active = [False]
        test = self
        class Connection:
            def execute(inner, sql, params=()):
                test.log.append((sql, params))
                if "WITH RECURSIVE" in sql: rows = [(17,)]
                elif "rolvaliduntil" in sql:
                    rows = [(17, "synthetic-owner", False, True, False, False, False, False, False, -1, None, None)]
                elif "current_setting('server_version_num')" in sql: rows = [("daon_user_d1_synthetic", 170000, True, False)]
                elif "FROM pg_database" in sql: rows = [("daon_user_d1_synthetic", "synthetic-owner", "{owner=CTc/owner}")]
                elif "pg_export_snapshot()" in sql: rows = [("00000003-0000001B-1",)]
                elif "version_num" in sql: rows = [("test-revision",)]
                elif "FROM public.object_records" in sql: rows = list(test.objects)
                else: rows = []
                class Cursor:
                    def fetchone(inner): return rows[0] if rows else None
                    def __iter__(inner): return iter(rows)
                return Cursor()
            def rollback(inner): test.log.append(("ROLLBACK", ()))
            def close(inner): test.active[0] = False; test.log.append(("CLOSE", ()))
        self.connection = Connection()
        self.objects = [("tenant-a", "workspace-a", "source", "a" * 32, "completed", "c" * 64, 7,
            "tenant-a/workspace-a/source/" + "a" * 32)]
        self.child = m_test_child = type("OwnedChild", (), {})()
        m_test_child.stdout = io.BytesIO(b"PGDMP synthetic archive")
        m_test_child.stderr = io.BytesIO(b"private credential /path DSN details\n" * 10000)
        m_test_child.returncode = None
        m_test_child.killed = False
        m_test_child.poll = lambda: m_test_child.returncode
        def wait(timeout=None): m_test_child.returncode = 0; return 0
        m_test_child.wait = wait
        def terminate(): m_test_child.killed = True; m_test_child.returncode = -15
        m_test_child.terminate = terminate
        m_test_child.kill = terminate
        self.spawned = []
        def spawn(argv, **kwargs):
            test.assertTrue(test.active[0])
            self.spawned.append((argv, kwargs)); return m_test_child
        def connect(config): self.active[0] = True; return self.connection
        @contextmanager
        def protected(config):
            yield SimpleNamespace(executable="/proc/self/fd/81", service_file="/proc/self/fd/82",
                password_file="/proc/self/fd/83", pass_fds=(81, 82, 83), options={"dbname": config.database_name})
        self.port = m.PostgresSnapshotPort(self.config, connect=connect, spawn=spawn,
            validate_protected=protected, object_opener=None, runtime_inventory=(), runtime_opener=None)

    def test_fixed_client_argv_never_embeds_secret(self):
        with self.port.open_snapshot(self.spec) as lease:
            with self.port.dump_archive(lease) as source:
                self.assertEqual(source.read(5), b"PGDMP")
        argv, options = self.spawned[0]
        self.assertEqual(argv, ["/proc/self/fd/81", "--format=custom", "--no-password",
            "--snapshot=00000003-0000001B-1", "--dbname=service=d1-source"])
        self.assertIs(options["shell"], False)
        self.assertNotIn(str(self.config.password_file), " ".join(argv))
        self.assertEqual(options["env"]["PGPASSFILE"], "/proc/self/fd/83")
        self.assertNotIn("PGPASSWORD", options["env"])

    def test_exporter_lifetime_covers_dump_snapshot_import(self):
        with self.port.open_snapshot(self.spec) as lease:
            self.assertTrue(self.active[0])
            with self.port.dump_archive(lease) as source: source.read(4096)
            self.assertTrue(self.active[0])
        self.assertFalse(self.active[0])
        self.assertEqual(self.log[0], ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", ()))
        self.assertEqual(self.log[-2:], [("ROLLBACK", ()), ("CLOSE", ())])
        with self.assertRaises(SnapshotError):
            with self.port.dump_archive(lease): pass

    def test_object_inventory_uses_same_snapshot(self):
        with self.port.open_snapshot(self.spec) as lease:
            self.assertEqual(lease.object_inventory[0].object_id, "a" * 32)
            self.assertEqual(lease.schema_revision, "test-revision")
            self.assertEqual(lease.catalog, {"roles": [["synthetic-owner", False, True, False, False, False, False, False, -1, None, None]], "memberships": [],
                "relations": [["database", "daon_user_d1_synthetic", "synthetic-owner", "{owner=CTc/owner}"]], "functions": []})
        query = next(sql for sql, _ in self.log if "FROM public.object_records" in sql)
        self.assertNotIn("WHERE status=", query)
        self.objects[0] = (*self.objects[0][:4], "pending", *self.objects[0][5:])
        with self.assertRaises(SnapshotError):
            with self.port.open_snapshot(self.spec): pass
        self.assertEqual(self.spawned, [])

    def test_missing_force_rls_privilege_is_not_partial_backup(self):
        from unittest.mock import patch
        old = self.connection.execute
        def execute(sql, params=()):
            if "current_database()" in sql:
                return type("Cursor", (), {"fetchone": lambda inner: ("daon_user_d1_synthetic", 170000, False, False)})()
            return old(sql, params)
        with patch.object(self.connection, "execute", side_effect=execute):
            with self.assertRaises(SnapshotError):
                with self.port.open_snapshot(self.spec): pass
        self.assertEqual(self.spawned, [])
        self.assertFalse(self.active[0])

    def test_stderr_is_bounded_and_sanitized(self):
        self.child.wait = lambda timeout=None: 1
        with self.port.open_snapshot(self.spec) as lease:
            with self.assertRaises(SnapshotError) as caught:
                with self.port.dump_archive(lease) as source: source.read(4096)
        self.assertEqual(str(caught.exception), "SNAPSHOT_DUMP_FAILED")
        self.assertNotIn("credential", repr(caught.exception))
        self.assertTrue(self.child.stderr.closed)
        self.assertTrue(self.child.stdout.closed)

    def test_cancel_stops_only_owned_child(self):
        with self.port.open_snapshot(self.spec) as lease:
            with self.assertRaises(SnapshotError):
                with self.port.dump_archive(lease) as source:
                    self.port.cancel_owned()
                    source.read(5)
        self.assertTrue(self.child.killed)
        self.assertEqual(len(self.spawned), 1)
        self.assertFalse(self.active[0])

    def test_cancel_callback_interrupts_blocked_owned_stdout(self):
        import threading
        requested, stopped = threading.Event(), threading.Event()
        class Blocked:
            def read(inner, count):
                requested.set()
                if not stopped.wait(1): raise RuntimeError("synthetic read did not stop")
                return b""
            def close(inner): pass
        self.child.stdout = Blocked()
        old = self.child.terminate
        def terminate(): old(); stopped.set()
        self.child.terminate = terminate
        with self.port.open_snapshot(self.spec, cancelled=requested.is_set) as lease:
            with self.assertRaises(SnapshotError) as caught:
                with self.port.dump_archive(lease) as source: source.read(5)
        self.assertEqual(caught.exception.code, "SNAPSHOT_CANCELLED")
        self.assertTrue(stopped.is_set())

    def test_catalog_keeps_nonsecret_role_membership_and_schema_privileges(self):
        with self.port.open_snapshot(self.spec): pass
        # 실제 SQL 읽기 포트에 발행한 보호 catalog 투영 계약이다. PG 실행 증거는 아니다.
        roles = next(sql for sql, _ in self.log if "rolvaliduntil" in sql)
        memberships = next(sql for sql, _ in self.log if "admin_option" in sql)
        self.assertIn("rolvaliduntil", roles)
        self.assertIn("inherit_option", memberships)
        self.assertIn("set_option", memberships)
        self.assertTrue(any("FROM pg_namespace" in sql for sql, _ in self.log))
        self.assertTrue(any("FROM pg_default_acl" in sql for sql, _ in self.log))
        self.assertTrue(any("attacl" in sql for sql, _ in self.log))
        self.assertFalse(any("rolpassword" in sql for sql, _ in self.log))

    def test_elapsed_deadline_before_export_never_starts_dump(self):
        from unittest.mock import patch
        tick = [0]
        original = self.connection.execute
        def delayed(sql, params=()):
            value = original(sql, params)
            if "current_setting('server_version_num')" in sql: tick[0] = 301
            return value
        with patch.object(self.connection, "execute", side_effect=delayed), patch.object(self.m.time, "monotonic", side_effect=lambda: tick[0]):
            with self.assertRaises(SnapshotError) as caught:
                with self.port.open_snapshot(self.spec): pass
        self.assertEqual(caught.exception.code, "SNAPSHOT_TIMEOUT")
        self.assertFalse(any("pg_export_snapshot()" in sql for sql, _ in self.log))
        self.assertEqual(self.spawned, [])

    def test_all_ancestors_require_protected_owner_and_mode(self):
        check = getattr(self.m, "_validate_ancestor_stat", None)
        self.assertTrue(callable(check), "직접 부모뿐 아니라 모든 조상의 보호 검사 필요")
        import stat
        check(SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0), 1000)
        for owner, mode in ((0, 0o777), (2000, 0o755), (1000, 0o775)):
            with self.assertRaises(SnapshotError):
                check(SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=owner), 1000)

    def test_unbound_protection_port_is_refused_before_connection(self):
        self.port._validate_protected = lambda config: None
        with self.assertRaises(SnapshotError):
            with self.port.open_snapshot(self.spec): pass
        self.assertFalse(self.active[0])
        self.assertEqual(self.spawned, [])

    def test_consumption_uses_verified_descriptor_binding_not_original_path(self):
        @contextmanager
        def protected(config):
            yield SimpleNamespace(executable="/proc/self/fd/81", service_file="/proc/self/fd/82",
                password_file="/proc/self/fd/83", pass_fds=(81, 82, 83), options={"dbname": config.database_name})
        self.port._validate_protected = protected
        with self.port.open_snapshot(self.spec) as lease:
            with self.port.dump_archive(lease) as source: source.read(5)
        argv, options = self.spawned[0]
        self.assertEqual(argv[0], "/proc/self/fd/81")
        self.assertEqual(options["pass_fds"], (81, 82, 83))
        self.assertEqual(options["env"]["PGSERVICEFILE"], "/proc/self/fd/82")
        self.assertEqual(options["env"]["PGPASSFILE"], "/proc/self/fd/83")

    def test_inventory_uses_public_schema_with_trusted_search_path(self):
        with self.port.open_snapshot(self.spec): pass
        sql = [item[0] for item in self.log]
        self.assertIn("SET LOCAL search_path = pg_catalog", sql)
        self.assertTrue(any("FROM public.alembic_version" in item for item in sql))
        self.assertTrue(any("FROM public.object_records" in item for item in sql))

    def test_catalog_roles_are_database_dependency_closure_only(self):
        with self.port.open_snapshot(self.spec): pass
        sql = [item[0] for item in self.log]
        self.assertTrue(any("pg_shdepend" in item and "WITH RECURSIVE" in item for item in sql))
        roles = next((item for item in sql if "rolvaliduntil" in item), "")
        memberships = next((item for item in sql if "admin_option" in item), "")
        self.assertIn("ANY(%s)", roles)
        self.assertIn("ANY(%s)", memberships)

    def test_catalog_does_not_collect_unrelated_roles_and_refuses_missing_dependency(self):
        from unittest.mock import patch
        original = self.connection.execute
        role_query = []
        def execute(sql, params=()):
            if "rolvaliduntil" in sql:
                role_query.append(params)
                if "ANY(%s)" not in sql:
                    raise AssertionError("다른 DB role 조회 금지")
            return original(sql, params)
        with patch.object(self.connection, "execute", side_effect=execute):
            with self.port.open_snapshot(self.spec) as lease:
                self.assertEqual([row[0] for row in lease.catalog["roles"]], ["synthetic-owner"])
        self.assertEqual(role_query, [([17],)])
        def missing(sql, params=()):
            if "WITH RECURSIVE" in sql:
                return iter([(17,), (23,)])
            return original(sql, params)
        with patch.object(self.connection, "execute", side_effect=missing):
            with self.assertRaises(SnapshotError) as caught:
                with self.port.open_snapshot(self.spec): pass
        self.assertEqual(caught.exception.code, "SNAPSHOT_CATALOG_ROLE_UNRESOLVED")

    def test_descriptor_binding_lives_until_connection_is_closed(self):
        @contextmanager
        def protected(config):
            try:
                yield SimpleNamespace(executable="/proc/self/fd/81", service_file="/proc/self/fd/82",
                    password_file="/proc/self/fd/83", pass_fds=(81, 82, 83), options={"dbname": config.database_name})
            finally:
                self.assertFalse(self.active[0], "libpq 소비 중 보호 FD 해제 금지")
                self.log.append(("DESCRIPTORS_CLOSED", ()))
        self.port._validate_protected = protected
        with self.port.open_snapshot(self.spec): pass
        self.assertEqual(self.log[-2:], [("CLOSE", ()), ("DESCRIPTORS_CLOSED", ())])

    def test_b2_explicit_concrete_port_binds_exact_inventory_and_cancel(self):
        import struct
        from daon_user_api.service_snapshot_streams import FixedRuntimeFile
        io_port = BoundedWorkerIO(b"D" + struct.pack(">I", 3) + b"abcE\0\0\0\0")
        factory, ref, _ = ServiceSnapshotConcreteStreamsTests().factory(io_port)
        runtime = SnapshotRuntimeRef("runtime-a", "bytes", hashlib.sha256(b"abc").hexdigest(), 3)
        factory.runtime_files = (FixedRuntimeFile(runtime, Path("D:/not-created/runtime.bin")),)
        self.objects = [(ref.tenant_id, ref.workspace_id, ref.area, ref.object_id, ref.state,
            ref.digest, ref.byte_size, "tenant-a/workspace-a/source/" + ref.object_id)]
        port = self.m.PostgresSnapshotPort(self.config, connect=self.port._connect, spawn=self.port._spawn,
            validate_protected=self.port._validate_protected, runtime_inventory=(runtime,), streams=factory)
        with port.open_snapshot(self.spec) as lease:
            with self.assertRaises(SnapshotError):
                with port.open_object(replace(ref, workspace_id="workspace-foreign")): pass
            with self.assertRaises(SnapshotError):
                with port.open_runtime(replace(runtime, digest="f" * 64)): pass
            self.assertEqual(io_port.started, 0)
            with port.open_object(lease.object_inventory[0]) as reader:
                self.assertEqual(reader.read_bounded(3, deadline=2., cancelled=lambda: False), b"abc")
                self.assertEqual(reader.read_bounded(3, deadline=2., cancelled=lambda: False), b"")
            io_port.payload.extend(b"D\0\0\0\x03abcE\0\0\0\0")
            with port.open_runtime(runtime) as reader:
                self.assertEqual(reader.read_bounded(3, deadline=2., cancelled=lambda: False), b"abc")
                port.cancel_owned()
                with self.assertRaises(SnapshotError) as caught:
                    reader.read_bounded(3, deadline=2., cancelled=lambda: False)
                self.assertEqual(caught.exception.code, "SNAPSHOT_CANCELLED")
        self.assertEqual(io_port.started, 2)
        with self.assertRaises(SnapshotError):
            with port.open_runtime(runtime): pass


class BoundedWorkerIO:
    """Windows에서도 주입하는 OS/child 대역. 실제 fork/IPC/Linux 실행 증거가 아니다."""
    def __init__(self, payload=b"", *, stalled=False, immortal=False, status=0, fragment=3):
        self.payload, self.stalled, self.immortal = bytearray(payload), stalled, immortal
        self.status, self.fragment, self.now, self.events, self.started = status, fragment, 0., [], 0
        self.stopped, self.closed = False, False
    def supported(self): return True
    def start(self, work):
        self.started += 1; self.events.append(("start", work)); return 41001, 42
    def readable(self, fd, timeout):
        self.now += min(timeout, .02)
        return not self.stalled
    def receive(self, fd, maximum):
        if self.stalled: raise BlockingIOError()
        count = min(maximum, self.fragment, len(self.payload))
        value = bytes(self.payload[:count]); del self.payload[:count]; return value
    def poll(self, pid):
        self.events.append(("poll", pid))
        return (not self.immortal and (self.stopped or (not self.stalled and not self.payload)), self.status)
    def terminate(self, pid): self.events.append(("terminate", pid)); self.stopped = True
    def kill(self, pid): self.events.append(("kill", pid)); self.stopped = True
    def close(self, fd): self.events.append(("close", fd)); self.closed = True
    def pause(self, duration): self.now += duration


class ServiceSnapshotConcreteStreamsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("daon_user_api.service_snapshot_streams"), "B2 concrete worker/reader 미구현")
        from daon_user_api import service_snapshot_streams
        return service_snapshot_streams

    def factory(self, io_port):
        import secrets
        from daon_user_api.object_queue import MinioObjectStorageAdapter
        module = self.module()
        # 설치 SDK 생성자만 사용한다. 자격은 메모리 합성·외부 연결0이다.
        storage = MinioObjectStorageAdapter(endpoint="snapshot.invalid:9000", bucket="synthetic-bucket",
            access_key=secrets.token_hex(12), secret_key=secrets.token_hex(24), secure=True)
        factory = module.FixedSnapshotStreams.from_storage(storage, source_instance_ref="source-ref", region="us-east-1",
            runtime_files=(), _io=io_port, _clock=lambda: io_port.now)
        from daon_user_api.service_snapshot import SnapshotObjectRef
        ref = SnapshotObjectRef("tenant-a", "workspace-a", "source", "a" * 32, "completed", hashlib.sha256(b"abc").hexdigest(), 3)
        return factory, ref, storage

    def test_b2_concrete_frames_normal_bytes_and_fixed_key_binding(self):
        self.module()
        import struct
        io_port = BoundedWorkerIO(b"D" + struct.pack(">I", 3) + b"abc" + b"E\0\0\0\0")
        factory, ref, _ = self.factory(io_port)
        with factory.open_object(ref, deadline=3., cancelled=lambda: False) as reader:
            self.assertEqual(reader.read_bounded(2, deadline=2., cancelled=lambda: False), b"ab")
            self.assertEqual(reader.read_bounded(2, deadline=2., cancelled=lambda: False), b"c")
            self.assertEqual(reader.read_bounded(2, deadline=2., cancelled=lambda: False), b"")
        self.assertTrue(io_port.closed)
        work = io_port.events[0][1]
        self.assertEqual(work.key, "tenant-a/workspace-a/source/" + "a" * 32)
        self.assertEqual(io_port.started, 1)
        self.assertFalse(any(action in {"terminate", "kill"} for action, _ in io_port.events))

    def test_b2_concrete_parent_deadline_cancel_and_uncertain_reap(self):
        self.module()
        for mode in ("blocked-open", "blocked-read", "dns", "slow-drip", "cancel", "uncertain"):
            io_port = BoundedWorkerIO(stalled=True, immortal=mode == "uncertain")
            factory, ref, _ = self.factory(io_port)
            with self.assertRaises(SnapshotError) as caught:
                with factory.open_object(ref, deadline=.05, cancelled=lambda: False) as reader:
                    reader.read_bounded(4096, deadline=.05, cancelled=lambda: mode == "cancel")
            self.assertEqual(caught.exception.code, "SNAPSHOT_WORKER_CLEANUP_UNCERTAIN" if mode == "uncertain" else
                "SNAPSHOT_CANCELLED" if mode == "cancel" else "SNAPSHOT_TIMEOUT")
            if io_port.started:
                self.assertTrue(io_port.closed)
                self.assertTrue(all(pid == 41001 for action, pid in io_port.events if action in {"terminate", "kill"}))
                self.assertTrue(any(action == "terminate" for action, _ in io_port.events))
            self.assertLess(io_port.now, 2.)
            if mode == "uncertain":
                with self.assertRaises(SnapshotError):
                    with factory.open_object(ref, deadline=2., cancelled=lambda: False): pass

    def test_b2_concrete_frame_cap_truncation_error_and_trailing_data(self):
        self.module()
        import struct
        for raw in (b"D" + struct.pack(">I", 65536), b"D\0\0\0\x03a", b"X\0\0\0\0",
                b"E\0\0\0\0private", b"D\0\0\0\0", b"Z\0\0\0\0"):
            io_port = BoundedWorkerIO(raw)
            factory, ref, _ = self.factory(io_port)
            with self.assertRaises(SnapshotError) as caught:
                with factory.open_object(ref, deadline=5., cancelled=lambda: False) as reader:
                    while reader.read_bounded(65536, deadline=5., cancelled=lambda: False): pass
            self.assertNotIn("private", str(caught.exception))
            self.assertTrue(io_port.closed)

    def test_b2_concrete_missing_runtime_foreign_ref_and_windows_refuse_before_start(self):
        module = self.module()
        io_port = BoundedWorkerIO()
        factory, ref, _ = self.factory(io_port)
        with self.assertRaises(SnapshotError):
            with factory.open_runtime(SnapshotRuntimeRef("missing", "bytes", "a" * 64, 3), deadline=2., cancelled=lambda: False): pass
        with self.assertRaises(SnapshotError):
            with factory.open_object(replace(ref, tenant_id="../foreign"), deadline=2., cancelled=lambda: False): pass
        self.assertEqual(io_port.started, 0)
        from unittest.mock import patch
        with patch.object(module.sys, "platform", "win32"):
            native = module.FixedSnapshotStreams(runtime_files=())
            with self.assertRaises(SnapshotError) as caught:
                with native.open_runtime(SnapshotRuntimeRef("missing", "bytes", "a" * 64, 3), deadline=2., cancelled=lambda: False): pass
            self.assertEqual(caught.exception.code, "SNAPSHOT_STREAM_PLATFORM_UNSUPPORTED")

    def test_b2_guarded_http_rejects_error_redirect_without_body_or_shared_pool_changes(self):
        module = self.module()
        from unittest.mock import patch
        from urllib3 import PoolManager
        from urllib3.response import HTTPResponse
        for status in (301, 307, 403, 404, 500, 206):
            io_port = BoundedWorkerIO(); factory, ref, shared = self.factory(io_port)
            original_http = shared._client._http
            closed, read = [], []
            response = HTTPResponse(status=status, preload_content=False)
            response.read = lambda *a, **k: read.append(True)
            response.close = lambda: closed.append("closed")
            response.release_conn = lambda: closed.append("released")
            with patch.object(PoolManager, "urlopen", return_value=response):
                with self.assertRaises(SnapshotError):
                    with module._minio_chunks(factory._object_work(ref, time.monotonic() + 2.)) as chunks: list(chunks)
            self.assertEqual(read, [])
            self.assertEqual(closed, ["closed", "released"])
            self.assertIs(shared._client._http, original_http)

    def test_b2_guarded_http_success_uses_read1_no_decode_fixed_get_and_region(self):
        module = self.module()
        from unittest.mock import patch
        from urllib3 import PoolManager
        from urllib3.response import HTTPResponse
        io_port = BoundedWorkerIO(); factory, ref, shared = self.factory(io_port)
        calls, reads = [], []
        response = HTTPResponse(status=200, preload_content=False)
        parts = iter((b"a", b"bc", b""))
        def read1(maximum, *, decode_content):
            reads.append((maximum, decode_content)); return next(parts)
        response.read1 = read1
        def exchange(pool, method, url, **kwargs):
            calls.append((method, url, kwargs)); return response
        with patch.object(PoolManager, "urlopen", new=exchange):
            with module._minio_chunks(factory._object_work(ref, time.monotonic() + 2.)) as chunks:
                self.assertEqual(b"".join(chunks), b"abc")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "GET")
        self.assertNotIn("location", calls[0][1])
        self.assertTrue(calls[0][1].endswith("/synthetic-bucket/tenant-a/workspace-a/source/" + "a" * 32))
        for name in ("redirect", "retries", "preload_content", "decode_content"): self.assertIs(calls[0][2][name], False)
        self.assertTrue(all(0 < maximum <= 65531 and decode is False for maximum, decode in reads))

    def test_b2_runtime_pinned_file_port_and_exact_ref_before_worker(self):
        module = self.module()
        from unittest.mock import patch
        ref = SnapshotRuntimeRef("runtime-a", "bytes", hashlib.sha256(b"abc").hexdigest(), 3)
        path = Path("D:/not-created/runtime.bin")
        events = []
        class Files:
            @contextmanager
            def pin(inner, parent):
                events.append(("pin", parent)); yield
                events.append(("unpin", parent))
            @contextmanager
            def file(inner, requested):
                events.append(("open", requested))
                stream = io.BytesIO(b"abc"); stream.fileno = lambda: 51
                try: yield stream
                finally: stream.close(); events.append(("closed", requested))
        work = module._RuntimeWork(module.FixedRuntimeFile(ref, path), time.monotonic() + 2)
        with patch.object(module, "_PosixFiles", Files), patch.object(module.os, "fstat", return_value=SimpleNamespace(st_size=3)):
            with module._runtime_chunks(work) as chunks: self.assertEqual(b"".join(chunks), b"abc")
        self.assertEqual(events, [("pin", path.parent), ("open", path), ("closed", path), ("unpin", path.parent)])
        # 실제 POSIX link 검사가 아닌 보호 file port의 거부 전파 시험이다.
        with patch.object(module, "_PosixFiles", side_effect=SnapshotError("SNAPSHOT_LINK_UNSAFE")):
            with self.assertRaises(SnapshotError):
                with module._runtime_chunks(work): pass
        io_port = BoundedWorkerIO()
        factory = module.FixedSnapshotStreams(runtime_files=(work.file,), _io=io_port, _clock=lambda: io_port.now)
        for changed in (replace(ref, ref_id="missing"), replace(ref, byte_size=4), replace(ref, digest="b" * 64)):
            with self.assertRaises(SnapshotError):
                with factory.open_runtime(changed, deadline=2., cancelled=lambda: False): pass
        self.assertEqual(io_port.started, 0)

    def test_b2_worker_closes_inherited_fds_and_bounds_every_frame(self):
        module = self.module()
        from unittest.mock import patch
        closed, writes = [], []
        with patch.object(module.os, "listdir", return_value=["0", "1", "2", "17", "42"]), patch.object(module.os, "close", side_effect=closed.append):
            module._close_inherited_fds({42})
        self.assertEqual(closed, [0, 1, 2, 17])
        def write(fd, frame):
            self.assertEqual(fd, 42); self.assertLessEqual(len(frame), 65536)
            count = min(7, len(frame)); writes.append(bytes(frame[:count])); return count
        with patch.object(module.os, "write", side_effect=write):
            module._send(42, b"D", b"a" * module.PAYLOAD)
            with self.assertRaises(SnapshotError): module._send(42, b"D", b"a" * (module.PAYLOAD + 1))
        self.assertEqual(len(b"".join(writes)), 65536)
        factory, ref, _ = self.factory(BoundedWorkerIO())
        work = factory._object_work(ref, time.monotonic() + 2.)
        events = []
        class ChildExit(BaseException): pass
        @contextmanager
        def source(job):
            self.assertEqual(job, work); events.append("open")
            try: yield iter((b"abc",))
            finally: events.append("source-close")
        def exit_child(code): events.append(("exit", code)); raise ChildExit()
        with patch.object(module, "_close_inherited_fds", side_effect=lambda keep: events.append(("fds", keep))), \
                patch.object(module, "_minio_chunks", source), patch.object(module, "_send", side_effect=lambda fd, kind, data=b"": events.append((kind, data))), \
                patch.object(module.os, "set_blocking", side_effect=lambda fd, value: events.append(("blocking", value))), \
                patch.object(module.os, "close", side_effect=lambda fd: events.append(("close", fd))), patch.object(module.os, "_exit", side_effect=exit_child):
            with self.assertRaises(ChildExit): module._worker(work, 42)
        self.assertEqual(events, [("fds", {42}), ("blocking", True), "open", (b"D", b"abc"),
            "source-close", (b"E", b""), ("close", 42), ("exit", 0)])

    def test_b2_parent_partial_frames_slow_drip_and_single_owned_execution(self):
        module = self.module()
        # 지연 시작/partial header/partial payload 모두 부모 absolute deadline을 공유한다.
        for payload, fragment in ((b"", 1), (b"D\0", 1), (b"D\0\0\0\x03a", 1), (b"D\0\0\0\x03abcE\0\0\0\0", 1)):
            io_port = BoundedWorkerIO(payload, fragment=fragment)
            factory, ref, _ = self.factory(io_port)
            with self.assertRaises(SnapshotError):
                with factory.open_object(ref, deadline=.04, cancelled=lambda: False) as reader:
                    while reader.read_bounded(3, deadline=.04, cancelled=lambda: False): pass
            self.assertTrue(io_port.closed)
            self.assertLess(io_port.now, 1.)
        io_port = BoundedWorkerIO(b"E\0\0\0\0")
        factory, ref, _ = self.factory(io_port)
        with factory.open_object(ref, deadline=1., cancelled=lambda: False) as reader:
            with self.assertRaises(SnapshotError) as caught:
                with factory.open_object(ref, deadline=1., cancelled=lambda: False): pass
            self.assertEqual(caught.exception.code, "SNAPSHOT_BUSY")
            self.assertEqual(reader.read_bounded(1, deadline=1., cancelled=lambda: False), b"")
        self.assertEqual(io_port.started, 1)
        for status in (1, -9):
            io_port = BoundedWorkerIO(b"E\0\0\0\0", status=status)
            factory, ref, _ = self.factory(io_port)
            with self.assertRaises(SnapshotError):
                with factory.open_object(ref, deadline=1., cancelled=lambda: False) as reader:
                    reader.read_bounded(1, deadline=1., cancelled=lambda: False)
            self.assertFalse(any(event in {"terminate", "kill"} for event, _ in io_port.events))


if __name__ == "__main__": unittest.main()
