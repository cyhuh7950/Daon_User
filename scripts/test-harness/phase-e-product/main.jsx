import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthLanding } from "../../../apps/web/components/auth-landing.jsx";
import { NotebookHomeWorkspace } from "../../../apps/web/components/notebook-home-workspace.jsx";
import { NotebookProductWorkspace } from "../../../apps/web/components/notebook-product-workspace.jsx";
import "../../../apps/web/app/globals.css";
import "../../../packages/ui/src/workspace.css";
import "../../../packages/ui/src/notebook-home.css";

const parts = window.location.pathname.split("/").filter(Boolean);
let screen = <AuthLanding />;
if (parts.length === 1 && parts[0] === "notebooks") screen = <NotebookHomeWorkspace />;
if (parts.length === 2 && parts[0] === "notebooks") screen = <NotebookProductWorkspace notebookId={decodeURIComponent(parts[1])} />;
createRoot(document.getElementById("root")).render(<StrictMode>{screen}</StrictMode>);
