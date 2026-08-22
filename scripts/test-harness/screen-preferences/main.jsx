import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { ScreenPreferencesPane } from "../../../apps/web/components/screen-preferences-pane.jsx";
import { ScreenThemeRuntime } from "../../../apps/web/components/screen-theme-runtime.jsx";
import * as screenPreferenceApi from "../../../apps/web/lib/screen-preference-api.js";

const testNotebook = Object.freeze({
  notebook: [{ id: "test-notebook-1", title: "Test Notebook" }],
  sources: [{ id: "test-source-1", name: "fixture.md" }],
  conversations: [{ id: "test-conversation-1", text: "fixture" }],
  outputs: [{ id: "test-output-1", kind: "report" }],
});
const fixtureHash = JSON.stringify(testNotebook);
let preference = "system";
const mediaListeners = new Set();
const testMedia = {
  matches: true,
  addEventListener(type, listener) { if (type === "change") mediaListeners.add(listener); },
  removeEventListener(type, listener) { if (type === "change") mediaListeners.delete(listener); },
};
globalThis.matchMedia = () => testMedia;

function setTestOsDark(matches) {
  testMedia.matches = matches;
  for (const listener of mediaListeners) listener({ matches });
}

globalThis.fetch = async (path, options = {}) => {
  if (path !== "/bff/api/preferences/screen") return new Response(null, { status: 404 });
  if (options.method === "GET") return Response.json({ data: { theme: preference }, meta: {} });
  if (options.method === "PUT") {
    const body = JSON.parse(options.body ?? "{}");
    preference = body.theme;
    return Response.json({ data: { theme: preference }, meta: {} });
  }
  return new Response(null, { status: 405 });
};

function Harness() {
  const [hash, setHash] = useState(fixtureHash);
  const result = useMemo(() => hash === fixtureHash ? "fixture-hash-unchanged" : "fixture-hash-changed", [hash]);
  return <>
    <ScreenThemeRuntime><ScreenPreferencesPane /></ScreenThemeRuntime>
    <aside aria-label="Test Notebook preference context">
      <p>Test Notebook preference context</p><output data-testid="fixture-hash">{result}</output>
      <button type="button" onClick={() => setHash(JSON.stringify(testNotebook))}>fixture hash 확인</button>
      <button type="button" onClick={() => setTestOsDark(false)}>운영체제 밝게</button>
      <button type="button" onClick={() => setTestOsDark(true)}>운영체제 어둡게</button>
    </aside>
  </>;
}

void screenPreferenceApi;
createRoot(document.getElementById("root")).render(<Harness />);
