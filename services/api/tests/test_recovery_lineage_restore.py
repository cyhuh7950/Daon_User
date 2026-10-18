"""R3 Windows 실보존본 + SQL/Object 외부 경계 대역. 실제 PG/MinIO 검증은 아니다."""
from __future__ import annotations

import copy
import hashlib
import importlib
import io
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import struct

from daon_user_api.recovery import RecoveryError
from daon_user_api.object_queue import (PostgresObjectQueueStore, MinioObjectStorageAdapter, ObjectRecord,
    ObjectJob, ObjectSubmission, StagedObject, StoredObject, ObjectStorageError)


class LineageRestoreTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((Path(__file__).parents[1] / "src/daon_user_api/recovery_lineage_restore.py").is_file(),
                        "R3 concrete materializer implementation missing")
        self.m = importlib.import_module("daon_user_api.recovery_lineage_restore")
        self.case = RestoreFixture(self, self.m)

    def test_restored_original_object_id_hash_and_version_fk(self):
        result = self.case.run()
        self.assertEqual(result.last_phase, 7)
        self.assertEqual(result.state, "materialized")
        self.assertEqual(len(self.case.journal.rows), 14)
        self.assertEqual([r["outcome"] for r in self.case.journal.rows], ["intent", "succeeded"]*7)
        for original in self.case.metadata["source_versions"]:
            actual = self.case.database.rows["source_versions"][original["record_id"]]
            self.assertEqual(actual, original)
            obj = self.case.objects.records[original["object_id"]]
            self.assertEqual(obj.status, "completed")
            self.assertEqual(self.case.storage.get(obj.object_key), self.case.content[original["object_id"]])
            self.assertEqual(obj.digest_sha256, hashlib.sha256(self.case.content[obj.object_id]).hexdigest())
        self.assertEqual(len(self.case.objects.records), 2)
        self.assertNotIn("NEVER_COPY", repr(self.case.database.rows))

    def test_source_state_base_and_real_transition_replay_not_past_audit(self):
        self.case.run()
        source = self.case.database.rows["sources"]["source-a"]
        self.assertEqual((source["state"], source["version"]), ("ready", 5))
        self.assertEqual(self.case.database.source_base, ("registered", 1, None))
        events = self.case.database.transitions
        self.assertEqual(len(events), 4)
        self.assertTrue(all(r["actor_id"] == self.case.context.actor_id for r in events))
        self.assertTrue(all(r["trace_id"] == self.case.context.trace_id for r in events))
        self.assertTrue(all(r["transition_id"].startswith("restore-replay-") for r in events))
        self.assertFalse(any(r["transition_id"].startswith("transition-") for r in events))
        self.assertEqual(len(self.case.database.restore_audits), 7)
        self.assertTrue(all(row["actor_id"] == self.case.context.actor_id for row in self.case.database.restore_audits))
        self.case.database.transitions[0]["actor_id"] = "actor-original"
        with self.assertRaises(RecoveryError): self.case.run()

    def test_source_nonnull_previous_or_invalid_base_is_rejected(self):
        for field, value in (("previous_version_id", "fabricated-parent"), ("state", "processing"), ("version", 0)):
            with self.subTest(field=field):
                meta = copy.deepcopy(self.case.metadata); meta["sources"][0][field] = value
                with self.assertRaises(RecoveryError): self.m.build_restore_plan(self.case.scope, meta)
        self.assertEqual(self.case.database.writes, 0)

    def test_sourceversion_chain_exact_and_no_fabricated_parent(self):
        for change in ("gap", "foreign", "digest", "extra-binding"):
            with self.subTest(change=change):
                meta = copy.deepcopy(self.case.metadata)
                if change == "gap": meta["source_versions"].pop(0)
                elif change == "foreign": meta["source_versions"][0]["source_id"] = "foreign"
                elif change == "digest": meta["source_versions"][0]["digest_sha256"] = "f"*64
                else: meta["notebook_bindings"].append({**meta["notebook_bindings"][0], "version_id": "prior-version"})
                with self.assertRaises(RecoveryError): self.m.build_restore_plan(self.case.scope, meta)
        self.case.run()
        self.assertEqual(self.case.database.version_order, ["prior-version", "root-version"])
        self.assertEqual(list(self.case.database.rows["notebook_bindings"].values()), self.case.metadata["notebook_bindings"])

    def test_target_object_failure_after_commit_never_completed(self):
        self.case.storage.fail_promote = True
        with self.assertRaises(self.m.MaterializationError) as caught: self.case.run()
        self.assertEqual(caught.exception.outcome, "uncertain")
        self.assertEqual(caught.exception.phase, 2)
        self.assertTrue(self.case.objects.records)  # 큐 DB 등록 후 Object 승격 실패, 삭제하지 않음.
        self.assertEqual(self.case.journal.rows[-1]["outcome"], "uncertain")
        self.assertEqual(self.case.database.rows["source_versions"], {})
        self.assertEqual(self.case.storage.deletes, 0)

    def test_uncertain_receipt_restart_never_recreates_or_deletes_target(self):
        self.case.journal.lose_result_phase = 2
        with self.assertRaises(self.m.MaterializationError): self.case.run()
        self.assertEqual(self.case.journal.rows[-1]["outcome"], "intent")
        before = (self.case.database.writes, len(self.case.objects.records), self.case.storage.puts)
        with self.assertRaises(self.m.MaterializationError): self.case.run()
        self.assertEqual((self.case.database.writes, len(self.case.objects.records), self.case.storage.puts), before)
        self.assertEqual(self.case.storage.deletes, 0)

    def test_target_application_and_outbound_stay_disabled(self):
        for enabled in ("application_enabled", "outbound_enabled"):
            with self.subTest(enabled=enabled):
                old = self.case.observation
                self.case.observation = replace(old, **{enabled: True})
                with self.assertRaises(RecoveryError): self.case.run()
                self.case.observation = old
                self.assertEqual(self.case.database.writes, 0)
        original = self.case.database.functions
        self.case.database.functions = [(*row[:-1], "tampered-protected-definition") for row in original]
        try:
            with self.assertRaises(RecoveryError): self.case.run()
        finally: self.case.database.functions = original
        self.assertEqual(self.case.database.writes, 0)
        self.case.source.physical_digest = "f"*64
        try:
            with self.assertRaises(RecoveryError): self.case.run()
        finally: self.case.source.physical_digest = None
        self.assertEqual(self.case.database.writes, 0)
        self.case.run()
        writes = self.case.database.writes
        self.case.observation = replace(self.case.observation, database_empty=False, bucket_empty=False)
        self.assertEqual(self.case.run().state, "materialized")
        self.assertEqual(self.case.database.writes, writes)
        self.assertFalse(self.case.observation.application_enabled or self.case.observation.outbound_enabled)

    def test_bounded_stream_over_4mib_and_total_512mib(self):
        self.assertIsNotNone(getattr(self.m,"BoundedLineageObjects",None),"R3 bounded streaming missing")
        self.m._object_budget([{"byte_size":512*1024*1024}])
        with self.assertRaises(RecoveryError): self.m._object_budget([{"byte_size":512*1024*1024+1}])
        with self.assertRaises(RecoveryError): self.m._object_budget([{"byte_size":True}])
        backend=self.case.storage
        transport,ioport=self.streams(backend)
        content=b"x"*(6*1024*1024+17)
        digest=hashlib.sha256(content).hexdigest()
        result=transport.upload(self.case.cloud,"a"*32,len(content),digest,"application/octet-stream",
            lambda sink:[sink.write(content[i:i+32771]) for i in range(0,len(content),32771)])
        self.assertEqual((result.byte_size,result.digest_sha256),(len(content),digest))
        self.assertLessEqual(ioport.maximum_frame,65536)
        self.assertEqual(ioport.active,set())
        self.assertEqual(backend.data[result.key],content)
        # 최종 hash 불일치는 새 staging 성공 반환/queue 등록을 만들지 않는다.
        before=backend.puts
        with self.assertRaises(RecoveryError): transport.upload(self.case.cloud,"b"*32,1,"f"*64,"text/plain",lambda sink:sink.write(b"x"))
        self.assertEqual(backend.puts,before)
        self.case.grow_capture(6*1024*1024+17)
        self.assertEqual(self.case.run().state,"materialized")

    def streams(self,backend,**kwargs):
        ioport=UploadIoDouble(backend,**kwargs)
        source=self.m._MinioSource("object.invalid",backend._bucket,"us-east-1",True,
            SimpleNamespace(access_key="synthetic-memory-access",secret_key="synthetic-memory-secret",session_token=None),None)
        return self.m.BoundedLineageObjects(source,_io=ioport,_clock=lambda:ioport.time,timeout=.1 if ioport.stalled else 1.),ioport

    def test_bounded_child_stall_cancel_and_cleanup_uncertain(self):
        self.assertIsNotNone(getattr(self.m,"BoundedLineageObjects",None),"R3 bounded streaming missing")
        for mode in ("stalled","cancelled","uncertain"):
            with self.subTest(mode=mode):
                transport,ioport=self.streams(self.case.storage,stalled=mode!="cancelled",uncertain=mode=="uncertain")
                transport.cancelled=lambda:mode=="cancelled" and ioport.started>0
                with self.assertRaises(RecoveryError): transport.upload(self.case.cloud,"c"*32,1,hashlib.sha256(b"x").hexdigest(),"text/plain",lambda sink:sink.write(b"x"))
                self.assertTrue(all(pid==12345 for pid in ioport.signalled))
                if mode=="uncertain":
                    started=ioport.started
                    with self.assertRaises(RecoveryError): transport.verify(self.case.cloud,"c"*32,1,hashlib.sha256(b"x").hexdigest(),"text/plain")
                    self.assertEqual(ioport.started,started)
                else: self.assertEqual(ioport.active,set())
        transport,ioport=self.streams(self.case.storage)
        interrupted=KeyboardInterrupt()
        ioport.poll=lambda pid: (_ for _ in ()).throw(interrupted)
        with self.assertRaises(KeyboardInterrupt) as caught:
            transport.upload(self.case.cloud,"d"*32,1,"a"*64,"text/plain",lambda sink:(_ for _ in ()).throw(RecoveryError("RESOURCE_UNAVAILABLE",503)))
        self.assertIs(caught.exception,interrupted)
        self.assertTrue(transport.uncertain)
        self.assertIsNotNone(transport.owned_uncertain)

    def test_bounded_frame_secret_error_and_unsupported_windows(self):
        self.assertIsNotNone(getattr(self.m,"BoundedLineageObjects",None),"R3 bounded streaming missing")
        for mode in ("oversize","truncated","error"):
            transport,ioport=self.streams(self.case.storage,response_mode=mode)
            with self.subTest(mode=mode), self.assertRaises(RecoveryError) as caught:
                transport.verify(self.case.cloud,"a"*32,1,"a"*64,"text/plain")
            self.assertNotIn("secret-canary",str(caught.exception))
            self.assertEqual(ioport.active,set())
        transport,ioport=self.streams(self.case.storage)
        ioport.platform_supported=False
        with self.assertRaises(RecoveryError): transport.verify(self.case.cloud,"a"*32,1,"a"*64,"text/plain")
        self.assertEqual(ioport.started,0)
        if self.m.os.name=="nt":
            with self.assertRaises(RecoveryError) as unsupported:
                self.m.BoundedLineageObjects.from_storage(self.case.storage,region="us-east-1")
            self.assertEqual(unsupported.exception.status,503)

    def test_catalog_protection_properties_and_logical_identity(self):
        self.case.database.require_logical_catalog=True
        self.case.run()
        before=self.case.database.writes
        # cluster OID는 fixture의 물리 값일 뿐 logical query 결과/지문에는 포함0.
        self.case.database.role_oid=98765
        self.case.run()
        self.assertEqual(self.case.database.writes,before)
        original=copy.deepcopy(self.case.database.catalog)
        self.case.database.catalog[0][7][0][2]=False
        with self.assertRaises(RecoveryError): self.case.run()
        self.case.database.catalog=original
        functions=self.case.database.functions
        self.case.database.functions=[(*row[:5],[["PUBLIC","daon_app","EXECUTE",False]],*row[6:]) for row in functions]
        with self.assertRaises(RecoveryError): self.case.run()

    def test_new_request_trace_replays_original_execution_evidence(self):
        self.case.run()
        original_trace=self.case.context.trace_id; original_metadata=copy.deepcopy(self.case.metadata)
        self.case.context=replace(self.case.context,trace_id="trace-new-request")
        writes=self.case.database.writes
        self.assertEqual(self.case.run().state,"materialized")
        self.assertEqual(self.case.database.writes,writes)
        self.assertTrue(all(row["trace_id"]==original_trace for row in self.case.database.restore_audits))
        self.assertEqual(self.case.store.verify(self.case.scope,expected_receipt=self.case.receipt).metadata,original_metadata)

    def test_operator_global_eight_tables_reject_hidden_rows_on_reentry(self):
        self.case.run()
        writes=self.case.database.writes
        for table in (*self.m._TABLES,"object_records","canon_state_transitions","canon_transition_attempts"):
            for foreign_scope in (True,False):
                with self.subTest(table=table,foreign_scope=foreign_scope):
                    self.case.database.extra_rows[table]=(1,int(foreign_scope))
                    with self.assertRaises(RecoveryError): self.case.run()
                    self.assertEqual(self.case.database.writes,writes)
                    self.case.database.extra_rows.clear()
        self.assertEqual(self.case.run().state,"materialized")
        self.assertEqual(self.case.database.writes,writes)

    def test_operator_visibility_and_actual_queue_transaction_role_required(self):
        self.case.database.operator_role=("limited_operator",False,False)
        with self.assertRaises(RecoveryError): self.case.run()
        self.assertEqual(self.case.database.writes,0)
        self.case.database.operator_role=("s44_operator",False,True)
        self.case.database.global_read_denied=True
        with self.assertRaises(RecoveryError): self.case.run()
        self.assertEqual(self.case.database.writes,0)
        self.case.database.global_read_denied=False
        for role in (("postgres",True,True),("daon_app",True,False),("daon_app",False,True),("other_app",False,False)):
            with self.subTest(role=role):
                self.case.objects.role=role
                before=self.case.storage.puts
                with self.assertRaises(RecoveryError): self.case.run()
                self.assertEqual(self.case.storage.puts,before)
        self.case.objects.role=("daon_app",False,False)
        self.assertEqual(self.case.run().state,"materialized")

    def test_copy_sixty_seconds_uses_actual_completion_time_and_finite_lease(self):
        started=self.case.now
        def delayed():
            self.case.now+=timedelta(seconds=60)
            self.case.observation=replace(self.case.observation,observed_at=self.case.now)
        self.case.target.streams.io.copy_ready_hook=delayed
        self.assertEqual(self.case.run().state,"materialized")
        self.assertEqual(self.case.objects.completed_times,[started+timedelta(seconds=60),started+timedelta(seconds=120)])
        self.assertTrue(all(60<lease<=3600 for lease in self.case.objects.leases))

    def test_copy_expired_or_changed_claim_owner_never_completed(self):
        self.lost_claim("expired")

    def test_copy_changed_claim_owner_never_completed(self):
        self.lost_claim("owner")

    def test_queue_role_is_checked_inside_each_normal_transaction(self):
        queue=self.case.target.queue
        self.assertIsNot(queue,self.case.objects)
        for role in (("daon_app",False,False),("postgres",True,True),("daon_app",False,False)):
            self.case.objects.role=role
            if role[0]=="postgres":
                with self.assertRaises(RecoveryError):
                    with queue._transaction(self.case.cloud): self.fail("unsafe queue transaction yielded")
            else:
                with queue._transaction(self.case.cloud): pass
        # 원 store/공유 연결에 역할 변경/transaction hook 설치0.
        with self.case.objects._transaction(self.case.cloud) as connection:
            self.assertIs(connection,self.case.objects)

    def test_copy_failure_uses_actual_failure_clock(self):
        cloud=self.case.cloud; queue=self.case.target.queue; identity="a"*32
        content=self.case.content[identity]; digest=hashlib.sha256(content).hexdigest()
        staged=self.case.storage.put_staged(self.m.ObjectKeyPolicy().staging_key(cloud,"source",identity),content,"text/plain",digest)
        queue.register_object_job(cloud,object_id=identity,area="source",staged=staged,
            final_key=self.m.ObjectKeyPolicy().final_key(cloud,"source",identity),trace_id=self.case.context.trace_id,
            idempotency_key="failure-clock",max_attempts=1)
        started=self.case.now; worker_id="restore-clock-worker"
        job=queue.claim(cloud,worker_id,now=started,lease_seconds=330)
        def delayed_failure(*args,**kwargs):
            self.case.now=started+timedelta(seconds=60)
            raise ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE",retryable=True)
        worker=self.m._RestoreObjectWorker(queue,SimpleNamespace(promote=delayed_failure),worker_id,
            retry_policy=self.m.RetryPolicy(max_attempts=1),clock=lambda:self.case.now)
        self.assertEqual(worker.process_claimed(cloud,job,now=started).state,"dead_letter")
        self.assertEqual(self.case.objects.failed_times,[started+timedelta(seconds=60)])
        self.assertEqual(self.case.objects.completed_times,[])

    def lost_claim(self,change):
        job_id=[]
        def lost():
            identity=next(k for k,r in self.case.objects.jobs.items() if r.state=="leased")
            job=self.case.objects.jobs[identity]; job_id.append(identity)
            if change=="expired": self.case.now=job.lease_until+timedelta(seconds=1)
            else: self.case.objects.jobs[identity]=replace(job,lease_owner="another-worker")
            self.case.observation=replace(self.case.observation,observed_at=self.case.now)
        self.case.target.streams.io.copy_ready_hook=lost
        with self.assertRaises(self.m.MaterializationError): self.case.run()
        self.assertTrue(job_id)
        self.assertEqual(self.case.objects.records[job_id[0]].status,"pending")
        self.assertNotEqual(self.case.objects.jobs[job_id[0]].state,"completed")
        self.assertEqual(self.case.journal.rows[-1]["outcome"],"uncertain")


    def test_upload_rechecks_after_large_capture_verification(self):
        read=self.case.store.read_object
        revoked=[False]
        def changed(*args,**kwargs):
            revoked[0]=True
            return read(*args,**kwargs)
        self.case.store.read_object=changed
        self.case.current_hook=lambda: (_ for _ in ()).throw(RecoveryError("CURRENT_ACCESS_DENIED",403)) if revoked[0] else None
        with self.assertRaises(RecoveryError): self.case.run()
        self.assertEqual(self.case.storage.puts,0)
        self.assertEqual(self.case.objects.records,{})
        self.assertEqual(self.case.journal.rows[-1]["outcome"],"uncertain")

    def test_upload_expiry_after_verification_blocks_first_write(self):
        original=self.case.store.read_object
        def expired(*args,**kwargs):
            self.case.now=self.case.binding.preview_expires_at+timedelta(seconds=1)
            return original(*args,**kwargs)
        self.case.store.read_object=expired
        with self.assertRaises(RecoveryError): self.case.run()
        self.assertEqual(self.case.storage.puts,0)
        self.assertEqual(self.case.objects.records,{})

    def test_copy_rechecks_after_staged_readback(self):
        revoked=[False]
        self.case.target.streams.io.copy_ready_hook=lambda:revoked.__setitem__(0,True)
        self.case.current_hook=lambda: (_ for _ in ()).throw(RecoveryError("CURRENT_ACCESS_DENIED",403)) if revoked[0] else None
        with self.assertRaises(RecoveryError): self.case.run()
        self.assertEqual(self.case.storage.puts,1)
        self.assertFalse(any(key.startswith(self.case.context.tenant_id+"/") for key in self.case.storage.data))
        self.assertFalse(any(row.status=="completed" for row in self.case.objects.records.values()))
        self.assertEqual(self.case.journal.rows[-1]["outcome"],"uncertain")

    def test_current_other_manager_uses_original_archive_not_original_authority(self):
        scope=self.case.scope
        self.case.context=replace(self.case.context,actor_id="manager-current")
        self.case.destination=replace(self.case.destination,actor_id=self.case.context.actor_id)
        self.case.capture_case.ledger.auth.auth_role="organization_admin"
        original_auth=self.case.capture_case.ledger.auth.execute
        def authorship(sql,params=()):
            if "FROM identity_users" in sql and params[0]==scope.actor_id: return Cursor([("suspended",)])
            return original_auth(sql,params)
        self.case.capture_case.ledger.auth.execute=authorship
        self.case.capture_case.ledger.auth.queries.clear()
        self.assertEqual(self.case.run().state,"materialized")
        self.assertEqual(self.case.database.rows["sources"]["source-a"]["created_by"],scope.actor_id)
        identity_actors=[params[0] for sql,params in self.case.capture_case.ledger.auth.queries if "FROM identity_users" in sql]
        self.assertTrue(identity_actors and set(identity_actors)=={"manager-current"})
        self.assertEqual(self.case.scope,scope)
        self.assertEqual(self.case.store.verify(scope,expected_receipt=self.case.receipt).metadata,self.case.metadata)
        self.assertTrue(all(row["actor_id"]=="manager-current" for row in self.case.database.restore_audits))
        self.case.capture_case.ledger.revoked.add("root-version")
        with self.assertRaises(RecoveryError): self.case.run()
        self.case.capture_case.ledger.revoked.clear()
        self.case.capture_case.ledger.workflow_state="suspended"
        with self.assertRaises(RecoveryError): self.case.run()
        self.case.capture_case.ledger.workflow_state="active"
        self.case.capture_case.ledger.auth.auth_role="viewer"
        with self.assertRaises(RecoveryError): self.case.run()
        self.case.capture_case.ledger.auth.auth_role="organization_admin"
        self.case.destination=replace(self.case.destination,actor_id="registry-other")
        with self.assertRaises(RecoveryError): self.case.run()
        self.case.destination=replace(self.case.destination,actor_id=self.case.context.actor_id)
        self.case.scope=replace(scope,actor_id=self.case.context.actor_id)
        with self.assertRaises(RecoveryError): self.case.run()

    def test_upload_reader_final_gate_and_error_redirect_body_not_read(self):
        from unittest.mock import patch
        source=self.case.target.streams.source
        content=b"bounded-final-gate"
        work=self.m._ObjectWork(source,"upload","staged","final",len(content),hashlib.sha256(content).hexdigest(),"text/plain",999.)
        wire=self.m._frame(b"D",content)+self.m._frame(b"E")
        def run(raw):
            channel=io.BytesIO(raw)
            real=self.m.os.read
            with patch.object(self.m.os,"read",side_effect=lambda fd,count:channel.read(count) if fd==987654 else real(fd,count)):
                return self.m._UploadReader(987654,work).read(len(content))
        self.assertEqual(run(wire),content)
        with self.assertRaises(RecoveryError): self.m._UploadReader(None,work).read(self.m.PART+2)
        with self.assertRaises(RecoveryError): run(self.m._frame(b"D",content))
        with self.assertRaises(RecoveryError): run(self.m._frame(b"D",b"changed")+self.m._frame(b"E"))
        for status in (302,403,503):
            response=SimpleNamespace(status=status,close=lambda:None,release_conn=lambda:None)
            response.read1=lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("ERROR_BODY_READ"))
            pool=self.m._RestorePool(source,1.)
            pool.allowed={("https","object.invalid","/s44-target/final")}
            try:
                with patch.object(self.m.PoolManager,"urlopen",return_value=response):
                    with self.assertRaises(RecoveryError): pool.urlopen("GET","https://object.invalid/s44-target/final")
            finally: pool.clear()


