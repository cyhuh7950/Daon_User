from __future__ import annotations

import unittest

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
