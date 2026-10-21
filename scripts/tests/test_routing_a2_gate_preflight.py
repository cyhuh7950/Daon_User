"""Docker/DB를 실행하지 않는 A2 gate 사전검사 음성 시험."""
import importlib.util
from pathlib import Path
import pytest
P=Path(__file__).with_name('routing-a2-postgres-gate.py')
spec=importlib.util.spec_from_file_location('a2_gate',P);gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
@pytest.mark.parametrize('mutation',[
 {'database':'daon_user'}, {'host':'local-postgres'}, {'host':'172.27.0.1'},
 {'owner':'daon_app'}, {'app_role':'postgres'}, {'dsn':'postgresql://secret@live/db'},
 {'pg_id':'short'}, {'revision':'0075'}, {'source_root':'/tmp/other'},
])
def test_nonapproved_target_fails_before_any_io(mutation):
    config=dict(host='127.0.0.1',database='qa_routing_a2_20261021',owner='postgres',
      app_role='qa_routing_a2_app_20261021',pg_id='a'*64,revision='0072',source_root=gate.ROOT)
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
 with pytest.raises(ValueError,match='A2_SQL_HASH_MISMATCH'):gate.verify_sql(tmp_path)

def test_gate_import_has_no_database_or_docker_execution():
 source=P.read_text(encoding='utf-8')
 assert 'if __name__==' in source
 assert 'upgrade head' not in source and "migrate('0072')" in source and "migrate('0040',app=True)" in source
 assert 'A2_0073_FORBIDDEN' in source


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
 with pytest.raises(ValueError,match='A2_EXPECTED_DENIAL_MISSING'):gate.expect_denial(lambda:None,DomainDenial,'EXPECTED',403)


