from __future__ import annotations

import os
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from daon_user_api.cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from daon_user_api.data_canon import CanonicalContext, PostgresDataCanonStore, canonical_json_bytes
from daon_user_api.license import (
    LicenseContext, LicenseError, LicenseService, UnavailableLicenseVerifier, VerifiedLicense,
)
from daon_user_api.license_postgres import PostgresLicenseRepository, enforce_license_creation
from daon_user_api.object_queue import ObjectKeyPolicy, PostgresObjectQueueStore, StagedObject
from daon_user_api.studio_report import StudioReportContext, StudioReportCreateRequest
from daon_user_api.studio_report_postgres import PostgresStudioReportRepository


DSN = os.getenv("DAON_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="DAON_TEST_POSTGRES_DSN is required")


def _license(
    tenant_id: str, *, digest: str = "a" * 64,
    resource_limits: tuple[tuple[str, int], ...] | None = None,
    expires_at: datetime | None = None,
) -> VerifiedLicense:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return VerifiedLicense(
        license_id="license-postgres-gate-001",
        product="daon-user",
        edition="enterprise",
        organization_id=tenant_id,
        issued_at=now - timedelta(days=1),
        expires_at=expires_at or now + timedelta(days=365),
        features=("citation", "studio_generation"),
        resource_limits=resource_limits or (
            ("generation_runs", 100), ("notebooks", 10),
            ("source_versions", 1), ("storage_bytes", 1024), ("studio_outputs", 100),
        ),
        claims_digest=digest,
        key_id="release-1",
    )


def _context(tenant_id: str, workspace_id: str) -> LicenseContext:
    return LicenseContext(tenant_id, workspace_id, "user-license-admin", "trace-license-pg", "policy-v1")


def _cloud(context: LicenseContext) -> CloudAccessContext:
    return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "license.gate")


def test_actual_postgres_license_is_append_only_audited_idempotent_and_force_rls_scoped():
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=1)
    repository = PostgresLicenseRepository(store)
    tenant_a = _context("tenant-license-a", "workspace-license-a")
    tenant_b = _context("tenant-license-b", "workspace-license-b")
    try:
        store.seed_scope(_cloud(tenant_a))
        store.seed_scope(_cloud(tenant_b))

        stored, replayed = repository.apply(
            tenant_a, _license(tenant_a.tenant_id),
            idempotency_key="license-pg-idem-0001", request_fingerprint="b" * 64,
        )
        replay, was_replayed = repository.apply(
            tenant_a, _license(tenant_a.tenant_id),
            idempotency_key="license-pg-idem-0001", request_fingerprint="b" * 64,
        )
        assert replayed is False
        assert was_replayed is True
        assert replay.claims_digest == stored.claims_digest
        assert store.audit_count(_cloud(tenant_a), "license.organization.applied") == 1

        with pytest.raises(LicenseError) as conflict:
            repository.apply(
                tenant_a, _license(tenant_a.tenant_id, digest="c" * 64),
                idempotency_key="license-pg-idem-0001", request_fingerprint="d" * 64,
            )
        assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
        assert repository.current(tenant_b) is None

        with store._transaction(_cloud(tenant_b)) as connection:
            cross_tenant_insert = connection.execute(
                "INSERT INTO organization_license_versions "
                "(tenant_id,version,license_id,product,edition,issued_at,expires_at,features,resource_limits,"
                "claims_digest,signing_key_id,applied_by,applied_at,trace_id,policy_version) "
                "SELECT tenant_id,version+1,license_id,product,edition,issued_at,expires_at,features,resource_limits,"
                "%s,signing_key_id,applied_by,now(),trace_id,policy_version "
                "FROM organization_license_versions WHERE tenant_id=%s",
                ("e" * 64, tenant_a.tenant_id),
            )
            assert cross_tenant_insert.rowcount == 0

        with pytest.raises(CloudDatabaseError):
            with store._transaction(_cloud(tenant_a)) as connection:
                connection.execute(
                    "UPDATE organization_license_versions SET edition='modified' "
                    "WHERE tenant_id=%s AND version=1",
                    (tenant_a.tenant_id,),
                )

        assert repository.current(tenant_a) is not None
        assert repository.usage(tenant_a)["users"] == 1
        with store._transaction(_cloud(tenant_a)) as connection:
            count = connection.execute(
                "SELECT count(*) FROM organization_license_versions WHERE tenant_id=%s",
                (tenant_a.tenant_id,),
            ).fetchone()[0]
            columns = {
                str(row[0])
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='organization_license_versions'"
                ).fetchall()
            }
        assert count == 1
        assert not ({"document", "signature", "private_key", "claims"} & columns)
        assert store.context_is_clear() is True
    finally:
        store.close()


