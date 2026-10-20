"""S16 Studio generation scope; real isolated SQL, no models or external I/O."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from runpy import run_path
from uuid import uuid4

import psycopg
import pytest

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.notebook import NotebookContext
from daon_user_api.notebook_postgres import PostgresNotebookRepository
from daon_user_api.studio_workspace import StudioContext, StudioError, StudioGenerationRequest
from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
from test_source_deletion_device_s16 import pg
from test_source_deletion_postgres_s16 import seed, scoped


def setup_studio(pg):
    f = seed(pg)
    f.other_nb = "s16-original-" + uuid4().hex
    with psycopg.connect(pg.admin) as connection:
        edges = run_path(str(Path(__file__).resolve().parents[1] / "migrations/versions/0003_data_canon_lineage.py"))["TRANSITIONS"]["Source"]
        for old, new in sorted(edges):
            connection.execute("INSERT INTO canon_transition_rules(entity_type,source_state,target_state) VALUES('Source',%s,%s) ON CONFLICT DO NOTHING", (old, new))
        connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())", (f.ctx.tenant_id, f.ctx.workspace_id, f.other_nb, f.ctx.actor_id))
        connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())", (f.ctx.tenant_id, f.ctx.workspace_id, f.other_nb, f.source, f.version, f.ctx.actor_id))
    with psycopg.connect(pg.app) as connection:
        scoped(connection, f.ctx, f.nb)
        for version, target in enumerate(("security_check", "processing", "indexing", "ready"), 1):
            row = connection.execute("SELECT state,version,outcome,error_code FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("Source", f.source, version, target, "s16-studio-" + uuid4().hex, "S16_FIXTURE_READY", f.ctx.trace_id, f.ctx.policy_version)).fetchone()
            assert row == (target, version + 1, "succeeded", None)
    store = PostgresCloudStore(pg.app)
    context = StudioContext(f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, f.ctx.trace_id, f.ctx.policy_version, f.nb)
    return f, store, context, PostgresStudioWorkspaceRepository(store)


def generation(f, *, source_only=True):
    return StudioGenerationRequest("evidence_report", f.source, (f.version,),
        None if source_only else "s16-run", None if source_only else "s16-result",
        "S16 binding check", "reviewer", None, "short", "summary", "pdf", "review_required",
        source_only=source_only)


def unbind(store, f):
    ctx = NotebookContext(f.ctx.tenant_id, f.ctx.workspace_id, f.ctx.actor_id, f.ctx.trace_id, f.ctx.policy_version)
    result, _ = PostgresNotebookRepository(store).unbind_source(ctx, f.nb, source_id=f.source,
        source_version_id=f.version, expected_etag='"notebook-binding:1"', idempotency_key="s16-unbind",
        request_fingerprint=hashlib.sha256(f.version.encode()).hexdigest(), now=datetime.now(timezone.utc))
    assert result.status == "unbound"


def close_studio(store, repository):
    if repository._generation_queue is not None:
        repository._generation_queue.close()
    store.close()


@pytest.mark.parametrize("source_only", [True, False])
def test_s16_pg_studio_unbind_denies_new_enqueue_and_replay_but_preserves_original_notebook(pg, source_only):
    f, store, ctx, repository = setup_studio(pg)
    try:
        request = generation(f, source_only=source_only)
        job, replayed = repository.enqueue_generation(ctx, request, "s16-queued-before-unbind")
        assert replayed is False and job["status"] == "queued"
        replay, replayed = repository.enqueue_generation(ctx, request, "s16-queued-before-unbind")
        assert replayed is True and replay["job_id"] == job["job_id"]
        unbind(store, f)
        for key in ("s16-queued-before-unbind", "s16-new-after-unbind"):
            with pytest.raises(StudioError, match="NOTEBOOK_SCOPE_MISMATCH") as denied:
                repository.enqueue_generation(ctx, request, key)
            assert denied.value.status == 409
        original, replayed = repository.enqueue_generation(replace(ctx, notebook_id=f.other_nb), request, "s16-original-notebook")
        assert replayed is False and original["status"] == "queued"
        with psycopg.connect(pg.admin) as connection:
            assert connection.execute("SELECT count(*) FROM studio_generation_jobs WHERE tenant_id=%s", (ctx.tenant_id,)).fetchone()[0] == 2
            assert connection.execute("SELECT count(*) FROM sources WHERE record_id=%s AND state='ready'", (f.source,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM studio_outputs WHERE tenant_id=%s", (ctx.tenant_id,)).fetchone()[0] == 0
    finally:
        close_studio(store, repository)


def test_s16_pg_studio_queued_before_unbind_cannot_pass_worker_recheck_or_create(pg):
    f, store, ctx, repository = setup_studio(pg)
    try:
        request = generation(f)
        job, _ = repository.enqueue_generation(ctx, request, "s16-queued")
        unbind(store, f)
        with pytest.raises(StudioError, match="NOTEBOOK_SCOPE_MISMATCH"):
            repository.require_source_access(ctx, request.source_version_ids)
        with pytest.raises(StudioError, match="NOTEBOOK_SCOPE_MISMATCH"):
            repository.create_generation(ctx, request, "s16-queued")
        with pytest.raises(StudioError, match="NOTEBOOK_SCOPE_MISMATCH"):
            repository.get_generation_job(ctx, job["job_id"])
        with psycopg.connect(pg.admin) as connection:
            for table in ("generation_requests", "studio_outputs", "output_versions"):
                assert connection.execute(f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (ctx.tenant_id,)).fetchone()[0] == 0
    finally:
        close_studio(store, repository)
