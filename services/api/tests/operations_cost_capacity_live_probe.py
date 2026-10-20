"""기존 정식 PG의 transaction/ROLLBACK 시험. Secret/DSN을 출력하거나 모델을 호출하지 않는다.

연결 공급자는 승인된 기존 운영 연결만 전달한다. 함수와 QA 행은 commit하지 않는다.
"""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib
import json
import threading
import time
import uuid

T='tenant-PvH_pofxPyDtfk3wWBrn3ru3'
W='workspace-2edb96cb84a252046e67307d'
A='usr-fwYlLlWlcP_GvvpPoZIkadvr'


def run_probe(connect,sql,*,host_reader=None,catalog_store=None):
    import psycopg
    result={}
    conn=blocker=None
    try:
        conn=connect()
        blocker=connect()
        if conn.autocommit is not False or blocker.autocommit is not False:
            raise ValueError('PROBE_REQUIRES_NEW_NON_AUTOCOMMIT_CONNECTIONS')
        if (conn.info.transaction_status!=psycopg.pq.TransactionStatus.IDLE
            or blocker.info.transaction_status!=psycopg.pq.TransactionStatus.IDLE):
            raise ValueError('PROBE_REQUIRES_NEW_NON_AUTOCOMMIT_CONNECTIONS')
        conn.execute("SET LOCAL statement_timeout='8s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        assert conn.info.transaction_status==psycopg.pq.TransactionStatus.INTRANS,'PROBE_TRANSACTION_NOT_ACTIVE'
        assert conn.execute('SELECT version_num FROM public.alembic_version').fetchall()==[('0072',)],'EXPECTED_CURRENT_0072'
        assert conn.execute("SELECT to_regprocedure('public.reserve_operations_run_cost(text,text,text,text,text,text,jsonb,bigint,integer)')").fetchone()==(None,),'EXPECTED_INITIAL_FUNCTION_ABSENCE'
        before=conn.execute("SELECT oid,md5(prosrc) FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY oid").fetchall()
        conn.execute(sql)
        if catalog_store is not None:
            conn.execute("UPDATE public.alembic_version SET version_num='0073' WHERE version_num='0072'")
            conn.execute('SET LOCAL ROLE daon_app')
            guards=(('organization',catalog_store._organization_storage_is_protected,{}),
                ('notification',catalog_store._notification_storage_is_protected,{}),
                ('restore',catalog_store._registered_restore_storage_is_protected,{'expected_revision':'0073'}),
                ('telemetry',catalog_store._telemetry_storage_is_protected,{'expected_revision':'0073'}),
                ('readiness',catalog_store._document_readiness_storage_is_protected,{'expected_revision':'0073'}),
                ('capacity',catalog_store._operations_cost_capacity_storage_is_protected,{}))
            for name,guard,kwargs in guards:
                assert guard(conn,**kwargs) is True,'CATALOG_GUARD_REJECTED_'+name
            conn.execute('RESET ROLE')
            class RestoreSavepoint(Exception):pass
            for mutation in (
                'GRANT SELECT ON public.operations_run_cost_events TO daon_app',
                'ALTER TABLE public.operations_host_capacity_samples DISABLE TRIGGER operations_host_capacity_guard',
                'ALTER TABLE public.operations_run_cost_events NO FORCE ROW LEVEL SECURITY',
                """DO $probe$ DECLARE check_name text; BEGIN
                    SELECT conname INTO STRICT check_name FROM pg_catalog.pg_constraint
                    WHERE conrelid='public.operations_host_capacity_samples'::regclass AND contype='c'
                        AND pg_catalog.pg_get_expr(conbin,conrelid) LIKE '%cardinality(cpu_counters)%';
                    EXECUTE format('ALTER TABLE public.operations_host_capacity_samples DROP CONSTRAINT %I',check_name);
                    END $probe$""",
                'GRANT EXECUTE ON FUNCTION public.operations_run_cost_parent_allowed(text,text,text,text,text) TO daon_app',
            ):
                try:
                    with conn.transaction():
                        conn.execute(mutation)
                        conn.execute('SET LOCAL ROLE daon_app')
                        assert catalog_store._operations_cost_capacity_storage_is_protected(conn) is False,'ALTERED_CATALOG_ACCEPTED'
                        raise RestoreSavepoint()
                except RestoreSavepoint:pass
            conn.execute('SET LOCAL ROLE daon_app')
            assert catalog_store._operations_cost_capacity_storage_is_protected(conn) is True,'SAVEPOINT_DID_NOT_RESTORE_CATALOG'
            conn.execute('RESET ROLE')
            conn.execute("UPDATE public.alembic_version SET version_num='0072' WHERE version_num='0073'")
            result['exact_catalog_and_all_existing_guards_and_five_mutations']=True
        suffix=uuid.uuid4().hex[:12]
        run='qa-s44-cost-pg-'+suffix
        route='qa-s44-cost-route-'+suffix
        first='qa-s44-cost-attempt-a-'+suffix
        second='qa-s44-cost-attempt-b-'+suffix
        sample=conn.execute('SELECT d.routing_policy_version_id,m.model_deployment_id,m.model_artifact_id '
            'FROM public.model_attempts m JOIN public.routing_decisions d ON '
            '(d.tenant_id,d.workspace_id,d.record_id)=(m.tenant_id,m.workspace_id,m.routing_decision_id) '
            'WHERE m.tenant_id=%s AND m.workspace_id=%s ORDER BY m.created_at DESC LIMIT 1',(T,W)).fetchone()
        assert sample is not None,'EXISTING_QA_MODEL_LINEAGE_REQUIRED'
        def insert(table,record,extra,values):
            body=json.dumps({'qa_marker':'S44 transactional SQL probe','record_id':record},separators=(',',':'))
            conn.execute('INSERT INTO public.'+table+'(tenant_id,workspace_id,record_id,aggregate_id,canonical_json,canonical_text,digest_sha256,created_by,trace_id'
                +(','+','.join(extra) if extra else '')+') VALUES('+','.join(['%s']*(9+len(extra)))+')',
                (T,W,record,record,body,body,hashlib.sha256(body.encode()).hexdigest(),A,'qa-s44-cost-probe',*values))
        insert('runs',run,(),())
        insert('routing_decisions',route,('run_id','routing_policy_version_id'),(run,sample[0]))
        now=datetime.now(timezone.utc)
        for attempt in (first,second):
            insert('model_attempts',attempt,('routing_decision_id','model_deployment_id','model_artifact_id','candidate_order','started_at'),
                (route,sample[1],sample[2],1,now))
        for key,value in (('tenant_id',T),('workspace_id',W),('actor_id',A),('capability','run.cost')):
            conn.execute('SELECT set_config(%s,%s,true)',('app.'+key,value))
        conn.execute('SET LOCAL ROLE daon_app')
        def denied(query,params=(),*,message=None,sqlstate='42501'):
            try:
                with conn.transaction():conn.execute(query,params)
            except psycopg.Error as error:
                assert error.sqlstate==sqlstate,'UNEXPECTED_ERROR_NOT_EXPECTED_DENIAL'
                if message is not None:assert error.diag.message_primary==message,'WRONG_SCOPE_DENIAL_REASON'
                return
            raise AssertionError('PROTECTED_WRITE_OR_SCOPE_WAS_ACCEPTED')
        denied('SELECT * FROM public.operations_run_cost_events')
        denied('INSERT INTO public.operations_host_capacity_samples(host_ref) VALUES(%s)',('wsl-server-daon-user',))
        result['direct_app_table_access_denied']=True
        def policy(expires):
            return {'schema_version':1,'policy_id':'qa-upstage-usd-run','version':1,'tenant_id':T,'workspace_id':W,'actor_id':A,
                'connection_id':'provider-upstage','model_id':'solar-pro4','run_kind':'question','currency':'USD','limit_minor_units':1000,
                'price_card':{'version':'s44-probe-price','base_url':'https://api.upstage.ai/v1','model_id':'solar-pro4','currency':'USD',
                    'input_per_million':'0.30','cached_input_per_million':'0.06','output_per_million':'1.20',
                    'valid_from':(now-timedelta(seconds=1)).isoformat(),'valid_until':expires.isoformat()}}
        reserve_sql='SELECT * FROM public.reserve_operations_run_cost(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)'
        lock_key='operations-run-cost-v1|'+T+'|'+W+'|question|'+run
        blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(lock_key,))
        expires=datetime.now(timezone.utc)+timedelta(seconds=1.5)
        waiting={}
        def wait_reserve():
            try:waiting['row']=conn.execute(reserve_sql,(T,W,A,'question',run,'qa-expiry-attempt',json.dumps(policy(expires)),1000,512)).fetchone()
            except BaseException as error:waiting['error_type']=type(error).__name__
        thread=threading.Thread(target=wait_reserve,daemon=True)
        thread.start()
        # 다른 연결의 실제 advisory lock 대기를 확인한 뒤 만료를 넘긴다.
        waiting_seen=False
        for _ in range(40):
            if blocker.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted)",(conn.info.backend_pid,)).fetchone()[0]:
                waiting_seen=True;break
            time.sleep(.025)
        assert waiting_seen,'RESERVATION_LOCK_WAIT_NOT_OBSERVED'
        time.sleep(max(0,(expires-datetime.now(timezone.utc)).total_seconds())+.1)
        blocker.rollback()
        thread.join(5)
        assert not thread.is_alive() and waiting.get('row')==('unknown','COST_POLICY_UNCONFIRMED',None,None,None,None,None),'EXPIRED_PRICE_RESERVED_AFTER_LOCK_WAIT'
        result['two_connections_expired_price_denied_after_wait']=True
        p=json.dumps(policy(now+timedelta(hours=1)))
        held=conn.execute(reserve_sql,(T,W,A,'question',run,first,p,1000,512)).fetchone()
        assert held[:6]==('reserved','COST_RESERVED',1,Decimal('0.000914400'),'USD','s44-probe-price'),'RESERVATION_INVALID'
        duplicate=conn.execute(reserve_sql,(T,W,A,'question',run,first,p,1000,512)).fetchone()
        assert duplicate[0:2]==('blocked','COST_ATTEMPT_ALREADY_RECORDED'),'DUPLICATE_ATTEMPT_ALLOWED'
        pending=conn.execute(reserve_sql,(T,W,A,'question',run,second,p,1000,512)).fetchone()
        assert pending[0:2]==('unknown','COST_SETTLEMENT_UNCONFIRMED'),'PENDING_RESERVATION_RELEASED'
        result['duplicate_and_pending_attempts_blocked']=True
        receipt='cost-'+hashlib.sha256(first.encode()).hexdigest()
        observed=datetime.now(timezone.utc)
        settle=(T,W,A,'question',run,first,1,'known',Decimal('0.000372000'),'USD','s44-probe-price','a'*64,receipt,observed)
        settle_sql='SELECT public.settle_operations_run_cost('+','.join(['%s']*14)+')'
        assert conn.execute(settle_sql,settle).fetchone()==(2,),'SETTLEMENT_FAILED'
        assert conn.execute(settle_sql,settle).fetchone()==(2,),'SETTLEMENT_REPLAY_NOT_STABLE'
        writer='SELECT public.record_operations_price_card_receipt('+','.join(['%s']*15)+')'
        values=(T,W,run,A,receipt,'provider-upstage',second,'price_card',Decimal('0.000372000'),'USD','a'*64,'s44-probe-price','known',observed,'b'*64)
        denied(writer,values,message='OPERATIONS_RECEIPT_SCOPE_INVALID')
        denied(writer,(*values[:6],None,*values[7:]),message='OPERATIONS_RECEIPT_SCOPE_INVALID')
        correct=(*values[:6],first,*values[7:])
        assert conn.execute(writer,correct).fetchone()==('b'*64,),'EXACT_ATTEMPT_NOT_ACCEPTED'
        result['wrong_same_run_attempt_and_known_null_denied']=True
        result['exact_attempt_receipt_saved']=True
        if host_reader is not None:
            assert conn.execute('SELECT * FROM public.read_operations_host_capacity()').fetchall()==[],'EXPECTED_EMPTY_HOST_SAMPLE'
            actual=host_reader(observed_at=datetime.now(timezone.utc),sequence=1)
            def host_args(row):
                return (row.host_ref,row.boot_id,row.sequence,row.observed_at,row.cpu_logical_millicores,
                    row.cpu_usage_basis_points,row.memory_total_bytes,row.memory_available_bytes,list(row.cpu_counters))
            host_sql='SELECT * FROM public.record_operations_host_capacity('+','.join(['%s']*9)+')'
            stored=conn.execute(host_sql,host_args(actual)).fetchone()
            assert stored[0:4]==(actual.host_ref,actual.boot_id,1,actual.observed_at),'HOST_SAMPLE_IDENTITY_CHANGED'
            assert stored[5:9]==(actual.cpu_logical_millicores,None,actual.memory_total_bytes,actual.memory_available_bytes),'HOST_SAMPLE_VALUES_CHANGED'
            assert conn.execute('SELECT * FROM public.read_operations_host_capacity()').fetchone()==stored,'HOST_READ_DIFFERENT_FROM_COMMITTED_WRITE_IN_TX'
            resumed=type(actual)(stored[0],stored[1],stored[2],stored[3],stored[5],stored[6],stored[7],stored[8],tuple(stored[9]))
            time.sleep(.15)
            next_sample=host_reader(observed_at=datetime.now(timezone.utc),sequence=resumed.sequence+1,previous=resumed)
            assert next_sample.boot_id==actual.boot_id and next_sample.sequence==2,'HOST_RESTART_SEQUENCE_CHANGED'
            assert type(next_sample.cpu_usage_basis_points) is int and 0<=next_sample.cpu_usage_basis_points<=10000,'HOST_CPU_DELTA_NOT_MEASURED'
            updated=conn.execute(host_sql,host_args(next_sample)).fetchone()
            assert updated[2]==2,'HOST_SEQUENCE_NOT_SAVED'
            denied(host_sql,host_args(actual),sqlstate='23514',message='HOST_CAPACITY_REPLAY')
            result['real_proc_sample_db_read_restart_sequence_replay_denial']=True
        conn.execute('RESET ROLE')
        assert conn.execute("SELECT oid,md5(prosrc) FROM pg_proc WHERE oid=ANY(%s) ORDER BY oid",([r[0] for r in before],)).fetchall()==before,'EXISTING_FUNCTION_DEFINITION_CHANGED'
        conn.rollback()
        verifier=connect()
        try:
            assert verifier.autocommit is False and verifier.info.transaction_status==psycopg.pq.TransactionStatus.IDLE,'PROBE_VERIFIER_NOT_NEW_TRANSACTION'
            assert verifier.execute("SELECT to_regclass('public.operations_run_cost_events'),to_regclass('public.operations_host_capacity_samples')").fetchone()==(None,None),'ROLLBACK_LEFT_NEW_TABLES'
            assert verifier.execute("SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=ANY(%s)",([
                'operations_run_cost_parent_allowed','guard_operations_run_cost_event','reserve_operations_run_cost',
                'settle_operations_run_cost','record_operations_price_card_receipt','guard_operations_host_capacity',
                'record_operations_host_capacity','read_operations_host_capacity'],)).fetchone()==(0,),'ROLLBACK_LEFT_NEW_FUNCTIONS'
            for table,record in (('runs',run),('routing_decisions',route),('model_attempts',first),('model_attempts',second)):
                assert verifier.execute('SELECT count(*) FROM public.'+table+' WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s',(T,W,record)).fetchone()==(0,),'ROLLBACK_LEFT_QA_ROW'
            assert verifier.execute('SELECT version_num FROM public.alembic_version').fetchall()==[('0072',)],'CURRENT_REVISION_CHANGED'
        finally:
            try:verifier.rollback()
            finally:verifier.close()
        result['existing_functions_unchanged_and_complete_rollback']=True
        return result
    finally:
        try:
            if blocker is not None:
                try:blocker.rollback()
                finally:blocker.close()
        finally:
            if conn is not None:
                try:conn.rollback()
                finally:conn.close()
