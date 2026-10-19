"""M2-1 직접 clock/thread/SQL 경계 시험. 실제 PG/cgroup/Linux producer 시험 아님."""
from __future__ import annotations

import importlib
import threading
import unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch


NOW=datetime(2026,10,19,4,tzinfo=timezone.utc)


def api():
    if importlib.util.find_spec("daon_user_api.operations_telemetry") is None:
        raise AssertionError("M2-1 worker observation producer missing")
    return importlib.import_module("daon_user_api.operations_telemetry")


class Sink:
    def __init__(self,module,clock):self.module,self.clock,self.samples=module,clock,[]
    def record_worker_sample(self,sample):
        self.module.validate_worker_sample(sample,received_at=self.clock(),previous=[s for s in self.samples if s.sample.worker_id==sample.worker_id])
        stored=self.module.ReceivedWorkerSample(sample,self.clock());self.samples.append(stored);return stored


class OperatorBaseline:
    """고정 command 응답 대역. 실제 Docker/Git/배포/DB 실행 증거가 아니다."""
    old_revision='a'*40
    new_revision='d'*40
    old_image='sha256:'+'1'*64
    new_image='sha256:'+'2'*64

    def __init__(self,test):
        import json
        from pathlib import Path
        path=Path(__file__).resolve().parents[3]/'deploy/daon-user/record-operation-receipt.py'
        spec=importlib.util.spec_from_file_location('operation_receipt_baseline_test',path)
        self.writer=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.writer)
        self.test=test;self.json=json;self.phase=0;self.calls=[];self.clock=NOW;self.elapsed=0
        self.snapshots=[dict(code_revision=self.old_revision,image_digest=self.old_image,container_id='b'*64,started_at=NOW-timedelta(seconds=10)),
            dict(code_revision=self.new_revision,image_digest=self.new_image,container_id='c'*64,started_at=NOW+timedelta(seconds=1))]
        self.labels={self.old_image:self.old_revision,self.new_image:self.new_revision}
        self.types={self.old_revision:'commit',self.new_revision:'commit'}
        self.blobs={self.old_revision:'e'*40,self.new_revision:'f'*40}
        self.source_blobs=['e'*40,'f'*40];self.smoke_fail=False;self.on_blob=None;self.on_smoke=None
        self.image_id_override=None;self.before_wait=None;self.command_timeout=False

    def run(self,args,**kwargs):
        self.test.assertIs(kwargs['shell'],False);self.calls.append(args);snapshot=self.snapshots[self.phase]
        if self.command_timeout:
            import subprocess
            raise subprocess.TimeoutExpired(args,10,stderr=b'PRIVATE_TIMEOUT_CANARY')
        if args[:2]==('git','rev-parse'):
            raw=self.new_revision  # checkout은 관찰 시작 전부터 이미 새 SHA다.
        elif args[:2]==('git','cat-file'):
            self.test.assertEqual(args[2],'-t');kind=self.types.get(args[3])
            if kind is None:return SimpleNamespace(returncode=1,stdout=b'',stderr=b'PRIVATE_COMMIT_CANARY')
            raw=kind
        elif args[:2]==('git','ls-tree'):
            digest=self.blobs.get(args[4]);raw='' if digest is None else '100644 blob '+digest+'\tservices/api/src/daon_user_api/__init__.py'
        elif args[:2]==('docker','compose'):raw=snapshot['container_id']
        elif args[:3]==('docker','image','inspect'):
            image=args[-1]
            raw=' '.join(self.json.dumps(value) for value in (self.image_id_override or image,self.labels.get(image)))
        elif args[:2]==('docker','inspect'):
            raw=' '.join(self.json.dumps(value) for value in
                (snapshot['container_id'],snapshot['image_digest'],True,snapshot['started_at'].isoformat()))
        elif args[-1]==self.writer._BLOBS:
            raw=self.json.dumps({'services/api/src/daon_user_api/__init__.py':self.source_blobs[self.phase]})
            if self.on_blob:self.on_blob()
        elif args[-1]==self.writer._SMOKE:
            if self.on_smoke:self.on_smoke()
            if self.smoke_fail:return SimpleNamespace(returncode=1,stdout=b'',stderr=b'PRIVATE_OPERATION_CANARY')
            raw='READY'
        else:raise AssertionError('UNEXPECTED_OPERATOR_COMMAND')
        return SimpleNamespace(returncode=0,stdout=raw.encode(),stderr=b'')

    def applied(self,seconds):
        self.test.assertEqual(seconds,300)
        self.phase=1;self.clock=NOW+timedelta(seconds=2)
        if self.before_wait:self.before_wait()

    def record(self,stored,**overrides):
        options=dict(expected_revision=self.new_revision,expected_image=self.new_image,
            observer=self.writer.FixedOperatorObserver(run=self.run),wait_applied=self.applied,sink=stored.append,
            clock=lambda:self.clock,monotonic=lambda:self.elapsed)
        options.update(overrides)
        return self.writer.record_operation('operation-a','deploy',**options)


