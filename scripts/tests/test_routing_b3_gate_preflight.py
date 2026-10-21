"""Docker/DB를 실행하지 않는 B3 gate 사전검사 음성 시험."""
import importlib.util
from pathlib import Path
import pytest
P=Path(__file__).with_name('routing-b3-postgres-gate.py')
spec=importlib.util.spec_from_file_location('b3_gate',P);gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
@pytest.mark.parametrize('mutation',[
 {'database':'daon_user'}, {'host':'local-postgres'}, {'host':'172.27.0.1'},
 {'owner':'daon_app'}, {'app_role':'postgres'}, {'dsn':'postgresql://secret@live/db'},
 {'pg_id':'short'}, {'revision':'0075'}, {'source_root':'/tmp/other'},
])
def test_nonapproved_target_fails_before_any_io(mutation):
    config=dict(host='127.0.0.1',database='qa_routing_b3_20261021',owner='postgres',
      app_role='qa_routing_b3_app_20261021',pg_id='a'*64,revision='0072',source_root=gate.ROOT)
    with pytest.raises(ValueError):gate.validate_target({**config,**mutation})


def snapshot():
 return dict(Id='a'*64,Name='/'+gate.PG,Image=gate.PG_IMAGE,Config={'Labels':{'io.daon.qa.issue':gate.ISSUE,'io.daon.qa.revision':'b'*40}},
 HostConfig=dict(NetworkMode='none',PortBindings={},Binds=None,Privileged=False,Memory=1073741824,NanoCpus=1000000000,Devices=[],VolumesFrom=None,Tmpfs={'/var/lib/postgresql/data':'rw,noexec,nosuid,size=536870912'}),Mounts=[])

@pytest.mark.parametrize('mutate',[
 lambda v:v.update(Name='/local-postgres'),lambda v:v.update(Image='sha256:'+'f'*64),
 lambda v:v['Config']['Labels'].update({'io.daon.qa.issue':'other'}),
 lambda v:v['HostConfig'].update(NetworkMode='bridge'),lambda v:v['HostConfig'].update(PortBindings={'5432/tcp':[{'HostPort':'5432'}]}),
 lambda v:v['HostConfig'].update(Binds=['/data:/data']),lambda v:v['HostConfig'].update(Privileged=True),
 lambda v:v['HostConfig'].update(Memory=2147483648),lambda v:v['HostConfig'].update(NanoCpus=2000000000),
 lambda v:v['HostConfig'].update(Tmpfs={}),lambda v:v['Mounts'].append({'Type':'volume','Destination':'/var/lib/postgresql/data'}),
 lambda v:v['HostConfig'].update(PidMode='host'),
])
def test_drifted_or_unowned_container_is_rejected(mutate):
 v=snapshot();mutate(v)
 with pytest.raises(ValueError):gate.verify_container(v,kind='pg',revision='b'*40)

def test_exact_snapshot_and_fixed_target_are_accepted_without_io():
 assert gate.verify_container(snapshot(),kind='pg',revision='b'*40)=='a'*64
 assert gate.validate_target(dict(host='127.0.0.1',database=gate.DB,owner='postgres',app_role=gate.APP,pg_id='a'*64,revision='0072',source_root=gate.ROOT))

def test_unexpected_sql_bytes_rejected(tmp_path):
 root=tmp_path/'services/api/migrations/versions';root.mkdir(parents=True)
 for name in gate.HASHES:(root/name).write_bytes(b'not the approved SQL')
 with pytest.raises(ValueError,match='B3_SQL_HASH_MISMATCH'):gate.verify_sql(tmp_path)

def test_gate_import_has_no_database_or_docker_execution():
 source=P.read_text(encoding='utf-8')
 assert 'if __name__==' in source
 assert 'upgrade head' not in source and "migrate('0072')" in source and "migrate('0040',app=True)" in source
 assert 'B3_0073_FORBIDDEN' in source


