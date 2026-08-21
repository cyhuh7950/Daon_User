from __future__ import annotations

import os
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event, Lock
import time

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from daon_user_api.cloud_storage import CloudAccessContext, PostgresCloudStore
from daon_user_api.notebook import NotebookContext, NotebookError
from daon_user_api.notebook_postgres import PostgresNotebookRepository
from daon_user_api.license import LicenseContext, LicenseError, VerifiedLicense
from daon_user_api.license_postgres import PostgresLicenseRepository, enforce_license_creation
from daon_user_api.question_answering import GroundedTextResult, TextModelSelection
from daon_user_api.question_answering_postgres import (
    PostgresQuestionAnsweringRepository,
    QuestionContext,
    QuestionRepositoryError,
)
from daon_user_api.document_index_postgres import IndexedEvidenceChunk
from daon_user_api.studio_report import (
    StudioReportContext,
    StudioReportCreateRequest,
    StudioReportError,
)
from daon_user_api.studio_report_postgres import PostgresStudioReportRepository
from daon_user_api.data_canon import CanonicalContext, CanonError, PostgresDataCanonStore
from daon_user_api.document_processing import DocumentProcessingContext
from daon_user_api.document_processing_postgres import PostgresDocumentProcessingRepository
from daon_user_api.document_understanding_adapter import DocumentUnderstandingError
from daon_user_api.egress_policy import EffectiveEgressPolicy
from daon_user_api.provider_settings import (
    ModelDeploymentView,
    ProviderProfileView,
    ProviderSettingsSnapshot,
)
from daon_user_api.question_answering_service import (
    QuestionAnsweringError,
    QuestionAnsweringService,
)
from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
from daon_user_api.audit import AuditEventStore
from daon_user_api.retention import DerivativeInput, RetentionContext, RetentionError
from daon_user_api.retention_inventory_postgres import PostgresRetentionInventoryProvider
from daon_user_api.retention_request_postgres import PostgresRetentionRequestService
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import ClientKind, IdentityPrincipal
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from test_identity_support import POLICY_VERSION, create_service


DSN = os.getenv("DAON_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="DAON_TEST_POSTGRES_DSN is required")


def test_actual_postgres_external_policy_exact_mismatch_has_zero_domain_write() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    context = QuestionContext(
        "tenant-egress-preflight", "workspace-egress-preflight", "user-egress-preflight",
        "trace-egress-preflight", "policy-v1", "notebook-egress-preflight",
    )
    tables = (
        "provider_profiles", "model_artifacts", "model_deployments", "routing_policy_versions",
        "routing_decisions", "egress_decisions", "runs", "model_attempts", "audit_events",
    )

    class PolicyBoundary:
        def get_effective(self, _context):  # type: ignore[no-untyped-def]
            return EffectiveEgressPolicy(
                "organization-policy", "organization-binding", "workspace-policy",
                "workspace-binding", "allow_approved_external", ("external_api",),
                ("api.groq.com",), "internal", 1_048_576, True, True,
                "organization_admin", False, "sha256:" + "a" * 64,
                '"effective"', '"organization"', '"workspace"', {}, {},
            )

    def counts() -> tuple[int, ...]:
        with store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "question.test",
        )) as connection:
            return tuple(int(connection.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id=%s AND workspace_id=%s",
                (context.tenant_id, context.workspace_id),
            ).fetchone()[0]) for table in tables)

    try:
        store.seed_scope(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "question.test",
        ))
        before = counts()
        authorizer = PostgresQuestionEgressAuthorizer(store, PolicyBoundary())  # type: ignore[arg-type]
        payload = b'{"question":"masked"}'
        with pytest.raises(QuestionAnsweringError) as denied:
            authorizer.authorize(
                context, run_id="run-egress-preflight", source_id=None,
                source_version_id=None,
                selection=TextModelSelection(
                    "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                    "deployment-upstage", "solar-pro", 1, "external_api",
                ),
                provider_payload=payload,
                approved_authorization={
                    "policy_fingerprint": "sha256:" + "a" * 64,
                    "provider_payload_fingerprint": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "provider_kind": "external_api", "deployment_id": "deployment-upstage",
                },
            )
        assert (denied.value.code, denied.value.status) == ("EGRESS_POLICY_DENIED", 403)
        assert counts() == before == (0,) * len(tables)
    finally:
        store.close()


