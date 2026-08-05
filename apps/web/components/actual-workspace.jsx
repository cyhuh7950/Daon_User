"use client";

import { AdaptiveWorkspace } from "@daon-user/ui";

import { uploadPdfSource } from "../lib/source-upload-api.js";

export function ActualWorkspace({ workspaceId, routeId, screenId }) {
  const onUploadPdf = (file) => uploadPdfSource(workspaceId, file);

  return (
    <AdaptiveWorkspace
      workspaceId={workspaceId}
      routeId={routeId}
      screenId={screenId}
      onUploadPdf={onUploadPdf}
    />
  );
}