def test_actual_postgres_two_connection_apply_and_creation_quota_are_serialized_and_atomic():
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=4)
    repository = PostgresLicenseRepository(store)
    same = _context("tenant-license-concurrent-same", "workspace-license-concurrent-same")
    different = _context("tenant-license-concurrent-different", "workspace-license-concurrent-different")
    quota = _context("tenant-license-concurrent-quota", "workspace-license-concurrent-quota")
    storage = _context("tenant-license-concurrent-storage", "workspace-license-concurrent-storage")
    try:
        for context in (same, different, quota, storage):
            store.seed_scope(_cloud(context))

        same_barrier = Barrier(2)
        def same_apply():
            same_barrier.wait(timeout=5)
            return repository.apply(
                same, _license(same.tenant_id),
                idempotency_key="license-pg-concurrent-same-0001",
                request_fingerprint="1" * 64,
            )[1]

        with ThreadPoolExecutor(max_workers=2) as executor:
            same_results = list(executor.map(lambda _index: same_apply(), range(2)))
        assert sorted(same_results) == [False, True]
        assert store.audit_count(_cloud(same), "license.organization.applied") == 1

        different_barrier = Barrier(2)
        def different_apply(index: int) -> str:
            different_barrier.wait(timeout=5)
            try:
                repository.apply(
                    different, _license(different.tenant_id, digest=str(index + 2) * 64),
                    idempotency_key="license-pg-concurrent-different-0001",
                    request_fingerprint=str(index + 4) * 64,
                )
                return "stored"
            except LicenseError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            different_results = list(executor.map(different_apply, range(2)))
        assert sorted(different_results) == ["IDEMPOTENCY_KEY_REUSED", "stored"]
        assert store.audit_count(_cloud(different), "license.organization.applied") == 1

        repository.apply(
            quota, _license(quota.tenant_id),
            idempotency_key="license-pg-concurrent-quota-0001",
            request_fingerprint="8" * 64,
        )
        repository.apply(
            storage, _license(storage.tenant_id, resource_limits=(
                ("generation_runs", 100), ("notebooks", 10),
                ("source_versions", 100), ("storage_bytes", 1024), ("studio_outputs", 100),
            )),
            idempotency_key="license-pg-concurrent-storage-0001",
            request_fingerprint="9" * 64,
        )
        canon = PostgresDataCanonStore(DSN, min_size=1, max_size=1)
        try:
            for index in range(2):
                canon.create_source(CanonicalContext(
                    quota.tenant_id, quota.workspace_id, quota.actor_id,
                    "source.create", quota.trace_id,
                ), f"source-quota-{index}")
        finally:
            canon.close()

        quota_barrier = Barrier(2)
        def create_source_version(index: int) -> str:
            quota_barrier.wait(timeout=5)
            worker = PostgresDataCanonStore(
                DSN, min_size=1, max_size=1, creation_enforcer=enforce_license_creation,
            )
            try:
                payload = {"title": f"quota-{index}"}
                worker.append_source_version(
                    CanonicalContext(
                        quota.tenant_id, quota.workspace_id, quota.actor_id,
                        "source.create", quota.trace_id,
                    ),
                    source_version_id=f"source-version-quota-{index}",
                    source_id=f"source-quota-{index}", version_number=1,
                    previous_version_id=None, canonical_payload=payload,
                    digest_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
                    created_at=datetime.now(timezone.utc),
                )
                return "stored"
            except LicenseError as error:
                return error.code
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            quota_results = list(executor.map(create_source_version, range(2)))
        assert sorted(quota_results) == ["LICENSE_RESOURCE_LIMIT_REACHED", "stored"]
        with store._transaction(_cloud(quota)) as connection:
            count = connection.execute(
                "SELECT count(*) FROM source_versions WHERE tenant_id=%s",
                (quota.tenant_id,),
            ).fetchone()[0]
        assert count == 1

        object_context = CloudAccessContext(
            storage.tenant_id, storage.workspace_id, storage.actor_id, "object.write",
        )
        storage_barrier = Barrier(2)
        def create_stored_bytes(index: int) -> str:
            storage_barrier.wait(timeout=5)
            worker = PostgresObjectQueueStore(
                DSN, min_size=1, max_size=1, creation_enforcer=enforce_license_creation,
            )
            object_id = f"{index + 1:032x}"
            key_policy = ObjectKeyPolicy()
            staged = StagedObject(
                key_policy.staging_key(object_context, "source", object_id),
                str(index + 3) * 64, 800, "application/pdf", f"etag-{index}", None,
            )
            try:
                worker.register_object_job(
                    object_context, object_id=object_id, area="source", staged=staged,
                    final_key=key_policy.final_key(object_context, "source", object_id),
                    idempotency_key=f"license-storage-quota-{index}",
                    trace_id=f"trace-license-storage-{index}",
                )
                return "stored"
            except LicenseError as error:
                return error.code
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            storage_results = list(executor.map(create_stored_bytes, range(2)))
        assert sorted(storage_results) == ["LICENSE_RESOURCE_LIMIT_REACHED", "stored"]
        with store._transaction(_cloud(storage)) as connection:
            stored_bytes = connection.execute(
                "SELECT coalesce(sum(byte_size),0) FROM object_records WHERE tenant_id=%s",
                (storage.tenant_id,),
            ).fetchone()[0]
        assert stored_bytes == 800
        view = LicenseService(
            repository, UnavailableLicenseVerifier(), product_code="daon-user",
            clock=lambda: datetime.now(timezone.utc), usage_reader=repository.usage,
        ).get(storage)
        projected_storage = next(
            item for item in view["resources"] if item["resource"] == "storage_bytes"
        )
        assert projected_storage == {
            "resource": "storage_bytes", "limit": 1024, "used": 800,
            "remaining": 224, "status": "available",
        }
        assert view["creation_allowed"] is True
        assert store.context_is_clear() is True
    finally:
        store.close()


