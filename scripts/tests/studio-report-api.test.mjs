import assert from "node:assert/strict";
import test from "node:test";

import { createGroundedReport, listStudioOutputs } from "../../apps/web/lib/product-workspace-api.js";

const output = Object.freeze({
  studio_output_id: "output-1", output_version_id: "version-1", output_type: "evidence_report",
  title: "승인 검토 보고서", purpose: "근거 기반 요약", status: "draft",
  content: "근거 답변", run_id: "run-1", run_result_id: "result-1",
  citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "source-version-1", evidence_span_id: "span-1", page: 2 }],
});
const request = Object.freeze({
  source_id: "source-1", source_version_id: "source-version-1", run_id: "run-1",
  run_result_id: "result-1", title: "승인 검토 보고서", purpose: "근거 기반 요약",
});

test("Studio 생성·목록 Client는 same-origin exact Route와 DTO를 사용한다", async () => {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, init });
    if (init.method === "POST") return Response.json({ data: output, meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false } }, { status: 201 });
    return Response.json({ data: { outputs: [output] }, meta: { trace_id: "trace-2", workspace_id: "workspace-1" } });
  };
  assert.deepEqual(await createGroundedReport("workspace-1", request, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "report-key-00001" }), output);
  assert.deepEqual(await listStudioOutputs("workspace-1", { notebookId: "notebook-1", fetchImpl }), [output]);
  assert.deepEqual(calls.map(({ url }) => url), [
    "/bff/api/workspaces/workspace-1/studio/reports",
    "/bff/api/workspaces/workspace-1/studio/outputs?notebook_id=notebook-1",
  ]);
  assert.equal(JSON.parse(calls[0].init.body).notebook_id, "notebook-1");
  assert.equal(calls[0].init.headers["Idempotency-Key"], "report-key-00001");
  assert.equal(calls[1].init.method, "GET");
});

test("Studio Client는 Idempotency-Key 15자를 거부하고 16자를 허용한다", async () => {
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    return Response.json({ data: output, meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false } });
  };
  await assert.rejects(
    () => createGroundedReport("workspace-1", request, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "123456789012345" }),
    /STUDIO_INPUT_INVALID/,
  );
  assert.equal(calls, 0);
  await createGroundedReport("workspace-1", request, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "1234567890123456" });
  assert.equal(calls, 1);
});

test("Studio Client는 malformed·내부 URL 유출 응답을 거부한다", async () => {
  for (const malformed of [
    { ...output, unexpected: true },
    { ...output, content: "http://internal:8000/private" },
    { ...output, citations: [{ ...output.citations[0], page: 0 }] },
  ]) {
    await assert.rejects(
      listStudioOutputs("workspace-1", { notebookId: "notebook-1", fetchImpl: async () => Response.json({ data: { outputs: [malformed] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } }) }),
      { message: "STUDIO_RESPONSE_INVALID" },
    );
  }
});