def test_runner_must_share_only_exact_pg_network_namespace_and_have_read_only_root():
 v=snapshot();v['Name']='/'+gate.RUNNER;v['Image']='sha256:'+'c'*64;v['Config']['User']='65534:65534'
 v['HostConfig'].update(Memory=536870912,NetworkMode='container:'+'a'*64,ReadonlyRootfs=True,Tmpfs={'/tmp':'rw,noexec,nosuid,size=134217728'})
 assert gate.verify_container(v,kind='runner',revision='b'*40,pg_id='a'*64,qa_image=v['Image'])=='a'*64
 for field,value in [('NetworkMode','container:'+'f'*64),('ReadonlyRootfs',False)]:
  from copy import deepcopy
  bad=deepcopy(v);bad['HostConfig'][field]=value
  with pytest.raises(ValueError):gate.verify_container(bad,kind='runner',revision='b'*40,pg_id='a'*64,qa_image=v['Image'])


class DomainDenial(Exception):
 def __init__(self,code,status):self.code=code;self.status=status
@pytest.mark.parametrize('error',[ValueError('syntax'),DomainDenial('WRONG_CODE',403),DomainDenial('EXPECTED',503)])
def test_unexpected_error_is_never_counted_as_expected_denial(error):
 def failing():raise error
 with pytest.raises(Exception):gate.expect_denial(failing,DomainDenial,'EXPECTED',403)


def test_only_exact_domain_denial_is_accepted():
 def denied():raise DomainDenial('EXPECTED',403)
 assert gate.expect_denial(denied,DomainDenial,'EXPECTED',403) is None
 with pytest.raises(ValueError,match='B3_EXPECTED_DENIAL_MISSING'):gate.expect_denial(lambda:None,DomainDenial,'EXPECTED',403)


def test_restrictive_source_umask_does_not_prevent_rootless_image_code_read():
 shell=P.parent.parent.joinpath('run-routing-b3-postgres-gate.sh').read_text(encoding='utf-8')
 dockerfile=shell.split('cat > "$STATE/context/Dockerfile" <<EOF',1)[1].split('\nEOF',1)[0]
 copy_end=dockerfile.rindex('COPY ')
 install=dockerfile.index('uv sync --frozen')
 before=dockerfile.index('chmod -R a+rX /qa')
 after=dockerfile.rindex('chmod -R a+rX /qa')
 assert copy_end<before<install<after
 assert '--user 65534:65534' in shell
 # 이미지의 a+rX는 쓰기 권한을 늘리지 않고 각 source mode의 읽기/탐색만 보장한다.
 for mask in (0o077,0o027,0o022):
  file_mode=0o666 & ~mask;directory_mode=0o777 & ~mask
  assert ((file_mode|0o444)&0o004)==0o004
  assert ((directory_mode|0o555)&0o005)==0o005
 assert 'chmod -R a+rX "$ROOT"' not in shell and 'chmod -R a+rX "$STATE"' not in shell


@pytest.mark.parametrize('kind',['table','relation','sequence','function'])
def test_permission_diagnostic_extracts_only_exact_safe_identifier(kind):
 from types import SimpleNamespace
 inner=SimpleNamespace(sqlstate='42501',diag=SimpleNamespace(sqlstate='42501',column_name=None,message_primary='permission denied for '+kind+' routing_authorization_plans'),__context__=None,__cause__=None)
 outer=SimpleNamespace(sqlstate=None,diag=None,__context__=inner,__cause__=None)
 assert gate.safe_error_metadata(outer)==dict(sqlstate='42501',column=None,permission_type=kind,permission_name='routing_authorization_plans',constraint=None,table=None)

@pytest.mark.parametrize('message',[
 'permission denied for table secret https://example.com',
 'permission denied for table schema.name','permission denied for table "quoted name"',
 'permission denied for function name(text)','permission denied for table name\nsecret',
 'password authentication failed','permission denied for database some_database',
 'prefix permission denied for table safe_name',
])
def test_permission_diagnostic_never_emits_raw_message_or_nonallowlisted_name(message):
 from types import SimpleNamespace
 error=SimpleNamespace(sqlstate='42501',diag=SimpleNamespace(sqlstate='42501',column_name=None,message_primary=message),__context__=None,__cause__=None)
 output=gate.safe_error_metadata(error)
 assert output['permission_type'] is None and output['permission_name'] is None
 assert 'message' not in output


