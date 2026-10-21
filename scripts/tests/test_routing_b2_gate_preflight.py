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
