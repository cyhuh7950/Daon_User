"""B2 전용 격리 PostgreSQL gate. 일반 DB/DSN 입력을 받지 않는다.
실행 책임은 main이며 사전검사 단위시험은 Docker/SQL을 실행하지 않는다.
"""
from __future__ import annotations
import hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path

ISSUE='R3-B2-ROUTING-20261021'
DB='qa_routing_b2_20261021'
APP='qa_routing_b2_app_20261021'
PG='daon-user-routing-b2-pg-20261021'
RUNNER='daon-user-routing-b2-runner-20261021'
TAG='daon-user-routing-b2-qa:20261021'
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
            require(not entry.is_symlink() and not entry.is_junction(),'B2_WRAPPER_LINK_FORBIDDEN')
        before=wrapper.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size<=65536,'B2_WRAPPER_FILE_INVALID')
        with wrapper.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            require((before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino),'B2_WRAPPER_FILE_CHANGED')
            raw=stream.read(65537)
            after=os.fstat(stream.fileno())
        require(len(raw)<=65536 and (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'B2_WRAPPER_FILE_CHANGED')
        text=raw.decode('utf-8')
    except (OSError,UnicodeError) as error:
        raise ValueError('B2_WRAPPER_READ_FAILED') from None
    assignments=[line for line in text.splitlines() if not line.lstrip().startswith('#') and re.search(r'\bROOT\s*=',line)]
    require(len(assignments)==1,'B2_WRAPPER_ROOT_AMBIGUOUS')
    match=re.fullmatch(r'readonly ROOT=(/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)',assignments[0])
    require(match is not None,'B2_WRAPPER_ROOT_NOT_LITERAL')
    root=match.group(1)
    require(root.rsplit('/',1)[-1]=='daon-user-routing-b2-qa-'+ISSUE.rsplit('-',1)[-1],'B2_WRAPPER_NAMESPACE_INVALID')
    return root

ROOT=read_approved_root(Path(__file__).absolute().parent.parent/'run-routing-b2-postgres-gate.sh')

def validate_target(config):
    expected=dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT)
    require(set(config)==set(expected)|{'pg_id'},'B2_TARGET_FIELDS_INVALID')
    require(all(config[k]==v for k,v in expected.items()),'B2_TARGET_NOT_APPROVED')
    require(type(config['pg_id']) is str and re.fullmatch('[0-9a-f]{64}',config['pg_id']),'B2_CONTAINER_ID_INVALID')
    return True

def verify_sql(root):
    root=Path(root)
    for name,sha in HASHES.items():
        require(hashlib.sha256((root/'services/api/migrations/versions'/name).read_bytes()).hexdigest()==sha,'B2_SQL_HASH_MISMATCH')

def verify_container(value,*,kind,revision,pg_id=None,qa_image=None):
    require(kind in {'pg','runner'},'B2_CONTAINER_KIND_INVALID')
    require(type(value.get('Id')) is str and re.fullmatch('[0-9a-f]{64}',value['Id']),'B2_CONTAINER_ID_INVALID')
    if kind=='pg':validate_target(dict(host='127.0.0.1',database=DB,owner='postgres',app_role=APP,revision='0072',source_root=ROOT,pg_id=value['Id']))
    config=value['Config'];host=value['HostConfig'];labels=config.get('Labels') or {}
    require(value['Name']=='/'+(PG if kind=='pg' else RUNNER),'B2_CONTAINER_NAME_INVALID')
    require(labels.get('io.daon.qa.issue')==ISSUE and labels.get('io.daon.qa.revision')==revision,'B2_RESOURCE_NOT_OWNED')
    require(host.get('NetworkMode')==('none' if kind=='pg' else 'container:'+pg_id),'B2_NETWORK_NOT_ISOLATED')
    require(not host.get('PortBindings') and not host.get('Binds') and not host.get('Privileged'),'B2_HOST_ACCESS_FORBIDDEN')
    require(host.get('Memory')==536870912 and host.get('NanoCpus')==1000000000,'B2_RESOURCE_LIMIT_INVALID')
    require(not host.get('Devices') and not host.get('VolumesFrom') and host.get('PidMode','') in {'','private'} and host.get('IpcMode','private') in {'','private'},'B2_HOST_ACCESS_FORBIDDEN')
    require(value['Image']==(PG_IMAGE if kind=='pg' else qa_image),'B2_IMAGE_MISMATCH')
    tmpfs=host.get('Tmpfs') or {};expected='/var/lib/postgresql/data' if kind=='pg' else '/tmp'
    require(tmpfs=={expected:'rw,noexec,nosuid,size='+('536870912' if kind=='pg' else '134217728')},'B2_TMPFS_INVALID')
    require(all(m.get('Type')=='tmpfs' and m.get('Destination')==expected for m in value.get('Mounts',[])),'B2_MOUNT_FORBIDDEN')
    if kind=='runner':require(host.get('ReadonlyRootfs') is True,'B2_RUNNER_WRITABLE_ROOT')
    return value['Id']