class RestoreFixture:
    def __init__(self, case, module):
        # 불변 R1B 시험의 setup만 재사용. 기존 시험 node를 중복 실행하지 않는다.
        from test_recovery_lineage_capture import LineageCaptureTests
        from test_recovery_lineage import recanonical
        from daon_user_api.recovery_lineage_capture import LineageCaptureScope
        from daon_user_api.recovery_destinations import (DatabaseIdentity,ObjectIdentity,ProbeRequest,
            PhysicalObservation,RegisteredDestination,TrustedPhysicalProbe,physical_receipt_digest)
        self.m, self.case = module, case
        LineageCaptureTests.setUpClass()
        case.addCleanup(LineageCaptureTests.tearDownClass)
        self.capture_case=LineageCaptureTests("test_v3_original_absent_restart_readback_and_manifest")
        self.capture_case.setUp()
        old=self.capture_case
        mapping={"object-a":"a"*32,"object-b":"b"*32}
        self.content={mapping[key]: (key.encode()+b"\x00synthetic\n")*17 for key in mapping}
        for file in old.original.iterdir(): file.unlink()
        for identity,content in self.content.items(): (old.original/identity).write_bytes(content)
        for row in old.ledger.tables["object_records"]:
            row["object_id"]=mapping[row["object_id"]]
            row["byte_size"]=len(self.content[row["object_id"]]); row["digest_sha256"]=hashlib.sha256(self.content[row["object_id"]]).hexdigest()
        for row in old.ledger.tables["source_versions"]:
            row["object_id"]=mapping[row["object_id"]]
            obj=next(o for o in old.ledger.tables["object_records"] if o["object_id"]==row["object_id"])
            recanonical(row,{"object_id":obj["object_id"],"byte_size":obj["byte_size"],"content_digest_sha256":obj["digest_sha256"]})
        old.selection=old.ledger.resolve()
        old.scope=LineageCaptureScope.from_selection(old.ledger.context,old.selection,capture_id="capture-a")
        self.scope,self.context,self.store=old.scope,old.ledger.context,old.store
        self.receipt=old.capture(); self.metadata=old.selection.metadata()
        for file in old.original.iterdir(): file.unlink()  # 복원은 source files가 아니라 보존본만 읽는다.
        self.now=datetime.now(timezone.utc)
        request=ProbeRequest("source-a","target-a","database-a","bucket-a","owner-a")
        self.observation=PhysicalObservation(request,DatabaseIdentity("10001",111),DatabaseIdentity("10001",222),
            ObjectIdentity("backend-a","s44-source"),ObjectIdentity("backend-a","s44-target"),self.now,True,True,"owner-a",False,False)
        self.destination=RegisteredDestination("generation-a","destination-a",1,"격리",(self.now+timedelta(hours=1)).isoformat(),
            self.context.tenant_id,self.context.workspace_id,self.context.actor_id,physical_receipt_digest(self.observation),
            self.observation.target_database.fingerprint(),self.observation.target_object.fingerprint(),"owner-a",self.now)
        self.database=TargetSqlDouble(self)
        self.binding=module.RestoreBinding("request-a","backup-a","a"*64,module._hash(self.receipt),self.scope.closure_fingerprint,
            self.scope.retention_watermark,1,self.scope.root_source_version_ids,"generation-a","destination-a",1,"owner-a",
            "operation-a","idempotency-a",self.now+timedelta(minutes=10),module._hash([self.database.catalog,self.database.functions]))
        self.storage=StorageDouble()
        from daon_user_api.cloud_storage import CloudAccessContext
        self.cloud=CloudAccessContext(self.context.tenant_id,self.context.workspace_id,self.context.actor_id,"object.write")
        self.objects=QueueStoreDouble(self)
        fixture=self
        class Probe(TrustedPhysicalProbe):
            def observe(self, request):
                case.assertEqual(request,fixture.observation.request)
                return fixture.observation
        ioport=UploadIoDouble(self.storage)
        source=module._MinioSource("object.invalid",self.storage._bucket,"us-east-1",True,
            SimpleNamespace(access_key="synthetic-memory-access",secret_key="synthetic-memory-secret",session_token=None),None)
        streams=module.BoundedLineageObjects(source,_io=ioport,_clock=lambda:ioport.time)
        self.target=module.PostgresLineageTarget(self.connection,self.objects,self.storage,probe=Probe(),probe_request=request,clock=lambda:self.now,object_streams=streams)
        self.source=PhaseSqlDouble(self)
        self.journal=module.PostgresPhaseJournal(self.source_connection)
        self.journal.rows=self.source.rows  # 시험 소유 SQL row 관찰용, 제품 메서드/ref 노출0.
        self.journal.lose_result_phase=None
        original=self.journal.append
        def append(*args,**kwargs):
            if args[3]=="succeeded" and args[2]==self.journal.lose_result_phase: raise OSError("SYNTHETIC_RESULT_LOST")
            return original(*args,**kwargs)
        self.journal.append=append
        self.current_hook=lambda:None

    @contextmanager
    def connection(self): yield self.database

    @contextmanager
    def source_connection(self): yield self.source

    def run(self):
        return self.m.materialize_lineage(self.context,self.scope,self.store,self.receipt,self.binding,self.destination,
            self.target,self.journal,recheck=self.current,clock=lambda:self.now)

    def grow_capture(self,size):
        from test_recovery_lineage import recanonical
        from daon_user_api.recovery_lineage_capture import LineageCaptureScope
        old=self.capture_case; identity="a"*32
        self.content[identity]=b"x"*size
        for key,content in self.content.items(): (old.original/key).write_bytes(content)
        for row in old.ledger.tables["object_records"]:
            content=self.content[row["object_id"]]; row["byte_size"]=len(content); row["digest_sha256"]=hashlib.sha256(content).hexdigest()
        for row in old.ledger.tables["source_versions"]:
            obj=next(o for o in old.ledger.tables["object_records"] if o["object_id"]==row["object_id"])
            recanonical(row,{"object_id":obj["object_id"],"byte_size":obj["byte_size"],"content_digest_sha256":obj["digest_sha256"]})
        old.selection=old.ledger.resolve(); old.scope=LineageCaptureScope.from_selection(old.ledger.context,old.selection,capture_id="capture-large")
        self.scope=old.scope; self.receipt=old.capture(); self.metadata=old.selection.metadata()
        self.binding=replace(self.binding,receipt_digest=self.m._hash(self.receipt),closure_fingerprint=self.scope.closure_fingerprint)
        for path in old.original.iterdir(): path.unlink()

    def current(self):
        # real authorization/SourceACL/특수 Source helper를 SQL 행 대역에 소비한다.
        # 실제 principal/session/Step-up HTTP 검증과는 구분한다.
        from daon_user_api.authorization import Action
        from daon_user_api.open_law_source_access import _current_auth_grant,_AuthReadConnection
        from daon_user_api.organization_access import OrganizationAccessBridge
        from daon_user_api.document_index_postgres import _source_access_allowed
        from daon_user_api.notification_source_access import _special_source_access_allowed
        context=SimpleNamespace(tenant_id=self.context.tenant_id,workspace_id=self.context.workspace_id,
            actor_id=self.context.actor_id,trace_id=self.context.trace_id,policy_version=self.context.policy_version,notebook_id=self.scope.notebook_id)
        ledger=self.capture_case.ledger; ledger.context=self.context
        self.current_hook()
        try:
            _current_auth_grant(context,ledger,Action.POLICY_MANAGE)
            if ledger.auth.auth_role!="personal_owner":
                OrganizationAccessBridge(postgres=True).assert_organization_active(connection=_AuthReadConnection(ledger),tenant_id=context.tenant_id,user_id=context.actor_id)
            for identity in self.scope.root_source_version_ids+self.scope.dependency_source_version_ids:
                if not _source_access_allowed(ledger,context,identity) or not _special_source_access_allowed(ledger,context,identity): raise RecoveryError("CURRENT_ACCESS_DENIED",403)
        except Exception: raise RecoveryError("CURRENT_ACCESS_DENIED",403) from None
        return self.m.CurrentRestoreState(self.context,self.binding)


