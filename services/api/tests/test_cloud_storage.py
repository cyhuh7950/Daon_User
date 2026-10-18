from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from daon_user_api.cloud_storage import (
    CloudAccessContext,
    CloudDatabaseError,
    PostgresCloudStore,
    _EXPECTED_SCHEMA_REVISION,
    classify_database_error,
)
from daon_user_api.cloud_admin import server_version_supported


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "services" / "api" / "migrations" / "versions" / "0001_cloud_foundation.py"
CLOUD_STORAGE = ROOT / "services" / "api" / "src" / "daon_user_api" / "cloud_storage.py"
DEFAULT_DENY_CANONICAL_TEXT = (
    '{"allowed_destinations":[],"allowed_provider_kinds":[],'
    '"classification":"restricted","masking_required":true,"max_bytes":0,'
    '"mode":"deny_external","redaction_required":true,'
    '"required_approver":"organization_admin"}'
)
DEFAULT_DENY_DIGEST = "caf695f3de7e3e05feb024b3ff4b8b14cbfad5318b885ac15d8e4da25b819d7f"


class CloudStorageContractTests(unittest.TestCase):
    def test_r4_readiness_0070_requires_all_protections_in_both_pool_states(self):
        class Result:
            def __init__(self,row):self.row=row
            def fetchone(self):return self.row
        class Connection:
            def execute(self,sql,params=()):return Result(("0070",) if "alembic_version" in sql else ("0.8.2",))
        class Pool:
            def __init__(self,closed):self.closed=closed
            @contextmanager
            def connection(self,**kwargs):yield Connection()
        @contextmanager
        def connect(*args,**kwargs):yield Connection()
        for closed in (True,False):
            for missing in (None,"organization","notification","registered"):
                with self.subTest(closed=closed,missing=missing):
                    store=object.__new__(PostgresCloudStore);store._dsn="not-a-real-connection";store._pool=Pool(closed)
                    store._organization_storage_is_protected=lambda c:missing!="organization"
                    store._notification_storage_is_protected=lambda c:missing!="notification"
                    store._registered_restore_storage_is_protected=lambda c:missing!="registered"
                    opened=[];store._ensure_pool_open=lambda:opened.append(True)
                    with patch("daon_user_api.cloud_storage.connect",connect):status=store.readiness()
                    self.assertEqual(status.ready,missing is None)
                    self.assertEqual(bool(opened),closed and missing is None)

    def test_r4_registered_catalog_guard_checks_exact_migration_and_rejects_each_missing_facet(self):
        import hashlib,re
        from daon_user_api import cloud_storage as api
        guard=getattr(PostgresCloudStore,"_registered_restore_storage_is_protected",None)
        self.assertTrue(callable(guard),"0070 actual protected catalog consumer missing")
        migration=(MIGRATION.parent/"0070_registered_restore_destinations.sql").read_text(encoding="utf-8")
        for signature,expected in api._REGISTERED_RESTORE_FUNCTIONS.items():
            name=signature.split("(")[0]
            match=re.search(r"CREATE FUNCTION public\."+name+r"\([^$]+?AS \$\$(.*?)\$\$;",migration,re.S)
            self.assertIsNotNone(match)
            self.assertEqual(hashlib.md5(match[1].encode()).hexdigest(),expected[0])
        for bad in (None,"role","table","columns","constraints","policy","function","trigger","owner","grant","rls","body","disabled-trigger","global-composite-unique"):
            queries=[]
            class Result:
                def __init__(self,rows):self.rows=rows
                def fetchone(self):return self.rows[0] if self.rows else None
                def fetchall(self):return self.rows
            class Reader:
                def execute(self,sql,params=()):
                    queries.append(sql)
                    facet=sql.split("/* r4:",1)[1].split(" */",1)[0]
                    if bad==facet:return Result([])
                    if facet=="table" and bad in {"owner","grant","rls"}:return Result([(False,)])
                    if facet=="columns":return Result(api._REGISTERED_RESTORE_COLUMNS[params[0]])
                    if facet=="constraints":
                        rows=api._REGISTERED_RESTORE_KEYS[params[0]]
                        if bad=="global-composite-unique" and params[0]==api._R2_RES:
                            rows=[(r[0],[*r[1],"tenant_id"],*r[2:]) if r[0]=="u" and r[1]==["target_database_fingerprint"] else r for r in rows]
                        return Result(rows)
                    if facet=="function":
                        expected=api._REGISTERED_RESTORE_FUNCTIONS[params[0].removeprefix("public.")]
                        return Result([("0"*32 if bad=="body" else expected[0],*expected[1:],True,True,True,True)])
                    if facet=="policy":return Result(api._REGISTERED_RESTORE_POLICIES[params[0]])
                    if facet=="trigger":return Result([(*r[:3],"D",*r[4:]) for r in api._REGISTERED_RESTORE_TRIGGERS] if bad=="disabled-trigger" else api._REGISTERED_RESTORE_TRIGGERS)
                    return Result([(True,)])
            with self.subTest(facet=bad):self.assertEqual(guard(Reader()),bad is None)
            self.assertTrue(all("pg_catalog" in query for query in queries))

    def test_s39_readiness_requires_0068_protection_and_flag_binding_in_both_pool_states(self):
        class Result:
            def __init__(self, row): self.row = row
            def fetchone(self): return self.row
        class Connection:
            def __init__(self, revision, vector): self.revision, self.vector = revision, vector
            def execute(self, query, _params=()):
                if "alembic_version" in query: return Result((self.revision,))
                if "pg_extension" in query: return Result(None if self.vector is None else (self.vector,))
                raise AssertionError(query)
        class Pool:
            def __init__(self, closed, connection): self.closed, self.conn = closed, connection
            @contextmanager
            def connection(self, **_kwargs): yield self.conn
        for closed in (True, False):
            for revision in ("0062", "0063", "0064", "0066", "0067", "0068", "0065", "0069", "unknown"):
                for enabled in (True, False):
                    for protected in (True, False):
                        connection = Connection(revision, "0.8.2")
                        store = object.__new__(PostgresCloudStore)
                        store._pool = Pool(closed, connection); store._dsn = "시험 전용"
                        store._organization_admin_v2_enabled = enabled
                        checked, opened = [], []
                        store._organization_storage_is_protected = lambda conn: checked.append(conn) or protected
                        store._notification_storage_is_protected = lambda conn: False
                        store._ensure_pool_open = lambda: opened.append(True)
                        @contextmanager
                        def fake_connect(*_args, **_kwargs): yield connection
                        with patch("daon_user_api.cloud_storage.connect", fake_connect): status = store.readiness()
                        expected = (revision == "0068" and protected) or (not enabled and revision in {"0062", "0063", "0064", "0066", "0067"})
                        self.assertEqual(status.ready, expected, (closed, revision, enabled, protected))
                        self.assertEqual(bool(checked), revision in {"0068", "0069"})
                        self.assertEqual(bool(opened), closed and expected)
            connection = Connection("0068", None)
            store._pool = Pool(closed, connection)
            with patch("daon_user_api.cloud_storage.connect", fake_connect): self.assertFalse(store.readiness().ready)

    def test_s24_lifecycle_readiness_accepts_exact_rolling_revisions_and_rejects_reserved_future(self):
        class Result:
            def __init__(self,row): self.row=row
            def fetchone(self): return self.row
        class Pool:
            closed=False
            def __init__(self,revision,vector): self.revision,self.vector=revision,vector
            @contextmanager
            def connection(self,**_kwargs):
                current=self
                class Connection:
                    def execute(self,sql,_params=()): return Result((current.revision,) if "alembic_version" in sql else ((current.vector,) if current.vector else None))
                yield Connection()
        self.assertEqual(_EXPECTED_SCHEMA_REVISION,"0067")
        store=object.__new__(PostgresCloudStore)
        for revision in ("0062","0063","0064","0066","0067","0065","0068","unknown"):
            for vector in (None,"0.8.2"):
                store._pool=Pool(revision,vector)
                self.assertEqual(store.readiness().ready,vector is not None and revision in {"0062","0063","0064","0066","0067"})

    def test_s16_readiness_tracks_source_cleanup_migration_head(self) -> None:
        self.assertEqual(_EXPECTED_SCHEMA_REVISION, "0067")

    def test_s16_readiness_accepts_only_0062_0063_and_0064_with_vector(self) -> None:
        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self, revision, vector):
                self.revision, self.vector = revision, vector

            def execute(self, query, _params=()):
                if query == "SELECT version_num FROM alembic_version":
                    return Result((self.revision,))
                if "FROM pg_extension" in query:
                    return Result(None if self.vector is None else (self.vector,))
                raise AssertionError(f"unexpected readiness query: {query}")

        class Pool:
            closed = False

            def __init__(self, revision, vector="0.8.2"):
                self.revision, self.vector = revision, vector

            @contextmanager
            def connection(self, *, timeout):
                self.timeout = timeout
                yield Connection(self.revision, self.vector)

        store = object.__new__(PostgresCloudStore)
        for revision, expected_ready in (("0064", True), ("0063", True), ("0062", True), ("0065", False), ("0061", False), ("0060", False), ("0059", False), ("0058", False), ("0057", False), ("0056", False)):
            store._pool = Pool(revision)
            status = store.readiness()
            self.assertEqual(status.ready, expected_ready)
            self.assertEqual(status.schema_revision, revision)
            self.assertEqual(status.vector_version, "0.8.2")
        for revision in ("0062", "0063", "0064"):
            store._pool = Pool(revision, None)
            self.assertFalse(store.readiness().ready)

    def test_s16_closed_pool_opens_only_after_compatible_head_is_ready(self) -> None:
        class Result:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self, revision):
                self.revision = revision

            def execute(self, query, _params=()):
                if query == "SELECT version_num FROM alembic_version":
                    return Result((self.revision,))
                if "FROM pg_extension" in query:
                    return Result(("0.8.2",))
                raise AssertionError(query)

        class Pool:
            closed = True

        store = object.__new__(PostgresCloudStore)
        store._pool = Pool()
        opened = []
        store._ensure_pool_open = lambda: opened.append(True)

        for revision, expected_ready in (("0059", False), ("0060", False), ("0061", False), ("0062", True), ("0063", True), ("0064", True), ("0065", False)):
            opened.clear()
            @contextmanager
            def connect_stub(_dsn, *, connect_timeout):
                self.assertEqual(connect_timeout, 1)
                yield Connection(revision)

            store._dsn = "unused-test-dsn"
            with patch("daon_user_api.cloud_storage.connect", connect_stub):
                status = store.readiness()
            self.assertEqual(status.ready, expected_ready)
            self.assertEqual(status.schema_revision, revision)
            self.assertEqual(len(opened), int(expected_ready))

    def test_postgres_major_version_range_accepts_packaging_suffix(self) -> None:
        for value in ("15.13", "16.9 (Debian 16.9-1.pgdg12+1)", "17.5", "18.4"):
            self.assertTrue(server_version_supported(value), value)
        for value in ("14.12", "19.0", "18", "postgres 18.4"):
            self.assertFalse(server_version_supported(value), value)

    def test_access_context_rejects_untrusted_or_empty_scope(self) -> None:
        with self.assertRaises(ValueError):
            CloudAccessContext(tenant_id="", workspace_id="workspace-a", actor_id="user-a", capability="view")
        with self.assertRaises(ValueError):
            CloudAccessContext(tenant_id="tenant-a", workspace_id="workspace-a", actor_id="user-a", capability="")

    def test_database_errors_are_safe_and_bounded(self) -> None:
        for sqlstate, code, retryable in (
            ("57014", "DATABASE_TIMEOUT", True),
            ("40P01", "DATABASE_RETRYABLE_CONFLICT", True),
            ("40001", "DATABASE_RETRYABLE_CONFLICT", True),
            ("23505", "DATABASE_CONSTRAINT_VIOLATION", False),
            ("42501", "DATABASE_ACCESS_DENIED", False),
            (None, "DATABASE_UNAVAILABLE", True),
        ):
            error = classify_database_error(sqlstate)
            self.assertIsInstance(error, CloudDatabaseError)
            self.assertEqual(error.code, code)
            self.assertEqual(error.retryable, retryable)
            self.assertNotIn("postgres", str(error).lower())

    def test_migration_declares_rls_atomicity_and_vector_contract(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            "CREATE EXTENSION IF NOT EXISTS vector",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "current_setting('app.tenant_id', true)",
            "current_setting('app.workspace_id', true)",
            "CREATE TRIGGER audit_events_immutable",
            "vector(3)",
            "idempotency_records",
            "PRIMARY KEY (tenant_id, workspace_id, actor_id, operation, idempotency_key)",
        ):
            self.assertIn(token, source)

    def test_seed_scope_declares_canonical_default_deny_policy_contract(self) -> None:
        source = CLOUD_STORAGE.read_text(encoding="utf-8")
        self.assertIn(DEFAULT_DENY_CANONICAL_TEXT, source)
        self.assertIn(DEFAULT_DENY_DIGEST, source)
        self.assertIn("egress-backfill-policy:", source)
        self.assertIn("egress-backfill-binding:", source)
        self.assertIn("egress_policy_versions", source)
        self.assertIn("egress_policy_bindings", source)
        self.assertIn("NOT EXISTS", source)

    def test_seed_scope_binds_every_sql_placeholder_once(self) -> None:
        class RecordingConnection:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
                self.calls.append((query, params))

        connection = RecordingConnection()
        store = object.__new__(PostgresCloudStore)

        @contextmanager
        def transaction(_context):
            yield connection

        store._transaction = transaction  # type: ignore[method-assign]
        context = CloudAccessContext(
            tenant_id="tenant-test",
            workspace_id="workspace-test",
            actor_id="user-test",
            capability="test",
        )

        store.seed_scope(context)

        self.assertGreaterEqual(len(connection.calls), 5)
        for query, params in connection.calls:
            self.assertEqual(
                query.count("%s"),
                len(params),
                msg=f"placeholder/parameter mismatch in query: {query[:120]}",
            )