def test_actual_postgres_external_full_service_persists_complete_run_and_http_replays() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    notebooks = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    scope = NotebookContext(
        "tenant-external-full", "workspace-external-full", "user-external-full",
        "trace-external-full", "policy-v1",
    )
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)

    class ProviderSettings:
        def snapshot(self, _context):  # type: ignore[no-untyped-def]
            return ProviderSettingsSnapshot(
                scope.workspace_id,
                (ProviderProfileView(
                    "profile-upstage", "UPSTAGE", "external_api",
                    "https://api.upstage.ai/v1", True, True, 1,
                ),),
                (ModelDeploymentView(
                    "deployment-upstage", "profile-upstage", "UPSTAGE", "solar-pro",
                    ("text",), True, True, 1,
                ),),
                {"text": "deployment-upstage"}, 1,
            )

    class Credential:
        def resolve(self, _provider_code: str) -> str:
            return "test-only-secret"

    class Transport:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = Lock()
            self.started = Event()
            self.release = Event()
            self.block = False

        def post_json(self, **_kwargs):  # type: ignore[no-untyped-def]
            with self.lock:
                self.calls += 1
            self.started.set()
            if self.block:
                assert self.release.wait(2), "transport release timeout"
            return {"choices": [{"message": {"content": json.dumps({
                "answer": "안녕하세요. 무엇을 도와드릴까요?",
            }, ensure_ascii=False)}}]}

    class EmptyIndex:
        def search(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return ()

    class PolicyBoundary:
        def __init__(self) -> None:
            self.calls = 0

        def get_effective(self, _context):  # type: ignore[no-untyped-def]
            self.calls += 1
            return EffectiveEgressPolicy(
                "organization-policy", "organization-binding", "workspace-policy",
                "workspace-binding", "allow_approved_external", ("external_api",),
                ("api.upstage.ai",), "internal", 1_048_576, True, True,
                "organization_admin", False, "sha256:" + "a" * 64,
                '"effective"', '"organization"', '"workspace"', {}, {},
            )

    class BindingBoundary:
        def __init__(self) -> None:
            self.calls = 0

        def require_selected_bindings(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            self.calls += 1

    class UnusedObjectStorage:
        def get(self, _key: str) -> bytes:
            raise AssertionError("general conversation must not read object storage")

    try:
        store.seed_scope(CloudAccessContext(
            scope.tenant_id, scope.workspace_id, scope.actor_id, "question.full.test",
        ))
        licenses.apply(
            LicenseContext(
                scope.tenant_id, scope.workspace_id, scope.actor_id,
                scope.trace_id, scope.policy_version,
            ),
            VerifiedLicense(
                license_id="license-external-full-001", product="daon-user",
                edition="enterprise", organization_id=scope.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365),
                features=("notebook_management",), resource_limits=(("notebooks", 10),),
                claims_digest="4" * 64, key_id="release-1",
            ), idempotency_key="license-external-full-apply",
            request_fingerprint="5" * 64,
        )
        notebook, _ = notebooks.create(
            scope, title="External full", description=None,
            idempotency_key="external-full-notebook-create",
            request_fingerprint="6" * 64, now=now,
        )
        repository = PostgresQuestionAnsweringRepository(
            store, UnusedObjectStorage(),  # type: ignore[arg-type]
        )
        policy = PolicyBoundary()
        transport = Transport()
        question_service = QuestionAnsweringService(
            ProviderSettings(), repository, EmptyIndex(), Credential(), transport,
            PostgresQuestionEgressAuthorizer(store, policy),  # type: ignore[arg-type]
        )
        binding = BindingBoundary()

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            audit = AuditEventStore()
            identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
            authorization_repository = SqliteAuthorizationRepository(db_path)
            principal = IdentityPrincipal(
                scope.actor_id, "session-external-full", "device-external-full", scope.tenant_id,
            )
            authorization_repository.bootstrap_workspace(
                tenant_id=scope.tenant_id, workspace_id=scope.workspace_id,
                owner_user_id=scope.actor_id, owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal", data_area="cloud_sync",
                cost_limit_cents=1000, now=clock(),
            )
            authorization = AuthorizationService(
                repository=authorization_repository, audit_store=audit, clock=clock,
                identity_service=identity,
            )
            dependencies = RuntimeDependencies(
                settings=RuntimeSettings.for_test(
                    database_path=db_path, policy_version=POLICY_VERSION,
                ),
                identity_service=identity, authorization_service=authorization,
                audit_store=audit, identity_repository=identity_repository,
                authorization_repository=authorization_repository,
                question_answering_service=question_service,
                notebook_service=binding,  # type: ignore[arg-type]
                egress_policy_service=policy,  # type: ignore[arg-type]
            )
            body = {"notebook_id": notebook.notebook_id, "question": "안녕하세요!"}
            with patch.object(identity, "describe_access", return_value=SimpleNamespace(
                client_kind=ClientKind.WEB, principal=principal,
            )):
                with TestClient(create_app(dependencies)) as client:
                    first = client.post(
                        f"/api/v1/workspaces/{scope.workspace_id}/questions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "question-external-full-0001"},
                        json=body,
                    )
                    assert first.status_code == 200, first.text
                    first_data = first.json()["data"]
                    first_calls = transport.calls
                    replay = client.post(
                        f"/api/v1/workspaces/{scope.workspace_id}/questions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "question-external-full-0001"},
                        json=body,
                    )
                    assert replay.status_code == 200, replay.text
                    assert replay.json()["data"] == first_data
                    assert transport.calls == first_calls == 1

            concurrent_run_id = "run-external-concurrent"
            concurrent_fingerprint = "sha256:" + "d" * 64
            prepared = question_service.prepare_authorization(
                QuestionContext(
                    scope.tenant_id, scope.workspace_id, scope.actor_id,
                    scope.trace_id, scope.policy_version, notebook.notebook_id,
                ), source_id=None, source_version_id=None, question="안녕하세요!",
                context_mode="general_ungrounded", context_sources=(),
            )
            approval = {
                "request_fingerprint": "sha256:" + "e" * 64,
                "policy_fingerprint": "sha256:" + "a" * 64,
                "provider_payload_fingerprint": "sha256:" + hashlib.sha256(
                    prepared.provider_payload,
                ).hexdigest(),
                "provider_kind": "external_api",
                "deployment_id": "deployment-upstage",
            }
            transport.block = True
            transport.started.clear()
            before_concurrent_calls = transport.calls

            def ask_concurrently() -> object:
                return question_service.ask(
                    QuestionContext(
                        scope.tenant_id, scope.workspace_id, scope.actor_id,
                        scope.trace_id, scope.policy_version, notebook.notebook_id,
                    ),
                    source_id=None, source_version_id=None, question="안녕하세요!",
                    run_id=concurrent_run_id, approved_authorization=approval,
                    context_mode="general_ungrounded", context_sources=(),
                    request_fingerprint=concurrent_fingerprint,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                owner = pool.submit(ask_concurrently)
                assert transport.started.wait(2)
                follower = pool.submit(ask_concurrently)
                time.sleep(0.1)
                transport.release.set()
                owner_result = owner.result(timeout=3)
                follower_result = follower.result(timeout=3)
            assert owner_result == follower_result
            assert transport.calls - before_concurrent_calls == 1

            with store._transaction(CloudAccessContext(
                scope.tenant_id, scope.workspace_id, scope.actor_id, "question.full.test",
            )) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM runs WHERE record_id=%s",
                    (concurrent_run_id,),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM run_results WHERE run_id=%s",
                    (concurrent_run_id,),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM egress_decisions WHERE run_id=%s",
                    (concurrent_run_id,),
                ).fetchone()[0] == 1
                audit_counts = dict(connection.execute(
                    "SELECT action,count(*) FROM audit_events WHERE target_id=%s "
                    "GROUP BY action ORDER BY action",
                    (concurrent_run_id,),
                ).fetchall())
                assert audit_counts == {
                    "canon.transition": 5,
                    "question.answer": 1,
                    "question.egress.authorize": 1,
                }

            mixed_run_id = "run-external-concurrent-mismatch"
            transport.started.clear()
            transport.release.clear()
            before_mixed_calls = transport.calls

            def ask_mixed(fingerprint: str) -> object:
                return question_service.ask(
                    QuestionContext(
                        scope.tenant_id, scope.workspace_id, scope.actor_id,
                        scope.trace_id, scope.policy_version, notebook.notebook_id,
                    ),
                    source_id=None, source_version_id=None, question="안녕하세요!",
                    run_id=mixed_run_id, approved_authorization=approval,
                    context_mode="general_ungrounded", context_sources=(),
                    request_fingerprint=fingerprint,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                mixed_owner = pool.submit(ask_mixed, "sha256:" + "1" * 64)
                assert transport.started.wait(2)
                mixed_follower = pool.submit(ask_mixed, "sha256:" + "2" * 64)
                time.sleep(0.1)
                transport.release.set()
                mixed_owner.result(timeout=3)
                with pytest.raises(QuestionAnsweringError) as mismatch:
                    mixed_follower.result(timeout=3)
            assert (mismatch.value.code, mismatch.value.status) == (
                "IDEMPOTENCY_KEY_REUSED", 409,
            )
            assert transport.calls - before_mixed_calls == 1
            with store._transaction(CloudAccessContext(
                scope.tenant_id, scope.workspace_id, scope.actor_id, "question.full.test",
            )) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM runs WHERE record_id=%s", (mixed_run_id,),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM run_results WHERE run_id=%s", (mixed_run_id,),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM egress_decisions WHERE run_id=%s", (mixed_run_id,),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM audit_events WHERE target_id=%s", (mixed_run_id,),
                ).fetchone()[0] == 7

            run_id = first_data["run_id"]
            with store._transaction(CloudAccessContext(
                scope.tenant_id, scope.workspace_id, scope.actor_id, "question.full.test",
            )) as connection:
                run = connection.execute(
                    "SELECT canonical_json,conversation_id FROM runs WHERE record_id=%s",
                    (run_id,),
                ).fetchone()
            assert run[0]["request_fingerprint"].startswith("sha256:")
            assert run[0]["provider_kind"] == "external_api"
            assert run[0]["egress_scope"]["destination"] == "api.upstage.ai"
            assert isinstance(run[1], str) and run[1]
            dependencies.close()
    finally:
        store.close()


def test_actual_postgres_notebook_replay_scope_metadata_and_rls() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    repository = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    context = NotebookContext("tenant-notebook-a", "workspace-notebook-a", "user-notebook-a", "trace-notebook", "policy-v1")
    foreign = NotebookContext("tenant-notebook-b", "workspace-notebook-b", "user-notebook-b", "trace-notebook", "policy-v1")
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    try:
        for item in (context, foreign):
            store.seed_scope(CloudAccessContext(item.tenant_id, item.workspace_id, item.actor_id, "notebook.test"))
        licenses.apply(
            LicenseContext(context.tenant_id, context.workspace_id, context.actor_id, context.trace_id, context.policy_version),
            VerifiedLicense(
                license_id="license-notebook-pg-001", product="daon-user", edition="enterprise",
                organization_id=context.tenant_id, issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365),
                features=("notebook_management",), resource_limits=(("notebooks", 10),), claims_digest="c" * 64, key_id="release-1",
            ), idempotency_key="license-notebook-apply-0001", request_fingerprint="d" * 64,
        )
        created, replay = repository.create(
            context, title="Notebook", description=None, idempotency_key="notebook-pg-create-0001",
            request_fingerprint="a" * 64, now=now,
        )
        same, repeated = repository.create(
            context, title="Notebook", description=None, idempotency_key="notebook-pg-create-0001",
            request_fingerprint="a" * 64, now=now,
        )
        assert created == same and replay is False and repeated is True
        assert repository.list(foreign) == ()
        with pytest.raises(NotebookError, match="NOTEBOOK_NOT_FOUND"):
            repository.get(foreign, created.notebook_id)
        updated, _ = repository.update_title(
            context, created.notebook_id, title="Notebook 2", expected_etag=created.etag,
            idempotency_key="notebook-pg-title-0001", request_fingerprint="b" * 64, now=now,
        )
        assert updated.etag == '"notebook:2"'
        original_create, create_replayed_after_update = repository.create(
            context, title="Notebook", description=None, idempotency_key="notebook-pg-create-0001",
            request_fingerprint="a" * 64, now=now,
        )
        assert original_create.etag == '"notebook:1"' and create_replayed_after_update is True
        with store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "notebook.test")) as connection:
            assert connection.execute(
                "SELECT count(*) FROM notebook_metadata_versions WHERE tenant_id=%s AND workspace_id=%s AND notebook_id=%s",
                (context.tenant_id, context.workspace_id, created.notebook_id),
            ).fetchone()[0] == 2
    finally:
        store.close()


def test_actual_postgres_notebook_limit_is_atomic_across_two_connections() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    licenses = PostgresLicenseRepository(store)
    context = NotebookContext("tenant-notebook-quota", "workspace-notebook-quota", "user-notebook-quota", "trace-notebook-quota", "policy-v1")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        store.seed_scope(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "notebook.test"))
        licenses.apply(
            LicenseContext(context.tenant_id, context.workspace_id, context.actor_id, context.trace_id, context.policy_version),
            VerifiedLicense(
                license_id="license-notebook-quota-001", product="daon-user", edition="enterprise", organization_id=context.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365), features=("notebook_management",),
                resource_limits=(("notebooks", 1),), claims_digest="e" * 64, key_id="release-1",
            ), idempotency_key="license-notebook-quota-apply", request_fingerprint="f" * 64,
        )
        barrier = Barrier(2)
        def create(index: int) -> str:
            worker_store = PostgresCloudStore(DSN, min_size=1, max_size=1)
            worker = PostgresNotebookRepository(worker_store, creation_enforcer=enforce_license_creation)
            try:
                barrier.wait(timeout=5)
                worker.create(context, title=f"Notebook {index}", description=None, idempotency_key=f"notebook-quota-create-{index:04d}", request_fingerprint=str(index) * 64, now=now)
                return "stored"
            except LicenseError as error:
                return error.code
            finally:
                worker_store.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, range(2)))
        assert sorted(results) == ["LICENSE_RESOURCE_LIMIT_REACHED", "stored"]
        assert len(PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation).list(context)) == 1
    finally:
        store.close()