class Cursor:
    def __init__(self,rows=()): self.rows=list(rows)
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)
    def __iter__(self): return iter(self.rows)


class TargetSqlDouble:
    """SQL/params에 따라 행을 갱신하는 대역. 실제 PG 제약/RLS 성공은 아니다."""
    def __init__(self,fixture):
        self.f=fixture; self.rows={table:{} for table in fixture.m._TABLES}
        self.writes=0; self.source_base=None; self.transitions=[]; self.attempts=[]; self.restore_audits=[]; self.version_order=[]; self.settings={}
        self.require_logical_catalog=False; self.role_oid=123
        self.operator_role=("s44_operator",True,False); self.extra_rows={}; self.global_read_denied=False
        self.catalog=[(table,"daon_app",True,True,"acl",["constraint"],["guard"],["scope"])
            for table in sorted((*fixture.m._TABLES,"object_records","canon_state_transitions","canon_transition_attempts"))]
        self.catalog=[(*row[:7],[["scope","*",True,["daon_app"],"tenant-scope","tenant-scope"]]) for row in self.catalog]
        self.functions=[(name,"daon_app",True,["search_path=pg_catalog,public"],"protected-definition")
            for name in ("transition_canon_state","validate_canon_insert","guard_canon_state_mutation")]
        self.functions=[(*row[:4],"text, text",[["daon_app","daon_app","EXECUTE",False]],row[-1]) for row in self.functions]

    @contextmanager
    def transaction(self):
        before=copy.deepcopy((self.rows,self.transitions,self.attempts,self.restore_audits,self.version_order))
        try: yield
        except BaseException:
            self.rows,self.transitions,self.attempts,self.restore_audits,self.version_order=before
            raise

    def execute(self,sql,params=()):
        import re
        case=self.f.case; ctx=self.f.context
        if sql.startswith("SET LOCAL ROLE"): case.assertEqual(sql,"SET LOCAL ROLE daon_app"); return Cursor()
        if sql.startswith("SELECT set_config"):
            self.settings[params[0]]=params[1]; return Cursor()
        if sql.startswith("SELECT current_user,"):
            case.assertIn("pg_catalog.pg_roles",sql)
            return Cursor([self.operator_role])
        if sql.startswith("SELECT count(*),"):
            case.assertNotRegex(sql,r"FROM public\.[a-z_]+\s+WHERE")  # FILTER 내부 조건은 전역 조회를 숨기지 않음.
            case.assertEqual(tuple(params),(ctx.tenant_id,ctx.workspace_id))
            if self.global_read_denied: raise PermissionError("SYNTHETIC_GLOBAL_VISIBILITY_DENIED")
            table=re.search(r"FROM public\.([a-z_]+)",sql)[1]
            if table=="object_records": count=len(self.f.objects.records)
            elif table=="canon_state_transitions": count=len(self.transitions)
            elif table=="canon_transition_attempts": count=len(self.attempts)
            else: count=len(self.rows[table])
            extra,outside=self.extra_rows.get(table,(0,0))
            return Cursor([(count+extra,outside)])
        if "pg_catalog.pg_control_system()" in sql: return Cursor([("10001",222)])
        if "FROM pg_catalog.pg_class" in sql:
            if self.require_logical_catalog:
                case.assertIn("p.polpermissive",sql); case.assertNotIn("p.polroles::text",sql)
                case.assertIn("unnest(p.polroles)",sql); case.assertIn("rolname",sql)
                case.assertNotIn("ORDER BY 1",sql)
            return Cursor(self.catalog)
        if "FROM pg_catalog.pg_proc" in sql:
            if self.require_logical_catalog:
                case.assertIn("p.proacl",sql); case.assertIn("pg_catalog.aclexplode",sql)
                case.assertIn("pg_get_function_identity_arguments",sql); case.assertNotIn("ORDER BY p.proname,p.oid",sql)
                case.assertNotIn("ORDER BY 1",sql)
            return Cursor(self.functions)
        if "FROM public.workspaces w" in sql:
            case.assertEqual(tuple(params),(ctx.tenant_id,ctx.workspace_id,ctx.actor_id)); return Cursor([tuple(params)])
        if sql.startswith("SELECT pg_advisory"): return Cursor()
        if sql.startswith("SELECT metadata->>"):
            return Cursor([(self.restore_audits[-1]["metadata"]["event_hash"],)]) if self.restore_audits else Cursor()
        if sql.startswith("SELECT event_id,"):
            return Cursor([tuple(row[k] for k in ("event_id","actor_id","trace_id","policy_version","action","target_id","metadata","occurred_at")) for row in self.restore_audits])
        if "FROM public.transition_canon_state(" in sql:
            from daon_user_api.data_canon import transition_allowed
            entity,identity,version,target,event,reason,trace,policy=params
            case.assertEqual(entity,"Source"); case.assertEqual(reason,"RESTORE_REPLAY")
            row=self.rows["sources"][identity]
            case.assertEqual(row["version"],version); case.assertTrue(transition_allowed(entity,row["state"],target))
            common=dict(tenant_id=ctx.tenant_id,workspace_id=ctx.workspace_id,actor_id=self.settings["app.actor_id"],trace_id=trace,
                record_id=identity,source_state=row["state"],target_state=target,entity_type=entity,policy_version=policy,
                reason_code=reason,occurred_at=self.f.now.isoformat())
            self.transitions.append(dict(common,transition_id=event,transition_version=version+1))
            self.attempts.append(dict(common,attempt_id=event,expected_version=version,current_version=version,
                result_version=version+1,result_state=target,outcome="succeeded",safe_error_code=None))
            row["state"],row["version"]=target,version+1; self.writes+=1
            return Cursor([(target,version+1,"succeeded",None)])
        if sql.startswith("SELECT jsonb_build_object"):
            table=re.search(r"FROM public\.([a-z_]+) r",sql)[1]
            case.assertEqual(tuple(params),(ctx.tenant_id,ctx.workspace_id))
            if table=="canon_state_transitions": return Cursor([(copy.deepcopy(row),) for row in self.transitions])
            if table=="canon_transition_attempts": return Cursor([(copy.deepcopy(row),) for row in self.attempts])
            return Cursor([(copy.deepcopy(row),) for row in self.rows[table].values()])
        if sql.startswith("INSERT INTO public."):
            match=re.match(r"INSERT INTO public\.([a-z_]+) \(([^)]+)\)",sql); case.assertIsNotNone(match)
            table=match[1]; keys=[k.strip() for k in match[2].split(",")]
            values=[value.obj if hasattr(value,"obj") else value for value in params]
            row=dict(zip(keys,values,strict=True)); self.writes+=1
            if table=="audit_events": self.restore_audits.append(row); return Cursor()
            case.assertIn(table,self.f.m._TABLES)
            if table=="sources": self.source_base=(row["state"],row["version"],row["previous_version_id"])
            if table=="source_versions":
                case.assertIn(row["source_id"],self.rows["sources"])
                case.assertEqual(self.f.objects.records[row["object_id"]].status,"completed")
                if row["previous_version_id"] is not None: case.assertIn(row["previous_version_id"],self.rows["source_versions"])
                self.version_order.append(row["record_id"])
            key=tuple(row[k] for k in self.f.m._KEYS[table]); key=key[0] if len(key)==1 else key
            case.assertNotIn(key,self.rows[table]); self.rows[table][key]=row
            return Cursor()
        raise AssertionError("UNEXPECTED_TARGET_SQL")


