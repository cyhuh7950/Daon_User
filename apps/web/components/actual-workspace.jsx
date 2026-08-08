"use client";

import { useState } from "react";
import { AdaptiveWorkspace } from "@daon-user/ui";

import {
  getDocumentProcessingStatus,
  uploadPdfSource,
} from "../lib/source-upload-api.js";
import { askGroundedQuestion, citationContentUrl } from "../lib/question-answering-api.js";

export function ActualWorkspace({ workspaceId, routeId, screenId }) {
  const [currentSource, setCurrentSource] = useState(null);
  const onUploadPdf = async (file) => {
    const submission = await uploadPdfSource(workspaceId, file);
    const processing = await getDocumentProcessingStatus(
      workspaceId, submission.processing_run_id,
    );
    setCurrentSource({
      sourceId: submission.source_id,
      sourceVersionId: submission.source_version_id,
    });
    return { ...submission, processing };
  };

  return (
    <AdaptiveWorkspace
      workspaceId={workspaceId}
      routeId={routeId}
      screenId={screenId}
      onUploadPdf={onUploadPdf}
      actualQuestion={{
        source: currentSource,
        ask: (input) => askGroundedQuestion(workspaceId, input),
        citationUrl: (citation) => citationContentUrl(workspaceId, citation),
      }}
    />
  );
}
