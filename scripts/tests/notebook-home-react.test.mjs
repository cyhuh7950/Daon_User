import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

async function bundle(output) {
  const root = path.resolve(import.meta.dirname, "../..");
  const { build } = await import("vite");
  await build({
    configFile: false,
    logLevel: "silent",
    root,
    build: {
      outDir: output,
      emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/notebook-home.jsx"), formats: ["es"], fileName: "notebook-home-react" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
    },
  });
  const built = (await readdir(output)).find((name) => name.startsWith("notebook-home-react") && /\.m?js$/u.test(name));
  return import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
}

function reactProps(element) {
  const key = Object.keys(element).find((item) => item.startsWith("__reactProps"));
  return element[key];
}

test("새 Notebook dialog는 initial focus·Tab trap·Escape·inert·opener focus return을 실제 적용한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".notebook-home-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { NotebookHome } = await bundle(output);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(NotebookHome, { notebooks: [] })); });
    const opener = buttonByText(container, "＋ 새 Notebook");
    opener.focus();
    await act(async () => { opener.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const dialog = findElements(container, (node) => node.getAttribute?.("role") === "dialog")[0];
    const title = findElements(dialog, (node) => node.tagName === "INPUT")[0];
    assert.equal(dom.document.activeElement, title);
    assert.ok(findElements(container, (node) => node.getAttribute?.("inert") !== null && !node.contains(dialog)).length >= 1);
    const focusables = findElements(dialog, (node) => ["BUTTON", "INPUT", "TEXTAREA"].includes(node.tagName) && !node.disabled);
    dialog.querySelectorAll = () => focusables;
    focusables.at(-1).focus();
    const tab = Object.assign(new MinimalEvent("keydown"), { key: "Tab", shiftKey: false });
    await act(async () => { dialog.dispatchEvent(tab); });
    assert.equal(tab.defaultPrevented, true);
    assert.equal(dom.document.activeElement, focusables[0]);
    focusables[0].focus();
    const shiftTab = Object.assign(new MinimalEvent("keydown"), { key: "Tab", shiftKey: true });
    await act(async () => { dialog.dispatchEvent(shiftTab); });
    assert.equal(shiftTab.defaultPrevented, true);
    assert.equal(dom.document.activeElement, focusables.at(-1));
    const escape = Object.assign(new MinimalEvent("keydown"), { key: "Escape", shiftKey: false });
    await act(async () => { dialog.dispatchEvent(escape); await Promise.resolve(); });
    assert.equal(findElements(container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
    assert.equal(dom.document.activeElement, opener);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});

test("새 Notebook create 실패는 dialog·안전 오류·retry를 유지하고 동시 submit은 1회만 전송한다", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(root, ".notebook-home-retry-react-"));
  const dom = installMinimalDom();
  let reactRoot;
  try {
    const { createElement, act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { NotebookHome } = await bundle(output);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    let calls = 0;
    let release;
    const first = new Promise((_, reject) => { release = () => reject(new Error("database detail must not reflect")); });
    const onCreate = async () => { calls += 1; if (calls === 1) return first; return { notebook_id: "notebook-created" }; };
    await act(async () => { reactRoot.render(createElement(NotebookHome, { notebooks: [], onCreate })); });
    await act(async () => { buttonByText(container, "＋ 새 Notebook").dispatchEvent(new MinimalEvent("click")); });
    let dialog = findElements(container, (node) => node.getAttribute?.("role") === "dialog")[0];
    const title = findElements(dialog, (node) => node.tagName === "INPUT")[0];
    await act(async () => { reactProps(title).onChange({ target: { value: "재시도 Notebook" } }); });
    const form = findElements(dialog, (node) => node.tagName === "FORM")[0];
    await act(async () => {
      form.dispatchEvent(new MinimalEvent("submit"));
      form.dispatchEvent(new MinimalEvent("submit"));
      await Promise.resolve();
    });
    assert.equal(calls, 1);
    await act(async () => { release(); try { await first; } catch {} await Promise.resolve(); });
    dialog = findElements(container, (node) => node.getAttribute?.("role") === "dialog")[0];
    assert.ok(dialog);
    assert.match(dialog.textContent, /NOTEBOOK_CREATE_FAILED/u);
    assert.doesNotMatch(dialog.textContent, /database detail/u);
    await act(async () => { findElements(dialog, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    assert.equal(calls, 2);
    assert.equal(findElements(container, (node) => node.getAttribute?.("role") === "dialog").length, 0);
  } finally {
    if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount()));
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
});
