from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from daon_user_api.retention import RetentionContext, RetentionError
from daon_user_api.retention_request_postgres import PostgresRetentionRequestService
from test_source_deletion_cleanup_s16 import DurableLedger, ObjectPort


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "migrations/versions/0025_source_immediate_deletion.py").read_text(encoding="utf-8")


def test_s16_confirmation_requires_step_up_and_durable_object_cleanup_before_terminal_state():
    # Exercise the real service and coordinator; substitute only durable DB and
    # object I/O. Actual SQL/role/rollback coverage lives in the isolated PG file.
    ledger = DurableLedger()
    storage = ObjectPort(ledger)
    service = PostgresRetentionRequestService(None, None,
        clock=lambda: datetime.now(timezone.utc), object_storage=storage)
    context = RetentionContext("tenant-s16", "workspace-s16", "actor-s16", "trace-s16", "policy-s16")
    request = dict(source_id="source-s16", notebook_id="notebook-s16",
        inventory=None, idempotency_key="source-delete-s16", if_match="*")
    with patch("daon_user_api.retention_request_postgres.PostgresSourceCleanupRepository", return_value=ledger):
        with pytest.raises(RetentionError, match="STEP_UP_REQUIRED") as denied:
            service.create_request(context, **request)
        assert denied.value.status == 403
        assert ledger.events == [] and storage.deleted == []
        first = service.create_request(context, **request, step_up_verified=True, authorization_key="verified-receipt")
        assert first.state == "failed"
        assert storage.keys == {"object-b"}
        assert "purged-and-success-audit-commit" not in ledger.events
        storage.fail.clear()
        second = service.create_request(context, **request, step_up_verified=True, authorization_key="renewed-receipt")
        assert second.request_id == first.request_id and second.state == "purged"
        assert storage.deleted == ["object-a", "object-b"] and storage.keys == set()
        assert ledger.events[0] == "database-and-intents-commit"
        assert ledger.events[-1] == "purged-and-success-audit-commit"


def test_source_cleanup_scope_removes_binding_index_and_retains_shared_objects():
    assert "CREATE OR REPLACE FUNCTION delete_source_scope" in MIGRATION
    assert "DELETE FROM notebook_bindings" in MIGRATION
    assert "DELETE FROM index_versions" in MIGRATION
    assert "DELETE FROM source_versions" in MIGRATION
    assert "DELETE FROM sources" in MIGRATION
    assert "DELETE FROM object_records" in MIGRATION
    assert "NOT EXISTS (SELECT 1 FROM source_versions" in MIGRATION
    assert "sv.source_id <> p_source_id" in MIGRATION
    assert "iv.object_id=o.object_id" in MIGRATION
    assert "GRANT EXECUTE ON FUNCTION delete_source_scope" in MIGRATION
