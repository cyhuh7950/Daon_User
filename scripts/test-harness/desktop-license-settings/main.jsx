import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createProductWorkspaceState } from "../../../packages/ui/src/product-workspace-model.js";
import { ProductWorkspaceShell } from "../../../packages/ui/src/product-workspace-shell.jsx";

const workspaceId = "workspace-desktop-license-evidence";
function createAdapter(mode) { return Object.freeze({
  listSources: async () => [{ source_id: "source-desktop-license-evidence", source_version_id: "source-version-desktop-license-evidence", filename: "verified-knowledge-snapshot.md", source_state: "ready", processing_state: "completed", job_state: "completed" }],
  listKnowledgePackages: async () => [],
  listStudioOutputs: async () => [],
  getLicense: async () => ({
    product: "daon-user", edition: "enterprise", license_id_hint: "…1-001",
    issued_at: "2026-08-01T00:00:00Z",
    expires_at: mode === "expired" ? "2026-08-14T00:00:00Z" : "2027-08-15T00:00:00Z",
    status: mode === "expired" ? "expired" : "active",
    features: ["citation", "studio_generation", "knowledge_sync"],
    resources: [
      { resource: "notebooks", limit: 100, used: 34, remaining: 66, status: "available" },
      { resource: "generation_runs", limit: 1000, used: 417, remaining: 583, status: "available" },
      { resource: "storage_bytes", limit: 107374182400, used: 32212254720, remaining: 75161927680, status: "available" },
    ],
    warning: mode === "expired"
      ? { code: "LICENSE_EXPIRED", action: "새 생성은 중단되며 기존 자료 조회와 Export는 계속 사용할 수 있습니다." }
      : null,
    creation_allowed: mode !== "expired", existing_read_allowed: true,
    existing_export_allowed: true, can_apply: mode === "admin",
  }),
}); }

function LicenseEvidenceHarness() {
  const [mode, setMode] = useState("readonly");
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "F7") setMode("readonly");
      if (event.key === "F8") setMode("admin");
      if (event.key === "F9") setMode("expired");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return <ProductWorkspaceShell
    key={mode}
    workspaceId={workspaceId}
    adapter={createAdapter(mode)}
    state={createProductWorkspaceState({ status: "loading" })}
  />;
}

createRoot(document.getElementById("root")).render(
  <StrictMode><LicenseEvidenceHarness /></StrictMode>,
);