def _seed_context_targets(store: PostgresCloudStore, context: NotebookContext, now: datetime) -> None:
    access = CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "notebook.test")
    common = (context.tenant_id, context.workspace_id, context.actor_id, context.trace_id, now)
    digest = hashlib.sha256(b"{}").hexdigest()
    with store._transaction(access) as connection:
        connection.execute(
            "INSERT INTO sources (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,created_by,trace_id,created_at) VALUES (%s,%s,'source-ctx-1','source-ctx-1',%s,%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO source_versions (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,source_id,created_by,trace_id,created_at) VALUES (%s,%s,'source-version-ctx-1','source-version-ctx-1',%s,'source-ctx-1',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO sources (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,created_by,trace_id,created_at) VALUES (%s,%s,'source-ctx-2','source-ctx-2',%s,%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO source_versions (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,source_id,created_by,trace_id,created_at) VALUES (%s,%s,'source-version-ctx-2','source-version-ctx-2',%s,'source-ctx-2',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO evidence_spans (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,source_version_id,created_by,trace_id,created_at) VALUES (%s,%s,'span-question-context','span-question-context',%s,'source-version-ctx-1',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO knowledge_scopes (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,created_by,trace_id,created_at) VALUES (%s,%s,'knowledge-scope-ctx-1','knowledge-scope-ctx-1',%s,%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO scope_snapshots (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,knowledge_scope_id,created_by,trace_id,created_at) VALUES (%s,%s,'scope-snapshot-ctx-1','scope-snapshot-ctx-1',%s,'knowledge-scope-ctx-1',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO conversations (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,created_by,trace_id,created_at) VALUES (%s,%s,'conversation-ctx-1','conversation-ctx-1',%s,%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO generation_settings_snapshots (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,created_by,trace_id,created_at) VALUES (%s,%s,'generation-settings-ctx-1','generation-settings-ctx-1',%s,%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO generation_requests (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,generation_settings_snapshot_id,created_by,trace_id,created_at) VALUES (%s,%s,'generation-request-ctx-1','generation-request-ctx-1',%s,'generation-settings-ctx-1',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO studio_outputs (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,generation_request_id,created_by,trace_id,created_at) VALUES (%s,%s,'studio-output-ctx-1','studio-output-ctx-1',%s,'generation-request-ctx-1',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )
        connection.execute(
            "INSERT INTO output_versions (tenant_id,workspace_id,record_id,aggregate_id,digest_sha256,studio_output_id,generation_settings_snapshot_id,created_by,trace_id,created_at) VALUES (%s,%s,'output-version-ctx-1','output-version-ctx-1',%s,'studio-output-ctx-1','generation-settings-ctx-1',%s,%s,%s)",
            (common[0], common[1], digest, common[2], common[3], common[4]),
        )


def _transition_context_source_ready(
    store: PostgresCloudStore, context: NotebookContext,
) -> None:
    access = CloudAccessContext(
        context.tenant_id, context.workspace_id, context.actor_id, "notebook.test",
    )
    with store._transaction(access) as connection:
        version = 1
        for target in ("security_check", "processing", "indexing", "ready"):
            row = connection.execute(
                "SELECT state,version,outcome FROM transition_canon_state("
                "'Source','source-ctx-1',%s,%s,%s,'notebook.test',%s,%s)",
                (version, target, f"source-context-{target}", context.trace_id, context.policy_version),
            ).fetchone()
            assert row is not None and row[2] == "succeeded"
            version = int(row[1])


def test_actual_postgres_notebook_context_bind_read_scope_and_empty() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    repository = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    context = NotebookContext("tenant-notebook-context", "workspace-notebook-context", "user-notebook-context", "trace-notebook-context", "policy-v1")
    foreign = NotebookContext("tenant-notebook-foreign", "workspace-notebook-foreign", "user-notebook-foreign", "trace-notebook-foreign", "policy-v1")
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    try:
        for item in (context, foreign):
            store.seed_scope(CloudAccessContext(item.tenant_id, item.workspace_id, item.actor_id, "notebook.test"))
        licenses.apply(
            LicenseContext(context.tenant_id, context.workspace_id, context.actor_id, context.trace_id, context.policy_version),
            VerifiedLicense(
                license_id="license-notebook-context-001", product="daon-user", edition="enterprise", organization_id=context.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365), features=("notebook_management",),
                resource_limits=(("notebooks", 10),), claims_digest="2" * 64, key_id="release-1",
            ), idempotency_key="license-notebook-context-apply", request_fingerprint="3" * 64,
        )
        existing, _ = repository.create(context, title="Existing", description=None, idempotency_key="notebook-context-create-1001", request_fingerprint="4" * 64, now=now)
        empty, _ = repository.create(context, title="Empty", description=None, idempotency_key="notebook-context-create-1002", request_fingerprint="5" * 64, now=now)
        _seed_context_targets(store, context, now)
        targets = (
            ("source", "source-ctx-1", "source-version-ctx-1"),
            ("source", "source-ctx-2", "source-version-ctx-2"),
            ("knowledge_context", "scope-snapshot-ctx-1", None),
            ("conversation_thread", "conversation-ctx-1", None),
            ("studio_output", "studio-output-ctx-1", None),
            ("output_version", "output-version-ctx-1", None),
            ("generation_settings", "generation-settings-ctx-1", None),
        )
        for kind, record_id, version_id in targets:
            assert repository.bind_verified(context, existing.notebook_id, binding_kind=kind, record_id=record_id, version_id=version_id, now=now) is False
        assert repository.bind_verified(context, existing.notebook_id, binding_kind="source", record_id="source-ctx-1", version_id="source-version-ctx-1", now=now) is True
        selected = repository.read_selected_context(context, existing.notebook_id)
        assert selected.sources == (
            ("source-ctx-1", "source-version-ctx-1"),
            ("source-ctx-2", "source-version-ctx-2"),
        )
        assert selected.knowledge_context_ids == ("scope-snapshot-ctx-1",)
        assert selected.conversation_thread_ids == ("conversation-ctx-1",)
        assert selected.studio_output_ids == ("studio-output-ctx-1",)
        assert selected.output_version_ids == ("output-version-ctx-1",)
        assert selected.generation_settings_ids == ("generation-settings-ctx-1",)
        barrier = Barrier(2)
        def unbind(index: int):  # type: ignore[no-untyped-def]
            source_number = 1 if index == 6 else 2
            barrier.wait()
            try:
                return repository.unbind_source(
                    context, existing.notebook_id,
                    source_id=f"source-ctx-{source_number}", source_version_id=f"source-version-ctx-{source_number}",
                    expected_etag=selected.etag,
                    idempotency_key=f"notebook-context-unbind-{index}",
                    request_fingerprint=str(index) * 64, now=now,
                )
            except NotebookError as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(unbind, (6, 7)))
        successes = [item for item in outcomes if not isinstance(item, NotebookError)]
        failures = [item for item in outcomes if isinstance(item, NotebookError)]
        assert len(successes) == 1 and len(failures) == 1, [
            (item.code, item.status) if isinstance(item, NotebookError) else (item[0].status, item[1])
            for item in outcomes
        ]
        assert (failures[0].code, failures[0].status) == ("NOTEBOOK_BINDING_ETAG_MISMATCH", 412)
        winner, replayed = successes[0]
        assert replayed is False and winner.status == "unbound"
        winner_index = 6 if winner.etag == '"notebook-binding:2"' and not isinstance(outcomes[0], NotebookError) else 7
        replay, repeated = repository.unbind_source(
            context, existing.notebook_id,
            source_id=winner.source_id, source_version_id=winner.source_version_id,
            expected_etag=selected.etag,
            idempotency_key=f"notebook-context-unbind-{winner_index}",
            request_fingerprint=str(winner_index) * 64, now=now,
        )
        assert repeated is True and replay == winner
        remaining = repository.read_selected_context(context, existing.notebook_id).sources
        assert len(remaining) == 1 and remaining[0][0] != winner.source_id
        with store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "notebook.test")) as connection:
            assert connection.execute("SELECT count(*) FROM notebook_source_unbindings WHERE notebook_id=%s", (existing.notebook_id,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM audit_events WHERE action='notebook.source_unbound' AND target_id=%s", (existing.notebook_id,)).fetchone()[0] == 1
        evidence_path = os.getenv("DAON_NOTEBOOK_CONTEXT_EVIDENCE_PATH")
        if evidence_path:
            Path(evidence_path).write_text(json.dumps({
                "notebook_id": selected.notebook_id,
                "sources": [
                    {"source_id": source_id, "source_version_id": version_id}
                    for source_id, version_id in selected.sources
                ],
                "knowledge_context_ids": list(selected.knowledge_context_ids),
                "conversation_thread_ids": list(selected.conversation_thread_ids),
                "studio_output_ids": list(selected.studio_output_ids),
                "output_version_ids": list(selected.output_version_ids),
                "generation_settings_ids": list(selected.generation_settings_ids),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        assert repository.read_selected_context(context, empty.notebook_id).is_empty is True
        with pytest.raises(NotebookError, match="NOTEBOOK_NOT_FOUND"):
            repository.read_selected_context(foreign, existing.notebook_id)
        with pytest.raises(NotebookError, match="NOTEBOOK_BINDING_TARGET_NOT_FOUND"):
            repository.bind_verified(context, empty.notebook_id, binding_kind="source", record_id="source-ctx-1", version_id="source-version-missing", now=now)
        assert repository.read_selected_context(context, empty.notebook_id).is_empty is True
    finally:
        store.close()


def test_actual_postgres_title_update_audit_replay_and_failure_are_atomic() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    repository = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    context = NotebookContext("tenant-notebook-audit", "workspace-notebook-audit", "user-notebook-audit", "trace-notebook-audit", "policy-v1")
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    try:
        store.seed_scope(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "notebook.test"))
        licenses.apply(
            LicenseContext(context.tenant_id, context.workspace_id, context.actor_id, context.trace_id, context.policy_version),
            VerifiedLicense(
                license_id="license-notebook-audit-001", product="daon-user", edition="enterprise", organization_id=context.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365), features=("notebook_management",),
                resource_limits=(("notebooks", 10),), claims_digest="6" * 64, key_id="release-1",
            ), idempotency_key="license-notebook-audit-apply", request_fingerprint="7" * 64,
        )
        created, _ = repository.create(context, title="Before", description=None, idempotency_key="notebook-audit-create-0001", request_fingerprint="8" * 64, now=now)
        updated, replay = repository.update_title(context, created.notebook_id, title="After", expected_etag=created.etag, idempotency_key="notebook-audit-title-0001", request_fingerprint="9" * 64, now=now)
        same, repeated = repository.update_title(context, created.notebook_id, title="After", expected_etag=created.etag, idempotency_key="notebook-audit-title-0001", request_fingerprint="9" * 64, now=now)
        assert updated == same and replay is False and repeated is True
        access = CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "notebook.test")
        with store._transaction(access) as connection:
            assert connection.execute("SELECT count(*) FROM audit_events WHERE action='notebook.title_updated' AND target_id=%s", (created.notebook_id,)).fetchone()[0] == 1
            failed_key = "notebook-audit-title-0002"
            failed_event_id = "notebook-audit-" + hashlib.sha256(f"title|{context.tenant_id}|{context.actor_id}|{failed_key}".encode()).hexdigest()[:24]
            connection.execute(
                "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata) VALUES (%s,%s,%s,%s,'notebook.audit_collision','notebook',%s,'succeeded',%s,%s,'{}'::jsonb)",
                (failed_event_id, context.tenant_id, context.workspace_id, context.actor_id, created.notebook_id, context.trace_id, context.policy_version),
            )
        before = repository.get(context, created.notebook_id)
        with pytest.raises(NotebookError, match="NOTEBOOK_UNAVAILABLE"):
            repository.update_title(context, created.notebook_id, title="Must Roll Back", expected_etag=before.etag, idempotency_key=failed_key, request_fingerprint="a" * 64, now=now)
        assert repository.get(context, created.notebook_id) == before
        with store._transaction(access) as connection:
            assert connection.execute("SELECT count(*) FROM notebook_activities WHERE notebook_id=%s AND activity_kind='title_updated'", (created.notebook_id,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM notebook_idempotency WHERE idempotency_key=%s", (failed_key,)).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM audit_events WHERE action='notebook.title_updated' AND target_id=%s", (created.notebook_id,)).fetchone()[0] == 1
    finally:
        store.close()


def test_actual_postgres_question_result_is_bound_to_selected_notebook_atomically() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    notebooks = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    notebook_context = NotebookContext(
        "tenant-question-context", "workspace-question-context", "user-question-context",
        "trace-question-context", "policy-v1",
    )
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)

    class UnusedObjectStorage:
        def get(self, _key: str) -> bytes:
            raise AssertionError("persist_completed must not read object storage")

    try:
        store.seed_scope(CloudAccessContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, "notebook.test",
        ))
        licenses.apply(
            LicenseContext(
                notebook_context.tenant_id, notebook_context.workspace_id,
                notebook_context.actor_id, notebook_context.trace_id,
                notebook_context.policy_version,
            ),
            VerifiedLicense(
                license_id="license-question-context-001", product="daon-user",
                edition="enterprise", organization_id=notebook_context.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365),
                features=("notebook_management",), resource_limits=(("notebooks", 10),),
                claims_digest="b" * 64, key_id="release-1",
            ),
            idempotency_key="license-question-context-apply",
            request_fingerprint="c" * 64,
        )
        selected, _ = notebooks.create(
            notebook_context, title="Selected", description=None,
            idempotency_key="question-context-create-0001",
            request_fingerprint="d" * 64, now=now,
        )
        unselected, _ = notebooks.create(
            notebook_context, title="Unselected", description=None,
            idempotency_key="question-context-create-0002",
            request_fingerprint="e" * 64, now=now,
        )
        _seed_context_targets(store, notebook_context, now)
        _transition_context_source_ready(store, notebook_context)
        notebooks.bind_verified(
            notebook_context, selected.notebook_id, binding_kind="source",
            record_id="source-ctx-1", version_id="source-version-ctx-1", now=now,
        )
        repository = PostgresQuestionAnsweringRepository(store, UnusedObjectStorage())  # type: ignore[arg-type]
        context = QuestionContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, notebook_context.trace_id,
            notebook_context.policy_version, notebook_id=selected.notebook_id,
        )
        evidence = (IndexedEvidenceChunk(
            "chunk-question-context", "source-ctx-1", "source-version-ctx-1", 1,
            "verified evidence", "span-question-context", 1.0,
        ),)
        stored = repository.persist_completed(
            context, run_id="run-question-context-1", source_id="source-ctx-1",
            source_version_id="source-version-ctx-1", question="What is verified?",
            selection=TextModelSelection(
                "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                "deployment-text", "solar-pro4", 1,
            ),
            evidence=evidence,
            result=GroundedTextResult(
                "verified evidence", ("chunk-question-context",), False,
                {"total_tokens": 8},
            ),
        )
        assert repository.load_completed(context, stored.run_id) == stored
        selected_projection = notebooks.read_selected_context(
            notebook_context, selected.notebook_id,
        )
        assert len(selected_projection.conversation_thread_ids) == 1
        assert selected_projection.conversation is not None
        assert selected_projection.conversation.answer == "verified evidence"

        studio = PostgresStudioReportRepository(store)
        studio_context = StudioReportContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, notebook_context.trace_id,
            notebook_context.policy_version, selected.notebook_id,
        )
        report_request = StudioReportCreateRequest(
            "source-ctx-1", "source-version-ctx-1", stored.run_id,
            stored.run_result_id, "Verified report", "Actual PostgreSQL scope",
        )
        report, replayed = studio.create_report(
            studio_context, report_request, "studio-question-context-0001",
        )
        same_report, repeated = studio.create_report(
            studio_context, report_request, "studio-question-context-0001",
        )
        assert replayed is False and repeated is True and same_report == report
        selected_after_report = notebooks.read_selected_context(
            notebook_context, selected.notebook_id,
        )
        assert report.studio_output_id in selected_after_report.studio_output_ids
        assert report.output_version_id in selected_after_report.output_version_ids
        assert len(studio.list_outputs(StudioReportContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, notebook_context.trace_id,
            notebook_context.policy_version, unselected.notebook_id,
        ))) == 0
        with pytest.raises(StudioReportError, match="RESOURCE_UNAVAILABLE"):
            studio.create_report(
                StudioReportContext(
                    notebook_context.tenant_id, notebook_context.workspace_id,
                    notebook_context.actor_id, notebook_context.trace_id,
                    notebook_context.policy_version, unselected.notebook_id,
                ), report_request, "studio-question-context-rejected",
            )

        rejected_context = QuestionContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, notebook_context.trace_id,
            notebook_context.policy_version, notebook_id=unselected.notebook_id,
        )
        with pytest.raises(QuestionRepositoryError, match="NOTEBOOK_SCOPE_MISMATCH"):
            repository.persist_completed(
                rejected_context, run_id="run-question-context-rejected",
                source_id="source-ctx-1", source_version_id="source-version-ctx-1",
                question="Must fail", selection=TextModelSelection(
                    "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                    "deployment-text", "solar-pro4", 1,
                ), evidence=evidence, result=GroundedTextResult(
                    "must not persist", ("chunk-question-context",), False,
                    {"total_tokens": 8},
                ),
            )
        with store._transaction(CloudAccessContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, "question.test",
        )) as connection:
            assert connection.execute(
                "SELECT count(*) FROM runs WHERE record_id='run-question-context-rejected'",
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM audit_events WHERE action='question.answer' "
                "AND target_id='run-question-context-1'",
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM idempotency_records WHERE operation='studio.report.create' "
                "AND idempotency_key='studio-question-context-rejected'",
            ).fetchone()[0] == 0
    finally:
        store.close()