class PhaseSqlDouble:
    """source SQL 경계의 새 phase 표만. 업무 원자료 INSERT는 허용하지 않는다."""
    def __init__(self,fixture): self.f=fixture; self.rows=[]; self.physical_digest=None
    @contextmanager
    def transaction(self): yield
    def execute(self,sql,params=()):
        binding=self.f.binding; ctx=self.f.context
        if sql.startswith("SELECT set_config"): return Cursor()
        if "FROM public.restore_requests" in sql:
            self.f.case.assertIn("FOR UPDATE",sql); return Cursor([(binding.backup_id,ctx.actor_id)])
        if "FROM public.registered_restore_previews p" in sql:
            return Cursor([(binding.registry_generation_id,binding.destination_id,binding.destination_version,binding.manifest_digest,
                binding.closure_fingerprint,binding.retention_watermark,binding.preview_expires_at,binding.owner_operation_id,
                self.physical_digest or self.f.destination.physical_receipt_digest,self.f.m._hash(list(binding.included_roots)),self.f.m._hash([]),
                self.f.destination.target_database_fingerprint,self.f.destination.target_bucket_fingerprint)])
        if sql.startswith("SELECT sequence,"):
            keys=("sequence","phase","outcome","target_digest","result_digest","operation_id","idempotency_key")+(("trace_id",) if "trace_id" in sql else ())
            return Cursor([tuple(r[k] for k in keys) for r in self.rows])
        if sql.startswith("SELECT coalesce(max(sequence)"): return Cursor([(len(self.rows)+1,)])
        self.f.case.assertTrue(sql.startswith("INSERT INTO public.registered_restore_phase_receipts"))
        keys=("tenant_id","workspace_id","request_id","sequence","actor_id","registry_generation_id","destination_id","destination_version",
              "preview_version","operation_id","idempotency_key","phase","outcome","target_digest","result_digest","trace_id")
        row=dict(zip(keys,params,strict=True)); self.rows.append(row)
        return Cursor()


