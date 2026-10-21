"""B3 전용 격리 PostgreSQL gate. 일반 DB/DSN 입력을 받지 않는다.
실행 책임은 main이며 사전검사 단위시험은 Docker/SQL을 실행하지 않는다.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path

ISSUE='R3-B3-PG-20261021'
DB='qa_routing_b3_20261021'
APP='qa_routing_b3_app_20261021'
PG='daon-user-routing-b3-pg-20261021'
RUNNER='daon-user-routing-b3-runner-20261021'
TAG='daon-user-routing-b3-qa:20261021'
PG_IMAGE='sha256:75f6767185020459c7e2c3f88fb66f1bd2d9790c435bc91512497146c8bf8d7e'
BASE='python:3.14.3-slim@sha256:69f1ba39d930d9510073db2edf85af17f1552a5591b0d56c9e44524532271cae'
HASHES={'0074_routing_policy.sql':'03568962142b86c611bf0a4811540b75b7b645373af0cafdbb7672f433a196fe',
 '0075_routing_execution.sql':'5b8e8dc3e3426081c488177db5eaaa55d209a051eb0288ddecaa7c092d2c5e5d'}

def require(ok,code):
    if not ok:raise ValueError(code)

def read_approved_root(wrapper):
    """운영 wrapper의 단일 literal 정본만 읽는다. shell/env/CLI 평가는 없다."""
    wrapper=Path(wrapper)
    try:
        for entry in (wrapper,*wrapper.parents):
            require(not entry.is_symlink() and not entry.is_junction(),'B3_WRAPPER_LINK_FORBIDDEN')
        before=wrapper.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size<=65536,'B3_WRAPPER_FILE_INVALID')
        with wrapper.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            require((before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino),'B3_WRAPPER_FILE_CHANGED')
            raw=stream.read(65537)
            after=os.fstat(stream.fileno())
        require(len(raw)<=65536 and (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'B3_WRAPPER_FILE_CHANGED')
        text=raw.decode('utf-8')
    except (OSError,UnicodeError) as error:
        raise ValueError('B3_WRAPPER_READ_FAILED') from None
    assignments=[line for line in text.splitlines() if not line.lstrip().startswith('#') and re.search(r'\bROOT\s*=',line)]
    require(len(assignments)==1,'B3_WRAPPER_ROOT_AMBIGUOUS')
    match=re.fullmatch(r'readonly ROOT=(/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)',assignments[0])
    require(match is not None,'B3_WRAPPER_ROOT_NOT_LITERAL')
    root=match.group(1)
    require(root.rsplit('/',1)[-1]=='daon-user-routing-b3-qa-'+ISSUE.rsplit('-',1)[-1],'B3_WRAPPER_NAMESPACE_INVALID')
    return root

ROOT=read_approved_root(Path(__file__).absolute().parent.parent/'run-routing-b3-postgres-gate.sh')

def validate_target(config):
    expected=dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT)
    require(set(config)==set(expected)|{'pg_id'},'B3_TARGET_FIELDS_INVALID')
    require(all(config[k]==v for k,v in expected.items()),'B3_TARGET_NOT_APPROVED')
    require(type(config['pg_id']) is str and re.fullmatch('[0-9a-f]{64}',config['pg_id']),'B3_CONTAINER_ID_INVALID')
    return True

def verify_sql(root):
    root=Path(root)
    for name,sha in HASHES.items():
        require(hashlib.sha256((root/'services/api/migrations/versions'/name).read_bytes()).hexdigest()==sha,'B3_SQL_HASH_MISMATCH')

def verify_container(value,*,kind,revision,pg_id=None,qa_image=None):
    require(kind in {'pg','runner'},'B3_CONTAINER_KIND_INVALID')
    require(type(value.get('Id')) is str and re.fullmatch('[0-9a-f]{64}',value['Id']),'B3_CONTAINER_ID_INVALID')
    if kind=='pg':validate_target(dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT,pg_id=value['Id']))
    config=value['Config'];host=value['HostConfig'];labels=config.get('Labels') or {}
    require(value['Name']=='/'+(PG if kind=='pg' else RUNNER),'B3_CONTAINER_NAME_INVALID')
    require(labels.get('io.daon.qa.issue')==ISSUE and labels.get('io.daon.qa.revision')==revision,'B3_RESOURCE_NOT_OWNED')
    require(host.get('NetworkMode')==('none' if kind=='pg' else 'container:'+pg_id),'B3_NETWORK_NOT_ISOLATED')
    require(not host.get('PortBindings') and not host.get('Binds') and not host.get('Privileged'),'B3_HOST_ACCESS_FORBIDDEN')
    require(host.get('Memory')==(1073741824 if kind=='pg' else 536870912) and host.get('NanoCpus')==1000000000,'B3_RESOURCE_LIMIT_INVALID')
    require(not host.get('Devices') and not host.get('VolumesFrom') and host.get('PidMode','') in {'','private'} and host.get('IpcMode','private') in {'','private'},'B3_HOST_ACCESS_FORBIDDEN')
    require(value['Image']==(PG_IMAGE if kind=='pg' else qa_image),'B3_IMAGE_MISMATCH')
    tmpfs=host.get('Tmpfs') or {};expected='/var/lib/postgresql/data' if kind=='pg' else '/tmp'
    require(tmpfs=={expected:'rw,noexec,nosuid,size='+('536870912' if kind=='pg' else '134217728')},'B3_TMPFS_INVALID')
    require(all(m.get('Type')=='tmpfs' and m.get('Destination')==expected for m in value.get('Mounts',[])),'B3_MOUNT_FORBIDDEN')
    if kind=='runner':
        require(host.get('ReadonlyRootfs') is True,'B3_RUNNER_WRITABLE_ROOT')
        require(config.get('User')=='65534:65534','B3_RUNNER_USER_INVALID')
    return value['Id']

CURRENT_TEST='preflight'

def expect_denial(call,expected_type,code,status):
    try:call()
    except expected_type as error:
        require(getattr(error,'code',None)==code and getattr(error,'status',None)==status,'B3_UNEXPECTED_DENIAL')
        return
    raise ValueError('B3_EXPECTED_DENIAL_MISSING')

def emit(stage,**values):
    global CURRENT_TEST
    allowed={'status','schema','sql_hashes','test_name','safe_code','sqlstate','column','permission_type','permission_name','groups','model_calls','identity_fixture'}
    require(set(values)<=allowed,'B3_OUTPUT_FIELDS_INVALID')
    require(type(stage) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}',stage),'B3_OUTPUT_FIELDS_INVALID')
    require(values.get('status') in {'RUNNING','PASS','FAIL'},'B3_OUTPUT_FIELDS_INVALID')
    for key,value in values.items():
        if key=='status':continue
        if key=='sql_hashes':require(value==HASHES,'B3_OUTPUT_FIELDS_INVALID')
        elif key=='schema':require(value=='baseline0072+exactSQL0074/0075','B3_OUTPUT_FIELDS_INVALID')
        elif key=='groups':require(type(value) is int and 0<=value<=6,'B3_OUTPUT_FIELDS_INVALID')
        elif key=='model_calls':require(type(value) is int and value==0,'B3_OUTPUT_FIELDS_INVALID')
        elif key=='identity_fixture':require(value=='isolated-synthetic-sqlite-mirrored-whitelist','B3_OUTPUT_FIELDS_INVALID')
        elif key=='safe_code':require(type(value) is str and re.fullmatch('B3_[A-Z0-9_]+',value),'B3_OUTPUT_FIELDS_INVALID')
        elif value is not None:require(type(value) is str and re.fullmatch('[A-Za-z0-9_]{1,128}',value),'B3_OUTPUT_FIELDS_INVALID')
    CURRENT_TEST=stage
    print(json.dumps(dict(issue_id=ISSUE,stage=stage,**values),sort_keys=True),flush=True)


def credentials():
    value=json.load(sys.stdin)
    require(set(value)=={'pg_password','app_password'},'B3_CREDENTIAL_FIELDS_INVALID')
    for secret in value.values():require(type(secret) is str and re.fullmatch('[0-9a-f]{64}',secret),'B3_CREDENTIAL_SHAPE_INVALID')
    return value

def connect(secret,*,app=False,database=DB):
    import psycopg
    return psycopg.connect(host='127.0.0.1',port=5432,dbname=database,user=APP if app else 'postgres',
        password=secret,connect_timeout=5,options='-c statement_timeout=15000 -c lock_timeout=8000')

def bootstrap(secrets):
    from psycopg import sql
    root=Path('/qa');verify_sql(root)
    require(sys.version_info[:3]==(3,14,3),'B3_PYTHON_VERSION_INVALID')
    with connect(secrets['pg_password'],database='postgres') as conn:
        require(conn.execute('SELECT current_user').fetchone()==('postgres',),'B3_ADMIN_ROLE_INVALID')
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
        require(result.returncode==0,'B3_BASELINE_PHASE_'+target+'_FAILED')
    with connect(secrets['pg_password']) as conn:
        conn.execute('SET LOCAL ROLE daon_app')
        require(conn.execute("SELECT current_user,has_schema_privilege(current_user,'public','CREATE')").fetchone()==('daon_app',True),'B3_APP_SCHEMA_PRECONDITION')
    emit('baseline_app_0040',status='RUNNING');migrate('0040',app=True);emit('baseline_app_0040',status='PASS')
    with connect(secrets['pg_password']) as conn:
        row=conn.execute("SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='organization_license_versions'::regclass").fetchone()
        require(row==('daon_app',True,True),'B3_LICENSE_OWNER_PRECONDITION')
        for signature in ('transition_canon_state(text,text,integer,text,text,text,text,text)','claim_document_processing_job(text,integer)'):
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==('daon_app',),'B3_FUNCTION_OWNER_PRECONDITION')
    emit('baseline_admin_0072',status='RUNNING');migrate('0072');emit('baseline_admin_0072',status='PASS')
    with connect(secrets['pg_password']) as conn:
        require(conn.execute('SELECT version_num FROM alembic_version').fetchall()==[('0072',)],'B3_REVISION_INVALID')
        for signature,owner in [('transition_canon_state(text,text,integer,text,text,text,text,text)','daon_notebook_delete'),('claim_document_processing_job(text,integer)','postgres')]:
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==(owner,),'B3_FUNCTION_OWNER_POSTCONDITION')
        require(conn.execute("SELECT to_regclass('operations_run_cost_events'),to_regclass('operations_host_capacity_samples')").fetchone()==(None,None),'B3_0073_FORBIDDEN')
        for name in HASHES:conn.execute((root/'services/api/migrations/versions'/name).read_text())
        conn.execute('REVOKE CREATE ON SCHEMA public FROM daon_app')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {} IN ROLE daon_app').format(sql.Identifier(APP),sql.Literal(secrets['app_password'])))
    with connect(secrets['app_password'],app=True) as conn:
        row=conn.execute("SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,has_schema_privilege(current_user,'public','CREATE'),pg_has_role(current_user,'daon_notebook_delete','MEMBER') FROM pg_roles WHERE rolname=current_user").fetchone()
        require(row==(APP,APP,False,False,False,False,False,False,False),'B3_RESTRICTED_ROLE_INVALID')
    emit('bootstrap',status='PASS',schema='baseline0072+exactSQL0074/0075',sql_hashes=HASHES)

def run_probe(secrets):
    """완전 합성 QA. SQLite 인증과 PG 현재자료 검사는 별도 실제 저장소다."""
    import asyncio,sqlite3,tempfile,secrets as random
    from contextlib import contextmanager
    from datetime import datetime,timedelta,timezone
    from dataclasses import replace
    from types import SimpleNamespace
    from psycopg import sql,Error
    from psycopg.types.json import Jsonb
    from sqlalchemy.engine import URL
    import httpx
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.authorization import AuthorizationService,SqliteAuthorizationRepository,Role
    from daon_user_api.identity import ClientKind,DevicePlatform
    from daon_user_api.question_answering import TextModelSelection,GroundedTextResult
    from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository as Questions,QuestionContext
    from daon_user_api.routing_policy import VerifiedCandidate,VerifiedObservation,RouteRequestSnapshot,build_route_plan,digest
    from daon_user_api.routing_policy_postgres import postgres_routing_policy_service
    from daon_user_api.routing_execution import RoutingExecutionService,PlanApproval,AttemptOutcome,RoutingExecutionError,_ScopedQuestionPort,FrozenAuthorizationPlan,SuppressedResult
    from daon_user_api.routing_attempts_postgres import PostgresRoutingAttemptsRepository
    from daon_user_api.data_canon import canonical_json_bytes
    from daon_user_api.runtime import RuntimeSettings,RuntimeDependencies,create_app,WEB_SESSION_COOKIE
    # 정본 Git에 포함된 순수 합성 인증 fixture만 이용한다. 외부 OIDC/모델 호출은 없다.
    sys.path.insert(0,'/qa/services/api/tests')
    from test_identity_support import create_service,FakeVerifiedOidcProvider,MutableClock
    now=datetime.now(timezone.utc);T,W,N='tenant-b3-qa','workspace-b3-qa','notebook-b3-qa'
    temp=tempfile.TemporaryDirectory(prefix='b3-identity-',dir='/tmp')
    identity_path=Path(temp.name)/'identity.sqlite3'
    identity,identity_repository,audit,clock=create_service(identity_path,clock=MutableClock(now))
    def login(subject=None):
        provider=FakeVerifiedOidcProvider(subject=subject or 'qa-'+random.token_hex(12),authorization_code=random.token_urlsafe(32))
        start=identity.begin_oidc_login(issuer='https://login.example.com',client_id='daon-web',audience='daon-user-api',
            redirect_uri='https://app.example.com/auth/callback',client_kind=ClientKind.WEB,tenant_id=T,
            trace_id='trace-b3-qa',policy_version='policy-b3-qa')
        provider.expected_nonce=start.nonce
        return identity.complete_oidc_login(state=start.state,authorization_code=provider.authorization_code,
            code_verifier=start.code_verifier,client_id='daon-web',redirect_uri='https://app.example.com/auth/callback',
            provider=provider,platform=DevicePlatform.WEB,trace_id='trace-b3-qa',policy_version='policy-b3-qa')
    owner_subject='qa-'+random.token_hex(12)
    owner=login(owner_subject);other=login();A=owner.user_id
    auth_repository=SqliteAuthorizationRepository(identity_path)
    auth_repository.bootstrap_workspace(tenant_id=T,workspace_id=W,owner_user_id=A,owner_role=Role.ORGANIZATION_ADMIN,
        workspace_kind='organization',data_area='cloud_sync',cost_limit_cents=1000,now=now)
    with auth_repository.transaction() as conn:
        conn.execute("INSERT INTO auth_memberships VALUES(?,?,?,'editor','active',1,?)",(T,W,other.user_id,now.isoformat()))
    authorization=AuthorizationService(repository=auth_repository,audit_store=audit,clock=clock,identity_service=identity)
    ctx=QuestionContext(T,W,A,'trace-b3-qa','policy-b3-qa',N,owner.session_id)
    # 합성 SQLite 생성행을 제한된 identity/auth 테이블에만 이식한다.
    identity_tables=('users','memberships','devices','sessions','auth_workspaces','auth_tenant_roles','auth_memberships',
        'auth_tenant_policies','auth_tenant_permission_rules','auth_workspace_policies','auth_workspace_permission_rules','auth_source_access')
    with connect(secrets['pg_password']) as conn,sqlite3.connect(identity_path) as local:
        require(conn.execute('SELECT version_num FROM alembic_version').fetchall()==[('0072',)],'B3_REVISION_INVALID')
        conn.execute('INSERT INTO tenants(tenant_id,display_name) VALUES(%s,%s)',(T,'Synthetic B3 QA'))
        conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)',(T,W,'Synthetic B3 QA'))
        for actor in (A,other.user_id):conn.execute('INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)',(T,actor))
        for table in identity_tables:
            target='identity_'+table
            columns=conn.execute('SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=\'public\' AND table_name=%s ORDER BY ordinal_position',(target,)).fetchall()
            require(bool(columns),'B3_IDENTITY_TABLE_MISSING')
            source_columns={r[1] for r in local.execute('PRAGMA table_info('+table+')')}
            names=[name for name,_ in columns if name in source_columns]
            for row in local.execute('SELECT '+','.join(names)+' FROM '+table):
                values=[bool(v) if dict(columns)[name]=='boolean' and v is not None else v for name,v in zip(names,row,strict=True)]
                conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(target),sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),values)
        conn.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,%s)',(T,W,N,A,now))
    dsn=URL.create('postgresql',username=APP,password=secrets['app_password'],host='127.0.0.1',port=5432,database=DB).render_as_string(hide_password=False)
    class ObservedStore(PostgresCloudStore):
        def __init__(self,value):super().__init__(value);self.observe=False;self.statements=[]
        @contextmanager
        def _transaction(self,context):
            with super()._transaction(context) as conn:
                if not self.observe:yield conn;return
                outer=self
                class ReadTrace:
                    def execute(self,statement,params=()):
                        text=statement if type(statement) is str else statement.as_string(conn)
                        outer.statements.append(text.lstrip().split(None,1)[0].upper())
                        return conn.execute(statement,params)
                    def __getattr__(self,key):return getattr(conn,key)
                yield ReadTrace()
    store=ObservedStore(dsn);repo=PostgresRoutingAttemptsRepository(store)
    source=SimpleNamespace(origin='raw_source',context_item_id='source-b3-qa',source_id='source-b3-qa',source_version_id='source-version-b3-qa',digest_sha256=None,knowledge_registration_id=None)
    def seed_source(item):
        with store._transaction(CloudAccessContext(T,W,A,'question.execute')) as conn:
            Questions._insert_canon(conn,ctx,'sources',item.source_id,{'filename':'synthetic-b3.txt'},state='registered')
            Questions._insert_canon(conn,ctx,'source_versions',item.source_version_id,{'origin_kind':'raw_source'},extra_columns=('source_id',),extra_values=(item.source_id,))
            conn.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,%s)",(T,W,N,item.source_id,item.source_version_id,A,now))
    seed_source(source)
    candidate=VerifiedCandidate('qa-connection','qa-model','qa-deployment',1,1,0,1,'workspace',None,upper_budget_reference='d'*64)
    body=dict(schema_version=1,expected_version=0,enabled=True,capability='text_generation',candidates=[{k:v for k,v in candidate.references().items() if k not in {'deployment_id','upper_budget_reference'}}],ranking_profile=dict(version='qa-rank',evaluation_set='qa-eval',minimum_quality=7000,observation_window='qa-window',input_class='short',minimum_samples=1,max_age_seconds=3600,tier_mapping_version='qa-tier',locality_mapping_version='qa-locality'),fallback_policy=dict(mode='transient_only'),budget=dict(currency='USD',run_limit='1.00',max_attempts=1))
    policy=dict(body=body,version=1,policy_version_id='qa-policy-v1',digest_sha256=digest(body))
    with store._transaction(CloudAccessContext(T,W,A,'routing_policy.put')) as conn:
        Questions._insert_canon(conn,ctx,'routing_policy_versions','qa-policy-v1',body)
        conn.execute('INSERT INTO workspace_routing_policy_current VALUES(%s,%s,%s)',(T,W,'qa-policy-v1'))
    def source_access(allowed):
        with connect(secrets['pg_password']) as conn:
            conn.execute('INSERT INTO identity_auth_source_access(tenant_id,workspace_id,user_id,source_version_id,allowed,version,updated_at) VALUES(%s,%s,%s,%s,%s,1,%s) ON CONFLICT(tenant_id,workspace_id,user_id,source_version_id) DO UPDATE SET allowed=excluded.allowed,version=identity_auth_source_access.version+1,updated_at=excluded.updated_at',(T,W,A,source.source_version_id,int(allowed),datetime.now(timezone.utc)))
    class Port(_ScopedQuestionPort):
        # 실제 현재 인가/Source/Notebook 검사, parents/materialize/write_result는 상속 그대로다.
        def __init__(self,run,instant,items):
            self.sources=items;self.evidence=();self.general=False
            self.options=dict(question='Synthetic QA question',source_id=items[0].source_id,source_version_id=items[0].source_version_id,context_mode='raw_only')
            self.owner=SimpleNamespace(clock=lambda:instant);self.run=run;self.calls=0;self.suppress=False
        def recheck(self,conn,context,plan):
            self._current_access(conn,context)
        def freeze(self,context,item):
            quote=dict(maximum_estimate='0.20',currency='USD',pricing_reference='qa-price',price_version='qa-v1',candidate_reference_digest=digest(item['candidate']),protocol_version='qa-fake-v1',transformed_payload_digest='f'*64,expires_at=(self.owner.clock()+timedelta(minutes=10)).isoformat(),billing_reference=dict(scope_id='qa-billing',provider_code='CUSTOM',account_reference='d'*64))
            return dict(candidate=item['candidate'],provider_kind='server_internal',provider_code='CUSTOM',artifact_digest='sha256:'+'1'*64,license_reference='qa-license',endpoint_digest='e'*64,protocol_version='qa-fake-v1',transformed_payload_digest='f'*64,payload_bytes=10,egress_policy_digest='a'*64,authorization_mode='policy_approved',required_approver='workspace_manager',quote=quote)
        def budget(self,context,route):return dict(scope_id='qa-budget-'+self.run,source_id='qa-budget',version='qa-v1',currency='USD',run_limit='1.00',period_limit='1.00',starts_at=(self.owner.clock()-timedelta(minutes=1)).isoformat(),expires_at=(self.owner.clock()+timedelta(minutes=15)).isoformat(),max_attempts=1)
        @contextmanager
        def open(self,context,plan,entry):
            def invoke():
                self.calls+=1
                if self.suppress:source_access(False)
                return AttemptOutcome('succeeded',result=GroundedTextResult('Synthetic stored answer',(),False,{}))
            yield SimpleNamespace(claims=entry,invoke=invoke)
    def prepare(run,*,past=False,legacy=False,items=(source,)):
        instant=datetime.now(timezone.utc)-timedelta(hours=1) if past else datetime.now(timezone.utc)
        observation=VerifiedObservation(candidate.deployment_id,'qa-observation','qa-v1',instant-timedelta(seconds=1),instant+timedelta(minutes=15),'qa-eval','qa-window','short',10,'qa-tier','qa-locality',0,9000,0,9900,100,'0.20','USD','qa-price',100,'sha256:'+'1'*64,'qa-license',True,True,True,True,True,True,digest(candidate.references()),'a'*64,'basis_points_ms_currency_v1')
        route=build_route_plan(ctx,RouteRequestSnapshot(N,'a'*64,'b'*64,'c'*64,'cloud_sync'),policy,(candidate,),(observation,),now=instant)
        port=Port(run,instant,items);repository=PostgresRoutingAttemptsRepository(store,clock=lambda:instant) if past else repo
        engine=RoutingExecutionService(repository,port,clock=lambda:instant)
        # 이전 저장형식은 exclusions가 없던 원형식으로 새 합성 INSERT한다. UPDATE/재해시는 하지 않는다.
        if legacy:
            actual_repository=repository
            class LegacyFixtureRepository:
                def prepare(self,context,plan,**kwargs):
                    value=plan.as_dict();value.pop('exclusions',None)
                    old=FrozenAuthorizationPlan(canonical_json_bytes(value).decode(),digest(value))
                    # 구형 canonical을 fixture 이식한다. 현재 발급 계약 통과 증거로 계산하지 않는다.
                    return actual_repository.prepare(context,old,**kwargs)
            engine=RoutingExecutionService(LegacyFixtureRepository(),port,clock=lambda:instant)
        grant=engine.prepare(ctx,route,'b'*64,run,authorize_plan=lambda p,r:PlanApproval(p.digest_sha256,A,ctx.session_id,'policy_approved'))
        plan=repository.read(ctx,grant.plan_id)
        if legacy:grant=replace(grant,plan_digest=plan.digest_sha256,routing_preview=None)
        return port,engine,grant,plan
    completed={};accepted={};failed={};uncertain={};suppressed={}
    def dispatch(data,outcome):
        port,engine,grant,plan=data
        repo.claim(ctx,plan,idempotency_key='claim-'+grant.run_id,request_digest='b'*64,recheck=port.recheck)
        attempt=repo.claim_attempt(ctx,plan,1,recheck=port.recheck)
        repo.finish(ctx,plan,1,attempt,outcome,final=True,write_result=port.write_result)
        return attempt
    def hidden(context,run):expect_denial(lambda:repo.read_routing(context,run),RoutingExecutionError,'RESOURCE_UNAVAILABLE',404)
    def group1():
        accepted['value']=prepare('qa-b3-accepted')
        require(repo.read_routing(ctx,'qa-b3-accepted')['run_state']=='accepted','B3_ACCEPTED_PROJECTION_INVALID')
        completed['value']=prepare('qa-b3-completed');p,e,g,v=completed['value']
        e.execute(ctx,plan_id=g.plan_id,authorization=g.authorization,request_digest='b'*64,idempotency_key='completed')
        read=repo.read_routing(ctx,g.run_id)
        require(read['run_state']=='completed' and len(read['attempts'])==1 and read['final_selection']['model_id']=='qa-model','B3_COMPLETED_PROJECTION_INVALID')
        failed['value']=prepare('qa-b3-failed');dispatch(failed['value'],AttemptOutcome('failed','TIMEOUT'))
        uncertain['value']=prepare('qa-b3-uncertain');dispatch(uncertain['value'],AttemptOutcome('uncertain','TRANSPORT_UNCERTAIN','uncertain'))
        for box,state in ((failed,'failed'),(uncertain,'uncertain')):
            view=repo.read_routing(ctx,box['value'][2].run_id)
            require(view['run_state']=='failed' and view['attempts'][0]['state']==state and view['attempts'][0]['cost_amount'] is None and view['final_selection'] is None,'B3_FAILED_COST_PROJECTION_INVALID')
        suppressed['value']=prepare('qa-b3-suppressed');p,e,g,v=suppressed['value'];p.suppress=True
        try:
            expect_denial(lambda:e.execute(ctx,plan_id=g.plan_id,authorization=g.authorization,request_digest='b'*64,idempotency_key='suppressed'),RoutingExecutionError,'ROUTING_RESULT_ACCESS_DENIED',403)
        finally:source_access(True)
        require(repo.read_routing(ctx,g.run_id)['run_state']=='failed','B3_SUPPRESSED_PROJECTION_INVALID')
        with connect(secrets['pg_password']) as conn:
            require(conn.execute('SELECT count(*) FROM run_results WHERE run_id=%s',(g.run_id,)).fetchone()==(0,),'B3_SUPPRESSED_RESULT_LEAK')
        expect_denial(lambda:repo.finish(ctx,v,1,'wrong-attempt',AttemptOutcome('failed','AUTH_DENIED'),final=True,write_result=None),RoutingExecutionError,'ROUTING_LINEAGE_INVALID',403)
    async def http_read(token,run,workspace=W):
        response=await client.get('/api/v1/runs/'+run+'/routing-decision',params={'workspace_id':workspace},cookies={WEB_SESSION_COOKIE:token})
        require(response.headers.get('cache-control')=='no-store','B3_HTTP_CACHE_INVALID')
        return response
    def group2():
        run=completed['value'][2].run_id
        require(asyncio.run(http_read(owner.access_token,run)).status_code==200,'B3_HTTP_READ_FAILED')
        for token,r,w in ((owner.access_token,'missing-run',W),(other.access_token,run,W),(owner.access_token,run,'other-workspace')):
            result=asyncio.run(http_read(token,r,w));require(result.status_code==404 and result.json()['error']['code']=='RESOURCE_UNAVAILABLE','B3_HTTP_HIDDEN_NOT_IDENTICAL')
        source_access(False)
        try:require(asyncio.run(http_read(owner.access_token,run)).status_code==404,'B3_SOURCE_REVOCATION_IGNORED')
        finally:source_access(True)
        with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_auth_tenant_roles SET state='inactive' WHERE tenant_id=%s AND user_id=%s",(T,A))
        try:hidden(ctx,run)
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_auth_tenant_roles SET state='active' WHERE tenant_id=%s AND user_id=%s",(T,A))
        with connect(secrets['pg_password']) as conn:conn.execute("DELETE FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s AND binding_kind='source' AND record_id=%s",(T,W,N,source.source_id))
        try:hidden(ctx,run)
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("INSERT INTO notebook_bindings(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES(%s,%s,%s,'source',%s,%s,%s,%s)",(T,W,N,source.source_id,source.source_version_id,A,now))
        item=SimpleNamespace(**{**vars(source),'context_item_id':'source-retention-b3','source_id':'source-retention-b3','source_version_id':'version-retention-b3'})
        seed_source(item);p,e,g,v=prepare('qa-b3-retention',items=(item,))
        with connect(secrets['pg_password']) as conn:
            conn.execute('INSERT INTO deletion_request_locator(tenant_id,request_id,workspace_id,source_id,created_at) VALUES(%s,%s,%s,%s,%s)',(T,'retention-b3',W,item.source_id,now))
            conn.execute("INSERT INTO deletion_requests(tenant_id,workspace_id,request_id,source_id,actor_id,state,version,source_active,purge_started,grace_until,policy_version,idempotency_key,request_fingerprint,trace_id,created_at,updated_at) VALUES(%s,%s,'retention-b3',%s,%s,'requested',1,false,false,%s,%s,'retention-b3',%s,%s,%s,%s)",(T,W,item.source_id,A,now+timedelta(days=1),ctx.policy_version,'a'*64,ctx.trace_id,now,now))
        hidden(ctx,g.run_id)
    def group3():
        p,e,g,v=prepare('qa-b3-legacy',past=True,legacy=True)
        value=v.as_dict()
        # 구형형식은 원본 Frozen route에 exclusions가 없던 fixture로 생성해야 한다.
        require('exclusions' not in value,'B3_LEGACY_SHAPE_NOT_PRODUCED')
        with connect(secrets['pg_password']) as conn:
            original=conn.execute('SELECT payload,content_digest FROM routing_authorization_plans WHERE plan_id=%s',(g.plan_id,)).fetchone()
            require(canonical_json_bytes(original[0]).decode()==v.canonical_text and original[1]==v.digest_sha256,'B3_LEGACY_BYTES_INVALID')
            conn.execute("UPDATE identity_sessions SET state='revoked' WHERE session_id=%s",(owner.session_id,))
        new_identity=login(owner_subject)
        with connect(secrets['pg_password']) as conn,sqlite3.connect(identity_path) as local:
            for table,key,value in [('devices','device_id',new_identity.device_id),('sessions','session_id',new_identity.session_id)]:
                target='identity_'+table
                columns=conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(target,)).fetchall()
                present={row[1] for row in local.execute('PRAGMA table_info('+table+')')}
                names=[name for name,_ in columns if name in present]
                row=local.execute('SELECT '+','.join(names)+' FROM '+table+' WHERE '+key+'=?',(value,)).fetchone()
                require(row is not None,'B3_CURRENT_SESSION_FIXTURE_MISSING')
                conn.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(target),sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names)),row)
        fresh=replace(ctx,session_id=new_identity.session_id)
        p.owner.clock=lambda:datetime.now(timezone.utc)
        try:
            require(repo.read_routing(fresh,g.run_id)['run_state']=='accepted','B3_LEGACY_HISTORY_FAILED')
            expect_denial(lambda:RoutingExecutionService(repo,p).execute(fresh,plan_id=g.plan_id,authorization=g.authorization,request_digest='b'*64,idempotency_key='new-session'),RoutingExecutionError,'ROUTING_AUTHORIZATION_INVALID',403)
            expect_denial(lambda:RoutingExecutionService(repo,p).execute(ctx,plan_id=g.plan_id,authorization=g.authorization,request_digest='b'*64,idempotency_key='revoked-session'),RoutingExecutionError,'ROUTING_SESSION_REQUIRED',401)
            require(p.calls==0,'B3_REVOKED_DISPATCHED')
        finally:
            with connect(secrets['pg_password']) as conn:conn.execute("UPDATE identity_sessions SET state='active' WHERE session_id=%s",(owner.session_id,))
        # 현재 권한은 상속 실제 검사, 만료는 현재 시각의 실제 repository.claim에서 거부한다.
        expect_denial(lambda:RoutingExecutionService(repo,p).execute(ctx,plan_id=g.plan_id,authorization=g.authorization,request_digest='b'*64,idempotency_key='expired-new-claim'),RoutingExecutionError,'ROUTING_AUTHORIZATION_EXPIRED',403)
        require(p.calls==0,'B3_EXPIRED_DISPATCHED')
        with connect(secrets['pg_password']) as conn:require(conn.execute('SELECT payload,content_digest FROM routing_authorization_plans WHERE plan_id=%s',(g.plan_id,)).fetchone()==original,'B3_LEGACY_BYTES_CHANGED')
    def group4():
        p,e,g,v=uncertain['value'];before=repo.read_routing(ctx,g.run_id)
        receipt=AttemptOutcome('uncertain','TRANSPORT_UNCERTAIN','known','0.10','USD','qa-b3-receipt')
        repo.reconcile(ctx,v,1,receipt);repo.reconcile(ctx,v,1,receipt)
        after=repo.read_routing(ctx,g.run_id);a=after['attempts'][0]
        require(before['attempts'][0]['cost_state']=='uncertain' and a['state']=='uncertain' and a['cost_state']=='known' and a['cost_amount']=='0.10' and after['final_selection'] is None,'B3_RECONCILED_PROJECTION_INVALID')
        target=failed['value'][3]
        expect_denial(lambda:repo.reconcile(ctx,target,1,receipt),RoutingExecutionError,'ROUTING_RECEIPT_REUSED',409)
        with connect(secrets['pg_password']) as conn:require(conn.execute("SELECT count(*) FROM routing_attempt_events WHERE plan_id=%s AND event_kind='reconciled'",(g.plan_id,)).fetchone()==(1,),'B3_RECONCILE_DUPLICATED')
    def group5():
        writer=Questions(store,None)
        selected=TextModelSelection('CUSTOM','https://fixture.invalid/v1','qa-pinned-profile','qa-pinned-deployment','qa-pinned-model',1,'server_internal','qa-pinned-connection',0,1)
        result=writer.persist_completed(ctx,run_id='qa-b3-pinned',source_id=source.source_id,source_version_id=source.source_version_id,question='Synthetic pinned QA',selection=selected,evidence=(),result=GroundedTextResult('Synthetic pinned answer',(),False,{}),context_sources=(source,))
        require(result.run_id=='qa-b3-pinned','B3_PINNED_WRITER_INVALID')
        view=repo.read_routing(ctx,result.run_id)
        require(view['routing_mode']=='pinned' and view['run_state']=='completed' and view['final_selection']['model_id']=='qa-pinned-model' and len(view['attempts'])==1,'B3_PINNED_READ_INCOMPATIBLE')
    business=('runs','run_results','model_attempts','routing_decisions','run_snapshots','notebook_bindings','routing_attempt_events','routing_billing_receipts','workspace_routing_policy_current','routing_policy_versions')
    def fingerprint():
        with connect(secrets['pg_password']) as conn:
            return {name:conn.execute(sql.SQL('SELECT md5(coalesce(string_agg(row_to_json(x)::text,\'\' ORDER BY row_to_json(x)::text),\'\')) FROM {} x').format(sql.Identifier(name))).fetchone()[0] for name in business}
    def group6():
        before=fingerprint();store.statements=[];store.observe=True
        try:repo.read_routing(ctx,completed['value'][2].run_id)
        finally:store.observe=False
        require(store.statements and set(store.statements)=={'SELECT'} and fingerprint()==before,'B3_PROJECTION_WROTE_BUSINESS_DATA')
        with store._transaction(CloudAccessContext('other-tenant','other-workspace',other.user_id,'routing_execution.projection')) as conn:
            require(conn.execute('SELECT count(*) FROM runs WHERE tenant_id=%s AND workspace_id=%s',(T,W)).fetchone()==(0,),'B3_RLS_SCOPE_LEAK')
        before=fingerprint();prior=len(audit.list(tenant_id=T,limit=100).items)
        async def cap():
            url='/api/v1/workspaces/'+W+'/routing-policy'
            base=await client.get(url,cookies={WEB_SESSION_COOKIE:owner.access_token})
            opted=await client.get(url+'?include_permissions=1',cookies={WEB_SESSION_COOKIE:owner.access_token})
            require(base.status_code==opted.status_code==200 and base.json()['data']==opted.json()['data'] and 'permissions' not in base.json()['meta'] and opted.json()['meta']['permissions']=={'policy_manage':True},'B3_CAP_OPT_IN_INVALID')
            require(base.headers['etag']==opted.headers['etag'] and opted.headers['cache-control']=='no-store','B3_CAP_CACHE_INVALID')
            read=await client.get(url+'?include_permissions=1',cookies={WEB_SESSION_COOKIE:other.access_token})
            require(read.status_code==200 and read.json()['meta']['permissions']=={'policy_manage':False},'B3_CAP_ROLE_INVALID')
        asyncio.run(cap())
        require(fingerprint()==before and len(audit.list(tenant_id=T,limit=100).items)>prior,'B3_CAP_AUDIT_OR_WRITE_INVALID')
    dependencies=RuntimeDependencies(settings=RuntimeSettings.for_test(database_path=identity_path,policy_version=ctx.policy_version),identity_service=identity,
        identity_repository=identity_repository,authorization_service=authorization,authorization_repository=auth_repository,audit_store=audit,
        cloud_store=store,routing_policy_service=postgres_routing_policy_service(store))
    client=httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(dependencies)),base_url='https://app.example.com')
    failures=[]
    try:
        for index,group in enumerate((group1,group2,group3,group4,group5,group6),1):
            name='group'+str(index);emit(name,status='RUNNING')
            try:group();emit(name,status='PASS')
            except Exception as error:
                failures.append(name)
                emit(name,status='FAIL',safe_code=str(error) if isinstance(error,ValueError) and re.fullmatch('B3_[A-Z0-9_]+',str(error)) else 'B3_GATE_FAILED',**safe_error_metadata(error))
        require(not failures,'B3_GROUPS_FAILED')
        emit('complete',status='PASS',groups=6,model_calls=0,identity_fixture='isolated-synthetic-sqlite-mirrored-whitelist')
    finally:
        asyncio.run(client.aclose());store.close();temp.cleanup()


def safe_error_metadata(error):
    result=dict(sqlstate=None,column=None,permission_type=None,permission_name=None)
    cause=error;seen=set()
    for _ in range(8):
        if cause is None or id(cause) in seen:break
        seen.add(id(cause));diag=getattr(cause,'diag',None)
        state=getattr(cause,'sqlstate',None) or getattr(diag,'sqlstate',None)
        if result['sqlstate'] is None and type(state) is str and re.fullmatch('[A-Z0-9]{5}',state):result['sqlstate']=state
        column=getattr(diag,'column_name',None)
        if result['column'] is None and type(column) is str and re.fullmatch('[a-z_][a-z0-9_]{0,62}',column):result['column']=column
        primary=getattr(diag,'message_primary',None)
        if state=='42501' and type(primary) is str and result['permission_name'] is None:
            matched=re.fullmatch(r'permission denied for (table|relation|sequence|function) ([a-z_][a-z0-9_]{0,62})',primary)
            if matched:result['permission_type'],result['permission_name']=matched.groups()
        cause=getattr(cause,'__cause__',None) or getattr(cause,'__context__',None)
    return result


def main():
    stage=sys.argv[1] if len(sys.argv)==2 else ''
    require(stage in {'bootstrap','probe'},'B3_STAGE_INVALID')
    secrets=credentials()
    if stage=='bootstrap':bootstrap(secrets)
    else:run_probe(secrets)

if __name__=='__main__':
    try:main()
    except Exception as error:
        code=str(error) if type(error) is ValueError and re.fullmatch('B3_[A-Z0-9_]+',str(error)) else 'B3_GATE_FAILED'
        emit('failed',test_name=CURRENT_TEST,status='FAIL',safe_code=code,**safe_error_metadata(error))
        raise SystemExit(1) from None