def test_error_diagnostic_retains_safe_sqlstate_and_column_without_raw_values():
 from types import SimpleNamespace
 error=SimpleNamespace(sqlstate='23502',diag=SimpleNamespace(sqlstate='23502',column_name='created_at',message_primary='raw secret must never be printed'),__context__=None,__cause__=None)
 assert gate.safe_error_metadata(error)==dict(sqlstate='23502',column='created_at',permission_type=None,permission_name=None,constraint=None,table=None)
 error.__context__=error
 assert gate.safe_error_metadata(error)['column']=='created_at'
 error.sqlstate='unsafe state';error.diag.sqlstate=None;error.diag.column_name='column with raw text'
 assert gate.safe_error_metadata(error)==dict(sqlstate=None,column=None,permission_type=None,permission_name=None,constraint=None,table=None)


def test_execution_gate_pins_reviewed_product_commit_and_denies_other_product_drift():
 shell=P.parent.parent.joinpath('run-routing-b3-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=ac537f7a689153e30491627a8610b4e683ac969d' in shell
 assert 'git merge-base --is-ancestor "$PRODUCT_BASE" "$revision"' in shell
 assert 'git diff --name-only "$PRODUCT_BASE" "$revision"' in shell
 assert 'scripts/run-routing-b3-postgres-gate.sh|scripts/tests/routing-b3-postgres-gate.py|scripts/tests/test_routing_b3_gate_preflight.py) ;; *) safe product_drift FAIL; exit 22' in shell
 assert '04f72f837a30d48830e36f38426c51b096027ee1' not in shell


def test_wrapper_root_is_single_static_configuration():
 wrapper=P.parent.parent/'run-routing-b3-postgres-gate.sh'
 assert gate.read_approved_root(wrapper)==gate.ROOT
 source=wrapper.read_text(encoding='utf-8')
 assert 'scripts/run-routing-b3-postgres-gate.sh | tar' in source
 assert 'COPY scripts/run-routing-b3-postgres-gate.sh /qa/scripts/run-routing-b3-postgres-gate.sh' in source

@pytest.mark.parametrize('content',[
 '', 'readonly ROOT=$HOME/other', 'readonly ROOT=$(pwd)',
 'readonly ROOT=/tmp/other', 'readonly ROOT=relative/other',
 'readonly ROOT='+gate.ROOT+';echo bad',
 'readonly ROOT='+gate.ROOT+'\nROOT=/tmp/other',
 'readonly ROOT='+gate.ROOT+'\nreadonly ROOT='+gate.ROOT,
 'readonly ROOT='+gate.ROOT.replace('/deploy/','/deploy/../deploy/'),
 'readonly ROOT="'+gate.ROOT+'"',
])
def test_wrapper_root_rejects_nonliteral_or_ambiguous_configuration(tmp_path,content):
 wrapper=tmp_path/'gate.sh';wrapper.write_text(content,encoding='utf-8')
 with pytest.raises(ValueError,match='B3_WRAPPER_'):gate.read_approved_root(wrapper)

def test_wrapper_root_rejects_missing_and_oversize_files(tmp_path):
 wrapper=tmp_path/'gate.sh'
 with pytest.raises(ValueError,match='B3_WRAPPER_'):gate.read_approved_root(wrapper)
 wrapper.write_bytes(b'x'*65537)
 with pytest.raises(ValueError,match='B3_WRAPPER_'):gate.read_approved_root(wrapper)

def test_wrapper_root_rejects_symlink_before_read(tmp_path,monkeypatch):
 wrapper=tmp_path/'gate.sh';wrapper.write_text('readonly ROOT='+gate.ROOT,encoding='utf-8')
 original=Path.is_symlink
 monkeypatch.setattr(Path,'is_symlink',lambda self: self==wrapper or original(self))
 with pytest.raises(ValueError,match='B3_WRAPPER_'):gate.read_approved_root(wrapper)


def test_b3_runner_uid_and_safe_output_are_closed(capsys):
 from copy import deepcopy
 value=snapshot();value['Name']='/'+gate.RUNNER;value['Image']='sha256:'+'c'*64
 value['Config']['User']='root';value['HostConfig'].update(Memory=536870912,NetworkMode='container:'+'a'*64,ReadonlyRootfs=True,Tmpfs={'/tmp':'rw,noexec,nosuid,size=134217728'})
 with pytest.raises(ValueError,match='B3_RUNNER_USER_INVALID'):gate.verify_container(value,kind='runner',revision='b'*40,pg_id='a'*64,qa_image=value['Image'])


def test_b3_safe_output_rejects_secret_field_before_print(capsys):
 import secrets
 secret=secrets.token_urlsafe(32)
 with pytest.raises(ValueError,match='B3_OUTPUT_FIELDS_INVALID'):gate.emit('probe',status='PASS',password=secret)
 assert secret not in capsys.readouterr().out


def test_b3_execution_gate_keeps_b2_pin_and_exact_new_three_file_boundary():
 shell=P.parent.parent.joinpath('run-routing-b3-postgres-gate.sh').read_text(encoding='utf-8')
 b2=P.parent.parent.joinpath('run-routing-b2-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=ac537f7a689153e30491627a8610b4e683ac969d' in shell
 assert 'readonly PRODUCT_BASE=3bfb0f3d29d5f4bace7d26541a34500cbc128d10' in b2
 assert '--network none --memory 1g --cpus 1' in shell
 assert 'unix:///var/run/docker.sock' in shell
 assert 'scripts/run-routing-b2-postgres-gate.sh' not in shell
 assert 'scripts/tests/routing-b2-postgres-gate.py' not in shell
 assert 'm._current_access=' not in P.read_text(encoding='utf-8')


def test_b3_image_and_sql_fingerprints_match_approved_exact_bytes():
 shell=P.parent.parent.joinpath('run-routing-b3-postgres-gate.sh').read_text(encoding='utf-8')
 approved='python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae'
 assert gate.BASE==approved
 assert 'readonly BASE='+approved+'\n' in shell
 assert gate.PG_IMAGE=='sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e'
 assert gate.HASHES=={'0074_routing_policy.sql':'03568962142b86c611bf0a4811540b75b7b645373af0cafdbb7672f433a196fe','0075_routing_execution.sql':'5b8e8dc3e3426081c488177db5eaaa55d209a051eb0288ddecaa7c092d2c5e5d'}


def test_b3_image_installs_locked_api_dev_group_and_preflights_actual_import_closure():
 shell=P.parent.parent.joinpath('run-routing-b3-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'uv sync --frozen --package daon-user-api --group dev --no-install-project' in shell
 assert 'uv sync --frozen --package daon-user-api --no-dev' not in shell
 assert 'm.version("httpx")=="0.28.1"' in shell
 assert 'from httpx import ASGITransport' in shell
 assert 'from test_identity_support import create_service,FakeVerifiedOidcProvider,MutableClock' in shell
 assert 'from daon_user_api.runtime import create_app' in shell


def test_b3_legacy_denials_use_real_repository_and_inherited_current_access():
 import ast
 source=P.read_text(encoding='utf-8');tree=ast.parse(source)
 port=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='Port')
 recheck=next(n for n in port.body if isinstance(n,ast.FunctionDef) and n.name=='recheck')
 assert len(recheck.body)==1 and isinstance(recheck.body[0],ast.Expr)
 assert ast.unparse(recheck.body[0])=='self._current_access(conn, context)'
 group=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='group3')
 body=ast.get_source_segment(source,group)
 assert "'ROUTING_AUTHORIZATION_INVALID',403" in body
 assert "'ROUTING_SESSION_REQUIRED',401" in body
 assert "'ROUTING_AUTHORIZATION_EXPIRED',403" in body
 assert 'ROUTING_CURRENT_ACCESS_DENIED' not in body
 assert 'RoutingExecutionService(repo,p).execute(fresh,' in body


def test_synthetic_identity_real_login_bootstrap_and_runtime_dependency_composition(tmp_path,monkeypatch):
 import sys
 from datetime import datetime,timezone
 root=P.parents[2]
 monkeypatch.syspath_prepend(str(root/'services/api/src'))
 monkeypatch.syspath_prepend(str(root/'services/api/tests'))
 from daon_user_api.authorization import AuthorizationService,SqliteAuthorizationRepository,Role
 from daon_user_api.runtime import RuntimeDependencies,RuntimeSettings,create_app
 path=tmp_path/'identity.sqlite3';tenant='tenant-b3-qa';workspace='workspace-b3-qa'
 identity,repository,audit,clock=gate.create_identity_fixture(path,datetime.now(timezone.utc),tenant)
 owner=gate.synthetic_login(identity,tenant);other=gate.synthetic_login(identity,tenant)
 assert owner.user_id!=other.user_id
 auth_repository=SqliteAuthorizationRepository(path)
 auth_repository.bootstrap_workspace(tenant_id=tenant,workspace_id=workspace,owner_user_id=owner.user_id,owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',data_area='cloud_sync',cost_limit_cents=1000,now=clock())
 authorization=AuthorizationService(repository=auth_repository,audit_store=audit,clock=clock,identity_service=identity)
 dependencies=RuntimeDependencies(settings=RuntimeSettings.for_test(database_path=path,policy_version='policy-b3-qa'),identity_service=identity,identity_repository=repository,authorization_service=authorization,authorization_repository=auth_repository,audit_store=audit)
 assert create_app(dependencies) is not None


def test_fixture_stage_and_exception_diagnostics_are_closed_and_secret_safe(capsys):
 import secrets
 secret=secrets.token_urlsafe(32)
 class UnknownFailure(Exception):pass
 assert gate.safe_exception_type(ValueError(secret))=='ValueError'
 assert gate.safe_exception_type(UnknownFailure(secret))=='Exception'
 gate.emit('fixture_identity',status='FAIL',exception_type=gate.safe_exception_type(ValueError(secret)))
 assert secret not in capsys.readouterr().out
 with pytest.raises(ValueError,match='B3_OUTPUT_FIELDS_INVALID'):gate.emit('fixture_identity',status='FAIL',exception_type=secret)
 assert capsys.readouterr().out==''
 source=P.read_text(encoding='utf-8')
 for stage in ['imports','identity','mirror','source','policy','runtime']:
  assert "emit('fixture_"+stage+"',status='RUNNING')" in source


def test_group2_uses_real_append_only_unbinding_and_substage_diagnostics():
 import ast
 source=P.read_text(encoding='utf-8');tree=ast.parse(source)
 group=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='group2')
 body=ast.get_source_segment(source,group)
 assert 'DELETE FROM notebook_bindings' not in body
 assert 'PostgresNotebookRepository(store).unbind_source' in body
 for stage in ['http','source_acl','role','binding','retention']:
  assert "emit('group2_"+stage+"',status='RUNNING')" in body
 assert "emit('group5_persist',status='RUNNING')" in source
 assert "emit('group5_projection',status='RUNNING')" in source
 assert "emit(name,status='FAIL',test_name=CURRENT_TEST," in source


def test_safe_sql_diagnostic_identifiers_are_bounded_without_raw_values(capsys):
 from types import SimpleNamespace
 error=SimpleNamespace(sqlstate='23503',diag=SimpleNamespace(column_name=None,constraint_name='fk_attempt_run',table_name='model_attempts'),__cause__=None,__context__=None)
 result=gate.safe_error_metadata(error)
 assert result['constraint']=='fk_attempt_run' and result['table']=='model_attempts'
 error.diag.constraint_name='secret sql raw value';error.diag.table_name='a'*64
 result=gate.safe_error_metadata(error)
 assert result['constraint'] is None and result['table'] is None
 assert gate.safe_exception_type(type('ForeignKeyViolation',(Exception,),{})())=='ForeignKeyViolation'
 assert gate.safe_exception_type(type('InsufficientPrivilege',(Exception,),{})())=='InsufficientPrivilege'
 with pytest.raises(ValueError,match='B3_OUTPUT_FIELDS_INVALID'):gate.emit('group2',status='FAIL',constraint='unsafe message')
 assert capsys.readouterr().out==''


def test_pinned_writer_fixture_keeps_fk_negative_and_real_parent_positive():
 import ast
 source=P.read_text(encoding='utf-8');tree=ast.parse(source)
 group=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='group5')
 body=ast.get_source_segment(source,group)
 assert "emit('group5_missing_parent',status='RUNNING')" in body
 assert "'operations_cost_receipts_provider_connection_id_fkey'" in body
 assert 'B3_PINNED_ROLLBACK_FAILED' in body
 assert 'INSERT INTO system_provider_connections' in body
 assert "false,'unverified'" in body
 assert body.index('group5_missing_parent')<body.index('INSERT INTO system_provider_connections')<body.index('result=persist()')
 assert 'connection_id=None' not in body and 'provider_called=False' not in body
