import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  REQUIRED_PATHS,
  buildSummary,
  canonicalizeDocument,
  evidenceRelativePathForProfile,
  validateOpenApiDocument
} from "../verify-openapi-contract.mjs";

const root = path.resolve(import.meta.dirname, "../..");
const contractPath = path.join(root, "packages/contracts/openapi/v1/openapi.json");

async function loadContract() {
  return JSON.parse(await readFile(contractPath, "utf8"));
}

function clone(value) {
  return structuredClone(value);
}

test("OpenAPI v1 정본은 설계 §17.1 전체 Path와 공통 안전 계약을 통과한다", async () => {
  const document = await loadContract();
  assert.doesNotThrow(() => validateOpenApiDocument(document));
  assert.deepEqual(Object.keys(document.paths).sort(), [...REQUIRED_PATHS].sort());
  const summary = buildSummary(document);
  assert.equal(summary.contract_version, "1.0.0");
  assert.equal(summary.path_count, REQUIRED_PATHS.length);
  assert.match(summary.canonical_sha256, /^[A-F0-9]{64}$/);
  assert.equal(canonicalizeDocument(JSON.parse(JSON.stringify(document))), canonicalizeDocument(document));
});

test("OpenAPI 검증기는 누락 Path를 fail-close 거부한다", async () => {
  const document = clone(await loadContract());
  delete document.paths[REQUIRED_PATHS[0]];
  assert.throws(() => validateOpenApiDocument(document), /required path/i);
});

test("OpenAPI 검증기는 중복 operationId를 fail-close 거부한다", async () => {
  const document = clone(await loadContract());
  const operations = Object.values(document.paths).flatMap((item) => Object.values(item).filter((value) => value?.operationId));
  operations[1].operationId = operations[0].operationId;
  assert.throws(() => validateOpenApiDocument(document), /duplicate operationId/i);
});

test("OpenAPI 검증기는 안전 오류의 내부 필드와 absolute server를 거부한다", async () => {
  const unsafeField = clone(await loadContract());
  unsafeField.components.schemas.SafeError.properties.stack_trace = { type: "string" };
  assert.throws(() => validateOpenApiDocument(unsafeField), /forbidden token/i);

  const absoluteServer = clone(await loadContract());
  absoluteServer.servers = [{ url: "https://internal.invalid/api" }];
  assert.throws(() => validateOpenApiDocument(absoluteServer), /server|absolute url/i);
});

test("Native Refresh 공개 계약은 단일 opaque credential과 Idempotency 예외를 고정한다", async () => {
  const document = clone(await loadContract());
  const post = document.paths["/api/v1/session/refresh"].post;
  assert.deepEqual(post.parameters ?? [], []);
  assert.equal(post.requestBody.content["application/json"].schema.$ref, "#/components/schemas/NativeRefreshRequest");
  assert.equal(post.responses["200"].$ref, "#/components/responses/NativeCredentialSessionResponse");
  const request = document.components.schemas.NativeRefreshRequest;
  assert.deepEqual(request.required, ["refresh_credential"]);
  assert.equal(request.additionalProperties, false);
  assert.equal(request.properties.refresh_credential.writeOnly, true);
  assert.equal(request.properties.refresh_credential.default, undefined);
  assert.equal(request.properties.refresh_credential.example, undefined);
});