def test_restrictive_source_umask_does_not_prevent_rootless_image_code_read():
 shell=P.parent.parent.joinpath('run-routing-a2-postgres-gate.sh').read_text(encoding='utf-8')
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
 shell=P.parent.parent.joinpath('run-routing-a2-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=70dfd1cbca7c2587c949596ff094b5e1752d2cce' in shell
 assert 'git merge-base --is-ancestor "$PRODUCT_BASE" "$revision"' in shell
 assert 'git diff --name-only "$PRODUCT_BASE" "$revision"' in shell
 assert 'scripts/run-routing-a2-postgres-gate.sh|scripts/tests/routing-a2-postgres-gate.py|scripts/tests/test_routing_a2_gate_preflight.py) ;; *) safe product_drift FAIL; exit 22' in shell
 assert '04f72f837a30d48830e36f38426c51b096027ee1' not in shell


def test_wrapper_root_is_single_static_configuration():
 wrapper=P.parent.parent/'run-routing-a2-postgres-gate.sh'
 assert gate.read_approved_root(wrapper)==gate.ROOT
 source=wrapper.read_text(encoding='utf-8')
 assert 'scripts/run-routing-a2-postgres-gate.sh | tar' in source
 assert 'COPY scripts/run-routing-a2-postgres-gate.sh /qa/scripts/run-routing-a2-postgres-gate.sh' in source

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
 with pytest.raises(ValueError,match='A2_WRAPPER_'):gate.read_approved_root(wrapper)

def test_wrapper_root_rejects_missing_and_oversize_files(tmp_path):
 wrapper=tmp_path/'gate.sh'
 with pytest.raises(ValueError,match='A2_WRAPPER_'):gate.read_approved_root(wrapper)
 wrapper.write_bytes(b'x'*65537)
 with pytest.raises(ValueError,match='A2_WRAPPER_'):gate.read_approved_root(wrapper)

def test_wrapper_root_rejects_symlink_before_read(tmp_path,monkeypatch):
 wrapper=tmp_path/'gate.sh';wrapper.write_text('readonly ROOT='+gate.ROOT,encoding='utf-8')
 original=Path.is_symlink
 monkeypatch.setattr(Path,'is_symlink',lambda self: self==wrapper or original(self))
 with pytest.raises(ValueError,match='A2_WRAPPER_'):gate.read_approved_root(wrapper)


def test_a2_runner_uid_and_safe_output_are_closed(capsys):
 from copy import deepcopy
 value=snapshot();value['Name']='/'+gate.RUNNER;value['Image']='sha256:'+'c'*64
 value['Config']['User']='root';value['HostConfig'].update(Memory=536870912,NetworkMode='container:'+'a'*64,ReadonlyRootfs=True,Tmpfs={'/tmp':'rw,noexec,nosuid,size=134217728'})
 with pytest.raises(ValueError,match='A2_RUNNER_USER_INVALID'):gate.verify_container(value,kind='runner',revision='b'*40,pg_id='a'*64,qa_image=value['Image'])


def test_a2_safe_output_rejects_secret_field_before_print(capsys):
 import secrets
 secret=secrets.token_urlsafe(32)
 with pytest.raises(ValueError,match='A2_OUTPUT_FIELDS_INVALID'):gate.emit('probe',status='PASS',password=secret)
 assert secret not in capsys.readouterr().out


def test_a2_execution_gate_keeps_b2_pin_and_exact_new_three_file_boundary():
 shell=P.parent.parent.joinpath('run-routing-a2-postgres-gate.sh').read_text(encoding='utf-8')
 b2=P.parent.parent.joinpath('run-routing-b2-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=70dfd1cbca7c2587c949596ff094b5e1752d2cce' in shell
 assert 'readonly PRODUCT_BASE=3bfb0f3d29d5f4bace7d26541a34500cbc128d10' in b2
 assert '--network none --memory 1g --cpus 1' in shell
 assert 'unix:///var/run/docker.sock' in shell
 assert 'scripts/run-routing-b2-postgres-gate.sh' not in shell
 assert 'scripts/tests/routing-b2-postgres-gate.py' not in shell
 assert 'm._current_access=' not in P.read_text(encoding='utf-8')


def test_a2_image_and_sql_fingerprints_match_approved_exact_bytes():
 shell=P.parent.parent.joinpath('run-routing-a2-postgres-gate.sh').read_text(encoding='utf-8')
 approved='python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae'
 assert gate.BASE==approved
 assert 'readonly BASE='+approved+'\n' in shell
 assert gate.PG_IMAGE=='sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e'
 assert gate.HASHES=={'0074_routing_policy.sql':'03568962142b86c611bf0a4811540b75b7b645373af0cafdbb7672f433a196fe','0075_routing_execution.sql':'5b8e8dc3e3426081c488177db5eaaa55d209a051eb0288ddecaa7c092d2c5e5d','0076_routing_budget_sources.sql':'8d2fc5efc942c3a012e4432135d359308ef6844b92f1b3b9467fd7a41796d732'}


def test_a2_image_installs_locked_api_dev_group_and_preflights_actual_import_closure():
 shell=P.parent.parent.joinpath('run-routing-a2-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'uv sync --frozen --package daon-user-api --group dev --no-install-project' in shell
 assert 'uv sync --frozen --package daon-user-api --no-dev' not in shell
 assert 'm.version("httpx")=="0.28.1"' in shell
 assert 'from httpx import ASGITransport' in shell
 assert 'from test_identity_support import create_service,FakeVerifiedOidcProvider,MutableClock' in shell
 assert 'from daon_user_api.runtime import create_app' in shell




def test_actual_identity_fixture_password_reauthentication_and_exact_consumption(tmp_path):
 import sys
 from datetime import datetime,timezone
 root=P.parents[2]
 for path in (root/'services/api/src',root/'services/api/tests'):
  if str(path) not in sys.path:sys.path.insert(0,str(path))
 q=gate.create_budget_fixture(tmp_path,datetime.now(timezone.utc),'tenant-a2-local','workspace-a2-local','owner-a2-local')
 try:
  request=gate.prepare_budget(q,key='qa-issue-key-local-01')
  q['adapter'].consume(q['ctx'],request['binding'],access_token=request['access_token'],proof=request['proof'])
  assert gate.identity_counts(q)==(1,1)
  q['adapter'].verify_consumed(q['ctx'],request['binding'],access_token=request['access_token'],proof=request['proof'])
  assert gate.identity_counts(q)==(1,1)
 finally:q['repo'].close()


def test_a2_all_six_groups_use_actual_repository_and_no_auth_guard_patches():
 source=P.read_text(encoding='utf-8')
 assert all('def group'+str(i)+'(' in source for i in range(1,7))
 assert 'PostgresBudgetSourceRepository' in source and 'BudgetSourceService' in source
 assert 'WorkspaceModelDefaultsContext' in source and 'WHEN(false)' in source
 assert 'ThreadPoolExecutor' in source and 'FOR SHARE' in source
 assert 'patch(' not in source and 'monkeypatch' not in source
 assert 'A2_PROBE_NOT_IMPLEMENTED' not in source


@pytest.mark.parametrize('change,expected',[
 ({'idempotency_key':'a2-changed-local-key'},'STEP_UP_REUSED'),
 ({'body_digest':'b'*64},'STEP_UP_REUSED'),
])
def test_actual_consumed_proof_changed_binding_denial(tmp_path,change,expected):
 from datetime import datetime,timezone
 from daon_user_api.identity import IdentityError
 q=gate.create_budget_fixture(tmp_path,datetime.now(timezone.utc),'tenant-a2-proof','workspace-a2-proof','owner-a2-proof')
 try:
  r=gate.prepare_budget(q,key='a2-original-local-key')
  q['adapter'].consume(q['ctx'],r['binding'],access_token=r['access_token'],proof=r['proof'])
  arguments=dict(operation=r['binding'].operation,body_digest=r['binding'].body_digest,preconditions=r['binding'].preconditions,idempotency_key=r['binding'].idempotency_key)
  b=q['adapter'].binding(q['ctx'],**{**arguments,**change})
  with pytest.raises(IdentityError) as error:q['adapter'].consume(q['ctx'],b,access_token=r['access_token'],proof=r['proof'])
  assert (error.value.code,error.value.http_status)==(expected,403) and gate.identity_counts(q)==(1,1)
 finally:q['repo'].close()


def test_real_fixture_current_session_revocation_denial(tmp_path):
 from datetime import datetime,timezone
 from daon_user_api.identity import IdentityError
 q=gate.create_budget_fixture(tmp_path,datetime.now(timezone.utc),'tenant-a2-session','workspace-a2-session','owner-a2-session')
 try:
  with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='revoked'")
  with pytest.raises(IdentityError) as error:q['adapter'].current(q['ctx'],access_token=q['credentials'].access_token)
  assert (error.value.code,error.value.http_status)==('SESSION_REVOKED',401)
 finally:q['repo'].close()


@pytest.mark.parametrize('attribute,value',[
 ('table_name','operations_cost_receipts'),('constraint_name','routing_budget_allocation_period_fk')])
def test_safe_diag_table_constraint_only_identifiers(attribute,value):
 from types import SimpleNamespace
 error=ValueError('raw SQL and password MUST NOT EMIT');error.diag=SimpleNamespace(**{attribute:value})
 result=gate.safe_error_metadata(error)
 assert result['table' if attribute=='table_name' else 'constraint']==value
 setattr(error.diag,attribute,'unsafe raw value')
 assert all(v is None for v in gate.safe_error_metadata(error).values())


def test_unknown_exception_type_never_exposes_original_name_or_message(capsys):
 class SecretNamedException(Exception):pass
 assert gate.safe_exception_type(SecretNamedException('secret'))=='Exception'
 with pytest.raises(ValueError):gate.emit('failed',status='FAIL',safe_code='password=secret')
 assert capsys.readouterr().out==''


def test_a2_fixture_mirror_and_fault_only_transaction_boundary_are_explicit():
 source=P.read_text(encoding='utf-8')
 assert "'identity_org_tenant_memberships' if name=='tenant_memberships'" in source
 assert 'fail_before_commit' in source and 'raise CommitFault()' in source
 assert "with super()._transaction(context) as conn:" in source
 assert 'conn.transaction(force_rollback=True)' in source
 assert "('UPDATE routing_budget_periods" in source
 assert 'group6_same_key_race' in source and 'group6_replacement_anchor' in source
 assert 'WHERE tenant_id=%s FOR UPDATE NOWAIT' in source
 assert 'exec 2>/dev/null' in P.parent.parent.joinpath('run-routing-a2-postgres-gate.sh').read_text(encoding='utf-8')


def test_denial_helper_uses_actual_identity_http_status():
 from daon_user_api.identity import IdentityError
 def reject():raise IdentityError('STEP_UP_EXPIRED',403)
 gate.expect_denial(reject,IdentityError,'STEP_UP_EXPIRED',403)


@pytest.mark.parametrize('exception_type',["NotNullViolation","SyntaxError","InsufficientPrivilege"])
def test_sql_negative_cannot_hide_unexpected_sqlstate(exception_type,capsys):
 import psycopg
 def reject():raise getattr(psycopg.errors,exception_type)('sensitive raw error')
 with pytest.raises(ValueError,match='A2_UNEXPECTED_SQLSTATE'):gate.sql_denial(reject,{'23514'})
 assert capsys.readouterr().out==''


def test_only_expected_sqlstate_is_safe_metadata_not_original_error(capsys):
 import psycopg
 def reject():raise psycopg.errors.CheckViolation('sensitive raw error')
 assert gate.sql_denial(reject,{'23514'})=='23514'
 output=capsys.readouterr().out
 assert '23514' in output and 'sensitive' not in output


def test_exact_git_sql_fingerprints_and_frozen_prior_tools():
 import hashlib,subprocess
 root=P.parents[2]
 for name,sha in gate.HASHES.items():
  raw=subprocess.run(['git','show','70dfd1cbca7c2587c949596ff094b5e1752d2cce:services/api/migrations/versions/'+name],cwd=root,check=True,stdout=subprocess.PIPE).stdout
  assert hashlib.sha256(raw).hexdigest()==sha
 # 기존 B2/B3 도구 기준은 바꾸지 않는다.
 for task in ('b2','b3'):
  for name in ('scripts/run-routing-'+task+'-postgres-gate.sh','scripts/tests/routing-'+task+'-postgres-gate.py','scripts/tests/test_routing_'+task+'_gate_preflight.py'):
   raw=subprocess.run(['git','show','70dfd1cbca7c2587c949596ff094b5e1752d2cce:'+name],cwd=root,check=True,stdout=subprocess.PIPE).stdout
   current=(root/name).read_bytes()
   assert current.replace(b'\r\n',b'\n')==raw



def test_pool_dsn_pins_actual_libpq_statement_lock_and_connection_timeouts():
 import secrets
 from sqlalchemy.engine import make_url
 from psycopg.conninfo import conninfo_to_dict
 value=gate.pool_dsn(secrets.token_hex(32))
 url=make_url(value)
 assert (url.host,url.port,url.username,url.database)==('127.0.0.1',5432,gate.APP,gate.DB)
 expected={'connect_timeout':'5','options':'-c statement_timeout=15000 -c lock_timeout=8000'}
 assert dict(url.query)==expected
 parsed=conninfo_to_dict(value)
 assert all(parsed[k]==v for k,v in expected.items())
 source=P.read_text(encoding='utf-8')
 assert "dsn=pool_dsn(secrets['app_password'])" in source
 assert 'store=OwnedStore(dsn)' in source


@pytest.mark.parametrize('invalid',['',None,'postgresql://unapproved/db','a'*63,'g'*64])
def test_pool_dsn_rejects_noncredential_or_override_input(invalid,capsys):
 with pytest.raises(ValueError,match='A2_CREDENTIAL_SHAPE_INVALID'):gate.pool_dsn(invalid)
 assert capsys.readouterr().out==''



def test_actual_pool_timeout_show_and_each_schema_apply_stage_are_explicit():
 source=P.read_text(encoding='utf-8')
 assert "conn.execute('SHOW statement_timeout').fetchone()==('15s',)" in source
 assert "conn.execute('SHOW lock_timeout').fetchone()==('8s',)" in source
 assert "emit('apply_'+name[:4],status='RUNNING')" in source
 assert "emit('apply_'+name[:4],status='PASS')" in source


def test_other_organization_is_issued_while_actual_first_organization_lock_is_held():
 source=P.read_text(encoding='utf-8')
 assert "step('group6_other_org_while_locked')" in source
 assert "('routing-budget|'+q['ctx'].tenant_id,)" in source
 assert "pg_try_advisory_xact_lock" in source
 assert 'other_future.result(timeout=20)' in source
 assert "require(other_future.done(),'A2_OTHER_ORG_BLOCKED')" in source
