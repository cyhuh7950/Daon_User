from __future__ import annotations

import hashlib
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from daon_user_api.knowledge_package import KnowledgePackageContext
from daon_user_api.knowledge_package_postgres import PostgresKnowledgePackageService


NOW = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
CONTENT = b"postgres knowledge package"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class _Result:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self) -> None:
        self.grant = None

    def execute(self, query, parameters):
        if "sv.source_id" in query and "registered_source_version_id" in query:
            return _Result(rows=[(
                "package-1", "source-knowledge-1", "version-knowledge-1", DIGEST,
            )])
        if "FROM knowledge_registrations" in query:
            return _Result(rows=[(
                "package-1", "daon3", "3.0", "registration-1", "output-1",
                "approved", "registered", "approved", DIGEST, len(CONTENT),
                "application/json", NOW - timedelta(days=1), NOW + timedelta(days=1),
            )])
        if query.startswith("SELECT copy_id"):
            return _Result(row=self.grant)
        if "INSERT INTO offline_knowledge_copy_grants" in query:
            self.grant = (
                parameters[2], parameters[15], "approved", parameters[17], parameters[10]
            )
            return _Result()
        if "FROM offline_knowledge_copy_grants" in query:
            return _Result(row=(
                "package-1", "daon3", "3.0", "registration-1", "output-1",
                "approved", "registered", "approved", DIGEST, len(CONTENT),
                "application/json", NOW, NOW + timedelta(days=1), "approved",
            ))
        raise AssertionError(query)


class _Cloud:
    def __init__(self) -> None:
        self.connection = _Connection()

    @contextmanager
    def _transaction(self, _context):
        yield self.connection


class _Content:
    def read_package(self, _context, _package):
        return CONTENT


class KnowledgePackagePostgresContractTests(unittest.TestCase):
    def test_list_grant_and_read_preserve_digest_and_idempotency(self) -> None:
        service = PostgresKnowledgePackageService(_Cloud(), _Content(), clock=lambda: NOW)
        context = KnowledgePackageContext(
            "tenant-1", "workspace-1", "actor-1", "trace-1", "policy-v1", "device-1"
        )
        self.assertEqual(service.list_packages(context)[0].digest_sha256, DIGEST)
        question_source = service.resolve_question_sources(context, ("package-1",))[0]
        self.assertEqual(
            (question_source.source_id, question_source.source_version_id),
            ("source-knowledge-1", "version-knowledge-1"),
        )
        grant = service.create_offline_copy(
            context, package_id="package-1", device_id="device-1",
            step_up_authorization_id="step-up-1", idempotency_key="copy-idem-1",
            approval_verified=True,
        )
        replay = service.create_offline_copy(
            context, package_id="package-1", device_id="device-1",
            step_up_authorization_id="step-up-1", idempotency_key="copy-idem-1",
            approval_verified=True,
        )
        self.assertEqual(grant.copy_id, replay.copy_id)
        self.assertEqual(service.read_content(context, copy_id=grant.copy_id), CONTENT)


if __name__ == "__main__":
    unittest.main()
