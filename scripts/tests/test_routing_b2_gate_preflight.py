"""Docker/DB를 실행하지 않는 B2 gate 사전검사 음성 시험."""
import importlib.util
from pathlib import Path
import pytest
P=Path(__file__).with_name('routing-b2-postgres-gate.py')
spec=importlib.util.spec_from_file_location('b2_gate',P);gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
@pytest.mark.parametrize('mutation',[
 {'database':'daon_user'}, {'host':'local-postgres'}, {'host':'172.27.0.1'},
 {'owner':'daon_app'}, {'app_role':'postgres'}, {'dsn':'postgresql://secret@live/db'},
 {'pg_id':'short'}, {'revision':'0075'}, {'source_root':'/tmp/other'},
])
def test_nonapproved_target_fails_before_any_io(mutation):
    config=dict(host='127.0.0.1',database='qa_routing_b2_20261021',owner='postgres',
      app_role='qa_routing_b2_app_20261021',pg_id='a'*64,revision='0072',source_root='/home/daon/deploy/daon-user-routing-b2-qa-20261021')
    with pytest.raises(ValueError):gate.validate_target({**config,**mutation})


def snapshot():
 return dict(Id='a'*64,Name='/'+gate.PG,Image=gate.PG_IMAGE,Config={'Labels':{'io.daon.qa.issue':gate.ISSUE,'io.daon.qa.revision':'b'*40}},
 HostConfig=dict(NetworkMode='none',PortBindings={},Binds=None,Privileged=False,Memory=536870912,NanoCpus=1000000000,Devices=[],VolumesFrom=None,Tmpfs={'/var/lib/postgresql/data':'rw,noexec,nosuid,size=536870912'}),Mounts=[])

@pytest.mark.parametrize('mutate',[
 lambda v:v.update(Name='/local-postgres'),lambda v:v.update(Image='sha256:'+'f'*64),
 lambda v:v['Config']['Labels'].update({'io.daon.qa.issue':'other'}),
 lambda v:v['HostConfig'].update(NetworkMode='bridge'),lambda v:v['HostConfig'].update(PortBindings={'5432/tcp':[{'HostPort':'5432'}]}),
 lambda v:v['HostConfig'].update(Binds=['/data:/data']),lambda v:v['HostConfig'].update(Privileged=True),
 lambda v:v['HostConfig'].update(Memory=1073741824),lambda v:v['HostConfig'].update(NanoCpus=2000000000),
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
 with pytest.raises(ValueError,match='B2_SQL_HASH_MISMATCH'):gate.verify_sql(tmp_path)

def test_gate_import_has_no_database_or_docker_execution():
 source=P.read_text(encoding='utf-8')
 assert 'if __name__==' in source
 assert 'upgrade head' not in source and "migrate('0072')" in source and "migrate('0040',app=True)" in source
 assert 'B2_0073_FORBIDDEN' in source


def test_runner_must_share_only_exact_pg_network_namespace_and_have_read_only_root():
 v=snapshot();v['Name']='/'+gate.RUNNER;v['Image']='sha256:'+'c'*64
 v['HostConfig'].update(NetworkMode='container:'+'a'*64,ReadonlyRootfs=True,Tmpfs={'/tmp':'rw,noexec,nosuid,size=134217728'})
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
 with pytest.raises(ValueError,match='B2_EXPECTED_DENIAL_MISSING'):gate.expect_denial(lambda:None,DomainDenial,'EXPECTED',403)


def test_restrictive_source_umask_does_not_prevent_rootless_image_code_read():
 shell=P.parent.parent.joinpath('run-routing-b2-postgres-gate.sh').read_text(encoding='utf-8')
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
 assert gate.safe_error_metadata(outer)==dict(sqlstate='42501',column=None,permission_type=kind,permission_name='routing_authorization_plans')

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
 assert gate.safe_error_metadata(error)==dict(sqlstate='23502',column='created_at',permission_type=None,permission_name=None)
 error.__context__=error
 assert gate.safe_error_metadata(error)['column']=='created_at'
 error.sqlstate='unsafe state';error.diag.sqlstate=None;error.diag.column_name='column with raw text'
 assert gate.safe_error_metadata(error)==dict(sqlstate=None,column=None,permission_type=None,permission_name=None)


def test_execution_gate_pins_reviewed_product_commit_and_denies_other_product_drift():
 shell=P.parent.parent.joinpath('run-routing-b2-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=3bfb0f3d29d5f4bace7d26541a34500cbc128d10' in shell
 assert 'git merge-base --is-ancestor "$PRODUCT_BASE" "$revision"' in shell
 assert 'git diff --name-only "$PRODUCT_BASE" "$revision"' in shell
 assert 'scripts/run-routing-b2-postgres-gate.sh|scripts/tests/routing-b2-postgres-gate.py|scripts/tests/test_routing_b2_gate_preflight.py) ;; *) safe product_drift FAIL; exit 22' in shell
 assert '04f72f837a30d48830e36f38426c51b096027ee1' not in shell
