import React from "react";
import { createRoot } from "react-dom/client";
import { ProductWorkspaceShell } from "../../../packages/ui/src/product-workspace-shell.jsx";

const source = {
  sourceId: "source-release-one",
  sourceVersionId: "source-version-release-one",
  filename: "운영 정책 기준서.pdf",
  displayName: "운영 정책 기준서",
  status: "ready",
};

const state = {
  status: "ready",
  sources: [source],
  selectedSource: source,
  answer: {
    answer: "운영 정책은 근거가 확인된 Source와 검토 조건을 함께 보존하도록 요구합니다.",
    insufficient: false,
    run_id: "run-release-one",
    run_result_id: "result-release-one",
    citations: [{ source_id: source.sourceId, source_version_id: source.sourceVersionId, page: 12, label: "p.12" }],
  },
  studioOutputs: [{
    studio_output_id: "output-release-one",
    output_version_id: "v3",
    title: "운영 정책 검토 보고서",
    status: "draft",
    source_count: 1,
    citations: 4,
    settings_snapshot_id: "snapshot-3",
    content: "핵심 정책과 검토 조건을 Source 근거에 맞춰 정리한 보고서입니다.",
  }],
  studioLocks: [],
  studioStatus: "ready",
  studioSafeError: null,
  safeError: null,
};

createRoot(document.getElementById("root")).render(
  <ProductWorkspaceShell workspaceId="workspace-release-one" state={state} />,
);
