from __future__ import annotations

import tempfile
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from daon_user_api.audit import AuditEventStore
from daon_user_api.authorization import AuthorizationService, Role, SqliteAuthorizationRepository
from daon_user_api.identity import IdentityError, IdentityPrincipal
from daon_user_api.runtime import WEB_SESSION_COOKIE, RuntimeDependencies, RuntimeSettings, create_app
from daon_user_api.studio_export import export_studio_output
from daon_user_api.studio_workspace import StudioError
from test_identity_support import POLICY_VERSION, TRACE_ID, create_service, identity_session_view, native_login, FakeVerifiedOidcProvider


class FakeStudioWorkspace:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, context, request, key):  # type: ignore[no-untyped-def]
        self.calls.append(("generate", context, request, key))
        return {"studio_output_id": "output-1", "output_version_id": "version-1", "output_type": "evidence_report", "status": "draft", "title": "산출물", "content": {"body": "생성"}, "settings_snapshot_id": "settings-1", "citations": [{"citation_id": "citation-1"}]}, False

    def enqueue(self, context, request, key):  # type: ignore[no-untyped-def]
        self.calls.append(("enqueue", context, request, key))
        return {"job_id": "job-1", "status": "queued", "output_type": request.output_type, "title": request.purpose}, False

    def generation_job(self, context, job_id):  # type: ignore[no-untyped-def]
        self.calls.append(("generation_job", context, job_id))
        return {"job_id": job_id, "status": "completed", "output_type": "evidence_report", "title": "산출물",
                "studio_output_id": "output-1", "output_version_id": "version-1",
                "output": self.list_versions(context, "output-1")[0]}

    def list_outputs(self, context):  # type: ignore[no-untyped-def]
        return ({"studio_output_id": "output-1", "output_version_id": "version-1", "status": "draft", "title": "산출물", "source_version_ids": ["source-version-1"], "run_id": "run-1", "run_result_id": "result-1", "citations": [{"citation_id": "citation-1", "source_version_id": "source-version-1"}]},)

    def list_versions(self, context, output_id):  # type: ignore[no-untyped-def]
        self.calls.append(("list_versions", context, output_id))
        return ({
            "output_version_id": "version-1", "content_version": 1, "previous_version_id": None,
            "status": "draft", "content": {"body": "생성"}, "revision_type": "initial",
            "change_reason": "initial_generation", "settings_snapshot_id": "settings-1",
            "output_format": "pdf",
            "citations": [{"citation_id": "citation-1", "source_version_id": "source-version-1", "evidence_span_id": "span-1", "origin": "raw_source", "locator": {"kind": "page", "value": "2"}}],
            "review_request_id": None, "approval_request_id": None, "approval_id": None,
            "delivery_id": None, "knowledge_registration_id": None,
        },)

    def revise(self, context, output_id, revision, key):  # type: ignore[no-untyped-def]
        self.calls.append(("revise", context, output_id, revision, key))
        return {"output_version_id": "version-2", "previous_version_id": "version-1", "status": "draft", "content": "변경", "revision_type": "user_edit", "change_reason": "문구 정정", "approval_required": True, "settings_snapshot_id": "settings-1", "generation_request_id": None, "resubmission_of_rejected_version": False}, False

    def action(self, context, action, payload, key):  # type: ignore[no-untyped-def]
        self.calls.append((action, context, payload, key))
        return {"record_id": f"{action}-1", "action": action, "status": "accepted", "output_version_id": payload["output_version_id"]}, False

    def export(self, context, output_id, version_id, format_name):  # type: ignore[no-untyped-def]
        self.calls.append(("export", context, output_id, version_id, format_name))
        return export_studio_output(format_name, "산출물", "근거 내용", {
            "output_version_id": version_id, "created_at": "2026-08-13T00:00:00Z",
            "knowledge_scope": "source-version-1", "evidence_appendix": "Citation page 2",
        })


class StudioWorkspaceRuntimeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        db_path = Path(self.directory.name) / "runtime.sqlite3"
        audit = AuditEventStore()
        identity, identity_repository, _, clock = create_service(db_path, audit_store=audit)
        authorization_repository = SqliteAuthorizationRepository(db_path)
        self.principal = IdentityPrincipal("user-001", "session-001", "device-001", "tenant-001")
        authorization_repository.bootstrap_workspace(
            tenant_id="tenant-001", workspace_id="workspace-001", owner_user_id="user-001",
            owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync",
            cost_limit_cents=1000, now=clock(),
        )
        authorization = AuthorizationService(repository=authorization_repository, audit_store=audit, clock=clock, identity_service=identity)
        self.identity = identity
        self.clock = clock
        self.studio = FakeStudioWorkspace()
        self.dependencies = RuntimeDependencies(
            settings=RuntimeSettings.for_test(database_path=db_path, policy_version=POLICY_VERSION),
            identity_service=identity, authorization_service=authorization, audit_store=audit,
            identity_repository=identity_repository, authorization_repository=authorization_repository,
            studio_workspace_service=self.studio,
        )
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(self.dependencies)), base_url="http://127.0.0.1")

    async def asyncTearDown(self):
        await self.client.aclose(); self.dependencies.close(); self.directory.cleanup()

    def authenticated(self):
        return patch.object(
            self.identity,
            "describe_access",
            return_value=identity_session_view(self.principal),
        )

    async def test_generation_version_action_and_export_routes_are_real_and_safe(self):
        generation = {
            "workspace_id": "workspace-001", "notebook_id": "notebook-001", "output_type": "evidence_report", "source_id": "source-1",
            "source_version_ids": ["source-version-1"], "run_id": "run-1", "run_result_id": "result-1",
            "settings": {"purpose": "목적", "audience": "독자", "source_version_ids": ["source-version-1"],
                         "ruleset_version_id": None, "length": "short", "structure": "summary", "output_format": "pdf", "review_condition": "review_required"},
        }
        cookies = {WEB_SESSION_COOKIE: "opaque"}
        with self.authenticated():
            created = await self.client.post("/api/v1/studio-generation-requests", cookies=cookies, headers={"Idempotency-Key": "generation-key-0001", "X-Trace-Id": TRACE_ID}, json=generation)
            job = await self.client.get("/api/v1/studio-generation-jobs/job-1?workspace_id=workspace-001&notebook_id=notebook-001", cookies=cookies)
            listed = await self.client.get("/api/v1/studio-outputs?workspace_id=workspace-001&notebook_id=notebook-001", cookies=cookies)
            versions = await self.client.get("/api/v1/studio-outputs/output-1/versions?workspace_id=workspace-001&notebook_id=notebook-001", cookies=cookies)
            revised = await self.client.post("/api/v1/studio-outputs/output-1/versions", cookies=cookies, headers={"Idempotency-Key": "revision-key-00001"}, json={"workspace_id": "workspace-001", "notebook_id": "notebook-001", "previous_version_id": "version-1", "revision_type": "user_edit", "change_reason": "문구 정정", "content": "변경"})
            review = await self.client.post("/api/v1/reviews", cookies=cookies, headers={"Idempotency-Key": "review-key-000001"}, json={"workspace_id": "workspace-001", "notebook_id": "notebook-001", "output_version_id": "version-1"})
        export_credentials = self.sensitive_session()
        exported = await self.export_request(export_credentials, self.step_up(export_credentials, target_id=self.export_target()))
        self.assertEqual([created.status_code, job.status_code, listed.status_code, versions.status_code, revised.status_code, review.status_code, exported.status_code], [201, 200, 200, 200, 201, 201, 200])
        self.assertEqual(created.json()["data"]["status"], "queued")
        self.assertEqual(job.json()["data"]["status"], "completed")
        created_lineage = created.json()["data"]["lineage"]
        self.assertEqual(created_lineage["notebook_id"], "notebook-001")
        self.assertEqual(created_lineage["source_version_ids"], ["source-version-1"])
        self.assertEqual(created_lineage["artifact_type"], "evidence_report")
        self.assertEqual(created_lineage["instruction"], "목적")
        self.assertEqual(created_lineage["run_id"], "run-1")
        self.assertFalse(created_lineage["verification_required"])
        listed_lineage = listed.json()["data"]["outputs"][0]["lineage"]
        self.assertEqual(listed_lineage["source_version_ids"], ["source-version-1"])
        self.assertFalse(listed_lineage["verification_required"])
        self.assertEqual(versions.json()["data"]["versions"][0]["citations"][0]["origin"], "raw_source")
        for response in (created, revised, review): self.assertRegex(response.headers["etag"], r'^"studio-[^"]+"$')
        self.assertEqual(revised.json()["data"]["content"], "변경")
        self.assertTrue(exported.content.startswith(b"%PDF-"))
        self.assertEqual(exported.headers["x-content-type-options"], "nosniff")
        self.assertEqual(exported.headers["cache-control"], "no-store")

    def sensitive_session(self):
        credentials = native_login(self.identity)
        self.dependencies.authorization_repository.bootstrap_workspace(
            tenant_id=credentials.tenant_id, workspace_id="workspace-sensitive", owner_user_id=credentials.user_id,
            owner_role=Role.PERSONAL_OWNER, workspace_kind="personal", data_area="cloud_sync",
            cost_limit_cents=1000, now=self.clock(),
        )
        return credentials

    def export_target(self, **changes):
        fields = dict(workspace="workspace-sensitive", notebook="notebook-001", output="output-1", version="version-1", format="pdf")
        fields.update(changes)
        return "studio-export-" + hashlib.sha256(json.dumps(list(fields.values()), separators=(",", ":")).encode()).hexdigest()

    async def export_request(self, credentials, token=None, key="s24-export-key-0001", **changes):
        fields = dict(workspace="workspace-sensitive", notebook="notebook-001", output="output-1", version="version-1", format="pdf")
        fields.update(changes)
        headers = {"Authorization": f"Bearer {credentials.access_token}", "Idempotency-Key": key}
        if token is not None:
            headers["X-Daon-Step-Up-Authorization"] = token
        return await self.client.get(f'/api/v1/studio-outputs/{fields["output"]}/versions/{fields["version"]}/exports/{fields["format"]}?workspace_id={fields["workspace"]}&notebook_id={fields["notebook"]}', headers=headers)

    async def test_s24_export_requires_token_before_file_generation(self):
        credentials = self.sensitive_session()
        response = await self.export_request(credentials)
        self.assertEqual(response.status_code, 403)
        self.assertEqual((response.status_code, response.json().get("error", {}).get("code")), (403, "STEP_UP_REQUIRED"))
        self.assertEqual(self.studio.calls, [])

    async def test_s24_export_rejects_wrong_bindings_expiry_tamper_and_reuse_before_export(self):
        credentials = self.sensitive_session()
        other = native_login(self.identity, FakeVerifiedOidcProvider(subject="s24-other-actor"))
        other_session = native_login(self.identity)
        cases = [("actor", self.step_up(other, target_id=self.export_target())),
                 ("session", self.step_up(other_session, target_id=self.export_target())),
                 ("policy", self.step_up(credentials, target_id=self.export_target(), policy_version="other-policy")),
                 ("tamper", "unknown-s24-token")]
        for field in ("workspace", "notebook", "output", "version", "format"):
            cases.append((field, self.step_up(credentials, target_id=self.export_target(**{field: "csv" if field == "format" else "other-id"}))))
        expired = self.step_up(credentials, target_id=self.export_target(), ttl_seconds=30)
        self.clock.advance(seconds=31)
        cases.append(("expired", expired))
        for name, token in cases:
            with self.subTest(name=name):
                response = await self.export_request(credentials, token)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.studio.calls, [])

    async def test_s24_export_exact_replay_keeps_current_authorization_and_repository_access(self):
        credentials = self.sensitive_session()
        token = self.step_up(credentials, target_id=self.export_target())
        with patch.object(self.identity, "consume_step_up", wraps=self.identity.consume_step_up) as consume:
            first = await self.export_request(credentials, token)
            replay = await self.export_request(credentials, token)
            changed = await self.export_request(credentials, token, key="s24-different-key-001")
        self.assertEqual((first.status_code, replay.status_code, changed.status_code), (200, 200, 403))
        self.assertTrue(first.content.startswith(b"%PDF-"))
        self.assertNotIn(token.encode(), first.content)
        self.assertEqual(len(self.studio.calls), 2)
        self.assertEqual(consume.call_args_list[0].kwargs["target_id"], self.export_target())
        self.assertEqual(consume.call_args_list[0].kwargs["operation"], "studio.export.pdf")
        # Replaying the same identity consumption cannot bypass current Source ACL.
        with patch.object(self.studio, "export", side_effect=StudioError("STUDIO_OUTPUT_NOT_FOUND", 404)) as exporter:
            denied = await self.export_request(credentials, token)
        self.assertEqual(denied.status_code, 404)
        exporter.assert_called_once()
        with patch.object(self.dependencies.authorization_service, "authorize_action", side_effect=StudioError("STUDIO_ACCESS_DENIED", 403)), patch.object(self.identity, "consume_step_up") as consume:
            denied = await self.export_request(credentials, token)
        self.assertEqual(denied.status_code, 403)
        consume.assert_not_called()

    async def test_s24_export_invalid_format_or_key_does_not_consume(self):
        credentials = self.sensitive_session()
        token = self.step_up(credentials, target_id=self.export_target())
        with patch.object(self.identity, "consume_step_up", wraps=self.identity.consume_step_up) as consume:
            bad_format = await self.export_request(credentials, token, format="exe")
            bad_key = await self.export_request(credentials, token, key="short")
        self.assertEqual((bad_format.status_code, bad_key.status_code), (400, 400))
        consume.assert_not_called()
        self.assertEqual(self.studio.calls, [])

    def step_up(self, credentials, **changes):
        return self.identity.issue_step_up(**{
            "access_token": credentials.access_token, "action_group": "final_approval_or_knowledge_registration",
            "target_id": "version-1", "policy_version": POLICY_VERSION, "trace_id": TRACE_ID, **changes,
        }).authorization

    async def sensitive_post(self, credentials, resource, token=None, key="sensitive-key-0001"):
        body = {"workspace_id": "workspace-sensitive", "notebook_id": "notebook-001", "output_version_id": "version-1"}
        if token is not None:
            body["step_up_authorization"] = token
        return await self.client.post(f"/api/v1/{resource}",
            headers={"Authorization": f"Bearer {credentials.access_token}", "Idempotency-Key": key}, json=body)

    async def test_sensitive_actions_reject_missing_step_up_before_studio_write(self):
        credentials = self.sensitive_session()
        for resource in ("approvals", "deliveries", "knowledge-registrations"):
            response = await self.sensitive_post(credentials, resource)
            self.assertEqual((response.status_code, response.json().get("error", {}).get("code")), (403, "STEP_UP_REQUIRED"))
        self.assertEqual(self.studio.calls, [])

    async def test_sensitive_step_up_binding_expiry_and_reuse_reject_before_write(self):
        credentials = self.sensitive_session()
        other = native_login(self.identity, FakeVerifiedOidcProvider(subject="other-step-up-actor"))
        other_session = native_login(self.identity)
        cases = [
            (" ", "INVALID_INPUT", 400),
            ("unknown-token", "STEP_UP_REQUIRED", 403),
            (self.step_up(other), "STEP_UP_BINDING_DENIED", 403),
            (self.step_up(other_session), "STEP_UP_BINDING_DENIED", 403),
            (self.step_up(credentials, target_id="other-version"), "STEP_UP_BINDING_DENIED", 403),
            (self.step_up(credentials, action_group="external_transfer"), "STEP_UP_BINDING_DENIED", 403),
            (self.step_up(credentials, policy_version="other-policy"), "STEP_UP_BINDING_DENIED", 403),
        ]
        expired = self.step_up(credentials, ttl_seconds=30)
        self.clock.advance(seconds=31)
        cases.append((expired, "STEP_UP_EXPIRED", 403))
        used = self.step_up(credentials)
        self.identity.consume_step_up(step_up_authorization=used, access_token=credentials.access_token,
            action_group="final_approval_or_knowledge_registration", target_id="version-1", policy_version=POLICY_VERSION,
            trace_id=TRACE_ID, operation="studio.approval", idempotency_key="other-key-00001")
        cases.append((used, "STEP_UP_REUSED", 403))
        for token, code, status in cases:
            for resource in ("approvals", "deliveries", "knowledge-registrations"):
                with self.subTest(code=code, resource=resource):
                    raw_errors = []
                    consume = self.identity.consume_step_up
                    def observe(**kwargs):
                        try:
                            return consume(**kwargs)
                        except IdentityError as error:
                            raw_errors.append(error.code)
                            raise
                    with patch.object(self.identity, "consume_step_up", side_effect=observe):
                        response = await self.sensitive_post(credentials, resource, token)
                    self.assertEqual(raw_errors, [code])
                    public_code = code if code == "STEP_UP_REQUIRED" else "INVALID_REQUEST"
                    self.assertEqual((response.status_code, response.json().get("error", {}).get("code")), (status, public_code))
        self.assertEqual(self.studio.calls, [])

    async def test_sensitive_valid_tokens_bind_operation_target_and_do_not_enter_payload(self):
        credentials = self.sensitive_session()
        for resource, action in (("approvals", "approval"), ("deliveries", "delivery"), ("knowledge-registrations", "knowledge_registration")):
            token = self.step_up(credentials)
            with patch.object(self.identity, "consume_step_up", wraps=self.identity.consume_step_up) as consume:
                response = await self.sensitive_post(credentials, resource, token)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertTrue(consume.called)
            self.assertEqual(consume.call_args.kwargs["operation"], f"studio.{action}")
            self.assertEqual(consume.call_args.kwargs["target_id"], "version-1")
            self.assertNotIn("step_up_authorization", self.studio.calls[-1][2])

    async def test_review_and_approval_request_do_not_consume_step_up(self):
        credentials = self.sensitive_session()
        with patch.object(self.identity, "consume_step_up", wraps=self.identity.consume_step_up) as consume:
            for resource in ("reviews", "approval-requests"):
                response = await self.sensitive_post(credentials, resource)
                self.assertEqual(response.status_code, 201)
        self.assertFalse(consume.called)
        self.assertEqual([call[0] for call in self.studio.calls], ["review", "approval_request"])

    async def test_consumed_token_survives_write_failure_and_exact_repeat_but_not_new_key(self):
        credentials = self.sensitive_session()
        token = self.step_up(credentials)
        result = {"record_id": "approval-1", "action": "approval", "output_version_id": "version-1"}
        with patch.object(self.studio, "action", side_effect=[StudioError("STUDIO_DATABASE_UNAVAILABLE", 503), (result, False), (result, True), (result, False)]) as writer:
            failed = await self.sensitive_post(credentials, "approvals", token)
            success = await self.sensitive_post(credentials, "approvals", token)
            replay = await self.sensitive_post(credentials, "approvals", token)
            changed = await self.sensitive_post(credentials, "approvals", token, key="different-key-0001")
        self.assertEqual((failed.status_code, success.status_code, replay.status_code, changed.status_code), (503, 201, 200, 403))
        self.assertEqual(changed.json()["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(writer.call_count, 3)
        used = [event for event in self.dependencies.audit_store.list(tenant_id=credentials.tenant_id).items if event.action == "identity.step_up.used"]
        self.assertEqual(len(used), 1)

    async def test_missing_default_policy_is_public_fail_closed_409(self):
        with self.authenticated(), patch.object(
            self.studio,
            "list_outputs",
            side_effect=StudioError("POLICY_PROJECTION_UNAVAILABLE", 409),
        ):
            response = await self.client.get(
                "/api/v1/studio-outputs?workspace_id=workspace-001&notebook_id=notebook-001",
                cookies={WEB_SESSION_COOKIE: "opaque"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "POLICY_PROJECTION_UNAVAILABLE")

    async def test_library_marks_unverifiable_output_without_source_lineage(self):
        self.studio.list_outputs = lambda context: ({
            "studio_output_id": "output-unverified", "output_version_id": "version-unverified",
            "status": "draft", "title": "확인 필요", "output_type": "evidence_report",
        },)
        with self.authenticated():
            response = await self.client.get(
                "/api/v1/studio-outputs?workspace_id=workspace-001&notebook_id=notebook-001",
                cookies={WEB_SESSION_COOKIE: "opaque"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["outputs"][0]["lineage"]["verification_required"])

    async def test_database_failure_remains_public_503(self):
        with self.authenticated(), patch.object(
            self.studio,
            "list_outputs",
            side_effect=StudioError("STUDIO_DATABASE_UNAVAILABLE", 503),
        ):
            response = await self.client.get(
                "/api/v1/studio-outputs?workspace_id=workspace-001&notebook_id=notebook-001",
                cookies={WEB_SESSION_COOKIE: "opaque"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "STUDIO_DATABASE_UNAVAILABLE")

    async def test_step_up_endpoint_requires_and_forwards_current_password(self):
        grant = type("Grant", (), {"authorization": "opaque-step-up", "issued_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc), "expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})()
        with self.authenticated(), patch.object(self.identity, "issue_step_up_after_reauthentication", return_value=grant) as issue:
            missing = await self.client.post("/api/v1/session/step-up", cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "step-key-missing"}, json={"action_group": "external_transfer", "target_id": "version-1"})
            issued = await self.client.post("/api/v1/session/step-up", cookies={WEB_SESSION_COOKIE: "opaque"}, headers={"Idempotency-Key": "step-key-present"}, json={"action_group": "external_transfer", "target_id": "version-1", "password": "current password value"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(issued.status_code, 201)
        self.assertEqual(issue.call_args.kwargs["password"], "current password value")


if __name__ == "__main__":
    unittest.main()
