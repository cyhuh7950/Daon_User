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
  const post = Object.values(document.paths).map((item) => item.post).find(Boolean);
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
