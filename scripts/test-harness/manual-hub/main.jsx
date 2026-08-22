import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { downloadManualAsset, getManualManifest, readManualDocument } from "../../../apps/web/lib/manual-api.js";
import { createProductWorkspaceState } from "../../../packages/ui/src/product-workspace-model.js";
import { ProductWorkspaceShell } from "../../../packages/ui/src/product-workspace-shell.jsx";

const adapter = Object.freeze({
  listSources: async () => [],
  listKnowledgePackages: async () => [],
  listStudioOutputs: async () => [],
  getManualManifest,
  readManual: (documentId, manifest, options) => readManualDocument(documentId, { ...options, manifest }),
  downloadManual: (documentId, format, manifest, options) => downloadManualAsset(documentId, format, { ...options, manifest }),
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ProductWorkspaceShell
      workspaceId="workspace-manual-evidence"
      adapter={adapter}
      state={createProductWorkspaceState({ status: "loading" })}
    />
  </StrictMode>,
);