class StorageDouble(MinioObjectStorageAdapter):
    """실 coordinator/worker 아래 SDK 대역. 네트워크·실 MinIO 검증은 아니다."""
    def __init__(self):
        self._bucket="s44-target"; self._client=self; self.data={}; self.puts=0; self.deletes=0; self.fail_promote=False
    def put_staged(self,key,content,content_type,digest):
        assert hashlib.sha256(content).hexdigest()==digest
        self.data[key]=content; self.puts+=1
        return StagedObject(key,digest,len(content),content_type,"etag",None)
    def promote(self,staged,final_key,*,expected_digest,expected_size,content_type):
        if self.fail_promote: raise ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE",retryable=True)
        content=self.data[staged.key]
        assert len(content)==expected_size and hashlib.sha256(content).hexdigest()==expected_digest
        self.data[final_key]=content
        return StoredObject(final_key,expected_digest,expected_size,content_type,"etag",None)
    def get(self,key): return self.data[key]
    def get_object(self,bucket,key):
        assert bucket==self._bucket
        response=io.BytesIO(self.data[key]); response.release_conn=lambda:None
        return response
    def delete(self,key): self.deletes+=1; raise AssertionError("DELETE_FORBIDDEN")


class QueueStoreDouble(PostgresObjectQueueStore):
    """PG 영속 queue 대역. 실 coordinator/worker와 결과 bytes는 제품 코드를 통과."""
    def __init__(self,fixture):
        self.f=fixture; self.records={}; self.jobs={}; self.role=("daon_app",False,False)
        self.completed_times=[]; self.failed_times=[]; self.leases=[]
    @contextmanager
    def _transaction(self,context): yield self
    def execute(self,sql,params=()):
        self.f.case.assertTrue(sql.startswith("SELECT current_user,"))
        self.f.case.assertIn("pg_catalog.pg_roles",sql)
        return Cursor([self.role])
    def scoped_transaction(self,context):
        with self._transaction(context): pass
    def register_object_job(self,context,*,object_id,area,staged,final_key,**kwargs):
        self.scoped_transaction(context)
        assert object_id not in self.records
        self.records[object_id]=ObjectRecord(object_id,area,staged.key,final_key,staged.digest_sha256,staged.byte_size,
            staged.content_type,"pending",None,None,1)
        self.jobs[object_id]=ObjectJob("job-"+object_id,"outbox-"+object_id,object_id,"pending",0,1,self.f.now,None,None,None,None,self.f.context.trace_id,1,self.f.now)
        return ObjectSubmission(object_id,"job-"+object_id,"outbox-"+object_id,False)
    def claim(self,context,worker_id,*,now,lease_seconds):
        self.scoped_transaction(context)
        self.leases.append(lease_seconds)
        job=next((r for r in self.jobs.values() if r.state=="pending"),None)
        if job is None: return None
        job=replace(job,state="leased",attempt=1,lease_owner=worker_id,lease_until=now+timedelta(seconds=lease_seconds))
        self.jobs[job.object_id]=job; return job
    def get_object(self,context,object_id):
        self.scoped_transaction(context)
        return self.records[object_id]
    def complete(self,context,job,worker_id,stored,*,now):
        self.scoped_transaction(context)
        self._assert_lease(job,self.jobs[job.object_id],worker_id,now)
        self.completed_times.append(now)
        self.records[job.object_id]=replace(self.records[job.object_id],status="completed",storage_etag=stored.etag,version=2)
        self.jobs[job.object_id]=replace(job,state="completed"); return self.jobs[job.object_id]
    def fail(self,context,job,worker_id,error,**kwargs):
        self.scoped_transaction(context)
        self._assert_lease(job,self.jobs[job.object_id],worker_id,kwargs["now"])
        self.failed_times.append(kwargs["now"])
        self.jobs[job.object_id]=replace(job,state="dead_letter",last_safe_error_code=error.code); return self.jobs[job.object_id]


