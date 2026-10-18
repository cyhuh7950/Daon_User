"""D1 B2 직접 SQL/프로세스 포트 시험. 실제 pg_dump/PG 실행이 아니다."""
from __future__ import annotations

import hashlib
import importlib.util
import io
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


if __name__ == "__main__": unittest.main()
