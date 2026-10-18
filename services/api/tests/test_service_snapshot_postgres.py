"""D1 B2 직접 포트 대역 + 별도 opt-in 실제 Linux/PG15/MinIO node를 구분한다."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import re
import secrets
import sys
import time
import unittest
from contextlib import ExitStack, contextmanager
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


class _NativePrivateConfig(dict):
    def __repr__(self): return "<S44_NATIVE_PRIVATE_CONFIG>"
    __str__ = __repr__


def _native_validate_config(values, *, environment=None):
    """네트워크/파일 생성 이전 고정 QA 경계. 운영 DSN/alias 우회는 허용하지 않는다."""
    ambient = os.environ if environment is None else environment
    if any(name.upper().startswith("PG") for name in ambient):
        raise ValueError("S44_NATIVE_LIBPQ_ENV_UNSUPPORTED")
    from psycopg.conninfo import conninfo_to_dict
    configured = _NativePrivateConfig(values)
    for name in ("SOURCE_DSN", "TARGET_DSN"):
        try: options = _NativePrivateConfig(conninfo_to_dict(values.get(name, "")))
        except Exception: raise ValueError("S44_NATIVE_SCOPE_INVALID") from None
        if (not set(options) <= {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout"}
                or options.get("host") != "pg-s44" or options.get("port", "5432") != "5432"
                or not re.fullmatch(r"daon_user_s44_[a-z0-9_]{1,48}", options.get("dbname", ""))
                or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", options.get("user", ""))
                or not options.get("password") or any(c in options["password"] for c in "\r\n\0")
                or options.get("sslmode", "disable") != "disable"):
            raise ValueError("S44_NATIVE_SCOPE_INVALID")
        options.update(port="5432", connect_timeout="5", sslmode="disable")
        configured[name] = options
    source, target = (configured[key] for key in ("SOURCE_DSN", "TARGET_DSN"))
    if (source["dbname"] == target["dbname"] or source["user"] != target["user"]
            or source["user"] == "daon_app"):
        raise ValueError("S44_NATIVE_SCOPE_INVALID")
    for name in ("SOURCE_BUCKET", "TARGET_BUCKET"):
        if not re.fullmatch(r"s44-[a-z0-9][a-z0-9-]{2,54}", values.get(name, "")):
            raise ValueError("S44_NATIVE_SCOPE_INVALID")
    if values["SOURCE_BUCKET"] == values["TARGET_BUCKET"]: raise ValueError("S44_NATIVE_SCOPE_INVALID")
    for name, pattern in (("OWNER", r"[0-9a-f]{32}"), ("PG_DUMP_SHA256", r"[0-9a-f]{64}"),
            ("PG_RESTORE_SHA256", r"[0-9a-f]{64}"), ("IMAGE_DIGEST", r"sha256:[0-9a-f]{64}"),
            ("CODE_REVISION", r"[0-9a-f]{40}"), ("OBJECT_REGION", r"[a-z0-9][a-z0-9-]{0,63}")):
        if not re.fullmatch(pattern, values.get(name, "")): raise ValueError("S44_NATIVE_CONFIG_INVALID")
    if not values.get("OBJECT_ACCESS_KEY") or not values.get("OBJECT_SECRET_KEY"):
        raise ValueError("S44_NATIVE_CONFIG_INVALID")
    return configured


def _native_validate_empty_target(source, target):
    if (sorted(target["schemas"]) != ["information_schema", "pg_catalog", "pg_toast", "public"]
            or target["relations"] or target["unknown_objects"] != 0
            or target["foreign_resources"] != (0, 0, 0)
            or any(target[key] != source[key] for key in ("extensions", "member_functions", "member_types"))):
        raise ValueError("S44_NATIVE_TARGET_NOT_EMPTY")


def _native_target_inventory(connection):
    # extension 자체/회원과 내부 자동 생성 종속물만 허용한다. 단순 사용(n)은 포함하지 않는다.
    allowed = """WITH RECURSIVE roots(classid,objid,objsubid) AS (
        SELECT 'pg_catalog.pg_extension'::regclass,oid,0 FROM pg_catalog.pg_extension
        UNION SELECT classid,objid,objsubid FROM pg_catalog.pg_depend
          WHERE refclassid='pg_catalog.pg_extension'::regclass AND deptype='e'
    ), allowed(classid,objid,objsubid) AS (
        SELECT * FROM roots UNION
        SELECT d.classid,d.objid,d.objsubid FROM pg_catalog.pg_depend d JOIN allowed a
          ON d.refclassid=a.classid AND d.refobjid=a.objid AND d.refobjsubid=a.objsubid
          WHERE d.deptype IN ('i','a')
    ) """
    nonsystem = "n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')"
    members = lambda table, alias: ("EXISTS (SELECT 1 FROM allowed a WHERE a.classid='pg_catalog."
        + table + "'::regclass AND a.objid=" + alias + ".oid AND a.objsubid=0)")
    return {
        "schemas": [row[0] for row in connection.execute("SELECT nspname FROM pg_catalog.pg_namespace ORDER BY 1")],
        "extensions": connection.execute("SELECT e.extname,e.extversion,n.nspname,pg_catalog.pg_get_userbyid(e.extowner) FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace ORDER BY 1").fetchall(),
        "relations": connection.execute("SELECT n.nspname,c.relname,c.relkind FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE " + nonsystem + " ORDER BY 1,2").fetchall(),
        "unknown_objects": connection.execute(allowed + """, addresses AS (
            SELECT DISTINCT d.classid,d.objid,d.objsubid FROM pg_catalog.pg_depend d
              JOIN pg_catalog.pg_namespace n ON d.refclassid='pg_catalog.pg_namespace'::regclass AND d.refobjid=n.oid
              WHERE """ + nonsystem + """
            UNION SELECT 'pg_catalog.pg_proc'::regclass,p.oid,0 FROM pg_catalog.pg_proc p
              JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE """ + nonsystem + """
            UNION SELECT 'pg_catalog.pg_type'::regclass,t.oid,0 FROM pg_catalog.pg_type t
              JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace WHERE """ + nonsystem + """
        ) SELECT count(*)+(SELECT count(*) FROM pg_catalog.pg_event_trigger) FROM addresses x
          WHERE NOT EXISTS (SELECT 1 FROM allowed a WHERE (a.classid,a.objid,a.objsubid)=(x.classid,x.objid,x.objsubid))""").fetchone()[0],
        "member_functions": connection.execute(allowed + """SELECT n.nspname,p.proname,
            pg_catalog.pg_get_function_identity_arguments(p.oid),pg_catalog.pg_get_userbyid(p.proowner),
            p.prokind,p.prosecdef,p.proconfig,
            CASE WHEN p.prokind IN ('f','p') THEN pg_catalog.pg_get_functiondef(p.oid) ELSE p.prokind::text END
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE """ + nonsystem + " AND " + members("pg_proc", "p") + " ORDER BY 1,2,3").fetchall(),
        "member_types": connection.execute(allowed + """SELECT n.nspname,t.typname,
            pg_catalog.pg_get_userbyid(t.typowner),t.typtype,t.typlen,t.typbyval,t.typalign,t.typstorage,
            t.typcategory,t.typnotnull,t.typdefault,pg_catalog.format_type(t.typelem,NULL),
            pg_catalog.format_type(t.typbasetype,NULL),t.typinput::regprocedure::text,
            t.typoutput::regprocedure::text,t.typreceive::regprocedure::text,t.typsend::regprocedure::text
            FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
            WHERE """ + nonsystem + " AND " + members("pg_type", "t") + " ORDER BY 1,2").fetchall(),
        "foreign_resources": connection.execute("SELECT (SELECT count(*) FROM pg_catalog.pg_foreign_server),(SELECT count(*) FROM pg_catalog.pg_foreign_data_wrapper),(SELECT count(*) FROM pg_catalog.pg_largeobject_metadata)").fetchone(),
    }


def _native_trigger_signature(row):
    if len(row) != 20 or type(row[2]) is not bool:
        raise ValueError("S44_NATIVE_TRIGGER_INVALID")
    # 내부 이름/정의의 OID만 비교에서 제외하고 constraint/함수/실속성은 모두 유지한다.
    return (*row[:3], *row[5:]) if row[2] else tuple(row)


def _native_validate_app_sql_identity(row, database, owner, *, baseline_login=None):
    if (not isinstance(row, tuple) or len(row) != 6 or row[:5] != (database, "daon_app", owner, False, False)
            or type(row[3]) is not bool or type(row[4]) is not bool or type(row[5]) is not bool
            or (baseline_login is not None and row[5] is not baseline_login)):
        raise ValueError("S44_NATIVE_APP_SQL_ROLE_INVALID")
    return row[5]


class ServiceSnapshotNativeConfigurationTests(unittest.TestCase):
    def test_native_target_rejects_nonextension_objects_and_unexpected_schema(self):
        check = globals().get("_native_validate_empty_target")
        self.assertTrue(callable(check), "public 밖/비extension 객체도 실제 복원 전에 거부해야 한다")
        source = dict(extensions=[("vector", "0.8.2", "public", "postgres")],
            member_functions=[("public", "vector_in", "cstring", "postgres")], member_types=[("public", "vector", "postgres")])
        target = dict(source, schemas=["information_schema", "pg_catalog", "pg_toast", "public"],
            relations=[], unknown_objects=0, foreign_resources=(0, 0, 0))
        check(source, target)
        for field, value in (("schemas", target["schemas"] + ["other_user"]),
                ("relations", [("other_user", "existing", "r")]),
                ("relations", [("public", "existing", "f")]), ("unknown_objects", 1),
                ("member_functions", source["member_functions"] + [("public", "unowned", "", "postgres")]),
                ("member_types", [("public", "vector", "wrong-owner")]),
                ("extensions", [("vector", "other-version", "public", "postgres")]),
                ("foreign_resources", (1, 0, 0))):
            with self.subTest(field=field), self.assertRaises(ValueError): check(source, {**target, field: value})

    def test_native_internal_trigger_logical_identity_and_duplicate_count(self):
        from collections import Counter
        normalize = globals().get("_native_trigger_signature")
        self.assertTrue(callable(normalize), "내부 RI trigger 이름/OID 대신 실제 논리 속성·중복 개수 비교 필요")
        # schema/table/internal/name/definition 뒤는 실제 constraint/함수/trigger 논리 속성이다.
        logical = ("public", "source_versions", "source_versions_source_id_fk",
            "FOREIGN KEY (tenant_id, workspace_id, source_id) REFERENCES sources(tenant_id, workspace_id, record_id)",
            "public", "sources", "pg_catalog", "RI_FKey_check_ins", "", 5, "O", False, False, "", None)
        a = ("public", "source_versions", True, "RI_ConstraintTrigger_c_123", "OID-dependent-source", *logical)
        b = ("public", "source_versions", True, "RI_ConstraintTrigger_c_456", "OID-dependent-target", *logical)
        self.assertEqual(normalize(a), normalize(b))
        self.assertNotEqual(Counter(map(normalize, [a, a])), Counter(map(normalize, [b])))
        for index, value in ((10, "foreign_table"), (12, "other_function"), (14, 9), (15, "D"), (16, True), (18, "01")):
            changed = list(b); changed[index] = value
            self.assertNotEqual(normalize(a), normalize(tuple(changed)))
        user_a = ("public", "sources", False, "sources_validate", "exact user trigger definition", *logical)
        self.assertNotEqual(normalize(user_a), normalize((*user_a[:3], "other_name", *user_a[4:])))
        self.assertNotEqual(normalize(user_a), normalize((*user_a[:4], "other definition", *user_a[5:])))

    def test_native_app_sql_role_preserves_login_flag_without_authentication_claim(self):
        validate = globals().get("_native_validate_app_sql_identity")
        self.assertTrue(callable(validate), "LOGIN flag는 실제 baseline 보존값이며 SQL 권한 성공 조건이 아님")
        for observed in (True, False):
            row = ("daon_user_s44_target", "daon_app", "s44_owner", False, False, observed)
            self.assertIs(validate(row, "daon_user_s44_target", "s44_owner"), observed)
            self.assertIs(validate(row, "daon_user_s44_target", "s44_owner", baseline_login=observed), observed)
            with self.assertRaises(ValueError):
                validate(row, "daon_user_s44_target", "s44_owner", baseline_login=not observed)
        for row in (("daon_user_s44_target", "s44_owner", "s44_owner", True, True, True),
                ("daon_user_s44_target", "daon_app", "s44_owner", True, False, True),
                ("daon_user_s44_target", "daon_app", "s44_owner", False, True, True)):
            with self.assertRaises(ValueError): validate(row, "daon_user_s44_target", "s44_owner")

    def test_native_formal_or_alias_scope_refused_before_any_io(self):
        # 이 순수 fixture guard의 RED/GREEN은 native PG/MinIO 실행 증거가 아니다.
        values = _NativePrivateConfig({
            "SOURCE_DSN": "host=pg-s44 port=5432 dbname=daon_user_s44_source user=postgres password=" + secrets.token_hex(20),
            "TARGET_DSN": "host=pg-s44 port=5432 dbname=daon_user_s44_target user=postgres password=" + secrets.token_hex(20),
            "SOURCE_BUCKET": "s44-native-source", "TARGET_BUCKET": "s44-native-target",
            "OWNER": "a" * 32, "PG_DUMP_SHA256": "b" * 64, "PG_RESTORE_SHA256": "c" * 64,
            "IMAGE_DIGEST": "sha256:" + "d" * 64, "CODE_REVISION": "e" * 40,
            "OBJECT_ACCESS_KEY": secrets.token_hex(12), "OBJECT_SECRET_KEY": secrets.token_hex(24), "OBJECT_REGION": "us-east-1",
        })
        validate = globals().get("_native_validate_config")
        self.assertTrue(callable(validate), "native fixture의 실제 쓰기 전 소유/scope 거부 guard 필요")
        self.assertIsNotNone(validate(values, environment={}))
        for name in ("PGHOSTADDR", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGPASSFILE",
                "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGSSLMODE", "PGCLIENTENCODING", "PGDATESTYLE", "PGTZ"):
            with self.subTest(environment=name), self.assertRaises(ValueError):
                validate(values, environment={name: ""})
        for field, value in (("SOURCE_DSN", values["SOURCE_DSN"].replace("daon_user_s44_source", "daon_user")),
                ("TARGET_DSN", values["SOURCE_DSN"]),
                ("SOURCE_DSN", values["SOURCE_DSN"].replace("pg-s44", "external.invalid")),
                ("SOURCE_DSN", values["SOURCE_DSN"] + " hostaddr=127.0.0.1"),
                ("SOURCE_DSN", values["SOURCE_DSN"] + " service=formal"),
                ("SOURCE_DSN", values["SOURCE_DSN"].replace("user=postgres", "user=daon_app")),
                ("SOURCE_BUCKET", "formal"), ("TARGET_BUCKET", values["SOURCE_BUCKET"]),
                ("OWNER", ""), ("PG_DUMP_SHA256", "unknown")):
            with self.subTest(field=field), self.assertRaises(ValueError): validate({**values, field: value}, environment={})


def _native_scope(connection, context, notebook):
    for key, value in (("tenant_id", context.tenant_id), ("workspace_id", context.workspace_id),
            ("actor_id", context.actor_id), ("notebook_id", notebook)):
        connection.execute("SELECT set_config(%s,%s,true)", ("app." + key, value))


def _native_service_files(files, folder, name, options):
    # 이 전용 QA 수명에서만 생성한다. 환경/자격을 argv나 평문 archive에 넣지 않는다.
    service, password = folder / (name + ".service"), folder / (name + ".pass")
    text = ("[" + name + "]\nhost=pg-s44\nport=5432\ndbname=" + options["dbname"]
        + "\nuser=" + options["user"] + "\nsslmode=disable\nconnect_timeout=5\n")
    escape = lambda value: value.replace("\\", "\\\\").replace(":", "\\:")
    entry = ":".join(escape(options[key]) for key in ("host", "port", "dbname", "user", "password")) + "\n"
    with files.pin(folder):
        for path, content in ((service, text), (password, entry)):
            with files.file(path, create=True) as handle: handle.write(content.encode())
    return service, password


def _native_seed(connection, context, notebook, objects):
    import json
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    with connection.transaction():
        _native_scope(connection, context, notebook)
        for sql, params in (
            ("INSERT INTO public.tenants(tenant_id,display_name) VALUES(%s,'S44 native synthetic')", (context.tenant_id,)),
            ("INSERT INTO public.workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S44 native synthetic')", (context.tenant_id, context.workspace_id)),
            ("INSERT INTO public.user_accounts(tenant_id,user_id) VALUES(%s,%s)", (context.tenant_id, context.actor_id)),
            ("INSERT INTO public.identity_users(user_id,issuer,subject,state) VALUES(%s,'s44-native',%s,'active')", (context.actor_id, context.actor_id)),
            ("INSERT INTO public.identity_tenants(tenant_id) VALUES(%s)", (context.tenant_id,)),
            ("INSERT INTO public.identity_memberships(tenant_id,user_id,role) VALUES(%s,%s,'organization_admin')", (context.tenant_id, context.actor_id)),
            ("INSERT INTO public.identity_auth_workspaces VALUES(%s,%s,'organization','cloud_sync',1000,1,1,now())", (context.workspace_id, context.tenant_id)),
            ("INSERT INTO public.identity_auth_tenant_roles VALUES(%s,%s,'organization_admin','active',1,now())", (context.tenant_id, context.actor_id)),
            ("INSERT INTO public.identity_auth_tenant_policies VALUES(%s,1,now())", (context.tenant_id,)),
            ("INSERT INTO public.identity_auth_workspace_policies VALUES(%s,%s,1,now())", (context.tenant_id, context.workspace_id)),
            ("INSERT INTO public.notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())", (context.tenant_id, context.workspace_id, notebook, context.actor_id)),
            ("INSERT INTO public.notebook_metadata_versions(tenant_id,workspace_id,notebook_id,version,title,is_current,updated_by,updated_at) VALUES(%s,%s,%s,1,'S44 native synthetic',true,%s,now())", (context.tenant_id, context.workspace_id, notebook, context.actor_id)),
        ): connection.execute(sql, params)
        source_id, version_ids = "s44-native-source", ("s44-native-version-1", "s44-native-version-2")
        Canon._insert(connection, context, "sources", source_id, {"filename": "S44 native synthetic.txt"}, state="registered")
        times = ["created_at"]
        if connection.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='object_records' AND column_name='updated_at'").fetchone():
            times.append("updated_at")
        for index, (ref, key, content) in enumerate(objects):
            connection.execute("INSERT INTO public.object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,cleanup_pending,created_by,trace_id,idempotency_key,request_fingerprint,"
                + ",".join(times) + ") VALUES(%s,%s,%s,'source',%s,%s,%s,%s,'text/plain','completed',false,%s,%s,%s,%s,"
                + ",".join("now()" for _ in times) + ")", (context.tenant_id, context.workspace_id, ref.object_id,
                "s44-native-staging/" + ref.object_id, key, ref.digest, ref.byte_size, context.actor_id, context.trace_id, ref.object_id, ref.digest))
            payload = {"object_id": ref.object_id, "byte_size": ref.byte_size, "content_digest_sha256": ref.digest}
            text = canonical_json_bytes(payload).decode()
            connection.execute("INSERT INTO public.source_versions(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,previous_version_id,canonical_json,canonical_text,digest_sha256,created_by,trace_id,source_id,object_id) VALUES(%s,%s,%s,%s,%s,1,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)",
                (context.tenant_id, context.workspace_id, version_ids[index], source_id, index + 1,
                version_ids[0] if index else None, json.dumps(payload), text, hashlib.sha256(text.encode()).hexdigest(),
                context.actor_id, context.trace_id, source_id, ref.object_id))
        connection.execute("INSERT INTO public.notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())",
            (context.tenant_id, context.workspace_id, notebook, source_id, version_ids[1], context.actor_id))
        connection.execute("INSERT INTO public.identity_auth_source_access VALUES(%s,%s,%s,%s,0,1,now())",
            (context.tenant_id, context.workspace_id, context.actor_id, version_ids[1]))
    return source_id, version_ids


class _NativeRestoreSink:
    """테스트 소유 pg_restore 하나에만 nonblocking stdin/제한 stderr 폐기를 적용한다."""
    def __init__(self, child, deadline):
        self.child, self.deadline, self.stderr_done, self.size = child, deadline, False, 0

    def prepare(self):
        os.set_blocking(self.child.stdin.fileno(), False)
        os.set_blocking(self.child.stderr.fileno(), False)

    def _check(self):
        if time.monotonic() >= self.deadline: raise RuntimeError("S44_NATIVE_RESTORE_TIMEOUT")

    def _drain(self):
        if self.stderr_done: return
        try: data = os.read(self.child.stderr.fileno(), 4096)
        except BlockingIOError: return
        if not data: self.stderr_done = True

    def write(self, value):
        import select
        view = memoryview(value)
        while view:
            self._check()
            if self.child.poll() is not None: raise RuntimeError("S44_NATIVE_RESTORE_FAILED")
            reads = [] if self.stderr_done else [self.child.stderr.fileno()]
            ready_read, ready_write, _ = select.select(reads, [self.child.stdin.fileno()], [], .02)
            if ready_read: self._drain()
            if ready_write:
                try: count = os.write(self.child.stdin.fileno(), view[:65536])
                except BlockingIOError: continue
                if count <= 0: raise RuntimeError("S44_NATIVE_RESTORE_FAILED")
                view = view[count:]; self.size += count

    def finish(self):
        import select
        self.child.stdin.close()
        while self.child.poll() is None or not self.stderr_done:
            self._check()
            if not self.stderr_done:
                if select.select([self.child.stderr.fileno()], [], [], .02)[0]: self._drain()
            else: time.sleep(.005)
        if self.child.wait(timeout=.1) != 0 or self.size == 0: raise RuntimeError("S44_NATIVE_RESTORE_FAILED")

    def close_owned(self):
        import subprocess
        try:
            if self.child.poll() is None:
                self.child.terminate()
                try: self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.child.kill(); self.child.wait(timeout=2)
            self.child.stdin.close(); self.child.stderr.close()
        except Exception: raise RuntimeError("S44_NATIVE_RESTORE_CLEANUP_UNCERTAIN") from None


def _native_read_object(client, bucket, key, maximum):
    response = client.get_object(bucket, key)
    try:
        content = response.read(maximum + 1)
        if len(content) > maximum: raise ValueError("S44_NATIVE_OBJECT_LIMIT")
        return content
    finally:
        try: response.close()
        finally: response.release_conn()


def _native_tool_version(binding, tool):
    import subprocess
    # 검증/봉인한 고정 도구의 공개 version만 읽는다. archive/자격 출력은 없다.
    result = subprocess.run([binding.executable, "--version"], shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, pass_fds=binding.pass_fds, env={})
    if result.returncode != 0 or len(result.stdout) > 256 or not result.stdout.startswith((tool + " (PostgreSQL) 15.").encode()):
        raise ValueError("S44_NATIVE_CLIENT_VERSION_INVALID")


def _native_run(config, phase):
    """실제 API/Popen/SQL/crypto만 사용. MAIN 소유 폐기 cluster/bucket 외 쓰기0."""
    import shutil
    import stat
    import subprocess
    from itertools import islice
    import psycopg
    from minio import Minio
    from urllib3 import PoolManager
    from urllib3.util import Timeout
    from daon_user_api.cloud_storage import CloudAccessContext
    from daon_user_api.object_queue import MinioObjectStorageAdapter, ObjectKeyPolicy
    from daon_user_api.recovery import RecoveryContext
    from daon_user_api.recovery_capture import _PosixFiles
    from daon_user_api.service_snapshot import ServiceSnapshotExecutor, SnapshotObjectRef
    from daon_user_api.service_snapshot_postgres import FixedPgSource, PostgresSnapshotPort, _validate_ancestor_stat
    from daon_user_api.service_snapshot_streams import FixedRuntimeFile, FixedSnapshotStreams
    from daon_user_api.document_index_postgres import _source_access_allowed
    from daon_user_api.open_law_source_access import _current_auth_grant
    from daon_user_api.authorization import Action
    files, parent, root = _PosixFiles(), Path("/qa/runtime"), Path("/qa/runtime/native-evidence")
    marker = "S44-D1-NATIVE-EVIDENCE-20261019:" + config["OWNER"]
    deadline = time.monotonic() + 300
    success, owned = False, None
    def stage(name):
        phase[0] = name
        if time.monotonic() >= deadline: raise RuntimeError("S44_NATIVE_TOTAL_TIMEOUT")
        print("S44_NATIVE_PHASE=" + name, flush=True)
    def connect(name):
        connection = psycopg.connect(**config[name], autocommit=True)
        try: connection.execute("SET statement_timeout='15s'")
        except BaseException:
            connection.close(); raise
        return connection
    stage("PREFLIGHT_READ_ONLY")
    with files.pin(parent):
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
        for ancestor in reversed((parent, *parent.parents)):
            with files.pin(ancestor) as fd: _validate_ancestor_stat(os.fstat(fd), os.geteuid())
        with files.file(parent / "owner.marker") as handle:
            info = os.fstat(handle.fileno())
            if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077 or handle.read(512) != marker.encode(): raise ValueError("S44_NATIVE_OWNER_INVALID")
        if files.exists(root): raise ValueError("S44_NATIVE_ROOT_NOT_NEW")
    with connect("SOURCE_DSN") as source, connect("TARGET_DSN") as target, connect("SOURCE_DSN") as app, ExitStack() as lifetime:
        signatures = []
        for label, connection, name in (("source", source, "SOURCE_DSN"), ("target", target, "TARGET_DSN")):
            row = connection.execute("SELECT current_database(),current_user,r.rolsuper,r.rolbypassrls,pg_get_userbyid(d.datdba),shobj_description(d.oid,'pg_database'),current_setting('server_version_num')::int,inet_server_addr()::text,inet_server_port(),d.oid FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()").fetchone()
            assert row and row[0] == config[name]["dbname"] and row[1] == row[4] == config[name]["user"] and (row[2] or row[3])
            assert row[5] == marker + ":" + label and row[6] // 10000 == 15
            assert row[7] is not None and row[8] == 5432
            signatures.append((row[7], row[8], row[9]))
        assert signatures[0][:2] == signatures[1][:2] and signatures[0][2] != signatures[1][2]
        # 같은 폐기 cluster의 owner 세션에서 역할만 낮춘 SQL probe다. 로그인/role 변경 아님.
        app.execute("SET ROLE daon_app")
        role_login_baseline = _native_validate_app_sql_identity(app.execute("SELECT current_database(),current_user,session_user,rolsuper,rolbypassrls,rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=current_user").fetchone(), config["SOURCE_DSN"]["dbname"], config["SOURCE_DSN"]["user"])
        assert source.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0069",)]
        assert source.execute("SELECT (SELECT count(*) FROM public.tenants)+(SELECT count(*) FROM public.sources)+(SELECT count(*) FROM public.object_records)+(SELECT count(*) FROM public.notebooks)").fetchone() == (0,)
        _native_validate_empty_target(_native_target_inventory(source), _native_target_inventory(target))
        client = Minio("object-s44:9000", access_key=config["OBJECT_ACCESS_KEY"], secret_key=config["OBJECT_SECRET_KEY"],
            secure=False, region=config["OBJECT_REGION"], http_client=PoolManager(retries=False, timeout=Timeout(connect=2, read=2)))
        lifetime.callback(client._http.clear)
        for label, bucket in (("source", config["SOURCE_BUCKET"]), ("target", config["TARGET_BUCKET"])):
            assert _native_read_object(client, bucket, "__s44_owner__", 512) == (marker + ":" + label).encode()
            assert [entry.object_name for entry in islice(client.list_objects(bucket, recursive=True), 2)] == ["__s44_owner__"]
        try:
            with files.pin(parent): files.mkdir(root)
            with files.pin(root):
                owned = files.identity(root)
                files.mkdir(root / "refs"); files.mkdir(root / "artifacts"); files.mkdir(root / "restored-runtime")
            stage("SYNTHETIC_SEED")
            context = RecoveryContext("s44-native-tenant", "s44-native-workspace", "s44-native-actor", "s44-native-trace", "runtime-policy-v1", True)
            notebook, objects = "s44-native-notebook", []
            for number, content in enumerate((b"S44 native first original\n" * 4096, b"S44 native second original\n" * 4096), 1):
                ref = SnapshotObjectRef(context.tenant_id, context.workspace_id, "source", f"{number:032x}", "completed", hashlib.sha256(content).hexdigest(), len(content))
                key = ObjectKeyPolicy().final_key(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "object.read"), ref.area, ref.object_id)
                client.put_object(config["SOURCE_BUCKET"], key, io.BytesIO(content), len(content))
                objects.append((ref, key, content))
            assert sum(len(value) for _, _, value in objects) < 1024 * 1024
            source_id, version_ids = _native_seed(source, context, notebook, objects)
            service, password = _native_service_files(files, root / "refs", "s44-source", config["SOURCE_DSN"])
            base = FixedPgSource("s44-source", "s44-credential", "s44-client", config["SOURCE_DSN"]["dbname"],
                Path("/qa/refs/pg_dump15"), config["PG_DUMP_SHA256"], 15, "s44-source", service, password)
            def connect_as_app(fixed):
                # 실제 봉인 service/pass ref로 연결한 뒤 기존 app 역할로 명시 전환한다.
                connection = app_port._connect_service(fixed)
                try:
                    connection.execute("SET ROLE daon_app")
                    _native_validate_app_sql_identity(connection.execute("SELECT current_database(),current_user,session_user,rolsuper,rolbypassrls,rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=current_user").fetchone(), config["SOURCE_DSN"]["dbname"], config["SOURCE_DSN"]["user"], baseline_login=role_login_baseline)
                    return connection
                except BaseException:
                    connection.close(); raise
            app_port = PostgresSnapshotPort(base, connect=connect_as_app)
            runtime_data, runtime_path = b"S44 synthetic required runtime\n", root / "runtime.bin"
            with files.pin(root):
                with files.file(runtime_path, create=True) as handle: handle.write(runtime_data)
            runtime = SnapshotRuntimeRef("s44-runtime", "bytes", hashlib.sha256(runtime_data).hexdigest(), len(runtime_data))
            storage = MinioObjectStorageAdapter(endpoint="object-s44:9000", bucket=config["SOURCE_BUCKET"],
                access_key=config["OBJECT_ACCESS_KEY"], secret_key=config["OBJECT_SECRET_KEY"], secure=False)
            lifetime.callback(storage._client._http.clear)
            streams = FixedSnapshotStreams.from_storage(storage, source_instance_ref="s44-source", region=config["OBJECT_REGION"], runtime_files=(FixedRuntimeFile(runtime, runtime_path),))
            port = PostgresSnapshotPort(base, streams=streams, runtime_inventory=(runtime,))
            spec = ServiceSnapshotSpec("s44-source", "s44-credential", "s44-client", "s44-root", config["CODE_REVISION"],
                config["IMAGE_DIGEST"], ("s44-runtime",), "s44-memory-v1", "s44-memory-ref", SnapshotLimits(256 * 1024 * 1024, 512 * 1024 * 1024, 32 * 1024 * 1024, 300))
            with port._protected_files(base) as binding: _native_tool_version(binding, "pg_dump")
            stage("APP_FULL_READ_REFUSAL")
            try:
                with app_port.open_snapshot(spec): raise AssertionError("S44_NATIVE_APP_FULL_READ_ACCEPTED")
            except SnapshotError as error: assert error.code == "SNAPSHOT_FULL_READ_PRIVILEGE_REQUIRED"
            readers, dumps, catalogs, mutate = [], [], [], [True]
            class NativePorts:
                # 단순 실제 포트 위임 + 의도한 실제 다른 transaction 변경 시점만 주입한다.
                @contextmanager
                def open_snapshot(inner, value, *, cancelled):
                    with port.open_snapshot(value, cancelled=cancelled) as lease:
                        catalogs.append(lease.catalog)
                        if mutate[0]:
                            with source.transaction():
                                _native_scope(source, context, notebook)
                                row = source.execute("SELECT state,version,outcome,error_code FROM public.transition_canon_state('Source',%s,1,'security_check','s44-native-transition','S44_NATIVE',%s,%s)", (source_id, context.trace_id, context.policy_version)).fetchone()
                                assert row == ("security_check", 2, "succeeded", None)
                            mutate[0] = False
                        yield lease
                @contextmanager
                def dump_archive(inner, lease):
                    with port.dump_archive(lease) as stream:
                        dumps.append(port._child); yield stream
                @contextmanager
                def open_object(inner, ref):
                    with port.open_object(ref) as reader:
                        readers.append(reader); yield reader
                @contextmanager
                def open_runtime(inner, ref):
                    with port.open_runtime(ref) as reader:
                        readers.append(reader); yield reader
            stage("REAL_CAPTURE_EXPORTED_SNAPSHOT")
            master = secrets.token_bytes(32)
            executor = ServiceSnapshotExecutor(root / "artifacts", master_key=master, ports=NativePorts())
            receipt = executor.capture(spec, capture_id="native-capture", cancelled=lambda: time.monotonic() >= deadline)
            assert receipt.schema_revision == "0069" and len(receipt.streams) == 4
            assert len(readers) == 3 and all(reader.exited and reader.status == 0 and reader.closed for reader in readers)
            assert port._child is None and len(dumps) == 1 and dumps[0].returncode == 0
            assert dumps[0].stdout.closed and dumps[0].stderr.closed
            stage("SOURCE_ABSENCE_AND_RESTART_READBACK")
            for _, key, _ in objects: client.remove_object(config["SOURCE_BUCKET"], key)
            with files.pin(root): files.unlink(runtime_path)
            restarted = ServiceSnapshotExecutor(root / "artifacts", master_key=master, ports=None)
            assert restarted.verify(spec, "native-capture") == receipt
            with files.pin(root / "restored-runtime"):
                with files.file(root / "restored-runtime" / "runtime.bin", create=True) as handle:
                    restarted.read_stream(spec, "native-capture", "runtime:s44-runtime", handle)
                with files.file(root / "restored-runtime" / "runtime.bin") as handle: runtime_copy = handle.read(len(runtime_data) + 1)
            assert runtime_copy == runtime_data and hashlib.sha256(runtime_copy).hexdigest() == runtime.digest
            for ref, key, expected in objects:
                copy = io.BytesIO(); restarted.read_stream(spec, "native-capture", ref.label(), copy)
                assert copy.getvalue() == expected and hashlib.sha256(copy.getvalue()).hexdigest() == ref.digest
                client.put_object(config["TARGET_BUCKET"], key, io.BytesIO(copy.getvalue()), len(copy.getvalue()))
                assert _native_read_object(client, config["TARGET_BUCKET"], key, ref.byte_size) == expected
            stage("EMPTY_TARGET_DEFAULT_PG_RESTORE")
            service, password = _native_service_files(files, root / "refs", "s44-target", config["TARGET_DSN"])
            restore_config = replace(base, database_name=config["TARGET_DSN"]["dbname"], executable=Path("/qa/refs/pg_restore15"),
                executable_sha256=config["PG_RESTORE_SHA256"], service_name="s44-target", service_file=service, password_file=password)
            with PostgresSnapshotPort(restore_config)._protected_files(restore_config) as binding:
                _native_tool_version(binding, "pg_restore")
                child = subprocess.Popen([binding.executable, "--exit-on-error", "--dbname=service=s44-target"], shell=False,
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, pass_fds=binding.pass_fds,
                    env={"PGSERVICEFILE": binding.service_file, "PGPASSFILE": binding.password_file, "PGCONNECT_TIMEOUT": "5"})
                sink = _NativeRestoreSink(child, deadline)
                try:
                    sink.prepare()
                    restarted.read_stream(spec, "native-capture", "archive", sink); sink.finish()
                finally:
                    try: sink.close_owned()
                    except RuntimeError:
                        phase[0] = "RESTORE_CLEANUP_UNCERTAIN"; raise
                assert child.returncode == 0
            stage("RESTORED_ROWS_FK_PROTECTION_CURRENT_ACCESS")
            assert target.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("0069",)]
            assert target.execute("SELECT state,version FROM public.sources WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s", (context.tenant_id, context.workspace_id, source_id)).fetchone() == ("registered", 1)
            assert source.execute("SELECT state,version FROM public.sources WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s", (context.tenant_id, context.workspace_id, source_id)).fetchone() == ("security_check", 2)
            restored = target.execute("SELECT record_id,previous_version_id,source_id,object_id FROM public.source_versions WHERE tenant_id=%s AND workspace_id=%s ORDER BY version", (context.tenant_id, context.workspace_id)).fetchall()
            assert restored == [(version_ids[0], None, source_id, objects[0][0].object_id), (version_ids[1], version_ids[0], source_id, objects[1][0].object_id)]
            restored_objects = target.execute("SELECT object_id,object_key,digest_sha256,byte_size,status FROM public.object_records WHERE tenant_id=%s AND workspace_id=%s ORDER BY object_id", (context.tenant_id, context.workspace_id)).fetchall()
            assert restored_objects == [(ref.object_id, key, ref.digest, ref.byte_size, "completed") for ref, key, _ in objects]
            import json
            canon = target.execute("SELECT canonical_text,canonical_json,digest_sha256 FROM public.sources WHERE tenant_id=%s AND workspace_id=%s UNION ALL SELECT canonical_text,canonical_json,digest_sha256 FROM public.source_versions WHERE tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id, context.tenant_id, context.workspace_id)).fetchall()
            assert len(canon) == 3
            assert all(json.loads(text) == payload and hashlib.sha256(text.encode()).hexdigest() == digest for text, payload, digest in canon)
            constraints_sql = """SELECT n.nspname,c.relname,k.conname,k.contype,
                pg_catalog.pg_get_constraintdef(k.oid,true),k.convalidated,k.condeferrable,k.condeferred
                FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' ORDER BY 1,2,3"""
            expected = source.execute(constraints_sql).fetchall()
            assert expected and target.execute(constraints_sql).fetchall() == expected
            # 사용자 trigger는 이름/정의까지, 내부 trigger는 FK/함수/속성별 중복 수까지 비교한다.
            triggers_sql = """SELECT n.nspname,c.relname,t.tgisinternal,t.tgname,
                pg_catalog.pg_get_triggerdef(t.oid,true),kn.nspname,kc.relname,k.conname,
                pg_catalog.pg_get_constraintdef(k.oid,true),rn.nspname,rc.relname,
                pn.nspname,p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),
                t.tgtype,t.tgenabled,t.tgdeferrable,t.tginitdeferred,encode(t.tgargs,'hex'),
                pg_catalog.pg_get_expr(t.tgqual,t.tgrelid,true)
                FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
                LEFT JOIN pg_catalog.pg_constraint k ON k.oid=t.tgconstraint
                LEFT JOIN pg_catalog.pg_class kc ON kc.oid=k.conrelid
                LEFT JOIN pg_catalog.pg_namespace kn ON kn.oid=kc.relnamespace
                LEFT JOIN pg_catalog.pg_class rc ON rc.oid=k.confrelid
                LEFT JOIN pg_catalog.pg_namespace rn ON rn.oid=rc.relnamespace
                WHERE n.nspname='public'"""
            from collections import Counter
            expected = Counter(map(_native_trigger_signature, source.execute(triggers_sql).fetchall()))
            actual = Counter(map(_native_trigger_signature, target.execute(triggers_sql).fetchall()))
            assert expected and actual == expected
            flags = target.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_catalog.pg_class WHERE oid=ANY(ARRAY['public.sources'::regclass,'public.source_versions'::regclass,'public.object_records'::regclass,'public.notebooks'::regclass])").fetchall()
            assert len(flags) == 4 and all(enabled and forced for _, enabled, forced in flags)
            with target.transaction():
                target.execute("SET LOCAL search_path=pg_catalog")
                current_catalog = PostgresSnapshotPort._catalog(target, 32 * 1024 * 1024)
            for row in current_catalog["relations"]:
                if row[0] == "database": row[1] = config["SOURCE_DSN"]["dbname"]
            assert current_catalog == catalogs[0]  # 같은 cluster의 global role 재생성 성공은 아님.
            with connect("TARGET_DSN") as restored_app, restored_app.transaction():
                restored_app.execute("SET LOCAL ROLE daon_app")
                _native_validate_app_sql_identity(restored_app.execute("SELECT current_database(),current_user,session_user,rolsuper,rolbypassrls,rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=current_user").fetchone(), config["TARGET_DSN"]["dbname"], config["TARGET_DSN"]["user"], baseline_login=role_login_baseline)
                _native_scope(restored_app, context, notebook)
                assert restored_app.execute("SELECT count(*) FROM public.source_versions WHERE tenant_id=%s AND workspace_id=%s", (context.tenant_id, context.workspace_id)).fetchone() == (2,)
                assert _current_auth_grant(context, restored_app, Action.POLICY_MANAGE).actor_id == context.actor_id
                assert _source_access_allowed(restored_app, context, version_ids[0]) is True
                assert _source_access_allowed(restored_app, context, version_ids[1]) is False
                restored_app.execute("SELECT set_config('app.workspace_id','s44-native-foreign',true)")
                assert restored_app.execute("SELECT count(*) FROM public.source_versions").fetchone() == (0,)
                try:
                    with restored_app.transaction():
                        restored_app.execute("INSERT INTO public.notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,'s44-forbidden',%s,now())", (context.tenant_id, context.workspace_id, context.actor_id))
                except psycopg.errors.InsufficientPrivilege: pass
                else: raise AssertionError("S44_NATIVE_FOREIGN_WRITE_ACCEPTED")
            stage("REAL_MISSING_AND_CORRUPT_OBJECT_REFUSAL")
            for mode in ("missing", "corrupt"):
                if mode == "corrupt":
                    ref, key, expected = objects[0]
                    client.put_object(config["SOURCE_BUCKET"], key, io.BytesIO(b"x" * len(expected)), len(expected))
                readers.clear()
                try: executor.capture(spec, capture_id="native-" + mode, cancelled=lambda: time.monotonic() >= deadline)
                except SnapshotError: pass
                else: raise AssertionError("S44_NATIVE_CORRUPT_CAPTURE_ACCEPTED")
                # 실제 worker가 시작/회수되고 corrupt는 GET 전체 성공 뒤 hash에서 거부돼야 한다.
                assert len(readers) == 1 and readers[0].pid is not None and readers[0].closed and readers[0].exited
                assert (readers[0].status == 0) == (mode == "corrupt")
                assert not (root / "artifacts" / ("native-" + mode) / "receipt.bin").exists()
                assert not (root / "artifacts" / "service-snapshot.lock").exists()
            stage("OWNED_CLEANUP")
            assert all(child.returncode == 0 and child.stdout.closed and child.stderr.closed for child in dumps)
            for bucket in (config["SOURCE_BUCKET"], config["TARGET_BUCKET"]):
                for _, key, _ in objects: client.remove_object(bucket, key)
                assert [entry.object_name for entry in islice(client.list_objects(bucket, recursive=True), 2)] == ["__s44_owner__"]
            success = True
        finally:
            # 실패 원장/append-only 행은 정식처럼 지우지 않는다. MAIN이 전용 cluster/bucket을 회수한다.
            # 실패/종료 미확정 보존본은 자동 삭제0. 성공 때만 정확 inode의 소유 폴더를 회수한다.
            if success and owned is not None:
                with files.pin(root):
                    assert files.identity(root) == owned and root == parent / "native-evidence"
                shutil.rmtree(root)
                assert not root.exists()


