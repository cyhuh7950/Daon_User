import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  REQUIRED_PATHS,
  buildSummary,
  canonicalizeDocument,
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

test("OpenAPI 검증기는 Write Header 누락을 거부한다", async () => {
  const document = clone(await loadContract());
  const post = document.paths["/api/v1/session/refresh"].post;
  post.parameters = post.parameters.filter((parameter) => parameter.$ref !== "#/components/parameters/IdempotencyKey");
  assert.throws(() => validateOpenApiDocument(document), /Idempotency-Key/i);
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
