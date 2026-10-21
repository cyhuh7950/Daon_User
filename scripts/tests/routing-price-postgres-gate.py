"""가격 전용 격리 PostgreSQL gate. 일반 DB/DSN 입력을 받지 않는다.
실행 책임은 main이며 사전검사 단위시험은 Docker/SQL을 실행하지 않는다.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path

ISSUE='R3-PRICE-PG-20261021'
DB='qa_routing_price_20261021'
APP='qa_routing_price_app_20261021'
PG='daon-user-routing-price-pg-20261021'
RUNNER='daon-user-routing-price-runner-20261021'
TAG='daon-user-routing-price-qa:20261021'
PG_IMAGE='sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e'
BASE='python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae'
HASHES={'0074_routing_policy.sql':'03568962142b86c611bf0a4811540b75b7b645373af0cafdbb7672f433a196fe',
 '0075_routing_execution.sql':'5b8e8dc3e3426081c488177db5eaaa55d209a051eb0288ddecaa7c092d2c5e5d',
 '0076_routing_budget_sources.sql':'8d2fc5efc942c3a012e4432135d359308ef6844b92f1b3b9467fd7a41796d732',
 '0077_routing_price_sources.sql':'be1a76acf48f6773ab3bb7df01f16d4fe2530c5fa450148e4d9e9c533f556987'}

def require(ok,code):
    if not ok:raise ValueError(code)

def read_approved_root(wrapper):
    """운영 wrapper의 단일 literal 정본만 읽는다. shell/env/CLI 평가는 없다."""
    wrapper=Path(wrapper)
    try:
        for entry in (wrapper,*wrapper.parents):
            require(not entry.is_symlink() and not entry.is_junction(),'PRICE_WRAPPER_LINK_FORBIDDEN')
        before=wrapper.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size<=65536,'PRICE_WRAPPER_FILE_INVALID')
        with wrapper.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            require((before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino),'PRICE_WRAPPER_FILE_CHANGED')
            raw=stream.read(65537)
            after=os.fstat(stream.fileno())
        require(len(raw)<=65536 and (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'PRICE_WRAPPER_FILE_CHANGED')
        text=raw.decode('utf-8')
    except (OSError,UnicodeError) as error:
        raise ValueError('PRICE_WRAPPER_READ_FAILED') from None
    assignments=[line for line in text.splitlines() if not line.lstrip().startswith('#') and re.search(r'\bROOT\s*=',line)]
    require(len(assignments)==1,'PRICE_WRAPPER_ROOT_AMBIGUOUS')
    match=re.fullmatch(r'readonly ROOT=(/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)',assignments[0])
    require(match is not None,'PRICE_WRAPPER_ROOT_NOT_LITERAL')
    root=match.group(1)
    require(root.rsplit('/',1)[-1]=='daon-user-routing-price-qa-'+ISSUE.rsplit('-',1)[-1],'PRICE_WRAPPER_NAMESPACE_INVALID')
    return root

ROOT=read_approved_root(Path(__file__).absolute().parent.parent/'run-routing-price-postgres-gate.sh')

def validate_target(config):
    expected=dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT)
    require(set(config)==set(expected)|{'pg_id'},'PRICE_TARGET_FIELDS_INVALID')
    require(all(config[k]==v for k,v in expected.items()),'PRICE_TARGET_NOT_APPROVED')
    require(type(config['pg_id']) is str and re.fullmatch('[0-9a-f]{64}',config['pg_id']),'PRICE_CONTAINER_ID_INVALID')
    return True

def verify_sql(root):
    root=Path(root)
    for name,sha in HASHES.items():
        require(hashlib.sha256((root/'services/api/migrations/versions'/name).read_bytes()).hexdigest()==sha,'PRICE_SQL_HASH_MISMATCH')

def verify_container(value,*,kind,revision,pg_id=None,qa_image=None):
    require(kind in {'pg','runner'},'PRICE_CONTAINER_KIND_INVALID')
    require(type(value.get('Id')) is str and re.fullmatch('[0-9a-f]{64}',value['Id']),'PRICE_CONTAINER_ID_INVALID')
    if kind=='pg':validate_target(dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT,pg_id=value['Id']))
    config=value['Config'];host=value['HostConfig'];labels=config.get('Labels') or {}
    require(value['Name']=='/'+(PG if kind=='pg' else RUNNER),'PRICE_CONTAINER_NAME_INVALID')
    require(labels.get('io.daon.qa.issue')==ISSUE and labels.get('io.daon.qa.revision')==revision,'PRICE_RESOURCE_NOT_OWNED')
    require(host.get('NetworkMode')==('none' if kind=='pg' else 'container:'+pg_id),'PRICE_NETWORK_NOT_ISOLATED')
    require(not host.get('PortBindings') and not host.get('Binds') and not host.get('Privileged'),'PRICE_HOST_ACCESS_FORBIDDEN')
    require(host.get('Memory')==(1073741824 if kind=='pg' else 536870912) and host.get('NanoCpus')==1000000000,'PRICE_RESOURCE_LIMIT_INVALID')
    require(not host.get('Devices') and not host.get('VolumesFrom') and host.get('PidMode','') in {'','private'} and host.get('IpcMode','private') in {'','private'},'PRICE_HOST_ACCESS_FORBIDDEN')
    require(value['Image']==(PG_IMAGE if kind=='pg' else qa_image),'PRICE_IMAGE_MISMATCH')
    tmpfs=host.get('Tmpfs') or {};expected='/var/lib/postgresql/data' if kind=='pg' else '/tmp'
    require(tmpfs=={expected:'rw,noexec,nosuid,size='+('536870912' if kind=='pg' else '134217728')},'PRICE_TMPFS_INVALID')
    require(all(m.get('Type')=='tmpfs' and m.get('Destination')==expected for m in value.get('Mounts',[])),'PRICE_MOUNT_FORBIDDEN')
    if kind=='runner':
        require(host.get('ReadonlyRootfs') is True,'PRICE_RUNNER_WRITABLE_ROOT')
        require(config.get('User')=='65534:65534','PRICE_RUNNER_USER_INVALID')
    return value['Id']

CURRENT_TEST='preflight'
EXCEPTION_TYPES=frozenset({'ValueError','TypeError','IdentityError','AuthorizationError','RoutingExecutionError','CloudDatabaseError','ImportError','ModuleNotFoundError','ProgrammingError','IntegrityError','OperationalError','InsufficientPrivilege','ForeignKeyViolation','NotNullViolation','CheckViolation','UniqueViolation','AssertionError','Exception','RoutingSourceError','OrganizationWorkflowError','RoutingPriceStorageError','RoutingPricingError'})

def safe_exception_type(error):
    name=type(error).__name__
    return name if name in EXCEPTION_TYPES else 'Exception'

def expect_denial(call,expected_type,code,status):
    try:call()
    except expected_type as error:
        require(getattr(error,'code',None)==code and getattr(error,'status',getattr(error,'http_status',None))==status,'PRICE_UNEXPECTED_DENIAL')
        return
    raise ValueError('PRICE_EXPECTED_DENIAL_MISSING')

def emit(stage,**values):
    global CURRENT_TEST
    allowed={'status','schema','sql_hashes','test_name','safe_code','sqlstate','column','permission_type','permission_name','groups','model_calls','identity_fixture','exception_type','constraint','table','not_run'}
    require(set(values)<=allowed,'PRICE_OUTPUT_FIELDS_INVALID')
    require(type(stage) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',stage),'PRICE_OUTPUT_FIELDS_INVALID')
    if stage=='complete':
        require(values.get('status')=='PASS_WITH_NOT_RUN' and values.get('groups')==6 and values.get('model_calls')==0 and values.get('not_run')==['credential_rotation_positive'],'PRICE_OUTPUT_FIELDS_INVALID')
    else:
        require('not_run' not in values,'PRICE_OUTPUT_FIELDS_INVALID')
        require(values.get('status') in {'RUNNING','PASS','FAIL'} or stage=='credential_rotation' and values.get('status')=='NOT_RUN' and values.get('safe_code')=='PRICE_CREDENTIAL_ROTATION_REQUIRES_EXCLUDED_SECRET','PRICE_OUTPUT_FIELDS_INVALID')
    for key,value in values.items():
        if key=='status':continue
        if key=='not_run':require(stage=='complete' and type(value) is list and value==['credential_rotation_positive'],'PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='sql_hashes':require(value==HASHES,'PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='schema':require(value=='baseline0072+exactSQL0074/0075/0076/0077','PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='groups':require(type(value) is int and 0<=value<=6,'PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='model_calls':require(type(value) is int and value==0,'PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='identity_fixture':require(value=='isolated-synthetic-sqlite-mirrored-whitelist','PRICE_OUTPUT_FIELDS_INVALID')
        elif key in {'constraint','table'}:require(value is None or type(value) is str and re.fullmatch('[a-z_][a-z0-9_]{0,62}',value),'PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='exception_type':require(type(value) is str and value in EXCEPTION_TYPES,'PRICE_OUTPUT_FIELDS_INVALID')
        elif key=='safe_code':require(type(value) is str and re.fullmatch('PRICE_[A-Z0-9_]+',value),'PRICE_OUTPUT_FIELDS_INVALID')
        elif value is not None:require(type(value) is str and re.fullmatch('[A-Za-z0-9_]{1,128}',value),'PRICE_OUTPUT_FIELDS_INVALID')
    CURRENT_TEST=stage
    print(json.dumps(dict(issue_id=ISSUE,stage=stage,**values),sort_keys=True),flush=True)


def credentials():
    value=json.load(sys.stdin)
    require(set(value)=={'pg_password','app_password'},'PRICE_CREDENTIAL_FIELDS_INVALID')
    for secret in value.values():require(type(secret) is str and re.fullmatch('[0-9a-f]{64}',secret),'PRICE_CREDENTIAL_SHAPE_INVALID')
    return value

def connect(secret,*,app=False,database=DB):
    import psycopg
    return psycopg.connect(host='127.0.0.1',port=5432,dbname=database,user=APP if app else 'postgres',
        password=secret,connect_timeout=5,options='-c statement_timeout=15000 -c lock_timeout=8000')

def pool_dsn(secret):
    """실제 pool의 모든 연결에도 제한을 전달한다. 임의 DSN/옵션 입력은 없다."""
    from sqlalchemy.engine import URL
    require(type(secret) is str and re.fullmatch('[0-9a-f]{64}',secret),'PRICE_CREDENTIAL_SHAPE_INVALID')
    from urllib.parse import urlencode,quote
    base=URL.create('postgresql',username=APP,password=secret,host='127.0.0.1',port=5432,database=DB).render_as_string(hide_password=False)
    # libpq URI는 '+'를 공백으로 바꾸지 않는다. options의 공백은 정확 %20이다.
    return base+'?'+urlencode({'connect_timeout':'5','options':'-c statement_timeout=15000 -c lock_timeout=8000'},quote_via=quote)


def bootstrap(secrets):
    from psycopg import sql
    root=Path('/qa');verify_sql(root)
    require(sys.version_info[:3]==(3,14,3),'PRICE_PYTHON_VERSION_INVALID')
    with connect(secrets['pg_password'],database='postgres') as conn:
        require(conn.execute('SELECT current_user').fetchone()==('postgres',),'PRICE_ADMIN_ROLE_INVALID')
        conn.execute('CREATE ROLE daon_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS')
        conn.commit();conn.autocommit=True
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DB)))
    with connect(secrets['pg_password']) as conn:
        conn.execute('CREATE EXTENSION vector')
        conn.execute('GRANT USAGE,CREATE ON SCHEMA public TO daon_app')
    def migrate(target,app=False):
        # SQLAlchemy URL is constructed by its URL API, never printed.
        from sqlalchemy.engine import URL
        env=dict(os.environ,DAON_DB_MIGRATION_DSN=URL.create('postgresql+psycopg',username='postgres',
            password=secrets['pg_password'],host='127.0.0.1',port=5432,database=DB).render_as_string(hide_password=False))
        env.pop('PGOPTIONS',None)
        if app:env['PGOPTIONS']='-c role=daon_app'
        result=subprocess.run([sys.executable,'-m','alembic','upgrade',target],cwd='/qa/services/api',env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240)
        require(result.returncode==0,'PRICE_BASELINE_PHASE_'+target+'_FAILED')
    with connect(secrets['pg_password']) as conn:
        conn.execute('SET LOCAL ROLE daon_app')
        require(conn.execute("SELECT current_user,has_schema_privilege(current_user,'public','CREATE')").fetchone()==('daon_app',True),'PRICE_APP_SCHEMA_PRECONDITION')
    emit('baseline_app_0040',status='RUNNING');migrate('0040',app=True);emit('baseline_app_0040',status='PASS')
    with connect(secrets['pg_password']) as conn:
        row=conn.execute("SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='organization_license_versions'::regclass").fetchone()
        require(row==('daon_app',True,True),'PRICE_LICENSE_OWNER_PRECONDITION')
        for signature in ('transition_canon_state(text,text,integer,text,text,text,text,text)','claim_document_processing_job(text,integer)'):
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==('daon_app',),'PRICE_FUNCTION_OWNER_PRECONDITION')
    emit('baseline_admin_0072',status='RUNNING');migrate('0072');emit('baseline_admin_0072',status='PASS')
    with connect(secrets['pg_password']) as conn:
        require(conn.execute('SELECT version_num FROM alembic_version').fetchall()==[('0072',)],'PRICE_REVISION_INVALID')
        for signature,owner in [('transition_canon_state(text,text,integer,text,text,text,text,text)','daon_notebook_delete'),('claim_document_processing_job(text,integer)','postgres')]:
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==(owner,),'PRICE_FUNCTION_OWNER_POSTCONDITION')
        require(conn.execute("SELECT to_regclass('operations_run_cost_events'),to_regclass('operations_host_capacity_samples')").fetchone()==(None,None),'PRICE_0073_FORBIDDEN')
        for name in HASHES:
            emit('apply_'+name[:4],status='RUNNING')
            conn.execute((root/'services/api/migrations/versions'/name).read_text())
            emit('apply_'+name[:4],status='PASS')
        conn.execute('REVOKE CREATE ON SCHEMA public FROM daon_app')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {} IN ROLE daon_app').format(sql.Identifier(APP),sql.Literal(secrets['app_password'])))
    with connect(secrets['app_password'],app=True) as conn:
        row=conn.execute("SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,has_schema_privilege(current_user,'public','CREATE'),pg_has_role(current_user,'daon_notebook_delete','MEMBER') FROM pg_roles WHERE rolname=current_user").fetchone()
        require(row==(APP,APP,False,False,False,False,False,False,False),'PRICE_RESTRICTED_ROLE_INVALID')
    emit('bootstrap',status='PASS',schema='baseline0072+exactSQL0074/0075/0076/0077',sql_hashes=HASHES)

def create_price_fixture(path,now,tenant,workspace,actor):
    """무작위 로컬 password와 실제 Identity/Authorization만 사용하는 합성 fixture."""
    import secrets
    from daon_user_api.identity import DevicePlatform,PASSWORD_HASHER
    from daon_user_api.authorization import AuthorizationService,SqliteAuthorizationRepository,Role
    from daon_user_api.routing_price_reauthentication import PriceReauthenticationAdapter
    from daon_user_api.routing_sources import BudgetContext
    from test_identity_support import create_service,MutableClock
    path=Path(path)/'identity.sqlite3'
    clock=MutableClock(now);identity,repository,audit,_=create_service(path,clock=clock)
    password=secrets.token_urlsafe(28);login='qa-'+secrets.token_hex(12)
    email='qa-'+hashlib.sha256(json.dumps([tenant,actor],separators=(',',':')).encode('utf-8')).hexdigest()[:40]+'@example.com'
    with repository.transaction() as conn:
        conn.execute('INSERT INTO tenants(tenant_id) VALUES (?)',(tenant,))
        conn.execute("INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,state) VALUES (?,?,?,?,?,?,?,'active')",(actor,'local',login,login,email,PASSWORD_HASHER.hash(password),clock().isoformat()))
        conn.execute("INSERT INTO memberships VALUES (?,?,'member')",(tenant,actor))
        conn.execute('CREATE TABLE tenant_memberships(tenant_id TEXT,user_id TEXT,role TEXT,state TEXT,version INTEGER,updated_at TEXT,join_workspace_id TEXT)')
        conn.execute("INSERT INTO tenant_memberships VALUES (?,?,'organization_admin','active',1,?,NULL)",(tenant,actor,clock().isoformat()))
    credentials=identity.local_login(login_id=login,password=password,platform=DevicePlatform.WEB,trace_id='trace-price-qa',policy_version='policy-price-qa')
    auth_repo=SqliteAuthorizationRepository(path)
    auth_repo.bootstrap_workspace(tenant_id=tenant,workspace_id=workspace,owner_user_id=actor,owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',data_area='cloud_sync',cost_limit_cents=0,now=now)
    authorization=AuthorizationService(repository=auth_repo,audit_store=audit,clock=clock,identity_service=identity)
    adapter=PriceReauthenticationAdapter(identity,authorization)
    principal=identity.validate_access(credentials.access_token,trace_id='trace-price-qa',policy_version='policy-price-qa')
    context=BudgetContext.from_principal(principal,workspace_id=workspace,trace_id='trace-price-qa',policy_version='policy-price-qa')
    return dict(identity=identity,repo=repository,auth_repo=auth_repo,auth=authorization,adapter=adapter,ctx=context,credentials=credentials,password=password,clock=clock,path=path,login=login,audit=audit)


def identity_counts(q):
    with q['repo'].transaction() as conn:
        return tuple(conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('step_up_authorizations','step_up_consumptions'))


def mirror_fixture(q,secrets):
    import sqlite3
    from psycopg import sql
    ctx=q['ctx']
    # 실제 합성 SQLite 생성행 중 현재 인증/인가에 필요한 표만 이식한다.
    tables=('tenants','users','memberships','devices','sessions','auth_workspaces','auth_tenant_roles','auth_memberships','auth_tenant_policies','auth_tenant_permission_rules','auth_workspace_policies','auth_workspace_permission_rules','auth_source_access','tenant_memberships')
    with connect(secrets['pg_password']) as conn,sqlite3.connect(q['path']) as local:
        conn.execute('INSERT INTO tenants(tenant_id,display_name) VALUES(%s,%s)',(ctx.tenant_id,'Synthetic Price QA'))
        conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,'Synthetic Price QA'))
        conn.execute('INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)',(ctx.tenant_id,ctx.actor_id))
        for name in tables:
            target='identity_org_tenant_memberships' if name=='tenant_memberships' else 'identity_'+name
            columns=conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(target,)).fetchall()
            require(bool(columns),'PRICE_IDENTITY_TABLE_MISSING')
            available={r[1] for r in local.execute('PRAGMA table_info('+name+')')}
            names=[key for key,_ in columns if key in available]
            for row in local.execute('SELECT '+','.join(names)+' FROM '+name):
                values=[bool(v) if dict(columns)[key]=='boolean' and v is not None else v for key,v in zip(names,row,strict=True)]
                conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(target),sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),values)


def sql_denial(call,states):
    """원문 예외를 출력하지 않으며 예상 SQLSTATE만 음성 성공으로 인정한다."""
    import psycopg
    try:call()
    except psycopg.Error as error:
        require(error.sqlstate in states,'PRICE_UNEXPECTED_SQLSTATE')
        emit(CURRENT_TEST,status='PASS',**safe_error_metadata(error))
        return error.sqlstate
    raise ValueError('PRICE_EXPECTED_SQL_DENIAL_MISSING')



def price_body(q,expected=0):
    from datetime import timedelta
    now=q['clock']()
    return dict(schema_version=1,expected_version=expected,workspace_id=q['ctx'].workspace_id,connection_id=q['connection'],model_id='qa/model-v1',connection_version=1,catalog_version=1,endpoint_version=1,credential_source='workspace',currency='USD',valid_from=now.isoformat(),valid_until=(now+timedelta(hours=24)).isoformat(),billing_basis='text_tokens_v1',unit_prices=dict(maximum_input_per_million='1.000001',maximum_output_per_million='2',mandatory_fee_per_request='0'),evidence=dict(source_kind='official_price_document',reference='https://prices.example.com/qa',document_version='synthetic-v1',document_digest='a'*64,reviewed_at=now.isoformat()))


def seed_target(q,secrets,index):
    # 합성 공개 무자격 metadata. 실제 Provider 검증/요청/암호문 생성이 아니다.
    q['connection']='price-connection-'+str(index)
    short=chr(65+index//26)+chr(65+index%26)
    with connect(secrets['pg_password']) as conn:
        conn.execute("INSERT INTO system_provider_connections(connection_id,provider_code,display_name,base_url,enabled,verification_status,version,updated_by,trace_id,policy_version,access_mode,credential_requirement,short_code,adapter_type,provider_name) VALUES(%s,'CUSTOM','Synthetic Price QA','https://provider.example.invalid',true,'verified',1,%s,%s,%s,'public','none',%s,'openai_compatible','Synthetic Price QA')",(q['connection'],q['ctx'].actor_id,q['ctx'].trace_id,q['ctx'].policy_version,short))
        conn.execute("INSERT INTO system_provider_models(connection_id,model_id,reported_capabilities,effective_capabilities,catalog_status,catalog_version,updated_by) VALUES(%s,'qa/model-v1',ARRAY['text_generation'],ARRAY['text_generation'],'ready',1,%s)",(q['connection'],q['ctx'].actor_id))
        conn.execute("INSERT INTO system_provider_allowed_models(connection_id,model_id,updated_by) VALUES(%s,'qa/model-v1',%s)",(q['connection'],q['ctx'].actor_id))


def run_probe(secrets):
    emit('fixture_imports',status='RUNNING')
    import tempfile
    from contextlib import contextmanager
    from datetime import datetime,timedelta,timezone
    from dataclasses import replace
    from copy import deepcopy
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event,Barrier
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from daon_user_api.cloud_storage import PostgresCloudStore
    from daon_user_api.routing_sources import CurrencyRegistry,RoutingSourceError
    from daon_user_api.routing_pricing_postgres import _authorize,_cloud
    from daon_user_api.routing_pricing import PriceSourceService,PricePreconditions,RoutingPriceStorageError
    from daon_user_api.routing_pricing_postgres import PostgresPriceSourceRepository
    from daon_user_api.routing_price_reauthentication import canonical,digest
    from daon_user_api.identity import IdentityError,DevicePlatform
    from daon_user_api.authorization import AuthorizationError
    from daon_user_api.organization_membership import OrganizationWorkflowError
    sys.path.insert(0,'/qa/services/api/tests')
    now=datetime.now(timezone.utc);temp=tempfile.TemporaryDirectory(prefix='price-identity-',dir='/tmp');fixtures=[];stores=[]
    registry=CurrencyRegistry((('USD',2),('JPY',0)))
    dsn=pool_dsn(secrets['app_password'])
    class CommitFault(Exception):pass
    class OwnedStore(PostgresCloudStore):
        # 실제 INSERT/audit 뒤 commit 직전만 실패. SQL/인가/제약은 수정하지 않는다.
        fail_before_commit=False
        observe=False
        statements=None
        @contextmanager
        def _transaction(self,context):
            with super()._transaction(context) as conn:
                if self.observe:
                    outer=self
                    class ReadTrace:
                        def execute(self,statement,params=()):
                            text=statement if type(statement) is str else statement.as_string(conn)
                            outer.statements.append(text.lstrip().split(None,1)[0].upper())
                            return conn.execute(statement,params)
                        def __getattr__(self,name):return getattr(conn,name)
                    yield ReadTrace()
                else:yield conn
                if self.fail_before_commit:
                    self.fail_before_commit=False
                    raise CommitFault()
    def fixture(label):
        emit('fixture_identity_'+label,status='RUNNING');path=Path(temp.name)/label;path.mkdir()
        q=create_price_fixture(path,now,'tenant-price-'+label,'workspace-price-'+label,'owner-price-'+label);fixtures.append(q)
        emit('fixture_mirror_'+label,status='RUNNING');mirror_fixture(q,secrets);seed_target(q,secrets,len(fixtures))
        store=OwnedStore(dsn);stores.append(store)
        repo=PostgresPriceSourceRepository(store,registry=registry,clock=q['clock'])
        q.update(store=store,repository=repo,service=PriceSourceService(repo,q['adapter'],registry=registry,clock=q['clock']))
        return q
    def request(q,key,body=None,conditions=None,operation='routing_price.register'):
        body=price_body(q) if body is None else body
        conditions=conditions or PricePreconditions(0,if_none_match='*')
        binding=q['service'].binding(q['ctx'],body,operation=operation,preconditions=conditions,idempotency_key=key,access_token=q['credentials'].access_token)
        proof=q['adapter'].issue(q['ctx'],binding,access_token=q['credentials'].access_token,password=q['password'])
        return dict(context=q['ctx'],body=body,operation=operation,preconditions=conditions,idempotency_key=key,access_token=q['credentials'].access_token,proof=proof)
    def execute(q,r):
        return (q['service'].register_price if r['operation']=='routing_price.register' else q['service'].revoke_price)(**{k:v for k,v in r.items() if k!='operation'})
    def register(q,key,previous=None):
        return execute(q,request(q,key,price_body(q,0 if previous is None else previous.current_revision),None if previous is None else PricePreconditions(previous.current_revision,if_match=previous.current_etag)))
    def revocation(q,view,key):
        return request(q,key,dict(series_id=view.series_id,price_version=view.price_version,reason_code='ADMIN_REVOKED'),PricePreconditions(view.price_version,if_match=view.version_etag),'routing_price.revoke')
    def read(q,v):return q['service'].get_price(q['ctx'],v.series_id,v.price_version,access_token=q['credentials'].access_token)
    def business(q):
        with connect(secrets['pg_password']) as conn:
            return tuple(conn.execute('SELECT count(*) FROM '+name+' WHERE tenant_id=%s',(q['ctx'].tenant_id,)).fetchone()[0] for name in ('routing_price_versions','routing_price_current','routing_price_revocations','idempotency_records','audit_events'))
    @contextmanager
    def limited(q,cap='routing_price.register',**scope):
        with connect(secrets['app_password'],app=True) as conn:
            values=dict(tenant_id=q['ctx'].tenant_id,workspace_id=q['ctx'].workspace_id,actor_id=q['ctx'].actor_id,capability=cap);values.update(scope)
            for name,value in values.items():conn.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            yield conn
    def step(name):emit(name,status='RUNNING')
    def denied_sql(q,statement,params=(),states=frozenset({'23514'}),cap='routing_price.register',**scope):
        def call():
            with limited(q,cap,**scope) as conn:conn.execute(statement,params)
        return sql_denial(call,states)
    def group1():
        step('group1_readiness')
        with connect(secrets['app_password'],app=True) as conn:PostgresPriceSourceRepository._ready(conn)
        mutations=(
          'DROP TRIGGER routing_price_versions_write ON routing_price_versions',
          'DROP TRIGGER routing_price_versions_write ON routing_price_versions; CREATE TRIGGER routing_price_versions_write BEFORE INSERT ON routing_price_versions FOR EACH ROW WHEN(false) EXECUTE FUNCTION guard_routing_price_write()',
          'ALTER POLICY routing_price_versions_scope ON routing_price_versions USING(true)',
          'ALTER TABLE routing_price_current DROP CONSTRAINT routing_price_current_version_fk',
          'ALTER TABLE routing_price_versions DROP CONSTRAINT routing_price_versions_valid',
          'REVOKE INSERT ON routing_price_versions FROM daon_app',
          'GRANT UPDATE ON routing_price_versions TO daon_app',
          "ALTER FUNCTION valid_routing_price_version(routing_price_versions) VOLATILE")
        for index,mutation in enumerate(mutations):
            step('group1_catalog_'+str(index));conn=connect(secrets['pg_password'])
            try:
                conn.execute(mutation)
                expect_denial(lambda:PostgresPriceSourceRepository._ready(conn),RoutingPriceStorageError,'PRICE_SOURCE_UNAVAILABLE',503)
            finally:conn.rollback();conn.close()
        with connect(secrets['app_password'],app=True) as conn:PostgresPriceSourceRepository._ready(conn)
    def group2():
        q=fixture('normal');step('group2_pool_timeouts')
        with q['store']._transaction(_cloud(q['ctx'],'routing_price.read')) as conn:
            require(conn.execute('SHOW statement_timeout').fetchone()==('15s',),'PRICE_POOL_STATEMENT_TIMEOUT_INVALID')
            require(conn.execute('SHOW lock_timeout').fetchone()==('8s',),'PRICE_POOL_LOCK_TIMEOUT_INVALID')
        step('group2_register');r=request(q,'price-normal-register-key');v=execute(q,r)
        require((v.price_version,v.current_revision,v.verification_state,v.revoked,v.target_changed)==(1,1,'unverified',False,False),'PRICE_NORMAL_INVALID')
        require(business(q)==(1,1,0,1,1) and identity_counts(q)==(1,1),'PRICE_NORMAL_COUNTS_INVALID')
        before=business(q);consumed=identity_counts(q);audit_before=len(q['audit'].list(tenant_id=q['ctx'].tenant_id,limit=100).items)
        step('group2_read_select_only');q['store'].observe=True;q['store'].statements=[]
        restarted=PostgresPriceSourceRepository(q['store'],registry=registry,clock=q['clock'])
        require(restarted.get(q['ctx'],v.series_id)==v and read(q,v)==v,'PRICE_RESTART_READ_INVALID')
        require(q['store'].statements and set(q['store'].statements)=={'SELECT'},'PRICE_READ_SQL_NOT_SELECT_ONLY');q['store'].observe=False
        require(business(q)==before and identity_counts(q)==consumed,'PRICE_READ_WROTE_BUSINESS')
        require(len(q['audit'].list(tenant_id=q['ctx'].tenant_id,limit=100).items)>audit_before,'PRICE_SECURITY_AUDIT_LOST')
        require(execute(q,r)==v and business(q)==before,'PRICE_COMMITTED_REPLAY_INVALID')
        second=register(q,'price-normal-second-key',v);third=register(q,'price-normal-third-key',second)
        require(third.price_version==third.current_revision==3 and third.series_id==v.series_id and read(q,v).price_version==1,'PRICE_VERSION_SEQUENCE_INVALID')
    def group3():
        q=fixture('rollback');step('group3_precommit_rollback');r=request(q,'price-rollback-register-key');before=business(q);q['store'].fail_before_commit=True
        try:execute(q,r)
        except CommitFault:pass
        else:raise ValueError('PRICE_FAULT_NOT_REACHED')
        require(business(q)==before and identity_counts(q)==(1,1),'PRICE_ROLLBACK_INVALID')
        q['clock'].advance(minutes=6);step('group3_consumed_expired_recovery');v=execute(q,r)
        require(execute(q,r)==v and identity_counts(q)==(1,1),'PRICE_RECOVERY_INVALID')
        step('group3_other_body');changed=deepcopy(r);changed['body']['unit_prices']['mandatory_fee_per_request']='1'
        expect_denial(lambda:execute(q,changed),RoutingPriceStorageError,'IDEMPOTENCY_KEY_REUSED',409)
        step('group3_other_key');expect_denial(lambda:execute(q,{**r,'idempotency_key':'price-different-recovery-key'}),RoutingPriceStorageError,'PRICE_VERSION_CONFLICT',409)
        step('group3_initial_expired');fresh=request(q,'price-initial-expired-key',price_body(q,1),PricePreconditions(1,if_match=v.current_etag));q['clock'].advance(minutes=6)
        expect_denial(lambda:execute(q,fresh),IdentityError,'STEP_UP_EXPIRED',403)
        step('group3_new_session');other=q['identity'].local_login(login_id=q['login'],password=q['password'],platform=DevicePlatform.WEB,trace_id=q['ctx'].trace_id,policy_version=q['ctx'].policy_version)
        expect_denial(lambda:execute(q,{**r,'access_token':other.access_token}),RoutingPriceStorageError,'PRICE_CURRENT_ACCESS_DENIED',403)
        step('group3_session_revoked')
        with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='revoked' WHERE session_id=?",(q['ctx'].session_id,))
        expect_denial(lambda:execute(q,r),IdentityError,'SESSION_REVOKED',401)
        with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='active' WHERE session_id=?",(q['ctx'].session_id,))
        step('group3_role_revoked')
        with q['auth_repo'].transaction() as conn:conn.execute("UPDATE auth_tenant_roles SET state='inactive'")
        expect_denial(lambda:execute(q,r),AuthorizationError,'ACTION_DENIED',403)
    def group4():
        q=fixture('revoke');step('group4_versions');v1=register(q,'price-revoke-first-key');v2=register(q,'price-revoke-second-key',v1)
        old=read(q,v1);r1=revocation(q,old,'price-historical-revoke-key');historical=execute(q,r1)
        require(historical.revoked and historical.current_revision==2,'PRICE_HISTORICAL_REVOKE_POINTER_CHANGED')
        current=read(q,v2);r2=revocation(q,current,'price-current-revoke-key');rv=execute(q,r2)
        require((rv.price_version,rv.current_revision,rv.revoked)==(2,3,True) and execute(q,r2)==rv,'PRICE_CURRENT_REVOKE_INVALID')
        step('group4_revision_distinct');v3=register(q,'price-revoke-third-key',rv)
        require((v3.price_version,v3.current_revision)==(3,4) and v3.series_id==v1.series_id,'PRICE_REVISION_INVALID')
        step('group4_stale_conditions')
        expect_denial(lambda:request(q,'price-stale-condition-key',price_body(q,2),PricePreconditions(2,if_match=v2.current_etag)),RoutingPriceStorageError,'PRICE_VERSION_CONFLICT',409)
        for verb in ('UPDATE routing_price_versions SET created_by=created_by','DELETE FROM routing_price_versions','TRUNCATE routing_price_versions','DELETE FROM routing_price_current','TRUNCATE routing_price_current','UPDATE routing_price_revocations SET created_by=created_by'):
            step('group4_immutable');denied_sql(q,verb,states={'42501','55000'})
        step('group4_pointer_reverse');denied_sql(q,'UPDATE routing_price_current SET current_revision=current_revision-1 WHERE series_id=%s',(v3.series_id,))
        with limited(q) as conn:
            result=conn.execute('SELECT * FROM routing_price_versions WHERE series_id=%s AND price_version=3',(v3.series_id,));columns=[d.name for d in result.description];original=dict(zip(columns,result.fetchone(),strict=True))
        def insert(conn,value):
            data=deepcopy(value);data['payload']=Jsonb(data['payload']);names=list(data)
            conn.execute(sql.SQL('INSERT INTO routing_price_versions ({}) VALUES ({})').format(sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),[data[k] for k in names])
        base=deepcopy(original);base['price_version']=4;base['payload']['price_version']=4;base['payload']['body']['expected_version']=4
        base['canonical_text']=canonical(base['payload']);base['content_digest']=digest(base['payload'])
        step('group4_direct_valid_control')
        with limited(q) as conn:
            with conn.transaction(force_rollback=True):insert(conn,base)
        for index,change in enumerate((dict(content_digest='0'*64),dict(canonical_text='{}'),dict(created_by='different-actor'))):
            step('group4_scalar_negative_'+str(index))
            def bad(change=change):
                with limited(q) as conn:insert(conn,{**base,**change})
            sql_denial(bad,{'42501'} if 'created_by' in change else {'23514'})
        for index,amount in enumerate((None,'0.0000001','NaN')):
            step('group4_payload_negative_'+str(index));bad=deepcopy(base);bad['payload']['body']['unit_prices']['mandatory_fee_per_request']=amount;bad['canonical_text']=canonical(bad['payload']);bad['content_digest']=digest(bad['payload'])
            def invalid():
                with limited(q) as conn:insert(conn,bad)
            sql_denial(invalid,{'23514'})
        step('group4_scoped_fk')
        body=dict(tenant_id=q['ctx'].tenant_id,anchor_workspace_id=q['ctx'].workspace_id,series_id='price-missing-parent',price_version=1,created_by=q['ctx'].actor_id,revoked_at=now.isoformat(),reason_code='ADMIN_REVOKED')
        denied_sql(q,'INSERT INTO routing_price_revocations(tenant_id,anchor_workspace_id,series_id,price_version,created_by,revoked_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,1,%s,%s,%s,%s,%s)',(q['ctx'].tenant_id,q['ctx'].workspace_id,body['series_id'],q['ctx'].actor_id,now,canonical(body),Jsonb(body),digest(body)),states={'23503'},cap='routing_price.revoke')
    def group5():
        q=fixture('drift');step('group5_snapshot_before_consume');r=request(q,'price-drift-register-key')
        with connect(secrets['pg_password']) as conn:conn.execute('UPDATE system_provider_connections SET version=2 WHERE connection_id=%s',(q['connection'],))
        expect_denial(lambda:execute(q,r),RoutingPriceStorageError,'PRICE_TARGET_CHANGED',409)
        require(identity_counts(q)==(1,0) and business(q)==(0,0,0,0,0),'PRICE_DRIFT_CONSUMED')
        with connect(secrets['pg_password']) as conn:conn.execute('UPDATE system_provider_connections SET version=1 WHERE connection_id=%s',(q['connection'],))
        v=execute(q,r);v2=register(q,'price-drift-second-key',v)
        drift_counts=identity_counts(q);drift_business=business(q)
        for index,statement in enumerate(("UPDATE system_provider_connections SET base_url='https://changed.example.invalid',version=2 WHERE connection_id=%s","UPDATE system_provider_models SET catalog_version=2 WHERE connection_id=%s")):
            step('group5_committed_drift_'+str(index))
            with connect(secrets['pg_password']) as conn:conn.execute(statement,(q['connection'],))
            require(execute(q,r).target_changed and identity_counts(q)==drift_counts and business(q)==drift_business,'PRICE_COMMITTED_DRIFT_INVALID')
        step('group5_keyless_credential_rotation_denied')
        def rotate():
            with connect(secrets['pg_password']) as conn:conn.execute('UPDATE system_provider_connections SET credential_version=1 WHERE connection_id=%s',(q['connection'],))
        sql_denial(rotate,{'23514'})
        emit('credential_rotation',status='NOT_RUN',safe_code='PRICE_CREDENTIAL_ROTATION_REQUIRES_EXCLUDED_SECRET')
        step('group5_disabled_read_revoke')
        with connect(secrets['pg_password']) as conn:conn.execute('UPDATE system_provider_connections SET enabled=false WHERE connection_id=%s',(q['connection'],))
        loaded=read(q,v);require(loaded.target_changed,'PRICE_DISABLED_TARGET_HIDDEN');require(execute(q,revocation(q,loaded,'price-disabled-revoke-key')).revoked,'PRICE_DISABLED_REVOKE_FAILED')
        step('group5_deleted_target_history')
        with connect(secrets['pg_password']) as conn:conn.execute('DELETE FROM system_provider_connections WHERE connection_id=%s',(q['connection'],))
        require(read(q,v).revoked and read(q,v).target_changed,'PRICE_DELETED_TARGET_HIDDEN')
        step('group5_deleted_target_revoke');deleted=read(q,v2)
        require(execute(q,revocation(q,deleted,'price-deleted-revoke-key')).revoked,'PRICE_DELETED_REVOKE_FAILED')
        step('group5_current_scope')
        for changed in (replace(q['ctx'],tenant_id='different-tenant',organization_id='different-tenant'),replace(q['ctx'],device_id='different-device')):
            expect_denial(lambda:q['service'].get_price(changed,v.series_id,access_token=q['credentials'].access_token),RoutingPriceStorageError,'PRICE_CURRENT_ACCESS_DENIED',403)
        step('group5_personal_source_rejected')
        from daon_user_api.routing_pricing import RoutingPricingError
        personal=price_body(q);personal['credential_source']='personal'
        try:request(q,'price-personal-source-key',personal)
        except RoutingPricingError as error:require(str(error)=='PRICE_INPUT_INVALID','PRICE_PERSONAL_SOURCE_DENIAL_INVALID')
        else:raise ValueError('PRICE_PERSONAL_SOURCE_DENIAL_MISSING')
        step('group5_rls')
        for scope in (dict(tenant_id='other-tenant'),dict(workspace_id='other-workspace'),dict(capability=''),dict(capability='questions.read')):
            with limited(q,**scope) as conn:require(conn.execute('SELECT count(*) FROM routing_price_versions').fetchone()==(0,),'PRICE_RLS_LEAK')
        step('group5_pg_workflow')
        with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_org_tenant_memberships SET state='suspended' WHERE tenant_id=%s",(q['ctx'].tenant_id,))
        try:expect_denial(lambda:q['repository'].get(q['ctx'],v.series_id),OrganizationWorkflowError,'ORGANIZATION_MEMBERSHIP_SUSPENDED',403)
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_org_tenant_memberships SET state='active' WHERE tenant_id=%s",(q['ctx'].tenant_id,))
        step('group5_pg_session')
        with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_sessions SET state='revoked' WHERE session_id=%s",(q['ctx'].session_id,))
        try:expect_denial(lambda:q['repository'].get(q['ctx'],v.series_id),RoutingPriceStorageError,'PRICE_CURRENT_ACCESS_DENIED',403)
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_sessions SET state='active' WHERE session_id=%s",(q['ctx'].session_id,))
        step('group5_actual_role_lock');entered=Event();finished=Event()
        def revoke_role():
            with connect(secrets['pg_password']) as conn:
                entered.set();conn.execute("UPDATE identity_auth_tenant_roles SET state='inactive' WHERE tenant_id=%s",(q['ctx'].tenant_id,))
            finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with limited(q,'routing_price.read') as conn:
                _authorize(conn,q['ctx'],now=q['clock']());future=pool.submit(revoke_role);require(entered.wait(2),'PRICE_LOCK_THREAD_NOT_ENTERED')
                def locked():
                    with connect(secrets['pg_password']) as c:c.execute('SELECT user_id FROM identity_auth_tenant_roles WHERE tenant_id=%s FOR UPDATE NOWAIT',(q['ctx'].tenant_id,))
                sql_denial(locked,{'55P03'});require(not finished.is_set(),'PRICE_ROLE_LOCK_BYPASSED')
            future.result(timeout=10)
        expect_denial(lambda:q['repository'].get(q['ctx'],v.series_id),RoutingPriceStorageError,'PRICE_CURRENT_ACCESS_DENIED',403)
    def group6():
        q=fixture('race');step('group6_same_revision');r1=request(q,'price-race-first-key');r2=request(q,'price-race-second-key');barrier=Barrier(2)
        def run(r):
            barrier.wait(timeout=5)
            try:return ('ok',execute(q,r))
            except RoutingPriceStorageError as error:return ('denied',error.code)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fs=[pool.submit(run,r) for r in (r1,r2)];results=[f.result(timeout=20) for f in fs]
        require([x[0] for x in results].count('ok')==1 and ('denied','PRICE_VERSION_CONFLICT') in results,'PRICE_RACE_INVALID')
        winner=r1 if results[0][0]=='ok' else r2;v=execute(q,winner)
        require(business(q)==(1,1,0,1,1),'PRICE_RACE_DUPLICATE')
        step('group6_same_key');barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:repeated=[f.result(timeout=20) for f in (pool.submit(run,winner),pool.submit(run,winner))]
        require(all(x==('ok',v) for x in repeated) and business(q)==(1,1,0,1,1),'PRICE_SAME_KEY_DUPLICATE')
        step('group6_register_revoke_race');reg=request(q,'price-race-next-register',price_body(q,1),PricePreconditions(1,if_match=v.current_etag));rev=revocation(q,v,'price-race-current-revoke');barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=[f.result(timeout=20) for f in (pool.submit(run,reg),pool.submit(run,rev))]
        current=q['service'].get_price(q['ctx'],v.series_id,access_token=q['credentials'].access_token)
        require(current.current_revision==2 and current.verification_state=='unverified','PRICE_REGISTER_REVOKE_RACE_INVALID')
        require(all(x[0]=='ok' or x==('denied','PRICE_VERSION_CONFLICT') for x in outcomes),'PRICE_RACE_UNEXPECTED_DENIAL')
        other=fixture('independent');other_req=request(other,'price-independent-register-key')
        step('group6_other_org_while_locked')
        next_req=request(q,'price-lock-next-register-key',price_body(q,current.current_revision),PricePreconditions(current.current_revision,if_match=current.current_etag))
        entered=Event();finished=Event()
        def waiting_same_series():
            entered.set()
            result=execute(q,next_req);finished.set();return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            with limited(q) as conn:
                lock='routing-price|'+q['ctx'].tenant_id+'|'+v.series_id
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(lock,))
                with limited(q) as check:require(check.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(lock,)).fetchone()==(False,),'PRICE_LOCK_NOT_HELD')
                step('group6_same_series_wait');waiting=pool.submit(waiting_same_series)
                require(entered.wait(2),'PRICE_WAIT_THREAD_NOT_ENTERED')
                result=pool.submit(execute,other,other_req).result(timeout=5)
                require(result.price_version==1 and business(other)==(1,1,0,1,1),'PRICE_OTHER_ORG_BLOCKED')
                require(not finished.is_set(),'PRICE_SAME_SERIES_BYPASSED_LOCK')
            require(waiting.result(timeout=20).current_revision==current.current_revision+1,'PRICE_SAME_SERIES_WAIT_FAILED')
    failures=[]
    try:
        for index,group in enumerate((group1,group2,group3,group4,group5,group6),1):
            name='group'+str(index);emit(name,status='RUNNING')
            try:group();emit(name,status='PASS')
            except Exception as error:
                failed_test=CURRENT_TEST;failures.append(name)
                code=str(error) if type(error) is ValueError and re.fullmatch('PRICE_[A-Z0-9_]+',str(error)) else 'PRICE_GATE_FAILED'
                emit(name,status='FAIL',test_name=failed_test,exception_type=safe_exception_type(error),safe_code=code,**safe_error_metadata(error))
        require(not failures,'PRICE_GROUPS_FAILED')
        emit('complete',status='PASS_WITH_NOT_RUN',groups=6,model_calls=0,not_run=['credential_rotation_positive'],identity_fixture='isolated-synthetic-sqlite-mirrored-whitelist')
    finally:
        for store in stores:store.close()
        for q in fixtures:q['repo'].close()
        temp.cleanup()



def safe_error_metadata(error):
    result=dict(sqlstate=None,column=None,permission_type=None,permission_name=None,constraint=None,table=None)
    cause=error;seen=set()
    for _ in range(8):
        if cause is None or id(cause) in seen:break
        seen.add(id(cause));diag=getattr(cause,'diag',None)
        state=getattr(cause,'sqlstate',None) or getattr(diag,'sqlstate',None)
        if result['sqlstate'] is None and type(state) is str and re.fullmatch('[A-Z0-9]{5}',state):result['sqlstate']=state
        column=getattr(diag,'column_name',None)
        if result['column'] is None and type(column) is str and re.fullmatch('[a-z_][a-z0-9_]{0,62}',column):result['column']=column
        for key,attribute in [('constraint','constraint_name'),('table','table_name')]:
            identifier=getattr(diag,attribute,None)
            if result[key] is None and type(identifier) is str and re.fullmatch('[a-z_][a-z0-9_]{0,62}',identifier):result[key]=identifier
        primary=getattr(diag,'message_primary',None)
        if state=='42501' and type(primary) is str and result['permission_name'] is None:
            matched=re.fullmatch(r'permission denied for (table|relation|sequence|function) ([a-z_][a-z0-9_]{0,62})',primary)
            if matched:result['permission_type'],result['permission_name']=matched.groups()
        cause=getattr(cause,'__cause__',None) or getattr(cause,'__context__',None)
    return result


def main():
    stage=sys.argv[1] if len(sys.argv)==2 else ''
    require(stage in {'bootstrap','probe'},'PRICE_STAGE_INVALID')
    secrets=credentials()
    if stage=='bootstrap':bootstrap(secrets)
    else:run_probe(secrets)

if __name__=='__main__':
    try:main()
    except Exception as error:
        code=str(error) if type(error) is ValueError and re.fullmatch('PRICE_[A-Z0-9_]+',str(error)) else 'PRICE_GATE_FAILED'
        emit('failed',test_name=CURRENT_TEST,status='FAIL',exception_type=safe_exception_type(error),safe_code=code,**safe_error_metadata(error))
        raise SystemExit(1) from None
