import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  askGroundedQuestion,
  citationContentUrl,
} from "../../apps/web/lib/question-answering-api.js";


test("question client posts only to same-origin BFF with bounded lineage input", async () => {
  const calls = [];
  const answer = await askGroundedQuestion(
    "workspace-cp3",
    {
      sourceId: "source-cp3",
      sourceVersionId: "source-version-cp3",
      question: "What is the citation verification phrase?",
    },
    {
      idempotencyKey: "question-cp3",
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return Response.json({
          data: {
            run_id: "run-cp3", run_result_id: "result-cp3", answer: "ORANGE-COMPASS-42", insufficient: false,
            citations: [{ citation_id: "citation-cp3", source_id: "source-cp3", source_version_id: "source-version-cp3", evidence_span_id: "span-cp3", page: 2 }],
          },
          meta: { trace_id: "trace-cp3", workspace_id: "workspace-cp3" },
        });
      },
    },
  );

  assert.equal(answer.answer, "ORANGE-COMPASS-42");
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-cp3/questions");
  assert.equal(calls[0].init.headers["Idempotency-Key"], "question-cp3");
  assert.doesNotMatch(JSON.stringify(calls), /localhost|127\.0\.0\.1|NEXT_PUBLIC/u);
});

test("Citation URL is same-origin and opens the exact persisted page", () => {
  assert.equal(
    citationContentUrl("workspace-cp3", { citation_id: "citation-cp3", page: 2 }),
    "/bff/api/workspaces/workspace-cp3/citations/citation-cp3/content#page=2",
  );
});

test("Actual middle Pane keeps live question UI distinct from Prototype", async () => {
  const [workspace, adaptive, runPane] = await Promise.all([
    readFile(new URL("../../apps/web/components/actual-workspace.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../packages/ui/src/adaptive-workspace.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../packages/ui/src/run-model-evidence-pane.jsx", import.meta.url), "utf8"),
  ]);
  assert.match(workspace, /askGroundedQuestion/);
  assert.match(adaptive, /actualQuestion/);
  assert.match(runPane, /Actual Workspace 질문/);
  assert.match(runPane, /실제 Provider·Index·Citation/);
  assert.match(runPane, /Prototype/);
});
