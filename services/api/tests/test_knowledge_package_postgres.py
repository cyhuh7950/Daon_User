from __future__ import annotations

import hashlib
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from daon_user_api.knowledge_package import KnowledgePackageContext, KnowledgePackageError
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
                "package-1", "source-knowledge-1", "version-knowledge-1", DIGEST, "registration-1",
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


class _QuestionSelectionConnection(_Connection):
    """SQL 대역이다. 실제 PG 실행/SQL 파서/동시성 검증으로 계산하지 않는다."""
    def __init__(self,rows):
        super().__init__()
        self.rows=rows
        self.selection_queries=[]

    def execute(self,query,parameters):
        if 'sv.source_id' in query and 'registered_source_version_id' in query:
            self.selection_queries.append((query,parameters))
            return _Result(rows=self.rows)
        return super().execute(query,parameters)


class KnowledgePackagePostgresContractTests(unittest.TestCase):
    def test_s42_question_source_uses_exact_registration_and_eligibility_in_same_query(self):
        """첫 목록의 registration-1 대신 동일 자료 조회의 정확 registration-current를 소비해야 한다."""
        cloud=_Cloud()
        cloud.connection=_QuestionSelectionConnection([
            ('package-1','source-current','version-current',DIGEST,'registration-current')])
        service=PostgresKnowledgePackageService(cloud,_Content(),clock=lambda:NOW)
        context=KnowledgePackageContext('tenant-1','workspace-1','actor-1','trace-1','policy-v1','device-1')
        resolved=service.resolve_question_sources(context,('package-1',))
        self.assertEqual(len(resolved),1)
        self.assertEqual((resolved[0].package_id,resolved[0].source_id,resolved[0].source_version_id,resolved[0].digest_sha256),
            ('package-1','source-current','version-current',DIGEST))
        self.assertEqual(getattr(resolved[0],'knowledge_registration_id',None),'registration-current',
            'SQL_SELECTION_EXACT_REGISTRATION_ID_NOT_CARRIED')
        self.assertEqual(len(cloud.connection.selection_queries),1)
        query,_=cloud.connection.selection_queries[0]
        compact=''.join(query.split())
        self.assertIn('kr.record_id',query.split('FROM',1)[0])
        for predicate in ("JOINoutput_versionsov","kr.state='registered'","ov.state='approved'",
            "kr.canonical_json->>'authority'='approved'",
            "kr.canonical_json->>'producer'IN('daon2','daon2_5','daon3')",'effective_at','expires_at'):
            with self.subTest(predicate=predicate):
                self.assertIn(predicate,compact)

    def test_s42_question_source_rejects_ambiguous_or_duplicate_aggregate_rows(self):
        """동일 package의 다른 등록/동일 행 중복을 dict-last로 임의 선택하지 않는다. SQL 대역 시험이다."""
        first=('package-1','source-current','version-current',DIGEST,'registration-current')
        cases=(('different_registration',[first,('package-1','source-other','version-other',DIGEST,'registration-other')]),
            ('duplicate_aggregate',[first,first]))
        for case,rows in cases:
            with self.subTest(case=case):
                cloud=_Cloud()
                cloud.connection=_QuestionSelectionConnection(rows)
                service=PostgresKnowledgePackageService(cloud,_Content(),clock=lambda:NOW)
                context=KnowledgePackageContext('tenant-1','workspace-1','actor-1','trace-1','policy-v1','device-1')
                with self.assertRaises(KnowledgePackageError) as caught:
                    service.resolve_question_sources(context,('package-1',))
                self.assertEqual((caught.exception.code,caught.exception.status),('KNOWLEDGE_PACKAGE_UNAVAILABLE',404))
                self.assertEqual(len(cloud.connection.selection_queries),1)

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
