"""Explicit S16 isolated PostgreSQL only; object I/O is a failure-capable port.

Admin credentials seed synthetic rows only. Every product connection SET ROLEs
to daon_app. No remote Provider, model, real Source, or real MinIO is used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from runpy import run_path
from threading import Event
import time
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.notebook import NotebookContext, NotebookError
from daon_user_api.notebook_postgres import PostgresNotebookRepository
from daon_user_api.object_queue import ObjectKeyPolicy, ObjectStorageError
from daon_user_api.retention import RetentionContext, RetentionError
from daon_user_api.retention_inventory_postgres import PostgresRetentionInventoryProvider
from daon_user_api.retention_request_postgres import PostgresRetentionRequestService


@pytest.fixture
def pg():
    dsn = os.environ.get("DAON_S16_ISOLATED_POSTGRES_DSN")
    if not dsn:
        pytest.skip("NOT RUN: explicit DAON_S16_ISOLATED_POSTGRES_DSN required; not PostgreSQL PASS")
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT current_database()").fetchone()[0] == "daon_user_s16_qa_20261016"
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] in ("0063", "0064")
    app_dsn = make_conninfo(dsn, options="-c role=daon_app")
    with psycopg.connect(app_dsn) as c:
        assert c.execute("SELECT current_user").fetchone()[0] == "daon_app"
    return SimpleNamespace(admin=dsn, app=app_dsn)


def scoped(c, ctx, notebook_id):
    for key, value in (("tenant_id", ctx.tenant_id), ("workspace_id", ctx.workspace_id),
                       ("actor_id", ctx.actor_id), ("notebook_id", notebook_id)):
        c.execute("SELECT set_config(%s,%s,true)", ("app." + key, value))


def canon(c, ctx, table, record, payload, *, extra="", params=()):
    # table/extra are fixed test literals; user input is always parameterized.
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    c.execute(f"INSERT INTO {table}(tenant_id,workspace_id,record_id,aggregate_id,version,canonical_json,canonical_text,digest_sha256,created_by,trace_id{extra}) "
        f"VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s,%s{',%s' * len(params)})",
        (ctx.tenant_id, ctx.workspace_id, record, record, Jsonb(payload), text, hashlib.sha256(text.encode()).hexdigest(), ctx.actor_id, ctx.trace_id, *params))


def receipt(c, ctx, source, session, device):
    suffix = uuid4().hex
    key = hashlib.sha256(suffix.encode()).hexdigest()
    c.execute("INSERT INTO identity_step_up_authorizations(step_up_id,authorization_digest,tenant_id,actor_id,session_id,device_id,action_group,target_id,policy_version,issued_at,expires_at,used_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,'permanent_delete_or_restore_rollback',%s,%s,now(),now()+interval '5 minutes',now())",
        (suffix, key, ctx.tenant_id, ctx.actor_id, session, device, source, ctx.policy_version))
    c.execute("INSERT INTO identity_step_up_consumptions(tenant_id,actor_id,operation,idempotency_key,request_fingerprint,step_up_id,consumed_at) "
        "VALUES(%s,%s,'retention.source.delete',%s,%s,%s,now())", (ctx.tenant_id, ctx.actor_id, key, key, suffix))
    return key


def seed(pg, *, origin=None):
    suffix = uuid4().hex
    ctx = RetentionContext("s16-t-" + suffix, "s16-w-" + suffix, "s16-a-" + suffix, "s16-trace", "s16-policy")
    prefixes = ("s16-n-", "web-source-", "web-version-") if origin == "website" else (
        ("s16-n-", "law-source-", "law-version-") if origin == "mcp_open_law" else ("s16-n-", "src-", "sv-"))
    nb, source, version = (prefix + suffix for prefix in prefixes)
    snapshot = ("web-snapshot-" if origin == "website" else "law-snapshot-") + suffix
    payload = {"filename": "S16-QA.txt", "object_id": suffix, "content_type": "text/plain", "byte_size": 2,
        "content_digest_sha256": "a" * 64, "deletion_policy": "delete_with_notebook"}
    if origin:
        payload.pop("content_digest_sha256")  # Web/law identity is not file-digest dedup.
        payload["filename"] = snapshot + ".txt"
        payload.update(origin_kind=origin, snapshot_id=snapshot, raw_sha256="b" * 64, text_sha256="a" * 64)
        if origin == "mcp_open_law": payload["connector_id"] = "mcp-open-law-go-kr"
    oid = suffix  # The real ObjectKeyPolicy accepts opaque hex32, not a prefix.
    session, device = "session-" + suffix, "device-" + suffix
    policy = ObjectKeyPolicy()
    from daon_user_api.cloud_storage import CloudAccessContext
    cloud_ctx = CloudAccessContext(ctx.tenant_id, ctx.workspace_id, ctx.actor_id, "s16.fixture")
    keys = (policy.staging_key(cloud_ctx, "source", oid), policy.final_key(cloud_ctx, "source", oid))
    with psycopg.connect(pg.admin) as c:
        c.execute("INSERT INTO tenants(tenant_id,display_name) VALUES(%s,'S16 synthetic')", (ctx.tenant_id,))
        c.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S16 synthetic')", (ctx.tenant_id, ctx.workspace_id))
        c.execute("INSERT INTO user_accounts(tenant_id,user_id,status) VALUES(%s,%s,'active')", (ctx.tenant_id, ctx.actor_id))
        c.execute("INSERT INTO memberships(tenant_id,workspace_id,user_id,role) VALUES(%s,%s,%s,'personal_owner')", (ctx.tenant_id, ctx.workspace_id, ctx.actor_id))
        c.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s16-fixture',%s,'active')", (ctx.actor_id, ctx.actor_id))
        c.execute("INSERT INTO identity_tenants(tenant_id) VALUES(%s)", (ctx.tenant_id,))
        c.execute("INSERT INTO identity_devices(device_id,tenant_id,user_id,platform,state,last_seen_at,created_at,updated_at) VALUES(%s,%s,%s,'web','registered',now(),now(),now())", (device, ctx.tenant_id, ctx.actor_id))
        c.execute("INSERT INTO identity_sessions(session_id,tenant_id,user_id,device_id,client_kind,access_digest,access_expires_at,state,created_at,updated_at) VALUES(%s,%s,%s,%s,'web',%s,now()+interval '1 hour','active',now(),now())", (session, ctx.tenant_id, ctx.actor_id, device, suffix))
        c.execute("INSERT INTO identity_auth_workspaces(workspace_id,tenant_id,workspace_kind,data_area,cost_limit_cents,acl_version,version,updated_at) VALUES(%s,%s,'personal','workspace',0,1,1,now())", (ctx.workspace_id, ctx.tenant_id))
        c.execute("INSERT INTO identity_auth_tenant_roles(tenant_id,user_id,role,state,version,updated_at) VALUES(%s,%s,'personal_owner','active',1,now())", (ctx.tenant_id, ctx.actor_id))
        c.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())", (ctx.tenant_id, ctx.workspace_id, nb, ctx.actor_id))
        c.execute("INSERT INTO notebook_metadata_versions(tenant_id,workspace_id,notebook_id,version,title,is_current,updated_by,updated_at) VALUES(%s,%s,%s,1,'S16 synthetic',true,%s,now())", (ctx.tenant_id, ctx.workspace_id, nb, ctx.actor_id))
        c.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,created_at) "
            "VALUES(%s,%s,%s,'source',%s,%s,%s,2,'text/plain','completed',%s,%s,%s,%s,now())",
            (ctx.tenant_id, ctx.workspace_id, oid, *keys, "a" * 64, ctx.actor_id, ctx.trace_id, oid, "a" * 64))
        # The deletion base fixture has no processing Run: keep the real
        # canonical initial registered state instead of inventing ready.
        canon(c, ctx, "sources", source, {})
        canon(c, ctx, "source_versions", version, payload, extra=",source_id,object_id", params=(source, oid))
        c.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (ctx.tenant_id, ctx.workspace_id, nb, source, version, ctx.actor_id))
        auth = receipt(c, ctx, source, session, device)
    return SimpleNamespace(ctx=ctx, nb=nb, source=source, version=version, oid=oid, keys=keys, auth=auth, session=session, device=device, snapshot=snapshot)


class ObjectPort:
    def __init__(self, pg, f, *, fail_on_call=None):
        self.pg, self.f, self.calls, self.keys = pg, f, [], set(f.keys)
        self.fail_on_call = fail_on_call

    def delete(self, key):
        # Observe a separate DB connection, so an uncommitted/stale intent can
        # never satisfy the assertion that database cleanup precedes object I/O.
        with psycopg.connect(self.pg.admin) as c:
            assert c.execute("SELECT count(*) FROM sources WHERE record_id=%s", (self.f.source,)).fetchone()[0] == 0
            assert c.execute("SELECT state FROM deletion_requests WHERE source_id=%s", (self.f.source,)).fetchone()[0] != "purged"
            assert c.execute("SELECT count(*) FROM source_cleanup_objects WHERE object_key=%s", (key,)).fetchone()[0] == 1
        self.calls.append(key)
        if len(self.calls) == self.fail_on_call:
            raise ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)
        self.keys.discard(key)


def service(pg, storage):
    store = PostgresCloudStore(pg.app)
    return store, PostgresRetentionRequestService(store, PostgresRetentionInventoryProvider(store), clock=lambda: datetime.now(timezone.utc), object_storage=storage)


def request(svc, f, **changes):
    return svc.create_request(f.ctx, source_id=f.source, notebook_id=f.nb, step_up_verified=True,
        authorization_key=changes.pop("authorization_key", f.auth), inventory=None,
        if_match="*", idempotency_key=changes.pop("idempotency_key", f.source), **changes)


def test_s16_pg_partial_cleanup_restart_replay_and_notebook_projection(pg):
    f = seed(pg); storage = ObjectPort(pg, f, fail_on_call=2)
    store, svc = service(pg, storage)
    try:
        first = request(svc, f)
        assert first.state == "failed" and not first.source_active
        assert len(storage.calls) == 2 and len(set(storage.calls)) == 2
        assert len(storage.keys) == 1
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT state,count(*) FROM source_cleanup_objects WHERE request_id=%s GROUP BY state ORDER BY state", (first.request_id,)).fetchall() == [("completed", 1), ("failed", 1)]
            completed_key = c.execute("SELECT object_key FROM source_cleanup_objects WHERE request_id=%s AND state='completed'", (first.request_id,)).fetchone()[0]
        nbctx = NotebookContext(f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, f.ctx.trace_id, f.ctx.policy_version)
        projection = PostgresNotebookRepository(store).read_selected_context(nbctx, f.nb)
        assert projection.sources == ()
        assert [(v.request_id, v.state) for v in projection.source_deletion_requests] == [(first.request_id, "failed")]
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM source_retention_locator WHERE source_id=%s", (f.source,)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM audit_events WHERE target_id=%s AND action='deletion.purged'", (f.source,)).fetchone()[0] == 0
    finally:
        store.close()
    storage.fail_on_call = None
    store, svc = service(pg, storage)
    try:
        assert svc.locate_source_workspace(f.ctx.tenant_id, f.source) == f.ctx.workspace_id
        second = request(svc, f)
        assert second.state == "purged" and second.request_id == first.request_id
        assert storage.calls.count(completed_key) == 1
        assert len(storage.calls) == 3
        assert not storage.keys
        assert request(svc, f).state == "purged"
        assert svc.get_request(f.ctx, second.request_id).state == "purged"
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT state,count(*),sum(attempt_count) FROM source_cleanup_objects WHERE request_id=%s GROUP BY state", (first.request_id,)).fetchall() == [("completed", 2, 3)]
            assert c.execute("SELECT count(*) FROM audit_events WHERE target_id=%s AND action='deletion.purged'", (f.source,)).fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["shared_notebook", "active_run", "studio_history", "sync_history", "active_hold", "source_acl"])
def test_s16_pg_preflight_preserves_source_for_shared_active_and_retained_references(pg, kind):
    f = seed(pg); storage = ObjectPort(pg, f)
    expected = {"shared_notebook": "SOURCE_SHARED_BINDING", "active_run": "SOURCE_PROCESSING_ACTIVE", "studio_history": "SOURCE_RETAINED_REFERENCE",
        "sync_history": "SOURCE_RETAINED_REFERENCE", "active_hold": "LEGAL_HOLD_ACTIVE", "source_acl": "CURRENT_ACCESS_DENIED"}[kind]
    with psycopg.connect(pg.admin) as c:
        if kind == "shared_notebook":
            other = "s16-other-" + uuid4().hex
            c.execute("INSERT INTO notebooks VALUES(%s,%s,%s,%s,now())", (f.ctx.tenant_id, f.ctx.workspace_id, other, f.ctx.actor_id))
            c.execute("INSERT INTO notebook_bindings VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (f.ctx.tenant_id, f.ctx.workspace_id, other, f.source, f.version, f.ctx.actor_id))
        elif kind == "active_run":
            canon(c, f.ctx, "processing_runs", "s16-pr-" + uuid4().hex, {}, extra=",source_version_id,modality,trigger_type", params=(f.version, "document", "initial"))
        elif kind == "studio_history":
            canon(c, f.ctx, "generation_requests", "s16-gen-" + uuid4().hex, {"source_version_ids": [f.version]})
        elif kind == "sync_history":
            operation = "s16-sync-" + uuid4().hex
            c.execute("INSERT INTO sync_operations(tenant_id,workspace_id,operation_id,actor_id,target_area,state,version,preview_digest,policy_version,idempotency_key,request_fingerprint,trace_id,state_document,created_at,updated_at) "
                "VALUES(%s,%s,%s,%s,'cloud_sync','preview',1,%s,%s,%s,%s,%s,'{}',now(),now())",
                (f.ctx.tenant_id, f.ctx.workspace_id, operation, f.ctx.actor_id, "a" * 64, f.ctx.policy_version, operation, "a" * 64, f.ctx.trace_id))
            c.execute("INSERT INTO sync_preview_items(tenant_id,workspace_id,operation_id,item_id,source_version_id,local_object_id,object_digest,byte_size,content_type,created_at) "
                "VALUES(%s,%s,%s,'s16-item',%s,'s16-local',%s,2,'text/plain',now())", (f.ctx.tenant_id, f.ctx.workspace_id, operation, f.version, "a" * 64))
        elif kind == "source_acl":
            c.execute("INSERT INTO identity_auth_source_access VALUES(%s,%s,%s,%s,0,1,now())", (f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, f.version))
    store, svc = service(pg, storage)
    try:
        if kind == "active_hold":
            svc.apply_legal_hold(replace(f.ctx, organization_admin=True), source_id=f.source, expected_version=1, idempotency_key="s16-active-hold", step_up_verified=True)
        with pytest.raises(RetentionError, match=expected): request(svc, f)
        assert not storage.calls
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM sources WHERE record_id=%s", (f.source,)).fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM source_cleanup_requests WHERE source_id=%s", (f.source,)).fetchone()[0] == 0
    finally:
        store.close()


def test_s16_pg_other_source_shared_object_is_preserved(pg):
    f = seed(pg); storage = ObjectPort(pg, f)
    other = "src-shared-" + uuid4().hex
    with psycopg.connect(pg.admin) as c:
        canon(c, f.ctx, "sources", other, {})
        canon(c, f.ctx, "source_versions", "sv-shared-" + uuid4().hex, {"object_id": f.oid}, extra=",source_id,object_id", params=(other, f.oid))
    store, svc = service(pg, storage)
    try:
        assert request(svc, f).state == "purged"
        assert not storage.calls and storage.keys == set(f.keys)
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM object_records WHERE object_id=%s", (f.oid,)).fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM sources WHERE record_id=%s", (other,)).fetchone()[0] == 1
    finally: store.close()


def test_s16_pg_post_stage_hold_and_new_binding_cannot_race_file_cleanup(pg):
    f = seed(pg); entered, release = Event(), Event()
    class PausedPort(ObjectPort):
        def delete(self, key):
            entered.set()
            if not release.wait(10): raise AssertionError("controller failed to release fixture")
            super().delete(key)
    storage = PausedPort(pg, f); store, svc = service(pg, storage)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            deleting = pool.submit(request, svc, f)
            assert entered.wait(10)
            holding = pool.submit(svc.apply_legal_hold, replace(f.ctx, organization_admin=True), source_id=f.source,
                expected_version=1, idempotency_key="s16-late-hold", step_up_verified=True)
            with psycopg.connect(pg.app) as c:
                scoped(c, f.ctx, f.nb)
                c.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    c.execute("INSERT INTO notebook_bindings VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (f.ctx.tenant_id, f.ctx.workspace_id, f.nb, f.source, f.version, f.ctx.actor_id))
            release.set()
            assert deleting.result(15).state == "purged"
            with pytest.raises(RetentionError, match="SOURCE_UNAVAILABLE"): holding.result(15)
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM legal_holds WHERE source_id=%s", (f.source,)).fetchone()[0] == 0
        with psycopg.connect(pg.app) as c:
            scoped(c, f.ctx, f.nb)
            with pytest.raises(psycopg.Error, match="SOURCE_DELETION_ACTIVE"):
                c.execute("INSERT INTO notebook_bindings VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (f.ctx.tenant_id, f.ctx.workspace_id, f.nb, f.source, f.version, f.ctx.actor_id))
    finally:
        release.set(); store.close()


def test_s16_pg_direct_mutation_and_foreign_scope_are_denied(pg):
    f = seed(pg)
    for statement, args in (("DELETE FROM sources WHERE record_id=%s", (f.source,)),
        ("DELETE FROM source_versions WHERE record_id=%s", (f.version,)),
        ("DELETE FROM object_records WHERE object_id=%s", (f.oid,)),
        ("SELECT * FROM delete_source_scope(%s,%s,%s)", (f.ctx.tenant_id, f.ctx.workspace_id, f.source))):
        with psycopg.connect(pg.app) as c:
            scoped(c, f.ctx, f.nb)
            with pytest.raises(psycopg.Error): c.execute(statement, args)
    storage = ObjectPort(pg, f, fail_on_call=2); store, svc = service(pg, storage)
    try:
        created = request(svc, f)
        with pytest.raises(RetentionError, match="DELETION_REQUEST_UNAVAILABLE"):
            svc.get_request(replace(f.ctx, actor_id="unrelated-actor"), created.request_id)
        with psycopg.connect(pg.app) as c:
            scoped(c, replace(f.ctx, actor_id="unrelated-actor"), f.nb)
            assert c.execute("SELECT count(*) FROM source_cleanup_requests").fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM source_cleanup_objects").fetchone()[0] == 0
            with pytest.raises(psycopg.Error): c.execute("SELECT read_source_cleanup_scope(%s)", (created.request_id,))
        with psycopg.connect(pg.admin) as c:
            c.execute("UPDATE identity_users SET state='disabled' WHERE user_id=%s", (f.ctx.actor_id,))
        before = list(storage.calls)
        with pytest.raises(RetentionError, match="CURRENT_ACCESS_DENIED"): request(svc, f)
        assert storage.calls == before
    finally: store.close()


def seed_snapshot(pg, kind):
    """Valid synthetic snapshot rows, with all production INSERT guards active."""
    f = seed(pg, origin=kind)
    raw = uuid4().hex
    from daon_user_api.cloud_storage import CloudAccessContext
    access = CloudAccessContext(f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, "s16.fixture")
    policy = ObjectKeyPolicy()
    raw_keys = (policy.staging_key(access, "source", raw), policy.final_key(access, "source", raw))
    request_id = "s16-import-" + uuid4().hex
    f.keys = (*f.keys, *raw_keys)
    # schema-only QA does not copy application/static rows. Seed only the exact
    # canonical Source rules from 0003, never a new transition or direct ready.
    edges = run_path(str(Path(__file__).resolve().parents[1] / "migrations/versions/0003_data_canon_lineage.py"))["TRANSITIONS"]["Source"]
    with psycopg.connect(pg.admin) as c:
        for old, new in sorted(edges):
            c.execute("INSERT INTO canon_transition_rules(entity_type,source_state,target_state) VALUES('Source',%s,%s) ON CONFLICT DO NOTHING", (old, new))
        c.execute("INSERT INTO object_records(tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,created_by,trace_id,idempotency_key,request_fingerprint,created_at) "
            "VALUES(%s,%s,%s,'source',%s,%s,%s,2,'application/json','completed',%s,%s,%s,%s,now())",
            (f.ctx.tenant_id, f.ctx.workspace_id, raw, *raw_keys, "b" * 64, f.ctx.actor_id, f.ctx.trace_id, raw, "b" * 64))
    with psycopg.connect(pg.app) as c:
        scoped(c, f.ctx, f.nb)
        canon(c, f.ctx, "evidence_spans", "s16-span-" + uuid4().hex,
            {"source_id": f.source, "text": "QA", "page": 1}, extra=",source_version_id", params=(f.version,))
        canon(c, f.ctx, "index_versions", "s16-index-" + uuid4().hex,
            {"source_id": f.source, "source_version_id": f.version, "strategy": "website_text_snapshot" if kind == "website" else "open_law_verbatim_snapshot"},
            extra=",source_version_id", params=(f.version,))
        for version, target in enumerate(("security_check", "processing", "indexing", "ready"), 1):
            row = c.execute("SELECT state,version,outcome,error_code FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("Source", f.source, version, target, "s16-transition-" + uuid4().hex, "S16_FIXTURE_INDEXED", f.ctx.trace_id, f.ctx.policy_version)).fetchone()
            assert row == (target, version + 1, "succeeded", None)
        common = (f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, f.nb, request_id, request_id, "c" * 64)
        if kind == "website":
            c.execute("INSERT INTO website_import_requests(tenant_id,workspace_id,actor_id,notebook_id,idempotency_key,request_id,fingerprint,requested_url,state,raw_object_id,text_object_id,policy_version,policy_fingerprint) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,'https://example.com/','fetching',%s,%s,%s,'s16-policy-fingerprint')", (*common, raw, f.oid, f.ctx.policy_version))
            c.execute("INSERT INTO website_source_snapshots(tenant_id,workspace_id,snapshot_id,source_id,source_version_id,notebook_id,request_id,raw_object_id,text_object_id,requested_url,final_url,redirect_urls,title,fetched_at,license_status,raw_sha256,text_sha256,extraction_version,policy_version,policy_fingerprint,created_by,trace_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'https://example.com/','https://example.com/','[]','S16 synthetic',now(),'unknown',%s,%s,'website-text-v1',%s,'s16-policy-fingerprint',%s,%s)",
                (f.ctx.tenant_id, f.ctx.workspace_id, f.snapshot, f.source, f.version, f.nb, request_id, raw, f.oid, "b" * 64, "a" * 64, f.ctx.policy_version, f.ctx.actor_id, f.ctx.trace_id))
            c.execute("UPDATE website_import_requests SET state='completed',snapshot_id=%s WHERE request_id=%s", (f.snapshot, request_id))
            table = "website_source_snapshots"
        else:
            c.execute("INSERT INTO open_law_import_requests(tenant_id,workspace_id,actor_id,notebook_id,idempotency_key,request_id,fingerprint,mst,effective_date,article_code,expected_raw_sha256,expected_text_sha256,state,raw_object_id,text_object_id,policy_version,policy_fingerprint) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,'123','20260101','000100',%s,%s,'fetching',%s,%s,%s,'s16-policy-fingerprint')", (*common, "b" * 64, "a" * 64, raw, f.oid, f.ctx.policy_version))
            c.execute("INSERT INTO open_law_source_snapshots(tenant_id,workspace_id,snapshot_id,connector_id,source_id,source_version_id,notebook_id,request_id,raw_object_id,text_object_id,mst,law_id,title,effective_date,article_code,origin_uri,fetched_at,raw_sha256,text_sha256,extraction_version,license_status,policy_version,policy_fingerprint,created_by,trace_id) "
                "VALUES(%s,%s,%s,'mcp-open-law-go-kr',%s,%s,%s,%s,%s,%s,'123','456','S16 synthetic','20260101','000100','https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=123&efYd=20260101',now(),%s,%s,'open-law-text-v1','unknown',%s,'s16-policy-fingerprint',%s,%s)",
                (f.ctx.tenant_id, f.ctx.workspace_id, f.snapshot, f.source, f.version, f.nb, request_id, raw, f.oid, "b" * 64, "a" * 64, f.ctx.policy_version, f.ctx.actor_id, f.ctx.trace_id))
            c.execute("UPDATE open_law_import_requests SET state='completed',snapshot_id=%s,source_id=%s,source_version_id=%s WHERE request_id=%s", (f.snapshot, f.source, f.version, request_id))
            table = "open_law_source_snapshots"
        assert c.execute(f"SELECT count(*) FROM {table} WHERE snapshot_id=%s", (f.snapshot,)).fetchone()[0] == 1
    f.raw, f.import_request, f.snapshot_table = raw, request_id, table
    return f


@pytest.mark.parametrize("kind", ["website", "mcp_open_law"])
def test_s16_pg_unused_snapshot_cycle_and_both_object_bodies_are_removed(pg, kind):
    f = seed_snapshot(pg, kind)
    storage = ObjectPort(pg, f)
    store, svc = service(pg, storage)
    try:
        result = request(svc, f)
        assert result.state == "purged" and not storage.keys
        assert len(storage.calls) == 4 and len(set(storage.calls)) == 4
        with psycopg.connect(pg.admin) as c:
            assert c.execute(f"SELECT count(*) FROM {f.snapshot_table} WHERE snapshot_id=%s", (f.snapshot,)).fetchone()[0] == 0
            table = "website_import_requests" if kind == "website" else "open_law_import_requests"
            assert c.execute(f"SELECT count(*) FROM {table} WHERE request_id=%s", (f.import_request,)).fetchone()[0] == 0
            for table, column in (("sources", "record_id"), ("source_versions", "record_id"), ("index_versions", "source_version_id"), ("evidence_spans", "source_version_id")):
                assert c.execute(f"SELECT count(*) FROM {table} WHERE {column}=%s", (f.source if table == "sources" else f.version,)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM object_records WHERE object_id=ANY(%s)", ([f.oid, f.raw],)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM source_cleanup_objects WHERE request_id=%s AND state='completed'", (result.request_id,)).fetchone()[0] == 4
    finally: store.close()


def test_s16_pg_object_success_then_db_error_rolls_back_ack_and_retries_idempotently(pg):
    f = seed(pg)
    storage = ObjectPort(pg, f)
    store, svc = service(pg, storage)
    class FaultConnection:
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, params=()):
            if sql.startswith("SELECT progress_source_cleanup("):
                # Real server error after the external delete, not a fabricated
                # exception or a mock commit. The real transaction must roll back.
                return self.connection.execute("SELECT 1 / 0")
            return self.connection.execute(sql, params)
    class FaultStore:
        @contextmanager
        def _transaction(self, access):
            with store._transaction(access) as connection:
                yield FaultConnection(connection)
    svc._store = FaultStore()  # Request-local persistence port, never a shared instance.
    try:
        with pytest.raises(RetentionError, match="RETENTION_UNAVAILABLE"):
            request(svc, f)
        assert len(storage.calls) == 1 and len(storage.keys) == 1
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT state FROM deletion_requests WHERE source_id=%s", (f.source,)).fetchone()[0] == "cleanup_pending"
            request_id = c.execute("SELECT request_id FROM deletion_requests WHERE source_id=%s", (f.source,)).fetchone()[0]
            assert c.execute("SELECT state,count(*),sum(attempt_count) FROM source_cleanup_objects WHERE request_id=%s GROUP BY state", (request_id,)).fetchall() == [("pending", 2, 0)]
            assert c.execute("SELECT count(*) FROM audit_events WHERE target_id=%s AND action='deletion.purged'", (f.source,)).fetchone()[0] == 0
    finally: store.close()
    store, svc = service(pg, storage)
    try:
        result = request(svc, f)
        assert result.state == "purged" and result.request_id == request_id
        assert len(storage.calls) == 3 and sorted(storage.calls.count(key) for key in set(storage.calls)) == [1, 2]
        assert not storage.keys
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT state,count(*),sum(attempt_count) FROM source_cleanup_objects WHERE request_id=%s GROUP BY state", (request_id,)).fetchall() == [("completed", 2, 2)]
            assert c.execute("SELECT count(*) FROM audit_events WHERE target_id=%s AND action='deletion.purged'", (f.source,)).fetchone()[0] == 1
    finally: store.close()


def notebook_delete(store, f, key="s16-notebook-delete"):
    context = NotebookContext(f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, f.ctx.trace_id, f.ctx.policy_version)
    return PostgresNotebookRepository(store).request_deletion(context, f.nb,
        title_confirmation="S16 synthetic", expected_etag='"notebook:1"',
        idempotency_key=key, request_fingerprint=hashlib.sha256(key.encode()).hexdigest(),
        now=datetime.now(timezone.utc))


def test_s16_pg_incomplete_source_cleanup_blocks_notebook_delete_until_purged(pg):
    f = seed(pg)
    storage = ObjectPort(pg, f, fail_on_call=2)
    store, svc = service(pg, storage)
    try:
        deletion = request(svc, f)
        assert deletion.state == "failed"
        assert len(storage.keys) == 1
        with pytest.raises(NotebookError, match="NOTEBOOK_DELETION_IN_PROGRESS") as denied:
            notebook_delete(store, f)
        assert denied.value.status == 409
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM notebook_deletion_requests WHERE notebook_id=%s", (f.nb,)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM notebooks WHERE notebook_id=%s", (f.nb,)).fetchone()[0] == 1
        # After real object cleanup, the opaque purged history is not a new
        # Notebook retention constraint, including the actual DELETE function.
        storage.fail_on_call = None
        assert request(svc, f).state == "purged" and not storage.keys
        accepted, replayed = notebook_delete(store, f)
        assert accepted.state == "accepted" and replayed is False
        with psycopg.connect(pg.app) as c:
            scoped(c, f.ctx, f.nb)
            c.execute("SELECT * FROM delete_notebook_scope(%s,%s,%s)", (f.ctx.tenant_id, f.ctx.workspace_id, f.nb)).fetchall()
            assert c.execute("SELECT count(*) FROM notebooks WHERE notebook_id=%s", (f.nb,)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM source_cleanup_requests WHERE request_id=%s", (deletion.request_id,)).fetchone()[0] == 1
    finally: store.close()


def test_s16_pg_pending_cleanup_rejects_app_direct_notebook_admission(pg):
    f = seed(pg)
    storage = ObjectPort(pg, f, fail_on_call=2)
    store, svc = service(pg, storage)
    try:
        assert request(svc, f).state == "failed"
        with psycopg.connect(pg.app) as c:
            scoped(c, f.ctx, f.nb)
            with pytest.raises(psycopg.Error) as denied:
                c.execute("INSERT INTO notebook_deletion_requests(request_id,tenant_id,workspace_id,notebook_id,actor_id,title_fingerprint,state,current_step,requested_at,idempotency_key,expected_version,request_fingerprint) "
                    "VALUES(%s,%s,%s,%s,%s,%s,'accepted','accepted',now(),%s,1,%s)",
                    ("s16-direct-" + uuid4().hex, f.ctx.tenant_id, f.ctx.workspace_id, f.nb, f.ctx.actor_id, "a" * 64, "s16-direct-key", "b" * 64))
            assert denied.value.sqlstate == "55000"
            assert denied.value.diag.message_primary == "NOTEBOOK_DELETION_IN_PROGRESS"
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM notebook_deletion_requests WHERE notebook_id=%s", (f.nb,)).fetchone()[0] == 0
    finally: store.close()


def test_s16_pg_source_first_holds_notebook_lifecycle_until_durable_cleanup_admission(pg):
    f = seed(pg)
    entered, release = Event(), Event()
    class PausedInventory(PostgresRetentionInventoryProvider):
        def read_in_transaction(self, connection, context, source_id):
            items = super().read_in_transaction(connection, context, source_id)
            entered.set()
            if not release.wait(10): raise AssertionError("fixture lifecycle release missing")
            return items
    storage = ObjectPort(pg, f, fail_on_call=2)
    store, svc = service(pg, storage)
    svc._inventory_provider = PausedInventory(store)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        source = pool.submit(request, svc, f)
        assert entered.wait(10)
        with psycopg.connect(pg.app) as c:
            scoped(c, f.ctx, f.nb)
            assert c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                (f"notebook-delete|{f.ctx.tenant_id}|{f.ctx.workspace_id}|{f.nb}",)).fetchone()[0] is False
        notebook = pool.submit(notebook_delete, store, f)
        release.set()
        assert source.result(15).state == "failed"
        with pytest.raises(NotebookError, match="NOTEBOOK_DELETION_IN_PROGRESS"):
            notebook.result(15)
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM notebook_deletion_requests WHERE notebook_id=%s", (f.nb,)).fetchone()[0] == 0
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
        store.close()


def test_s16_pg_notebook_first_blocks_source_before_source_lock_and_rejects_after_commit(pg):
    f = seed(pg)
    admitted, release = Event(), Event()
    notebook_store = PostgresCloudStore(pg.app)
    class PausedNotebookStore:
        @contextmanager
        def _transaction(self, access):
            with notebook_store._transaction(access) as connection:
                yield connection
                admitted.set()
                if not release.wait(10): raise AssertionError("fixture Notebook commit release missing")
    label = "s16-source-" + uuid4().hex
    source_store = PostgresCloudStore(make_conninfo(pg.app, application_name=label))
    storage = ObjectPort(pg, f)
    svc = PostgresRetentionRequestService(source_store, PostgresRetentionInventoryProvider(source_store),
        clock=lambda: datetime.now(timezone.utc), object_storage=storage)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        notebook = pool.submit(notebook_delete, PausedNotebookStore(), f)
        assert admitted.wait(10)
        source = pool.submit(request, svc, f)
        waiting = False
        # Observe a real backend lock wait, not a sleep-based assumption that the
        # second request happened to start. The fixture never waits on a model.
        with psycopg.connect(pg.admin) as c:
            for _ in range(100):
                c.execute("SELECT pg_stat_clear_snapshot()")
                waiting = c.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock')", (label,)).fetchone()[0]
                if waiting: break
                time.sleep(0.02)
            assert waiting, "Source must wait behind the admitted Notebook transaction"
            scoped(c, f.ctx, f.nb)
            assert c.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                (f"retention-source|{f.ctx.tenant_id}|{f.ctx.workspace_id}|{f.source}",)).fetchone()[0] is True
        release.set()
        assert notebook.result(15)[0].state == "accepted"
        with pytest.raises(RetentionError, match="CURRENT_ACCESS_DENIED"):
            source.result(15)
        assert storage.calls == []
        with psycopg.connect(pg.admin) as c:
            assert c.execute("SELECT count(*) FROM sources WHERE record_id=%s", (f.source,)).fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM source_cleanup_requests WHERE source_id=%s", (f.source,)).fetchone()[0] == 0
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
        source_store.close()
        notebook_store.close()