@unittest.skipUnless(os.name == "posix" and sys.platform.startswith("linux") and os.environ.get("DAON_S44_NATIVE_RUN") == "1",
    "NOT RUN: MAIN GO·Linux/PG15/MinIO 소유 격리 환경 명시 주입 필요")
class ServiceSnapshotNativeIntegrationTests(unittest.TestCase):
    def test_native_pg15_minio_capture_restore_and_current_access(self):
        # 외부 오류/SQL params/Secret locals는 재반사하지 않고 실패 phase만 전달한다.
        phase = ["CONFIG_READ_ONLY"]
        try:
            config = _native_validate_config(_NativePrivateConfig({name.removeprefix("DAON_S44_NATIVE_"): value
                for name, value in os.environ.items() if name.startswith("DAON_S44_NATIVE_")}))
            _native_run(config, phase)
        except Exception:
            raise AssertionError("S44_NATIVE_" + phase[0] + "_FAILED; " + _native_safe_failure()
                + "; SOURCE/TARGET_QA_ONLY; MAIN_OWNED_CLEANUP_REQUIRED") from None


def _native_safe_failure():
    # 예외 객체 자체를 outer test locals/pytest --showlocals에 보존하지 않는다.
    import psycopg
    error = sys.exception()
    trace = error.__traceback__
    while trace.tb_next is not None: trace = trace.tb_next
    location = Path(trace.tb_frame.f_code.co_filename).name + ":" + str(trace.tb_lineno)
    if isinstance(error, SnapshotError) and re.fullmatch(r"SNAPSHOT_[A-Z_]+", error.code): return error.code + ";AT=" + location
    if isinstance(error, psycopg.Error) and error.sqlstate and re.fullmatch(r"[A-Z0-9]{5}", error.sqlstate):
        return "SQLSTATE=" + error.sqlstate + ";AT=" + location
    return "ASSERTION_OR_CONFIG_OR_IO_FAILURE;AT=" + location


if __name__ == "__main__": unittest.main()
