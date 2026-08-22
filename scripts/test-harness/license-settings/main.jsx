import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { getWorkspaceLicense } from "../../../apps/web/lib/license-api.js";
import { createProductWorkspaceState } from "../../../packages/ui/src/product-workspace-model.js";
import { ProductWorkspaceShell } from "../../../packages/ui/src/product-workspace-shell.jsx";

const workspaceId = "workspace-license-evidence";
const adapter = Object.freeze({
  listSources: async () => [{
    source_id: "source-license-evidence", source_version_id: "source-version-license-evidence",
    filename: "verified-knowledge-snapshot.md", source_state: "ready",
    processing_state: "completed", job_state: "completed",
  }],
  listKnowledgePackages: async () => [],
  listStudioOutputs: async () => [],
  getLicense: (options) => getWorkspaceLicense(workspaceId, options),
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ProductWorkspaceShell
      workspaceId={workspaceId}
      adapter={adapter}
      state={createProductWorkspaceState({ status: "loading" })}
    />
  </StrictMode>,
);