test("Knowledge Package 세 경계와 versioned Sync item은 exact 계약이다", async () => {
  const document = await loadContract();
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/knowledge-packages"]), ["get"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/operations/status"]), ["get"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/output-version-settings"]), ["get", "patch"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/sync-operations"]), ["get", "post"]);
  assert.ok(document.components.schemas.SyncOperation.required.includes("item_ids"));
  const outputSettings = document.components.schemas.OutputVersionSettings;
  assert.deepEqual(outputSettings.required, ["workspace_id", "default_formats", "version_save_mode", "version"]);
  assert.equal(outputSettings.properties.version_save_mode.const, "append_only");
  assert.deepEqual(document.components.schemas.OutputVersionSettingsRequest.required, ["default_formats", "expected_version"]);
  const operationsSchema = document.components.schemas.OperationsStatus;
  assert.deepEqual(operationsSchema.required, ["workspace_id", "overall_status", "checked_at", "components"]);
  assert.deepEqual(operationsSchema.properties.components.items.required, ["component_id", "status", "safe_code", "pending_count", "recovery_action"]);
  assert.deepEqual(Object.keys(document.paths["/api/v1/workspaces/{id}/knowledge-packages/{package_id}/offline-copies"]), ["post"]);
  assert.deepEqual(
    document.components.schemas.KnowledgePackage.required,
    [
      "package_id", "producer", "producer_version", "knowledge_registration_id",
      "output_version_id", "authority", "registration_state", "review_state",
      "digest_sha256", "byte_size", "content_type", "effective_at", "expires_at",
    ],
  );
  assert.equal(document.components.schemas.KnowledgePackage.properties.registration_state.const, "registered");
  assert.equal(document.components.schemas.KnowledgePackage.properties.review_state.const, "approved");
  assert.deepEqual(Object.keys(document.paths["/api/v1/offline-knowledge-copies/{copy_id}/content"]), ["get"]);
  assert.deepEqual(document.components.schemas.SyncItemInput.properties.item_kind.enum, ["source_version", "output_version"]);
  const invalid = clone(document);
  invalid.components.schemas.SyncItemInput.properties.item_kind.default = "output_version";
  assert.throws(() => validateOpenApiDocument(invalid), /versioned Sync item schema mismatch/);
});

test("OpenAPI evidence profile은 보호 baseline과 R1-M8 exact 경로만 허용한다", () => {
  assert.equal(evidenceRelativePathForProfile(), "docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json");
  assert.equal(evidenceRelativePathForProfile("r1-m8-10"), "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/openapi-contract-summary.json");
  assert.throws(() => evidenceRelativePathForProfile("../../outside"), /unsupported evidence profile/);
});

test("사용자 Studio 보고서 수직 Route는 exact 요청·응답 계약을 고정한다", async () => {
  const document = await loadContract();
  const create = document.paths["/api/v1/workspaces/{id}/studio/reports"].post;
  const list = document.paths["/api/v1/workspaces/{id}/studio/outputs"].get;
  assert.equal(create.requestBody.content["application/json"].schema.$ref, "#/components/schemas/StudioReportCreateRequest");
  assert.equal(create.responses["201"].$ref, "#/components/responses/StudioReportResponse");
  assert.equal(list.responses["200"].$ref, "#/components/responses/StudioOutputListResponse");
  const request = document.components.schemas.StudioReportCreateRequest;
  assert.equal(request.additionalProperties, false);
  assert.deepEqual(request.required, ["notebook_id", "source_id", "source_version_id", "run_id", "run_result_id", "title", "purpose"]);
  assert.equal(list.parameters.some((parameter) => parameter.name === "notebook_id" && parameter.in === "query" && parameter.required === true), true);
  const output = document.components.schemas.StudioOutputProjection;
  assert.equal(output.additionalProperties, false);
  assert.equal(output.properties.output_type.const, "evidence_report");
  assert.equal(output.properties.status.const, "draft");
  assert.equal(document.components.parameters.IdempotencyKey.schema.minLength, 16);
  assert.equal(document.components.parameters.IdempotencyKey.schema.maxLength, 128);
});

test("Product Studio Version 상세는 Citation과 lifecycle 재진입 계약을 공개한다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/studio-outputs/{id}/versions"].get;
  assert.equal(operation.operationId, "listStudioOutputVersions");
  assert.equal(operation.responses["200"].$ref, "#/components/responses/StudioVersionHistoryResponse");
  const version = document.components.schemas.StudioVersionProjection;
  assert.equal(version.additionalProperties, false);
  assert.deepEqual(version.required, [
    "output_version_id", "content_version", "previous_version_id", "status", "content",
    "revision_type", "change_reason", "settings_snapshot_id", "citations", "review_request_id",
    "approval_request_id", "approval_id", "delivery_id", "knowledge_registration_id", "output_format",
  ]);
  assert.deepEqual(document.components.schemas.StudioCitationProjection.properties.origin.enum, ["raw_source", "daon_knowledge"]);
  assert.deepEqual(document.components.schemas.StudioCitationLocator.properties.kind.enum, ["page", "section"]);
});

