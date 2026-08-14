"use client";

import { createProductWorkspaceState } from "@daon-user/ui/product-workspace-model";
import { ProductWorkspaceShell } from "@daon-user/ui/product-workspace-shell";
import { ProviderSettingsWorkspace } from "./provider-settings-workspace.jsx";
import { getDocumentProcessingStatus, uploadPdfSource } from "../lib/source-upload-api.js";
import { askGroundedQuestion, authorizeGroundedQuestion, citationContentUrl } from "../lib/question-answering-api.js";
import { createGroundedReport, createStudioGeneration, createStudioVersion, createStudioAction, downloadStudioExport, getWorkspaceOperationsStatus, getWorkspaceOutputVersionSettings, issueStudioStepUp, listProductStudioOutputs, listStudioOutputs, listStudioVersions, listWorkspaceKnowledgePackages, listWorkspaceSources, saveWorkspaceOutputVersionSettings } from "../lib/product-workspace-api.js";
import { approveWorkspaceSyncOperation, listWorkspaceSyncOperations } from "../lib/sync-approval-settings-api.js";
import { getEffectiveEgressPolicy } from "../lib/egress-policy-api.js";

export function createWebProductWorkspaceAdapter(workspaceId) {
  return Object.freeze({
    listSources: (options) => listWorkspaceSources(workspaceId, options),
    listKnowledgePackages: (options) => listWorkspaceKnowledgePackages(workspaceId, options),
    getOperationsStatus: (options) => getWorkspaceOperationsStatus(workspaceId, options),
    getOutputVersionSettings: (options) => getWorkspaceOutputVersionSettings(workspaceId, options),
    saveOutputVersionSettings: (settings, options) => saveWorkspaceOutputVersionSettings(workspaceId, settings, options),
    listSyncOperations: (options) => listWorkspaceSyncOperations(workspaceId, options),
    approveSyncOperation: (operation, input, options) => approveWorkspaceSyncOperation(workspaceId, operation, input, options),
    getEgressPolicy: (options = {}) => getEffectiveEgressPolicy({ workspaceId, signal: options.signal }).then((result) => result.data),
    uploadPdf: (file, options) => uploadPdfSource(workspaceId, file, options),
    getProcessingStatus: (processingRunId, options) => getDocumentProcessingStatus(workspaceId, processingRunId, options),
    askQuestion: (input, options) => askGroundedQuestion(workspaceId, input, options),
    authorizeQuestion: (input, options) => authorizeGroundedQuestion(workspaceId, input, options),
    citationUrl: (citation) => citationContentUrl(workspaceId, citation),
    createReport: (input, options) => createGroundedReport(workspaceId, input, options),
    listStudioOutputs: (options) => listStudioOutputs(workspaceId, options),
    listProductStudioOutputs: (options) => listProductStudioOutputs(workspaceId, options),
    createGeneration: (input, options) => createStudioGeneration(workspaceId, input, options),
    createStudioVersion: (outputId, input, options) => createStudioVersion(workspaceId, outputId, input, options),
    listStudioVersions: (outputId, options) => listStudioVersions(workspaceId, outputId, options),
    createStudioAction: (action, input, options) => createStudioAction(workspaceId, action, input, options),
    issueStudioStepUp,
    downloadStudioExport: (outputId, versionId, format, options) => downloadStudioExport(workspaceId, outputId, versionId, format, options),
  });
}

export function ActualWorkspace({ workspaceId, adapter, processingPollOptions }) {
  const activeAdapter = workspaceId ? (adapter ?? createWebProductWorkspaceAdapter(workspaceId)) : null;
  return (
    <ProductWorkspaceShell
      workspaceId={workspaceId}
      adapter={activeAdapter}
      processingPollOptions={processingPollOptions}
      providerSettings={<ProviderSettingsWorkspace workspaceId={workspaceId} embedded />}
      state={createProductWorkspaceState(workspaceId
        ? { status: "loading" }
        : { status: "unavailable", safeError: "WORKSPACE_ADAPTER_UNAVAILABLE" })}
    />
  );
}
