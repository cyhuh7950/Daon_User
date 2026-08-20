import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createNotebook, listNotebooks } from "../../../apps/web/lib/notebook-api.js";
import { NotebookHome } from "../../../packages/ui/src/notebook-home.jsx";

document.documentElement.dataset.theme = new URLSearchParams(window.location.search).get("theme") === "light" ? "light" : "dark";

function Harness() {
  const [notebooks, setNotebooks] = useState([]);
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState("기존 Notebook을 선택하면 보존된 Context로 재진입합니다.");
  useEffect(() => { listNotebooks("workspace-notebook-evidence").then(({ data }) => { setNotebooks(data); setState("ready"); }, () => setState("error")); }, []);
  const create = async (input) => {
    const { data: item } = await createNotebook("workspace-notebook-evidence", input, { idempotencyKey: "notebook-evidence-create-0001" });
    setNotebooks((value) => [item, ...value]);
    return item;
  };
  return <><div id="harness-notice" role="status">{notice}</div><NotebookHome state={state} notebooks={notebooks} onCreate={create} onOpenNotebook={({ notebookId, mode }) => setNotice(`${mode}:${notebookId}`)} onOpenSetting={(id) => setNotice(`setting:${id}`)} /></>;
}

createRoot(document.getElementById("root")).render(<StrictMode><Harness /></StrictMode>);