test("Provider 연결 확인은 비밀·Endpoint 없는 안전 상태 계약이다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/model-profiles/{provider_code}/connection-check"].get;
  assert.equal(operation.requestBody, undefined);
  assert.equal(operation.parameters.find((item) => item.name === "workspace_id").in, "query");
  assert.equal(operation.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/ProviderConnectionStatusEnvelope");
  const status = document.components.schemas.ProviderConnectionStatus;
  assert.equal(status.additionalProperties, false);
  assert.deepEqual(status.required, ["provider_code", "status", "checked_at"]);
  assert.deepEqual(status.properties.status.enum, ["ready"]);
  for (const forbidden of ["credential", "api_key", "secret", "base_url", "endpoint", "response_body"]) {
    assert.equal(status.properties[forbidden], undefined, forbidden);
  }
});

test("Session Safe Projection은 Recovery 최소 권한만 exact enum 배열로 공개한다", async () => {
  const document = await loadContract();
  assert.equal(
    document.paths["/api/v1/session"].get.responses["503"].$ref,
    "#/components/responses/ServiceUnavailable"
  );
  const session = document.components.schemas.IdentitySession;
  assert.equal(session.additionalProperties, false);
  assert.deepEqual(session.required, [
    "user_id", "tenant_id", "workspace_id", "session_id", "device_id", "client_kind",
    "delivery", "expires_at", "recovery_operations"
  ]);
  assert.deepEqual(session.properties.recovery_operations, {
    type: "array",
    uniqueItems: true,
    items: {
      type: "string",
      enum: [
        "cloud_backup_create",
        "cloud_backup_get",
        "cloud_backup_list",
        "cloud_restore_cancel",
        "cloud_restore_execute",
        "cloud_restore_get",
        "cloud_restore_preview"
      ]
    }
  });
  for (const forbidden of ["role", "role_scope", "effective_permissions", "permission", "access_credential", "refresh_credential"]) {
    assert.equal(session.properties[forbidden], undefined, forbidden);
  }
});

test("OpenAPI 검증기는 Session ServiceUnavailable 503 누락을 거부한다", async () => {
  const document = clone(await loadContract());
  delete document.paths["/api/v1/session"].get.responses["503"];
  assert.throws(() => validateOpenApiDocument(document), /session.*503|503.*session/i);
});

test("OpenAPI 검증기는 Recovery Unknown Operation과 중복 허용을 거부한다", async () => {
  const unknown = clone(await loadContract());
  unknown.components.schemas.IdentitySession.required.push("workspace_id", "recovery_operations");
  unknown.components.schemas.IdentitySession.properties.workspace_id = { $ref: "#/components/schemas/OpaqueId" };
  unknown.components.schemas.IdentitySession.properties.recovery_operations = {
    type: "array", uniqueItems: true, items: { type: "string", enum: [
      "cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel",
      "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview", "cloud_restore_admin"
    ] }
  };
  assert.throws(() => validateOpenApiDocument(unknown), /recovery operation/i);

  const duplicated = clone(await loadContract());
  duplicated.components.schemas.IdentitySession.required.push("workspace_id", "recovery_operations");
  duplicated.components.schemas.IdentitySession.properties.workspace_id = { $ref: "#/components/schemas/OpaqueId" };
  duplicated.components.schemas.IdentitySession.properties.recovery_operations = {
    type: "array", uniqueItems: true, items: { type: "string", enum: [
      "cloud_backup_create", "cloud_backup_create", "cloud_backup_get", "cloud_backup_list",
      "cloud_restore_cancel", "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"
    ] }
  };
  assert.throws(() => validateOpenApiDocument(duplicated), /recovery operation/i);
});

test("OpenAPI 검증기는 Run Event SSE Content 누락을 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.responses.EventStreamResponse.content = {
    "application/json": { schema: { $ref: "#/components/schemas/SuccessEnvelope" } }
  };
  assert.throws(() => validateOpenApiDocument(document), /text\/event-stream/i);
});