class UploadIoDouble:
    """Linux process/SDK 경계 대역. 실제 child/network 실행 성공은 아니다."""
    def __init__(self,backend,*,stalled=False,uncertain=False,response_mode=None):
        self.backend=backend; self.stalled=stalled; self.uncertain=uncertain; self.response_mode=response_mode
        self.platform_supported=True; self.time=0.; self.started=0; self.signalled=[]; self.active=set(); self.maximum_frame=0
    def supported(self): return self.platform_supported
    def start(self,work):
        assert not self.active
        self.started+=1; self.active.add(12345); self.work=work; self.input=bytearray(); self.output=bytearray(); self.pending=bytearray(); self.exited=False; self.status=0
        if work.mode=="copy" and not self.stalled:
            content=self.backend.data[work.staging_key]
            assert len(content)==work.size and hashlib.sha256(content).hexdigest()==work.digest
            getattr(self,"copy_ready_hook",lambda:None)()
            self.output.extend(b"C\x00\x00\x00\x00")
        elif work.mode!="upload" and not self.stalled:
            try: self._result()
            except Exception:
                self.output.extend(b"X\x00\x00\x00\x00"); self.exited=True; self.status=1
        return 12345,99
    def _result(self):
        work=self.work
        if self.response_mode=="error": raise OSError("secret-canary")
        if self.response_mode=="oversize": self.output.extend(b"R"+struct.pack(">I",65537))
        elif self.response_mode=="truncated": self.output.extend(b"R\x00")
        else:
            try:
                if work.mode=="upload":
                    content=bytes(self.input)
                    assert len(content)==work.size and hashlib.sha256(content).hexdigest()==work.digest
                    self.backend.put_staged(work.staging_key,content,work.content_type,work.digest)
                elif work.mode=="copy":
                    self.backend.promote(StagedObject(work.staging_key,work.digest,work.size,work.content_type,"",None),work.final_key,
                        expected_digest=work.digest,expected_size=work.size,content_type=work.content_type)
                else:
                    content=self.backend.get(work.final_key)
                    assert len(content)==work.size and hashlib.sha256(content).hexdigest()==work.digest
                raw=json.dumps(dict(size=work.size,digest=work.digest,etag="0"*32,version=None)).encode()
                self.output.extend(b"R"+struct.pack(">I",len(raw))+raw)
            except Exception:
                self.output.extend(b"X\x00\x00\x00\x00"); self.status=1
        self.exited=True
    def wait(self,fd,writing,timeout):
        self.time+=.001
        return not self.stalled
    def send(self,fd,data):
        data=bytes(data); self.maximum_frame=max(self.maximum_frame,len(data)); assert len(data)<=65536
        self.pending.extend(data)
        while len(self.pending)>=5:
            kind,size=struct.unpack(">cI",self.pending[:5]); assert size<=65531
            if len(self.pending)<5+size: break
            body=bytes(self.pending[5:5+size]); del self.pending[:5+size]
            if kind==b"D": self.input.extend(body)
            else: assert kind in {b"E",b"G"} and not size; self._result()
        return len(data)
    def finish_write(self,fd): assert not self.pending
    def receive(self,fd,count):
        result=bytes(self.output[:count]); del self.output[:count]; return result
    def poll(self,pid):
        assert pid==12345
        if self.exited: self.active.discard(pid)
        return self.exited,self.status if self.exited else None
    def terminate(self,pid):
        assert pid in self.active; self.signalled.append(pid)
        if not self.uncertain: self.exited=True
    def kill(self,pid):
        assert pid in self.active; self.signalled.append(pid)
        if not self.uncertain: self.exited=True
    def pause(self,duration): self.time+=duration
    def close(self,fd): assert fd==99


if __name__ == "__main__": unittest.main()
