import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { createOfflineStudioState } from "../../../apps/desktop/src/offline-studio-model.js";
import { WorkspaceSettingsModal } from "../../../apps/desktop/src/workspace-settings-modal.jsx";
import "../../../packages/design-tokens/tokens.css";
import "../../../apps/desktop/src/desktop-shell.css";
import "../../../apps/desktop/src/workspace-visual-tokens.css";

const notebookFixture = Object.freeze({
  notebookId: "test-notebook-screen-preference",
  title: "Test Notebook",
  sources: Object.freeze(["검증된 Knowledge Snapshot", "Daon 생성 지식"]),
  outputs: Object.freeze(["근거 기반 보고서"]),
});
const fixtureHash = "screen-preference-fixture-v1-0d4cc4f4";

function EvidenceHarness() {
  const [open, setOpen] = useState(true);
  const [theme, setTheme] = useState("system");
  const [systemDark, setSystemDark] = useState(false);
  const [pixelRatio, setPixelRatio] = useState(window.devicePixelRatio);
  const [visualScale, setVisualScale] = useState(1);
  const offlineState = useMemo(() => createOfflineStudioState(), []);
  const nativeInvoke = window.__TAURI_INTERNALS__?.invoke;

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => {
      setSystemDark(media.matches);
      setPixelRatio(window.devicePixelRatio);
    };
    update();
    media.addEventListener("change", update);
    window.addEventListener("resize", update);
    return () => {
      media.removeEventListener("change", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  useEffect(() => {
    const onEvidenceScale = (event) => {
      if (!event.ctrlKey) return;
      if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        setVisualScale(2);
      } else if (event.key === "0") {
        event.preventDefault();
        setVisualScale(1);
      }
    };
    window.addEventListener("keydown", onEvidenceScale);
    return () => window.removeEventListener("keydown", onEvidenceScale);
  }, []);

  const effectiveTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;
  return (
    <div className="desktop-shell" data-theme={effectiveTheme} data-selected-theme={theme} data-fixture-hash={fixtureHash} style={{ zoom: visualScale }}>
      <header className="desktop-titlebar">
        <div><p className="desktop-eyebrow">Windows App · 실제 WebView 증거</p><h1>{notebookFixture.title}</h1></div>
        <p role="status">선택 {theme} · 적용 {effectiveTheme} · DPR {pixelRatio.toFixed(2)} · 배율 {Math.round(visualScale * 100)}%</p>
        <button type="button" onClick={() => setOpen(true)}>설정</button>
      </header>
      <main className="desktop-content" inert={open ? "" : undefined} aria-hidden={open ? "true" : undefined}>
        <section className="desktop-safe-surface" aria-label="Test Notebook 고정 자료">
          <h2>Notebook 작업 화면</h2>
          <p>화면 설정 초기화는 Notebook 자료를 변경하지 않습니다.</p>
          <dl><div><dt>Fixture hash</dt><dd>{fixtureHash}</dd></div><div><dt>Source</dt><dd>{notebookFixture.sources.length}</dd></div><div><dt>Output</dt><dd>{notebookFixture.outputs.length}</dd></div></dl>
        </section>
      </main>
      <WorkspaceSettingsModal
        open={open}
        onClose={() => setOpen(false)}
        offlineState={offlineState}
        nativeInvoke={nativeInvoke}
        onScreenTheme={setTheme}
        onSave={() => {}}
      />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<StrictMode><EvidenceHarness /></StrictMode>);