test("Native 로컬 로그인 공개 계약은 credential 비노출 경계와 Windows 고정 발급을 기술한다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/auth/native/login"].post;
  assert.equal(operation.operationId, "nativeLocalLogin");
  assert.deepEqual(operation.parameters ?? [], []);
  assert.equal(operation.requestBody.content["application/json"].schema.$ref, "#/components/schemas/NativeLocalLoginRequest");
  assert.equal(operation.responses["200"].$ref, "#/components/responses/NativeCredentialSessionResponse");

  const request = document.components.schemas.NativeLocalLoginRequest;
  assert.deepEqual(request.required, ["login_id", "password"]);
  assert.equal(request.additionalProperties, false);
  assert.equal(request.properties.password.writeOnly, true);
  assert.equal(request.properties.password.default, undefined);
  assert.equal(request.properties.password.example, undefined);

  const session = document.components.schemas.NativeCredentialSession;
  assert.deepEqual(session.required, [
    "user_id", "tenant_id", "workspace_id", "session_id", "device_id", "client_kind",
    "delivery", "access_credential", "refresh_credential", "expires_at"
  ]);
  assert.equal(session.properties.client_kind.const, "native");
  assert.equal(session.properties.delivery.const, "native_https_opaque_bearer");
  for (const field of ["access_credential", "refresh_credential"]) {
    assert.equal(session.properties[field].format, undefined);
    assert.equal(session.properties[field].default, undefined);
    assert.equal(session.properties[field].example, undefined);
  }
});

test("OpenAPI 검증기는 승인된 Native 요청 password 한 곳 밖의 secret 명칭을 계속 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.schemas.NativeCredentialSession.properties.password = { type: "string" };
  assert.throws(() => validateOpenApiDocument(document), /forbidden token.*password/i);
});

test("OpenAPI 검증기는 승인된 Native password 속성 내부의 secret 명칭도 거부한다", async () => {
  const document = clone(await loadContract());
  document.components.schemas.NativeLocalLoginRequest.properties.password.description = "password must never be logged";
  assert.throws(() => validateOpenApiDocument(document), /forbidden token.*password/i);
});

test("OpenAPI 검증기는 Native opaque credential의 example과 default를 각각 거부한다", async () => {
  for (const field of ["access_credential", "refresh_credential"]) {
    for (const attribute of ["example", "default"]) {
      const document = clone(await loadContract());
      document.components.schemas.NativeCredentialSession.properties[field][attribute] = "opaque-value";
      assert.throws(() => validateOpenApiDocument(document), /Native credential session/i);
    }
  }
});

test("Audit Event 목록은 generic Resource가 아닌 불변 Hash-chain 계약을 사용한다", async () => {
  const document = await loadContract();
  const operation = document.paths["/api/v1/audit-events"].get;
  assert.equal(operation.responses["200"].$ref, "#/components/responses/AuditEventListResponse");
  const required = document.components.schemas.AuditEvent.required;
  for (const field of [
    "sequence", "actor_id", "trace_id", "policy_version", "before", "after",
    "previous_event_hash", "event_hash"
  ]) assert.ok(required.includes(field), field);
  assert.equal(document.components.schemas.AuditEvent.properties.event_hash.pattern, "^[0-9a-f]{64}$");
  assert.equal(document.components.schemas.AuditEvent.properties.occurred_at.format, "date-time");
  assert.equal(document.components.schemas.AuditEventPage.properties.items.items.$ref, "#/components/schemas/AuditEvent");
});

test("OpenAPI 검증기는 Audit Event generic 응답 회귀를 거부한다", async () => {
  const document = clone(await loadContract());
  document.paths["/api/v1/audit-events"].get.responses["200"].$ref = "#/components/responses/ListSuccessResponse";
  assert.throws(() => validateOpenApiDocument(document), /AuditEventListResponse/i);
});

