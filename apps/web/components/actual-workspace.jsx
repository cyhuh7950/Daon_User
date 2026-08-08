"use client";

import { AdaptiveWorkspace } from "@daon-user/ui";

import {
  getDocumentProcessingStatus,
  uploadPdfSource,
} from "../lib/source-upload-api.js";

export function ActualWorkspace({ workspaceId, routeId, screenId }) {
  const onUploadPdf = async (file) => {
    const submission = await uploadPdfSource(workspaceId, file);
    const processing = await getDocumentProcessingStatus(
      workspaceId, submission.processing_run_id,
    );
    return { ...submission, processing };
  };

  return (
    <AdaptiveWorkspace
      workspaceId={workspaceId}
      routeId={routeId}
      screenId={screenId}
      onUploadPdf={onUploadPdf}
    />
  );
}
