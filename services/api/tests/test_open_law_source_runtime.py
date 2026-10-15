import importlib
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_open_law_source_import import context


def test_actual_citation_repository_never_uses_ordinary_get_for_law(monkeypatch):
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository, QuestionRepositoryError
    from test_question_answering_postgres import FakeCloudStore, Cursor
    import hashlib
    ctx = context()
    content = '민법 원문 확인'.encode()
    marker = {'origin_kind':'mcp_open_law', 'connector_id':'mcp-open-law-go-kr',
        'snapshot_id':'law-snapshot', 'text_sha256':hashlib.sha256(content).hexdigest()}
    cloud = FakeCloudStore()
    ordinary = cloud.connection.execute
    access = Connection(marker)
    def execute(sql, params=()):
        if 'identity_auth_' in sql or 'FROM identity_users' in sql or sql.startswith(('SELECT canonical_json FROM source_versions', 'SELECT binding.tenant_id',
                           'SELECT role FROM memberships', 'SELECT set_config', 'SELECT w.snapshot_id')):
            return access.execute(sql, params)
        return ordinary(sql, params)
    cloud.connection.execute = execute
    cloud.connection.ready_row = ('source-law','law-version-1','ready','law.txt','0'*32,
        ctx.tenant_id+'/'+ctx.workspace_id+'/source/'+'0'*32, hashlib.sha256(content).hexdigest(),
        len(content),'text/plain','completed')
    evidence = {'kind':'text_source_verbatim_fact','source_id':'source-law','source_version_id':'law-version-1',
        'page':1,'text':'민법 원문','start':0,'end':5}
    cloud.connection.citation_row = ('source-law','law-version-1','1','1',evidence,'mcp_open_law','law.txt','ready','law-span-1',{'kind':'section','value':'law-span-1'})
    class Storage:
        def get(self, key): raise AssertionError('unbounded ordinary storage read')
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true')
    monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID',ctx.workspace_id)
    repo = PostgresQuestionAnsweringRepository(cloud, Storage())
    # Missing bounded law adapter must not fall back to ordinary storage.
    with pytest.raises(QuestionRepositoryError, match='CITATION_CONTENT_UNAVAILABLE'):
        repo.read_citation_content(ctx, 'citation-law')
    reads = []
    def bounded(context, key, digest, size):
        reads.append((key, digest, size))
        return content
    repo = PostgresQuestionAnsweringRepository(cloud, Storage(), open_law_text_reader=bounded)
    actual, locator = repo.read_citation_content(ctx, 'citation-law')
    assert actual.content == content
    assert locator == {'kind':'section','value':'law-span-1'}
    for field, invalid in [('start', 1), ('end', 6), ('page', 2), ('text', '위조')]:
        saved = evidence[field]
        evidence[field] = invalid
        with pytest.raises(QuestionRepositoryError, match='CITATION_CONTENT_UNAVAILABLE'):
            repo.read_citation_content(ctx, 'citation-law')
        evidence[field] = saved
    from daon_user_api.open_law_source_import import OpenLawImportError
    def unavailable(*args): raise OpenLawImportError('OPEN_LAW_TIMEOUT',504)
    failed_reader = PostgresQuestionAnsweringRepository(cloud,Storage(),open_law_text_reader=unavailable)
    with pytest.raises(QuestionRepositoryError) as failed:
        failed_reader.read_citation_content(ctx,'citation-law')
    assert failed.value.status == 503
    reads.clear()
    access.accessible = False
    with pytest.raises(QuestionRepositoryError):
        repo.read_citation_content(ctx, 'citation-law')
    assert reads == []


