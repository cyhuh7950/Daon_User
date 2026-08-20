import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("OpenAPI는 Notebook create/list/get/update-title exact 계약만 공개한다", async () => {
  const contract = JSON.parse(await readFile(new URL("../../packages/contracts/openapi/v1/openapi.json", import.meta.url), "utf8"));
  const collection = contract.paths["/api/v1/workspaces/{id}/notebooks"];
  const item = contract.paths["/api/v1/workspaces/{id}/notebooks/{notebook_id}"];
  const context = contract.paths["/api/v1/workspaces/{id}/notebooks/{notebook_id}/context"];
  assert.deepEqual(Object.keys(collection).sort(), ["get", "post"]);
  assert.deepEqual(Object.keys(item).sort(), ["get", "patch"]);
  assert.deepEqual(Object.keys(context), ["get"]);
  assert.equal(context.get.responses["200"].content["application/json"].schema.$ref, "#/components/schemas/NotebookContextEnvelope");
  const selected = contract.components.schemas.NotebookSelectedContext;
  assert.equal(selected.required.includes("conversation"), true);
  assert.equal(selected.properties.conversation.oneOf[1].properties.answer.$ref, "#/components/schemas/GroundedAnswer");
  assert.equal(contract.components.schemas.GroundedQuestionRequest.required.includes("notebook_id"), true);
  assert.equal(contract.components.schemas.GroundedQuestionAuthorizationRequest.required.includes("notebook_id"), true);
  assert.equal(collection.post.parameters.some((entry) => entry.$ref === "#/components/parameters/IdempotencyKey"), true);
  assert.equal(item.patch.parameters.some((entry) => entry.$ref === "#/components/parameters/IfMatch"), true);
  assert.equal(item.patch.parameters.some((entry) => entry.$ref === "#/components/parameters/IdempotencyKey"), true);
  const projection = contract.components.schemas.NotebookHomeProjection;
  assert.deepEqual(projection.required, ["notebook_id", "title", "source_count", "output_count", "updated_at", "status"]);
  assert.equal(projection.additionalProperties, false);
  for (const excluded of ["description", "tenant_id", "workspace_id", "policy", "provider", "fingerprint"]) {
    assert.equal(Object.hasOwn(projection.properties, excluded), false);
  }
  assert.equal(Object.keys(contract.paths).some((path) => /notebooks.*(?:delete|share|recommend|template)/u.test(path)), false);
});
