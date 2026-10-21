"""Docker/DB를 실행하지 않는 가격 gate 사전검사 음성 시험."""
import importlib.util
from pathlib import Path
import pytest
P=Path(__file__).with_name('routing-price-postgres-gate.py')
spec=importlib.util.spec_from_file_location('price_gate',P);gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

@pytest.fixture(autouse=True)
def isolated_receipts():
 gate.reset_receipts()
 yield
 gate.reset_receipts()

@pytest.mark.parametrize('mutation',[
 {'database':'daon_user'}, {'host':'local-postgres'}, {'host':'172.27.0.1'},
 {'owner':'daon_app'}, {'app_role':'postgres'}, {'dsn':'postgresql://secret@live/db'},
 {'pg_id':'short'}, {'revision':'0075'}, {'source_root':'/tmp/other'},
])
def test_nonapproved_target_fails_before_any_io(mutation):
    config=dict(host='127.0.0.1',database='qa_routing_price_20261021',owner='postgres',
      app_role='qa_routing_price_app_20261021',pg_id='a'*64,revision='0072',source_root=gate.ROOT)
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
 with pytest.raises(ValueError,match='PRICE_SQL_HASH_MISMATCH'):gate.verify_sql(tmp_path)

def test_gate_import_has_no_database_or_docker_execution():
 source=P.read_text(encoding='utf-8')
 assert 'if __name__==' in source
 assert 'upgrade head' not in source and "migrate('0072')" in source and "migrate('0040',app=True)" in source
 assert 'PRICE_0073_FORBIDDEN' in source


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
 with pytest.raises(ValueError,match='PRICE_EXPECTED_DENIAL_MISSING'):gate.expect_denial(lambda:None,DomainDenial,'EXPECTED',403)


