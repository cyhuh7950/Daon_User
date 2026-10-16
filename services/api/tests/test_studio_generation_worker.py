from __future__ import annotations

import unittest
import threading
from unittest.mock import patch

from daon_user_api.studio_generation_queue import StudioGenerationJob
from daon_user_api.studio_generation_worker import StudioGenerationWorker


class FakeQueue:
    def __init__(self, job):
        self.job = job
        self.finished = []

    def claim(self, worker_id, *, lease_seconds):
        job, self.job = self.job, None
        return job

    def finish(self, job, **kwargs):
        self.finished.append(kwargs)


class FakeRepository:
    def __init__(self, result=None, error=None):
        self.result = result or {"studio_output_id": "output-1", "output_version_id": "version-1"}
        self.error = error

    def create_generation(self, context, request, key):
        if self.error is not None:
            raise self.error
        return self.result, False


def job(output_type: str) -> StudioGenerationJob:
    return StudioGenerationJob(
        tenant_id="tenant-1", workspace_id="workspace-1", job_id="job-1", actor_id="actor-1",
        trace_id="trace-1", policy_version="policy-1", idempotency_key="key-1",
        request_json={
            "notebook_id": "notebook-1", "output_type": output_type, "source_id": "source-1",
            "source_version_ids": ["version-1"], "run_id": "run-1", "run_result_id": "result-1",
            "purpose": "보고서", "audience": "운영자", "ruleset_version_id": None,
            "length": "standard", "structure": "summary", "output_format": "json" if output_type in {"audio", "video"} else "pdf",
            "review_condition": "review_required",
        }, state="leased", attempt=1, version=2,
    )


