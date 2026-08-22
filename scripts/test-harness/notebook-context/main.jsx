import React from "react";
import { createRoot } from "react-dom/client";
import actualContext from "../../../docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-d-notebook-context-actual.json";
import { createNotebookContextWorkspaceAdapter } from "../../../packages/ui/src/notebook-context-adapter.js";
import { createProductWorkspaceState } from "../../../packages/ui/src/product-workspace-model.js";
import { ProductWorkspaceShell } from "../../../packages/ui/src/product-workspace-shell.jsx";

const emptyContext = Object.freeze({
  notebook_id: "notebook-empty-actual", sources: [], knowledge_context_ids: [],
  conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [],
});
const mode = new URLSearchParams(globalThis.location.search).get("mode") === "empty" ? "empty" : "existing";
const context = mode === "empty" ? emptyContext : actualContext;
const baseAdapter = {
  listSources: async () => [{ source_id: "source-ctx-1", source_version_id: "source-version-ctx-1", filename: "PostgreSQL 검증 Source.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
  resolveKnowledgeContext: async (id) => id === "scope-snapshot-ctx-1" ? { package_ids: ["knowledge-package-ctx-1"] } : { package_ids: [] },
  listKnowledgePackages: async () => [{ package_id: "knowledge-package-ctx-1", producer: "daon3", producer_version: "3.0", authority: "approved", registration_state: "registered" }],
  getConversationThread: async (id) => ({ conversation_thread_id: id, answer: { run_id: "run-ctx-1", run_result_id: "run-result-ctx-1", answer: "PostgreSQL Notebook에 보존된 대화입니다.", insufficient: false, citations: [] } }),
  listStudioOutputs: async () => [{ studio_output_id: "studio-output-ctx-1", output_version_id: "output-version-ctx-1", output_type: "evidence_report", title: "PostgreSQL 보존 보고서", purpose: "Notebook Context 검증", status: "draft", content: "검증 내용", run_id: "run-ctx-1", run_result_id: "run-result-ctx-1", citations: [] }],
};
const adapter = createNotebookContextWorkspaceAdapter(baseAdapter, context);

createRoot(document.getElementById("root")).render(<>
  <ProductWorkspaceShell workspaceId="workspace-notebook-context" state={createProductWorkspaceState()} adapter={adapter} />
  <div id="context-evidence" role="status">{mode === "existing" ? "ACTUAL_POSTGRES_CONTEXT · Source·대화·Library 보존" : "NEW_NOTEBOOK_CONTEXT · empty"}</div>
</>);