def test_actual_postgres_general_conversation_persists_selected_provider_lineage_without_source() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    notebooks = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    notebook_context = NotebookContext(
        "tenant-general-context", "workspace-general-context", "user-general-context",
        "trace-general-context", "policy-v1",
    )
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)

    class UnusedObjectStorage:
        def get(self, _key: str) -> bytes:
            raise AssertionError("general conversation must not read source storage")

    try:
        store.seed_scope(CloudAccessContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, "notebook.test",
        ))
        licenses.apply(
            LicenseContext(
                notebook_context.tenant_id, notebook_context.workspace_id,
                notebook_context.actor_id, notebook_context.trace_id,
                notebook_context.policy_version,
            ),
            VerifiedLicense(
                license_id="license-general-context-001", product="daon-user",
                edition="enterprise", organization_id=notebook_context.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365),
                features=("notebook_management",), resource_limits=(("notebooks", 10),),
                claims_digest="7" * 64, key_id="release-1",
            ), idempotency_key="license-general-context-apply",
            request_fingerprint="8" * 64,
        )
        notebook, _ = notebooks.create(
            notebook_context, title="General", description=None,
            idempotency_key="general-context-create-0001",
            request_fingerprint="9" * 64, now=now,
        )
        repository = PostgresQuestionAnsweringRepository(store, UnusedObjectStorage())  # type: ignore[arg-type]
        context = QuestionContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, notebook_context.trace_id,
            notebook_context.policy_version, notebook_id=notebook.notebook_id,
        )
        stored = repository.persist_completed(
            context, run_id="run-general-context-1", source_id=None,
            source_version_id=None, question="안녕하세요!",
            selection=TextModelSelection(
                "UPSTAGE", "https://api.upstage.ai/v1", "profile-upstage",
                "deployment-text", "solar-pro4", 1,
            ), evidence=(), result=GroundedTextResult(
                "안녕하세요. 무엇을 도와드릴까요?", (), False, {"total_tokens": 8},
            ), context_mode="general_ungrounded", context_sources=(),
            request_fingerprint="sha256:" + "c" * 64,
            egress_authorization={"frozen_routing_context": {
                "classification": "internal", "destination": "api.upstage.ai",
                "masking_required": True, "payload_bytes": 128,
                "redaction_required": True,
            }},
        )
        assert stored.citations == ()
        assert repository.load_completed(context, stored.run_id) == stored
        assert stored.provider_kind == "external_api"
        assert repository.load_completed_for_replay(
            context, stored.run_id, "sha256:" + "c" * 64,
        ) == stored
        selected = notebooks.read_selected_context(notebook_context, notebook.notebook_id)
        assert len(selected.conversation_thread_ids) == 1
        assert selected.conversation is not None
        assert selected.conversation.answer == "안녕하세요. 무엇을 도와드릴까요?"
        with store._transaction(CloudAccessContext(
            notebook_context.tenant_id, notebook_context.workspace_id,
            notebook_context.actor_id, "question.test",
        )) as connection:
            run = connection.execute(
                "SELECT canonical_json FROM runs WHERE record_id=%s", (stored.run_id,),
            ).fetchone()[0]
            assert run["source_id"] is None and run["source_version_id"] is None
            assert run["context_mode"] == "general_ungrounded"
            assert connection.execute(
                "SELECT count(*) FROM model_attempts WHERE canonical_json->>'run_id'=%s",
                (stored.run_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM citations WHERE canonical_json->>'run_result_id'=%s",
                (stored.run_result_id,),
            ).fetchone()[0] == 0
    finally:
        store.close()


def test_actual_postgres_source_registration_binds_only_after_canonical_commit() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    notebooks = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    canon = PostgresDataCanonStore(DSN, min_size=1, max_size=1)
    context = NotebookContext(
        "tenant-source-context", "workspace-source-context", "user-source-context",
        "trace-source-context", "policy-v1",
    )
    foreign = NotebookContext(
        "tenant-source-foreign", "workspace-source-foreign", "user-source-foreign",
        "trace-source-foreign", "policy-v1",
    )
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    digest = hashlib.sha256(b"%PDF-1.4\nphase-e-source\n").hexdigest()
    try:
        for item in (context, foreign):
            store.seed_scope(CloudAccessContext(
                item.tenant_id, item.workspace_id, item.actor_id, "source.test",
            ))
        licenses.apply(
            LicenseContext(
                context.tenant_id, context.workspace_id, context.actor_id,
                context.trace_id, context.policy_version,
            ),
            VerifiedLicense(
                license_id="license-source-context-001", product="daon-user",
                edition="enterprise", organization_id=context.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365),
                features=("notebook_management",), resource_limits=(("notebooks", 10),),
                claims_digest="1" * 64, key_id="release-1",
            ), idempotency_key="license-source-context-apply",
            request_fingerprint="2" * 64,
        )
        selected, _ = notebooks.create(
            context, title="Source selected", description=None,
            idempotency_key="source-context-create-0001",
            request_fingerprint="3" * 64, now=now,
        )
        with store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "source.test",
        )) as connection:
            connection.execute(
                "INSERT INTO object_records (tenant_id,workspace_id,object_id,area,staging_key,object_key,digest_sha256,byte_size,content_type,status,cleanup_pending,created_by,trace_id,idempotency_key,request_fingerprint,created_at,completed_at) "
                "VALUES (%s,%s,'object-source-context','source','staging/source-context','tenant-source-context/workspace-source-context/source/object-source-context',%s,27,'application/pdf','completed',false,%s,%s,'source-object-context-0001',%s,%s,%s)",
                (context.tenant_id, context.workspace_id, digest, context.actor_id,
                 context.trace_id, "4" * 64, now, now),
            )
        canon.register_uploaded_source(
            CanonicalContext(
                context.tenant_id, context.workspace_id, context.actor_id,
                "source.create", context.trace_id,
            ), notebook_id=selected.notebook_id,
            source_id="source-phase-e-actual", source_version_id="source-version-phase-e-actual",
            object_id="object-source-context", filename="phase-e.pdf",
            digest_sha256=digest, byte_size=27, created_at=now,
        )
        projection = notebooks.read_selected_context(context, selected.notebook_id)
        assert projection.sources == ((
            "source-phase-e-actual", "source-version-phase-e-actual",
        ),)
        with store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "retention.trigger.setup",
        )) as connection:
            connection.execute(
                "INSERT INTO sync_operations (tenant_id,workspace_id,operation_id,actor_id,target_area,state,version,preview_digest,policy_version,idempotency_key,request_fingerprint,trace_id,state_document,created_at,updated_at) "
                "VALUES (%s,%s,'retention-trigger-sync',%s,'cloud_sync','preview',1,%s,%s,'retention-trigger-sync-key',%s,%s,%s,%s,%s)",
                (context.tenant_id, context.workspace_id, context.actor_id, "7" * 64,
                 context.policy_version, "8" * 64, context.trace_id, Jsonb({}), now, now),
            )

        source_lock_key = (
            f"retention-source|{context.tenant_id}|{context.workspace_id}|source-phase-e-actual"
        )

        def set_scope(connection) -> None:  # type: ignore[no-untyped-def]
            connection.execute("SELECT set_config('app.tenant_id',%s,true)", (context.tenant_id,))
            connection.execute("SELECT set_config('app.workspace_id',%s,true)", (context.workspace_id,))
            connection.execute("SELECT set_config('app.actor_id',%s,true)", (context.actor_id,))
            connection.execute("SELECT set_config('app.capability','retention.inventory.read',true)")

        def insert_inventory_mutation(connection, kind: str, suffix: str) -> None:  # type: ignore[no-untyped-def]
            if kind == "source_version":
                aggregate_id = connection.execute(
                    "SELECT aggregate_id FROM source_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id='source-version-phase-e-actual'",
                    (context.tenant_id, context.workspace_id),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO source_versions (tenant_id,workspace_id,record_id,aggregate_id,version,previous_version_id,canonical_json,canonical_text,digest_sha256,source_id,object_id,created_by,trace_id,created_at) "
                    "VALUES (%s,%s,%s,%s,2,'source-version-phase-e-actual',%s,%s,%s,'source-phase-e-actual','object-source-context',%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, f"retention-trigger-sv-{suffix}",
                     aggregate_id, Jsonb({"source_id": "source-phase-e-actual", "version": 2}),
                     '{"source_id":"source-phase-e-actual","version":2}',
                     hashlib.sha256(b'{"source_id":"source-phase-e-actual","version":2}').hexdigest(),
                     context.actor_id, context.trace_id, now),
                )
            elif kind == "index":
                connection.execute(
                    "INSERT INTO index_versions (tenant_id,workspace_id,record_id,aggregate_id,canonical_json,canonical_text,digest_sha256,source_version_id,created_by,trace_id,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'source-version-phase-e-actual',%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, f"retention-trigger-index-{suffix}",
                     f"retention-trigger-index-{suffix}", Jsonb({"source_version_id": "source-version-phase-e-actual"}),
                     '{"source_version_id":"source-version-phase-e-actual"}',
                     hashlib.sha256(b'{"source_version_id":"source-version-phase-e-actual"}').hexdigest(),
                     context.actor_id, context.trace_id, now),
                )
            else:
                connection.execute(
                    "INSERT INTO sync_preview_items (tenant_id,workspace_id,operation_id,item_id,source_version_id,local_object_id,object_digest,byte_size,content_type,created_at) "
                    "VALUES (%s,%s,'retention-trigger-sync',%s,'source-version-phase-e-actual','object-source-context',%s,27,'application/pdf',%s)",
                    (context.tenant_id, context.workspace_id,
                     f"retention-trigger-sync-{suffix}", "b" * 64, now),
                )

        for mutation_kind in ("source_version", "index", "sync"):
            mutation_locked = Event()
            release_mutation = Event()

            def hold_mutation_lock(kind: str = mutation_kind) -> None:
                with store._pool.connection() as raw_connection:
                    with raw_connection.transaction():
                        set_scope(raw_connection)
                        insert_inventory_mutation(raw_connection, kind, "mutation-first")
                        mutation_locked.set()
                        assert release_mutation.wait(timeout=5)
                        raise RuntimeError("rollback trigger probe")

            with ThreadPoolExecutor(max_workers=1) as executor:
                mutation_future = executor.submit(hold_mutation_lock)
                if not mutation_locked.wait(timeout=5):
                    mutation_future.result(timeout=1)
                    raise AssertionError(f"{mutation_kind} mutation did not acquire Source lock")
                with store._pool.connection() as probe_connection:
                    with probe_connection.transaction():
                        set_scope(probe_connection)
                        acquired = probe_connection.execute(
                            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                            (source_lock_key,),
                        ).fetchone()[0]
                        assert acquired is False
                release_mutation.set()
                with pytest.raises(RuntimeError, match="rollback trigger probe"):
                    mutation_future.result(timeout=5)

            request_locked = Event()
            release_request = Event()

            def hold_request_lock() -> None:
                with store._pool.connection() as raw_connection:
                    with raw_connection.transaction():
                        set_scope(raw_connection)
                        raw_connection.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            (source_lock_key,),
                        )
                        request_locked.set()
                        assert release_request.wait(timeout=5)

            with ThreadPoolExecutor(max_workers=1) as executor:
                request_future = executor.submit(hold_request_lock)
                assert request_locked.wait(timeout=5)
                with store._pool.connection() as probe_connection:
                    with probe_connection.transaction():
                        set_scope(probe_connection)
                        probe_connection.execute("SET LOCAL lock_timeout='100ms'")
                        try:
                            acquired = probe_connection.execute(
                                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                                (source_lock_key,),
                            ).fetchone()[0]
                        except Exception as error:
                            acquired = False if getattr(error, "sqlstate", None) == "55P03" else None
                        assert acquired is False
                release_request.set()
                request_future.result(timeout=5)

        service_context = RetentionContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        for mutation_kind in ("source_version", "index", "sync"):
            inventory_started = Event()
            release_inventory = Event()

            class BlockingInventory:
                def read_in_transaction(self, connection, inventory_context, source_id):  # type: ignore[no-untyped-def]
                    inventory_started.set()
                    assert release_inventory.wait(timeout=10)
                    return PostgresRetentionInventoryProvider(store).read_in_transaction(
                        connection, inventory_context, source_id,
                    )

            blocking_service = PostgresRetentionRequestService(
                store, BlockingInventory(), clock=lambda: now,
            )
            create_result = {}

            def create_with_real_service() -> None:
                create_result["view"] = blocking_service.create_request(
                    service_context, source_id="source-phase-e-actual", inventory=None,
                    idempotency_key=f"retention-barrier-create-{mutation_kind}", if_match="*",
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                create_future = executor.submit(create_with_real_service)
                assert inventory_started.wait(timeout=10)
                with store._pool.connection() as mutation_connection:
                    with mutation_connection.transaction():
                        set_scope(mutation_connection)
                        mutation_connection.execute("SET LOCAL lock_timeout='100ms'")
                        try:
                            insert_inventory_mutation(
                                mutation_connection, mutation_kind, "service-blocked",
                            )
                            raise AssertionError("service-held Source lock was bypassed")
                        except Exception as error:
                            assert getattr(error, "sqlstate", None) == "55P03"
                release_inventory.set()
                create_future.result(timeout=10)
            assert create_result["view"].state == "grace_period"
            with store._pool.connection() as mutation_connection:
                with mutation_connection.transaction():
                    set_scope(mutation_connection)
                    insert_inventory_mutation(
                        mutation_connection, mutation_kind, "service-after-release",
                    )
            cancelled_barrier = blocking_service.cancel(
                service_context, create_result["view"].request_id,
                expected_version=1,
                idempotency_key=f"retention-barrier-cancel-{mutation_kind}",
            )
            assert cancelled_barrier.state == "cancelled"
        inventory = PostgresRetentionInventoryProvider(store)(
            RetentionContext(
                context.tenant_id, context.workspace_id, context.actor_id,
                context.trace_id, context.policy_version,
            ),
            "source-phase-e-actual",
        )
        assert tuple(item.kind for item in inventory) == (
            "original_content", "index", "preview", "cache",
            "known_local_copy", "sync_reference",
        )
        assert tuple(item.disposition for item in inventory) == (
            "present", "not_present", "not_applicable", "not_applicable",
            "verification_pending", "not_present",
        )
        assert inventory[0].reference_id == "object-source-context"
        assert inventory[4].acknowledgement_required is True
        with pytest.raises(RetentionError, match="DELETION_INVENTORY_INVALID"):
            PostgresRetentionInventoryProvider(store)(
                RetentionContext(
                    foreign.tenant_id, foreign.workspace_id, foreign.actor_id,
                    foreign.trace_id, foreign.policy_version,
                ),
                "source-phase-e-actual",
            )
        retention_context = RetentionContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        admin_retention_context = RetentionContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version, organization_admin=True,
        )
        hold_store = PostgresCloudStore(DSN, min_size=1, max_size=2)
        hold_service = PostgresRetentionRequestService(
            hold_store, PostgresRetentionInventoryProvider(hold_store), clock=lambda: now,
        )
        hold = hold_service.apply_legal_hold(
            admin_retention_context, source_id="source-phase-e-actual", expected_version=1,
            idempotency_key="retention-durable-hold-0001", step_up_verified=True,
        )
        hold_store.close()
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "retention-identity.sqlite3"
            audit = AuditEventStore()
            identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
            authorization_repository = SqliteAuthorizationRepository(db_path)
            principal = IdentityPrincipal(
                context.actor_id, "session-retention-http", "device-retention-http", context.tenant_id,
            )
            authorization_repository.bootstrap_workspace(
                tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                owner_user_id=context.actor_id, owner_role=Role.ORGANIZATION_ADMIN,
                workspace_kind="organization", data_area="cloud_sync",
                cost_limit_cents=1000, now=clock(),
            )
            authorization = AuthorizationService(
                repository=authorization_repository, audit_store=audit, clock=clock,
                identity_service=identity,
            )
            runtime_store = PostgresCloudStore(DSN, min_size=1, max_size=2)
            dependencies = RuntimeDependencies(
                settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
                identity_service=identity, authorization_service=authorization,
                audit_store=audit, identity_repository=identity_repository,
                authorization_repository=authorization_repository, cloud_store=runtime_store,
            )
            with patch.object(identity, "describe_access", return_value=SimpleNamespace(
                client_kind=ClientKind.WEB, principal=principal,
            )):
                with TestClient(create_app(dependencies)) as client:
                    created_response = client.post(
                        "/api/v1/sources/source-phase-e-actual/deletion-requests",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "retention-durable-create-0001", "If-Match": "*"},
                        json={},
                    )
                    assert created_response.status_code == 201, created_response.text
                    fetched_response = client.get(
                        f"/api/v1/deletion-requests/{created_response.json()['data']['request_id']}",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                    )
                    assert fetched_response.status_code == 200
                    requested_id = created_response.json()["data"]["request_id"]
        restart_store = PostgresCloudStore(DSN, min_size=1, max_size=2)
        restarted = PostgresRetentionRequestService(
            restart_store, PostgresRetentionInventoryProvider(restart_store), clock=lambda: now,
        )
        requested = restarted.get_request(retention_context, requested_id)
        assert requested.state == "blocked_by_hold" and requested.version == 1
        assert restarted.locate_workspace(context.tenant_id, requested.request_id) == context.workspace_id
        assert notebooks.read_selected_context(context, selected.notebook_id).sources == ()
        blocked_report_repository = PostgresStudioReportRepository(store)
        assert blocked_report_repository.list_sources(StudioReportContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version, selected.notebook_id,
        )) == ()
        with pytest.raises(RetentionError, match="DELETION_REQUEST_ACTIVE"):
            restarted.create_request(
                retention_context,
                source_id="source-phase-e-actual",
                inventory=None,
                idempotency_key="retention-durable-create-other",
                if_match="*",
            )
        released = restarted.release_legal_hold(
            admin_retention_context, hold.hold_id, expected_version=1,
            idempotency_key="retention-durable-release-0001", step_up_verified=True,
        )
        assert released.state == "released" and released.version == 2
        requested = restarted.get_request(retention_context, requested_id)
        assert requested.state == "grace_period" and requested.version == 2
        cancelled = restarted.cancel(
            retention_context, requested.request_id, expected_version=2,
            idempotency_key="retention-durable-cancel-0001",
        )
        assert cancelled.state == "cancelled" and cancelled.version == 3
        assert notebooks.read_selected_context(context, selected.notebook_id).sources == (
            ("source-phase-e-actual", "source-version-phase-e-actual"),
        )
        assert restarted.cancel(
            retention_context, requested.request_id, expected_version=2,
            idempotency_key="retention-durable-cancel-0001",
        ) == cancelled

        race_hold = restarted.apply_legal_hold(
            admin_retention_context, source_id="source-phase-e-actual", expected_version=1,
            idempotency_key="retention-race-hold-0001", step_up_verified=True,
        )
        race_start = Barrier(2)

        def release_race_hold() -> object:
            race_start.wait(timeout=5)
            return restarted.release_legal_hold(
                admin_retention_context, race_hold.hold_id, expected_version=1,
                idempotency_key="retention-race-release-0001", step_up_verified=True,
            )

        def create_race_request() -> object:
            race_start.wait(timeout=5)
            return restarted.create_request(
                retention_context, source_id="source-phase-e-actual", inventory=None,
                idempotency_key="retention-race-create-0001", if_match="*",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            release_future = executor.submit(release_race_hold)
            create_future = executor.submit(create_race_request)
            released_race_hold = release_future.result(timeout=10)
            created_race_request = create_future.result(timeout=10)
        assert released_race_hold.state == "released"
        race_request = restarted.get_request(
            retention_context, created_race_request.request_id,
        )
        assert race_request.state == "grace_period"

        cancel_hold = restarted.apply_legal_hold(
            admin_retention_context, source_id="source-phase-e-actual",
            expected_version=race_request.version,
            idempotency_key="retention-race-cancel-hold-prepare", step_up_verified=True,
        )
        assert restarted.release_legal_hold(
            admin_retention_context, cancel_hold.hold_id, expected_version=1,
            idempotency_key="retention-race-cancel-release-prepare", step_up_verified=True,
        ).state == "released"
        race_request = restarted.get_request(retention_context, race_request.request_id)
        hold_cancel_start = Barrier(2)

        def apply_hold_during_cancel() -> tuple[str, object]:
            hold_cancel_start.wait(timeout=5)
            try:
                return "hold", restarted.apply_legal_hold(
                    admin_retention_context, source_id="source-phase-e-actual",
                    expected_version=race_request.version,
                    idempotency_key="retention-race-cancel-hold-0001", step_up_verified=True,
                )
            except RetentionError as error:
                return "hold_error", error

        def cancel_during_hold() -> tuple[str, object]:
            hold_cancel_start.wait(timeout=5)
            try:
                return "cancel", restarted.cancel(
                    retention_context, race_request.request_id,
                    expected_version=race_request.version,
                    idempotency_key="retention-race-blocked-cancel-0001",
                )
            except RetentionError as error:
                return "cancel_error", error

        with ThreadPoolExecutor(max_workers=2) as executor:
            hold_future = executor.submit(apply_hold_during_cancel)
            cancel_future = executor.submit(cancel_during_hold)
            hold_cancel_results = (
                hold_future.result(timeout=10), cancel_future.result(timeout=10),
            )
        assert all(
            not (label.endswith("_error") and getattr(value, "status_code", 0) == 503)
            for label, value in hold_cancel_results
        )
        current_race_request = restarted.get_request(retention_context, race_request.request_id)
        assert current_race_request.state in {"blocked_by_hold", "cancelled"}
        active_cancel_hold = next(
            (value for label, value in hold_cancel_results if label == "hold"), None,
        )
        if active_cancel_hold is not None:
            assert restarted.release_legal_hold(
                admin_retention_context, active_cancel_hold.hold_id, expected_version=1,
                idempotency_key="retention-race-cancel-release-0001", step_up_verified=True,
            ).state == "released"
        current_race_request = restarted.get_request(retention_context, race_request.request_id)
        if current_race_request.state != "cancelled":
            assert restarted.cancel(
                retention_context, current_race_request.request_id,
                expected_version=current_race_request.version,
                idempotency_key="retention-race-blocked-cancel-final",
            ).state == "cancelled"
        canon.register_uploaded_source(
            CanonicalContext(
                context.tenant_id, context.workspace_id, context.actor_id,
                "source.create", context.trace_id,
            ), notebook_id=selected.notebook_id,
            source_id="fixture-source-purge", source_version_id="fixture-version-purge",
            object_id="object-source-context", filename="fixture-purge.pdf",
            digest_sha256=digest, byte_size=27, created_at=now,
        )
        class FixtureInventory:
            @staticmethod
            def read_in_transaction(connection, inventory_context, source_id):  # type: ignore[no-untyped-def]
                del connection, inventory_context, source_id
                return tuple(DerivativeInput(
                    kind, f"fixture-{kind}-purge", acknowledgement_required=kind == "known_local_copy",
                ) for kind in ("original_content", "index", "preview", "cache", "known_local_copy", "sync_reference"))
        fixture_service = PostgresRetentionRequestService(
            restart_store, FixtureInventory(), clock=lambda: now - timedelta(days=31), fixture_purge=True,
        )
        fixture_request = fixture_service.create_request(
            retention_context, source_id="fixture-source-purge", inventory=None,
            idempotency_key="retention-fixture-create-0001", if_match="*",
        )
        with restart_store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "retention.fixture.complete",
        )) as connection:
            connection.execute("SELECT set_config('app.retention_transition','allowed',true)")
            connection.execute(
                "UPDATE deletion_cleanup_items SET state='completed',attempt_count=attempt_count+1,evidence_kind='key_destroyed' "
                "WHERE tenant_id=%s AND workspace_id=%s AND request_id=%s",
                (context.tenant_id, context.workspace_id, fixture_request.request_id),
            )
        purge_service = PostgresRetentionRequestService(
            restart_store, FixtureInventory(), clock=lambda: now, fixture_purge=True,
        )
        hold_purge_start = Barrier(2)

        def hold_during_purge() -> tuple[str, object]:
            hold_purge_start.wait(timeout=5)
            try:
                return "hold", purge_service.apply_legal_hold(
                    admin_retention_context, source_id="fixture-source-purge", expected_version=1,
                    idempotency_key="retention-fixture-hold-race", step_up_verified=True,
                )
            except RetentionError as error:
                return "hold_error", error

        def purge_during_hold() -> tuple[str, object]:
            hold_purge_start.wait(timeout=5)
            try:
                return "purge", purge_service.purge(
                    admin_retention_context, fixture_request.request_id, expected_version=1,
                    idempotency_key="retention-fixture-purge-race", step_up_verified=True,
                )
            except RetentionError as error:
                return "purge_error", error

        with ThreadPoolExecutor(max_workers=2) as executor:
            hold_purge_future = executor.submit(hold_during_purge)
            purge_hold_future = executor.submit(purge_during_hold)
            hold_purge_results = (
                hold_purge_future.result(timeout=10), purge_hold_future.result(timeout=10),
            )
        assert sum(label in {"hold", "purge"} for label, _ in hold_purge_results) == 1
        assert all(
            not (label.endswith("_error") and getattr(value, "status_code", 0) == 503)
            for label, value in hold_purge_results
        )
        winning_purge = next(
            (value for label, value in hold_purge_results if label == "purge"), None,
        )
        if winning_purge is None:
            winning_hold = next(value for label, value in hold_purge_results if label == "hold")
            assert purge_service.release_legal_hold(
                admin_retention_context, winning_hold.hold_id, expected_version=1,
                idempotency_key="retention-fixture-release-race", step_up_verified=True,
            ).state == "released"
            fixture_current = purge_service.get_request(retention_context, fixture_request.request_id)
            winning_purge = purge_service.purge(
                admin_retention_context, fixture_request.request_id,
                expected_version=fixture_current.version,
                idempotency_key="retention-fixture-purge-after-release", step_up_verified=True,
            )
        purged = winning_purge
        assert purged.state == "purged" and purged.version in {3, 5}
        assert purge_service.get_request(retention_context, fixture_request.request_id) == purged
        with pytest.raises(RetentionError, match="DELETION_REQUEST_UNAVAILABLE"):
            restarted.get_request(RetentionContext(
                foreign.tenant_id, foreign.workspace_id, foreign.actor_id,
                foreign.trace_id, foreign.policy_version,
            ), requested.request_id)
        with store._transaction(CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "retention.test")) as connection:
            assert connection.execute("SELECT count(*) FROM deletion_requests WHERE request_id=%s", (requested.request_id,)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM deletion_cleanup_items WHERE request_id=%s", (requested.request_id,)).fetchone()[0] == 6
            lifecycle_audit = dict(connection.execute(
                "SELECT action,count(*) FROM audit_events WHERE target_id='source-phase-e-actual' "
                "AND action IN ('deletion.requested','deletion.cancelled','legal_hold.applied','legal_hold.released') "
                "GROUP BY action",
            ).fetchall())
            assert lifecycle_audit == {
                "deletion.requested": 5,
                "deletion.cancelled": 5,
                "legal_hold.applied": 4,
                "legal_hold.released": 4,
            }
        restart_store.close()
        report_repository = PostgresStudioReportRepository(store)
        selected_sources = report_repository.list_sources(StudioReportContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version, selected.notebook_id,
        ))
        assert selected_sources == ()
        assert report_repository.list_sources(StudioReportContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version, "notebook-unselected",
        )) == ()
        processing_repository = PostgresDocumentProcessingRepository(store, object())  # type: ignore[arg-type]
        processing_context = DocumentProcessingContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        processing_run_id = processing_repository.start(
            processing_context, "source-version-phase-e-actual", enqueue=True,
        )
        status = processing_repository.get_status(
            processing_context, processing_run_id, notebook_id=selected.notebook_id,
        )
        assert status.source_id == "source-phase-e-actual"
        with pytest.raises(DocumentUnderstandingError, match="PROCESSING_RUN_NOT_FOUND"):
            processing_repository.get_status(
                processing_context, processing_run_id, notebook_id="notebook-unselected",
            )
        with pytest.raises(CanonError, match="NOTEBOOK_NOT_FOUND"):
            canon.register_uploaded_source(
                CanonicalContext(
                    foreign.tenant_id, foreign.workspace_id, foreign.actor_id,
                    "source.create", foreign.trace_id,
                ), notebook_id=selected.notebook_id,
                source_id="source-phase-e-rejected",
                source_version_id="source-version-phase-e-rejected",
                object_id="object-source-context", filename="phase-e.pdf",
                digest_sha256=digest, byte_size=27, created_at=now,
            )
        with store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, "source.test",
        )) as connection:
            assert connection.execute(
                "SELECT count(*) FROM sources WHERE record_id='source-phase-e-rejected'",
            ).fetchone()[0] == 0
    finally:
        canon.close()
        store.close()


