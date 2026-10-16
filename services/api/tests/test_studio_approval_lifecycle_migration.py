"""0066 migration entrypoint tests; these do not prove actual PostgreSQL DDL."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import os
from dataclasses import replace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/versions/0066_studio_approval_lifecycle.py"


def migration():
    assert MIGRATION.is_file(), "0066 lifecycle migration has not been implemented"
    spec = importlib.util.spec_from_file_location("s24_migration_0066", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_s24_lifecycle_migration_skips_reserved_0065_and_executes_adjacent_exact_sql():
    module = migration()
    assert (module.revision, module.down_revision, module.branch_labels, module.depends_on) == ("0066", "0064", None, None)
    with patch.object(module.op, "execute") as execute:
        module.upgrade()
    execute.assert_called_once_with(MIGRATION.with_suffix(".sql").read_text(encoding="utf-8"))


def test_s24_lifecycle_downgrade_checks_durable_rows_before_drop():
    module = migration()
    with patch.object(module.op, "execute", side_effect=RuntimeError("STUDIO_APPROVAL_FORWARD_RECOVERY_REQUIRED")) as execute:
        try:
            module.downgrade()
        except RuntimeError as error:
            assert str(error) == "STUDIO_APPROVAL_FORWARD_RECOVERY_REQUIRED"
        else:
            raise AssertionError("downgrade ignored the database refusal")
    assert execute.call_count == 1


@pytest.fixture
def pg_scope():
    """Controller-owned synthetic DB only; never a general DAON_TEST DSN."""
    import psycopg
    from psycopg.conninfo import make_conninfo
    from types import SimpleNamespace
    from daon_user_api.studio_workspace import StudioContext
    dsn = os.environ.get("DAON_S24_APPROVAL_ISOLATED_POSTGRES_DSN")
    if not dsn:
        pytest.skip("NOT RUN: controller-owned S24 approval isolated PostgreSQL DSN required")
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == "daon_user_s24_approval_qa_20261016"
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert revision in ("0064", "0066")
    suffix = uuid4().hex
    context = StudioContext("s24-tenant-"+suffix, "s24-workspace-"+suffix, "s24-actor-"+suffix,
                            "s24-trace-"+suffix, "runtime-policy-v1", "s24-notebook-"+suffix)
    return SimpleNamespace(admin=dsn, app=make_conninfo(dsn, options="-c role=daon_app"),
                           context=context, revision=revision, suffix=suffix)


def seed_delivery(scope, *, pending=False, historical_case=None):
    import psycopg
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository as Canon
    from daon_user_api.studio_approval_lifecycle import freeze_deadline
    ctx, suffix = scope.context, scope.suffix
    output, version = "s24-output-"+suffix, "s24-version-"+suffix
    settings, generation = "s24-settings-"+suffix, "s24-generation-"+suffix
    review, request, approval = "s24-review-"+suffix, "s24-request-"+suffix, "s24-approval-"+suffix
    with psycopg.connect(scope.admin) as connection:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES(%s,'S24 synthetic tenant')", (ctx.tenant_id,))
        connection.execute("INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,'S24 synthetic workspace')", (ctx.tenant_id,ctx.workspace_id))
        connection.execute("INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)", (ctx.tenant_id,ctx.actor_id))
        connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s24-fixture',%s,'active')", (ctx.actor_id,ctx.actor_id))
        connection.execute("INSERT INTO identity_auth_workspaces VALUES(%s,%s,'personal','cloud_sync',1000,1,1,now())", (ctx.workspace_id,ctx.tenant_id))
        connection.execute("INSERT INTO identity_auth_tenant_roles VALUES(%s,%s,'personal_owner','active',1,now())", (ctx.tenant_id,ctx.actor_id))
        connection.execute("INSERT INTO identity_auth_tenant_policies VALUES(%s,1,now())", (ctx.tenant_id,))
        connection.execute("INSERT INTO identity_auth_workspace_policies VALUES(%s,%s,1,now())", (ctx.tenant_id,ctx.workspace_id))
        for name, value in (("tenant_id",ctx.tenant_id),("workspace_id",ctx.workspace_id),("actor_id",ctx.actor_id)):
            connection.execute("SELECT set_config(%s,%s,true)", ("app."+name,value))
        connection.execute("INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,now())",
                           (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,ctx.actor_id))
        source, source_version, span = "s24-source-"+suffix,"s24-source-version-"+suffix,"s24-span-"+suffix
        Canon._insert(connection,ctx,"sources",source,{"filename":"S24 synthetic.txt"},state="registered")
        Canon._insert(connection,ctx,"source_versions",source_version,{"origin_kind":"native_txt"},extra_columns=("source_id",),extra_values=(source,))
        Canon._insert(connection,ctx,"evidence_spans",span,{"text":"S24 immutable evidence"},extra_columns=("source_version_id",),extra_values=(source_version,))
        connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,now())",
                           (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,source,source_version,ctx.actor_id))
        Canon._insert(connection,ctx,"generation_settings_snapshots",settings,{"output_format":"csv"})
        Canon._insert(connection,ctx,"generation_requests",generation,{},state="configuring",extra_columns=("generation_settings_snapshot_id",),extra_values=(settings,))
        Canon._insert(connection,ctx,"studio_outputs",output,{"title":"S24 synthetic comparison","output_type":"comparison_table","purpose":"S24 FK lock"},extra_columns=("generation_request_id",),extra_values=(generation,))
        Canon._insert(connection,ctx,"output_versions",version,{"content":"S24 original immutable bytes","output_format":"csv"},state="generating",
                      extra_columns=("studio_output_id","generation_settings_snapshot_id","content_version"),extra_values=(output,settings,1))
        Canon._insert(connection,ctx,"evidence_references","s24-evidence-"+suffix,{"source_version_id":source_version,"evidence_span_id":span},
                      extra_columns=("output_version_id","source_version_id","evidence_span_id"),extra_values=(version,source_version,span))
        state_version=1
        for state in ("draft","review_requested","in_review"):
            state_version=Canon._transition(connection,ctx,"OutputVersion",version,state_version,state,"s24-transition-"+state+suffix)
        connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,created_by,created_at) VALUES(%s,%s,%s,'output_version',%s,%s,now())",
                           (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,version,ctx.actor_id))
        Canon._insert(connection,ctx,"review_requests",review,{"output_version_id":version},extra_columns=("output_version_id",),extra_values=(version,))
        now=connection.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        request_payload={"output_version_id":version,"review_request_id":review}
        extra_columns=("output_version_id","review_request_id")
        extra_values=(version,review)
        if historical_case is not None:
            from datetime import timedelta
            assert scope.revision == "0064", "historical INSERT is permitted only before 0066"
            assert pending is True
            ages={"legacy_expired":timedelta(days=8), "legacy_due":timedelta(days=6,hours=12),
                  "legacy_future":timedelta(days=5), "one_day_due":timedelta(minutes=1),
                  "legacy_race":timedelta(days=8)}
            created=now-ages[historical_case]
            request_payload["s24_fixture_kind"]=historical_case
            if historical_case == "one_day_due":
                request_payload.update(freeze_deadline(created,1,0))
            extra_columns+=("created_at",)
            extra_values+=(created,)
        else:
            request_payload.update(freeze_deadline(now))
        Canon._insert(connection,ctx,"approval_requests",request,request_payload,state="pending",
                      extra_columns=extra_columns,extra_values=extra_values)
        if not pending:
            Canon._insert(connection,ctx,"approvals",approval,{"output_version_id":version,"approval_request_id":request,"decision":"approved"},
                          extra_columns=("output_version_id","approval_request_id","decision"),extra_values=(version,request,"approved"))
            Canon._transition(connection,ctx,"ApprovalRequest",request,1,"approved","s24-transition-request-"+suffix)
            Canon._transition(connection,ctx,"OutputVersion",version,state_version,"approved","s24-transition-approved-"+suffix)
        original=connection.execute("SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
                                    (ctx.tenant_id,ctx.workspace_id,version)).fetchone()
    scope.request_id=request
    scope.source_version_id=source_version
    return version, approval, original


def test_s24_lifecycle_pg_prepare_historical_before_upgrade(pg_scope):
    """Explicit controller node: 0064 INSERT-only, no UPDATE/clock/guard change."""
    import psycopg
    if pg_scope.revision != "0064":
        pytest.skip("NOT RUN: historical preparation must precede 0066 upgrade")
    cases={"legacy_expired","legacy_due","legacy_future","one_day_due","legacy_race"}
    with psycopg.connect(pg_scope.admin) as connection:
        existing=connection.execute("SELECT canonical_json->>'s24_fixture_kind' FROM approval_requests "
            "WHERE tenant_id LIKE 's24-tenant-%%' AND canonical_json->>'s24_fixture_kind'=ANY(%s)", (list(cases),)).fetchall()
    if existing:
        assert len(existing)==5 and {row[0] for row in existing}==cases, "partial historical preparation: controller must inspect"
        return
    for case in sorted(cases):
        suffix=uuid4().hex
        scope=replace_scope(pg_scope,suffix)
        seed_delivery(scope,pending=True,historical_case=case)
        with psycopg.connect(scope.admin) as connection:
            row=connection.execute("SELECT state,created_at,canonical_json,CURRENT_TIMESTAMP FROM approval_requests "
                "WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
                (scope.context.tenant_id,scope.context.workspace_id,scope.request_id)).fetchone()
            assert row[0]=="pending" and row[1]<row[3] and row[2]["s24_fixture_kind"]==case
            assert ("ttl_days" in row[2]) is (case=="one_day_due")


def replace_scope(scope,suffix):
    from types import SimpleNamespace
    return SimpleNamespace(admin=scope.admin,app=scope.app,revision=scope.revision,suffix=suffix,
        context=replace(scope.context,tenant_id="s24-tenant-"+suffix,workspace_id="s24-workspace-"+suffix,
            actor_id="s24-actor-"+suffix,trace_id="s24-trace-"+suffix,notebook_id="s24-notebook-"+suffix))


def historical_scopes(scope):
    import psycopg
    from types import SimpleNamespace
    from daon_user_api.studio_workspace import StudioContext
    assert scope.revision=="0066", "post-upgrade node requires reviewed 0066"
    with psycopg.connect(scope.admin) as connection:
        rows=connection.execute("SELECT r.canonical_json->>'s24_fixture_kind',r.tenant_id,r.workspace_id,r.created_by,"
            "r.trace_id,b.notebook_id,r.record_id,r.output_version_id,r.created_at,r.canonical_json "
            "FROM approval_requests r JOIN notebook_bindings b ON b.tenant_id=r.tenant_id AND b.workspace_id=r.workspace_id "
            "AND b.binding_kind='output_version' AND b.record_id=r.output_version_id "
            "WHERE r.tenant_id LIKE 's24-tenant-%%' AND r.canonical_json ? 's24_fixture_kind'").fetchall()
    assert len(rows)==5, "run the explicit 0064 historical preparation node before upgrade"
    return {row[0]:SimpleNamespace(admin=scope.admin,app=scope.app,request_id=row[6],version_id=row[7],
        created_at=row[8],payload=row[9],context=StudioContext(row[1],row[2],row[3],row[4],"runtime-policy-v1",row[5])) for row in rows}


def test_s24_lifecycle_pg_delivery_fk_replay_and_scope_preserve_approval_and_content(pg_scope):
    """Expected RED on 0064: real FK lock permission, not a mock export."""
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace import StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    version, approval, original = seed_delivery(pg_scope)
    ctx=pg_scope.context
    store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    try:
        repo=PostgresStudioWorkspaceRepository(store)
        payload={"output_version_id":version,"approval_id":approval,"recipient":"workspace_recipient"}
        result,replayed=repo.record_action(ctx,"delivery",payload,"s24-delivery-key-0001")
        assert replayed is False and result["action"]=="delivery"
        again,replayed=repo.record_action(ctx,"delivery",payload,"s24-delivery-key-0001")
        assert replayed is True and again==result
        with pytest.raises(StudioError):
            repo.record_action(replace(ctx,notebook_id="s24-other-notebook"),"delivery",payload,"s24-foreign-key-00001")
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute("SELECT count(*) FROM deliveries WHERE tenant_id=%s AND workspace_id=%s AND output_version_id=%s",(ctx.tenant_id,ctx.workspace_id,version)).fetchone()[0]==1
            assert connection.execute("SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",(ctx.tenant_id,ctx.workspace_id,version)).fetchone()==original
        with store._transaction(repo._cloud(ctx,"s24.direct")) as connection:
            for statement in ("UPDATE approvals SET decision='rejected' WHERE record_id=%s", "DELETE FROM approvals WHERE record_id=%s"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute(statement,(approval,))
    finally:
        store.close()


def test_s24_lifecycle_pg_text_source_deny_blocks_delivery_export_and_preserves_canonical(pg_scope):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace import StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from unittest.mock import patch
    from daon_user_api.authorization import Action
    assert pg_scope.revision=='0066'
    version,approval,original=seed_delivery(pg_scope)
    ctx=pg_scope.context
    with psycopg.connect(pg_scope.admin) as connection:
        before=connection.execute('SELECT canonical_text,digest_sha256,decision,output_version_id,approval_request_id,created_by,created_at FROM approvals WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
            (ctx.tenant_id,ctx.workspace_id,approval)).fetchone()
        connection.execute('INSERT INTO identity_auth_source_access(tenant_id,workspace_id,source_version_id,user_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,0,1,now())',
            (ctx.tenant_id,ctx.workspace_id,pg_scope.source_version_id,ctx.actor_id))
    store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    try:
        repo=PostgresStudioWorkspaceRepository(store)
        with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE') as denied:
            repo.record_action(ctx,'delivery',{'output_version_id':version,'approval_id':approval,'recipient':'workspace_recipient'},'s24-deny-delivery-key-001')
        assert denied.value.status==404
        with patch('daon_user_api.studio_workspace_postgres.export_studio_output') as render:
            with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE') as denied:
                repo.export_output(ctx,'s24-output-'+pg_scope.suffix,version,'csv')
            assert denied.value.status==404
            render.assert_not_called()
        with store._transaction(repo._cloud(ctx,'s24.source.deny.check')) as connection:
            for action in (Action.VIEW,Action.EDIT):
                with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE'):
                    repo._require_output_sources(connection,ctx,version,action=action)
            assert connection.execute('SELECT count(*) FROM deliveries WHERE tenant_id=%s AND workspace_id=%s',
                (ctx.tenant_id,ctx.workspace_id)).fetchone()==(0,)
            assert connection.execute('SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,version)).fetchone()==original
            assert connection.execute('SELECT canonical_text,digest_sha256,decision,output_version_id,approval_request_id,created_by,created_at FROM approvals WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,approval)).fetchone()==before
    finally:
        store.close()


def test_s24_lifecycle_version_detail_explicit_source_deny_consumes_output_guard():
    """Real repository consumer; row/JSON boundary double, ACL SELECT on SQLite."""
    import sqlite3
    from test_studio_workspace_postgres import Cloud, VersionHistoryConnection
    from daon_user_api.studio_workspace import StudioContext, StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    database=sqlite3.connect(':memory:')
    database.executescript('CREATE TABLE evidence_references(tenant_id,workspace_id,output_version_id,source_version_id);'
        'CREATE TABLE source_versions(tenant_id,workspace_id,record_id);'
        'CREATE TABLE identity_auth_source_access(tenant_id,workspace_id,source_version_id,user_id,allowed);')
    database.execute('INSERT INTO evidence_references VALUES(?,?,?,?)',('tenant-1','workspace-1','version-2','source-version-1'))
    database.execute('INSERT INTO source_versions VALUES(?,?,?)',('tenant-1','workspace-1','source-version-1'))
    class Connection(VersionHistoryConnection):
        def execute(self,sql,params=()):
            if sql.startswith('SELECT 1 FROM evidence_references er '):
                return database.execute(sql.replace('%s','?'),params)
            return super().execute(sql,params)
    cloud=Cloud();cloud.connection=Connection()
    repo=PostgresStudioWorkspaceRepository(cloud)
    ctx=StudioContext('tenant-1','workspace-1','actor-1','trace-1','policy-1','notebook-1')
    try:
        before=repo.list_versions(ctx,'output-1')
        assert len(before)==2 and before[0]['content']=={'body':'승인 본문'}
        database.execute('INSERT INTO identity_auth_source_access VALUES(?,?,?,?,?)',('tenant-1','workspace-1','source-version-1','actor-1',0))
        with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE') as denied:
            repo.list_versions(ctx,'output-1')
        assert denied.value.status==404
    finally:
        database.close()


def test_s24_lifecycle_pg_version_detail_positive_then_explicit_deny_blocks_body_preserves_history(pg_scope):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_workspace import StudioError
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    assert pg_scope.revision=='0066'
    version,approval,original=seed_delivery(pg_scope)
    ctx=pg_scope.context;output='s24-output-'+pg_scope.suffix
    with psycopg.connect(pg_scope.admin) as connection:
        connection.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,created_by,created_at) VALUES(%s,%s,%s,'studio_output',%s,%s,now())",
            (ctx.tenant_id,ctx.workspace_id,ctx.notebook_id,output,ctx.actor_id))
        approval_before=connection.execute('SELECT canonical_text,digest_sha256,decision,output_version_id,approval_request_id,created_by,created_at FROM approvals WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
            (ctx.tenant_id,ctx.workspace_id,approval)).fetchone()
    store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    try:
        repo=PostgresStudioWorkspaceRepository(store)
        visible=repo.list_versions(ctx,output)
        assert len(visible)==1 and visible[0]['output_version_id']==version
        assert visible[0]['content']=='S24 original immutable bytes'
        assert visible[0]['status']=='approved' and len(visible[0]['citations'])==1
        with psycopg.connect(pg_scope.admin) as connection:
            connection.execute('INSERT INTO identity_auth_source_access(tenant_id,workspace_id,source_version_id,user_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,0,1,now())',
                (ctx.tenant_id,ctx.workspace_id,pg_scope.source_version_id,ctx.actor_id))
        with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE') as denied:
            repo.list_versions(ctx,output)
        assert denied.value.status==404
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute('SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,version)).fetchone()==original
            assert connection.execute('SELECT canonical_text,digest_sha256,decision,output_version_id,approval_request_id,created_by,created_at FROM approvals WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',
                (ctx.tenant_id,ctx.workspace_id,approval)).fetchone()==approval_before
    finally:
        store.close()


def test_s24_lifecycle_pg_withdraw_notices_read_etag_and_current_scope(pg_scope):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace import StudioError
    version, _, original=seed_delivery(pg_scope,pending=True)
    ctx=pg_scope.context; store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    try:
        repo=StudioApprovalLifecycleRepository(store)
        assert repo.get_policy(ctx)["ttl_days"]==7
        with pytest.raises(StudioError):
            repo.withdraw(replace(ctx,actor_id="s24-wrong-actor"),pg_scope.request_id,version,"s24-withdraw-key-001")
        with pytest.raises(StudioError):
            repo.withdraw(replace(ctx,notebook_id="s24-wrong-notebook"),pg_scope.request_id,version,"s24-withdraw-key-001")
        result,replayed=repo.withdraw(ctx,pg_scope.request_id,version,"s24-withdraw-key-001")
        assert replayed is False and result=={"record_id":pg_scope.request_id,"action":"withdraw","status":"withdrawn","output_version_id":version}
        assert repo.withdraw(ctx,pg_scope.request_id,version,"s24-withdraw-key-001")== (result,True)
        notices=repo.list_notices(ctx)
        assert notices["unread_count"]==1 and len(notices["items"])==1
        notice=notices["items"][0]
        assert notice["event_kind"]=="withdrawn" and notice["approval_request_id"]==pg_scope.request_id
        assert set(notice)=={"notification_id","approval_request_id","output_version_id","event_kind","scheduled_at","created_at","read_at","version"}
        assert repo.list_notices(replace(ctx,notebook_id="s24-wrong-notebook"))=={"items":[],"unread_count":0}
        read,replayed=repo.mark_notice_read(ctx,notice["notification_id"],1,"s24-notice-read-001")
        assert replayed is False and read["version"]==2
        assert repo.mark_notice_read(ctx,notice["notification_id"],1,"s24-notice-read-001")== (read,True)
        with pytest.raises(StudioError,match="PRECONDITION_FAILED"):
            repo.mark_notice_read(ctx,notice["notification_id"],1,"s24-notice-read-002")
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute("SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",(ctx.tenant_id,ctx.workspace_id,version)).fetchone()==original
            assert connection.execute("SELECT count(*) FROM approvals WHERE tenant_id=%s AND workspace_id=%s AND output_version_id=%s",(ctx.tenant_id,ctx.workspace_id,version)).fetchone()[0]==0
        # Reconnect, not a process-local notice map.
        store.close(); store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
        assert StudioApprovalLifecycleRepository(store).list_notices(ctx)["unread_count"]==0
    finally:
        store.close()


def test_s24_lifecycle_pg_scoped_lock_without_app_update_or_cross_scope_access(pg_scope):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    assert pg_scope.revision=="0066"
    version, _, _=seed_delivery(pg_scope,pending=True)
    repo=StudioApprovalLifecycleRepository(PostgresCloudStore(pg_scope.app,min_size=0,max_size=2))
    ctx=pg_scope.context
    try:
        with repo._store._transaction(repo._cloud(ctx,"s24.lock.check")) as connection:
            assert connection.execute("SELECT has_table_privilege(current_user,'public.output_versions','UPDATE'),"
                "has_table_privilege(current_user,'public.approval_requests','UPDATE')").fetchone()==(False,False)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute("SELECT record_id FROM output_versions WHERE record_id=%s FOR UPDATE",(version,))
            assert connection.execute("SELECT lock_studio_approval_version(%s,%s)",(version,ctx.notebook_id)).fetchone()==(True,)
            assert connection.execute("SELECT lock_studio_approval_version(%s,%s)",(version,"s24-wrong-notebook")).fetchone()==(False,)
            assert connection.execute("SELECT lock_studio_approval_version(%s,%s)",("s24-wrong-version",ctx.notebook_id)).fetchone()==(False,)
        for foreign in (replace(ctx,actor_id="s24-other-actor"),replace(ctx,workspace_id="s24-other-workspace"),replace(ctx,tenant_id="s24-other-tenant")):
            with repo._store._transaction(repo._cloud(foreign,"s24.lock.check")) as connection:
                assert connection.execute("SELECT lock_studio_approval_version(%s,%s)",(version,ctx.notebook_id)).fetchone()==(False,)
                if foreign.tenant_id!=ctx.tenant_id or foreign.workspace_id!=ctx.workspace_id:
                    assert connection.execute("SELECT count(*) FROM approval_requests WHERE record_id=%s",(pg_scope.request_id,)).fetchone()==(0,)
    finally:
        repo._store.close()


def test_s24_lifecycle_pg_text_source_explicit_deny_blocks_approval_and_withdraw(pg_scope):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioError
    assert pg_scope.revision=="0066"
    version,_,original=seed_delivery(pg_scope,pending=True)
    ctx=pg_scope.context
    with psycopg.connect(pg_scope.admin) as connection:
        connection.execute("INSERT INTO identity_auth_source_access(tenant_id,workspace_id,source_version_id,user_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,0,1,now())",
            (ctx.tenant_id,ctx.workspace_id,pg_scope.source_version_id,ctx.actor_id))
    store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    try:
        repo=PostgresStudioWorkspaceRepository(store)
        with pytest.raises(StudioError,match="RESOURCE_UNAVAILABLE"):
            repo.record_action(ctx,"approval",{"output_version_id":version,"approval_request_id":pg_scope.request_id,"decision":"approved"},"s24-acl-approve-key-001")
        with pytest.raises(StudioError):
            StudioApprovalLifecycleRepository(store).withdraw(ctx,pg_scope.request_id,version,"s24-acl-withdraw-001")
        with store._transaction(repo._cloud(ctx,"s24.acl.check")) as connection:
            assert connection.execute("SELECT lock_studio_approval_version(%s,%s)",(version,ctx.notebook_id)).fetchone()==(False,)
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute("SELECT count(*) FROM approvals WHERE tenant_id=%s AND output_version_id=%s",(ctx.tenant_id,version)).fetchone()==(0,)
            assert connection.execute("SELECT state FROM approval_requests WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,pg_scope.request_id)).fetchone()==("pending",)
            assert connection.execute("SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,version)).fetchone()==original
    finally:
        store.close()


def test_s24_lifecycle_pg_historical_expiry_due_sweep_and_immutable_payload(pg_scope):
    import hashlib
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioError
    scopes=historical_scopes(pg_scope)
    expired=scopes["legacy_expired"]
    store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=4)
    repo=PostgresStudioWorkspaceRepository(store)
    lifecycle=StudioApprovalLifecycleRepository(store)
    try:
        with pytest.raises(StudioError,match="APPROVAL_REQUEST_EXPIRED"):
            repo.record_action(expired.context,"approval",{"output_version_id":expired.version_id,
                "approval_request_id":expired.request_id,"decision":"approved"},"s24-historical-expired-001")
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute("SELECT state FROM approval_requests WHERE tenant_id=%s AND record_id=%s",
                (expired.context.tenant_id,expired.request_id)).fetchone()==("expired",)
            assert connection.execute("SELECT count(*) FROM approvals WHERE tenant_id=%s AND approval_request_id=%s",
                (expired.context.tenant_id,expired.request_id)).fetchone()==(0,)
        # Both real connections race for the same historical pending batches.
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports=list(pool.map(lambda worker:lifecycle.sweep_due(worker,100),["s24-sweep-a","s24-sweep-b"]))
        assert all(set(report)=={"processed_count","notice_count","expired_count"} for report in reports)
        assert all(0<=report["processed_count"]<=100 for report in reports)
        assert sum(report["expired_count"] for report in reports)==1
        assert lifecycle.sweep_due("s24-sweep-restart",100)=={"processed_count":0,"notice_count":0,"expired_count":0}
        with psycopg.connect(pg_scope.admin) as connection:
            for kind,scope in scopes.items():
                state,canonical,digest,expiry=connection.execute("SELECT state,canonical_text,digest_sha256,"
                    "studio_approval_deadline(canonical_json,created_at) FROM approval_requests WHERE tenant_id=%s AND record_id=%s",
                    (scope.context.tenant_id,scope.request_id)).fetchone()
                assert hashlib.sha256(canonical.encode()).hexdigest()==digest
                assert state==("expired" if kind in {"legacy_expired","legacy_race"} else "pending")
                from datetime import timedelta
                assert expiry==scope.created_at+timedelta(days=1 if kind=="one_day_due" else 7)
                count=connection.execute("SELECT count(*) FROM studio_approval_notices WHERE tenant_id=%s AND approval_request_id=%s",
                    (scope.context.tenant_id,scope.request_id)).fetchone()[0]
                assert count==(0 if kind=="legacy_future" else 1)
                assert ("ttl_days" in scope.payload) is (kind=="one_day_due")
                output=connection.execute("SELECT canonical_text FROM output_versions WHERE tenant_id=%s AND record_id=%s",
                    (scope.context.tenant_id,scope.version_id)).fetchone()[0]
                assert output=='{"content":"S24 original immutable bytes","output_format":"csv"}'
        assert [item["event_kind"] for item in lifecycle.list_notices(scopes["legacy_due"].context)["items"]]==["due_24h"]
        assert lifecycle.list_notices(scopes["legacy_future"].context)=={"items":[],"unread_count":0}
    finally:
        store.close()


def test_s24_lifecycle_pg_empty_downgrade_reupgrade_and_owner_drift_guard(pg_scope):
    """Run on an empty 0066 clone before historical/business seeding, rollback all DDL."""
    import psycopg
    assert pg_scope.revision=="0066"
    with psycopg.connect(pg_scope.admin) as connection:
        if connection.execute("SELECT EXISTS(SELECT 1 FROM approval_requests) OR EXISTS(SELECT 1 FROM approvals) "
            "OR EXISTS(SELECT 1 FROM studio_approval_policy_versions) OR EXISTS(SELECT 1 FROM studio_approval_notices)").fetchone()[0]:
            pytest.skip("NOT RUN: empty-only downgrade node requires an empty controller-owned clone")
        module=migration()
        with connection.transaction(force_rollback=True):
            metadata="SELECT pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity," \
                "(SELECT jsonb_agg(jsonb_build_array(a.grantee,a.privilege_type,a.is_grantable) ORDER BY a.grantee,a.privilege_type) " \
                "FROM aclexplode(c.relacl) a) FROM pg_class c WHERE c.oid='public.approvals'::regclass"
            before=connection.execute(metadata).fetchone()
            with patch.object(module.op,"execute",side_effect=lambda statement:connection.execute(statement,prepare=False)):
                module.downgrade()
            assert connection.execute("SELECT pg_get_userbyid(relowner),has_table_privilege('daon_app',oid,'UPDATE'),"
                "has_table_privilege('daon_app',oid,'DELETE') FROM pg_class WHERE oid='public.approvals'::regclass").fetchone()==("daon_app",False,False)
            connection.execute("UPDATE alembic_version SET version_num='0064' WHERE version_num='0066'")
            with patch.object(module.op,"execute",side_effect=lambda statement:connection.execute(statement,prepare=False)):
                module.upgrade()
            connection.execute("UPDATE alembic_version SET version_num='0066' WHERE version_num='0064'")
            assert connection.execute(metadata).fetchone()==before
        with connection.transaction(force_rollback=True):
            connection.execute("ALTER TABLE approvals OWNER TO daon_app")
            with pytest.raises(psycopg.Error,match="STUDIO_APPROVAL_DOWNGRADE_OWNER_PRECONDITION"):
                with connection.transaction():
                    with patch.object(module.op,"execute",side_effect=lambda statement:connection.execute(statement,prepare=False)):
                        module.downgrade()


def test_s24_lifecycle_pg_data_downgrade_refusal_preserves_schema_and_rows(pg_scope):
    import psycopg
    assert pg_scope.revision=="0066"
    version,_,original=seed_delivery(pg_scope,pending=True)
    with psycopg.connect(pg_scope.admin) as connection:
        with pytest.raises(psycopg.Error,match="STUDIO_APPROVAL_FORWARD_RECOVERY_REQUIRED"):
            with connection.transaction():
                module=migration()
                with patch.object(module.op,"execute",side_effect=lambda statement:connection.execute(statement,prepare=False)):
                    module.downgrade()
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()==("0066",)
        assert connection.execute("SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND record_id=%s",
            (pg_scope.context.tenant_id,version)).fetchone()==original


def seed_policy_consumption(scope,key,*,target=None,policy=None):
    """Synthetic persisted SQL proof, NOT real IdentityService/UI reauthentication."""
    import hashlib
    import json
    import psycopg
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    ctx=scope.context
    suffix=uuid4().hex
    step,session,device=(prefix+suffix for prefix in ("s24-step-","s24-session-","s24-device-"))
    target=target or StudioApprovalLifecycleRepository._policy_target(ctx)
    policy=policy or ctx.policy_version
    group="organization_security_or_connector_policy_change"
    fingerprint=hashlib.sha256(json.dumps([step,ctx.actor_id,session,device,ctx.tenant_id,group,target,policy],separators=(",",":")).encode()).hexdigest()
    with psycopg.connect(scope.admin) as connection:
        connection.execute("INSERT INTO identity_tenants(tenant_id) VALUES(%s) ON CONFLICT DO NOTHING",(ctx.tenant_id,))
        connection.execute("INSERT INTO identity_devices(device_id,tenant_id,user_id,platform,state,last_seen_at,created_at,updated_at) "
            "VALUES(%s,%s,%s,'web','registered',now(),now(),now())",(device,ctx.tenant_id,ctx.actor_id))
        connection.execute("INSERT INTO identity_sessions(session_id,tenant_id,user_id,device_id,client_kind,access_digest,access_expires_at,state,created_at,updated_at) "
            "VALUES(%s,%s,%s,%s,'web',%s,now()+interval '1 hour','active',now(),now())",
            (session,ctx.tenant_id,ctx.actor_id,device,hashlib.sha256(("session|"+suffix).encode()).hexdigest()))
        connection.execute("INSERT INTO identity_step_up_authorizations(step_up_id,authorization_digest,tenant_id,actor_id,session_id,device_id,action_group,target_id,policy_version,issued_at,expires_at,used_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now()+interval '5 minutes',now())",
            (step,hashlib.sha256(("authorization|"+suffix).encode()).hexdigest(),ctx.tenant_id,ctx.actor_id,session,device,group,target,policy))
        connection.execute("INSERT INTO identity_step_up_consumptions(tenant_id,actor_id,operation,idempotency_key,request_fingerprint,step_up_id,consumed_at) "
            "VALUES(%s,%s,'studio.approval.policy.save',%s,%s,%s,now())",(ctx.tenant_id,ctx.actor_id,key,fingerprint,step))
        assert connection.execute("SELECT count(*) FROM identity_step_up_consumptions c "
            "JOIN identity_step_up_authorizations a ON a.step_up_id=c.step_up_id "
            "JOIN identity_sessions s ON s.session_id=a.session_id AND s.user_id=a.actor_id AND s.tenant_id=a.tenant_id "
            "JOIN identity_devices d ON d.device_id=a.device_id AND d.device_id=s.device_id AND d.user_id=a.actor_id AND d.tenant_id=a.tenant_id "
            "WHERE c.tenant_id=%s AND c.actor_id=%s AND c.operation='studio.approval.policy.save' AND c.idempotency_key=%s "
            "AND c.request_fingerprint=%s AND a.used_at IS NOT NULL AND a.used_at<a.expires_at AND s.state='active' AND d.state='registered'",
            (ctx.tenant_id,ctx.actor_id,key,fingerprint)).fetchone()==(1,)


def test_s24_lifecycle_pg_policy_save_receipt_cas_replay_and_deny(pg_scope):
    import psycopg
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace import StudioError
    assert pg_scope.revision=="0066"
    seed_delivery(pg_scope,pending=True)
    ctx=pg_scope.context; store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    repo=StudioApprovalLifecycleRepository(store)
    try:
        assert repo.get_policy(ctx)["policy_version"]==0
        with pytest.raises(StudioError,match="STEP_UP_REQUIRED"):
            repo.save_policy(ctx,30,0,"s24-policy-no-proof-001")
        key="s24-policy-valid-001"
        seed_policy_consumption(pg_scope,key)
        saved,replayed=repo.save_policy(ctx,30,0,key)
        assert replayed is False and saved["ttl_days"]==30 and saved["policy_version"]==1
        assert repo.save_policy(ctx,30,0,key)==(saved,True)
        with pytest.raises(StudioError,match="IDEMPOTENCY_CONFLICT"):
            repo.save_policy(ctx,1,0,key)
        with pytest.raises(StudioError,match="STEP_UP_REQUIRED"):
            repo.save_policy(ctx,1,1,"s24-policy-other-key-001")
        stale="s24-policy-stale-001"
        seed_policy_consumption(pg_scope,stale)
        with pytest.raises(StudioError,match="PRECONDITION_FAILED") as conflict:
            repo.save_policy(ctx,1,0,stale)
        assert conflict.value.status==412
        bad_target="s24-policy-bad-target-001"
        seed_policy_consumption(pg_scope,bad_target,target="s24-other-target")
        with pytest.raises(StudioError,match="STEP_UP_REQUIRED"):
            repo.save_policy(ctx,1,1,bad_target)
        bad_policy="s24-policy-bad-policy-001"
        seed_policy_consumption(pg_scope,bad_policy,policy="s24-other-policy")
        with pytest.raises(StudioError,match="STEP_UP_REQUIRED"):
            repo.save_policy(ctx,1,1,bad_policy)
        viewer="s24-viewer-"+pg_scope.suffix
        with psycopg.connect(pg_scope.admin) as connection:
            connection.execute("INSERT INTO identity_users(user_id,issuer,subject,state) VALUES(%s,'s24-fixture',%s,'active')",(viewer,viewer))
            connection.execute("INSERT INTO identity_auth_memberships(tenant_id,workspace_id,user_id,role,state,version,updated_at) "
                "VALUES(%s,%s,%s,'viewer','active',1,now())",(ctx.tenant_id,ctx.workspace_id,viewer))
        with pytest.raises(StudioError,match="RESOURCE_UNAVAILABLE"):
            repo.save_policy(replace(ctx,actor_id=viewer),1,1,key)
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute("SELECT policy_version,ttl_days FROM studio_approval_policy_versions WHERE tenant_id=%s",(ctx.tenant_id,)).fetchall()==[(1,30)]
    finally:
        store.close()


def test_s24_lifecycle_pg_new_request_freezes_policy_without_rewriting_history(pg_scope):
    import psycopg
    from datetime import timedelta, timezone
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioError
    assert pg_scope.revision=="0066"
    version,_,original=seed_delivery(pg_scope,pending=True)
    ctx=pg_scope.context; old_request=pg_scope.request_id
    store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=2)
    lifecycle=StudioApprovalLifecycleRepository(store); repo=PostgresStudioWorkspaceRepository(store)
    try:
        with psycopg.connect(pg_scope.admin) as connection:
            old=connection.execute("SELECT canonical_text,digest_sha256 FROM approval_requests WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,old_request)).fetchone()
        seed_policy_consumption(pg_scope,"s24-freeze-policy-one")
        lifecycle.save_policy(ctx,1,0,"s24-freeze-policy-one")
        lifecycle.withdraw(ctx,old_request,version,"s24-freeze-withdraw-one")
        payload={"output_version_id":version,"review_request_id":"s24-review-"+pg_scope.suffix}
        saved,replayed=repo.record_action(ctx,"approval_request",payload,"s24-freeze-new-request")
        assert replayed is False
        request=saved["record_id"]
        with psycopg.connect(pg_scope.admin) as connection:
            current=connection.execute("SELECT created_at,canonical_json,canonical_text,digest_sha256,studio_approval_deadline(canonical_json,created_at) "
                "FROM approval_requests WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,request)).fetchone()
            assert current[1]["ttl_days"]==1 and current[1]["ttl_policy_version"]==1
            assert current[4]==current[0]+timedelta(days=1)
            assert current[1]["remind_at"]==current[0].astimezone(timezone.utc).isoformat().replace("+00:00","Z")
        seed_policy_consumption(pg_scope,"s24-freeze-policy-thirty")
        lifecycle.save_policy(ctx,30,1,"s24-freeze-policy-thirty")
        assert repo.record_action(ctx,"approval_request",payload,"s24-freeze-new-request")== (saved,True)
        with pytest.raises(StudioError,match="APPROVAL_REQUEST_PENDING"):
            repo.record_action(ctx,"approval_request",payload,"s24-freeze-duplicate-request")
        with psycopg.connect(pg_scope.admin) as connection:
            assert connection.execute("SELECT canonical_text,digest_sha256 FROM approval_requests WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,old_request)).fetchone()==old
            assert connection.execute("SELECT canonical_text,digest_sha256 FROM approval_requests WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,request)).fetchone()==current[2:4]
            assert connection.execute("SELECT canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,version)).fetchone()==original
    finally:
        store.close()


def test_s24_lifecycle_pg_normal_approval_withdraw_race_has_one_terminal_transition(pg_scope):
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.studio_approval_lifecycle import StudioApprovalLifecycleRepository
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioError
    assert pg_scope.revision=="0066"
    version,_,original=seed_delivery(pg_scope,pending=True)
    ctx=pg_scope.context; store=PostgresCloudStore(pg_scope.app,min_size=0,max_size=4)
    barrier=Barrier(2)
    def consume(action):
        barrier.wait(timeout=5)
        try:
            if action=="approve":
                PostgresStudioWorkspaceRepository(store).record_action(ctx,"approval",{"output_version_id":version,
                    "approval_request_id":pg_scope.request_id,"decision":"approved"},"s24-race-approval-key")
            else:
                StudioApprovalLifecycleRepository(store).withdraw(ctx,pg_scope.request_id,version,"s24-race-withdraw-key")
            return "succeeded"
        except StudioError as error:
            return error.code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(consume,["approve","withdraw"]))
        assert sorted(results)==["APPROVAL_REQUEST_REQUIRED","succeeded"]
        with psycopg.connect(pg_scope.admin) as connection:
            state=connection.execute("SELECT state FROM approval_requests WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,pg_scope.request_id)).fetchone()[0]
            assert state in {"approved","withdrawn"}
            assert connection.execute("SELECT target_state FROM canon_state_transitions WHERE tenant_id=%s AND workspace_id=%s "
                "AND entity_type='ApprovalRequest' AND record_id=%s",(ctx.tenant_id,ctx.workspace_id,pg_scope.request_id)).fetchall()==[(state,)]
            assert connection.execute("SELECT count(*) FROM approvals WHERE tenant_id=%s AND approval_request_id=%s",(ctx.tenant_id,pg_scope.request_id)).fetchone()==(1 if state=="approved" else 0,)
            assert connection.execute("SELECT state,canonical_text,digest_sha256 FROM output_versions WHERE tenant_id=%s AND record_id=%s",(ctx.tenant_id,version)).fetchone()==("approved" if state=="approved" else "in_review",*original)
    finally:
        store.close()