@unittest.skipUnless(os.environ.get("DAON_TEST_POSTGRES_DSN"), "isolated PostgreSQL DSN required")
class PostgresCloudIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PostgresCloudStore(os.environ["DAON_TEST_POSTGRES_DSN"])
        suffix = self._testMethodName
        self.tenant_a = CloudAccessContext(
            f"tenant-a-{suffix}", f"workspace-a-{suffix}", f"user-a-{suffix}", "notification.read"
        )
        self.tenant_b = CloudAccessContext(
            f"tenant-b-{suffix}", f"workspace-b-{suffix}", f"user-b-{suffix}", "notification.read"
        )

    def tearDown(self) -> None:
        self.store.close()

    def test_readiness_requires_migration_and_vector(self) -> None:
        status = self.store.readiness()
        self.assertTrue(status.ready)
        self.assertEqual(status.schema_revision, "0064")
        self.assertEqual(status.vector_version, "0.8.2")

    def test_rls_blocks_cross_tenant_and_context_does_not_leak(self) -> None:
        self.store.seed_scope(self.tenant_a)
        self.store.seed_scope(self.tenant_b)
        self.store.put_vector(self.tenant_a, "vector-a", [1.0, 0.0, 0.0])
        self.assertEqual(self.store.get_vector(self.tenant_a, "vector-a"), (1.0, 0.0, 0.0))
        self.assertIsNone(self.store.get_vector(self.tenant_b, "vector-a"))
        self.assertTrue(self.store.context_is_clear())

    def test_seed_scope_creates_idempotent_current_default_deny_policies(self) -> None:
        self.store.seed_scope(self.tenant_a)
        self.store.seed_scope(self.tenant_a)

        with self.store._transaction(self.tenant_a) as connection:
            rows = connection.execute(
                "SELECT version.scope_type, version.workspace_id, "
                "version.policy_version_id, binding.binding_id, "
                "version.canonical_text, version.digest_sha256 "
                "FROM egress_policy_bindings AS binding "
                "JOIN egress_policy_versions AS version "
                "ON version.tenant_id = binding.tenant_id "
                "AND version.policy_version_id = binding.policy_version_id "
                "WHERE binding.tenant_id = %s AND binding.current "
                "ORDER BY version.scope_type",
                (self.tenant_a.tenant_id,),
            ).fetchall()
            expected_ids = connection.execute(
                "SELECT "
                "'egress-backfill-policy:' || md5(%s || ':organization'), "
                "'egress-backfill-binding:' || md5(%s || ':organization'), "
                "'egress-backfill-policy:' || md5(%s || ':' || %s), "
                "'egress-backfill-binding:' || md5(%s || ':' || %s)",
                (
                    self.tenant_a.tenant_id,
                    self.tenant_a.tenant_id,
                    self.tenant_a.tenant_id,
                    self.tenant_a.workspace_id,
                    self.tenant_a.tenant_id,
                    self.tenant_a.workspace_id,
                ),
            ).fetchone()

        self.assertIsNotNone(expected_ids)
        self.assertEqual(len(rows), 2)
        by_scope = {str(row[0]): row for row in rows}
        self.assertEqual(by_scope["organization"][1], None)
        self.assertEqual(by_scope["organization"][2:4], expected_ids[0:2])
        self.assertEqual(by_scope["workspace"][1], self.tenant_a.workspace_id)
        self.assertEqual(by_scope["workspace"][2:4], expected_ids[2:4])
        for row in rows:
            self.assertEqual(row[4], DEFAULT_DENY_CANONICAL_TEXT)
            self.assertEqual(row[5], DEFAULT_DENY_DIGEST)

    def test_seed_scope_preserves_existing_current_policies(self) -> None:
        context = self.tenant_b
        existing = (
            ("organization", None, "custom-policy-organization", "custom-binding-organization"),
            ("workspace", context.workspace_id, "custom-policy-workspace", "custom-binding-workspace"),
        )
        with self.store._transaction(context) as connection:
            connection.execute(
                "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s)",
                (context.tenant_id, context.tenant_id),
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id, workspace_id, display_name) VALUES (%s, %s, %s)",
                (context.tenant_id, context.workspace_id, context.workspace_id),
            )
            for scope_type, workspace_id, policy_id, binding_id in existing:
                connection.execute(
                    "INSERT INTO egress_policy_versions "
                    "(tenant_id, organization_id, workspace_id, policy_version_id, scope_type, "
                    "policy_version, state, canonical_json, canonical_text, digest_sha256, created_by, trace_id) "
                    "VALUES (%s, %s, %s, %s, %s, 1, 'active', %s::jsonb, %s, %s, %s, %s)",
                    (
                        context.tenant_id,
                        context.tenant_id,
                        workspace_id,
                        policy_id,
                        scope_type,
                        DEFAULT_DENY_CANONICAL_TEXT,
                        DEFAULT_DENY_CANONICAL_TEXT,
                        DEFAULT_DENY_DIGEST,
                        "test:custom-policy",
                        f"test:custom-policy:{scope_type}",
                    ),
                )
                connection.execute(
                    "INSERT INTO egress_policy_bindings "
                    "(tenant_id, organization_id, workspace_id, binding_id, scope_type, "
                    "policy_version_id, binding_version, active, current, created_by, trace_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 1, true, true, %s, %s)",
                    (
                        context.tenant_id,
                        context.tenant_id,
                        workspace_id,
                        binding_id,
                        scope_type,
                        policy_id,
                        "test:custom-policy",
                        f"test:custom-policy:{scope_type}",
                    ),
                )

        self.store.seed_scope(context)

        with self.store._transaction(context) as connection:
            rows = connection.execute(
                "SELECT version.policy_version_id, binding.binding_id "
                "FROM egress_policy_bindings AS binding "
                "JOIN egress_policy_versions AS version "
                "ON version.tenant_id = binding.tenant_id "
                "AND version.policy_version_id = binding.policy_version_id "
                "WHERE binding.tenant_id = %s AND binding.current "
                "ORDER BY version.scope_type",
                (context.tenant_id,),
            ).fetchall()
            version_count = connection.execute(
                "SELECT count(*) FROM egress_policy_versions WHERE tenant_id = %s",
                (context.tenant_id,),
            ).fetchone()

        self.assertEqual(
            rows,
            [
                ("custom-policy-organization", "custom-binding-organization"),
                ("custom-policy-workspace", "custom-binding-workspace"),
            ],
        )
        self.assertIsNotNone(version_count)
        self.assertEqual(version_count[0], 2)

    def test_notification_audit_and_idempotency_are_atomic(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-a", "event-a")
        first = self.store.mark_notification_read(self.tenant_a, created.notification_id, 1, "idem-cloud-read-0001")
        replay = self.store.mark_notification_read(self.tenant_a, created.notification_id, 1, "idem-cloud-read-0001")
        self.assertEqual(first, replay)
        self.assertEqual(self.store.audit_count(self.tenant_a, "notification.read"), 1)
        self.assertEqual(self.store.idempotency_count(self.tenant_a, "idem-cloud-read-0001"), 1)

    def test_same_key_concurrency_replays_one_result_and_one_audit(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-c", "event-c")
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = tuple(executor.map(
                lambda _: self.store.mark_notification_read(
                    self.tenant_a, created.notification_id, 1, "idem-cloud-read-0003"
                ),
                range(8),
            ))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.store.audit_count(self.tenant_a, "notification.read"), 1)

    def test_different_key_same_version_allows_one_winner(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-d", "event-d")

        def attempt(index: int) -> str:
            try:
                self.store.mark_notification_read(
                    self.tenant_a, created.notification_id, 1, f"idem-cloud-compete-{index:04d}"
                )
                return "success"
            except CloudDatabaseError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = tuple(executor.map(attempt, range(8)))
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("VERSION_CONFLICT"), 7)
        self.assertEqual(self.store.audit_count(self.tenant_a, "notification.read"), 1)

    def test_audit_failure_rolls_back_state_and_idempotency(self) -> None:
        self.store.seed_scope(self.tenant_a)
        created = self.store.create_notification(self.tenant_a, "notification-b", "event-b")
        with self.assertRaises(CloudDatabaseError):
            self.store.mark_notification_read(
                self.tenant_a,
                created.notification_id,
                1,
                "idem-cloud-read-0002",
                force_audit_failure=True,
            )
        current = self.store.get_notification(self.tenant_a, created.notification_id)
        self.assertEqual(current.version, 1)
        self.assertIsNone(current.read_at)
        self.assertEqual(self.store.idempotency_count(self.tenant_a, "idem-cloud-read-0002"), 0)
        self.assertTrue(self.store.context_is_clear())

    def test_same_tenant_actor_key_isolated_by_workspace(self) -> None:
        suffix = self._testMethodName
        first = CloudAccessContext(
            f"tenant-shared-{suffix}", f"workspace-one-{suffix}", f"user-shared-{suffix}",
            "notification.read",
        )
        second = CloudAccessContext(
            first.tenant_id, f"workspace-two-{suffix}", first.actor_id, "notification.read"
        )
        self.store.seed_scope(first)
        self.store.seed_scope(second)
        first_item = self.store.create_notification(first, "notification-ws-one", "event-ws-one")
        second_item = self.store.create_notification(second, "notification-ws-two", "event-ws-two")
        key = "idem-shared-workspace-0001"
        self.store.mark_notification_read(first, first_item.notification_id, 1, key)
        self.store.mark_notification_read(second, second_item.notification_id, 1, key)
        self.assertEqual(self.store.idempotency_count(first, key), 1)
        self.assertEqual(self.store.idempotency_count(second, key), 1)
        self.assertEqual(self.store.audit_count(first, "notification.read"), 1)
        self.assertEqual(self.store.audit_count(second, "notification.read"), 1)
        with self.assertRaises(CloudDatabaseError):
            self.store.get_notification(second, first_item.notification_id)
        self.assertTrue(self.store.context_is_clear())


if __name__ == "__main__":
    unittest.main()
