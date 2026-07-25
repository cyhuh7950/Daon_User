import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { DesktopShell } from "./desktop-shell.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <DesktopShell />
  </StrictMode>
);