test("Notification·Inbox 공개 계약은 ACL·Cursor·ETag·멱등성 경계를 고정한다", async () => {
  const document = JSON.parse(await readFile(contractPath, "utf8"));
  const list = document.paths["/api/v1/notifications"].get;
  const detail = document.paths["/api/v1/notifications/{id}"].get;
  const read = document.paths["/api/v1/notifications/{id}"].patch;
  const inbox = document.paths["/api/v1/inbox"].get;
  assert.equal(list.responses["200"].$ref, "#/components/responses/NotificationListResponse");
  assert.equal(detail.responses["200"].$ref, "#/components/responses/NotificationResponse");
  assert.equal(inbox.responses["200"].$ref, "#/components/responses/InboxListResponse");
  const readParameters = new Set(read.parameters.map((item) => item.$ref));
  assert.ok(readParameters.has("#/components/parameters/IfMatch"));
  assert.ok(readParameters.has("#/components/parameters/IdempotencyKey"));
  assert.equal(read.requestBody.content["application/json"].schema.$ref, "#/components/schemas/NotificationReadRequest");
  for (const field of ["recipient_id", "source_event_id", "resource_id", "deep_link", "audit_event_id", "trace_id", "delivery_state", "read_at", "version"]) {
    assert.ok(document.components.schemas.Notification.required.includes(field), field);
  }
  assert.equal(document.components.schemas.Notification.properties.title.description.includes("Plain text"), true);
  assert.equal(document.components.schemas.InboxItem.description.includes("read-only projection"), true);
});

test("Product Studio OpenAPI는 Runtime method·exact body·7 media type 불일치를 거부한다", async () => {
  const document = clone(await loadContract());
  document.paths["/api/v1/reviews"].get = structuredClone(document.paths["/api/v1/studio-outputs"].get);
  assert.throws(() => validateOpenApiDocument(document), /Studio Runtime method mismatch/);
  delete document.paths["/api/v1/reviews"].get;
  const bodyMismatch = clone(await loadContract());
  bodyMismatch.paths["/api/v1/approvals"].post.requestBody.$ref = "#/components/requestBodies/ResourceMutation";
  assert.throws(() => validateOpenApiDocument(bodyMismatch), /Studio Runtime request mismatch/);
  const mediaMismatch = clone(await loadContract());
  delete mediaMismatch.components.responses.StudioExportResponse.content["image/png"];
  assert.throws(() => validateOpenApiDocument(mediaMismatch), /Studio export media types mismatch/);
  for (const [apiPath, response] of [
    ["/api/v1/studio-generation-requests", "#/components/responses/StudioGenerationMutationResponse"],
    ["/api/v1/studio-outputs/{id}/versions", "#/components/responses/StudioVersionMutationResponse"],
    ["/api/v1/reviews", "#/components/responses/StudioActionMutationResponse"],
  ]) {
    assert.equal(document.paths[apiPath].post.responses["201"].$ref, response);
    assert.equal(document.paths[apiPath].post.responses["200"].$ref, response);
  }
  const responseMismatch = structuredClone(document);
  responseMismatch.paths["/api/v1/reviews"].post.responses["201"].$ref = "#/components/responses/CreatedResponse";
  assert.throws(() => validateOpenApiDocument(responseMismatch), /Studio Runtime response mismatch/);
  assert.ok(document.components.schemas.StudioRevisionRequest.properties.settings.$ref.endsWith("/StudioGenerationSettings"));
  assert.ok(document.components.schemas.StudioVersionMutationEnvelope.properties.data.required.includes("content"));
  assert.deepEqual(document.components.schemas.StudioVersionMutationEnvelope.properties.data.properties.content.oneOf, [
    { type: "string" }, { type: "object" },
  ]);
  for (const responseName of ["StudioGenerationMutationResponse", "StudioVersionMutationResponse", "StudioActionMutationResponse"]) {
    assert.equal(document.components.responses[responseName].headers.ETag.$ref, "#/components/headers/ETag");
  }
});

test("Question Citation 계약은 원본 페이지와 Daon 지식 구간을 같은 locator로 표현한다", async () => {
  const document = await loadContract();
  const citation = document.components.schemas.GroundedCitation;
  assert.ok(citation.required.includes("locator"));
  assert.equal(citation.properties.locator.$ref, "#/components/schemas/CitationLocator");
  assert.deepEqual(document.components.schemas.CitationLocator.required, ["kind", "value"]);
  assert.deepEqual(document.components.schemas.CitationLocator.properties.kind.enum, ["page", "section"]);
  const response = document.paths["/api/v1/workspaces/{id}/citations/{citation_id}/content"].get.responses["200"];
  assert.ok(response.content["application/pdf"]);
  assert.ok(response.content["text/plain"]);
  assert.equal(response.headers["X-Citation-Locator-Kind"].schema.enum.join(","), "page,section");
});