class StudioGenerationWorkerTests(unittest.TestCase):
    def test_s24_lifecycle_default_sixty_second_sweep_runs_without_generation_queue_and_stops(self):
        entered=threading.Event(); waited=threading.Event(); waits=[]; calls=[]
        class Stop:
            def __init__(self): self.event=threading.Event()
            def is_set(self): return self.event.is_set()
            def set(self): self.event.set()
            def wait(self,seconds):
                if threading.current_thread().name.startswith('studio-approval-'):
                    waits.append(seconds); waited.set()
                return self.event.wait(seconds)
        class Approval:
            def sweep_due(self,worker_id,limit):
                calls.append((worker_id,limit));entered.set()
                return {'processed_count':0,'notice_count':0,'expired_count':0}
        stop=Stop();worker=StudioGenerationWorker(FakeQueue(None),FakeRepository(),worker_id='worker-lifecycle',approval_repository=Approval())
        running=threading.Thread(target=worker.run_forever,args=(stop,),kwargs={'poll_seconds':0.01})
        running.start()
        try:
            self.assertTrue(entered.wait(2),'empty queue must not prevent approval sweep')
            self.assertTrue(waited.wait(2),'approval cadence must wait independently')
            self.assertEqual(waits,[60.0]);self.assertEqual(calls,[('worker-lifecycle',100)])
            self.assertTrue(any(t.name.startswith('studio-approval-') and not t.daemon for t in threading.enumerate()))
        finally:
            stop.set();running.join(2)
        self.assertFalse(running.is_alive())
        self.assertFalse(any(t.name.startswith('studio-approval-') for t in threading.enumerate()))

    def test_s24_lifecycle_long_generation_does_not_block_approval_sweeps(self):
        generating=threading.Event();release=threading.Event();twice=threading.Event();calls=[]
        class Repository(FakeRepository):
            def create_generation(self,*args):
                generating.set();release.wait(2)
                return self.result,False
        class Approval:
            def sweep_due(self,worker_id,limit):
                calls.append((worker_id,limit))
                if generating.is_set() and len(calls)>=2: twice.set()
                return {'processed_count':0,'notice_count':0,'expired_count':0}
        stop=threading.Event();queue=FakeQueue(job('evidence_report'))
        worker=StudioGenerationWorker(queue,Repository(),worker_id='worker-long',approval_repository=Approval(),approval_sweep_seconds=0.01)
        running=threading.Thread(target=worker.run_forever,args=(stop,),kwargs={'poll_seconds':0.01})
        running.start()
        try:
            self.assertTrue(generating.wait(2));self.assertTrue(twice.wait(2))
            self.assertEqual(queue.finished,[])
        finally:
            stop.set();release.set();running.join(2)
        self.assertFalse(running.is_alive())
        self.assertEqual(queue.finished,[{'state':'completed','studio_output_id':'output-1','output_version_id':'version-1'}])

    def test_s24_lifecycle_generation_and_sweep_errors_recover_independently_without_secret_logs(self):
        completed=threading.Event();swept=threading.Event();approval_calls=[]
        class Queue(FakeQueue):
            def __init__(self): super().__init__(job('evidence_report'));self.claims=0
            def claim(self,*args,**kwargs):
                self.claims+=1
                if self.claims==1: raise RuntimeError('private generation canary')
                return super().claim(*args,**kwargs)
            def finish(self,*args,**kwargs): super().finish(*args,**kwargs);completed.set()
        class Approval:
            def sweep_due(self,worker_id,limit):
                approval_calls.append((worker_id,limit))
                if len(approval_calls)==1: raise RuntimeError('private sweep canary')
                swept.set();return {'processed_count':0,'notice_count':0,'expired_count':0}
        stop=threading.Event();queue=Queue()
        worker=StudioGenerationWorker(queue,FakeRepository(),worker_id='worker-recover',approval_repository=Approval(),approval_sweep_seconds=0.01)
        running=threading.Thread(target=worker.run_forever,args=(stop,),kwargs={'poll_seconds':0.01})
        with self.assertLogs('daon_user_api.studio_generation_worker',level='WARNING') as logs:
            running.start()
            try:
                self.assertTrue(completed.wait(2));self.assertTrue(swept.wait(2))
            finally:
                stop.set();running.join(2)
        self.assertFalse(running.is_alive());self.assertEqual(queue.finished[0]['state'],'completed')
        self.assertEqual(set(logs.output),{
            'WARNING:daon_user_api.studio_generation_worker:STUDIO_APPROVAL_SWEEP_FAILED',
            'WARNING:daon_user_api.studio_generation_worker:STUDIO_GENERATION_POLL_FAILED'})
        self.assertNotIn('canary',' '.join(logs.output))

    def test_s24_lifecycle_main_wires_real_repository_and_signal_stops_sweep_before_pool_cleanup(self):
        from daon_user_api import studio_generation_worker as module
        handlers={};events=[];active=threading.Event();release=threading.Event();observed=[]
        class Store:
            def __init__(self,dsn): self.dsn=dsn
            def close(self):
                self_test.assertFalse(active.is_set());events.append('pool.closed')
        class Queue(FakeQueue):
            def __init__(self,dsn,store): super().__init__(None)
            def close(self): events.append('queue.closed')
        class Approval:
            def __init__(self,store): observed.append(store.dsn)
            def sweep_due(self,worker_id,limit):
                observed.append((worker_id,limit));active.set()
                handlers[module.signal.SIGTERM]()
                release.wait(2);active.clear();events.append('sweep.finished')
                return {'processed_count':0,'notice_count':0,'expired_count':0}
        self_test=self
        settings=module.StudioWorkerSettings('synthetic-no-connection','worker-cli',600,0.01)
        with patch.object(module.StudioWorkerSettings,'from_env',return_value=settings),patch.object(module,'PostgresCloudStore',Store),\
             patch.object(module,'PostgresStudioGenerationQueue',Queue),patch.object(module,'PostgresStudioWorkspaceRepository',lambda store:FakeRepository()),\
             patch.object(module,'StudioApprovalLifecycleRepository',Approval,create=True),patch.object(module.signal,'signal',side_effect=lambda signum,handler:handlers.setdefault(signum,handler)):
            running=threading.Thread(target=module.main);running.start()
            try:
                self.assertTrue(active.wait(2),'CLI must wire the lifecycle repository')
                self.assertEqual(events,[],'pool may not close while sweep is running')
            finally:
                release.set()
                if module.signal.SIGTERM in handlers: handlers[module.signal.SIGTERM]()
                running.join(2)
        self.assertFalse(running.is_alive())
        self.assertEqual(observed,['synthetic-no-connection',('worker-cli',100)])
        self.assertEqual(events,['sweep.finished','queue.closed','pool.closed'])

    def test_s24_lifecycle_invalid_internal_cadence_rejects_before_starting_threads(self):
        for value in (True,0,-1,61,float('nan'),float('inf'),None):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'STUDIO_WORKER_APPROVAL_POLL_INVALID'):
                StudioGenerationWorker(FakeQueue(None),FakeRepository(),worker_id='worker-1',approval_repository=object(),approval_sweep_seconds=value)

    def test_queued_open_law_revoked_before_generation_never_calls_create(self):
        from daon_user_api.studio_workspace import StudioError
        class RevokedRepository(FakeRepository):
            def __init__(self):super().__init__();self.created=False
            def require_source_access(self,context,versions):
                raise StudioError('RESOURCE_UNAVAILABLE',404)
            def create_generation(self,*args):self.created=True;return super().create_generation(*args)
        repo=RevokedRepository();queue=FakeQueue(job('evidence_report'))
        StudioGenerationWorker(queue,repo,worker_id='worker-law').run_once()
        self.assertFalse(repo.created)
        self.assertEqual(queue.finished,[{'state':'failed','error_code':'RESOURCE_UNAVAILABLE'}])

    def test_supported_output_finishes_completed(self):
        queue = FakeQueue(job("evidence_report"))
        worker = StudioGenerationWorker(queue, FakeRepository(), worker_id="worker-1")
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.finished, [{
            "state": "completed", "studio_output_id": "output-1", "output_version_id": "version-1",
        }])

    def test_media_without_provider_finishes_unavailable(self):
        from daon_user_api.studio_workspace import StudioError

        queue = FakeQueue(job("audio"))
        worker = StudioGenerationWorker(
            queue, FakeRepository(error=StudioError("STUDIO_OUTPUT_UNAVAILABLE", 409)), worker_id="worker-1",
        )
        self.assertTrue(worker.run_once())
        self.assertEqual(queue.finished, [{"state": "unavailable", "error_code": "STUDIO_OUTPUT_UNAVAILABLE"}])


if __name__ == "__main__":
    unittest.main()
