"use client";

import { useMemo, useState } from "react";

import { createProductWorkspaceState } from "@daon-user/ui/product-workspace-model";
import { ProductWorkspaceShell } from "@daon-user/ui/product-workspace-shell";
import { ProviderSettingsWorkspace } from "./provider-settings-workspace.jsx";
import { getDocumentProcessingStatus, reprocessSource, uploadSource } from "../lib/source-upload-api.js";
import { askGroundedQuestion, authorizeGroundedQuestion, citationContentUrl } from "../lib/question-answering-api.js";
import { createGroundedReport, createStudioGeneration, getStudioGenerationJob, createStudioVersion, createStudioAction, downloadStudioExport, getWorkspaceOperationsStatus, getWorkspaceOutputVersionSettings, issueStudioStepUp, listProductStudioOutputs, listStudioOutputs, listStudioVersions, listWorkspaceConnectors, listWorkspaceKnowledgePackages, listWorkspaceSources, reconnectWorkspaceConnector, registerWorkspaceConnector, unregisterWorkspaceConnector, saveWorkspaceOutputVersionSettings, unbindWorkspaceSource } from "../lib/product-workspace-api.js";
import { issueStudioExportStepUp } from "../lib/product-workspace-api.js";
import { getStudioApprovalPolicy, saveStudioApprovalPolicy, issueStudioApprovalPolicyStepUp, withdrawStudioApprovalRequest, listStudioApprovalNotices, markStudioApprovalNoticeRead } from "../lib/product-workspace-api.js";
import { approveWorkspaceSyncOperation, listWorkspaceSyncOperations } from "../lib/sync-approval-settings-api.js";
import { getEffectiveEgressPolicy } from "../lib/egress-policy-api.js";
import { applyCurrentOrganizationLicenseWithStepUp, getWorkspaceLicense } from "../lib/license-api.js";
import { downloadManualAsset, getManualManifest, readManualDocument } from "../lib/manual-api.js";
import { cancelSourceDeletionRequest, getSourceDeletionRequest, issueSourceDeletionStepUp, requestSourceDeletion } from "../lib/source-retention-api.js";
import { importWebsiteSource, getWebsiteSnapshot, validWebsiteUrl } from '../lib/website-source-api.js';
import { searchOpenLaw,previewOpenLaw,importOpenLaw,getOpenLawSnapshot,getOpenLawCitation,listWorkspaceConnectorSources } from '../lib/product-workspace-api.js';