def test_restrictive_source_umask_does_not_prevent_rootless_image_code_read():
 shell=P.parent.parent.joinpath('run-routing-price-postgres-gate.sh').read_text(encoding='utf-8')
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
 shell=P.parent.parent.joinpath('run-routing-price-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=16e81ce4caf2fdaf5ba4f84090ca34e8b792e929' in shell
 assert 'git merge-base --is-ancestor "$PRODUCT_BASE" "$revision"' in shell
 assert 'git diff --name-only "$PRODUCT_BASE" "$revision"' in shell
 assert 'scripts/run-routing-price-postgres-gate.sh|scripts/tests/routing-price-postgres-gate.py|scripts/tests/test_routing_price_gate_preflight.py) ;; *) safe product_drift FAIL; exit 22' in shell
 assert '04f72f837a30d48830e36f38426c51b096027ee1' not in shell


def test_wrapper_root_is_single_static_configuration():
 wrapper=P.parent.parent/'run-routing-price-postgres-gate.sh'
 assert gate.read_approved_root(wrapper)==gate.ROOT
 source=wrapper.read_text(encoding='utf-8')
 assert 'scripts/run-routing-price-postgres-gate.sh | tar' in source
 assert 'COPY scripts/run-routing-price-postgres-gate.sh /qa/scripts/run-routing-price-postgres-gate.sh' in source

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
 with pytest.raises(ValueError,match='PRICE_WRAPPER_'):gate.read_approved_root(wrapper)

def test_wrapper_root_rejects_missing_and_oversize_files(tmp_path):
 wrapper=tmp_path/'gate.sh'
 with pytest.raises(ValueError,match='PRICE_WRAPPER_'):gate.read_approved_root(wrapper)
 wrapper.write_bytes(b'x'*65537)
 with pytest.raises(ValueError,match='PRICE_WRAPPER_'):gate.read_approved_root(wrapper)

def test_wrapper_root_rejects_symlink_before_read(tmp_path,monkeypatch):
 wrapper=tmp_path/'gate.sh';wrapper.write_text('readonly ROOT='+gate.ROOT,encoding='utf-8')
 original=Path.is_symlink
 monkeypatch.setattr(Path,'is_symlink',lambda self: self==wrapper or original(self))
 with pytest.raises(ValueError,match='PRICE_WRAPPER_'):gate.read_approved_root(wrapper)


def test_price_runner_uid_and_safe_output_are_closed(capsys):
 from copy import deepcopy
 value=snapshot();value['Name']='/'+gate.RUNNER;value['Image']='sha256:'+'c'*64
 value['Config']['User']='root';value['HostConfig'].update(Memory=536870912,NetworkMode='container:'+'a'*64,ReadonlyRootfs=True,Tmpfs={'/tmp':'rw,noexec,nosuid,size=134217728'})
 with pytest.raises(ValueError,match='PRICE_RUNNER_USER_INVALID'):gate.verify_container(value,kind='runner',revision='b'*40,pg_id='a'*64,qa_image=value['Image'])


def test_price_safe_output_rejects_secret_field_before_print(capsys):
 import secrets
 secret=secrets.token_urlsafe(32)
 with pytest.raises(ValueError,match='PRICE_OUTPUT_FIELDS_INVALID'):gate.emit('probe',status='PASS',password=secret)
 assert secret not in capsys.readouterr().out


def test_price_execution_gate_keeps_b2_pin_and_exact_new_three_file_boundary():
 shell=P.parent.parent.joinpath('run-routing-price-postgres-gate.sh').read_text(encoding='utf-8')
 b2=P.parent.parent.joinpath('run-routing-b2-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'readonly PRODUCT_BASE=16e81ce4caf2fdaf5ba4f84090ca34e8b792e929' in shell
 assert 'readonly PRODUCT_BASE=3bfb0f3d29d5f4bace7d26541a34500cbc128d10' in b2
 assert '--network none --memory 1g --cpus 1' in shell
 assert 'unix:///var/run/docker.sock' in shell
 assert 'scripts/run-routing-b2-postgres-gate.sh' not in shell
 assert 'scripts/tests/routing-b2-postgres-gate.py' not in shell
 assert 'm._current_access=' not in P.read_text(encoding='utf-8')


def test_price_image_and_sql_fingerprints_match_approved_exact_bytes():
 shell=P.parent.parent.joinpath('run-routing-price-postgres-gate.sh').read_text(encoding='utf-8')
 approved='python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae'
 assert gate.BASE==approved
 assert 'readonly BASE='+approved+'\n' in shell
 assert gate.PG_IMAGE=='sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e'
 assert gate.HASHES=={'0074_routing_policy.sql':'03568962142b86c611bf0a4811540b75b7b645373af0cafdbb7672f433a196fe','0075_routing_execution.sql':'5b8e8dc3e3426081c488177db5eaaa55d209a051eb0288ddecaa7c092d2c5e5d','0076_routing_budget_sources.sql':'8d2fc5efc942c3a012e4432135d359308ef6844b92f1b3b9467fd7a41796d732','0077_routing_price_sources.sql':'be1a76acf48f6773ab3bb7df01f16d4fe2530c5fa450148e4d9e9c533f556987'}


def test_price_image_installs_locked_api_dev_group_and_preflights_actual_import_closure():
 shell=P.parent.parent.joinpath('run-routing-price-postgres-gate.sh').read_text(encoding='utf-8')
 assert 'uv sync --frozen --package daon-user-api --group dev --no-install-project' in shell
 assert 'uv sync --frozen --package daon-user-api --no-dev' not in shell
 assert 'm.version("httpx")=="0.28.1"' in shell
 assert 'from httpx import ASGITransport' in shell
 assert 'from test_identity_support import create_service,FakeVerifiedOidcProvider,MutableClock' in shell
 assert 'from daon_user_api.runtime import create_app' in shell






def test_two_actual_local_identity_fixtures_have_unique_users_and_price_proofs(tmp_path):
 import sys,sqlite3
 from datetime import datetime,timezone
 root=P.parents[2]
 for path in (root/'services/api/src',root/'services/api/tests'):
  if str(path) not in sys.path:sys.path.insert(0,str(path))
 from daon_user_api.routing_pricing import PricePreconditions
 fixtures=[]
 try:
  for suffix in ('one','two'):
   directory=tmp_path/suffix;directory.mkdir()
   q=gate.create_price_fixture(directory,datetime.now(timezone.utc),'tenant-price-'+suffix,'workspace-price-'+suffix,'owner-price-'+suffix);fixtures.append(q)
   b=q['adapter'].binding(q['ctx'],operation='routing_price.register',body_digest='a'*64,target_digest='b'*64,preconditions=PricePreconditions(0,if_none_match='*'),idempotency_key='price-local-proof-'+suffix)
   proof=q['adapter'].issue(q['ctx'],b,access_token=q['credentials'].access_token,password=q['password'])
   q['adapter'].consume(q['ctx'],b,access_token=q['credentials'].access_token,proof=proof)
   assert gate.identity_counts(q)==(1,1)
  with sqlite3.connect(':memory:') as mirror:
   mirror.execute('CREATE TABLE users(user_id TEXT PRIMARY KEY,issuer TEXT,subject TEXT,login_id TEXT UNIQUE,email TEXT UNIQUE,UNIQUE(issuer,subject))')
   for q in fixtures:
    with sqlite3.connect(q['path']) as local:mirror.executemany('INSERT INTO users VALUES(?,?,?,?,?)',local.execute('SELECT user_id,issuer,subject,login_id,email FROM users'))
   assert mirror.execute('SELECT count(*) FROM users').fetchone()==(2,)
 finally:
  for q in fixtures:q['repo'].close()


def test_price_probe_six_groups_use_real_repository_and_no_permission_patch():
 source=P.read_text(encoding='utf8')
 assert all('def group'+str(i)+'(' in source for i in range(1,7))
 assert 'PriceSourceService' in source and 'PostgresPriceSourceRepository' in source
 assert 'WHEN(false)' in source and 'ThreadPoolExecutor' in source and "_authorize(conn,q['ctx'],now=q['clock']())" in source
 import inspect
 from daon_user_api.routing_sources_postgres import _authorize
 assert 'FOR SHARE' in inspect.getsource(_authorize)
 assert 'quote(' not in source and '.decrypt(' not in source and 'monkeypatch' not in source
 assert 'PRICE_PROBE_NOT_IMPLEMENTED' not in source



def test_pool_dsn_enforces_timeouts_without_libpq_plus_and_rejects_override():
 from urllib.parse import urlsplit,parse_qs
 value=gate.pool_dsn('a'*64);parsed=urlsplit(value)
 assert (parsed.hostname,parsed.port,parsed.path,parsed.username)==('127.0.0.1',5432,'/'+gate.DB,gate.APP)
 assert parse_qs(parsed.query)=={'connect_timeout':['5'],'options':['-c statement_timeout=15000 -c lock_timeout=8000']}
 assert '+' not in parsed.query
 with pytest.raises(ValueError):gate.pool_dsn('postgresql://other')
 source=P.read_text(encoding='utf8')
 assert "conn.execute('SHOW statement_timeout')" in source and "conn.execute('SHOW lock_timeout')" in source


def test_only_declared_credential_rotation_can_be_not_run(capsys):
 import json
 gate.emit('credential_rotation',status='NOT_RUN',safe_code='PRICE_CREDENTIAL_ROTATION_REQUIRES_EXCLUDED_SECRET')
 data=json.loads(capsys.readouterr().out)
 assert data['stage']=='credential_rotation' and data['status']=='NOT_RUN'
 for stage,status,code in [('group5','NOT_RUN','PRICE_CREDENTIAL_ROTATION_REQUIRES_EXCLUDED_SECRET'),('credential_rotation','NOT_RUN','PRICE_OTHER'),('credential_rotation','SKIP','PRICE_CREDENTIAL_ROTATION_REQUIRES_EXCLUDED_SECRET')]:
  with pytest.raises(ValueError):gate.emit(stage,status=status,safe_code=code)
 assert capsys.readouterr().out==''


def test_sql_denial_never_accepts_unrelated_database_failure(capsys):
 import psycopg
 def denied():raise psycopg.errors.CheckViolation('raw value must not be printed')
 assert gate.sql_denial(denied,{'23514'})=='23514'
 assert 'raw value' not in capsys.readouterr().out
 for error in (psycopg.errors.NotNullViolation('sensitive'),psycopg.errors.SyntaxError('sensitive'),RuntimeError('sensitive')):
  def fail():raise error
  with pytest.raises((ValueError,RuntimeError)):gate.sql_denial(fail,{'23514'})
 assert capsys.readouterr().out==''


@pytest.mark.parametrize('key,value',[('table','unsafe table'),('constraint','private.raw'),('exception_type','UnknownRawException'),('safe_code','password=hidden'),('test_name','raw value with spaces')])
def test_all_diagnostic_fields_are_closed_before_print(key,value,capsys):
 with pytest.raises(ValueError):gate.emit('failure',status='FAIL',**{key:value})
 assert capsys.readouterr().out==''


def test_provider_fixture_has_no_credentials_and_deleted_target_revoke_is_exercised():
 import inspect
 source=inspect.getsource(gate.seed_target)
 assert 'encrypted_credential' not in source and 'credential_nonce' not in source and "'public','none'" in source
 source=P.read_text(encoding='utf8')
 assert 'group5_deleted_target_revoke' in source
 assert 'group6_same_series_wait' in source and 'group6_other_org_while_locked' in source
 assert 'credential_rotation' in source and "sql_denial(rotate,{'23514'})" in source



def test_final_complete_carries_exact_not_run_without_hiding_limitation(capsys):
 import json
 gate.reset_receipts()
 for i in range(1,7):gate.emit('group'+str(i),status='PASS')
 capsys.readouterr()
 gate.emit('complete',status='PASS_WITH_NOT_RUN',groups=6,model_calls=0,not_run=['credential_rotation_positive'])
 value=json.loads(capsys.readouterr().out)
 assert value['status']=='PASS_WITH_NOT_RUN' and value['not_run']==['credential_rotation_positive']
 for stage,status,missing in [('complete','PASS',['credential_rotation_positive']),('complete','PASS_WITH_NOT_RUN',[]),('group5','PASS_WITH_NOT_RUN',['credential_rotation_positive']),('complete','PASS_WITH_NOT_RUN',['raw_secret'])]:
  with pytest.raises(ValueError):gate.emit(stage,status=status,not_run=missing)
 with pytest.raises(ValueError):gate.emit('complete',status='PASS_WITH_NOT_RUN',groups=6,model_calls=0)
 assert capsys.readouterr().out==''


def test_drift_replay_uses_captured_post_registration_consumption_baseline():
 source=P.read_text(encoding='utf8')
 section=source.split('    def group5():',1)[1].split('    def group6():',1)[0]
 assert 'drift_counts=identity_counts(q)' in section
 assert 'identity_counts(q)==drift_counts' in section
 assert "identity_counts(q)==(1,1)" not in section
 assert 'business(q)==drift_business' in section



def test_actual_cipher_envelopes_are_memory_only_and_new_nonce_per_version():
 first,second=gate.synthetic_envelopes('price-credential-fixture')
 assert (first.credential_version,second.credential_version)==(1,2)
 assert first.schema_version==second.schema_version==1 and first.encryption_key_version==second.encryption_key_version==1
 valid_nonce=len(first.nonce)==len(second.nonce)==12 and first.nonce!=second.nonce
 gate.require(valid_nonce,'PRICE_QA_NONCE_INVALID')
 valid_cipher=len(first.ciphertext)>=48 and len(second.ciphertext)>=48 and first.ciphertext!=second.ciphertext
 gate.require(valid_cipher,'PRICE_QA_CIPHERTEXT_INVALID')
 assert 'ciphertext=' not in repr(first) and 'nonce=' not in repr(first)


def test_existing_cipher_roundtrip_and_aad_mismatch_are_real_and_secret_safe():
 import secrets
 from daon_user_api.provider_credentials import ProviderCredentialCipher,ProviderCredentialError
 master=secrets.token_bytes(32);plain=secrets.token_bytes(32)
 cipher=ProviderCredentialCipher(master,encryption_key_version=1)
 sealed=cipher.encrypt('price-credential-fixture','CUSTOM',1,plain)
 matches=cipher.decrypt('price-credential-fixture','CUSTOM',1,sealed)==plain
 gate.require(matches,'PRICE_QA_ROUNDTRIP_INVALID')
 for connection,provider,version in [('other-connection','CUSTOM',1),('price-credential-fixture','OPENAI',1),('price-credential-fixture','CUSTOM',2)]:
  with pytest.raises(ProviderCredentialError) as error:cipher.decrypt(connection,provider,version,sealed)
  assert str(error.value)=='CREDENTIAL_DECRYPTION_FAILED'


def test_final_pass_requires_all_actual_group_receipts_and_rotation_receipt(capsys):
 import json
 gate.reset_receipts()
 with pytest.raises(ValueError):gate.emit('complete',status='PASS',groups=6,model_calls=0,not_run=[])
 for i in range(1,7):gate.emit('group'+str(i),status='PASS')
 with pytest.raises(ValueError):gate.emit('complete',status='PASS',groups=6,model_calls=0,not_run=[])
 gate.emit('complete',status='PASS_WITH_NOT_RUN',groups=6,model_calls=0,not_run=['credential_rotation_positive'])
 with pytest.raises(ValueError):gate.emit('credential_rotation',status='PASS',safe_code='PRICE_OTHER')
 gate.emit('credential_rotation',status='PASS',safe_code='PRICE_SYNTHETIC_CREDENTIAL_ROTATION_VERIFIED')
 gate.emit('complete',status='PASS',groups=6,model_calls=0,not_run=[])
 rows=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
 assert rows[-1]['status']=='PASS' and rows[-1]['not_run']==[]
 assert rows[-2]['stage']=='credential_rotation' and rows[-2]['status']=='PASS'
 gate.reset_receipts()


def test_credential_fixture_has_stale_proof_and_actual_snapshot_assertions():
 import inspect
 code=inspect.getsource(gate.run_probe)
 assert "fixture('credential')" in code and 'group5_credential_stale_proof' in code
 assert "'STEP_UP_BINDING_DENIED'" in code
 assert 'group5_credential_new_snapshot' in code and 'old_snapshot==new_old_snapshot' in code
 assert 'credential_version' in code and 'PRICE_SYNTHETIC_CREDENTIAL_ROTATION_VERIFIED' in code
 assert 'replace_credential(' not in code and '.decrypt(' not in code



@pytest.mark.parametrize('stage',['group3','credential_rotation'])
def test_failure_after_receipt_cannot_be_hidden_by_final_pass(stage,capsys):
 for i in range(1,7):gate.emit('group'+str(i),status='PASS')
 gate.emit('credential_rotation',status='PASS',safe_code='PRICE_SYNTHETIC_CREDENTIAL_ROTATION_VERIFIED')
 gate.emit(stage,status='FAIL')
 with pytest.raises(ValueError):gate.emit('complete',status='PASS',groups=6,model_calls=0,not_run=[])
 capsys.readouterr()


def test_fixture_crypto_helper_never_reads_or_writes_external_secret_sources():
 import inspect,ast
 code=inspect.getsource(gate.synthetic_envelopes)
 calls=[n for n in ast.walk(ast.parse(code)) if isinstance(n,ast.Call)]
 names={n.func.id for n in calls if isinstance(n.func,ast.Name)}
 assert not (names&{'open','print','input','getenv','eval','exec'})
 assert 'environ' not in code and 'write_' not in code and '.decrypt(' not in code
 code=inspect.getsource(gate.install_synthetic_envelope)
 assert 'credential_version=%s AND version=1' in code
 assert 'version=version+1' not in code and 'catalog_version=' not in code and 'base_url=' not in code
