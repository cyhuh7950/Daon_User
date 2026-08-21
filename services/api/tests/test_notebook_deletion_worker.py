from datetime import datetime, timezone

from daon_user_api.notebook import NotebookContext, NotebookDeletionView
from daon_user_api.notebook_deletion import NotebookDeletionWorker


class FakeStore:
    def __init__(self):
        self.requests = [NotebookDeletionView("request-1", "notebook-1", "accepted", "accepted", 0, None, datetime.now(timezone.utc), None)]
        self.deleted = []

    def load_pending(self, context):
        return tuple(self.requests)

    def delete_scoped(self, context, request_id):
        self.deleted.append(request_id)

    def mark_completed(self, context, request_id):
        return NotebookDeletionView(request_id, "notebook-1", "completed", "completed", 1, None, datetime.now(timezone.utc), datetime.now(timezone.utc))

    def mark_failed(self, context, request_id, code):
        return NotebookDeletionView(request_id, "notebook-1", "failed", "cleanup", 1, code, datetime.now(timezone.utc), None)


def test_worker_processes_scoped_request_and_resumes_pending():
    store = FakeStore(); worker = NotebookDeletionWorker(store)
    context = NotebookContext("tenant", "workspace", "actor", "trace", "policy")
    result = worker.process(context, "request-1")
    assert result.status == "completed"
    assert worker.resume_pending(context) == 1
    assert store.deleted == ["request-1", "request-1"]
