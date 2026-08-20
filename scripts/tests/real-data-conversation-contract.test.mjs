import assert from "node:assert/strict";
import test from "node:test";

import { isGeneralConversationIntent } from "../../packages/ui/src/conversation-intent.js";
import { askGroundedQuestion } from "../../apps/web/lib/question-answering-api.js";
import { createWindowsWorkspaceAdapter } from "../../apps/desktop/src/windows-workspace-adapter.js";
import contract from "../../packages/contracts/openapi/v1/openapi.json" with { type: "json" };
import { verifyProductionFixtureBoundary } from "../verify-production-fixture-boundary.mjs";

test("일반대화 allowlist는 exact이며 사실 질의 suffix를 fail-close한다", () => {
  for (const value of ["안녕", "안녕하세요!", "안녕하세요?", "고마워", "감사합니다.", "Daon 사용법 알려줘"]) {
    assert.equal(isGeneralConversationIntent(value), true, value);
  }
  for (const value of ["", "안녕, 삼성 매출 알려줘", "고마워. 이 문서를 요약해줘", "2026년 매출은?", "Ｄａｏｎ 사용법 알려줘", "안녕하세요！", "안녕하세요？", "안녕하세요　"]) {
    assert.equal(isGeneralConversationIntent(value), false, value);
  }
});

test("Web Question은 일반대화만 context 없는 same-origin body를 허용한다", async () => {
  const calls = [];
  const answer = { run_id: "run-general", run_result_id: "result-general", answer: "안녕하세요.", insufficient: false, citations: [] };
  await askGroundedQuestion("workspace-1", { notebookId: "notebook-1", question: "안녕하세요!" }, {
    idempotencyKey: "question-general-0001",
    fetchImpl: async (url, init) => {
      calls.push({ url, body: JSON.parse(init.body) });
      return Response.json({ data: answer, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
    },
  });
  assert.deepEqual(calls, [{
    url: "/bff/api/workspaces/workspace-1/questions",
    body: { notebook_id: "notebook-1", question: "안녕하세요!" },
  }]);
  await assert.rejects(
    askGroundedQuestion("workspace-1", { notebookId: "notebook-1", question: "2026년 매출은?" }),
    { message: "QUESTION_INPUT_INVALID" },
  );
});

test("Windows Question은 일반대화만 source 없는 Native body를 허용한다", async () => {
  const calls = [];
  const adapter = createWindowsWorkspaceAdapter("workspace-1", {
    notebookId: "notebook-1",
    invoke: async (command, args) => {
      calls.push({ command, args });
      return { run_id: "run-general", run_result_id: "result-general", answer: "안녕하세요.", insufficient: false, citations: [] };
    },
  });
  await adapter.askQuestion({ knowledgeContext: null, question: "안녕하세요!" });
  assert.deepEqual(calls[0], {
    command: "workspace_ask_question",
    args: { input: { workspace_id: "workspace-1", notebook_id: "notebook-1", question: "안녕하세요!" } },
  });
  await assert.rejects(adapter.askQuestion({ knowledgeContext: null, question: "2026년 매출은?" }), {
    message: "QUESTION_INPUT_INVALID",
  });
});

test("OpenAPI Question 기존 DTO는 일반대화 no-context semantic branch를 exact 기술한다", () => {
  const request = contract.components.schemas.GroundedQuestionRequest;
  const authorization = contract.components.schemas.GroundedQuestionAuthorizationRequest;
  for (const schema of [request, authorization]) {
    assert.equal(schema.oneOf.length, 3);
    assert.deepEqual(schema.oneOf[2], {
      not: { anyOf: [
        { required: ["source_id"] }, { required: ["source_version_id"] }, { required: ["knowledge_context"] },
      ] },
    });
    assert.match(schema.description, /narrow general conversation/u);
  }
});

test("production Web/Desktop/UI import graph에는 fixture·test harness 유입이 없다", async () => {
  const result = await verifyProductionFixtureBoundary(new URL("../..", import.meta.url));
  assert.equal(result.violations.length, 0, result.violations.join("\n"));
  assert.equal(result.visited > 8, true);
});