def test_actual_postgres_http_local_question_replay_revalidates_binding_before_provider_and_policy() -> None:
    assert DSN is not None
    store = PostgresCloudStore(DSN, min_size=1, max_size=2)
    notebooks = PostgresNotebookRepository(store, creation_enforcer=enforce_license_creation)
    licenses = PostgresLicenseRepository(store)
    scope = NotebookContext(
        "tenant-http-replay", "workspace-http-replay", "user-http-replay",
        "trace-http-replay", "policy-v1",
    )
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)

    class UnusedObjectStorage:
        def get(self, _key: str) -> bytes:
            raise AssertionError("replay must not read object storage")

    class BindingBoundary:
        def __init__(self) -> None:
            self.calls = 0
            self.blocked = False

        def require_selected_bindings(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.blocked:
                raise AssertionError("current binding rejected")

    class EgressPolicyBoundary:
        def __init__(self) -> None:
            self.calls = 0

        def get_effective(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            raise AssertionError("current policy must not run for local provider or replay")

    try:
        store.seed_scope(CloudAccessContext(
            scope.tenant_id, scope.workspace_id, scope.actor_id, "question.http.test",
        ))
        licenses.apply(
            LicenseContext(
                scope.tenant_id, scope.workspace_id, scope.actor_id,
                scope.trace_id, scope.policy_version,
            ),
            VerifiedLicense(
                license_id="license-http-replay-001", product="daon-user",
                edition="enterprise", organization_id=scope.tenant_id,
                issued_at=now - timedelta(days=1), expires_at=now + timedelta(days=365),
                features=("notebook_management",), resource_limits=(("notebooks", 10),),
                claims_digest="6" * 64, key_id="release-1",
            ),
            idempotency_key="license-http-replay-apply",
            request_fingerprint="7" * 64,
        )
        selected, _ = notebooks.create(
            scope, title="HTTP replay", description=None,
            idempotency_key="http-replay-notebook-create",
            request_fingerprint="8" * 64, now=now,
        )
        other, _ = notebooks.create(
            scope, title="Other", description=None,
            idempotency_key="http-replay-notebook-other",
            request_fingerprint="9" * 64, now=now,
        )
        repository = PostgresQuestionAnsweringRepository(
            store, UnusedObjectStorage(),  # type: ignore[arg-type]
        )

        class QuestionBoundary:
            def __init__(self) -> None:
                self.prepare_calls = 0
                self.ask_calls = 0
                self.blocked = False

            def replay(self, context, *, run_id, request_fingerprint):  # type: ignore[no-untyped-def]
                return repository.load_completed_for_replay(
                    context, run_id, request_fingerprint,
                )

            def prepare_authorization(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
                self.prepare_calls += 1
                if self.blocked:
                    raise AssertionError("current provider must not run during replay")
                return SimpleNamespace(
                    selection=TextModelSelection(
                        "OLLAMA", "http://127.0.0.1", "profile-local",
                        "deployment-local", "local-model", 1, "local_runtime",
                    ),
                    provider_payload={},
                )

            def ask(self, context, **kwargs):  # type: ignore[no-untyped-def]
                self.ask_calls += 1
                if self.blocked:
                    raise AssertionError("current provider must not run during replay")
                return repository.persist_completed(
                    context, run_id=kwargs["run_id"], source_id=None,
                    source_version_id=None, question=kwargs["question"],
                    selection=TextModelSelection(
                        "OLLAMA", "http://127.0.0.1", "profile-local",
                        "deployment-local", "local-model", 1, "local_runtime",
                    ),
                    evidence=(), result=GroundedTextResult(
                        "저장된 일반 대화", (), False, {"total_tokens": 4},
                    ), provider_called=True, context_mode="general_ungrounded",
                    context_sources=(), request_fingerprint=kwargs["request_fingerprint"],
                )

        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "identity.sqlite3"
            audit = AuditEventStore()
            identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
            authorization_repository = SqliteAuthorizationRepository(db_path)
            principal = IdentityPrincipal(
                scope.actor_id, "session-http-replay", "device-http-replay", scope.tenant_id,
            )
            authorization_repository.bootstrap_workspace(
                tenant_id=scope.tenant_id, workspace_id=scope.workspace_id,
                owner_user_id=scope.actor_id, owner_role=Role.PERSONAL_OWNER,
                workspace_kind="personal", data_area="cloud_sync",
                cost_limit_cents=1000, now=clock(),
            )
            authorization = AuthorizationService(
                repository=authorization_repository, audit_store=audit, clock=clock,
                identity_service=identity,
            )
            question_boundary = QuestionBoundary()
            binding_boundary = BindingBoundary()
            policy_boundary = EgressPolicyBoundary()
            dependencies = RuntimeDependencies(
                settings=RuntimeSettings.for_test(
                    database_path=db_path, policy_version=POLICY_VERSION,
                ),
                identity_service=identity, authorization_service=authorization,
                audit_store=audit, identity_repository=identity_repository,
                authorization_repository=authorization_repository,
                question_answering_service=question_boundary,  # type: ignore[arg-type]
                notebook_service=binding_boundary,  # type: ignore[arg-type]
                egress_policy_service=policy_boundary,  # type: ignore[arg-type]
            )
            body = {"notebook_id": selected.notebook_id, "question": "안녕하세요!"}
            with patch.object(identity, "describe_access", return_value=SimpleNamespace(
                client_kind=ClientKind.WEB, principal=principal,
            )):
                with TestClient(create_app(dependencies)) as client:
                    first = client.post(
                        f"/api/v1/workspaces/{scope.workspace_id}/questions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "question-http-replay-0001"},
                        json=body,
                    )
                    assert first.status_code == 200, first.text
                    first_data = first.json()["data"]
                    first_counts = (
                        binding_boundary.calls, question_boundary.prepare_calls,
                        question_boundary.ask_calls, policy_boundary.calls,
                    )
                    question_boundary.blocked = True
                    replay = client.post(
                        f"/api/v1/workspaces/{scope.workspace_id}/questions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "question-http-replay-0001"},
                        json=body,
                    )
                    assert replay.status_code == 200, replay.text
                    assert replay.json()["data"] == first_data
                    assert binding_boundary.calls == first_counts[0] + 1
                    assert (
                        question_boundary.prepare_calls, question_boundary.ask_calls,
                        policy_boundary.calls,
                    ) == first_counts[1:]
                    mismatch = client.post(
                        f"/api/v1/workspaces/{scope.workspace_id}/questions",
                        cookies={WEB_SESSION_COOKIE: "opaque-session"},
                        headers={"Idempotency-Key": "question-http-replay-0001"},
                        json={"notebook_id": selected.notebook_id, "question": "고마워요"},
                    )
                    assert mismatch.status_code == 409, mismatch.text
                    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
                    assert binding_boundary.calls == first_counts[0] + 2
                    assert (
                        question_boundary.prepare_calls, question_boundary.ask_calls,
                        policy_boundary.calls,
                    ) == first_counts[1:]

            run_id = first_data["run_id"]
            stored_fingerprint = None
            with store._transaction(CloudAccessContext(
                scope.tenant_id, scope.workspace_id, scope.actor_id, "question.http.test",
            )) as connection:
                stored_fingerprint = connection.execute(
                    "SELECT canonical_json->>'request_fingerprint' FROM runs WHERE record_id=%s",
                    (run_id,),
                ).fetchone()[0]
            assert isinstance(stored_fingerprint, str)
            with pytest.raises(QuestionRepositoryError) as missing_binding:
                repository.load_completed_for_replay(
                    QuestionContext(
                        scope.tenant_id, scope.workspace_id, scope.actor_id,
                        scope.trace_id, scope.policy_version, other.notebook_id,
                    ), run_id, stored_fingerprint,
                )
            assert (missing_binding.value.code, missing_binding.value.status) == (
                "NOTEBOOK_NOT_FOUND", 404,
            )
            dependencies.close()
    finally:
        store.close()