def test_law_original_runtime_uses_bounded_child_and_checks_full_hash(monkeypatch):
    import hashlib
    import subprocess
    import time
    from daon_user_api import runtime
    from daon_user_api.open_law_source_import import OpenLawImportError
    ctx = context()
    raw = '민법 원문'.encode()
    digest = hashlib.sha256(raw).hexdigest()
    storage = object.__new__(runtime.WebsiteProcessStorage)
    storage._config = {}
    popen = subprocess.Popen
    children = []
    def fixture_child(args, **kwargs):
        assert kwargs.get('shell', False) is False
        assert args[0] == runtime.sys.executable
        child = popen([args[0], '-B', '-c',
            'import sys;sys.stdin.buffer.read();sys.stdout.buffer.write('+repr(raw)+')'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(runtime.subprocess, 'Popen', fixture_child)
    exchange = runtime._website_read_exchange
    def bounded_exchange(child, payload, deadline):
        assert 0 < deadline - time.monotonic() <= 5
        return exchange(child, payload, deadline)
    monkeypatch.setattr(runtime, '_website_read_exchange', bounded_exchange)
    key = ctx.tenant_id+'/'+ctx.workspace_id+'/source/'+'0'*32
    assert runtime._read_open_law_original(ctx, key, digest, len(raw), storage) == raw
    assert len(children) == 1 and children[0].poll() == 0
    with pytest.raises(OpenLawImportError):
        runtime._read_open_law_original(ctx, key, '0'*64, len(raw), storage)
    assert all(child.poll() is not None for child in children)


def test_law_original_timeout_kills_and_reaps_its_real_child(monkeypatch):
    import hashlib
    import subprocess
    import time
    from daon_user_api import runtime
    from daon_user_api.open_law_source_import import OpenLawImportError
    ctx=context();storage=object.__new__(runtime.WebsiteProcessStorage);storage._config={}
    children=[];popen=subprocess.Popen;exchange=runtime._website_read_exchange
    def child(args,**kwargs):
        process=popen([args[0],'-B','-c','import sys,time;sys.stdin.buffer.read();time.sleep(.1)'],**kwargs)
        children.append(process);return process
    monkeypatch.setattr(runtime.subprocess,'Popen',child)
    monkeypatch.setattr(runtime,'_website_read_exchange',lambda c,p,d:exchange(c,p,time.monotonic()+.03))
    with pytest.raises(OpenLawImportError,match='OPEN_LAW_TIMEOUT'):
        runtime._read_open_law_original(ctx,ctx.tenant_id+'/'+ctx.workspace_id+'/source/'+'0'*32,
            hashlib.sha256(b'x').hexdigest(),1,storage)
    assert len(children)==1 and children[0].poll() is not None


@pytest.mark.parametrize('operation', ['enqueue', 'versions'])
def test_actual_studio_consumer_rejects_disabled_law_copy(monkeypatch, operation):
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioContext, StudioError, StudioGenerationRequest
    from test_studio_workspace_postgres import Cloud, Result
    cloud = Cloud()
    execute = cloud.connection.execute
    def law(sql, params=()):
        if sql.startswith('SELECT canonical_json FROM source_versions'):
            return Result(({'origin_kind':'mcp_open_law', 'connector_id':'mcp-open-law-go-kr','snapshot_id':'law-snapshot'},))
        if sql.startswith('SELECT ov.record_id,ov.content_version'):
            return Result(rows=[('version-1',1,None,'draft',{'content':'stored law copy',
                'source_version_ids':['law-version-1']},'settings-1',
                [{'source_version_id':'law-version-1','evidence_span_id':'law-span-1'}],None,None,None,None,None)])
        return execute(sql, params)
    cloud.connection.execute = law
    monkeypatch.delenv('DAON_OPEN_LAW_ENABLED', raising=False)
    repo = PostgresStudioWorkspaceRepository(cloud)
    ctx = StudioContext('tenant','workspace','actor','trace','policy','notebook')
    request = StudioGenerationRequest('evidence_report','law-source-1',('law-version-1',),
        'run-1','result-1','목적','독자',None,'short','summary','pdf','review_required')
    with pytest.raises(StudioError, match='RESOURCE_UNAVAILABLE'):
        if operation == 'enqueue': repo.enqueue_generation(ctx, request, 'law-job')
        else: repo.list_versions(ctx, 'output-1')


@pytest.mark.parametrize('operation', ['revision','export','knowledge_registration'])
def test_studio_new_use_cannot_bypass_disconnected_law_lineage(monkeypatch, operation):
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioContext, StudioError
    from test_studio_workspace_postgres import (Cloud, Result, RevisionConnection,
        ExportConnection, KnowledgeRegistrationConnection, ObjectStorage)
    cloud = Cloud()
    cloud.connection = {'revision':RevisionConnection, 'export':ExportConnection,
        'knowledge_registration':KnowledgeRegistrationConnection}[operation]()
    execute = cloud.connection.execute
    def law(sql, params=()):
        if sql.startswith('SELECT DISTINCT er.source_version_id FROM evidence_references er'):
            return Result(rows=[('law-version-1',)])
        if sql.startswith('SELECT canonical_json FROM source_versions'):
            return Result(({'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'law-snapshot'},))
        return execute(sql, params)
    cloud.connection.execute = law
    storage = ObjectStorage()
    repo = PostgresStudioWorkspaceRepository(cloud,storage)
    ctx = StudioContext('tenant-1','workspace-1','actor-1','trace-1','policy-1','notebook-1')
    monkeypatch.delenv('DAON_OPEN_LAW_ENABLED',raising=False)
    with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE'):
        if operation == 'revision':
            repo.create_version(ctx,'output-1',{'previous_version_id':'version-1',
                'revision_type':'user_edit','change_reason':'정정','content':{'body':'편집'}},'law-edit')
        elif operation == 'export': repo.export_output(ctx,'output-1','version-1','pdf')
        else: repo.record_action(ctx,'knowledge_registration',{'output_version_id':'version-1','explicit':True},'law-register')
    assert storage.content == b''


@pytest.mark.parametrize('operation',['workspace_outputs','report_outputs','sources'])
def test_studio_catalog_hides_revoked_law_without_changing_ordinary_sources(monkeypatch,operation):
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioContext
    from daon_user_api.studio_report_postgres import PostgresStudioReportRepository
    from test_studio_workspace_postgres import Cloud, Result
    cloud=Cloud();execute=cloud.connection.execute
    citation={'citation_id':'citation-law','source_id':'law-source','source_version_id':'law-version',
        'evidence_span_id':'law-span','page':1}
    payload={'output_type':'evidence_report','title':'제목','purpose':'목적','content':'법령 사본','run_id':'run-law','run_result_id':'rr-law'}
    def law(sql,params=()):
        if sql.startswith('SELECT canonical_json FROM source_versions'):
            return Result(({'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'law-snapshot'} if params[-1]=='law-version' else {},))
        if sql.startswith('SELECT DISTINCT er.source_version_id FROM evidence_references er'):
            return Result(rows=[('law-version',)])
        if sql.startswith('SELECT so.record_id,so.canonical_json'):
            return Result(rows=[('output-law',{},'version-law','draft',payload)])
        if sql.startswith('SELECT ov.canonical_json,ov.state'):
            return Result(rows=[(payload,'draft','output-law','version-law',citation)])
        if sql.startswith('SELECT s.record_id,sv.record_id'):
            return Result(rows=[('law-source','law-version','law.txt','ready','accepted','pending'),
                ('source-plain','version-plain','plain.txt','ready','completed','completed')])
        return execute(sql,params)
    cloud.connection.execute=law
    monkeypatch.delenv('DAON_OPEN_LAW_ENABLED',raising=False)
    ctx=StudioContext('tenant-1','workspace-1','actor-1','trace-1','policy-1','notebook-1')
    if operation=='workspace_outputs':
        assert PostgresStudioWorkspaceRepository(cloud).list_outputs(ctx)['outputs']==()
    elif operation=='report_outputs':
        assert PostgresStudioReportRepository(cloud).list_outputs(ctx)==()
    else:
        rows=PostgresStudioReportRepository(cloud).list_sources(ctx)
        assert len(rows)==1 and rows[0].source_version_id=='version-plain'


class Connection:
    def __init__(self,marker=None,accessible=True,policy_allowed=True,*,auth_role='personal_owner',auth_active=True,source_allowed=True,
            required_approver='organization_admin',legacy_role='personal_owner',deny_download=False,user_state='active'):
        self.marker=marker;self.accessible=accessible;self.queries=[];self.policy_allowed=policy_allowed
        self.auth_role=auth_role;self.auth_active=auth_active;self.source_allowed=source_allowed
        self.required_approver=required_approver;self.legacy_role=legacy_role;self.deny_download=deny_download
        self.user_state=user_state
    def execute(self,sql,params=()):
        self.queries.append((sql,params))
        if 'FROM identity_users' in sql:
            return SimpleNamespace(fetchone=lambda:None if self.user_state is None else (self.user_state,))
        if sql.startswith('SELECT * FROM identity_auth_workspaces'):
            return SimpleNamespace(fetchone=lambda:{'workspace_id':params[1],'tenant_id':params[0],
                'workspace_kind':'personal' if self.auth_role=='personal_owner' else 'organization',
                'data_area':'workspace','cost_limit_cents':0,'acl_version':1,'version':1,'updated_at':'2026-10-16'})
        if 'FROM identity_auth_tenant_roles' in sql:
            row={'role':self.auth_role,'version':1} if self.auth_active and self.auth_role in ('personal_owner','organization_admin') else None
            return SimpleNamespace(fetchone=lambda:row)
        if 'FROM identity_auth_memberships' in sql:
            row={'role':self.auth_role,'version':1} if self.auth_active and self.auth_role not in ('personal_owner','organization_admin') else None
            return SimpleNamespace(fetchone=lambda:row)
        if 'FROM identity_auth_tenant_policies' in sql or 'FROM identity_auth_workspace_policies' in sql:
            return SimpleNamespace(fetchone=lambda:{'version':1})
        if 'FROM identity_auth_tenant_permission_rules' in sql or 'FROM identity_auth_workspace_permission_rules' in sql:
            row={'effect':'deny','locked':1} if self.deny_download and params[-1]=='file_download_share' else None
            return SimpleNamespace(fetchone=lambda:row)
        if 'FROM identity_auth_source_access' in sql:
            return SimpleNamespace(fetchone=lambda:{'allowed':int(self.source_allowed)})
        if sql.startswith('SELECT canonical_json FROM source_versions'):
            return SimpleNamespace(fetchone=lambda:(self.marker or {},))
        if sql.startswith('SELECT binding.tenant_id'):
            payload={'mode':'allow_approved_external','allowed_provider_kinds':['external_api'],'allowed_destinations':['www.law.go.kr'],
                'classification':'public','max_bytes':2097152,'masking_required':True,'redaction_required':True,'required_approver':self.required_approver}
            if not self.policy_allowed:payload.update(mode='deny_external',allowed_provider_kinds=[],allowed_destinations=[],max_bytes=0)
            return SimpleNamespace(fetchone=lambda:(params[0],params[0],params[2],params[1],'policy-1',1,'active','binding-1',1,True,True,payload))
        if sql.startswith('SELECT role FROM memberships'):return SimpleNamespace(fetchone=lambda:(self.legacy_role,))
        return SimpleNamespace(fetchone=lambda:('snapshot-law',) if self.accessible else None)


def check(connection,ctx=None):
    api=importlib.import_module('daon_user_api.open_law_source_access')
    return api.require_open_law_source_access(ctx or context(),'law-version-1',connection)


def test_mcp_marker_with_hidden_snapshot_fails_closed():
    with patch.dict(os.environ,{'DAON_OPEN_LAW_ENABLED':'true','DAON_OPEN_LAW_QA_WORKSPACE_ID':'workspace-law'}):
        with pytest.raises(ValueError,match='^OPEN_LAW_ACCESS_DENIED$'):
            check(Connection({'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'snapshot-law'},False))


def test_disconnect_blocks_saved_source_consumers():
    marker={'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'snapshot-law'}
    with patch.dict(os.environ,{'DAON_OPEN_LAW_ENABLED':'true','DAON_OPEN_LAW_QA_WORKSPACE_ID':'workspace-law'}):
        check(Connection(marker,True))
        with pytest.raises(ValueError):check(Connection(marker,False))
    with patch.dict(os.environ,{'DAON_OPEN_LAW_ENABLED':'false'}):
        with pytest.raises(ValueError):check(Connection(marker,True))
    ordinary=Connection({'filename':'law.txt'})
    check(ordinary);assert len(ordinary.queries)==1


def test_current_policy_revocation_blocks_mcp_stored_copy_not_ordinary_text():
    marker={'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'snapshot-law'}
    with patch.dict(os.environ,{'DAON_OPEN_LAW_ENABLED':'true','DAON_OPEN_LAW_QA_WORKSPACE_ID':'workspace-law'}):
        with pytest.raises(ValueError,match='^OPEN_LAW_ACCESS_DENIED$'):check(Connection(marker,True,False))
    check(Connection({'filename':'ordinary.txt'},True,False))


@pytest.mark.parametrize('condition',['auth_inactive','source_acl'])
def test_current_auth_revocation_is_not_overridden_by_legacy_role(monkeypatch,condition):
    marker={'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'snapshot-law'}
    conn=Connection(marker,auth_active=condition!='auth_inactive',source_allowed=condition!='source_acl')
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID','workspace-law')
    with pytest.raises(ValueError,match='OPEN_LAW_ACCESS_DENIED'):check(conn)


@pytest.mark.parametrize('state',['disabled','pending_email','pending_admin_approval',None])
def test_inactive_current_identity_blocks_law_without_changing_ordinary_sources(monkeypatch,state):
    marker={'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'snapshot-law'}
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID','workspace-law')
    with pytest.raises(ValueError,match='OPEN_LAW_ACCESS_DENIED'):check(Connection(marker,user_state=state))
    ordinary=Connection({'filename':'ordinary.txt'},user_state=state)
    check(ordinary)
    assert len(ordinary.queries)==1


def test_current_auth_permission_denial_blocks_law_export_before_object_io(monkeypatch):
    from daon_user_api.studio_workspace_postgres import PostgresStudioWorkspaceRepository
    from daon_user_api.studio_workspace import StudioContext,StudioError
    from test_studio_workspace_postgres import Cloud,Result,ExportConnection,ObjectStorage
    cloud=Cloud();cloud.connection=ExportConnection();execute=cloud.connection.execute
    auth=Connection({'origin_kind':'mcp_open_law','connector_id':'mcp-open-law-go-kr','snapshot_id':'law-snapshot'},deny_download=True)
    def sql(statement,params=()):
        if statement.startswith('SELECT DISTINCT er.source_version_id FROM evidence_references er'):return Result(rows=[('law-version-1',)])
        if ('identity_auth_' in statement or 'FROM identity_users' in statement or statement.startswith(('SELECT canonical_json FROM source_versions','SELECT binding.tenant_id',
                'SELECT role FROM memberships','SELECT set_config','SELECT w.snapshot_id'))):return auth.execute(statement,params)
        return execute(statement,params)
    cloud.connection.execute=sql
    monkeypatch.setenv('DAON_OPEN_LAW_ENABLED','true');monkeypatch.setenv('DAON_OPEN_LAW_QA_WORKSPACE_ID','workspace-1')
    storage=ObjectStorage();repo=PostgresStudioWorkspaceRepository(cloud,storage)
    with pytest.raises(StudioError,match='RESOURCE_UNAVAILABLE'):
        repo.export_output(StudioContext('tenant-1','workspace-1','actor-1','trace-1','policy-1','notebook-1'),'output-1','version-1','pdf')
    assert storage.content==b''
