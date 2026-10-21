"""A2 전용 격리 PostgreSQL gate. 일반 DB/DSN 입력을 받지 않는다.
실행 책임은 main이며 사전검사 단위시험은 Docker/SQL을 실행하지 않는다.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path

ISSUE='R3-A2-PG-20261021'
DB='qa_routing_a2_20261021'
APP='qa_routing_a2_app_20261021'
PG='daon-user-routing-a2-pg-20261021'
RUNNER='daon-user-routing-a2-runner-20261021'
TAG='daon-user-routing-a2-qa:20261021'
PG_IMAGE='sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e'
BASE='python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae'
HASHES={'0074_routing_policy.sql':'03568962142b86c611bf0a4811540b75b7b645373af0cafdbb7672f433a196fe',
 '0075_routing_execution.sql':'5b8e8dc3e3426081c488177db5eaaa55d209a051eb0288ddecaa7c092d2c5e5d',
 '0076_routing_budget_sources.sql':'8d2fc5efc942c3a012e4432135d359308ef6844b92f1b3b9467fd7a41796d732'}

def require(ok,code):
    if not ok:raise ValueError(code)

def read_approved_root(wrapper):
    """운영 wrapper의 단일 literal 정본만 읽는다. shell/env/CLI 평가는 없다."""
    wrapper=Path(wrapper)
    try:
        for entry in (wrapper,*wrapper.parents):
            require(not entry.is_symlink() and not entry.is_junction(),'A2_WRAPPER_LINK_FORBIDDEN')
        before=wrapper.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size<=65536,'A2_WRAPPER_FILE_INVALID')
        with wrapper.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            require((before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino),'A2_WRAPPER_FILE_CHANGED')
            raw=stream.read(65537)
            after=os.fstat(stream.fileno())
        require(len(raw)<=65536 and (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'A2_WRAPPER_FILE_CHANGED')
        text=raw.decode('utf-8')
    except (OSError,UnicodeError) as error:
        raise ValueError('A2_WRAPPER_READ_FAILED') from None
    assignments=[line for line in text.splitlines() if not line.lstrip().startswith('#') and re.search(r'\bROOT\s*=',line)]
    require(len(assignments)==1,'A2_WRAPPER_ROOT_AMBIGUOUS')
    match=re.fullmatch(r'readonly ROOT=(/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)',assignments[0])
    require(match is not None,'A2_WRAPPER_ROOT_NOT_LITERAL')
    root=match.group(1)
    require(root.rsplit('/',1)[-1]=='daon-user-routing-a2-qa-'+ISSUE.rsplit('-',1)[-1],'A2_WRAPPER_NAMESPACE_INVALID')
    return root

ROOT=read_approved_root(Path(__file__).absolute().parent.parent/'run-routing-a2-postgres-gate.sh')

def validate_target(config):
    expected=dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT)
    require(set(config)==set(expected)|{'pg_id'},'A2_TARGET_FIELDS_INVALID')
    require(all(config[k]==v for k,v in expected.items()),'A2_TARGET_NOT_APPROVED')
    require(type(config['pg_id']) is str and re.fullmatch('[0-9a-f]{64}',config['pg_id']),'A2_CONTAINER_ID_INVALID')
    return True

def verify_sql(root):
    root=Path(root)
    for name,sha in HASHES.items():
        require(hashlib.sha256((root/'services/api/migrations/versions'/name).read_bytes()).hexdigest()==sha,'A2_SQL_HASH_MISMATCH')

def verify_container(value,*,kind,revision,pg_id=None,qa_image=None):
    require(kind in {'pg','runner'},'A2_CONTAINER_KIND_INVALID')
    require(type(value.get('Id')) is str and re.fullmatch('[0-9a-f]{64}',value['Id']),'A2_CONTAINER_ID_INVALID')
    if kind=='pg':validate_target(dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT,pg_id=value['Id']))
    config=value['Config'];host=value['HostConfig'];labels=config.get('Labels') or {}
    require(value['Name']=='/'+(PG if kind=='pg' else RUNNER),'A2_CONTAINER_NAME_INVALID')
    require(labels.get('io.daon.qa.issue')==ISSUE and labels.get('io.daon.qa.revision')==revision,'A2_RESOURCE_NOT_OWNED')
    require(host.get('NetworkMode')==('none' if kind=='pg' else 'container:'+pg_id),'A2_NETWORK_NOT_ISOLATED')
    require(not host.get('PortBindings') and not host.get('Binds') and not host.get('Privileged'),'A2_HOST_ACCESS_FORBIDDEN')
    require(host.get('Memory')==(1073741824 if kind=='pg' else 536870912) and host.get('NanoCpus')==1000000000,'A2_RESOURCE_LIMIT_INVALID')
    require(not host.get('Devices') and not host.get('VolumesFrom') and host.get('PidMode','') in {'','private'} and host.get('IpcMode','private') in {'','private'},'A2_HOST_ACCESS_FORBIDDEN')
    require(value['Image']==(PG_IMAGE if kind=='pg' else qa_image),'A2_IMAGE_MISMATCH')
    tmpfs=host.get('Tmpfs') or {};expected='/var/lib/postgresql/data' if kind=='pg' else '/tmp'
    require(tmpfs=={expected:'rw,noexec,nosuid,size='+('536870912' if kind=='pg' else '134217728')},'A2_TMPFS_INVALID')
    require(all(m.get('Type')=='tmpfs' and m.get('Destination')==expected for m in value.get('Mounts',[])),'A2_MOUNT_FORBIDDEN')
    if kind=='runner':
        require(host.get('ReadonlyRootfs') is True,'A2_RUNNER_WRITABLE_ROOT')
        require(config.get('User')=='65534:65534','A2_RUNNER_USER_INVALID')
    return value['Id']

CURRENT_TEST='preflight'
EXCEPTION_TYPES=frozenset({'ValueError','TypeError','IdentityError','AuthorizationError','RoutingExecutionError','CloudDatabaseError','ImportError','ModuleNotFoundError','ProgrammingError','IntegrityError','OperationalError','InsufficientPrivilege','ForeignKeyViolation','NotNullViolation','CheckViolation','UniqueViolation','AssertionError','Exception','RoutingSourceError','OrganizationWorkflowError'})

def safe_exception_type(error):
    name=type(error).__name__
    return name if name in EXCEPTION_TYPES else 'Exception'

def expect_denial(call,expected_type,code,status):
    try:call()
    except expected_type as error:
        require(getattr(error,'code',None)==code and getattr(error,'status',getattr(error,'http_status',None))==status,'A2_UNEXPECTED_DENIAL')
        return
    raise ValueError('A2_EXPECTED_DENIAL_MISSING')

def emit(stage,**values):
    global CURRENT_TEST
    allowed={'status','schema','sql_hashes','test_name','safe_code','sqlstate','column','permission_type','permission_name','groups','model_calls','identity_fixture','exception_type','constraint','table'}
    require(set(values)<=allowed,'A2_OUTPUT_FIELDS_INVALID')
    require(type(stage) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',stage),'A2_OUTPUT_FIELDS_INVALID')
    require(values.get('status') in {'RUNNING','PASS','FAIL'},'A2_OUTPUT_FIELDS_INVALID')
    for key,value in values.items():
        if key=='status':continue
        if key=='sql_hashes':require(value==HASHES,'A2_OUTPUT_FIELDS_INVALID')
        elif key=='schema':require(value=='baseline0072+exactSQL0074/0075/0076','A2_OUTPUT_FIELDS_INVALID')
        elif key=='groups':require(type(value) is int and 0<=value<=6,'A2_OUTPUT_FIELDS_INVALID')
        elif key=='model_calls':require(type(value) is int and value==0,'A2_OUTPUT_FIELDS_INVALID')
        elif key=='identity_fixture':require(value=='isolated-synthetic-sqlite-mirrored-whitelist','A2_OUTPUT_FIELDS_INVALID')
        elif key in {'constraint','table'}:require(value is None or type(value) is str and re.fullmatch('[a-z_][a-z0-9_]{0,62}',value),'A2_OUTPUT_FIELDS_INVALID')
        elif key=='exception_type':require(type(value) is str and value in EXCEPTION_TYPES,'A2_OUTPUT_FIELDS_INVALID')
        elif key=='safe_code':require(type(value) is str and re.fullmatch('A2_[A-Z0-9_]+',value),'A2_OUTPUT_FIELDS_INVALID')
        elif value is not None:require(type(value) is str and re.fullmatch('[A-Za-z0-9_]{1,128}',value),'A2_OUTPUT_FIELDS_INVALID')
    CURRENT_TEST=stage
    print(json.dumps(dict(issue_id=ISSUE,stage=stage,**values),sort_keys=True),flush=True)


def credentials():
    value=json.load(sys.stdin)
    require(set(value)=={'pg_password','app_password'},'A2_CREDENTIAL_FIELDS_INVALID')
    for secret in value.values():require(type(secret) is str and re.fullmatch('[0-9a-f]{64}',secret),'A2_CREDENTIAL_SHAPE_INVALID')
    return value

def connect(secret,*,app=False,database=DB):
    import psycopg
    return psycopg.connect(host='127.0.0.1',port=5432,dbname=database,user=APP if app else 'postgres',
        password=secret,connect_timeout=5,options='-c statement_timeout=15000 -c lock_timeout=8000')

def pool_dsn(secret):
    """실제 pool의 모든 연결에도 제한을 전달한다. 임의 DSN/옵션 입력은 없다."""
    from sqlalchemy.engine import URL
    require(type(secret) is str and re.fullmatch('[0-9a-f]{64}',secret),'A2_CREDENTIAL_SHAPE_INVALID')
    from urllib.parse import urlencode,quote
    base=URL.create('postgresql',username=APP,password=secret,host='127.0.0.1',port=5432,database=DB).render_as_string(hide_password=False)
    # libpq URI는 '+'를 공백으로 바꾸지 않는다. options의 공백은 정확 %20이다.
    return base+'?'+urlencode({'connect_timeout':'5','options':'-c statement_timeout=15000 -c lock_timeout=8000'},quote_via=quote)


def bootstrap(secrets):
    from psycopg import sql
    root=Path('/qa');verify_sql(root)
    require(sys.version_info[:3]==(3,14,3),'A2_PYTHON_VERSION_INVALID')
    with connect(secrets['pg_password'],database='postgres') as conn:
        require(conn.execute('SELECT current_user').fetchone()==('postgres',),'A2_ADMIN_ROLE_INVALID')
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
        require(result.returncode==0,'A2_BASELINE_PHASE_'+target+'_FAILED')
    with connect(secrets['pg_password']) as conn:
        conn.execute('SET LOCAL ROLE daon_app')
        require(conn.execute("SELECT current_user,has_schema_privilege(current_user,'public','CREATE')").fetchone()==('daon_app',True),'A2_APP_SCHEMA_PRECONDITION')
    emit('baseline_app_0040',status='RUNNING');migrate('0040',app=True);emit('baseline_app_0040',status='PASS')
    with connect(secrets['pg_password']) as conn:
        row=conn.execute("SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='organization_license_versions'::regclass").fetchone()
        require(row==('daon_app',True,True),'A2_LICENSE_OWNER_PRECONDITION')
        for signature in ('transition_canon_state(text,text,integer,text,text,text,text,text)','claim_document_processing_job(text,integer)'):
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==('daon_app',),'A2_FUNCTION_OWNER_PRECONDITION')
    emit('baseline_admin_0072',status='RUNNING');migrate('0072');emit('baseline_admin_0072',status='PASS')
    with connect(secrets['pg_password']) as conn:
        require(conn.execute('SELECT version_num FROM alembic_version').fetchall()==[('0072',)],'A2_REVISION_INVALID')
        for signature,owner in [('transition_canon_state(text,text,integer,text,text,text,text,text)','daon_notebook_delete'),('claim_document_processing_job(text,integer)','postgres')]:
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==(owner,),'A2_FUNCTION_OWNER_POSTCONDITION')
        require(conn.execute("SELECT to_regclass('operations_run_cost_events'),to_regclass('operations_host_capacity_samples')").fetchone()==(None,None),'A2_0073_FORBIDDEN')
        for name in HASHES:
            emit('apply_'+name[:4],status='RUNNING')
            conn.execute((root/'services/api/migrations/versions'/name).read_text())
            emit('apply_'+name[:4],status='PASS')
        conn.execute('REVOKE CREATE ON SCHEMA public FROM daon_app')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {} IN ROLE daon_app').format(sql.Identifier(APP),sql.Literal(secrets['app_password'])))
    with connect(secrets['app_password'],app=True) as conn:
        row=conn.execute("SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,has_schema_privilege(current_user,'public','CREATE'),pg_has_role(current_user,'daon_notebook_delete','MEMBER') FROM pg_roles WHERE rolname=current_user").fetchone()
        require(row==(APP,APP,False,False,False,False,False,False,False),'A2_RESTRICTED_ROLE_INVALID')
    emit('bootstrap',status='PASS',schema='baseline0072+exactSQL0074/0075/0076',sql_hashes=HASHES)

def create_budget_fixture(path,now,tenant,workspace,actor):
    """무작위 로컬 password와 실제 Identity/Authorization만 사용하는 합성 fixture."""
    import secrets
    from daon_user_api.identity import DevicePlatform,PASSWORD_HASHER
    from daon_user_api.authorization import AuthorizationService,SqliteAuthorizationRepository,Role
    from daon_user_api.routing_budget_reauthentication import BudgetReauthenticationAdapter
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
    credentials=identity.local_login(login_id=login,password=password,platform=DevicePlatform.WEB,trace_id='trace-a2-qa',policy_version='policy-a2-qa')
    auth_repo=SqliteAuthorizationRepository(path)
    auth_repo.bootstrap_workspace(tenant_id=tenant,workspace_id=workspace,owner_user_id=actor,owner_role=Role.ORGANIZATION_ADMIN,workspace_kind='organization',data_area='cloud_sync',cost_limit_cents=0,now=now)
    authorization=AuthorizationService(repository=auth_repo,audit_store=audit,clock=clock,identity_service=identity)
    adapter=BudgetReauthenticationAdapter(identity,authorization)
    principal=identity.validate_access(credentials.access_token,trace_id='trace-a2-qa',policy_version='policy-a2-qa')
    context=BudgetContext.from_principal(principal,workspace_id=workspace,trace_id='trace-a2-qa',policy_version='policy-a2-qa')
    return dict(identity=identity,repo=repository,auth_repo=auth_repo,auth=authorization,adapter=adapter,ctx=context,credentials=credentials,password=password,clock=clock,path=path,login=login,audit=audit)


def identity_counts(q):
    with q['repo'].transaction() as conn:
        return tuple(conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('step_up_authorizations','step_up_consumptions'))


def budget_body(q,offset=0):
    from datetime import timedelta
    now=q['clock']()
    return dict(schema_version=1,currency='USD',period_limit='10',starts_at=(now+timedelta(days=offset)-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(days=offset+1)).isoformat(),allocations=[dict(workspace_id=q['ctx'].workspace_id,period_limit='7',run_limit='2')])


def prepare_budget(q,*,key,body=None,operation='routing_budget.issue',conditions=None):
    from daon_user_api.routing_sources import BudgetPreconditions
    from daon_user_api.routing_budget_reauthentication import digest
    body=budget_body(q) if body is None else body
    conditions=conditions or BudgetPreconditions(0,if_none_match='*')
    # 서비스 존재 시 실제 서비스의 현재 guard/anchor/통화 검증으로 binding을 만든다.
    if 'service' in q:
        binding=q['service'].binding(q['ctx'],body,preconditions=conditions,idempotency_key=key,operation=operation)
    else:
        binding=q['adapter'].binding(q['ctx'],operation=operation,body_digest=digest(body),preconditions=conditions,idempotency_key=key)
    proof=q['adapter'].issue(q['ctx'],binding,access_token=q['credentials'].access_token,password=q['password'])
    return dict(context=q['ctx'],body=body,preconditions=conditions,idempotency_key=key,access_token=q['credentials'].access_token,proof=proof,binding=binding)


def mirror_fixture(q,secrets):
    import sqlite3
    from psycopg import sql
    ctx=q['ctx']
    # 실제 합성 SQLite 생성행 중 현재 인증/인가에 필요한 표만 이식한다.
    tables=('tenants','users','memberships','devices','sessions','auth_workspaces','auth_tenant_roles','auth_memberships','auth_tenant_policies','auth_tenant_permission_rules','auth_workspace_policies','auth_workspace_permission_rules','auth_source_access','tenant_memberships')
    with connect(secrets['pg_password']) as conn,sqlite3.connect(q['path']) as local:
        conn.execute('INSERT INTO tenants(tenant_id,display_name) VALUES(%s,%s)',(ctx.tenant_id,'Synthetic A2 QA'))
        conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)',(ctx.tenant_id,ctx.workspace_id,'Synthetic A2 QA'))
        conn.execute('INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)',(ctx.tenant_id,ctx.actor_id))
        for name in tables:
            target='identity_org_tenant_memberships' if name=='tenant_memberships' else 'identity_'+name
            columns=conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(target,)).fetchall()
            require(bool(columns),'A2_IDENTITY_TABLE_MISSING')
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
        require(error.sqlstate in states,'A2_UNEXPECTED_SQLSTATE')
        emit(CURRENT_TEST,status='PASS',**safe_error_metadata(error))
        return error.sqlstate
    raise ValueError('A2_EXPECTED_SQL_DENIAL_MISSING')


def run_probe(secrets):
    emit('fixture_imports',status='RUNNING')
    import tempfile,sqlite3
    from contextlib import contextmanager
    from datetime import datetime,timedelta,timezone
    from dataclasses import replace
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event,Barrier
    from time import monotonic
    from psycopg import sql
    from psycopg.types.json import Jsonb
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.routing_sources import CurrencyRegistry,BudgetSourceService,BudgetPreconditions,RoutingSourceError,parse_budget_input
    from daon_user_api.routing_sources_postgres import PostgresBudgetSourceRepository,_authorize,_cloud
    from daon_user_api.routing_budget_reauthentication import canonical,digest
    from daon_user_api.workspace_model_defaults import WorkspaceModelDefaultsContext
    from daon_user_api.identity import IdentityError,DevicePlatform
    from daon_user_api.authorization import AuthorizationError
    from daon_user_api.organization_membership import OrganizationWorkflowError
    sys.path.insert(0,'/qa/services/api/tests')
    now=datetime.now(timezone.utc);temp=tempfile.TemporaryDirectory(prefix='a2-identity-',dir='/tmp');fixtures=[];stores=[]
    registry=CurrencyRegistry((('USD',2),('JPY',0)))
    dsn=pool_dsn(secrets['app_password'])
    class CommitFault(Exception):pass
    class OwnedStore(PostgresCloudStore):
        """실제 거래 commit 직전만 실패시킨다. 인가/SQL/보호는 실제 객체 그대로다."""
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
        emit('fixture_identity_'+label,status='RUNNING')
        path=Path(temp.name)/label;path.mkdir()
        q=create_budget_fixture(path,now,'tenant-a2-'+label,'workspace-a2-'+label,'owner-a2-'+label);fixtures.append(q)
        emit('fixture_mirror_'+label,status='RUNNING');mirror_fixture(q,secrets)
        store=OwnedStore(dsn);stores.append(store)
        repo=PostgresBudgetSourceRepository(store,registry=registry,clock=q['clock'])
        q.update(store=store,repository=repo,service=BudgetSourceService(repo,q['adapter'],registry=registry,clock=q['clock']))
        return q
    def request(q,key,**kw):return prepare_budget(q,key=key,**kw)
    def issue(q,req):return q['service'].issue_budget(**{k:v for k,v in req.items() if k!='binding'})
    def business(q):
        with connect(secrets['pg_password']) as conn:
            return tuple(conn.execute('SELECT count(*) FROM '+name+' WHERE tenant_id=%s',(q['ctx'].tenant_id,)).fetchone()[0] for name in ('routing_budget_periods','routing_budget_allocations','routing_budget_revocations','idempotency_records','audit_events'))
    @contextmanager
    def limited(q,cap='routing_budget.issue',**scope):
        with connect(secrets['app_password'],app=True) as conn:
            values=dict(tenant_id=q['ctx'].tenant_id,workspace_id=q['ctx'].workspace_id,actor_id=q['ctx'].actor_id,capability=cap);values.update(scope)
            for name,value in values.items():conn.execute('SELECT set_config(%s,%s,true)',('app.'+name,value))
            yield conn
    def step(name):emit(name,status='RUNNING')
    def rejected(q,statement,params=(),states=frozenset({'23514'}),cap='routing_budget.issue',**scope):
        def call():
            with limited(q,cap,**scope) as conn:conn.execute(statement,params)
        return sql_denial(call,states)
    def group1():
        step('group1_readiness')
        with connect(secrets['app_password'],app=True) as conn:PostgresBudgetSourceRepository._ready(conn)
        # owner의 rollback-only DDL 거래에서만 보호 catalog 변조를 시험한다.
        mutations=(
          'DROP TRIGGER routing_budget_periods_insert ON routing_budget_periods',
          'DROP TRIGGER routing_budget_periods_insert ON routing_budget_periods; CREATE TRIGGER routing_budget_periods_insert BEFORE INSERT ON routing_budget_periods FOR EACH ROW WHEN(false) EXECUTE FUNCTION guard_routing_budget_insert()',
          "DROP TRIGGER routing_budget_periods_insert ON routing_budget_periods; CREATE TRIGGER routing_budget_periods_insert BEFORE INSERT ON routing_budget_periods FOR EACH ROW EXECUTE FUNCTION guard_routing_budget_insert('qa')",
          'ALTER POLICY routing_budget_periods_scope ON routing_budget_periods USING(true)',
          'DROP INDEX routing_budget_one_anchor',
          'ALTER TABLE routing_budget_allocations DROP CONSTRAINT routing_budget_allocation_period_fk',
          'ALTER TABLE routing_budget_periods DROP CONSTRAINT routing_budget_periods_valid',
          'REVOKE INSERT ON routing_budget_periods FROM daon_app',
          "CREATE OR REPLACE FUNCTION guard_routing_budget_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN RETURN NEW; END $$")
        for index,mutation in enumerate(mutations):
            step('group1_catalog_'+str(index))
            conn=connect(secrets['pg_password'])
            try:
                conn.execute(mutation)
                expect_denial(lambda:PostgresBudgetSourceRepository._ready(conn),RoutingSourceError,'BUDGET_SOURCE_UNAVAILABLE',503)
            finally:conn.rollback();conn.close()
        with connect(secrets['app_password'],app=True) as conn:PostgresBudgetSourceRepository._ready(conn)
    def group2():
        q=fixture('normal');step('group2_actual_pool_timeouts')
        with q['store']._transaction(_cloud(q['ctx'],'routing_budget.read')) as conn:
            require(conn.execute('SHOW statement_timeout').fetchone()==('15s',),'A2_POOL_STATEMENT_TIMEOUT_INVALID')
            require(conn.execute('SHOW lock_timeout').fetchone()==('8s',),'A2_POOL_LOCK_TIMEOUT_INVALID')
        step('group2_issue')
        r=request(q,'a2-normal-issue-key');v=issue(q,r)
        require(business(q)[:4]==(2,1,0,1) and identity_counts(q)==(1,1),'A2_NORMAL_COUNTS_INVALID')
        before=business(q);consumed=identity_counts(q);audit_before=len(q['audit'].list(tenant_id=q['ctx'].tenant_id,limit=100).items)
        step('group2_restart_read')
        q['store'].observe=True;q['store'].statements=[]
        restarted=PostgresBudgetSourceRepository(q['store'],registry=registry,clock=q['clock'])
        require(restarted.get(q['ctx'],v.source.period_id)==v,'A2_RESTART_READ_INVALID')
        require(q['service'].get_budget(q['ctx'],v.source.period_id,access_token=r['access_token'])==v,'A2_SERVICE_READ_INVALID')
        ctx=WorkspaceModelDefaultsContext(q['ctx'].tenant_id,q['ctx'].workspace_id,q['ctx'].actor_id,q['ctx'].trace_id,q['ctx'].policy_version)
        with q['store']._transaction(_cloud(ctx,'routing_budget.upper')) as conn:upper=restarted.upper_budget(conn,ctx)
        require(upper.version=='1' and upper.currency=='USD' and upper.run_limit=='2' and upper.source_id==v.source.source_id,'A2_PROJECTION_INVALID')
        require(bool(q['store'].statements) and set(q['store'].statements)=={'SELECT'},'A2_READ_SQL_NOT_SELECT_ONLY')
        q['store'].observe=False
        require(business(q)==before and identity_counts(q)==consumed,'A2_READ_WROTE_BUSINESS')
        require(len(q['audit'].list(tenant_id=q['ctx'].tenant_id,limit=100).items)>audit_before,'A2_SECURITY_AUDIT_LOST')
        require(issue(q,r)==v and business(q)==before,'A2_EXACT_REPLAY_INVALID')
    def group3():
        q=fixture('rollback');step('group3_precommit_rollback')
        r=request(q,'a2-rollback-issue-key');before=business(q);q['store'].fail_before_commit=True
        try:issue(q,r)
        except CommitFault:pass
        else:raise ValueError('A2_FAULT_NOT_REACHED')
        require(business(q)==before and identity_counts(q)==(1,1),'A2_ROLLBACK_INVALID')
        q['clock'].advance(minutes=6)
        step('group3_consumed_expired_recovery');v=issue(q,r)
        require(identity_counts(q)==(1,1) and issue(q,r)==v,'A2_CONSUMED_RECOVERY_INVALID')
        for index,changed in enumerate((dict(idempotency_key='a2-other-issue-key'),dict(body={**r['body'],'period_limit':'11'}))):
            step('group3_binding_'+str(index))
            if index==0:expect_denial(lambda:issue(q,{**r,**changed}),IdentityError,'STEP_UP_REUSED',403)
            else:expect_denial(lambda:issue(q,{**r,**changed}),RoutingSourceError,'IDEMPOTENCY_KEY_REUSED',409)
        step('group3_initial_expiry')
        fresh=request(q,'a2-initial-expiry-key',body=budget_body(q,2));q['clock'].advance(minutes=6)
        expect_denial(lambda:issue(q,fresh),IdentityError,'STEP_UP_EXPIRED',403)
        step('group3_new_session')
        other=q['identity'].local_login(login_id=q['login'],password=q['password'],platform=DevicePlatform.WEB,trace_id=q['ctx'].trace_id,policy_version=q['ctx'].policy_version)
        expect_denial(lambda:issue(q,{**r,'access_token':other.access_token}),RoutingSourceError,'BUDGET_CURRENT_ACCESS_DENIED',403)
        step('group3_current_session_revoked')
        with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='revoked' WHERE session_id=?",(q['ctx'].session_id,))
        try:
            q['adapter'].current(q['ctx'],access_token=r['access_token'])
        except IdentityError as error:require(error.code=='SESSION_REVOKED' and error.http_status==401,'A2_SESSION_DENIAL_INVALID')
        else:raise ValueError('A2_SESSION_DENIAL_MISSING')
        with q['repo'].transaction() as conn:conn.execute("UPDATE sessions SET state='active' WHERE session_id=?",(q['ctx'].session_id,))
        step('group3_current_role')
        with q['auth_repo'].transaction() as conn:conn.execute("UPDATE auth_tenant_roles SET state='inactive'")
        expect_denial(lambda:issue(q,r),AuthorizationError,'ACTION_DENIED',403)
    def group4():
        q=fixture('revoke');step('group4_issue');r=request(q,'a2-revoke-source-key');v=issue(q,r)
        with limited(q) as conn:
            columns=[d.name for d in conn.execute("SELECT * FROM routing_budget_periods WHERE record_kind='period'").description]
            row=conn.execute("SELECT * FROM routing_budget_periods WHERE record_kind='period'").fetchone();original=dict(zip(columns,row,strict=True))
        from copy import deepcopy
        base=deepcopy(original)
        base.update(period_id='qa-invalid-period',source_id='qa-invalid-source',starts_at=now+timedelta(days=30),expires_at=now+timedelta(days=31))
        base['payload'].update(period_id=base['period_id'],source_id=base['source_id'])
        base['payload']['budget'].update(starts_at=base['starts_at'].isoformat(),expires_at=base['expires_at'].isoformat())
        base['canonical_text']=canonical(base['payload']);base['content_digest']=digest(base['payload'])
        def insert_period(conn,value):
            data=deepcopy(value);data['payload']=Jsonb(data['payload']);names=list(data)
            return conn.execute(sql.SQL('INSERT INTO routing_budget_periods ({}) VALUES ({})').format(sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),[data[k] for k in names])
        # 정상 scalar/payload fixture가 실제 DB에서 유효한지 rollback-only 거래로 먼저 확인한다.
        step('group4_direct_period_control')
        with limited(q) as conn:
            with conn.transaction(force_rollback=True):insert_period(conn,base)
        for index,change in enumerate((dict(content_digest='0'*64),dict(period_limit=None),dict(period_limit='1.0000001'),dict(canonical_text='{}'),dict(created_by='not-current-actor'))):
            step('group4_period_negative_'+str(index))
            def call(change=change):
                with limited(q) as conn:insert_period(conn,{**base,**change})
            sql_denial(call,{'42501'} if 'created_by' in change else {'23514'})
        step('group4_payload_null')
        bad=deepcopy(base);bad['payload']['budget']['period_limit']=None
        bad['canonical_text']=canonical(bad['payload']);bad['content_digest']=digest(bad['payload'])
        def null_payload():
            with limited(q) as conn:insert_period(conn,bad)
        sql_denial(null_payload,{'23514'})
        for index,(amount,run) in enumerate((('11','2'),('7','8'),('7','0.0000001'))):
            step('group4_allocation_negative_'+str(index))
            def allocation_negative(amount=amount,run=run):
                with limited(q) as conn:
                    insert_period(conn,base)
                    from daon_user_api.routing_sources_postgres import _identifier
                    scope_id=_identifier('budget-scope',q['ctx'].tenant_id,base['period_id'],q['ctx'].workspace_id)
                    conn.execute('INSERT INTO routing_budget_allocations(tenant_id,anchor_workspace_id,period_id,budget_scope_id,period_limit,run_limit) VALUES(%s,%s,%s,%s,%s,%s)',(q['ctx'].tenant_id,q['ctx'].workspace_id,base['period_id'],scope_id,amount,run))
            sql_denial(allocation_negative,{'23514'})
        step('group4_minor_unit_service')
        expect_denial(lambda:parse_budget_input({**budget_body(q),'currency':'JPY','period_limit':'0.1'},registry=registry),RoutingSourceError,'BUDGET_INPUT_INVALID',422)
        step('group4_root_allocation')
        with limited(q) as conn:root=conn.execute("SELECT period_id FROM routing_budget_periods WHERE record_kind='anchor'").fetchone()[0]
        rejected(q,'INSERT INTO routing_budget_allocations(tenant_id,anchor_workspace_id,period_id,budget_scope_id,period_limit,run_limit) VALUES(%s,%s,%s,%s,0,0)',(q['ctx'].tenant_id,q['ctx'].workspace_id,root,'invalid-root-scope'),states={'P0002'})
        for index,parent_id in enumerate((root,'period-from-another-scope')):
            step('group4_revocation_fk_'+str(index))
            value=dict(tenant_id=q['ctx'].tenant_id,anchor_workspace_id=q['ctx'].workspace_id,period_id=parent_id,version=2,actor_id=q['ctx'].actor_id,revoked_at=now.isoformat(),reason_code='ADMIN_REVOKED')
            rejected(q,'INSERT INTO routing_budget_revocations(tenant_id,anchor_workspace_id,period_id,created_by,revoked_at,reason_code,canonical_text,payload,content_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(q['ctx'].tenant_id,q['ctx'].workspace_id,parent_id,q['ctx'].actor_id,now,'ADMIN_REVOKED',canonical(value),Jsonb(value),digest(value)),states={'23503'},cap='routing_budget.revoke')
        step('group4_revoke')
        rv=request(q,'a2-revoke-period-key',body=dict(period_id=v.source.period_id,reason_code='ADMIN_REVOKED'),operation='routing_budget.revoke',conditions=BudgetPreconditions(1,if_match=v.etag))
        revoked=q['service'].revoke_budget(**{k:value for k,value in rv.items() if k!='binding'})
        require(revoked.source.revoked and issue(q,r).source.revoked,'A2_REVOKED_REPLAY_INVALID')
        with q['store']._transaction(_cloud(q['ctx'],'routing_budget.upper')) as conn:
            expect_denial(lambda:q['repository'].upper_budget(conn,q['ctx']),RoutingSourceError,'BUDGET_SOURCE_UNAVAILABLE',503)
        overlap=request(q,'a2-reissue-overlap-key',body=r['body'])
        expect_denial(lambda:issue(q,overlap),RoutingSourceError,'BUDGET_PERIOD_OVERLAP',409)
    def group5():
        q=fixture('isolation');r=request(q,'a2-isolation-key');v=issue(q,r)
        step('group5_rls')
        for scope in (dict(tenant_id='different-tenant'),dict(workspace_id='different-workspace'),dict(capability=''),dict(capability='questions.read')):
            with limited(q,**scope) as conn:require(conn.execute('SELECT count(*) FROM routing_budget_periods').fetchone()==(0,),'A2_RLS_READ_LEAK')
        step('group5_cross_scope_insert')
        root_value=dict(schema_version=1,record_kind='anchor',tenant_id=q['ctx'].tenant_id,anchor_workspace_id=q['ctx'].workspace_id,period_id='forged-anchor')
        rejected(q,"INSERT INTO routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind,anchor_record_id,created_by,issued_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,'anchor',%s,%s,%s,%s,%s,%s)",(q['ctx'].tenant_id,q['ctx'].workspace_id,'forged-anchor','forged-anchor',q['ctx'].actor_id,now,canonical(root_value),Jsonb(root_value),digest(root_value)),states={'42501'},tenant_id='different-tenant')
        step('group5_actor_repository')
        from daon_user_api.open_law_source_access import OpenLawAccessError
        try:q['repository'].get(replace(q['ctx'],actor_id='not-a-member'),v.source.period_id)
        except OpenLawAccessError as error:require(error.args==('OPEN_LAW_ACCESS_DENIED',),'A2_UNEXPECTED_CURRENT_DENIAL')
        else:raise ValueError('A2_ACTOR_DENIAL_MISSING')
        step('group5_current_pg_session')
        with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_sessions SET state='revoked' WHERE session_id=%s",(q['ctx'].session_id,))
        try:expect_denial(lambda:q['repository'].get(q['ctx'],v.source.period_id),RoutingSourceError,'BUDGET_CURRENT_ACCESS_DENIED',403)
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_sessions SET state='active' WHERE session_id=%s",(q['ctx'].session_id,))
        for verb in ('UPDATE routing_budget_periods SET created_by=created_by','DELETE FROM routing_budget_periods','TRUNCATE routing_budget_periods'):
            step('group5_immutable_privilege');rejected(q,verb,states={'42501','55000'})
        step('group5_current_workflow')
        with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_org_tenant_memberships SET state='suspended' WHERE tenant_id=%s",(q['ctx'].tenant_id,))
        try:
            expect_denial(lambda:q['repository'].get(q['ctx'],v.source.period_id),OrganizationWorkflowError,'ORGANIZATION_MEMBERSHIP_SUSPENDED',403)
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_org_tenant_memberships SET state='active' WHERE tenant_id=%s",(q['ctx'].tenant_id,))
        step('group5_row_lock_revocation')
        entered=Event();finished=Event()
        def revoke_role():
            with connect(secrets['pg_password']) as conn:
                entered.set();conn.execute("UPDATE identity_auth_tenant_roles SET state='inactive' WHERE tenant_id=%s",(q['ctx'].tenant_id,))
            finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with limited(q,'routing_budget.read') as conn:
                _authorize(conn,q['ctx'],now=q['clock']())  # 실제 FOR SHARE admission 잠금
                future=pool.submit(revoke_role);require(entered.wait(2),'A2_LOCK_THREAD_NOT_ENTERED')
                # 동일 backend에서 NOWAIT는 실제 held row lock과 충돌해야 한다.
                def locked():
                    with connect(secrets['pg_password']) as c:c.execute('SELECT user_id FROM identity_auth_tenant_roles WHERE tenant_id=%s FOR UPDATE NOWAIT',(q['ctx'].tenant_id,))
                sql_denial(locked,{'55P03'});require(not finished.is_set(),'A2_REVOKE_BYPASSED_LOCK')
            future.result(timeout=10)
        require(finished.is_set(),'A2_REVOKE_NOT_COMPLETED')
        from daon_user_api.open_law_source_access import OpenLawAccessError
        try:q['repository'].get(q['ctx'],v.source.period_id)
        except OpenLawAccessError as error:require(error.args==('OPEN_LAW_ACCESS_DENIED',),'A2_UNEXPECTED_CURRENT_DENIAL')
        else:raise ValueError('A2_CURRENT_ROLE_DENIAL_MISSING')
    def group6():
        q=fixture('race');step('group6_overlap_race')
        r1=request(q,'a2-race-first-key');r2=request(q,'a2-race-second-key',body=r1['body']);barrier=Barrier(2)
        def execute(r):
            barrier.wait(timeout=5)
            try:return ('ok',issue(q,r))
            except RoutingSourceError as error:return ('denied',error.code)
        start=monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fs=[pool.submit(execute,r) for r in (r1,r2)];results=[f.result(timeout=20) for f in fs]
        require(monotonic()-start<25 and [x[0] for x in results].count('ok')==1 and ('denied','BUDGET_PERIOD_OVERLAP') in results,'A2_RACE_RESULT_INVALID')
        require(business(q)[:4]==(2,1,0,1),'A2_RACE_DUPLICATE')
        winner=r1 if results[0][0]=='ok' else r2;winner_view=issue(q,winner);require(business(q)[:4]==(2,1,0,1),'A2_RACE_REPLAY_DUPLICATE')
        step('group6_same_key_race');barrier=Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            fs=[pool.submit(execute,winner),pool.submit(execute,winner)]
            repeated=[f.result(timeout=20) for f in fs]
        require(all(result==('ok',winner_view) for result in repeated) and business(q)[:4]==(2,1,0,1),'A2_SAME_KEY_RACE_DUPLICATE')
        other=fixture('independent');other_request=request(other,'a2-independent-key')
        step('group6_other_org_while_locked')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with limited(q) as locked_conn:
                locked_conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('routing-budget|'+q['ctx'].tenant_id,))
                with limited(q) as check_conn:
                    require(check_conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',('routing-budget|'+q['ctx'].tenant_id,)).fetchone()==(False,),'A2_ORG_LOCK_NOT_HELD')
                other_future=pool.submit(issue,other,other_request)
                independent_view=other_future.result(timeout=20)
                require(other_future.done(),'A2_OTHER_ORG_BLOCKED')
                require(independent_view.source.tenant_id==other['ctx'].tenant_id and business(other)[:4]==(2,1,0,1),'A2_OTHER_ORG_RESULT_INVALID')
        # 지원되는 Workspace 생성이 아닌 합성 현재상태 교체 음성이다. 옛 root는 유지한다.
        step('group6_replacement_anchor')
        ctx=q['ctx'];new_ws='workspace-a2-replacement'
        with connect(secrets['pg_password']) as conn:
            conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)',(ctx.tenant_id,new_ws,'Synthetic invalid anchor replacement'))
            conn.execute("UPDATE identity_auth_workspaces SET workspace_kind='personal' WHERE tenant_id=%s",(ctx.tenant_id,))
            conn.execute('INSERT INTO identity_auth_workspaces(workspace_id,tenant_id,workspace_kind,data_area,cost_limit_cents,acl_version,version,updated_at) SELECT %s,tenant_id,workspace_kind,data_area,cost_limit_cents,acl_version,version,updated_at FROM identity_auth_workspaces WHERE tenant_id=%s',(new_ws,ctx.tenant_id))
            conn.execute("UPDATE identity_auth_workspaces SET workspace_kind='organization' WHERE tenant_id=%s AND workspace_id=%s",(ctx.tenant_id,new_ws))
        from daon_user_api.routing_sources_postgres import _identifier
        root=_identifier('budget-anchor',ctx.tenant_id);value=dict(schema_version=1,record_kind='anchor',tenant_id=ctx.tenant_id,anchor_workspace_id=new_ws,period_id=root)
        rejected(q,"INSERT INTO routing_budget_periods(tenant_id,anchor_workspace_id,period_id,record_kind,anchor_record_id,created_by,issued_at,canonical_text,payload,content_digest) VALUES(%s,%s,%s,'anchor',%s,%s,%s,%s,%s,%s)",(ctx.tenant_id,new_ws,root,root,ctx.actor_id,now,canonical(value),Jsonb(value),digest(value)),states={'23505'},workspace_id=new_ws)
    failures=[]
    try:
        for index,group in enumerate((group1,group2,group3,group4,group5,group6),1):
            name='group'+str(index);emit(name,status='RUNNING')
            try:group();emit(name,status='PASS')
            except Exception as error:
                failed_test=CURRENT_TEST;failures.append(name)
                code=str(error) if type(error) is ValueError and re.fullmatch('A2_[A-Z0-9_]+',str(error)) else 'A2_GATE_FAILED'
                emit(name,status='FAIL',test_name=failed_test,exception_type=safe_exception_type(error),safe_code=code,**safe_error_metadata(error))
        require(not failures,'A2_GROUPS_FAILED')
        emit('complete',status='PASS',groups=6,model_calls=0,identity_fixture='isolated-synthetic-sqlite-mirrored-whitelist')
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
    require(stage in {'bootstrap','probe'},'A2_STAGE_INVALID')
    secrets=credentials()
    if stage=='bootstrap':bootstrap(secrets)
    else:run_probe(secrets)

if __name__=='__main__':
    try:main()
    except Exception as error:
        code=str(error) if type(error) is ValueError and re.fullmatch('A2_[A-Z0-9_]+',str(error)) else 'A2_GATE_FAILED'
        emit('failed',test_name=CURRENT_TEST,status='FAIL',exception_type=safe_exception_type(error),safe_code=code,**safe_error_metadata(error))
        raise SystemExit(1) from None
