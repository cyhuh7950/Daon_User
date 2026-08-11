import assert from "node:assert/strict";
import test from "node:test";

import { askGroundedQuestion, citationContentUrl } from "../../apps/web/lib/question-answering-api.js";

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
