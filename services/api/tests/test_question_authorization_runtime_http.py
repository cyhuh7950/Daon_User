"""S18: real identity receipts at the HTTP boundary, no external Provider I/O."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import DevicePlatform, PASSWORD_HASHER
from daon_user_api.question_answering import TextModelSelection
from daon_user_api.question_answering_postgres import StoredQuestionAnswer
from daon_user_api.question_answering_service import QuestionAnsweringError
from daon_user_api.runtime import RuntimeDependencies, RuntimeSettings, WEB_SESSION_COOKIE, create_app
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service
from test_question_answering_runtime_http import FakeNotebookService


PASSWORD = "S18 synthetic local reauthentication fixture"
BODY = {"notebook_id": "notebook-s18", "question": "선택한 근거를 설명해 주세요."}


class QuestionBoundary:
    """Replace model/storage only; keep runtime authorization and real identity intact."""
    def __init__(self):
        self.selection = TextModelSelection(
            "fixture", "https://provider.example.com/v1", "profile-s18", "deployment-s18",
            "fixture-model", 4, connection_id="connection-s18", credential_version=2, catalog_version=3,
        )
        self.results = {}
        self.calls = 0
        self.payload_suffix = ""

    def replay(self, context, *, run_id, request_fingerprint):
        saved = self.results.get(run_id)
        if saved and saved[0] != request_fingerprint:
            raise QuestionAnsweringError("QUESTION_NEW_RUN_REQUIRED", status=409)
        return saved[1] if saved else None

    def prepare_authorization(self, context, **kwargs):
        return SimpleNamespace(selection=self.selection, provider_payload=json.dumps({
            "question": kwargs["question"], "notebook": context.notebook_id,
            "source": kwargs["source_id"], "version": kwargs["source_version_id"],
            "suffix": self.payload_suffix,
        }, sort_keys=True).encode())

    def ask(self, context, **kwargs):
        self.calls += 1
        result = StoredQuestionAnswer(
            kwargs["run_id"], "result-s18", "격리된 응답", False, (), self.selection.provider_kind,
            {"destination": "provider.example.com", "max_bytes": 512, "classification": "internal",
             "masking_required": True, "redaction_required": True},
        )
        self.results[kwargs["run_id"]] = (kwargs["request_fingerprint"], result)
        return result


class S18QuestionAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "s18.sqlite3"
        audit = AuditEventStore()
        self.identity, self.repo, _, self.clock = create_service(path, audit_store=audit)
        connection = self.repo._connect()
        try:
            connection.execute("INSERT OR IGNORE INTO tenants(tenant_id) VALUES ('tenant-001')")
            for user in ("user-s18", "other-s18"):
                connection.execute(
                    "INSERT INTO users(user_id,issuer,subject,login_id,email,password_digest,email_verified_at,state) "
                    "VALUES (?,?,?,?,?,?,?,'active')",
                    (user, "local", user, user, user + "@example.com", PASSWORD_HASHER.hash(PASSWORD), self.clock().isoformat()),
                )
                connection.execute("INSERT INTO memberships(tenant_id,user_id,role) VALUES ('tenant-001',?,'member')", (user,))
            connection.commit()
        finally:
            connection.close()
        self.repo.add_step_up_action("tenant-001", "external_transfer")
        self.credentials = self.login("user-s18")
        authorization_repository = SqliteAuthorizationRepository(path)
        authorization_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-s18", owner_user_id="user-s18",
            owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync",
            cost_limit_cents=1000, now=self.clock(),
        )
        self.service = QuestionBoundary()
        self.effective = SimpleNamespace(
            fingerprint="policy-s18-v1", required_approver="workspace_manager",
            mode="allow_approved_external", allowed_provider_kinds=("external_api",),
            allowed_destinations=("provider.example.com",), max_bytes=65536, classification="internal",
            masking_required=True, redaction_required=True,
        )
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=path, policy_version=POLICY_VERSION),
            audit_store=audit, identity_service=self.identity, identity_repository=self.repo,
            authorization_service=AuthorizationService(repository=authorization_repository, audit_store=audit, clock=self.clock),
            authorization_repository=authorization_repository, question_answering_service=self.service,
            notebook_service=FakeNotebookService(),
            egress_policy_service=SimpleNamespace(get_effective=lambda _: self.effective),
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)), base_url="http://127.0.0.1")

    def login(self, user):
        return self.identity.local_login(login_id=user, password=PASSWORD, platform=DevicePlatform.WEB,
                                         trace_id=TRACE_ID, policy_version=POLICY_VERSION)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.dependencies.close()
        self.directory.cleanup()

    async def post(self, *, authorization=False, body=None, key="question-s18-0001", token=None):
        return await self.client.post(
            "/api/v1/workspaces/workspace-s18/questions" + ("/authorization" if authorization else ""),
            headers={"Idempotency-Key": key, "Cookie": f"{WEB_SESSION_COOKIE}={token or self.credentials.access_token}"},
            json=body or BODY,
        )

    async def grant(self, **kwargs):
        response = await self.post(authorization=True, body={**BODY, "password": PASSWORD, **kwargs})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]

    def consumption_count(self):
        connection = self.repo._connect()
        try:
            return connection.execute("SELECT count(*) FROM step_up_consumptions").fetchone()[0]
        finally:
            connection.close()

    async def test_s18_question_missing_token_is_step_up_required_before_provider(self):
        response = await self.post()
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error"]["code"], "STEP_UP_REQUIRED")
        self.assertEqual((self.service.calls, self.consumption_count()), (0, 0))

    async def test_s18_question_real_grant_consumed_once_success_replay_and_policy_recheck(self):
        grant = await self.grant()
        response = await self.post(body={**BODY, "step_up_authorization_id": grant["step_up_authorization_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.service.calls, self.consumption_count()), (1, 1))
        replay = await self.post()  # completed replay does not need the already consumed token
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual((self.service.calls, self.consumption_count()), (1, 1))
        self.effective.mode = "deny_external"
        denied = await self.post()
        self.assertEqual(denied.status_code, 403)
        self.assertEqual((self.service.calls, self.consumption_count()), (1, 1))

    async def test_s18_question_policy_denial_consumes_nothing(self):
        grant = await self.grant()
        self.effective.mode = "deny_external"
        response = await self.post(body={**BODY, "step_up_authorization_id": grant["step_up_authorization_id"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "EGRESS_POLICY_DENIED")
        self.assertEqual((self.service.calls, self.consumption_count()), (0, 0))

    async def test_s18_question_foreign_session_question_notebook_policy_model_payload_rejected(self):
        for changed in ("session", "question", "notebook", "policy", "model", "payload"):
            with self.subTest(changed=changed):
                self.service.results.clear()
                self.service.calls = 0
                grant = await self.grant()
                body = {**BODY, "step_up_authorization_id": grant["step_up_authorization_id"]}
                token = None
                if changed == "session": token = self.login("user-s18").access_token
                if changed == "question": body["question"] = "다른 질문입니다."
                if changed == "notebook": body["notebook_id"] = "notebook-other"
                if changed == "policy": self.effective.fingerprint = "policy-s18-v2"
                if changed == "model": self.service.selection = replace(self.service.selection, binding_version=5)
                if changed == "payload": self.service.payload_suffix = "changed"
                response = await self.post(body=body, token=token)
                self.assertEqual(response.status_code, 403, response.text)
                # Existing public error mapping intentionally hides binding details.
                self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
                self.assertEqual((self.service.calls, self.consumption_count()), (0, 0))
                self.effective.fingerprint = "policy-s18-v1"
                self.service.selection = replace(self.service.selection, binding_version=4)
                self.service.payload_suffix = ""

    async def test_s18_question_foreign_actor_tampered_expired_and_reused_tokens(self):
        grant = await self.grant()
        foreign = self.login("other-s18")
        receipt = self.identity.issue_step_up_after_reauthentication(
            access_token=foreign.access_token, password=PASSWORD, action_group="external_transfer",
            target_id=grant["run_id"], policy_version=grant["request_fingerprint"], trace_id=TRACE_ID,
        )
        for raw, code in ((receipt.authorization, "INVALID_REQUEST"),
                          ("not-an-issued-receipt", "STEP_UP_REQUIRED")):
            response = await self.post(body={**BODY, "step_up_authorization_id": raw})
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["error"]["code"], code)
        self.clock.advance(seconds=301)
        response = await self.post(body={**BODY, "step_up_authorization_id": grant["step_up_authorization_id"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
        fresh = await self.grant()
        self.identity.consume_step_up(
            step_up_authorization=fresh["step_up_authorization_id"], access_token=self.credentials.access_token,
            action_group="external_transfer", target_id=fresh["run_id"], policy_version=fresh["request_fingerprint"],
            trace_id=TRACE_ID, operation="different.operation", idempotency_key="different-key",
        )
        response = await self.post(body={**BODY, "step_up_authorization_id": fresh["step_up_authorization_id"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.service.calls, 0)

    async def test_s18_question_internal_models_do_not_require_new_token(self):
        for kind in ("server_internal", "local_runtime"):
            self.service.selection = replace(self.service.selection, provider_kind=kind)
            response = await self.post(key="question-s18-" + kind)
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.service.calls, self.consumption_count()), (2, 0))

    async def test_s18_question_revoked_real_session_consumes_nothing(self):
        grant = await self.grant()
        self.identity.revoke_current_web_session(
            access_token=self.credentials.access_token, trace_id=TRACE_ID, policy_version=POLICY_VERSION,
        )
        response = await self.post(body={**BODY, "step_up_authorization_id": grant["step_up_authorization_id"]})
        self.assertEqual(response.status_code, 401)
        self.assertEqual((self.service.calls, self.consumption_count()), (0, 0))

    async def test_s18_question_receipt_for_different_source_version_is_rejected(self):
        grant = await self.grant(source_id="source-s18", source_version_id="version-s18")
        response = await self.post(body={**BODY, "source_id": "source-s18", "source_version_id": "version-other",
                                         "step_up_authorization_id": grant["step_up_authorization_id"]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((self.service.calls, self.consumption_count()), (0, 0))

    async def test_s18_question_changed_input_same_completed_key_is_not_replayed(self):
        grant = await self.grant()
        first = await self.post(body={**BODY, "step_up_authorization_id": grant["step_up_authorization_id"]})
        self.assertEqual(first.status_code, 200, first.text)
        changed = await self.post(body={**BODY, "question": "새 질문"})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.service.calls, 1)