CURRENT_TEST='preflight'

def expect_denial(call,expected_type,code,status):
    try:call()
    except expected_type as error:
        require(getattr(error,'code',None)==code and getattr(error,'status',None)==status,'B2_UNEXPECTED_DENIAL')
        return
    raise ValueError('B2_EXPECTED_DENIAL_MISSING')

def emit(stage,**values):
    global CURRENT_TEST
    CURRENT_TEST=stage
    print(json.dumps(dict(issue_id=ISSUE,stage=stage,**values),sort_keys=True),flush=True)

def credentials():
    value=json.load(sys.stdin)
    require(set(value)=={'pg_password','app_password'},'B2_CREDENTIAL_FIELDS_INVALID')
    for secret in value.values():require(type(secret) is str and re.fullmatch('[0-9a-f]{64}',secret),'B2_CREDENTIAL_SHAPE_INVALID')
    return value

def connect(secret,*,app=False,database=DB):
    import psycopg
    return psycopg.connect(host='127.0.0.1',port=5432,dbname=database,user=APP if app else 'postgres',
        password=secret,connect_timeout=5,options='-c statement_timeout=15000 -c lock_timeout=8000')

def bootstrap(secrets):
    from psycopg import sql
    root=Path('/qa');verify_sql(root)
    require(sys.version_info[:3]==(3,14,3),'B2_PYTHON_VERSION_INVALID')
    with connect(secrets['pg_password'],database='postgres') as conn:
        require(conn.execute('SELECT current_user').fetchone()==('postgres',),'B2_ADMIN_ROLE_INVALID')
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
        require(result.returncode==0,'B2_BASELINE_PHASE_'+target+'_FAILED')
    with connect(secrets['pg_password']) as conn:
        conn.execute('SET LOCAL ROLE daon_app')
        require(conn.execute("SELECT current_user,has_schema_privilege(current_user,'public','CREATE')").fetchone()==('daon_app',True),'B2_APP_SCHEMA_PRECONDITION')
    emit('baseline_app_0040',status='RUNNING');migrate('0040',app=True);emit('baseline_app_0040',status='PASS')
    with connect(secrets['pg_password']) as conn:
        row=conn.execute("SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='organization_license_versions'::regclass").fetchone()
        require(row==('daon_app',True,True),'B2_LICENSE_OWNER_PRECONDITION')
        for signature in ('transition_canon_state(text,text,integer,text,text,text,text,text)','claim_document_processing_job(text,integer)'):
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==('daon_app',),'B2_FUNCTION_OWNER_PRECONDITION')
    emit('baseline_admin_0072',status='RUNNING');migrate('0072');emit('baseline_admin_0072',status='PASS')
    with connect(secrets['pg_password']) as conn:
        require(conn.execute('SELECT version_num FROM alembic_version').fetchall()==[('0072',)],'B2_REVISION_INVALID')
        for signature,owner in [('transition_canon_state(text,text,integer,text,text,text,text,text)','daon_notebook_delete'),('claim_document_processing_job(text,integer)','postgres')]:
            require(conn.execute('SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s::regprocedure',(signature,)).fetchone()==(owner,),'B2_FUNCTION_OWNER_POSTCONDITION')
        require(conn.execute("SELECT to_regclass('operations_run_cost_events'),to_regclass('operations_host_capacity_samples')").fetchone()==(None,None),'B2_0073_FORBIDDEN')
        for name in HASHES:conn.execute((root/'services/api/migrations/versions'/name).read_text())
        conn.execute('REVOKE CREATE ON SCHEMA public FROM daon_app')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {} IN ROLE daon_app').format(sql.Identifier(APP),sql.Literal(secrets['app_password'])))
    with connect(secrets['app_password'],app=True) as conn:
        row=conn.execute("SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication,has_schema_privilege(current_user,'public','CREATE'),pg_has_role(current_user,'daon_notebook_delete','MEMBER') FROM pg_roles WHERE rolname=current_user").fetchone()
        require(row==(APP,APP,False,False,False,False,False,False,False),'B2_RESTRICTED_ROLE_INVALID')
    emit('bootstrap',status='PASS',schema='baseline0072+exactSQL0074/0075',sql_hashes=HASHES)

