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


class OperationsTelemetryTests(unittest.TestCase):
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
