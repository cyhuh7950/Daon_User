"""S16 durable cleanup ordering; only database/object ports are substituted."""
from dataclasses import dataclass
import unittest

from daon_user_api.object_queue import ObjectStorageError
from daon_user_api.retention import RetentionError
from daon_user_api.retention import RetentionContext
from daon_user_api.retention_inventory_postgres import PostgresRetentionInventoryProvider
from daon_user_api.source_deletion_cleanup import SourceDeletionCleanup


@dataclass
class View:
    request_id: str = "deletion-s16"
    state: str = "cleanup_pending"


class DurableLedger:
    """External persistence fixture survives creation of a new coordinator."""
    def __init__(self):
        self.view = View()
        self.items = {"object-a": "pending", "object-b": "pending"}
        self.events = []
        self.fail_commit = False
        self.revoked = False

    def prepare(self, *args, **kwargs):
        self.events.append("database-and-intents-commit")
        self.require_current()
        return self.view

    def require_current(self):
        if self.revoked:
            raise RetentionError("CURRENT_ACCESS_DENIED", 403)

    def pending(self, context, request_id):
        self.require_current()
        return tuple(key for key, state in self.items.items() if state != "completed")

    def delete_one(self, context, request_id, key, storage):
        self.require_current()
        try:
            storage.delete(key)
        except ObjectStorageError:
            self.items[key] = "failed"
            self.view.state = "failed"
            return False
        if self.fail_commit:
            self.fail_commit = False
            raise RetentionError("RETENTION_UNAVAILABLE", 503)
        self.items[key] = "completed"
        self.events.append(f"ack:{key}")
        return True

    def finish(self, context, request_id):
        self.require_current()
        if all(state == "completed" for state in self.items.values()):
            self.view.state = "purged"
            self.events.append("purged-and-success-audit-commit")
        return self.view


class ObjectPort:
    def __init__(self, ledger):
        self.ledger = ledger
        self.keys = {"object-a", "object-b"}
        self.fail = {"object-b"}
        self.deleted = []

    def delete(self, key):
        if not self.ledger.events or self.ledger.events[0] != "database-and-intents-commit":
            raise AssertionError("object delete before durable intent")
        if self.ledger.view.state == "purged":
            raise AssertionError("purged before actual cleanup")
        if key in self.fail:
            raise ObjectStorageError("OBJECT_STORAGE_UNAVAILABLE", retryable=True)
        self.deleted.append(key)
        self.keys.discard(key)  # S3 remove is idempotent after a lost commit.


class S16CleanupTests(unittest.TestCase):
    def test_s16_inventory_uses_all_versions_and_stored_sync_history_not_hardcoded_unknown(self):
        class Cursor:
            def __init__(self, rows): self.rows = rows
            def fetchone(self): return next(iter(self.rows), None)
            def fetchall(self): return self.rows

        class InventoryDatabase:
            def __init__(self, sync): self.sync, self.queries = sync, []
            def execute(self, sql, params):
                self.queries.append((sql, params))
                if "FROM sources s JOIN source_versions" in sql:
                    return Cursor([("version-2", "object-2"), ("version-1", "object-1")])
                if "FROM object_records" in sql: return Cursor([("object-2",)])
                if "FROM index_versions" in sql: return Cursor([])
                if "sync_preview_items" in sql: return Cursor([("sync-old-version",)] if self.sync else [])
                raise AssertionError(sql)

        ctx = RetentionContext("tenant-s16", "workspace-s16", "actor-s16", "trace-s16", "policy-s16")
        provider = PostgresRetentionInventoryProvider(None)
        clean = InventoryDatabase(False)
        items = {item.kind: item for item in provider.read_in_transaction(clean, ctx, "source-s16")}
        self.assertEqual(items["known_local_copy"].disposition, "not_present")
        self.assertEqual(items["sync_reference"].disposition, "not_present")
        sync = InventoryDatabase(True)
        items = {item.kind: item for item in provider.read_in_transaction(sync, ctx, "source-s16")}
        self.assertEqual(items["known_local_copy"].disposition, "verification_pending")
        self.assertEqual(items["sync_reference"].disposition, "present")
        sync_query = next(sql for sql, _ in sync.queries if "sync_preview_items" in sql)
        self.assertIn("sync_manifest_items", sync_query)
        self.assertIn("sync_target_versions", sync_query)
        self.assertIn(["version-2", "version-1"], next(params for sql, params in sync.queries if sql == sync_query))

    def test_s16_partial_failure_restart_uses_same_ledger_and_skips_completed_objects(self):
        ledger = DurableLedger()
        storage = ObjectPort(ledger)
        worker = SourceDeletionCleanup(ledger, storage)
        first = worker.execute(None, source_id="source-s16", notebook_id="notebook-s16", idempotency_key="business-s16")
        self.assertEqual(first.state, "failed")
        self.assertEqual(storage.keys, {"object-b"})
        self.assertNotIn("purged-and-success-audit-commit", ledger.events)
        storage.fail.clear()
        second = SourceDeletionCleanup(ledger, storage).execute(None, source_id="source-s16", notebook_id="notebook-s16", idempotency_key="business-s16")
        self.assertEqual(second.request_id, first.request_id)
        self.assertEqual(second.state, "purged")
        self.assertEqual(storage.deleted, ["object-a", "object-b"])
        self.assertFalse(storage.keys)

    def test_s16_object_success_then_db_failure_is_not_false_purged_and_retries_idempotently(self):
        ledger = DurableLedger()
        storage = ObjectPort(ledger)
        storage.fail.clear()
        ledger.fail_commit = True
        with self.assertRaisesRegex(RetentionError, "RETENTION_UNAVAILABLE"):
            SourceDeletionCleanup(ledger, storage).execute(None, source_id="source-s16", notebook_id="notebook-s16", idempotency_key="business-s16")
        self.assertEqual(ledger.view.state, "cleanup_pending")
        self.assertEqual(ledger.items["object-a"], "pending")
        result = SourceDeletionCleanup(ledger, storage).execute(None, source_id="source-s16", notebook_id="notebook-s16", idempotency_key="business-s16")
        self.assertEqual(result.state, "purged")
        self.assertEqual(storage.deleted, ["object-a", "object-a", "object-b"])

    def test_s16_revoked_retry_or_missing_object_port_cannot_claim_success(self):
        ledger = DurableLedger()
        storage = ObjectPort(ledger)
        with self.assertRaisesRegex(RetentionError, "OBJECT_STORAGE_UNAVAILABLE"):
            SourceDeletionCleanup(ledger, None).execute(None, source_id="source-s16", notebook_id="notebook-s16", idempotency_key="business-s16")
        self.assertFalse(ledger.events)
        ledger.revoked = True
        with self.assertRaisesRegex(RetentionError, "CURRENT_ACCESS_DENIED"):
            SourceDeletionCleanup(ledger, storage).execute(None, source_id="source-s16", notebook_id="notebook-s16", idempotency_key="business-s16")
        self.assertFalse(storage.deleted)