export function createWebProductWorkspaceAdapter(workspaceId, notebookId) {
  const notebookOptions = (options = {}) => (notebookId
    ? { ...options, notebookId }
    : options);
  const notebookInput = (input = {}) => (notebookId
    ? { ...input, notebookId }
    : input);
  return Object.freeze({
    listSources: (options) => listWorkspaceSources(workspaceId, notebookOptions(options)),
    searchOpenLaw: (input,options={}) => searchOpenLaw(workspaceId,{...input,notebook_id:notebookId ?? options.notebookId},options),
    previewOpenLaw: (input,options={}) => previewOpenLaw(workspaceId,{...input,notebook_id:notebookId ?? options.notebookId},options),
    importOpenLaw: (input,options={}) => importOpenLaw(workspaceId,{...input,notebook_id:notebookId ?? options.notebookId},options),
    getOpenLawSnapshot: (version,options) => getOpenLawSnapshot(workspaceId,version,notebookOptions(options)),
    getOpenLawCitation: (citation,options) => getOpenLawCitation(workspaceId,citation,notebookOptions(options)),
    listConnectorSources: (connectorId,options) => listWorkspaceConnectorSources(workspaceId,connectorId,options),
    validWebsiteUrl,
    importWebsite: ({url,publicPageConfirmed}, options = {}) => importWebsiteSource(workspaceId,
      {notebook_id:notebookId ?? options.notebookId,url,public_page_confirmed:publicPageConfirmed},options),
    getWebsiteSnapshot: (sourceVersionId, options) => getWebsiteSnapshot(workspaceId,sourceVersionId,notebookOptions(options)),
    unbindSource: (source, options = {}) => unbindWorkspaceSource(workspaceId, { sourceId: source?.sourceId, sourceVersionId: source?.sourceVersionId }, { ...notebookOptions(options), etag: options.etag ?? options.bindingEtag }),
    requestSourceDeletion: (source, options) => requestSourceDeletion(source.sourceId, notebookOptions(options)),
    issueSourceDeletionStepUp,
    getSourceDeletionRequest,
    cancelSourceDeletionRequest,
    listKnowledgePackages: (options) => listWorkspaceKnowledgePackages(workspaceId, options),
    listConnectors: (options) => listWorkspaceConnectors(workspaceId, options),
    registerConnector: (input, options) => registerWorkspaceConnector(workspaceId, input, options),
    reconnectConnector: (connectorId, options) => reconnectWorkspaceConnector(workspaceId, connectorId, options),
    unregisterConnector: (connectorId, options) => unregisterWorkspaceConnector(workspaceId, connectorId, options),
    getOperationsStatus: (options) => getWorkspaceOperationsStatus(workspaceId, options),
    getOutputVersionSettings: (options) => getWorkspaceOutputVersionSettings(workspaceId, options),
    saveOutputVersionSettings: (settings, options) => saveWorkspaceOutputVersionSettings(workspaceId, settings, options),
    listSyncOperations: (options) => listWorkspaceSyncOperations(workspaceId, options),
    approveSyncOperation: (operation, input, options) => approveWorkspaceSyncOperation(workspaceId, operation, input, options),
    getEgressPolicy: (options = {}) => getEffectiveEgressPolicy({ workspaceId, signal: options.signal }).then((result) => result.data),
    getLicense: (options) => getWorkspaceLicense(workspaceId, options),
    applyLicense: (document, password, options) => applyCurrentOrganizationLicenseWithStepUp(document, password, options),
    getManualManifest,
    readManual: (documentId, manifest, options) => readManualDocument(documentId, { ...options, manifest }),
    downloadManual: (documentId, format, manifest, options) => downloadManualAsset(documentId, format, { ...options, manifest }),
    uploadSource: (file, options) => uploadSource(workspaceId, file, notebookOptions(options)),
    uploadPdf: (file, options) => uploadSource(workspaceId, file, notebookOptions(options)),
    reprocessSource: (source, options) => reprocessSource(workspaceId, source, notebookOptions(options)),
    getProcessingStatus: (processingRunId, options) => getDocumentProcessingStatus(workspaceId, processingRunId, notebookOptions(options)),
    askQuestion: (input, options) => askGroundedQuestion(workspaceId, notebookInput(input), options),
    authorizeQuestion: (input, options) => authorizeGroundedQuestion(workspaceId, notebookInput(input), options),
    citationUrl: (citation, options) => citationContentUrl(workspaceId, citation, notebookOptions(options)),
    createReport: (input, options) => createGroundedReport(workspaceId, input, notebookOptions(options)),
    listStudioOutputs: (options) => listStudioOutputs(workspaceId, notebookOptions(options)),
    listProductStudioOutputs: (options) => listProductStudioOutputs(workspaceId, notebookOptions(options)),
    createGeneration: (input, options) => createStudioGeneration(workspaceId, input, notebookOptions(options)),
    getGenerationJob: (jobId, options) => getStudioGenerationJob(workspaceId, jobId, notebookOptions(options)),
    createStudioVersion: (outputId, input, options) => createStudioVersion(workspaceId, outputId, input, notebookOptions(options)),
    listStudioVersions: (outputId, options) => listStudioVersions(workspaceId, outputId, notebookOptions(options)),
    createStudioAction: (action, input, options) => createStudioAction(workspaceId, action, input, notebookOptions(options)),
    issueStudioStepUp,
    getStudioApprovalPolicy: (options) => getStudioApprovalPolicy(workspaceId, notebookOptions(options)),
    saveStudioApprovalPolicy: (input, options) => saveStudioApprovalPolicy(workspaceId, input, notebookOptions(options)),
    issueStudioApprovalPolicyStepUp,
    withdrawStudioApprovalRequest: (requestId, versionId, options) => withdrawStudioApprovalRequest(workspaceId, requestId, versionId, notebookOptions(options)),
    listStudioApprovalNotices: (options) => listStudioApprovalNotices(workspaceId, notebookOptions(options)),
    markStudioApprovalNoticeRead: (noticeId, version, options) => markStudioApprovalNoticeRead(workspaceId, noticeId, version, notebookOptions(options)),
    issueStudioExportStepUp: (outputId, versionId, format, password, options) => issueStudioExportStepUp(workspaceId, outputId, versionId, format, password, notebookOptions(options)),
    downloadStudioExport: (outputId, versionId, format, options) => downloadStudioExport(workspaceId, outputId, versionId, format, notebookOptions(options)),
  });
}

export function ActualWorkspace({ workspaceId, notebookId, notebookTitle, adapter, processingPollOptions, onLogout,
  initialSourceVersionId = null, initialStudioOutput = null }) {
  const activeAdapter = useMemo(() => workspaceId ? (adapter ?? createWebProductWorkspaceAdapter(workspaceId, notebookId)) : null, [workspaceId, notebookId, adapter]);
  const [providerSettingsDirty, setProviderSettingsDirty] = useState(false);
  const [providerSettingsSaving, setProviderSettingsSaving] = useState(false);
  return (
    <ProductWorkspaceShell
      workspaceId={workspaceId}
      notebookTitle={notebookTitle}
      adapter={activeAdapter}
      initialSourceVersionId={initialSourceVersionId}
      initialStudioOutput={initialStudioOutput}
      processingPollOptions={processingPollOptions}
      providerSettings={<ProviderSettingsWorkspace key="provider-settings" workspaceId={workspaceId} embedded onDirtyChange={setProviderSettingsDirty} onSavingChange={setProviderSettingsSaving} />}
      providerSettingsDirty={providerSettingsDirty}
      providerSettingsSaving={providerSettingsSaving}
      onLogout={onLogout}
      state={createProductWorkspaceState(workspaceId
        ? { status: "loading" }
        : { status: "unavailable", safeError: "WORKSPACE_ADAPTER_UNAVAILABLE" })}
    />
  );
}