def run_probe(secrets):
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from datetime import datetime,timedelta,timezone
    from types import SimpleNamespace
    from dataclasses import replace
    from psycopg import Error
    from psycopg.types.json import Jsonb
    from sqlalchemy.engine import URL
    from daon_user_api.cloud_storage import PostgresCloudStore,CloudAccessContext
    from daon_user_api.question_answering_postgres import QuestionContext
    from daon_user_api.routing_policy import build_route_plan,VerifiedObservation,VerifiedCandidate,RouteRequestSnapshot,digest
    from daon_user_api.routing_execution import RoutingExecutionService,PlanApproval,AttemptOutcome,RoutingExecutionError,_ScopedQuestionPort
    from daon_user_api.routing_attempts_postgres import PostgresRoutingAttemptsRepository
    from daon_user_api.data_canon import canonical_json_bytes
    T,W,A='tenant-b2-qa','workspace-b2-qa','actor-b2-qa'
    now=datetime.now(timezone.utc);ctx=QuestionContext(T,W,A,'trace-b2-qa','policy-b2-qa','notebook-b2-qa','session-b2-qa')
    with connect(secrets['pg_password']) as conn:
        require(conn.execute('SELECT version_num FROM alembic_version').fetchall()==[('0072',)],'B2_REVISION_INVALID')
        require(conn.execute("SELECT to_regclass('operations_run_cost_events'),to_regclass('operations_host_capacity_samples')").fetchone()==(None,None),'B2_0073_FORBIDDEN')
        conn.execute('INSERT INTO tenants(tenant_id,display_name) VALUES(%s,%s)',(T,T))
        conn.execute('INSERT INTO workspaces(tenant_id,workspace_id,display_name) VALUES(%s,%s,%s)',(T,W,W))
        conn.execute('INSERT INTO user_accounts(tenant_id,user_id) VALUES(%s,%s)',(T,A))
        conn.execute('INSERT INTO notebooks(tenant_id,workspace_id,notebook_id,created_by,created_at) VALUES(%s,%s,%s,%s,%s)',(T,W,ctx.notebook_id,A,now))
    dsn=URL.create('postgresql',username=APP,password=secrets['app_password'],host='127.0.0.1',port=5432,database=DB).render_as_string(hide_password=False)
    store=PostgresCloudStore(dsn);repo=PostgresRoutingAttemptsRepository(store,clock=lambda:now)
    c=VerifiedCandidate('qa-connection','qa-model','qa-deployment',1,1,0,1,'workspace',None,upper_budget_reference='d'*64)
    body=dict(schema_version=1,expected_version=0,enabled=True,capability='text_generation',candidates=[{k:v for k,v in c.references().items() if k not in {'deployment_id','upper_budget_reference'}}],
      ranking_profile=dict(version='qa-rank',evaluation_set='qa-eval',minimum_quality=7000,observation_window='qa-window',input_class='short',minimum_samples=1,max_age_seconds=3600,tier_mapping_version='qa-tier',locality_mapping_version='qa-locality'),fallback_policy=dict(mode='transient_only'),budget=dict(currency='USD',run_limit='1.00',max_attempts=1))
    policy=dict(body=body,version=1,policy_version_id='qa-policy-v1',digest_sha256=digest(body))
    def insert_policy(conn,record,version,prior):
        text=canonical_json_bytes(body).decode()
        conn.execute('INSERT INTO routing_policy_versions(tenant_id,workspace_id,record_id,aggregate_id,version,previous_version_id,canonical_json,canonical_text,digest_sha256,created_by,trace_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(T,W,record,'qa-routing-policy',version,prior,Jsonb(body),text,digest(body),A,'trace-b2-qa'))
    with store._transaction(CloudAccessContext(T,W,A,'routing_policy.put')) as conn:
        insert_policy(conn,'qa-policy-v1',1,None)
        conn.execute('INSERT INTO workspace_routing_policy_current VALUES(%s,%s,%s)',(T,W,'qa-policy-v1'))
    def race(function):
        barrier=Barrier(2)
        def task(i):barrier.wait(timeout=10);return function(i)
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(task,range(2)))
    def policy_update(i):
        try:
            with store._transaction(CloudAccessContext(T,W,A,'routing_policy.put')) as conn:
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('b2-pointer',))
                current=conn.execute('SELECT policy_version_id FROM workspace_routing_policy_current FOR UPDATE').fetchone()
                if current[0]!='qa-policy-v1':return 'conflict'
                insert_policy(conn,'qa-policy-v2-'+str(i),2,'qa-policy-v1')
                conn.execute('UPDATE workspace_routing_policy_current SET policy_version_id=%s',('qa-policy-v2-'+str(i),))
            return 'won'
        except Exception:raise
    try:
        emit('group1_pointer',status='RUNNING')
        require(sorted(race(policy_update))==['conflict','won'],'B2_POINTER_RACE_FAILED')
        with connect(secrets['pg_password']) as conn:
            current=conn.execute('SELECT policy_version_id FROM workspace_routing_policy_current').fetchone()[0]
        try:
            with store._transaction(CloudAccessContext(T,W,A,'routing_policy.put')) as conn:
                insert_policy(conn,'qa-policy-v3-rollback',3,current)
                conn.execute('UPDATE workspace_routing_policy_current SET policy_version_id=%s',('qa-policy-v3-rollback',))
                raise ValueError('B2_EXPECTED_ROLLBACK')
        except ValueError as error:require(str(error)=='B2_EXPECTED_ROLLBACK','B2_ROLLBACK_UNEXPECTED')
        with connect(secrets['pg_password']) as conn:
            require(conn.execute('SELECT policy_version_id FROM workspace_routing_policy_current').fetchone()==(current,),'B2_POINTER_ROLLBACK_FAILED')
            require(conn.execute("SELECT count(*) FROM routing_policy_versions WHERE record_id='qa-policy-v3-rollback'").fetchone()==(0,),'B2_VERSION_ROLLBACK_FAILED')
        policy['policy_version_id']=current
        # 원본 정책 본문은 같은 범용 예산 상한이며 순위/관측은 합성 서버 port다.
        request=RouteRequestSnapshot(ctx.notebook_id,'a'*64,'b'*64,'c'*64,'cloud_sync')
        observation=VerifiedObservation(c.deployment_id,'qa-observation','qa-v1',now-timedelta(seconds=1),now+timedelta(minutes=15),'qa-eval','qa-window','short',10,'qa-tier','qa-locality',0,9000,0,9900,100,'0.20','USD','qa-price',100,'sha256:'+'1'*64,'qa-license',True,True,True,True,True,True,digest(c.references()),'a'*64,'basis_points_ms_currency_v1')
        route=build_route_plan(ctx,request,policy,(c,),(observation,),now=now)
        class Port(_ScopedQuestionPort):
            def __init__(self,pool='qa-pool'):
                self.sources=();self.evidence=();self.general=True;self.options=dict(question='QA synthetic question',source_id=None,source_version_id=None,context_mode='raw_only')
                self.owner=SimpleNamespace(clock=lambda:now);self.pool=pool;self.calls=0;self.suppress=False
            def _current_access(self,conn,context):
                row=conn.execute('SELECT status FROM user_accounts WHERE tenant_id=%s AND user_id=%s',(T,A)).fetchone()
                if row!=('active',) or self.suppress:raise RoutingExecutionError('ROUTING_CURRENT_ACCESS_DENIED',403)
            def recheck(self,conn,context,plan):self._current_access(conn,context)
            def freeze(self,context,item):
                quote=dict(maximum_estimate='0.20',currency='USD',pricing_reference='qa-price',price_version='qa-v1',candidate_reference_digest=digest(item['candidate']),protocol_version='qa-fake-v1',transformed_payload_digest='f'*64,expires_at=(now+timedelta(minutes=10)).isoformat(),billing_reference=dict(scope_id='qa-billing',provider_code='CUSTOM',account_reference='d'*64))
                return dict(candidate=item['candidate'],provider_kind='server_internal',provider_code='CUSTOM',artifact_digest='sha256:'+'1'*64,license_reference='qa-license',endpoint_digest='e'*64,protocol_version='qa-fake-v1',transformed_payload_digest='f'*64,payload_bytes=10,egress_policy_digest='a'*64,authorization_mode='policy_approved',required_approver='workspace_manager',quote=quote)
            def budget(self,context,route):return dict(scope_id=self.pool,source_id='qa-budget',version='qa-v1',currency='USD',run_limit='1.00',period_limit='0.20',starts_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(minutes=15)).isoformat(),max_attempts=1)
            @contextmanager
            def open(self,context,plan,entry):
                def invoke():
                    self.calls+=1
                    return AttemptOutcome('succeeded',result=SimpleNamespace(answer='QA stored answer',insufficient=False,cited_chunk_ids=(),usage={}))
                yield SimpleNamespace(claims=entry,invoke=invoke)
        def prepare(run,pool):
            port=Port(pool);service=RoutingExecutionService(repo,port,clock=lambda:now)
            auth=service.prepare(ctx,route,'b'*64,run,authorize_plan=lambda p,r:PlanApproval(p.digest_sha256,A,ctx.session_id,'policy_approved'))
            return port,service,auth,repo.read(ctx,auth.plan_id)
        emit('group1_pointer',status='PASS')
        emit('group2_claim',status='RUNNING')
        emit('group2_prepare',status='RUNNING')
        port=Port('qa-claim-pool');engine=RoutingExecutionService(repo,port,clock=lambda:now)
        auth=engine.prepare(ctx,route,'b'*64,'qa-run-claim',authorize_plan=lambda p,r:PlanApproval(p.digest_sha256,A,ctx.session_id,'policy_approved'))
        emit('group2_read',status='RUNNING')
        plan=repo.read(ctx,auth.plan_id)
        def claim(i):return repo.claim(ctx,plan,idempotency_key='qa-claim-key',request_digest='b'*64,recheck=port.recheck)
        emit('group2_claim_race',status='RUNNING')
        require(sorted(race(claim))==[False,True],'B2_CLAIM_RACE_FAILED')
        emit('group2_claim_replay',status='RUNNING')
        require(repo.claim(ctx,plan,idempotency_key='qa-claim-key',request_digest='b'*64,recheck=port.recheck) is False,'B2_REPLAY_FAILED')
        emit('group2_wrong_request',status='RUNNING')
        expect_denial(lambda:repo.claim(ctx,plan,idempotency_key='qa-other-key',request_digest='f'*64,recheck=port.recheck),RoutingExecutionError,'ROUTING_AUTHORIZATION_INVALID',403)
        emit('group2_wrong_nonce',status='RUNNING')
        expect_denial(lambda:engine.execute(ctx,plan_id=auth.plan_id,authorization='wrong-nonce',request_digest='b'*64,idempotency_key='qa-claim-key'),RoutingExecutionError,'ROUTING_AUTHORIZATION_INVALID',403)
        emit('group2_wrong_scope',status='RUNNING')
        expect_denial(lambda:repo.read(replace(ctx,workspace_id='other-workspace'),auth.plan_id),RoutingExecutionError,'ROUTING_AUTHORIZATION_INVALID',403)
        emit('group2_claim_attempt',status='RUNNING')
        attempt=repo.claim_attempt(ctx,plan,1,recheck=port.recheck)
        emit('group2_duplicate_attempt',status='RUNNING')
        expect_denial(lambda:repo.claim_attempt(ctx,plan,1,recheck=port.recheck),RoutingExecutionError,'ROUTING_DISPATCH_ALREADY_CLAIMED',409)
        emit('group2_persisted_counts',status='RUNNING')
        with connect(secrets['pg_password']) as conn:
            require(conn.execute('SELECT count(*) FROM routing_execution_claims WHERE plan_id=%s',(auth.plan_id,)).fetchone()==(1,),'B2_CLAIM_COUNT_INVALID')
            require(conn.execute('SELECT count(*) FROM routing_dispatch_outbox WHERE plan_id=%s',(auth.plan_id,)).fetchone()==(1,),'B2_OUTBOX_COUNT_INVALID')
        emit('group2_claim',status='PASS')
        emit('group3_budget',status='RUNNING')
        a=prepare('qa-budget-a','qa-shared-budget');b=prepare('qa-budget-b','qa-shared-budget')
        for p,e,u,v in (a,b):repo.claim(ctx,v,idempotency_key='key-'+v.as_dict()['run_id'],request_digest='b'*64,recheck=p.recheck)
        def reserve(i):
            p,e,u,v=(a,b)[i]
            try:return repo.claim_attempt(ctx,v,1,recheck=p.recheck)
            except RoutingExecutionError as error:
                require(error.code=='COST_LIMIT_EXCEEDED','B2_BUDGET_UNEXPECTED_ERROR');return 'denied'
        reservations=race(reserve);require(reservations.count('denied')==1,'B2_BUDGET_RACE_FAILED')
        winner=0 if reservations[0]!='denied' else 1;p,e,u,v=(a,b)[winner];aid=reservations[winner]
        repo.finish(ctx,v,1,aid,AttemptOutcome('uncertain','TRANSPORT_UNCERTAIN','uncertain'),final=True,write_result=None)
        require(reserve(1-winner)=='denied','B2_UNCERTAIN_RESERVATION_RELEASED')
        receipt=AttemptOutcome('failed','TIMEOUT','known','0.10','USD','qa-late-receipt')
        require(repo.reconcile(ctx,v,1,receipt) is True and repo.reconcile(ctx,v,1,receipt) is False,'B2_LATE_RECEIPT_REPLAY_FAILED')
        with store._transaction(CloudAccessContext(T,W,A,'routing_execution.read')) as conn:
            from decimal import Decimal
            require(repo._obligation(repo._events(conn,ctx,budget_scope='qa-shared-budget'),'USD')==Decimal('0.10'),'B2_KNOWN_SETTLEMENT_WRONG')
        emit('group3_budget',status='PASS')
        emit('group4_rls',status='RUNNING')
        tables=['routing_authorization_plans','routing_execution_claims','routing_attempt_events','routing_dispatch_outbox','routing_billing_receipts']
        with connect(secrets['app_password'],app=True) as conn:
            for key,value in [('tenant_id',T),('workspace_id',W),('actor_id',A),('capability','routing_execution.read')]:conn.execute('SELECT set_config(%s,%s,true)',('app.'+key,value))
            for table in tables:
                require(conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT'),has_table_privilege(current_user,%s,'INSERT'),has_table_privilege(current_user,%s,'UPDATE'),has_table_privilege(current_user,%s,'DELETE'),has_table_privilege(current_user,%s,'TRUNCATE')",(table,)*5).fetchone()==(True,True,False,False,False),'B2_APP_PRIVILEGES_INVALID')
                for op in ('UPDATE '+table+' SET tenant_id=tenant_id','DELETE FROM '+table,'TRUNCATE '+table):
                    try:
                        with conn.transaction():conn.execute(op)
                    except Error as error:require(error.sqlstate in {'42501','55000'},'B2_MUTATION_DENIAL_WRONG')
                    else:raise ValueError('B2_MUTATION_ACCEPTED')
            conn.execute("SELECT set_config('app.workspace_id','other-workspace',true)")
            for table in tables:require(conn.execute('SELECT count(*) FROM '+table).fetchone()==(0,),'B2_RLS_LEAK')
        # 같은 backend를 다시 빌려 정상 commit과 rollback 뒤 SET LOCAL 복원을 검사한다.
        single=PostgresCloudStore(dsn,min_size=1,max_size=1)
        try:
            def pooled_clean(expected_pid):
                with single._pool.connection(timeout=2) as conn:
                    row=conn.execute("SELECT pg_backend_pid(),nullif(current_setting('app.tenant_id',true),''),nullif(current_setting('app.workspace_id',true),''),nullif(current_setting('app.actor_id',true),''),nullif(current_setting('app.capability',true),'')").fetchone()
                    require(row==(expected_pid,None,None,None,None),'B2_POOLED_CONTEXT_LEAK')
            with single._transaction(CloudAccessContext(T,W,A,'routing_execution.read')) as conn:
                pid=conn.execute('SELECT pg_backend_pid()').fetchone()[0]
            pooled_clean(pid)
            try:
                with single._transaction(CloudAccessContext(T,W,A,'routing_execution.rollback')) as conn:
                    require(conn.execute('SELECT pg_backend_pid()').fetchone()==(pid,),'B2_POOL_BACKEND_CHANGED')
                    raise ValueError('B2_EXPECTED_CONTEXT_ROLLBACK')
            except ValueError as error:require(str(error)=='B2_EXPECTED_CONTEXT_ROLLBACK','B2_ROLLBACK_UNEXPECTED')
            pooled_clean(pid)
        finally:single.close()
        with connect(secrets['pg_password']) as conn:
            for table in tables:
                require(conn.execute("SELECT count(*) FROM pg_class c,LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a WHERE c.oid=%s::regclass AND a.grantee=0",(table,)).fetchone()==(0,),'B2_PUBLIC_GRANT')
            try:
                with conn.transaction():conn.execute('UPDATE routing_execution_claims SET tenant_id=tenant_id')
            except Error as error:require(error.sqlstate=='55000','B2_IMMUTABILITY_WRONG')
            else:raise ValueError('B2_OWNER_MUTATION_ACCEPTED')
        emit('group4_rls',status='PASS')
        emit('group5_lineage_receipt',status='RUNNING')
        expect_denial(lambda:repo.finish(ctx,plan,1,'wrong-attempt',AttemptOutcome('failed','AUTH_DENIED'),final=True,write_result=None),RoutingExecutionError,'ROUTING_LINEAGE_INVALID',403)
        receipt=AttemptOutcome('failed','TIMEOUT','known','0.10','USD','qa-first-receipt')
        repo.finish(ctx,plan,1,attempt,receipt,final=True,write_result=None)
        with connect(secrets['pg_password']) as conn:
            require(conn.execute("SELECT count(*) FROM routing_attempt_events WHERE plan_id=%s AND event_kind='terminal'",(auth.plan_id,)).fetchone()==(1,),'B2_TERMINAL_COUNT_INVALID')
            try:
                with conn.transaction():conn.execute("INSERT INTO routing_execution_claims(tenant_id,workspace_id,plan_id,actor_id,session_id,idempotency_key,request_digest) VALUES(%s,'wrong-workspace',%s,%s,%s,'cross-fk',%s)",(T,auth.plan_id,A,ctx.session_id,'b'*64))
            except Error as error:require(error.sqlstate=='23503','B2_SCOPE_FK_WRONG')
            else:raise ValueError('B2_SCOPE_FK_ACCEPTED')
        with connect(secrets['pg_password']) as conn:
            try:
                with conn.transaction():
                    payload={'outcome':'uncertain'}
                    conn.execute("INSERT INTO routing_attempt_events(tenant_id,workspace_id,plan_id,attempt_id,event_kind,payload,content_digest) VALUES(%s,%s,%s,'qa-missing-attempt','terminal',%s,%s)",(T,W,auth.plan_id,Jsonb(payload),digest(payload)))
                    conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            except Error as error:require(error.sqlstate in {'23503','23514'},'B2_DEFERRED_FK_WRONG')
            else:raise ValueError('B2_DEFERRED_FK_ACCEPTED')
        expect_denial(lambda:repo.reconcile(ctx,v,1,AttemptOutcome('failed','TIMEOUT','known','0.10','USD','qa-first-receipt')),RoutingExecutionError,'ROUTING_RECEIPT_REUSED',409)
        emit('group5_lineage_receipt',status='PASS')
        emit('group6_savepoint_and_success',status='RUNNING')
        p,e,u,v=prepare('qa-savepoint-run','qa-savepoint-pool');repo.claim(ctx,v,idempotency_key='qa-savepoint-key',request_digest='b'*64,recheck=p.recheck)
        aid=repo.claim_attempt(ctx,v,1,recheck=p.recheck);p.suppress=True
        out=repo.finish(ctx,v,1,aid,AttemptOutcome('succeeded',cost_state='known',amount='0.10',currency='USD',receipt_id='qa-suppressed-receipt',result=SimpleNamespace(answer='must not be stored',insufficient=False,cited_chunk_ids=(),usage={})),final=True,write_result=p.write_result)
        from daon_user_api.routing_execution import SuppressedResult
        require(isinstance(out,SuppressedResult),'B2_SUPPRESSION_FAILED')
        with connect(secrets['pg_password']) as conn:
            require(conn.execute('SELECT state FROM runs WHERE record_id=%s',('qa-savepoint-run',)).fetchone()==('failed',),'B2_SUPPRESSION_RUN_INVALID')
            require(conn.execute('SELECT count(*) FROM run_results WHERE run_id=%s',('qa-savepoint-run',)).fetchone()==(0,),'B2_ANSWER_LEAK')
            require(conn.execute('SELECT cost_amount FROM model_attempts WHERE record_id=%s',(aid,)).fetchone()[0] is not None,'B2_ACTUAL_COST_LOST')
        p,e,u,v=prepare('qa-success-run','qa-success-pool')
        result=e.execute(ctx,plan_id=u.plan_id,authorization=u.authorization,request_digest='b'*64,idempotency_key='qa-success-key')
        replay=e.execute(ctx,plan_id=u.plan_id,authorization=u.authorization,request_digest='b'*64,idempotency_key='qa-success-key')
        require(result.answer==replay.answer=='QA stored answer' and p.calls==1,'B2_SUCCESS_REPLAY_FAILED')
        with connect(secrets['pg_password']) as conn:
            require(conn.execute('SELECT m.cost_amount FROM model_attempts m JOIN routing_decisions d ON (d.tenant_id,d.workspace_id,d.record_id)=(m.tenant_id,m.workspace_id,m.routing_decision_id) WHERE d.run_id=%s',('qa-success-run',)).fetchall()==[(None,)],'B2_UNKNOWN_COST_NOT_NULL')
        emit('group6_savepoint_and_success',status='PASS');emit('complete',status='PASS',groups=6,provider_calls=0)
    finally:store.close()

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
    require(stage in {'bootstrap','probe'},'B2_STAGE_INVALID')
    secrets=credentials()
    if stage=='bootstrap':bootstrap(secrets)
    else:run_probe(secrets)

if __name__=='__main__':
    try:main()
    except Exception as error:
        code=str(error) if type(error) is ValueError and re.fullmatch('B2_[A-Z0-9_]+',str(error)) else 'B2_GATE_FAILED'
        emit('failed',test_name=CURRENT_TEST,status='FAIL',safe_code=code,**safe_error_metadata(error))
        raise SystemExit(1) from None