class OperationsTelemetryTests(unittest.TestCase):
    def test_operator_before_uses_old_image_commit_when_checkout_is_new(self):
        case=OperatorBaseline(self)
        observed=case.writer.FixedOperatorObserver(run=case.run).inspect()
        self.assertEqual(observed,case.snapshots[0])
        self.assertIn(('git','cat-file','-t',case.old_revision),case.calls)
        self.assertFalse(any(args[:2]==('git','rev-parse') for args in case.calls),'HEAD fallback 금지')

    def test_operator_before_rejects_unproved_label_commit_blob_and_observation_change(self):
        for mode in ('missing-label','malformed-label','missing-commit','tag-object','forged-label','wrong-blob',
                'missing-source-tree','image-id','changed-container','changed-image','changed-label','command-timeout'):
            with self.subTest(mode=mode):
                case=OperatorBaseline(self)
                if mode=='missing-label':case.labels[case.old_image]=None
                if mode=='malformed-label':case.labels[case.old_image]='api:latest'
                if mode=='missing-commit':case.labels[case.old_image]='9'*40
                if mode=='tag-object':case.types[case.old_revision]='tag'
                if mode=='forged-label':case.labels[case.old_image]=case.new_revision
                if mode=='wrong-blob':case.source_blobs[0]='0'*40
                if mode=='missing-source-tree':case.blobs.pop(case.old_revision)
                if mode=='image-id':case.image_id_override='sha256:'+'9'*64
                if mode=='changed-container':case.on_blob=lambda:case.snapshots[0].update(container_id='8'*64)
                if mode=='changed-image':case.on_blob=lambda:case.snapshots[0].update(image_digest=case.new_image)
                if mode=='changed-label':case.on_blob=lambda:case.labels.update({case.old_image:case.new_revision})
                if mode=='command-timeout':case.command_timeout=True
                with self.assertRaises(api().TelemetryUnavailable) as failed:case.writer.FixedOperatorObserver(run=case.run).inspect()
                self.assertEqual(str(failed.exception),'OPERATIONS_TELEMETRY_UNAVAILABLE')
                self.assertFalse(any(args[:2]==('git','rev-parse') for args in case.calls))

    def test_operator_after_requires_approved_exact_revision_and_image(self):
        for options in (dict(expected_revision='f'*40),dict(expected_image='sha256:'+'9'*64),
                dict(expected_revision='main'),dict(expected_image='api:latest')):
            with self.subTest(options=options):
                case=OperatorBaseline(self);stored=[]
                with self.assertRaises(api().TelemetryUnavailable):case.record(stored,**options)
                self.assertEqual(stored,[])

    def test_operator_change_during_smoke_is_unavailable_without_receipt(self):
        for field,value in (('container_id','9'*64),('image_digest','sha256:'+'9'*64)):
            with self.subTest(field=field):
                case=OperatorBaseline(self);stored=[]
                case.on_smoke=lambda:case.snapshots[1].update({field:value})
                with self.assertRaises(api().TelemetryUnavailable):case.record(stored)
                self.assertEqual(stored,[])

    def test_operator_deadline_replacement_and_started_clock_boundaries_remain_closed(self):
        for mode in ('deadline','same-container','old-start','before-window','future-start','backwards-clock'):
            with self.subTest(mode=mode):
                case=OperatorBaseline(self);stored=[]
                if mode=='deadline':case.before_wait=lambda:setattr(case,'elapsed',301)
                if mode=='same-container':case.snapshots[1]=dict(case.snapshots[0])
                if mode=='old-start':case.snapshots[1]['started_at']=NOW-timedelta(seconds=20)
                if mode=='before-window':case.snapshots[1]['started_at']=NOW-timedelta(seconds=1)
                if mode=='future-start':case.snapshots[1]['started_at']=NOW+timedelta(seconds=3)
                if mode=='backwards-clock':case.before_wait=lambda:setattr(case,'elapsed',-1)
                with self.assertRaises(api().TelemetryUnavailable):case.record(stored)
                self.assertEqual(stored,[])

    def test_using_transaction_dict_cursor_is_scoped_and_never_owns_api_pool(self):
        from contextlib import contextmanager
        from psycopg.rows import dict_row,tuple_row
        import psycopg
        pg=importlib.import_module('daon_user_api.operations_telemetry_postgres')
        events=[];fail=[False]
        class Cursor:
            def __enter__(self):events.append('cursor-enter');return self
            def __exit__(self,*args):events.append('cursor-exit')
            def execute(self,sql,params=None):
                self_test.assertEqual(sql,'SELECT * FROM public.read_operations_deployment_receipt()')
                if fail[0]:raise psycopg.OperationalError('PRIVATE_BORROWED_CONNECTION_CANARY')
                return SimpleNamespace(fetchall=lambda:[])
        class Connection:
            def __init__(self):self.row_factory=tuple_row
            def cursor(self,*,row_factory):
                self_test.assertIs(row_factory,dict_row);return Cursor()
            def execute(self,*args):raise AssertionError('BORROWED_DEFAULT_CURSOR_MUST_NOT_CHANGE')
            def transaction(self):raise AssertionError('NESTED_TRANSACTION_NOT_OWNED')
            def commit(self):raise AssertionError('CALLER_COMMIT_NOT_OWNED')
            def rollback(self):raise AssertionError('CALLER_ROLLBACK_NOT_OWNED')
            def close(self):raise AssertionError('API_CONNECTION_CLOSE_NOT_OWNED')
        connection=Connection();self_test=self
        @contextmanager
        def factory():
            events.append('transaction-enter')
            try:yield connection
            except BaseException:
                events.append('caller-rollback');raise
            else:events.append('caller-commit')
        with patch.object(pg,'ConnectionPool',side_effect=AssertionError('NEW_POOL_FORBIDDEN')):
            self.assertTrue(callable(getattr(pg.PostgresOperationsTelemetryRepository,'using_transaction',None)),'borrowed transaction adapter missing')
            repo=pg.PostgresOperationsTelemetryRepository.using_transaction(factory)
            self.assertIsNone(repo.read_deployment_receipt())
            self.assertEqual(events,['transaction-enter','cursor-enter','cursor-exit','caller-commit'])
            events.clear();fail[0]=True
            with self.assertRaises(api().TelemetryUnavailable) as failed:repo.read_deployment_receipt()
            self.assertEqual(str(failed.exception),'OPERATIONS_TELEMETRY_UNAVAILABLE')
            self.assertEqual(events,['transaction-enter','cursor-enter','cursor-exit','caller-rollback'])
            self.assertIs(connection.row_factory,tuple_row)
            repo.close();repo.close();events.clear()
            with self.assertRaises(api().TelemetryUnavailable):repo.read_deployment_receipt()
            self.assertEqual(events,[])

    def test_operator_concrete_preinspect_postinspect_smoke_and_owner_sql_no_secret_argv(self):
        pg=importlib.import_module('daon_user_api.operations_telemetry_postgres')
        calls=[];stored=[]
        for fail in (False,True):
            case=OperatorBaseline(self);case.smoke_fail=fail
            receipt=case.record(stored);calls.extend(case.calls)
            self.assertEqual(receipt.state,'uncertain' if fail else 'succeeded')
            self.assertEqual(receipt.previous_image_digest,'sha256:'+'1'*64)
        self.assertNotIn('PRIVATE_OPERATION_CANARY',str(stored)+str(calls))
        sql_calls=[];role=['daon_app',False]
        class Connection:
            def execute(self,sql,params=()):
                sql_calls.append((sql,params))
                return SimpleNamespace(fetchone=lambda:tuple(role) if sql.startswith('SELECT current_user') else (params[-1],))
        with self.assertRaises(api().TelemetryUnavailable):pg.record_deployment_in_operator_transaction(Connection(),stored[0])
        self.assertEqual(len(sql_calls),1)
        role[:]=['operator-owner',True]
        self.assertEqual(pg.record_deployment_in_operator_transaction(Connection(),stored[0]),stored[0].digest())

    def test_latest_deployment_is_none_until_real_append_and_validates_protected_digest(self):
        from contextlib import contextmanager
        from dataclasses import asdict
        pg=importlib.import_module('daon_user_api.operations_telemetry_postgres');rows=[]
        repo=object.__new__(pg.PostgresOperationsTelemetryRepository)
        class Connection:
            def execute(self,sql):
                self_test.assertEqual(sql,'SELECT * FROM public.read_operations_deployment_receipt()')
                return SimpleNamespace(fetchall=lambda:rows)
        self_test=self
        @contextmanager
        def transaction():yield Connection()
        repo._transaction=transaction
        self.assertIsNone(repo.read_deployment_receipt())
        receipt=api().DeploymentReceipt('operation-a','deploy','a'*40,'sha256:'+'b'*64,'sha256:'+'c'*64,'succeeded',NOW,'DEPLOYMENT_SUCCEEDED')
        rows.append(dict(asdict(receipt),received_at=NOW,receipt_digest=receipt.digest()))
        self.assertEqual(repo.read_deployment_receipt()['image_digest'],'sha256:'+'b'*64)
        rows[0]['receipt_digest']='0'*64
        with self.assertRaises(api().TelemetryUnavailable):repo.read_deployment_receipt()

    def test_tag_only_is_not_deployment_evidence(self):
        module=api()
        self.assertTrue(callable(getattr(module,'DeploymentReceipt',None)),'M2-3 deployment receipt missing')
        with self.assertRaises(ValueError):module.DeploymentReceipt('operation-a','deploy','a'*40,'web:latest',None,'succeeded',NOW,'DEPLOYMENT_SUCCEEDED')

    def test_rollback_not_executed_is_not_success(self):
        from pathlib import Path
        path=Path(__file__).resolve().parents[3]/'deploy/daon-user/record-operation-receipt.py'
        self.assertTrue(path.is_file(),'M2-3 concrete operator consumer missing')
        spec=importlib.util.spec_from_file_location('operation_receipt_test',path);writer=importlib.util.module_from_spec(spec);spec.loader.exec_module(writer)
        before=dict(code_revision='a'*40,image_digest='sha256:'+'b'*64,container_id='c'*64,started_at=NOW-timedelta(seconds=10))
        calls=[]
        observer=SimpleNamespace(inspect=lambda:dict(before),smoke=lambda container:True)
        with self.assertRaises(api().TelemetryUnavailable):
            writer.record_operation('operation-a','rollback',observer=observer,wait_applied=lambda seconds:None,
                expected_revision=before['code_revision'],expected_image=before['image_digest'],
                sink=lambda receipt:calls.append(receipt),clock=lambda:NOW,monotonic=lambda:0)
        self.assertEqual(calls,[])

    def test_uncertain_receipt_not_ready(self):
        module=api();self.assertTrue(callable(getattr(module,'DeploymentReceipt',None)))
        receipt=module.DeploymentReceipt('operation-a','deploy','a'*40,'sha256:'+'b'*64,'sha256:'+'c'*64,'uncertain',NOW,'DEPLOYMENT_UNCERTAIN')
        self.assertEqual(receipt.state,'uncertain');self.assertEqual(len(receipt.digest()),64)

    def test_user_cannot_register_deployment(self):
        pg=importlib.import_module('daon_user_api.operations_telemetry_postgres')
        self.assertFalse(hasattr(pg.PostgresOperationsTelemetryRepository,'record_deployment_receipt'))
        self.assertTrue(callable(getattr(pg,'record_deployment_in_operator_transaction',None)),'operator-only writer missing')

    def test_cost_read_rechecks_current_actor_scope_source_and_does_not_bypass_rls(self):
        self._cost_read_case(borrowed=False)

    def test_using_transaction_cost_read_keeps_actual_auth_and_source_rechecks(self):
        self._cost_read_case(borrowed=True)

    def _cost_read_case(self,*,borrowed):
        from contextlib import contextmanager,nullcontext
        from test_recovery_lineage import Ledger
        from daon_user_api.authorization import AuthorizationError
        from decimal import Decimal
        pg=importlib.import_module('daon_user_api.operations_telemetry_postgres');ledger=Ledger()
        ledger.auth.auth_role='organization_admin'
        context=SimpleNamespace(**vars(ledger.context) if hasattr(ledger.context,'__dict__') else dict(
            tenant_id=ledger.context.tenant_id,workspace_id=ledger.context.workspace_id,actor_id=ledger.context.actor_id,
            trace_id=ledger.context.trace_id,policy_version=ledger.context.policy_version))
        context.scope='workspace_visible'
        log=[];deny=[False];after=[lambda:None]
        self.assertTrue(callable(getattr(pg.PostgresOperationsTelemetryRepository,'read_cost_receipts',None)),'M2-3 cost read missing')
        class Connection:
            info=ledger.info
            def cursor(inner,**kwargs):
                if not borrowed:return inner
                class Cursor:
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def execute(self,sql,params=()):return inner._execute(sql,params)
                return Cursor()
            def transaction(self):return nullcontext()
            def execute(inner,sql,params=()):
                if borrowed:raise AssertionError('BORROWED_DEFAULT_CURSOR_MUST_NOT_CHANGE')
                return inner._execute(sql,params)
            def _execute(self,sql,params=()):
                log.append((sql,params))
                if sql.startswith(('SET LOCAL ','SELECT set_config')):return SimpleNamespace()
                if '/* operations-cost:run */' in sql:return SimpleNamespace(fetchall=lambda:[('run-a','other-actor','notebook-a',['version-a'])])
                if '/* operations-cost:binding */' in sql:return SimpleNamespace(fetchone=lambda:(1,))
                if sql.startswith('SELECT NOT EXISTS (SELECT 1 FROM identity_auth_source_access'):return SimpleNamespace(fetchone=lambda:(not deny[0],))
                if sql.startswith(('SELECT canonical_json FROM source_versions','SELECT canonical_json FROM public.source_versions')):return SimpleNamespace(fetchone=lambda:({},))
                if sql.startswith("SELECT current_setting('app.notebook_id'"):return SimpleNamespace(fetchone=lambda:('',))
                if sql=='SELECT * FROM public.read_operations_cost_receipts(%s,%s)':
                    after[0]()
                    return SimpleNamespace(fetchall=lambda:[dict(run_id='run-a',actor_id='other-actor',model_attempt_id='attempt-a',
                        state='known',amount=Decimal('1.25'),currency='KRW',observed_at=NOW,received_at=NOW)])
                return ledger.execute(sql,params)
        class Pool:
            closed=False
            @contextmanager
            def connection(self,**kwargs):yield Connection()
            def close(self):pass
        with patch.object(pg,'ConnectionPool',return_value=Pool()):
            if borrowed:
                @contextmanager
                def transaction():yield Connection()
                self.assertTrue(callable(getattr(pg.PostgresOperationsTelemetryRepository,'using_transaction',None)),'borrowed transaction adapter missing')
                repo=pg.PostgresOperationsTelemetryRepository.using_transaction(transaction)
                pg.ConnectionPool.assert_not_called()
            else:repo=pg.PostgresOperationsTelemetryRepository('host=pg.invalid dbname=daon_user_s44_cost_test')
            self.addCleanup(repo.close)
            rows=repo.read_cost_receipts(context,('run-a',));self.assertEqual(rows[0]['amount'],Decimal('1.25'))
            self.assertEqual(set(rows[0]),{'run_id','actor_id','model_attempt_id','state','amount','currency','observed_at','received_at'})
            for mode in ('self','source','late-source','organization','non-manager','late-manager'):
                deny[0]=False;after[0]=lambda:None;context.scope='workspace_visible';ledger.workflow_state='active';ledger.auth.auth_role='organization_admin'
                if mode=='self':context.scope='self'
                if mode=='source':deny[0]=True
                if mode=='late-source':after[0]=lambda:deny.__setitem__(0,True)
                if mode=='organization':ledger.workflow_state='suspended'
                if mode=='non-manager':ledger.auth.auth_role='viewer'
                if mode=='late-manager':after[0]=lambda:setattr(ledger.auth,'auth_role','viewer')
                with self.subTest(mode=mode),self.assertRaises(AuthorizationError):repo.read_cost_receipts(context,('run-a',))
        self.assertFalse(any('row_security' in sql or 'bypass' in sql.lower() for sql,_ in log))

    def test_cost_sql_writers_and_manager_select_are_closed_without_changing_self_policy(self):
        from pathlib import Path
        sql=(Path(__file__).resolve().parents[1]/'migrations/versions/0071_operations_telemetry.sql').read_text(encoding='utf-8')
        for name in ('record_operations_cost_receipt','read_operations_cost_receipts','read_operations_deployment_receipt','record_operations_deployment_receipt'):
            self.assertIn('CREATE FUNCTION public.'+name+'(',sql)
        self.assertIn('CREATE POLICY operations_cost_receipts_manager_select',sql)
        self.assertIn('FOR SELECT TO daon_notebook_delete',sql)
        self.assertIn("public.registered_restore_actor_allowed(tenant_id,workspace_id,nullif(current_setting('app.actor_id',true),''))",sql)
        self.assertNotIn('CREATE OR REPLACE FUNCTION public.registered_restore_actor_allowed',sql)
        self.assertIn('OPERATIONS_COST_REPLAY',sql)
        self.assertNotIn('GRANT INSERT ON',sql)

    def test_receipt_replay_or_changed_attempt_rejected_in_same_transaction_port(self):
        module=api();pg=importlib.import_module("daon_user_api.operations_telemetry_postgres")
        self.assertTrue(callable(getattr(pg,"record_cost_in_transaction",None)),"M2-3 protected append missing")
        from decimal import Decimal
        import psycopg
        context=SimpleNamespace(tenant_id="tenant-a",workspace_id="workspace-a",actor_id="actor-a")
        receipt=module.make_cost_receipt(module.ReportedCost(Decimal("1.25"),"USD","reported-a"),{},provider_connection_id="provider-a",outcome="succeeded",observed_at=NOW)
        calls=[];seen=[]
        class Connection:
            def execute(self,sql,params=()):
                calls.append((sql,params))
                if seen:raise psycopg.errors.UniqueViolation("PRIVATE_SECRET_CANARY")
                seen.append(params);return SimpleNamespace(fetchone=lambda:(params[-1],))
        connection=Connection()
        pg.record_cost_in_transaction(connection,context,"run-a",receipt,model_attempt_id="attempt-a")
        for attempt in ("attempt-a","attempt-b"):
            with self.subTest(attempt=attempt),self.assertRaises(module.TelemetryUnavailable) as failed:
                pg.record_cost_in_transaction(connection,context,"run-a",receipt,model_attempt_id=attempt)
            self.assertEqual(str(failed.exception),"OPERATIONS_TELEMETRY_UNAVAILABLE")
        self.assertEqual(len(calls),3)
        self.assertTrue(all(sql.startswith("SELECT public.record_operations_cost_receipt(") for sql,_ in calls))
        self.assertEqual(calls[0][1][:4],("tenant-a","workspace-a","run-a","actor-a"))
        self.assertNotEqual(calls[0][1][-1],calls[-1][1][-1],"attempt도 receipt digest에 결속")

    def test_failure_or_cancellation_cost_not_zero(self):
        module=api();self.assertTrue(callable(getattr(module,"make_cost_receipt",None)),"M2-3 cost receipt producer missing")
        from decimal import Decimal
        reported=module.ReportedCost(Decimal("1.25"),"USD","reported-a")
        for outcome in ("failed","cancelled"):
            receipt=module.make_cost_receipt(reported,{"total_tokens":7},provider_connection_id="provider-a",outcome=outcome,observed_at=NOW)
            self.assertEqual((receipt.state,receipt.amount,receipt.currency),("uncertain",None,None))

    def test_currency_separated_and_price_version_required(self):
        module=api();self.assertTrue(callable(getattr(module,"make_cost_receipt",None)),"M2-3 cost receipt producer missing")
        from decimal import Decimal
        for currency in ("USD","KRW"):
            result=module.make_cost_receipt(module.ReportedCost(Decimal("1.000000001"),currency,"reported-a"),{},provider_connection_id="provider-a",outcome="succeeded",observed_at=NOW)
            self.assertEqual((result.amount,result.currency),(Decimal("1.000000001"),currency))
        with self.assertRaises(ValueError):module.CostReceipt("receipt-a","provider-a","price_card",Decimal("1"),"USD","a"*64,None,"known",NOW)

    def publisher(self,*,sink=None,reader=None):
        module=api();self.clock=[NOW];self.sink=sink or Sink(module,lambda:self.clock[0])
        publisher=module.WorkerObservationPublisher(self.sink,clock=lambda:self.clock[0],resources=reader or SimpleNamespace(read=lambda:module.ResourceValues()))
        self.addCleanup(publisher.stop)
        return module,publisher

    def test_worker_actual_poll_is_separate_from_process_heartbeat(self):
        module,p=self.publisher()
        p.start("document","document-worker-1","boot-a",NOW-timedelta(seconds=1))
        p.poll_started();self.clock[0]+=timedelta(seconds=3);p._publish()
        row=self.sink.samples[-1].sample
        self.assertEqual(row.poll_started_at,NOW);self.assertIsNone(row.poll_finished_at)
        self.assertIsNone(row.active_started_at)
        p.processing_started();self.clock[0]+=timedelta(seconds=2);p.poll_finished();p._publish()
        current=self.sink.samples[-1].sample
        self.assertEqual(current.poll_finished_at,self.clock[0]);self.assertIsNone(current.active_started_at)
        self.assertGreater(current.sequence,row.sequence)

    def test_long_job_not_ready_from_heartbeat(self):
        module,p=self.publisher();p.start("studio","studio-worker-1","boot-a",NOW-timedelta(seconds=1))
        p.poll_started();p.processing_started();self.clock[0]+=timedelta(seconds=90);p._publish()
        value=module.project_worker(self.sink.samples[-1],now=self.clock[0])
        self.assertEqual((value["state"],value["process_state"]),("fresh","processing"))
        self.assertIsNone(value["poll_finished_at"]);self.assertNotIn("ready",str(value))

    def test_stale_45_seconds_and_restart_new_boot(self):
        module,p=self.publisher();p.start("object","object-worker-1","boot-a",NOW-timedelta(seconds=2));p._publish()
        stored=self.sink.samples[-1]
        self.assertEqual(module.project_worker(stored,now=NOW+timedelta(seconds=45))["state"],"fresh")
        self.assertEqual(module.project_worker(stored,now=NOW+timedelta(seconds=46))["state"],"stale")
        p.stop();self.clock[0]+=timedelta(seconds=1)
        p.start("object","object-worker-1","boot-b",self.clock[0]);p._publish()
        self.assertEqual(self.sink.samples[-1].sample.boot_id,"boot-b")
        self.assertGreaterEqual(self.sink.samples[-1].sample.sequence,1)
        self.assertEqual(module.project_worker(None,now=self.clock[0],worker_kind="document")["state"],"not_collected")

    def test_replayed_or_future_sample_rejected(self):
        module,p=self.publisher();p.start("object","object-worker-1","boot-a",NOW-timedelta(seconds=2));p._publish()
        stored=self.sink.samples[-1]
        for change in ({"sequence":stored.sample.sequence},{"sequence":True},{"sequence":2**63},
                {"sequence":stored.sample.sequence+1,"observed_at":NOW+timedelta(seconds=6)},
                {"sequence":stored.sample.sequence+1,"observed_at":NOW-timedelta(seconds=1)},
                {"sequence":stored.sample.sequence+1,"process_started_at":NOW-timedelta(seconds=3)},
                {"boot_id":"old-boot","process_started_at":NOW-timedelta(seconds=10)}):
            with self.subTest(fields=tuple(change)),self.assertRaises(ValueError):
                module.validate_worker_sample(replace(stored.sample,**change),received_at=NOW,previous=[stored])

    def test_collector_failure_does_not_change_job_result(self):
        module=api()
        from test_document_processing_worker import FakeQueue,FakeProcessingService,FakeIndex,job
        from daon_user_api.document_processing_worker import DocumentProcessingWorker
        class Broken:
            def __getattr__(self,name):return lambda *a,**k:(_ for _ in ()).throw(RuntimeError("PRIVATE_SECRET_CANARY"))
        queue=FakeQueue(job());worker=DocumentProcessingWorker("document-worker-1",queue,FakeProcessingService(),FakeIndex(queue.events),telemetry=Broken())
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.events[-1],("complete",job().processing_run_id))
        self.assertNotIn("PRIVATE_SECRET_CANARY",str(queue.events))
        from test_studio_generation_worker import FakeQueue as StudioQueue,FakeRepository,job as studio_job
        from daon_user_api.studio_generation_worker import StudioGenerationWorker
        studio=StudioQueue(studio_job("evidence_report"))
        self.assertTrue(StudioGenerationWorker(studio,FakeRepository(),worker_id="studio-worker-1",telemetry=Broken()).run_once())
        self.assertEqual(studio.finished[0]["state"],"completed")
        from test_object_queue import FakeObjectStorage
        from daon_user_api.object_queue import DurableObjectWorker
        from daon_user_api.cloud_storage import CloudAccessContext
        import hashlib
        content=b"synthetic-one-byte-range";digest=hashlib.sha256(content).hexdigest();storage=FakeObjectStorage()
        storage.put_staged("staged",content,"text/plain",digest)
        record=SimpleNamespace(staging_key="staged",digest_sha256=digest,byte_size=len(content),content_type="text/plain",object_key="scoped-final")
        object_job=SimpleNamespace(object_id="a"*32);completed=[]
        def complete(context,claimed,owner,stored,**kwargs):
            self.assertEqual((claimed,owner,stored.digest_sha256,stored.byte_size),(object_job,"existing-owner",digest,len(content)))
            completed.append(stored);return object_job
        store=SimpleNamespace(claim=lambda *a,**k:object_job,get_object=lambda *a:record,complete=complete)
        result=DurableObjectWorker(store,storage,"existing-owner",telemetry=Broken()).run_once(CloudAccessContext("tenant-a","workspace-a","actor-a","object.write"))
        self.assertIs(result,object_job);self.assertEqual(len(completed),1)

    def test_cgroup_unlimited_or_denied_is_null(self):
        module=api()
        rows={"cpu.max":"150000 100000","memory.max":"10000000","memory.current":"2000"}
        reader=module.LocalResourceReader(_platform="linux",_read=lambda name:rows[name.rsplit("/",1)[-1]],
            _statvfs=lambda path:SimpleNamespace(f_bavail=3,f_frsize=4096))
        self.assertEqual(reader.read(),module.ResourceValues(1500,10000000,2000,12288))
        rows.update({"cpu.max":"max 100000","memory.max":"max","memory.current":"-1"})
        self.assertEqual(reader.read(),module.ResourceValues(None,None,None,12288))
        denied=module.LocalResourceReader(_platform="linux",_read=lambda _:(_ for _ in ()).throw(PermissionError("PRIVATE_PATH")),
            _statvfs=lambda _:(_ for _ in ()).throw(PermissionError()))
        self.assertEqual(denied.read(),module.ResourceValues())
        self.assertEqual(module.LocalResourceReader(_platform="win32").read(),module.ResourceValues())

    def test_stop_joins_publisher_and_no_secret_in_failure(self):
        module,p=self.publisher();waited=threading.Event();periods=[]
        def wait(stop,seconds):periods.append(seconds);waited.set();return stop.wait(seconds)
        p._wait=wait
        p.start("studio","studio-worker-1","boot-a",NOW-timedelta(seconds=1))
        self.assertTrue(waited.wait(2));self.assertEqual(periods,[15.0])
        p.stop();p.stop();self.assertFalse(p.running)
        self.assertEqual(self.sink.samples[-1].sample.last_safe_code,"WORKER_STOPPED")
        self.assertFalse(any(t.name=="operations-observation-publisher" for t in threading.enumerate()))

    def test_all_worker_poll_lifetimes_stop_after_actual_loop(self):
        from daon_user_api.document_processing_worker import DocumentProcessingWorker
        from daon_user_api.studio_generation_worker import StudioGenerationWorker
        from daon_user_api.object_queue import DurableObjectWorker
        from daon_user_api.cloud_storage import CloudAccessContext
        for kind in ("document","studio","object"):
            with self.subTest(kind=kind):
                events=[];stop=threading.Event()
                class Observer:
                    def start(self,*args):
                        self_test.assertEqual(args[:2],(kind,kind+"-worker-1"))
                        self_test.assertLessEqual(args[3],datetime.now(timezone.utc));events.append("start")
                    def poll_started(self):events.append("poll-start")
                    def poll_finished(self,**kwargs):events.append("poll-end")
                    def stop(self):events.append("stop")
                self_test=self;observer=Observer()
                def claim(*args,**kwargs):
                    events.append("claim");stop.set()
                    if kind=="object":worker._stop.set()
                    return None
                queue=SimpleNamespace(claim=claim)
                if kind=="document":
                    worker=DocumentProcessingWorker("document-worker-1",queue,None,None,telemetry=observer)
                    worker.run_forever(stop)
                elif kind=="studio":
                    worker=StudioGenerationWorker(queue,None,worker_id="studio-worker-1",telemetry=observer)
                    worker.run_forever(stop)
                else:
                    worker=DurableObjectWorker(queue,None,"existing-lease-owner",telemetry=observer,telemetry_worker_id="object-worker-1")
                    worker.start((CloudAccessContext("tenant-a","workspace-a","actor-a","object.write"),))
                    self.assertTrue(stop.wait(2));worker.shutdown(timeout=2)
                    self.assertFalse(worker.running)
                self.assertEqual(events,["start","poll-start","claim","poll-end","stop"])

    def test_actual_worker_processing_hooks_publish_active_then_finished_without_ready_inference(self):
        from test_document_processing_worker import FakeQueue,FakeIndex,job,result
        from test_studio_generation_worker import FakeQueue as StudioQueue,job as studio_job
        from daon_user_api.document_processing_worker import DocumentProcessingWorker
        from daon_user_api.studio_generation_worker import StudioGenerationWorker
        from daon_user_api.object_queue import DurableObjectWorker,StoredObject
        from daon_user_api.cloud_storage import CloudAccessContext
        for kind in ("document","studio","object"):
            with self.subTest(kind=kind):
                module,p=self.publisher();active=[];completed=[]
                p.start(kind,kind+"-worker-1","boot-active",NOW-timedelta(seconds=1))
                def processing():
                    self.clock[0]+=timedelta(seconds=90);p._publish()
                    sample=self.sink.samples[-1]
                    active.append(sample.sample.active_started_at)
                    self.assertEqual(sample.sample.poll_started_at,NOW)
                    self.assertIsNone(sample.sample.poll_finished_at)
                    projected=module.project_worker(sample,now=self.clock[0])
                    self.assertEqual((projected["state"],projected["process_state"]),("fresh","processing"))
                if kind=="document":
                    queue=FakeQueue(job())
                    def process(*args,**kwargs):processing();return result()
                    worker=DocumentProcessingWorker("document-worker-1",queue,SimpleNamespace(process_existing=process),
                        FakeIndex(queue.events),telemetry=p)
                    invoke=worker.run_once
                elif kind=="studio":
                    queue=StudioQueue(studio_job("evidence_report"))
                    def generate(*args,**kwargs):
                        processing();return {"studio_output_id":"output-1","output_version_id":"version-1"},False
                    worker=StudioGenerationWorker(queue,SimpleNamespace(create_generation=generate),worker_id="studio-worker-1",telemetry=p)
                    invoke=worker.run_once
                else:
                    claimed=SimpleNamespace(object_id="a"*32)
                    record=SimpleNamespace(staging_key="staged",digest_sha256="a"*64,byte_size=1,content_type="text/plain",object_key="scoped-final")
                    def promote(*args,**kwargs):
                        processing();return StoredObject("scoped-final","a"*64,1,"text/plain","etag",None)
                    queue=SimpleNamespace(claim=lambda *a,**k:claimed,get_object=lambda *a:record,
                        complete=lambda *a,**k:completed.append(claimed) or claimed)
                    worker=DurableObjectWorker(queue,SimpleNamespace(promote=promote),"existing-lease-owner",telemetry=p)
                    invoke=lambda:worker.run_once(CloudAccessContext("tenant-a","workspace-a","actor-a","object.write"))
                try:
                    self.assertTrue(invoke());self.assertEqual(active,[NOW])
                    p._publish();finished=self.sink.samples[-1].sample
                    self.assertEqual(finished.poll_finished_at,NOW+timedelta(seconds=90))
                    self.assertIsNone(finished.active_started_at)
                    if kind=="document":self.assertEqual(queue.events[-1],("complete",job().processing_run_id))
                    elif kind=="studio":self.assertEqual(queue.finished[0]["state"],"completed")
                    else:self.assertEqual(completed,[claimed])
                finally:p.stop()
                self.assertFalse(p.running)
                self.assertEqual(self.sink.samples[-1].sample.last_safe_code,"WORKER_STOPPED")

    def test_postgres_sample_uses_protected_function_and_database_received_time(self):
        if importlib.util.find_spec("daon_user_api.operations_telemetry_postgres") is None:
            self.fail("M2-1 protected PostgreSQL storage consumer missing")
        from dataclasses import asdict
        from contextlib import contextmanager
        import psycopg
        module=api();pg=importlib.import_module("daon_user_api.operations_telemetry_postgres")
        sample=module.WorkerSample("document-worker-1","boot-a","document",NOW-timedelta(seconds=2),1,NOW)
        calls=[];failure=[False];closes=[]
        class Connection:
            def execute(inner,sql,params=()):
                calls.append((sql,params))
                if sql.startswith("SELECT * FROM public.record_operations_worker_sample"):
                    self.assertEqual(params,tuple(asdict(sample).values()))
                    if failure[0]:raise psycopg.OperationalError("PRIVATE_SECRET_CANARY")
                    return SimpleNamespace(fetchone=lambda:{**asdict(sample),"received_at":NOW+timedelta(seconds=1)})
                self.assertTrue(sql.startswith("SET LOCAL "));return SimpleNamespace()
            def transaction(inner):return __import__("contextlib").nullcontext()
        class Pool:
            closed=False
            @contextmanager
            def connection(self,**kwargs):yield Connection()
            def close(self):closes.append(True)
        with patch.object(pg,"ConnectionPool",return_value=Pool()):
            repository=pg.PostgresOperationsTelemetryRepository("host=pg.invalid dbname=daon_user_s44_telemetry_test")
            stored=repository.record_worker_sample(sample)
            self.assertEqual(stored.sample,sample);self.assertEqual(stored.received_at,NOW+timedelta(seconds=1))
            before=len(calls);failure[0]=True
            with self.assertRaises(module.TelemetryUnavailable) as failed:repository.record_worker_sample(sample)
            self.assertEqual(str(failed.exception),"OPERATIONS_TELEMETRY_UNAVAILABLE")
            self.assertEqual(sum(sql.startswith("SELECT * FROM public.record_operations_worker_sample") for sql,_ in calls[before:]),1)
            repository.close();repository.close();self.assertEqual(closes,[True])

    def test_postgres_expected_workers_read_rechecks_actual_view_and_org(self):
        from contextlib import contextmanager,nullcontext
        from dataclasses import asdict
        from test_recovery_lineage import Ledger
        from daon_user_api.authorization import AuthorizationError
        pg=importlib.import_module("daon_user_api.operations_telemetry_postgres");module=api();ledger=Ledger()
        ledger.auth.auth_role="organization_admin";log=[];on_read=[lambda:None]
        stored=module.WorkerSample("document-worker-1","boot-a","document",NOW-timedelta(seconds=2),1,NOW)
        class Connection:
            info=ledger.info
            def cursor(inner,**kwargs):return ledger
            def transaction(inner):return nullcontext()
            def execute(inner,sql,params=()):
                log.append((sql,params))
                if sql.startswith("SET LOCAL ") or sql.startswith("SELECT set_config"):return SimpleNamespace()
                if sql=="SELECT clock_timestamp() AS checked_at":return SimpleNamespace(fetchone=lambda:dict(checked_at=NOW))
                if sql=="SELECT * FROM public.read_operations_worker_samples(%s)":
                    self.assertEqual(params,(["document-worker-1","studio-worker-1","object-worker-1"],));on_read[0]()
                    return SimpleNamespace(fetchall=lambda:[{**asdict(stored),"received_at":NOW}])
                raise AssertionError("UNEXPECTED_TELEMETRY_SQL")
        class Pool:
            closed=False
            @contextmanager
            def connection(self,**kwargs):yield Connection()
            def close(self):pass
        with patch.object(pg,"ConnectionPool",return_value=Pool()):
            repository=pg.PostgresOperationsTelemetryRepository("host=pg.invalid dbname=daon_user_s44_telemetry_test");self.addCleanup(repository.close)
            result=repository.read_worker_samples(ledger.context)
            self.assertEqual([r["worker_kind"] for r in result],["document","studio","object"])
            self.assertEqual([r["state"] for r in result],["fresh","not_collected","not_collected"])
            self.assertFalse(any(key in str(result) for key in ("worker_id","boot_id","source-a","SECRET")))
            for mode in ("suspended","revoked","late-revoked"):
                with self.subTest(mode=mode):
                    ledger.workflow_state="active";ledger.auth.auth_active=True;on_read[0]=lambda:None
                    if mode=="suspended":ledger.workflow_state="suspended"
                    elif mode=="revoked":ledger.auth.auth_active=False
                    else:on_read[0]=lambda:setattr(ledger.auth,"auth_active",False)
                    with self.assertRaises(AuthorizationError) as denied:repository.read_worker_samples(ledger.context)
                    self.assertEqual(denied.exception.http_status,403)
            self.assertFalse(any(sql.startswith(("INSERT","UPDATE","DELETE")) for sql,_ in log))

    def test_worker_job_and_factory_failure_never_log_private_error(self):
        module=api();pg=importlib.import_module("daon_user_api.operations_telemetry_postgres")
        with self.assertLogs(module._LOG,level="WARNING") as captured:
            with patch.object(pg,"PostgresOperationsTelemetryRepository",side_effect=ValueError("PRIVATE_SECRET_CANARY")):
                self.assertIsNone(module.worker_publisher("synthetic-private-ref"))
        self.assertNotIn("PRIVATE_SECRET_CANARY"," ".join(captured.output))

    def test_owned_pool_connection_failure_is_value_free_before_library_logging(self):
        import psycopg
        pg=importlib.import_module("daon_user_api.operations_telemetry_postgres")
        connection=getattr(pg,"_TelemetryConnection",None)
        self.assertIsNotNone(connection,"pool background logger 전 safe error 경계 필요")
        with patch.object(psycopg.Connection,"connect",side_effect=psycopg.OperationalError("PRIVATE_SECRET_HOST_CANARY")):
            with self.assertRaises(psycopg.OperationalError) as failed:connection.connect("synthetic-no-network-dsn")
        self.assertEqual(str(failed.exception),"OPERATIONS_TELEMETRY_UNAVAILABLE")

    def test_malformed_telemetry_dsn_rejects_before_pool_creation(self):
        module=api();pg=importlib.import_module("daon_user_api.operations_telemetry_postgres")
        with patch.object(pg,"ConnectionPool") as pool:
            with self.assertRaises(module.TelemetryUnavailable):pg.PostgresOperationsTelemetryRepository("synthetic-no-connection")
            pool.assert_not_called()

    def test_object_drain_timeout_stops_owned_publisher_without_claiming_worker_stopped(self):
        module,p=self.publisher()
        from daon_user_api.object_queue import DurableObjectWorker,ObjectQueueError,StoredObject
        from daon_user_api.cloud_storage import CloudAccessContext
        entered=threading.Event();release=threading.Event();completed=[]
        job=SimpleNamespace(object_id="a"*32)
        record=SimpleNamespace(staging_key="staged",digest_sha256="a"*64,byte_size=1,content_type="text/plain",object_key="scoped-final")
        def promote(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise AssertionError("TEST_PROMOTE_NOT_RELEASED")
            return StoredObject("scoped-final","a"*64,1,"text/plain","etag",None)
        store=SimpleNamespace(claim=lambda *a,**k:job,get_object=lambda *a:record,
            complete=lambda *a,**k:completed.append(True) or job)
        worker=DurableObjectWorker(store,SimpleNamespace(promote=promote),"existing-lease-owner",telemetry=p,telemetry_worker_id="object-worker-1")
        with patch.object(module,"process_started_at",return_value=NOW-timedelta(seconds=1)):
            worker.start((CloudAccessContext("tenant-a","workspace-a","actor-a","object.write"),))
            try:
                self.assertTrue(entered.wait(2));p._publish()
                with self.assertRaises(ObjectQueueError):worker.shutdown(timeout=0.01)
                self.assertTrue(worker.running);self.assertFalse(p.running,"작업 timeout 후 별도 non-daemon publisher 잔류 금지")
                self.assertNotEqual(self.sink.samples[-1].sample.last_safe_code,"WORKER_STOPPED")
                self.assertEqual(completed,[])
            finally:release.set();worker.shutdown(timeout=2)
        self.assertEqual(completed,[True]);self.assertFalse(worker.running)

    def test_migration_draft_only_new_three_tables_protected_write_and_down_guard(self):
        # SQL 원문/upgrade/down static proof일 뿐 PG parser/up/down·실효권한 실행 PASS가 아니다.
        import ast,re
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]/"migrations"/"versions"
        sql=(root/"0071_operations_telemetry.sql").read_text(encoding="utf-8")
        py=(root/"0071_operations_telemetry.py").read_text(encoding="utf-8");tree=ast.parse(py)
        assignments={node.targets[0].id:ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)}
        self.assertEqual((assignments["revision"],assignments["down_revision"]),("0071","0070"))
        names=re.findall(r"CREATE TABLE public\.([a-z_]+)",sql)
        self.assertEqual(names,["operations_worker_observations","operations_deployment_receipts","operations_cost_receipts"])
        for name in names:
            self.assertIn(f"ALTER TABLE public.{name} OWNER TO daon_notebook_delete",sql)
            self.assertIn(f"REVOKE ALL ON public.{name} FROM PUBLIC,daon_app",sql)
            self.assertIn(f"EXISTS(SELECT 1 FROM public.{name})",py)
        self.assertIn("FORCE ROW LEVEL SECURITY",sql)
        self.assertIn("p_sequence<=previous.sequence",sql);self.assertIn("NEW.sequence<=OLD.sequence",sql)
        self.assertIn("NEW.received_at=clock_timestamp()",sql);self.assertIn("'5 seconds'",sql)
        self.assertNotRegex(sql,r"GRANT\s+(?:ALL|INSERT|UPDATE|DELETE|TRUNCATE|SELECT)\s+ON\s+(?:TABLE\s+)?public\.")
        self.assertNotRegex(sql,r"CREATE ROLE|ALTER ROLE|GRANT [a-z_]+ TO daon_app|CASCADE")
