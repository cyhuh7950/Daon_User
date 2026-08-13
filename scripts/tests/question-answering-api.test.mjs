import assert from "node:assert/strict";
import test from "node:test";

import { askGroundedQuestion, authorizeGroundedQuestion, citationContentUrl } from "../../apps/web/lib/question-answering-api.js";

const citation = Object.freeze({
  citation_id: "citation-1",
  source_id: "source-1",
  source_version_id: "version-1",
  evidence_span_id: "span-1",
  page: 2
});

const answerData = Object.freeze({
  run_id: "run-1",
  run_result_id: "result-1",
  answer: "검증된 근거 답변",
  insufficient: false,
  citations: [citation]
});

const envelope = (data = answerData) => ({
  data,
  meta: { trace_id: "trace-1", workspace_id: "workspace-1" }
});

const request = (payload) => askGroundedQuestion(
  "workspace-1",
  { sourceId: "source-1", sourceVersionId: "version-1", question: "근거는?" },
  { idempotencyKey: "question-1", fetchImpl: async () => Response.json(payload) }
);

test("Question API는 exact outer/data와 Citation Safe DTO만 반환한다", async () => {
  assert.deepEqual(await request(envelope()), answerData);
  assert.equal(
    citationContentUrl("workspace-1", citation),
    "/bff/api/workspaces/workspace-1/citations/citation-1/content#page=2"
  );
});

test("Question authorization은 same-origin exact body와 동일 Idempotency-Key만 사용한다", async () => {
  const calls = [];
  const data = await authorizeGroundedQuestion("workspace-1", {
    sourceId: "source-1", sourceVersionId: "version-1", question: "근거는?", password: "memory-only",
  }, { idempotencyKey: "question-auth-0001", fetchImpl: async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    return Response.json({ data: { step_up_authorization_id: "grant-1", expires_at: "2026-08-13T00:00:00Z", run_id: "run-1", request_fingerprint: `sha256:${"a".repeat(64)}` }, meta: { trace_id: "trace-1" } }, { status: 201 });
  } });
  assert.equal(data.step_up_authorization_id, "grant-1");
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/questions/authorization");
  assert.equal(calls[0].init.headers["Idempotency-Key"], "question-auth-0001");
  assert.deepEqual(Object.keys(calls[0].body).sort(), ["password", "question", "source_id", "source_version_id"]);
});

test("Question API는 non-JSON gateway timeout과 upstream failure를 안전 오류로 보존한다", async () => {
  const execute = (status) => askGroundedQuestion(
    "workspace-1",
    { sourceId: "source-1", sourceVersionId: "version-1", question: "근거는?" },
    {
      idempotencyKey: "question-gateway-error",
      fetchImpl: async () => new Response("<html>gateway</html>", {
        status, headers: { "Content-Type": "text/html" },
      }),
    },
  );
  await assert.rejects(() => execute(504), { message: "GATEWAY_TIMEOUT" });
  await assert.rejects(() => execute(502), { message: "UPSTREAM_FAILURE" });
});

for (const [name, payload] of [
  ["citations 객체", envelope({ ...answerData, citations: { citation } })],
  ["invalid citation id", envelope({ ...answerData, citations: [{ ...citation, citation_id: "bad/id" }] })],
  ["invalid citation page", envelope({ ...answerData, citations: [{ ...citation, page: 0 }] })],
  ["unknown data field", envelope({ ...answerData, unexpected: true })],
  ["unknown outer field", { ...envelope(), unexpected: true }]
]) {
  test(`Question API는 ${name} 응답을 QUESTION_RESPONSE_INVALID로 거부한다`, async () => {
    await assert.rejects(request(payload), { message: "QUESTION_RESPONSE_INVALID" });
  });
}
