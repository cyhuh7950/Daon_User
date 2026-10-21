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
  assert.deepEqual(projection.required, ["notebook_id", "title", "source_count", "output_count", "updated_at", "status", "etag"]);
  assert.equal(projection.additionalProperties, false);
  for (const excluded of ["description", "tenant_id", "workspace_id", "policy", "provider", "fingerprint"]) {
    assert.equal(Object.hasOwn(projection.properties, excluded), false);
  }
  assert.equal(Object.keys(contract.paths).some((path) => /notebooks.*(?:delete|share|recommend|template)/u.test(path)), false);
});

test('B 전체 대화 조회 OpenAPI는 페이지·현재 scope·닫힌 turn만 공개한다',async()=>{
  const c=JSON.parse(await readFile(new URL('../../packages/contracts/openapi/v1/openapi.json',import.meta.url),'utf8'));
  const route=c.paths['/api/v1/workspaces/{id}/notebooks/{notebook_id}/conversation-turns'];
  assert.deepEqual(Object.keys(route),['get']);
  assert.equal(route.get.parameters.find(p=>p.name==='limit').schema.maximum,100);
  assert.equal(route.get.parameters.find(p=>p.name==='before').schema.maxLength,2048);
  assert.equal(c.components.schemas.NotebookConversationTurn.additionalProperties,false);
  assert.deepEqual(c.components.schemas.NotebookConversationTurn.properties.state.enum,['pending','awaiting_authorization','completed','failed','uncertain','unavailable','no_knowledge','no_relevant_evidence']);
  assert.equal(c.components.schemas.NotebookConversationPage.properties.items.maxItems,100);
  assert.equal(route.get.responses['200'].headers['Cache-Control'].schema.enum[0],'no-store');
});