def test_actual_postgres_studio_replay_precedes_quota_and_expiry_enforcement():
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    repository = PostgresLicenseRepository(store)
    context = _context("tenant-license-studio-replay", "workspace-license-studio-replay")
    cloud_context = _cloud(context)
    canon = PostgresDataCanonStore(DSN, min_size=1, max_size=1)
    source_id = "source-license-studio-replay"
    source_version_id = "source-version-license-studio-replay"
    notebook_id = "notebook-license-studio-replay"
    conversation_id = "conversation-license-studio-replay"
    run_id = "run-license-studio-replay"
    run_result_id = "result-license-studio-replay"
    try:
        store.seed_scope(cloud_context)
        limits = (
            ("generation_runs", 1), ("notebooks", 10),
            ("source_versions", 100), ("storage_bytes", 1024), ("studio_outputs", 1),
        )
        repository.apply(
            context, _license(context.tenant_id, resource_limits=limits),
            idempotency_key="license-studio-replay-apply-0001",
            request_fingerprint="a" * 64,
        )
        canonical_context = CanonicalContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            "source.create", context.trace_id,
        )
        canon.create_source(canonical_context, source_id)
        payload = {"filename": "license-studio-replay.pdf"}
        canon.append_source_version(
            canonical_context, source_version_id=source_version_id, source_id=source_id,
            version_number=1, previous_version_id=None, canonical_payload=payload,
            digest_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
            created_at=datetime.now(timezone.utc),
        )
        version = 1
        for state in ("security_check", "processing", "indexing", "ready"):
            canon.transition(
                canonical_context, entity_type="Source", record_id=source_id,
                expected_version=version, target_state=state,
                transition_id=f"transition-license-studio-{state}",
                reason_code="LICENSE_STUDIO_FIXTURE", policy_version="policy-v1",
            )
            version += 1
        with store._transaction(cloud_context) as connection:
            def insert_canon(table: str, record_id: str, value: dict[str, object], **extra):
                text = canonical_json_bytes(value).decode()
                columns = [
                    "tenant_id", "workspace_id", "record_id", "aggregate_id", "version",
                    "schema_version", "canonical_json", "canonical_text", "digest_sha256",
                    "created_by", "trace_id", *extra,
                ]
                values = [
                    context.tenant_id, context.workspace_id, record_id, record_id, 1, 1,
                    json.dumps(value), text, hashlib.sha256(text.encode()).hexdigest(),
                    context.actor_id, context.trace_id, *extra.values(),
                ]
                connection.execute(
                    f"INSERT INTO {table} ({','.join(columns)}) "
                    f"VALUES ({','.join(['%s'] * len(values))})", values,
                )

            now = datetime.now(timezone.utc)
            connection.execute(
                "INSERT INTO notebooks (tenant_id,workspace_id,notebook_id,created_by,created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (context.tenant_id, context.workspace_id, notebook_id, context.actor_id, now),
            )
            connection.execute(
                "INSERT INTO notebook_metadata_versions "
                "(tenant_id,workspace_id,notebook_id,version,title,description,is_current,updated_by,updated_at) "
                "VALUES (%s,%s,%s,1,%s,NULL,true,%s,%s)",
                (context.tenant_id, context.workspace_id, notebook_id,
                 "라이선스 Studio replay", context.actor_id, now),
            )
            insert_canon("conversations", conversation_id, {"title": "라이선스 replay 대화"})
            insert_canon("runs", run_id, {
                "source_id": source_id, "source_version_id": source_version_id,
            }, conversation_id=conversation_id)
            insert_canon("run_results", run_result_id, {
                "answer": "라이선스 replay 근거 답변", "insufficient": False,
            }, run_id=run_id)
            insert_canon(
                "evidence_spans", "span-license-studio-replay", {"text": "근거", "page": 1},
                source_version_id=source_version_id,
            )
            insert_canon("citations", "citation-license-studio-replay", {
                "page": 1, "run_result_id": run_result_id,
            }, run_result_id=run_result_id, source_version_id=source_version_id,
                evidence_span_id="span-license-studio-replay")
            binding_sql = (
                "INSERT INTO notebook_bindings "
                "(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            )
            for binding in (
                (context.tenant_id, context.workspace_id, notebook_id, "source",
                 source_id, source_version_id, context.actor_id, now),
                (context.tenant_id, context.workspace_id, notebook_id, "conversation_thread",
                 conversation_id, None, context.actor_id, now),
            ):
                connection.execute(binding_sql, binding)

        report_repository = PostgresStudioReportRepository(
            store, creation_enforcer=enforce_license_creation,
        )
        report_context = StudioReportContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version, notebook_id,
        )
        request = StudioReportCreateRequest(
            source_id, source_version_id, run_id, run_result_id,
            "라이선스 replay 보고서", "replay 우선순위 검증",
        )
        first, first_replayed = report_repository.create_report(
            report_context, request, "studio-license-replay-key-0001",
        )
        replay, replayed = report_repository.create_report(
            report_context, request, "studio-license-replay-key-0001",
        )
        assert first_replayed is False and replayed is True
        assert replay.output_version_id == first.output_version_id
        with pytest.raises(LicenseError) as quota_blocked:
            report_repository.create_report(
                report_context, request, "studio-license-new-after-quota-0001",
            )
        assert quota_blocked.value.code == "LICENSE_RESOURCE_LIMIT_REACHED"

        repository.apply(
            context, _license(
                context.tenant_id, digest="b" * 64, resource_limits=limits,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
            idempotency_key="license-studio-replay-expired-0001",
            request_fingerprint="c" * 64,
        )
        expired_replay, expired_replayed = report_repository.create_report(
            report_context, request, "studio-license-replay-key-0001",
        )
        assert expired_replayed is True
        assert expired_replay.output_version_id == first.output_version_id
        with pytest.raises(LicenseError) as expired_blocked:
            report_repository.create_report(
                report_context, request, "studio-license-new-after-expiry-0001",
            )
        assert expired_blocked.value.code == "LICENSE_EXPIRED"
        with store._transaction(cloud_context) as connection:
            assert connection.execute(
                "SELECT count(*) FROM studio_outputs WHERE tenant_id=%s",
                (context.tenant_id,),
            ).fetchone()[0] == 1
        assert store.audit_count(cloud_context, "studio.report.create") == 1
    finally:
        canon.close()
        store.close()
