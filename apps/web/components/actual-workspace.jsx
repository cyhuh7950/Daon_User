"use client";

import { createProductWorkspaceState } from "@daon-user/ui/product-workspace-model";
import { ProductWorkspaceShell } from "@daon-user/ui/product-workspace-shell";
import { getDocumentProcessingStatus, uploadPdfSource } from "../lib/source-upload-api.js";
import { askGroundedQuestion, citationContentUrl } from "../lib/question-answering-api.js";

export function createWebProductWorkspaceAdapter(workspaceId) {
  return Object.freeze({
    uploadPdf: (file, options) => uploadPdfSource(workspaceId, file, options),
    getProcessingStatus: (processingRunId, options) => getDocumentProcessingStatus(workspaceId, processingRunId, options),
    askQuestion: (input) => askGroundedQuestion(workspaceId, input),
    citationUrl: (citation) => citationContentUrl(workspaceId, citation)
  });
}

export function ActualWorkspace({ workspaceId, adapter, processingPollOptions }) {
  const activeAdapter = workspaceId ? (adapter ?? createWebProductWorkspaceAdapter(workspaceId)) : null;
  return (
    <ProductWorkspaceShell
      workspaceId={workspaceId}
      adapter={activeAdapter}
      processingPollOptions={processingPollOptions}
      state={createProductWorkspaceState(workspaceId
        ? { status: "loading" }
        : { status: "unavailable", safeError: "WORKSPACE_ADAPTER_UNAVAILABLE" })}
    />
  );
}
