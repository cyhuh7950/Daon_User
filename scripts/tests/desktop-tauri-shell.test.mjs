import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { access, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

class MinimalEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = options.bubbles !== false;
    this.cancelable = options.cancelable !== false;
    this.defaultPrevented = false;
    this.cancelBubble = false;
    this.target = null;
    this.currentTarget = null;
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this.cancelBubble = true; }
}

class MinimalNode {
  constructor(nodeType, nodeName, ownerDocument = null) {
    this.nodeType = nodeType;
    this.nodeName = nodeName;
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.childNodes = [];
    this.listeners = new Map();
  }
  appendChild(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  insertBefore(child, before) {
    child.parentNode = this;
    const index = this.childNodes.indexOf(before);
    if (index < 0) this.childNodes.push(child); else this.childNodes.splice(index, 0, child);
    return child;
  }
  removeChild(child) { const index = this.childNodes.indexOf(child); if (index >= 0) this.childNodes.splice(index, 1); child.parentNode = null; return child; }
  get firstChild() { return this.childNodes[0] ?? null; }
  get lastChild() { return this.childNodes.at(-1) ?? null; }
  get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map((child) => child.textContent).join(""); }
  set textContent(value) {
    if (this.nodeType === 3) { this.nodeValue = String(value); return; }
    this.childNodes = [];
    if (value !== "") this.appendChild(this.ownerDocument.createTextNode(String(value)));
  }
  addEventListener(type, listener) { const listeners = this.listeners.get(type) ?? []; listeners.push(listener); this.listeners.set(type, listeners); }
  removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) ?? []).filter((item) => item !== listener)); }
  dispatchEvent(event) {
    if (event.type === "click" && this.disabled) return true;
    if (!event.target) event.target = this;
    event.currentTarget = this;
    for (const listener of this.listeners.get(event.type) ?? []) listener.call(this, event);
    if (event.bubbles && !event.cancelBubble && this.parentNode) this.parentNode.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  contains(candidate) { return candidate === this || this.childNodes.some((child) => child.contains?.(candidate)); }
  getRootNode() { let node = this; while (node.parentNode) node = node.parentNode; return node; }
}

class MinimalElement extends MinimalNode {
  constructor(tagName, ownerDocument) {
    super(1, tagName.toUpperCase(), ownerDocument);
    this.tagName = this.nodeName;
    this.namespaceURI = "http://www.w3.org/1999/xhtml";
    this.attributes = new Map();
    this.style = {};
    this.value = "";
    this.disabled = false;
    this.hidden = false;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); if (name === "class") this.className = String(value); if (name === "value") this.value = String(value); if (name === "disabled") this.disabled = true; if (name === "hidden") this.hidden = true; }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); if (name === "disabled") this.disabled = false; if (name === "hidden") this.hidden = false; }
  hasAttribute(name) { return this.attributes.has(name); }
  get options() { return this.tagName === "SELECT" ? this.childNodes.filter((child) => child.tagName === "OPTION") : undefined; }
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
}

class MinimalText extends MinimalNode {
  constructor(value, ownerDocument) { super(3, "#text", ownerDocument); this.nodeValue = String(value); }
}

function installMinimalDom() {
  const document = new MinimalNode(9, "#document", null);
  document.ownerDocument = document;
  document.createElement = (tagName) => new MinimalElement(tagName, document);
  document.createElementNS = (_namespace, tagName) => new MinimalElement(tagName, document);
  document.createTextNode = (value) => new MinimalText(value, document);
  document.createComment = (value) => { const node = new MinimalNode(8, "#comment", document); node.nodeValue = value; return node; };
  document.getElementById = (id) => findElements(document, (node) => node.getAttribute("id") === id)[0] ?? null;
  document.documentElement = document.createElement("html");
  document.body = document.createElement("body");
  document.documentElement.appendChild(document.body);
  document.appendChild(document.documentElement);
  document.activeElement = document.body;
  const createStorage = () => {
    const values = new Map();
    return { getItem: (key) => values.get(String(key)) ?? null, setItem: (key, value) => values.set(String(key), String(value)), removeItem: (key) => values.delete(String(key)), clear: () => values.clear() };
  };
  const window = {
    document,
    innerWidth: 1920,
    location: { pathname: "/" },
    history: {
      pushState: (_state, _title, target) => {
        if (String(target).startsWith("#")) window.location.hash = String(target);
        else window.location.pathname = String(target);
      },
      replaceState: (_state, _title, target) => {
        if (String(target).startsWith("#")) window.location.hash = String(target);
        else window.location.pathname = String(target);
      },
    },
    localStorage: createStorage(),
    sessionStorage: createStorage(),
    addEventListener: (...args) => document.addEventListener(...args),
    removeEventListener: (...args) => document.removeEventListener(...args),
    dispatchEvent: (...args) => document.dispatchEvent(...args),
    Event: MinimalEvent,
    MouseEvent: MinimalEvent,
    Node: MinimalNode,
    Element: MinimalElement,
    HTMLElement: MinimalElement,
    HTMLIFrameElement: class extends MinimalElement {}
  };
  document.defaultView = window;
  const prior = Object.fromEntries(["window", "document", "Node", "Element", "HTMLElement", "HTMLIFrameElement", "Event", "MouseEvent", "IS_REACT_ACT_ENVIRONMENT"].map((key) => [key, globalThis[key]]));
  Object.assign(globalThis, { window, document, Node: MinimalNode, Element: MinimalElement, HTMLElement: MinimalElement, HTMLIFrameElement: window.HTMLIFrameElement, Event: MinimalEvent, MouseEvent: MinimalEvent, IS_REACT_ACT_ENVIRONMENT: true });
  return { document, window, restore: () => { for (const [key, value] of Object.entries(prior)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } } };
}

function findElements(root, predicate, matches = []) {
  if (root?.nodeType === 1 && predicate(root)) matches.push(root);
  for (const child of root?.childNodes ?? []) findElements(child, predicate, matches);
  return matches;
}

const buttonByText = (root, label) => findElements(root, (node) => node.tagName === "BUTTON" && node.textContent.trim() === label)[0];

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");
const readBinary = (path) => readFile(new URL(`../../${path}`, import.meta.url));

test("Tauri registers the exact command-bound offline Studio bridge", async () => {
  const lib = await read("apps/desktop/src-tauri/src/lib.rs");
  const bridge = await read("apps/desktop/src-tauri/src/offline_studio_bridge.rs");
  for (const command of [
    "offline_studio_list_models", "offline_studio_list_raw_sources",
    "offline_studio_import_raw_source", "offline_studio_prepare_context",
    "offline_studio_confirm_settings", "offline_studio_generate_draft",
    "offline_studio_get_draft", "offline_studio_append_edit", "offline_studio_queue_sync"
  ]) {
    assert.match(lib, new RegExp(command));
    assert.match(bridge, new RegExp(command));
  }
  assert.match(bridge, /execute_workspace_studio_request/);
  assert.match(bridge, /serde\(deny_unknown_fields\)/);
  assert.doesNotMatch(bridge, /NEXT_PUBLIC_|reqwest|TcpStream|WebSocket/i);
});

test("desktop shell directly consumes shared UI, tokens, and contracts", async () => {
  const source = await read("apps/desktop/src/desktop-shell.jsx");
  assert.match(source, /@daon-user\/ui/);
  assert.match(source, /@daon-user\/contracts\/navigation\.json/);
  assert.match(source, /@daon-user\/design-tokens\/tokens\.css/);
  assert.doesNotMatch(source, /apps\/web|next\/|NEXT_PUBLIC_/);
});

test("desktop shell은 Native Session과 Product Workspace를 결합하고 Prototype 제품 화면을 import하지 않는다", async () => {
  const source = await read("apps/desktop/src/desktop-shell.jsx");
  const authPanel = await read("apps/desktop/src/native-auth-panel.jsx");
  assert.match(source, /createNativeSessionBridge/);
  assert.match(source, /useMemo\(\(\) => createNativeSessionBridge/);
  assert.match(source, /ProductWorkspaceShell/);
  assert.match(source, /NativeAuthPanel/);
  assert.match(source, /recoveryAuthorizationStatus/);
  assert.match(source, /recoveryOperations:/);
  assert.match(source, /authorizationRevision: request \* 2/);
  assert.match(source, /authorizationRevision: request \* 2 \+ 1/);
  assert.match(source, /nativeSession\.sessionId/);
  assert.match(authPanel, /type="password"/);
  assert.match(authPanel, /defaultValue=""/);
  assert.doesNotMatch(authPanel, /setPassword|useState\([^)]*password|localStorage|sessionStorage|console\./i);
  assert.doesNotMatch(source, /ProductionBoundEvidenceHub|AdaptiveWorkspace|AccountSecurityWorkspace|OperationsRecoveryWorkspace/);
  assert.doesNotMatch(source, /fetch\s*\(|XMLHttpRequest|WebSocket|https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_/i);
});

test("실제 Offline Studio DOM action은 context부터 queue까지 exact 순서로 실행한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".r1-offline-studio-react-"));
  const dom = installMinimalDom();
  let root;
  try {
    const { act, createElement, Fragment, useReducer } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false, logLevel: "silent", root: repositoryRoot,
      build: {
        outDir: bundleRoot, emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/desktop/src/offline-studio-pane.jsx"), formats: ["es"], fileName: "offline-studio-pane" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const entry = (await readdir(bundleRoot)).find((name) => name.startsWith("offline-studio-pane") && /\.(?:m?js)$/u.test(name));
    assert.ok(entry);
    const { OfflineStudioPane } = await import(`${pathToFileURL(path.join(bundleRoot, entry)).href}?flow=${Date.now()}`);
    const { createOfflineStudioState, reduceOfflineStudioState } = await import("../../apps/desktop/src/offline-studio-model.js");
    const calls = [];
    const studioAdapter = {
      listModels: async (workspaceId) => { calls.push(["listModels", workspaceId]); return [{ deployment_id: "deployment-1", provider_code: "OLLAMA", provider_kind: "server_internal", readiness: "ready", label: "qwen" }]; },
      listRawSources: async (workspaceId) => { calls.push(["listRawSources", workspaceId]); return [{ source_version_id: "source-v1", filename: "source.txt", digest_sha256: "a".repeat(64), quality_state: "unverified" }]; },
      importRawSource: async (request) => { calls.push(["importRawSource", structuredClone(request)]); return { source_version_id: "source-v1" }; },
      prepareContext: async (request) => { calls.push(["prepareContext", request]); return { mode: "raw_only", snapshot_id: "scope-1", items: [{ origin: "raw_source", item_id: "source-v1", version_id: "source-v1" }], warnings: ["RAW_SOURCE_ONLY"] }; },
      confirmSettings: async (request) => { calls.push(["confirmSettings", request]); return { request_id: "request-1", settings_snapshot_id: "settings-1" }; },
      generateDraft: async (request) => { calls.push(["generateDraft", request]); return { draft_id: "draft-1", output_version_id: "version-1", title: "실제 초안", sections: [{ title: "본문", body: "근거", unverified: true }] }; },
      appendEdit: async (request) => { calls.push(["appendEdit", request]); return { draft_id: "draft-1", output_version_id: "version-2", title: "실제 초안", sections: request.sections }; },
      queueSync: async (request) => { calls.push(["queueSync", request]); return { approval_state: "awaiting_approval", operation_id: "sync-1" }; },
    };
    const syncAdapter = { listKnowledge: async () => [] };
    const sources = [{ ready: true, sourceVersionId: "source-v1" }];
    function Harness() {
      const [state, dispatch] = useReducer(reduceOfflineStudioState, undefined, () => createOfflineStudioState({
        context: { mode: "raw_only", snapshotId: null, items: [{ origin: "raw_source", item_id: "source-v1", version_id: "source-v1" }], warnings: [] },
        rawSources: [{ source_version_id: "source-v1", filename: "source.txt", digest_sha256: "a".repeat(64), quality_state: "unverified" }],
        selectedRawSourceVersionIds: ["source-v1"],
        selectedModelDeploymentId: "deployment-1",
      }));
      const props = { state, dispatch, studioAdapter, syncAdapter, workspaceId: "workspace-1", sources };
      return createElement(Fragment, null,
        createElement(OfflineStudioPane, { ...props, surface: "studio" }),
        createElement(OfflineStudioPane, { ...props, surface: "editor" }),
      );
    }
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    const flush = async () => { await new Promise((resolve) => setTimeout(resolve, 0)); };
    await act(async () => { root.render(createElement(Harness)); });
    const rawFileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    assert.ok(rawFileInput, "실제 로컬 Raw Source file input이 필요하다");
    rawFileInput.files = [{
      name: "source.txt",
      type: "text/plain",
      arrayBuffer: async () => new TextEncoder().encode("local evidence").buffer,
    }];
    const rawFilePropsKey = Object.keys(rawFileInput).find((key) => key.startsWith("__reactProps$"));
    assert.ok(rawFilePropsKey);
    await act(async () => { await rawFileInput[rawFilePropsKey].onChange({ currentTarget: rawFileInput, target: rawFileInput }); await flush(); });
    const settingsForm = findElements(container, (node) => node.tagName === "FORM" && node.getAttribute("aria-label") === "Offline Studio 설정")[0];
    await act(async () => { settingsForm.dispatchEvent(new MinimalEvent("submit")); await flush(); });
    await act(async () => { buttonByText(container, "초안 생성").dispatchEvent(new MinimalEvent("click")); await flush(); });
    assert.match(container.textContent, /실제 초안/u, JSON.stringify(calls));
    const sectionTitle = findElements(container, (node) => (node.getAttribute("name") ?? node.name) === "section-title-0")[0];
    const sectionBody = findElements(container, (node) => (node.getAttribute("name") ?? node.name) === "section-body-0")[0];
    assert.ok(sectionTitle, "실제 초안 제목 편집 input이 필요하다");
    assert.ok(sectionBody, "실제 초안 본문 편집 textarea가 필요하다");
    sectionTitle.value = "수정 제목";
    sectionBody.value = "사용자가 수정한 본문";
    const titlePropsKey = Object.keys(sectionTitle).find((key) => key.startsWith("__reactProps$"));
    const bodyPropsKey = Object.keys(sectionBody).find((key) => key.startsWith("__reactProps$"));
    assert.ok(titlePropsKey);
    assert.ok(bodyPropsKey);
    await act(async () => {
      sectionTitle[titlePropsKey].onChange({ currentTarget: sectionTitle, target: sectionTitle });
      sectionBody[bodyPropsKey].onChange({ currentTarget: sectionBody, target: sectionBody });
    });
    await act(async () => { buttonByText(container, "새 Version 저장").dispatchEvent(new MinimalEvent("click")); await flush(); });
    await act(async () => { buttonByText(container, "Sync 대기열").dispatchEvent(new MinimalEvent("click")); await flush(); });
    assert.deepEqual(calls.map(([name]) => name), ["listModels", "listRawSources", "importRawSource", "listRawSources", "prepareContext", "confirmSettings", "generateDraft", "appendEdit", "queueSync"]);
    assert.equal(calls[2][1].workspace_id, "workspace-1");
    assert.equal(calls[2][1].filename, "source.txt");
    assert.equal(new TextDecoder().decode(calls[2][1].bytes), "local evidence");
    assert.equal(calls[4][1].workspace_id, "workspace-1");
    assert.equal(calls[5][1].context_snapshot_id, "scope-1");
    assert.equal(calls[7][1].previous_version_id, "version-1");
    assert.deepEqual(calls[7][1].sections, [{ title: "수정 제목", body: "사용자가 수정한 본문", unverified: true }]);
    assert.equal(calls[8][1].output_version_id, "version-2");
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("실제 React Tree는 Login 실패·성공·권한 없음·Logout 경쟁을 fail-close한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".c10-r02-react-"));
  const dom = installMinimalDom();
  let root;
  let reactAct;
  const priorNodeEnv = process.env.NODE_ENV;
  try {
    const { act, createElement, StrictMode } = await import("react");
    const { createRoot } = await import("react-dom/client");
    reactAct = act;
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/desktop/src/desktop-shell.jsx"), formats: ["es"], fileName: "desktop-shell" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("desktop-shell") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Vite actual JSX bundle entry가 필요하다");
    const { DesktopShell } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?r02=${Date.now()}`);
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    const operations = ["cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel", "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"];
    const session = { user_id: "user-1", tenant_id: "tenant-1", workspace_id: "workspace-1", session_id: "session-1", device_id: "device-1", expires_at: "2026-08-11T01:00:00Z" };
    const calls = [];
    let scheduledPoll = null;
    let polledSession = { authenticated: false, session: null };
    let resolveSessionTwoSources;
    const sessionTwo = { ...session, user_id: "user-2", workspace_id: "workspace-2", session_id: "session-2" };
    const invoke = async (command, args) => {
      calls.push(command);
      if (command === "native_session_status") return polledSession;
      if (command === "native_login") {
        polledSession = { authenticated: true, session };
        return polledSession;
      }
      if (command === "native_recovery_authorization_status") return { recovery_operations: operations };
      if (command === "notebook_create") return { notebook_id: "notebook-empty", title: args.input.title, source_count: 0, output_count: 0, updated_at: "2026-08-11T01:00:00Z", status: "empty" };
      if (command === "notebook_get") return { notebook_id: args.input.notebook_id, title: args.input.notebook_id === "notebook-empty" ? "새 Notebook" : `Notebook ${args.input.workspace_id}`, source_count: args.input.notebook_id === "notebook-empty" ? 0 : 1, output_count: 0, updated_at: "2026-08-11T01:00:00Z", status: args.input.notebook_id === "notebook-empty" ? "empty" : "active" };
      if (command === "notebook_context") return {
        notebook_id: args.input.notebook_id,
        sources: args.input.notebook_id === "notebook-empty" ? [] : [{ source_id: `source-${args.input.workspace_id}`, source_version_id: `version-${args.input.workspace_id}` }],
        knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [], conversation: null,
      };
      if (command === "notebook_list") return [{ notebook_id: "notebook-1", title: "Notebook", source_count: 1, output_count: 0, updated_at: "2026-08-11T01:00:00Z", status: "active" }];
      if (command === "workspace_list_sources" && args.input.workspace_id === "workspace-2") {
        return new Promise((resolve) => { resolveSessionTwoSources = resolve; });
      }
      if (command === "workspace_list_sources") return [{
        source_id: `source-${args.input.workspace_id}`,
        source_version_id: `version-${args.input.workspace_id}`,
        filename: `${args.input.workspace_id}.pdf`,
        source_state: "ready",
        processing_state: "completed",
        job_state: "completed"
      }];
      if (command === "workspace_list_studio_outputs") return [];
      if (command === "workspace_ask_question") return {
        run_id: "run-1", run_result_id: "result-1", answer: "StrictMode 질문 성공", insufficient: false,
        citations: [{ citation_id: "citation-1", source_id: "source-workspace-1", source_version_id: "version-workspace-1", evidence_span_id: "span-1", page: 1, origin: "raw_source", context_item_id: "source-workspace-1", locator: { kind: "page", value: "1" } }]
      };
      if (command === "recovery_cloud_list_backups") return { data: [], etag: null };
      if (command === "local_service_status") return { state: "ready", retryable: false, error_code: null };
      throw new Error(`unexpected:${command}`);
    };
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    window.location.hash = "#/notebooks";
    await act(async () => {
      root.render(createElement(StrictMode, null, createElement(DesktopShell, { nativeInvoke: invoke, sessionWatchOptions: { schedule: (poll) => { scheduledPoll = poll; return 1; }, cancel: () => {} } })));
    });
    assert.ok(scheduledPoll, "제품 Tree가 주입된 watch scheduler를 사용해야 한다");
    assert.equal(findElements(container, (node) => node.getAttribute("aria-label") === "Windows 주 탐색").length, 0);
    assert.equal(buttonByText(container, "Workspace"), undefined);
    assert.doesNotMatch(container.textContent, /Evidence Hub|Workspace 준비 상태/);
    const loginId = findElements(container, (node) => (node.getAttribute("name") ?? node.name) === "login-id")[0];
    const password = findElements(container, (node) => (node.getAttribute("name") ?? node.name) === "password")[0];
    assert.ok(loginId, "실제 Login ID DOM input이 필요하다");
    assert.ok(password, "실제 Password DOM input이 필요하다");
    loginId.value = "user-1";
    password.value = "password-value";
    const form = findElements(container, (node) => node.tagName === "FORM")[0];
    await act(async () => { form.dispatchEvent(new MinimalEvent("submit")); });
    assert.equal(password.value, "");
    assert.ok(calls.includes("native_login"), `native_login 호출 필요: ${calls.join(",")}`);
    assert.match(container.textContent, /지식에서 결과까지/u, "로그인 성공은 Notebook Home이어야 한다");
    assert.equal(calls.includes("workspace_list_sources"), false, "사용자가 Notebook을 고르기 전에는 3열 조회가 없어야 한다");
    const createOpener = findElements(container, (node) => node.tagName === "BUTTON" && node.textContent.includes("새 Notebook"))[0];
    await act(async () => { createOpener.dispatchEvent(new MinimalEvent("click")); });
    const createDialog = findElements(container, (node) => node.getAttribute("role") === "dialog")[0];
    const createTitle = findElements(createDialog, (node) => node.tagName === "INPUT")[0];
    createTitle.value = "새 Notebook";
    const createTitleProps = Object.keys(createTitle).find((key) => key.startsWith("__reactProps$"));
    await act(async () => { createTitle[createTitleProps].onChange({ currentTarget: createTitle, target: createTitle }); });
    const createForm = findElements(createDialog, (node) => node.tagName === "FORM")[0] ?? createDialog;
    const createFormProps = Object.keys(createForm).find((key) => key.startsWith("__reactProps$"));
    await act(async () => { await createForm[createFormProps].onSubmit({ preventDefault() {} }); await Promise.resolve(); });
    assert.ok(calls.includes("notebook_create"), "새 Notebook은 Native create를 호출해야 한다");
    assert.match(container.textContent, /Raw Source1/u, "새 Notebook은 현재 Workspace Source 목록을 표시해야 한다");
    await act(async () => { buttonByText(container, "← Notebook 홈").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const notebookCard = findElements(container, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label")?.endsWith("Notebook 열기"))[0];
    assert.ok(notebookCard, "서버 목록의 Notebook 카드가 필요하다");
    await act(async () => { notebookCard.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.match(container.textContent, /인증됨 · user-1 · workspace-1/);
    assert.equal(buttonByText(container, "Workspace")?.getAttribute("aria-current"), "page");
    assert.doesNotMatch(container.textContent, /Evidence Hub/);
    const shell = findElements(container, (node) => node.getAttribute("data-client-type") === "windows")[0];
    assert.equal(shell.getAttribute("data-session-tree-key"), "session-1:5");
    assert.equal(calls.filter((command) => command === "recovery_cloud_list_backups").length, 0);
    assert.match(container.textContent, /workspace-1\.pdf/u);
    const strictQuestionForm = findElements(container, (node) => node.tagName === "FORM"
      && (node.getAttribute("class") ?? "").split(/\s+/u).includes("conversation-composer"))[0];
    assert.ok(strictQuestionForm, "실제 대화 composer가 필요하다");
    const strictQuestion = findElements(strictQuestionForm, (node) => node.tagName === "TEXTAREA")[0];
    strictQuestion.value = "근거는?";
    const strictQuestionProps = Object.keys(strictQuestion).find((key) => key.startsWith("__reactProps$"));
    assert.equal(strictQuestion.disabled, false);
    await act(async () => {
      strictQuestion[strictQuestionProps].onChange({ currentTarget: strictQuestion, target: strictQuestion });
    });
    const strictSubmit = findElements(strictQuestionForm, (node) => node.tagName === "BUTTON" && node.getAttribute("type") === "submit")[0];
    assert.equal(strictSubmit.disabled, false, "질문 state가 실제 composer에 반영되어야 한다");
    await act(async () => { strictQuestionForm.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    assert.ok(calls.includes("workspace_ask_question"), `StrictMode 질문 Command 호출 필요: ${calls.join(",")} / ${container.textContent}`);
    assert.match(container.textContent, /StrictMode 질문 성공/u, "StrictMode effect 재실행 뒤에도 Session lifetime signal이 살아 있어야 한다");
    polledSession = { authenticated: true, session: sessionTwo };
    await act(async () => { await scheduledPoll(); await Promise.resolve(); });
    assert.equal(shell.getAttribute("data-session-tree-key"), "session-2:7");
    assert.doesNotMatch(container.textContent, /workspace-1\.pdf/u, "이전 Session Source가 재노출되면 안 된다");
    assert.match(container.textContent, /지식에서 결과까지/u, "Workspace 전환은 이전 Notebook을 자동 선택하지 않아야 한다");
    const workspaceTwoCard = findElements(container, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label")?.endsWith("Notebook 열기"))[0];
    await act(async () => { workspaceTwoCard.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.ok(resolveSessionTwoSources, "새 Session Source 조회가 시작되어야 한다");
    await act(async () => { resolveSessionTwoSources([{
      source_id: "source-workspace-2", source_version_id: "version-workspace-2", filename: "workspace-2.pdf",
      source_state: "ready", processing_state: "completed", job_state: "completed"
    }]); });
    assert.match(container.textContent, /workspace-2\.pdf/u);

    await act(async () => { root.unmount(); });
    root = null;

    const rejectedCalls = [];
    const rejectedInvoke = async (command) => {
      rejectedCalls.push(command);
      if (command === "native_session_status") return { authenticated: false, session: null };
      if (command === "native_login") throw { code: "AUTHENTICATION_REQUIRED" };
      if (command === "local_service_status") return { state: "ready", retryable: false, error_code: null };
      throw new Error(`unexpected:${command}`);
    };
    const rejectedContainer = dom.document.createElement("div");
    dom.document.body.appendChild(rejectedContainer);
    root = createRoot(rejectedContainer);
    await act(async () => { root.render(createElement(DesktopShell, { nativeInvoke: rejectedInvoke, sessionWatchOptions: { schedule: () => 1, cancel: () => {} } })); });
    const rejectedLoginId = findElements(rejectedContainer, (node) => (node.getAttribute("name") ?? node.name) === "login-id")[0];
    const rejectedPassword = findElements(rejectedContainer, (node) => (node.getAttribute("name") ?? node.name) === "password")[0];
    rejectedLoginId.value = "user-1";
    rejectedPassword.value = "password-value";
    await act(async () => { findElements(rejectedContainer, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); });
    assert.equal(rejectedPassword.value, "");
    assert.match(rejectedContainer.textContent, /AUTHENTICATION_REQUIRED/);
    assert.match(findElements(rejectedContainer, (node) => node.getAttribute("data-client-type") === "windows")[0].getAttribute("data-session-tree-key"), /^unauthenticated:/u);
    assert.equal(rejectedCalls.filter((command) => command.startsWith("recovery_")).length, 0);
    await act(async () => { root.unmount(); });
    root = null;

    for (const authorizationMode of ["empty", "reject"]) {
      const deniedCalls = [];
      const deniedInvoke = async (command) => {
        deniedCalls.push(command);
        if (command === "native_session_status") return { authenticated: false, session: null };
        if (command === "native_login") return { authenticated: true, session };
        if (command === "native_recovery_authorization_status") {
          if (authorizationMode === "reject") throw { code: "AUTHENTICATION_REQUIRED" };
          return { recovery_operations: [] };
        }
        if (command === "local_service_status") return { state: "ready", retryable: false, error_code: null };
        throw new Error(`unexpected:${command}`);
      };
      const deniedContainer = dom.document.createElement("div");
      dom.document.body.appendChild(deniedContainer);
      root = createRoot(deniedContainer);
      await act(async () => { root.render(createElement(DesktopShell, { nativeInvoke: deniedInvoke, sessionWatchOptions: { schedule: () => 1, cancel: () => {} } })); });
      const deniedLoginId = findElements(deniedContainer, (node) => (node.getAttribute("name") ?? node.name) === "login-id")[0];
      const deniedPassword = findElements(deniedContainer, (node) => (node.getAttribute("name") ?? node.name) === "password")[0];
      deniedLoginId.value = "user-1";
      deniedPassword.value = "password-value";
      await act(async () => { findElements(deniedContainer, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); });
      assert.equal(buttonByText(deniedContainer, "Organization"), undefined, `${authorizationMode}: Organization 메뉴가 없어야 한다`);
      assert.equal(buttonByText(deniedContainer, "Operations"), undefined, `${authorizationMode}: Operations 메뉴가 없어야 한다`);
      assert.equal(deniedCalls.filter((command) => command.startsWith("recovery_cloud_")).length, 0);
      await act(async () => { root.unmount(); });
      root = null;
    }

    let resolveLateAuthorization;
    let resolveLogout;
    let logoutStarted = false;
    let authorizationCount = 0;
    let latePoll = null;
    const raceCalls = [];
    const raceInvoke = async (command, args) => {
      raceCalls.push(command);
      if (command === "native_session_status") return logoutStarted ? { authenticated: true, session } : { authenticated: false, session: null };
      if (command === "native_login") return { authenticated: true, session };
      if (command === "native_recovery_authorization_status") {
        authorizationCount += 1;
        if (authorizationCount === 1) return { recovery_operations: operations };
        return new Promise((resolve) => { resolveLateAuthorization = resolve; });
      }
      if (command === "notebook_get") return { notebook_id: args.input.notebook_id, title: "Notebook", source_count: 0, output_count: 0, updated_at: "2026-08-11T01:00:00Z", status: "empty" };
      if (command === "notebook_context") return { notebook_id: args.input.notebook_id, sources: [], knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [], conversation: null };
      if (command === "notebook_list") return [{ notebook_id: "notebook-1", title: "Notebook", source_count: 0, output_count: 0, updated_at: "2026-08-11T01:00:00Z", status: "empty" }];
      if (command === "workspace_list_sources") return [];
      if (command === "workspace_list_studio_outputs") return [];
      if (command === "native_logout") { logoutStarted = true; return new Promise((resolve) => { resolveLogout = resolve; }); }
      if (command === "recovery_cloud_list_backups") return { data: [], etag: null };
      if (command === "local_service_status") return { state: "ready", retryable: false, error_code: null };
      throw new Error(`unexpected:${command}`);
    };
    const raceContainer = dom.document.createElement("div");
    dom.document.body.appendChild(raceContainer);
    root = createRoot(raceContainer);
    window.location.hash = "#/notebooks/notebook-1";
    await act(async () => { root.render(createElement(DesktopShell, { nativeInvoke: raceInvoke, sessionWatchOptions: { schedule: (poll) => { latePoll = poll; return 1; }, cancel: () => {} } })); });
    const raceLoginId = findElements(raceContainer, (node) => (node.getAttribute("name") ?? node.name) === "login-id")[0];
    const racePassword = findElements(raceContainer, (node) => (node.getAttribute("name") ?? node.name) === "password")[0];
    raceLoginId.value = "user-1";
    racePassword.value = "password-value";
    await act(async () => { findElements(raceContainer, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); });
    const raceNotebook = findElements(raceContainer, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label")?.endsWith("Notebook 열기"))[0];
    await act(async () => { raceNotebook.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    const recoveryBeforeLogout = raceCalls.filter((command) => command.startsWith("recovery_")).length;
    await act(async () => { buttonByText(raceContainer, "Operations").dispatchEvent(new MinimalEvent("click")); });
    assert.ok(resolveLateAuthorization, "Operations 진입의 늦은 Authorization이 필요하다");
    assert.equal(buttonByText(raceContainer, "Operations")?.getAttribute("aria-current"), "page", "권한 재조회 중에도 Operations Route를 유지해야 한다");
    await act(async () => { buttonByText(raceContainer, "로그아웃").dispatchEvent(new MinimalEvent("click")); });
    assert.match(findElements(raceContainer, (node) => node.getAttribute("data-client-type") === "windows")[0].getAttribute("data-session-tree-key"), /^unauthenticated:/u);
    await act(async () => {
      resolveLateAuthorization({ recovery_operations: operations });
      await latePoll();
      resolveLogout({ authenticated: false, session: null });
    });
    assert.doesNotMatch(raceContainer.textContent, /인증됨 · user-1 · workspace-1/);
    assert.match(findElements(raceContainer, (node) => node.getAttribute("data-client-type") === "windows")[0].getAttribute("data-session-tree-key"), /^unauthenticated:/u);
    assert.equal(raceCalls.filter((command) => command.startsWith("recovery_")).length, recoveryBeforeLogout);
  } finally {
    if (root) {
      await reactAct(async () => { root.unmount(); });
    }
    dom.restore();
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Operations 직접 진입은 Safe unavailable만 렌더하고 Session·Recovery Network를 호출하지 않는다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-operations-react-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  let networkCalls = 0;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    globalThis.fetch = async () => {
      networkCalls += 1;
      return Response.json({ error: { code: "AUTHENTICATION_REQUIRED" } }, { status: 401 });
    };
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/app/operations/recovery-workspace.jsx"), formats: ["es"], fileName: "operations-safe" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("operations-safe") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Web Operations actual JSX bundle entry가 필요하다");
    const { RecoveryOperationsWorkspace } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?stageA=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(createElement(RecoveryOperationsWorkspace, { routeId: "operations", screenId: "operations" }));
      await Promise.resolve();
    });
    assert.match(container.textContent, /RESOURCE_UNAVAILABLE/);
    assert.match(container.textContent, /후속 Stage C/);
    assert.equal(networkCalls, 0, "Session·Backup·Restore Network는 0건이어야 한다");
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    globalThis.fetch = priorFetch;
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Product Workspace는 actual Adapter 호출을 보존하고 loading·unavailable·error를 실제 React로 표시한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-web-workspace-react-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "actual-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("actual-workspace") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Web actual Workspace JSX bundle entry가 필요하다");
    const workspaceModule = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?stageA=${Date.now()}`);
    assert.equal(typeof workspaceModule.createWebProductWorkspaceAdapter, "function");

    const calls = [];
    globalThis.fetch = async (url, init = {}) => {
      calls.push({ url, method: init.method ?? "GET", signal: init.signal, body: init.body });
      if ((init.method ?? "GET") === "GET" && String(url).includes("/sources?notebook_id=")) return Response.json({ data: { sources: [] }, meta: { trace_id: "trace-source-list-1", workspace_id: "workspace-1" } });
      if (String(url).endsWith("/sources")) return Response.json({ data: { source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32), digest_sha256: "b".repeat(64), byte_size: 128, status: "accepted", replayed: false, processing_run_id: "run-1", processing_state: "queued", job_state: "queued" }, meta: { trace_id: "trace-upload-1", workspace_id: "workspace-1" } }, { status: 202 });
      if (String(url).includes("/processing-runs/")) return Response.json({ data: { processing_run_id: "run-1", source_id: "source-1", source_version_id: "version-1", processing_state: "completed", source_state: "ready", job_state: "completed", safe_error_code: null }, meta: { trace_id: "trace-processing-1", workspace_id: "workspace-1" } });
      if (String(url).endsWith("/questions")) return Response.json({ data: { run_id: "answer-run-1", run_result_id: "answer-result-1", answer: "근거 답변", insufficient: false, citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" } }] }, meta: { trace_id: "trace-question-1", workspace_id: "workspace-1" } });
      throw new Error(`unexpected:${url}`);
    };
    const adapter = workspaceModule.createWebProductWorkspaceAdapter("workspace-1", "notebook-1");
    assert.deepEqual(await adapter.listSources(), []);
    const uploadController = new AbortController();
    const upload = await adapter.uploadPdf(
      { name: "actual.pdf", type: "application/pdf", size: 128 },
      { signal: uploadController.signal }
    );
    const processingController = new AbortController();
    const processing = await adapter.getProcessingStatus(upload.processing_run_id, { signal: processingController.signal });
    const answer = await adapter.askQuestion({ sourceId: upload.source_id, sourceVersionId: upload.source_version_id, question: "근거는?" });
    const citation = adapter.citationUrl(answer.citations[0]);
    assert.deepEqual(calls.map((call) => call.url), [
      "/bff/api/workspaces/workspace-1/sources?notebook_id=notebook-1",
      "/bff/api/workspaces/workspace-1/sources",
      "/bff/api/workspaces/workspace-1/processing-runs/run-1?notebook_id=notebook-1",
      "/bff/api/workspaces/workspace-1/questions"
    ]);
    assert.equal(JSON.parse(calls[3].body).notebook_id, "notebook-1");
    assert.equal(processing.job_state, "completed");
    assert.equal(calls[1].signal, uploadController.signal, "actual upload fetch가 operation AbortSignal을 전달해야 한다");
    assert.equal(calls[2].signal, processingController.signal, "actual status fetch가 polling AbortSignal을 전달해야 한다");
    assert.equal(citation, "/bff/api/workspaces/workspace-1/citations/citation-1/content?notebook_id=notebook-1#page=2");

    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(createElement(workspaceModule.ActualWorkspace, { workspaceId: "" })); });
    assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === "unavailable").length, 1);
    await act(async () => { root.unmount(); });
    root = createRoot(container);
    const failingAdapter = {
      listSources: async () => [],
      listKnowledgePackages: async () => [],
      listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
      uploadPdf: async () => { throw new Error("PDF_UPLOAD_FAILED"); },
      getProcessingStatus: async () => { throw new Error("PROCESSING_STATUS_FAILED"); },
      askQuestion: async () => { throw new Error("QUESTION_FAILED"); },
      citationUrl: () => ""
    };
    await act(async () => { root.render(createElement(workspaceModule.ActualWorkspace, { workspaceId: "workspace-1", adapter: failingAdapter })); });
    assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === "empty").length, 1);
    const fileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    assert.ok(fileInput, "실제 PDF 선택 input이 필요하다");
    fileInput.files = [{ name: "failure.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); });
    assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === "error").length, 1);
    assert.match(container.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    globalThis.fetch = priorFetch;
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Processing은 queued→leased→processing→completed를 polling한 뒤에만 질문·Citation에 도달한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-processing-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "processing-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("processing-workspace") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Processing actual JSX bundle entry가 필요하다");
    const { ActualWorkspace } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?processing=${Date.now()}`);
    const uploadResult = {
      source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32), digest_sha256: "b".repeat(64),
      byte_size: 128, status: "accepted", replayed: false, processing_run_id: "run-1", processing_state: "queued", job_state: "queued"
    };
    const terminalStatus = {
      processing_run_id: "run-1", source_id: "source-1", source_version_id: "version-1",
      source_state: "ready", processing_state: "completed", job_state: "completed", safe_error_code: null
    };
    const processingStatuses = [
      { ...terminalStatus, source_state: "registered", processing_state: "accepted", job_state: null },
      { ...terminalStatus, source_state: "processing", processing_state: "vision_llm_understanding", job_state: "leased" },
      { ...terminalStatus, source_state: "indexing", processing_state: "completed", job_state: "processing" },
      terminalStatus
    ];
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    let statusCalls = 0;
    let questionCalls = 0;
    const waits = [];
    const readyAdapter = {
      listSources: async () => [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
      listKnowledgePackages: async () => [],
      listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
      uploadPdf: async () => uploadResult,
      getProcessingStatus: async () => processingStatuses[statusCalls++],
      askQuestion: async () => {
        questionCalls += 1;
        return {
          run_id: "run-answer", run_result_id: "result-answer", answer: "근거 답변", insufficient: false,
          citations: [{ citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" } }]
        };
      },
      citationUrl: () => "/bff/api/workspaces/workspace-1/citations/citation-1/content#page=2"
    };
    const processingPollOptions = { maxAttempts: 4, intervalMs: 17, wait: async (intervalMs) => { waits.push(intervalMs); } };
    await act(async () => { root.render(createElement(ActualWorkspace, { workspaceId: "workspace-1", adapter: readyAdapter, processingPollOptions })); });
    const fileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    fileInput.files = [{ name: "ready.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
    assert.equal(statusCalls, 4);
    assert.deepEqual(waits, [17, 17, 17]);
    // Processing completion reloads the canonical Source list. This fixture
    // intentionally returns an empty list, so the shell settles on empty
    // rather than fabricating a ready Source from the upload response.
    assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === "ready").length, 1);
    const questionInput = findElements(container, (node) => node.tagName === "TEXTAREA")[0];
    assert.equal(questionInput.disabled, false);
    questionInput.value = "근거는?";
    const questionPropsKey = Object.keys(questionInput).find((key) => key.startsWith("__reactProps$"));
    await act(async () => { questionInput[questionPropsKey].onChange({ currentTarget: questionInput, target: questionInput }); });
    const form = findElements(container, (node) => node.tagName === "FORM")[0];
    const formPropsKey = Object.keys(form).find((key) => key.startsWith("__reactProps$"));
    await act(async () => { await form[formPropsKey].onSubmit({ preventDefault() {} }); });
    assert.equal(questionCalls, 1);
    assert.match(container.textContent, /근거 답변/);
    assert.equal(findElements(container, (node) => node.tagName === "A" && node.getAttribute("href")?.endsWith("#page=2")).length, 1);
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Processing bounded polling은 timeout·lineage mismatch·malformed를 Safe error로 만들고 unmount에서 중단한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-processing-safe-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "processing-safe-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("processing-safe-workspace") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Processing Safe actual JSX bundle entry가 필요하다");
    const { ActualWorkspace } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?processingSafe=${Date.now()}`);
    const uploadResult = {
      source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32), digest_sha256: "b".repeat(64),
      byte_size: 128, status: "accepted", replayed: false, processing_run_id: "run-1", processing_state: "accepted", job_state: "pending"
    };
    const pendingStatus = {
      processing_run_id: "run-1", source_id: "source-1", source_version_id: "version-1",
      source_state: "processing", processing_state: "vision_llm_understanding", job_state: "leased", safe_error_code: null
    };
    const cases = [
      { name: "timeout", status: pendingStatus, deadlineMs: 2, expectedCode: "PROCESSING_TIMEOUT", expectedCalls: 2 },
      { name: "lineage mismatch", status: { ...pendingStatus, source_version_id: "version-other" }, deadlineMs: 4, expectedCode: "PROCESSING_LINEAGE_MISMATCH", expectedCalls: 1 },
      { name: "malformed", status: { processing_run_id: "run-1", source_id: "source-1", source_version_id: "version-1", source_state: "processing", processing_state: "accepted", job_state: "pending" }, deadlineMs: 4, expectedCode: "PROCESSING_STATUS_INVALID", expectedCalls: 1 }
    ];

    for (const scenario of cases) {
      const container = dom.document.createElement("div");
      dom.document.body.appendChild(container);
      root = createRoot(container);
      let statusCalls = 0;
      let questionCalls = 0;
      let virtualNow = 0;
      const adapter = {
        listSources: async () => [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
        listKnowledgePackages: async () => [],
        listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
        uploadPdf: async () => uploadResult,
        getProcessingStatus: async () => { statusCalls += 1; return scenario.status; },
        askQuestion: async () => { questionCalls += 1; throw new Error("QUESTION_SHOULD_NOT_RUN"); },
        citationUrl: () => ""
      };
      const processingPollOptions = {
        deadlineMs: scenario.deadlineMs,
        intervalMs: 1,
        now: () => virtualNow,
        wait: async (intervalMs) => { virtualNow += intervalMs; }
      };
      await act(async () => { root.render(createElement(ActualWorkspace, { workspaceId: "workspace-1", adapter, processingPollOptions })); });
      const fileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
      fileInput.files = [{ name: `${scenario.name}.pdf`, type: "application/pdf", size: 128 }];
      await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
      assert.equal(statusCalls, scenario.expectedCalls, scenario.name);
      assert.equal(questionCalls, 0, scenario.name);
      // Registration was accepted; a later processing failure reloads the
      // canonical list instead of misreporting the upload as failed.
      assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === "ready").length, 1, scenario.name);
      assert.doesNotMatch(container.textContent, /Source를 불러오지 못했습니다/u, scenario.name);
      const questionInput = findElements(container, (node) => node.tagName === "TEXTAREA")[0];
      assert.equal(questionInput.disabled, false, scenario.name);
      await act(async () => { root.unmount(); });
      root = null;
    }

    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    let statusCalls = 0;
    let questionCalls = 0;
    let waitSignal;
    const adapter = {
      listSources: async () => [],
      listKnowledgePackages: async () => [],
      listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
      uploadPdf: async () => uploadResult,
      getProcessingStatus: async (_runId, { signal } = {}) => { statusCalls += 1; assert.equal(signal?.aborted, false); return pendingStatus; },
      askQuestion: async () => { questionCalls += 1; throw new Error("QUESTION_SHOULD_NOT_RUN"); },
      citationUrl: () => ""
    };
    const processingPollOptions = {
      maxAttempts: 4,
      intervalMs: 1,
      wait: (_intervalMs, signal) => new Promise((resolve, reject) => {
        waitSignal = signal;
        signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })), { once: true });
      })
    };
    await act(async () => { root.render(createElement(ActualWorkspace, { workspaceId: "workspace-1", adapter, processingPollOptions })); });
    const fileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    fileInput.files = [{ name: "unmount.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
    for (let attempt = 0; attempt < 20 && !waitSignal; attempt += 1) await Promise.resolve();
    assert.ok(waitSignal, "poll wait가 시작되어야 한다");
    await act(async () => { root.unmount(); await Promise.resolve(); });
    root = null;
    assert.equal(waitSignal?.aborted, true);
    assert.equal(statusCalls, 1, "unmount 뒤 status network 재호출은 없어야 한다");
    assert.equal(questionCalls, 0);
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Processing SLA는 12초 초과 완료를 허용하고 status hang을 150초 Deadline 안에서 Safe timeout한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-processing-sla-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "processing-sla-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("processing-sla-workspace") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Processing SLA actual JSX bundle entry가 필요하다");
    const { ActualWorkspace } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?processingSla=${Date.now()}`);
    const uploadResult = {
      source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32), digest_sha256: "b".repeat(64),
      byte_size: 128, status: "accepted", replayed: false, processing_run_id: "run-1", processing_state: "queued", job_state: "queued"
    };
    const terminalStatus = {
      processing_run_id: "run-1", source_id: "source-1", source_version_id: "version-1",
      source_state: "ready", processing_state: "completed", job_state: "completed", safe_error_code: null
    };

    const successContainer = dom.document.createElement("div");
    dom.document.body.appendChild(successContainer);
    root = createRoot(successContainer);
    let virtualNow = 0;
    let statusCalls = 0;
    const waitSignals = [];
    const successAdapter = {
      listSources: async () => [{ source_id: "source-1", source_version_id: "version-1", filename: "slow-success.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
      listKnowledgePackages: async () => [],
      listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
      uploadPdf: async () => uploadResult,
      getProcessingStatus: async () => {
        statusCalls += 1;
        return statusCalls >= 14
          ? terminalStatus
          : { ...terminalStatus, source_state: "processing", processing_state: "vision_llm_understanding", job_state: "leased" };
      },
      askQuestion: async () => { throw new Error("QUESTION_SHOULD_NOT_RUN"); },
      citationUrl: () => ""
    };
    await act(async () => {
      root.render(createElement(ActualWorkspace, {
        workspaceId: "workspace-1",
        adapter: successAdapter,
        processingPollOptions: {
          deadlineMs: 150_000,
          intervalMs: 1_000,
          statusRequestTimeoutMs: 10_000,
          now: () => virtualNow,
          wait: async (intervalMs, signal) => { waitSignals.push(signal); virtualNow += intervalMs; }
        }
      }));
    });
    const successInput = findElements(successContainer, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    successInput.files = [{ name: "slow-success.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { successInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
    assert.equal(statusCalls, 14, "12초를 넘겨 완료된 실제 처리도 150초 Deadline 안이면 성공해야 한다");
    assert.equal(virtualNow, 13_000);
    assert.ok(waitSignals.length === 13 && waitSignals.every((signal) => signal === waitSignals[0] && !signal.aborted));
    assert.equal(findElements(successContainer, (node) => node.getAttribute("data-product-workspace-state") === "ready").length, 1);
    await act(async () => { root.unmount(); });
    root = null;

    const timeoutContainer = dom.document.createElement("div");
    dom.document.body.appendChild(timeoutContainer);
    root = createRoot(timeoutContainer);
    let timeoutStatusCalls = 0;
    const statusSignals = [];
    const hangingAdapter = {
      listSources: async () => [],
      listKnowledgePackages: async () => [],
      listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
      uploadPdf: async () => uploadResult,
      getProcessingStatus: async (_runId, { signal } = {}) => {
        timeoutStatusCalls += 1;
        statusSignals.push(signal);
        return new Promise(() => {});
      },
      askQuestion: async () => { throw new Error("QUESTION_SHOULD_NOT_RUN"); },
      citationUrl: () => ""
    };
    await act(async () => {
      root.render(createElement(ActualWorkspace, {
        workspaceId: "workspace-1",
        adapter: hangingAdapter,
        processingPollOptions: { deadlineMs: 80, intervalMs: 1, statusRequestTimeoutMs: 5 }
      }));
    });
    const timeoutInput = findElements(timeoutContainer, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    timeoutInput.files = [{ name: "hang.pdf", type: "application/pdf", size: 128 }];
    await act(async () => {
      timeoutInput.dispatchEvent(new MinimalEvent("change"));
      await new Promise((resolve) => setTimeout(resolve, 120));
    });
    assert.ok(timeoutStatusCalls >= 2, "개별 status 10초 제한 뒤 전체 Deadline 전이면 재시도해야 한다");
    assert.ok(statusSignals.every((signal) => signal?.aborted), "각 status fetch의 request-local signal은 제한 시 중단되어야 한다");
    assert.equal(findElements(timeoutContainer, (node) => node.getAttribute("data-product-workspace-state") === "empty").length, 1);
    assert.doesNotMatch(timeoutContainer.textContent, /Source를 불러오지 못했습니다/u);
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Upload lifecycle은 새 Upload와 unmount에서 이전 operation을 abort하고 이전 결과를 반영하지 않는다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-upload-lifecycle-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "upload-lifecycle-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("upload-lifecycle-workspace") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Upload lifecycle actual JSX bundle entry가 필요하다");
    const { ActualWorkspace } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?uploadLifecycle=${Date.now()}`);
    const terminalStatus = (runId, sourceId) => ({
      processing_run_id: runId, source_id: sourceId, source_version_id: `${sourceId}-version`,
      source_state: "ready", processing_state: "completed", job_state: "completed", safe_error_code: null
    });
    let firstResolve;
    let firstSignal;
    const statusRunIds = [];
    const adapter = {
      listSources: async () => [{ source_id: "source-2", source_version_id: "source-2-version", filename: "second.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
      uploadPdf: (file, { signal } = {}) => {
        if (file.name === "first.pdf") {
          firstSignal = signal;
          return new Promise((resolve) => { firstResolve = resolve; });
        }
        return Promise.resolve({ source_id: "source-2", source_version_id: "source-2-version", processing_run_id: "run-2" });
      },
      getProcessingStatus: async (runId) => { statusRunIds.push(runId); return terminalStatus(runId, "source-2"); },
      askQuestion: async () => { throw new Error("QUESTION_SHOULD_NOT_RUN"); },
      citationUrl: () => ""
    };
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(createElement(ActualWorkspace, { workspaceId: "workspace-1", adapter })); });
    const fileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    fileInput.files = [{ name: "first.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
    assert.ok(firstSignal && !firstSignal.aborted, "첫 Upload가 operation signal을 받아야 한다");
    fileInput.files = [{ name: "second.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
    assert.equal(firstSignal.aborted, true, "새 Upload는 이전 Upload fetch를 중단해야 한다");
    firstResolve({ source_id: "source-1", source_version_id: "source-1-version", processing_run_id: "run-1" });
    await act(async () => { await Promise.resolve(); });
    assert.deepEqual(statusRunIds, ["run-2"], "중단된 첫 Upload 결과는 status/state에 반영되지 않아야 한다");
    assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === "ready").length, 1);
    await act(async () => { root.unmount(); });
    root = null;

    let unmountResolve;
    let unmountSignal;
    let unmountStatusCalls = 0;
    const unmountAdapter = {
      uploadPdf: (_file, { signal } = {}) => {
        unmountSignal = signal;
        return new Promise((resolve) => { unmountResolve = resolve; });
      },
      getProcessingStatus: async () => { unmountStatusCalls += 1; return terminalStatus("run-unmount", "source-unmount"); },
      askQuestion: async () => { throw new Error("QUESTION_SHOULD_NOT_RUN"); },
      citationUrl: () => ""
    };
    const unmountContainer = dom.document.createElement("div");
    dom.document.body.appendChild(unmountContainer);
    root = createRoot(unmountContainer);
    await act(async () => { root.render(createElement(ActualWorkspace, { workspaceId: "workspace-1", adapter: unmountAdapter })); });
    const unmountInput = findElements(unmountContainer, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
    unmountInput.files = [{ name: "unmount-upload.pdf", type: "application/pdf", size: 128 }];
    await act(async () => { unmountInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
    await act(async () => { root.unmount(); await Promise.resolve(); });
    root = null;
    assert.equal(unmountSignal?.aborted, true, "unmount는 진행 중 Upload fetch를 중단해야 한다");
    unmountResolve({ source_id: "source-unmount", source_version_id: "source-unmount-version", processing_run_id: "run-unmount" });
    await act(async () => { await Promise.resolve(); });
    assert.equal(unmountStatusCalls, 0, "unmount 뒤 Upload 결과는 status/state에 반영되지 않아야 한다");
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Question React는 Safe DTO만 state에 반영하고 malformed Citation을 crash 없이 거부한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-question-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/components/actual-workspace.jsx"), formats: ["es"], fileName: "question-workspace" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("question-workspace") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Question actual JSX bundle entry가 필요하다");
    const { ActualWorkspace } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?question=${Date.now()}`);
    const uploadResult = {
      source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32), digest_sha256: "b".repeat(64),
      byte_size: 128, status: "accepted", replayed: false, processing_run_id: "run-1", processing_state: "queued", job_state: "queued"
    };
    const processingStatus = {
      processing_run_id: "run-1", source_id: "source-1", source_version_id: "version-1",
      source_state: "ready", processing_state: "completed", job_state: "completed", safe_error_code: null
    };
    const validCitation = {
      citation_id: "citation-1", source_id: "source-1", source_version_id: "version-1", evidence_span_id: "span-1", page: 2, origin: "raw_source", context_item_id: "source-1", locator: { kind: "page", value: "2" }
    };
    const validAnswer = {
      run_id: "answer-run-1", run_result_id: "answer-result-1", answer: "근거 답변", insufficient: false, citations: [validCitation]
    };
    const cases = [
      { name: "normal", answer: validAnswer, url: "/bff/api/workspaces/workspace-1/citations/citation-1/content#page=2", expected: "ready" },
      { name: "citations object", answer: { ...validAnswer, citations: { citation: validCitation } }, url: "", expected: "ready" },
      { name: "invalid id", answer: { ...validAnswer, citations: [{ ...validCitation, citation_id: "bad/id" }] }, url: "", expected: "ready" },
      { name: "invalid id type", answer: { ...validAnswer, citations: [{ ...validCitation, citation_id: 7 }] }, url: "/bff/api/workspaces/workspace-1/citations/7/content#page=2", expected: "ready" },
      { name: "invalid page", answer: { ...validAnswer, citations: [{ ...validCitation, page: 0 }] }, url: "", expected: "ready" },
      { name: "unknown field", answer: { ...validAnswer, unexpected: true }, url: "", expected: "ready" },
      { name: "invalid citationUrl", answer: validAnswer, url: "https://internal.invalid/citation", expected: "ready" }
    ];

    for (const scenario of cases) {
      const container = dom.document.createElement("div");
      dom.document.body.appendChild(container);
      root = createRoot(container);
      const adapter = {
        listSources: async () => [{ source_id: "source-1", source_version_id: "version-1", filename: "ready.pdf", source_state: "ready", processing_state: "completed", job_state: "completed" }],
        listKnowledgePackages: async () => [],
        listProductStudioOutputs: async () => ({ outputs: [], studioLocks: [] }),
        uploadPdf: async () => uploadResult,
        getProcessingStatus: async () => processingStatus,
        askQuestion: async () => scenario.answer,
        citationUrl: () => scenario.url
      };
      await act(async () => { root.render(createElement(ActualWorkspace, { workspaceId: "workspace-1", adapter })); });
      const fileInput = findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("type") ?? node.type) === "file")[0];
      fileInput.files = [{ name: "ready.pdf", type: "application/pdf", size: 128 }];
      await act(async () => { fileInput.dispatchEvent(new MinimalEvent("change")); await Promise.resolve(); });
      const questionInput = findElements(container, (node) => node.tagName === "TEXTAREA")[0];
      questionInput.value = "근거는?";
      const questionPropsKey = Object.keys(questionInput).find((key) => key.startsWith("__reactProps$"));
      assert.ok(questionPropsKey, "실제 React question input props가 필요하다");
      await act(async () => {
        questionInput[questionPropsKey].onChange({ currentTarget: questionInput, target: questionInput });
      });
      const form = findElements(container, (node) => node.tagName === "FORM")[0];
      const formPropsKey = Object.keys(form).find((key) => key.startsWith("__reactProps$"));
      assert.ok(formPropsKey, "실제 React question form props가 필요하다");
      let renderError = null;
      try {
        await act(async () => { await form[formPropsKey].onSubmit({ preventDefault() {} }); });
      } catch (error) {
        renderError = error;
      }
      assert.equal(renderError, null, `${scenario.name}는 render crash 없이 Safe state로 전환해야 한다`);
      assert.equal(findElements(container, (node) => node.getAttribute("data-product-workspace-state") === scenario.expected).length, 1, scenario.name);
      if (scenario.name !== "normal") assert.doesNotMatch(container.textContent, /근거 답변/u, scenario.name);
      else {
        assert.match(container.textContent, /근거 답변/);
        assert.equal(findElements(container, (node) => node.tagName === "A" && node.getAttribute("href") === scenario.url).length, 1);
      }
      await act(async () => { root.unmount(); });
      root = null;
    }
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web AuthPane actual React는 로그인·가입 인증·비밀번호 재설정을 단계별로 분리한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".r1-auth-pane-react-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    const redirects = [];
    const calls = [];
    let fetchResponse = async () => Response.json({ data: {} });
    dom.window.location.assign = (target) => { redirects.push(target); };
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init, body: JSON.parse(init.body) });
      return fetchResponse(url, init);
    };
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/lib/auth-pane.jsx"), formats: ["es"], fileName: "auth-pane" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("auth-pane") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Web AuthPane actual JSX bundle entry가 필요하다");
    const { AuthPane } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?r1Auth=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    const inputs = () => findElements(container, (node) => node.tagName === "INPUT");
    const inputNamed = (name) => inputs().find((node) => (node.getAttribute("name") ?? node.name) === name);
    const inputValue = async (name, value) => {
      const input = inputNamed(name);
      assert.ok(input, `${name} input이 필요하다`);
      input.value = value;
      await act(async () => { input.dispatchEvent(new MinimalEvent("input")); });
      return input;
    };
    const click = async (label) => {
      const button = buttonByText(container, label);
      assert.ok(button, `${label} button이 필요하다`);
      await act(async () => {
        button.dispatchEvent(new MinimalEvent("click"));
        await Promise.resolve();
      });
      return button;
    };
    const assertNoAction = (label) => assert.equal(buttonByText(container, label), undefined, `현재 DOM에 ${label}가 없어야 한다`);
    await act(async () => { root.render(createElement(AuthPane)); });
    assert.equal(inputs().length, 2);
    assert.ok(inputNamed("login-id"));
    assert.ok(inputNamed("password"));
    assert.equal(dom.document.activeElement, inputNamed("login-id"));
    assert.ok(buttonByText(container, "로그인"));
    assert.match(buttonByText(container, "가입하기")?.getAttribute("class") ?? "", /daon-auth-link/u);
    assert.match(buttonByText(container, "비밀번호를 잊으셨나요?")?.getAttribute("class") ?? "", /daon-auth-link/u);
    for (const label of ["가입", "이메일 인증", "인증 재전송", "재설정 메일 요청", "비밀번호 재설정"]) assertNoAction(label);
    await inputValue("login-id", "login-user");
    await inputValue("password", "password-value");
    fetchResponse = async () => Response.json({ error: { code: "http://internal:8000 secret stack" } }, { status: 401 });
    const failureCallStart = calls.length;
    await click("로그인");
    assert.equal(calls.length - failureCallStart, 1);
    assert.equal(calls.at(-1).url, "/bff/api/auth/login");
    assert.deepEqual(calls.at(-1).body, { login_id: "login-user", password: "password-value" });
    assert.equal(inputNamed("password").value, "");
    assert.doesNotMatch(container.textContent, /http:\/\/internal|secret|stack/u);
    assert.match(container.textContent, /요청을 완료하지 못했습니다/u);
    await click("가입하기");
    assert.equal(inputs().length, 3);
    assert.ok(inputNamed("signup-login-id"));
    assert.ok(inputNamed("email"));
    assert.ok(inputNamed("signup-password"));
    assert.equal(dom.document.activeElement, inputNamed("signup-login-id"));
    assert.ok(buttonByText(container, "로그인으로 돌아가기"));
    assertNoAction("계정 복구");
    assertNoAction("이메일 인증");
    assertNoAction("인증 재전송");
    await inputValue("signup-login-id", "signup-user");
    await inputValue("email", "signup@example.com");
    await inputValue("signup-password", "signup-password");
    fetchResponse = async () => Response.json({ data: {} });
    await click("가입");
    assert.equal(calls.at(-1).url, "/bff/api/auth/signup");
    assert.deepEqual(calls.at(-1).body, { login_id: "signup-user", email: "signup@example.com", password: "signup-password" });
    assert.equal(inputs().length, 1);
    assert.ok(inputNamed("verification-token"));
    assert.equal(dom.document.activeElement, inputNamed("verification-token"));
    assertNoAction("가입");
    await inputValue("verification-token", "verify-token");
    await click("이메일 인증");
    assert.equal(calls.at(-1).url, "/bff/api/auth/verify-email");
    assert.deepEqual(calls.at(-1).body, { token: "verify-token" });
    assert.equal(inputNamed("verification-token").value, "");
    await click("인증 재전송");
    assert.equal(calls.at(-1).url, "/bff/api/auth/resend-verification");
    assert.deepEqual(calls.at(-1).body, { identifier: "signup-user" });
    await click("로그인으로 돌아가기");
    assert.equal(inputs().length, 2);
    await click("비밀번호를 잊으셨나요?");
    assert.equal(inputs().length, 1);
    assert.ok(inputNamed("reset-identifier"));
    assert.equal(dom.document.activeElement, inputNamed("reset-identifier"));
    assert.ok(buttonByText(container, "재설정 메일 요청"));
    assertNoAction("비밀번호 재설정");
    await inputValue("reset-identifier", "recovery@example.com");
    await click("재설정 메일 요청");
    assert.equal(calls.at(-1).url, "/bff/api/auth/password-reset/request");
    assert.deepEqual(calls.at(-1).body, { identifier: "recovery@example.com" });
    assert.equal(inputs().length, 2);
    assert.ok(inputNamed("reset-token"));
    assert.ok(inputNamed("reset-password"));
    assert.equal(dom.document.activeElement, inputNamed("reset-token"));
    assertNoAction("재설정 메일 요청");
    await inputValue("reset-token", "reset-token-value");
    await inputValue("reset-password", "changed-password");
    await click("비밀번호 재설정");
    assert.equal(calls.at(-1).url, "/bff/api/auth/password-reset/confirm");
    assert.deepEqual(calls.at(-1).body, { token: "reset-token-value", new_password: "changed-password" });
    assert.equal(inputNamed("reset-token").value, "");
    assert.equal(inputNamed("reset-password").value, "");
    await click("로그인으로 돌아가기");
    assert.equal(inputs().length, 2);
    assert.equal(dom.document.activeElement, inputNamed("login-id"));
    await inputValue("login-id", "login-user");
    await inputValue("password", "successful-password");
    let resolveLogin;
    fetchResponse = () => new Promise((resolve) => { resolveLogin = resolve; });
    const successCallStart = calls.length;
    const loginButton = buttonByText(container, "로그인");
    await act(async () => {
      loginButton.dispatchEvent(new MinimalEvent("click"));
      await Promise.resolve();
    });
    assert.equal(loginButton.disabled, true);
    loginButton.dispatchEvent(new MinimalEvent("click"));
    assert.equal(calls.length - successCallStart, 1);
    await act(async () => {
      resolveLogin(Response.json({ data: { workspace_id: "workspace-login-1" } }));
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(inputNamed("password").value, "");
    assert.deepEqual(redirects, ["/notebooks"]);
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    globalThis.fetch = priorFetch;
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Web Login actual React는 same-origin 성공 응답의 Workspace로 이동한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".stage-a-web-login-react-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    const redirects = [];
    const calls = [];
    dom.window.location.assign = (target) => { redirects.push(target); };
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      return Response.json({ data: { workspace_id: "workspace-login-1" } });
    };
    await build({
      configFile: false,
      logLevel: "silent",
      root: repositoryRoot,
      build: {
        outDir: bundleRoot,
        emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/web/lib/auth-pane.jsx"), formats: ["es"], fileName: "auth-pane" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] }
      }
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("auth-pane") && /\.(?:m?js)$/u.test(name));
    assert.ok(bundleEntry, "Web AuthPane actual JSX bundle entry가 필요하다");
    const { AuthPane } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?stageA=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => { root.render(createElement(AuthPane)); });
    const inputs = findElements(container, (node) => node.tagName === "INPUT");
    const password = inputs.find((node) => (node.getAttribute("type") ?? node.type) === "password");
    const loginId = inputs[0];
    loginId.value = "user-login-1";
    password.value = "password-value";
    await act(async () => {
      loginId.dispatchEvent(new MinimalEvent("input"));
      password.dispatchEvent(new MinimalEvent("input"));
      buttonByText(container, "로그인").dispatchEvent(new MinimalEvent("click"));
      await Promise.resolve();
    });
    assert.equal(calls[0].url, "/bff/api/auth/login");
    assert.deepEqual(redirects, ["/notebooks"]);
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    globalThis.fetch = priorFetch;
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    dom.restore();
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("native navigation은 기본 사용자 메뉴만 노출하고 권한 없는 직접 Route를 거부한다", async () => {
  const { createWindowsNavigation, selectNativeRoute } = await import("../../apps/desktop/src/desktop-shell-model.js");
  const navigation = JSON.parse(await read("packages/contracts/navigation.json"));
  const routes = createWindowsNavigation(navigation.routes);
  assert.ok(routes.length > 0);
  assert.ok(routes.every((route) => route.clients.includes("windows")));
  assert.ok(routes.every((route) => route.key === route.native_route_key));
  assert.deepEqual(routes.map((route) => route.key), ["WorkspaceDetail", "Notifications", "AccountSettings"]);
  assert.equal(selectNativeRoute("WorkspaceDetail", "OrganizationSettings", routes), "WorkspaceDetail");
  assert.equal(selectNativeRoute("WorkspaceDetail", "Operations", routes), "WorkspaceDetail");
  const authorizedRoutes = createWindowsNavigation(navigation.routes, { organization: true, operations: true });
  assert.deepEqual(authorizedRoutes.map((route) => route.key), ["WorkspaceDetail", "Notifications", "AccountSettings", "OrganizationSettings", "Operations"]);
  assert.ok(routes.every((route) => !("capabilities" in route)));
});

test("500px desktop navigation wraps all routes without horizontal scrolling", async () => {
  const css = await read("apps/desktop/src/desktop-shell.css");
  const compactRule = css.match(/@media \(max-width: 600px\) \{([\s\S]*?)\n\}/)?.[1] ?? "";
  assert.match(compactRule, /\.desktop-navigation\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(compactRule, /\.desktop-navigation\s*\{[^}]*overflow-x:\s*(?:clip|hidden)/);
  assert.match(compactRule, /\.desktop-navigation button\s*\{[^}]*flex:\s*1 1 auto/);
});

test("production Tauri configuration is bundled and WebView remains fail-closed", async () => {
  const cargoManifest = await read("apps/desktop/src-tauri/Cargo.toml");
  assert.match(cargoManifest, /^default-run\s*=\s*"daon-user-desktop"$/m);

  const config = JSON.parse(await read("apps/desktop/src-tauri/tauri.conf.json"));
  assert.equal(config.build.frontendDist, "../dist");
  assert.equal("devUrl" in config.build, false);
  assert.equal(config.bundle.targets, "nsis");
  assert.deepEqual(config.bundle.icon, ["icons/icon.ico", "icons/icon.png"]);
  assert.equal(config.app.security.capabilities[0], "desktop-main");
  assert.match(config.app.security.csp, /connect-src 'none'/);
  assert.doesNotMatch(config.app.security.csp, /unsafe-eval|\*/);

  const capability = JSON.parse(await read("apps/desktop/src-tauri/capabilities/desktop-main.json"));
  assert.deepEqual(capability.windows, ["main"]);
  assert.deepEqual(capability.permissions, []);

  const rust = await read("apps/desktop/src-tauri/src/lib.rs");
  assert.match(rust, /local_service_status/);
  assert.match(rust, /local_service_retry/);
  assert.match(rust, /native_login/);
  assert.match(rust, /native_logout/);
  assert.match(rust, /native_session_status/);
  assert.match(rust, /generate_handler!/);
  assert.doesNotMatch(rust, /tauri_plugin_shell|plugin\(/);
  assert.deepEqual(await readdir(new URL("../../apps/desktop/src-tauri/icons/", import.meta.url)), ["icon.ico", "icon.png"]);
  await assert.rejects(access(new URL("../../apps/desktop/src-tauri/gen/", import.meta.url)));
});

test("native session commands retain a fixed HTTPS gateway and never expose credentials to the WebView", async () => {
  const rust = await read("apps/desktop/src-tauri/src/native_session.rs");
  const manifest = await read("apps/desktop/src-tauri/Cargo.toml");
  const wrapper = await read("scripts/run-isolated-desktop-cargo.mjs");
  assert.match(manifest, /\[features\][\s\S]*?default\s*=\s*\[\][\s\S]*?contract-test\s*=\s*\[\]/u);
  assert.match(manifest, /reqwest\s*=\s*\{\s*version\s*=\s*"=0\.13\.4",\s*default-features\s*=\s*false,\s*features\s*=\s*\["json",\s*"rustls",\s*"stream"\]\s*\}/);
  assert.match(manifest, /zeroize\s*=\s*"=1\.9\.0"/);
  assert.match(rust, /DaonUser\/NativeSession\/v1/);
  assert.match(rust, /https:\/\/daon-user\.sinsan\.kr/);
  assert.match(rust, /\/api\/v1\/auth\/native\/login/);
  assert.match(rust, /\/api\/v1\/session\/refresh/);
  assert.match(rust, /redirect\(reqwest::redirect::Policy::none\(\)\)/);
  assert.match(rust, /connect_timeout/);
  assert.match(rust, /MAX_RESPONSE_BYTES/);
  assert.doesNotMatch(rust, /CONTRACT_TEST_ONLY_START|CONTRACT_TEST_ONLY_END/u);
  assert.match(rust, /#\[cfg\(feature = "contract-test"\)\]\s*pub trait NativeIdentityTransportPort/u);
  assert.match(rust, /#\[cfg\(feature = "contract-test"\)\]\s*pub trait NativeSessionVaultPort/u);
  assert.doesNotMatch(rust, /NativeRefreshFlow|NativeRefreshTransport/u);
  assert.match(rust, /#\[cfg\(feature = "contract-test"\)\][\s\S]{0,80}?pub fn for_contract_test\(/u);
  assert.match(wrapper, /"--features",\s*"contract-test"/u);
  assert.match(rust, /gateway\.starts_with\("http:\/\/127\.0\.0\.1:"\)/u);
});

test("Recovery 권한 조회는 입력 없는 전용 Tauri command와 고정 Session path만 사용한다", async () => {
  const rust = await read("apps/desktop/src-tauri/src/native_session.rs");
  const lib = await read("apps/desktop/src-tauri/src/lib.rs");
  assert.match(rust, /\/api\/v1\/session/u);
  assert.match(lib, /async fn native_recovery_authorization_status\(\s*runtime:/u);
  assert.match(lib, /native_recovery_authorization_status,/u);
  assert.doesNotMatch(lib, /native_recovery_authorization_status\([^)]*(?:method|path|gateway|authorization|action|permission)/iu);
  assert.doesNotMatch(rust, /recovery_operations[\s\S]{0,120}(?:persisted_bytes|vault_write)/iu);
});

test("desktop bundle includes valid Windows ICO and cross-platform square RGBA PNG", async () => {
  const ico = await readBinary("apps/desktop/src-tauri/icons/icon.ico");
  const png = await readBinary("apps/desktop/src-tauri/icons/icon.png");

  assert.deepEqual([...ico.subarray(0, 4)], [0x00, 0x00, 0x01, 0x00]);
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.equal(png.subarray(12, 16).toString("ascii"), "IHDR");

  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  assert.equal(width, height);
  assert.equal(width, 256);
  assert.equal(png[24], 8);
  assert.equal(png[25], 6);

  const iconCount = ico.readUInt16LE(4);
  const sourceFrame = Array.from({ length: iconCount }, (_, index) => 6 + index * 16)
    .map((offset) => ({
      width: ico[offset] || 256,
      height: ico[offset + 1] || 256,
      bytes: ico.readUInt32LE(offset + 8),
      dataOffset: ico.readUInt32LE(offset + 12)
    }))
    .find((entry) => entry.width === 256 && entry.height === 256);
  assert.ok(sourceFrame);
  assert.equal(ico.subarray(sourceFrame.dataOffset, sourceFrame.dataOffset + sourceFrame.bytes).equals(png), true);
});

test("desktop package pins production build and Tauri commands", async () => {
  const pkg = JSON.parse(await read("apps/desktop/package.json"));
  assert.equal(pkg.devDependencies["@tauri-apps/cli"], "2.11.4");
  assert.match(pkg.devDependencies.vite, /^\d+\.\d+\.\d+$/);
  assert.equal(pkg.scripts.build, "vite build");
  assert.equal(pkg.scripts["sidecar:build"], "node ../../scripts/build-local-service-sidecar.mjs");
  assert.equal(pkg.scripts["tauri:build"], "npm run sidecar:build && tauri build --bundles nsis");
});

test("quality gate registers only reproducible desktop runtime capabilities", async () => {
  const root = JSON.parse(await read("package.json"));
  const npmConfig = await read(".npmrc");
  const policy = JSON.parse(await read("quality-gate-policy.json"));
  const desktop = policy.components.find((component) => component.id === "apps/desktop");
  assert.deepEqual(desktop.capabilities.lint.command.command, ["npm", "run", "verify:desktop-lint"]);
  assert.deepEqual(desktop.capabilities.type.command.command, ["npm", "run", "verify:desktop-type"]);
  assert.deepEqual(desktop.capabilities.unit.command.command, ["npm", "run", "verify:desktop-unit"]);
  assert.deepEqual(desktop.capabilities.build.command.command, ["npm", "run", "verify:desktop-build"]);
  for (const script of ["verify:desktop-lint", "verify:desktop-type", "verify:desktop-unit", "verify:desktop-build"]) {
    assert.equal(typeof root.scripts[script], "string");
  }
  assert.equal("preverify:desktop-type" in root.scripts, false);
  assert.equal(root.scripts["verify:desktop-type"], "npm run verify:desktop-build && node scripts/run-isolated-desktop-cargo.mjs check");
  assert.match(npmConfig, /^ignore-scripts=true$/m);
  assert.equal(root.scripts["build:desktop-installer"], "node scripts/run-isolated-desktop-cargo.mjs installer");
});

test("isolated cargo wrapper propagates failure and removes only its exact target", async () => {
  const wrapperSource = await read("scripts/run-isolated-desktop-cargo.mjs");
  assert.match(wrapperSource, /npm_execpath/);
  assert.doesNotMatch(wrapperSource, /npm\.cmd/);
  const { runIsolatedCargo } = await import("../run-isolated-desktop-cargo.mjs");
  const parent = path.join(os.tmpdir(), "daon-user-wrapper-test-");
  let observedTarget = "";
  const result = await runIsolatedCargo({
    prefix: parent,
    keepOnSuccess: false,
    command: "cargo",
    args: ["check"],
    spawnImpl: (_command, _args, options) => {
      observedTarget = options.env.CARGO_TARGET_DIR;
      return { status: 23, signal: null };
    }
  });
  assert.equal(result.exitCode, 23);
  await assert.rejects(access(observedTarget));
  assert.ok(path.dirname(observedTarget).startsWith(os.tmpdir()));
});

test("non-installer Cargo can override only the generated bundle input contract", async () => {
  const wrapperSource = await read("scripts/run-isolated-desktop-cargo.mjs");
  const { runIsolatedCargo } = await import("../run-isolated-desktop-cargo.mjs");
  const tauriConfig = JSON.stringify({ bundle: { externalBin: [] } });
  let observedEnvironment;
  const result = await runIsolatedCargo({
    prefix: path.join(os.tmpdir(), "daon-user-wrapper-env-test-"),
    keepOnSuccess: false,
    command: "cargo",
    args: ["check"],
    envOverrides: { TAURI_CONFIG: tauriConfig },
    spawnImpl: (_command, _args, options) => {
      observedEnvironment = options.env;
      return { status: 0, signal: null };
    }
  });
  assert.equal(result.exitCode, 0);
  assert.equal(observedEnvironment.TAURI_CONFIG, tauriConfig);
  assert.match(wrapperSource, /externalBin:\s*\[\]/u);
});

test("installer Tauri 환경은 Frontend Build 직후 Product UI Bundle Gate를 실행한다", async () => {
  const wrapper = await import(`../run-isolated-desktop-cargo.mjs?t=${Date.now()}`);
  assert.equal(typeof wrapper.createDesktopCargoEnvironment, "function");
  const environment = wrapper.createDesktopCargoEnvironment("installer");
  const config = JSON.parse(environment.TAURI_CONFIG);
  assert.equal(config.build.beforeBuildCommand, "npm run build && node ../../scripts/verify-product-ui-boundary.mjs --target desktop");
  assert.deepEqual(wrapper.createDesktopCargoEnvironment("check"), { TAURI_CONFIG: JSON.stringify({ bundle: { externalBin: [] } }) });
});

test("isolated cargo wrapper removes only gen created by its child and preserves pre-existing sentinels", async () => {
  const { runDesktopCargoSafely } = await import("../run-isolated-desktop-cargo.mjs");
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-user-wrapper-gen-fixture-"));
  const generatedDir = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "gen");
  const adjacentSentinel = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "adjacent.bin");
  const targetParent = path.join(fixtureRoot, "targets");
  const otherWorktreeGen = path.join(fixtureRoot, "other-worktree", "apps", "desktop", "src-tauri", "gen");
  const otherSentinel = path.join(otherWorktreeGen, "sentinel.bin");
  await mkdir(targetParent, { recursive: true });
  await mkdir(otherWorktreeGen, { recursive: true });
  await mkdir(path.dirname(adjacentSentinel), { recursive: true });
  await writeFile(adjacentSentinel, Buffer.from([0x11, 0x22, 0x33]));
  await writeFile(otherSentinel, Buffer.from([0x00, 0x23, 0xff, 0x41]));
  const adjacentHash = createHash("sha256").update(await readFile(adjacentSentinel)).digest("hex");
  const otherHash = createHash("sha256").update(await readFile(otherSentinel)).digest("hex");

  const runGeneratedScenario = async (status) => runDesktopCargoSafely({
    generatedDir,
    prefix: path.join(targetParent, "daon-user-desktop-check-"),
    keepOnSuccess: false,
    command: "fixture-cargo",
    args: [],
    spawnImpl: () => {
      mkdirSync(path.join(generatedDir, "schemas"), { recursive: true });
      writeFileSync(path.join(generatedDir, "schemas", "generated.json"), `status=${status}`);
      return { status, signal: null };
    }
  });

  try {
    const success = await runGeneratedScenario(0);
    assert.equal(success.exitCode, 0);
    await assert.rejects(access(generatedDir));
    assert.deepEqual(await readdir(targetParent), []);

    const failure = await runGeneratedScenario(23);
    assert.equal(failure.exitCode, 23);
    await assert.rejects(access(generatedDir));
    assert.deepEqual(await readdir(targetParent), []);

    const spawnError = await runDesktopCargoSafely({
      generatedDir,
      prefix: path.join(targetParent, "daon-user-desktop-check-"),
      keepOnSuccess: false,
      command: "missing-fixture-cargo",
      args: [],
      spawnImpl: () => {
        const error = new Error("fixture spawn error");
        error.code = "ENOENT";
        throw error;
      }
    });
    assert.equal(spawnError.exitCode, 2);
    await assert.rejects(access(generatedDir));
    assert.deepEqual(await readdir(targetParent), []);

    await mkdir(generatedDir, { recursive: true });
    const sentinel = path.join(generatedDir, "sentinel.bin");
    const sentinelBytes = Buffer.from([0xde, 0xad, 0x00, 0xbe, 0xef]);
    await writeFile(sentinel, sentinelBytes);
    const sentinelHash = createHash("sha256").update(sentinelBytes).digest("hex");
    let childCalls = 0;
    const preexisting = await runDesktopCargoSafely({
      generatedDir,
      prefix: path.join(targetParent, "daon-user-desktop-check-"),
      keepOnSuccess: false,
      command: "must-not-run",
      args: [],
      spawnImpl: () => {
        childCalls += 1;
        return { status: 0, signal: null };
      }
    });
    assert.equal(preexisting.exitCode, 2);
    assert.equal(preexisting.preexistingGeneratedDir, true);
    assert.equal(childCalls, 0);
    assert.equal(createHash("sha256").update(await readFile(sentinel)).digest("hex"), sentinelHash);
    assert.deepEqual(await readdir(targetParent), []);
    assert.equal(createHash("sha256").update(await readFile(adjacentSentinel)).digest("hex"), adjacentHash);
    assert.equal(createHash("sha256").update(await readFile(otherSentinel)).digest("hex"), otherHash);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("desktop cargo wrapper fails closed when gen state cannot be probed", async () => {
  const { runDesktopCargoSafely } = await import("../run-isolated-desktop-cargo.mjs");
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-user-wrapper-probe-fixture-"));
  const generatedDir = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "gen");
  const adjacentSentinel = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "adjacent.bin");
  const targetParent = path.join(fixtureRoot, "targets");
  await mkdir(path.dirname(adjacentSentinel), { recursive: true });
  await mkdir(targetParent, { recursive: true });
  const sentinelBytes = Buffer.from([0xe1, 0xac, 0xce, 0x55]);
  await writeFile(adjacentSentinel, sentinelBytes);
  const sentinelHash = createHash("sha256").update(sentinelBytes).digest("hex");

  try {
    for (const code of ["EACCES", "EIO"]) {
      let childCalls = 0;
      const result = await runDesktopCargoSafely({
        generatedDir,
        prefix: path.join(targetParent, "daon-user-desktop-check-"),
        keepOnSuccess: false,
        command: "must-not-run",
        args: [],
        probePathImpl: async () => {
          const error = new Error(`fixture ${code}`);
          error.code = code;
          throw error;
        },
        spawnImpl: () => {
          childCalls += 1;
          return { status: 0, signal: null };
        }
      });
      assert.equal(result.exitCode, 2);
      assert.equal(result.childStarted, false);
      assert.equal(result.targetDir, null);
      assert.equal(result.preexistingGeneratedDir, null);
      assert.equal(result.error.code, code);
      assert.equal(childCalls, 0);
      assert.deepEqual(await readdir(targetParent), []);
      await assert.rejects(access(generatedDir));
      assert.equal(createHash("sha256").update(await readFile(adjacentSentinel)).digest("hex"), sentinelHash);
    }
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("PostCSS 보정 이력은 고정 Successor Blob으로, 현재 Checkout은 핵심 Pin으로 검증한다", async () => {
  const root = JSON.parse(await read("package.json"));
  const lock = JSON.parse(await read("package-lock.json"));
  const successorCommit = "8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa";
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const readSuccessorJson = (artifactPath) => {
    const result = spawnSync("git", ["show", `${successorCommit}:${artifactPath}`], { cwd: repositoryRoot, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  const successorRoot = readSuccessorJson("package.json");
  const successorLock = readSuccessorJson("package-lock.json");

  assert.deepEqual(successorRoot.overrides, { postcss: "8.5.23" });
  assert.equal(successorLock.packages["node_modules/next"].version, "16.3.0-canary.93");
  assert.equal(successorLock.packages["node_modules/vite"].version, "8.1.5");
  assert.equal(successorLock.packages["node_modules/postcss"].version, "8.5.23");
  assert.equal(successorLock.packages["node_modules/vite/node_modules/postcss"], undefined);
  const successorNonPostcssPackages = Object.fromEntries(
    Object.entries(successorLock.packages).filter(([packagePath]) => !/(^|\/)node_modules\/postcss$/.test(packagePath))
  );
  assert.equal(
    createHash("sha256").update(JSON.stringify(successorNonPostcssPackages)).digest("hex"),
    "49a32ff6e416651358ef5638da18aa2be4de4e04d7f47268cc2ad5f5d1cfd0ca"
  );

  assert.deepEqual(root.overrides, { postcss: "8.5.23" });
  assert.equal(lock.packages["node_modules/next"].version, "16.3.3");
  assert.equal(lock.packages["apps/desktop/node_modules/vite"].version, "8.2.2");
  assert.equal(lock.packages["node_modules/postcss"].version, "8.5.23");
  assert.equal(lock.packages["node_modules/vite/node_modules/postcss"], undefined);

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const listing = spawnSync(npmCommand, ["ls", "next", "vite", "postcss", "--all", "--json"], {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: process.platform === "win32"
  });
  assert.equal(listing.status, 0, listing.stderr || listing.stdout);
  const listingJson = JSON.parse(listing.stdout);
  assert.deepEqual(
    listingJson.problems ?? [],
    []
  );
  assert.equal(listingJson.error, undefined);

  const problemKinds = [];
  const invalidReasons = new Set();
  const visitListing = (value) => {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (key === "invalid" && typeof child === "string") invalidReasons.add(child);
      if ((key === "missing" || key === "extraneous") && child) problemKinds.push(key);
      if (key === "problems" && Array.isArray(child)) {
        for (const problem of child) {
          const kind = String(problem).split(":", 1)[0];
          if (kind !== "invalid") problemKinds.push(kind);
        }
      }
      visitListing(child);
    }
  };
  visitListing(listingJson.dependencies);
  assert.deepEqual([...invalidReasons], []);
  assert.deepEqual(problemKinds, []);

  const findPackage = (value, name) => {
    if (!value || typeof value !== "object") return null;
    if (value[name] && typeof value[name] === "object") return value[name];
    for (const child of Object.values(value)) {
      const found = findPackage(child, name);
      if (found) return found;
    }
    return null;
  };
  const nextPackage = findPackage(listingJson.dependencies, "next");
  const vitePackage = findPackage(listingJson.dependencies, "vite");
  const nextPostcss = nextPackage?.dependencies?.postcss;
  const collectPackages = (value, name, found = []) => {
    if (!value || typeof value !== "object") return found;
    if (value[name] && typeof value[name] === "object") found.push(value[name]);
    for (const child of Object.values(value)) collectPackages(child, name, found);
    return found;
  };
  const vitePostcss = collectPackages(listingJson.dependencies, "postcss").find((item) => item?.version === "8.5.23");
  assert.ok(nextPostcss, "npm ls must expose the web Next.js PostCSS node");
  assert.ok(vitePostcss, "npm ls must expose the desktop Vite PostCSS node");
  assert.equal(nextPostcss.version, "8.5.23");
  assert.equal(vitePostcss.version, "8.5.23");
  assert.equal(nextPostcss.invalid, undefined);
  assert.equal(vitePostcss.invalid, undefined);
});
test("Native Source Question Studio는 canonical Notebook scope와 rich Citation exact shape를 wire에 결속한다", async () => {
  const bridge = await readFile("apps/desktop/src-tauri/src/workspace_bridge.rs", "utf8");
  const session = await readFile("apps/desktop/src-tauri/src/native_session.rs", "utf8");
  assert.match(bridge, /pub notebook_id: String/u);
  assert.match(session, /X-Notebook-Id/u);
  assert.match(session, /notebook_id=/u);
  assert.match(bridge, /pub origin: String/u);
  assert.match(bridge, /pub context_item_id: String/u);
  assert.match(bridge, /pub locator: WorkspaceCitationLocator/u);
});

test("Desktop Notebook async epoch는 Session·Workspace·Hash 전환 뒤 stale 응답 A-D를 렌더하지 않는다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".phase-e-stale-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  const deferred = () => {
    let resolve;
    const promise = new Promise((next) => { resolve = next; });
    return { promise, resolve };
  };
  const session = (suffix) => ({
    user_id: `user-${suffix}`, tenant_id: `tenant-${suffix}`, workspace_id: `workspace-${suffix}`,
    session_id: `session-${suffix}`, device_id: `device-${suffix}`, expires_at: "2026-08-20T23:59:59Z",
  });
  const view = (notebookId, title) => ({
    notebook_id: notebookId, title, source_count: 0, output_count: 0,
    updated_at: "2026-08-20T01:02:03Z", status: "empty",
  });
  const context = (notebookId, sourceId = null) => ({
    notebook_id: notebookId,
    sources: sourceId ? [{ source_id: sourceId, source_version_id: `version-${sourceId}` }] : [],
    knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [],
    generation_settings_ids: [], source_deletion_requests: [], conversation: null,
  });
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false, logLevel: "silent", root: repositoryRoot,
      build: {
        outDir: bundleRoot, emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/desktop/src/desktop-shell.jsx"), formats: ["es"], fileName: "desktop-shell-stale" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
      },
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("desktop-shell-stale") && /\.(?:m?js)$/u.test(name));
    const { DesktopShell } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?stale=${Date.now()}`);
    const mount = async ({ invoke, hash = "#/notebooks" }) => {
      const container = dom.document.createElement("div");
      dom.document.body.appendChild(container);
      let poll;
      window.location.hash = hash;
      root = createRoot(container);
      await act(async () => {
        root.render(createElement(DesktopShell, { nativeInvoke: invoke, sessionWatchOptions: { schedule: (next) => { poll = next; return 1; }, cancel: () => {} } }));
        await Promise.resolve();
      });
      return { container, poll: () => poll() };
    };
    const unmount = async () => { await act(async () => { root.unmount(); }); root = null; };

    // A. 이전 Workspace list가 늦게 끝나도 새 Session/Home을 덮지 않는다.
    {
      const oldList = deferred();
      let status = { authenticated: true, session: session("old") };
      const invoke = async (command, args) => {
        if (command === "native_session_status") return status;
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return args.input.workspace_id === "workspace-old" ? oldList.promise : [view("notebook-new", "NEW WORKSPACE")];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount({ invoke });
      status = { authenticated: true, session: session("new") };
      await act(async () => { await mountedCase.poll(); await Promise.resolve(); });
      oldList.resolve([view("notebook-old", "OLD WORKSPACE")]);
      await act(async () => { await oldList.promise; await Promise.resolve(); });
      assert.match(mountedCase.container.textContent, /NEW WORKSPACE/u);
      assert.doesNotMatch(mountedCase.container.textContent, /OLD WORKSPACE/u);
      await unmount();
    }

    // B. 이전 get 뒤 context가 지연되어도 Workspace 전환 뒤 3열/Hash를 복원하지 않는다.
    {
      const oldContext = deferred();
      let status = { authenticated: true, session: session("old") };
      const invoke = async (command, args) => {
        if (command === "native_session_status") return status;
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_get") return view(args.input.notebook_id, "OLD SELECTED");
        if (command === "notebook_context") return oldContext.promise;
        if (command === "notebook_list") return [view("notebook-new", "NEW HOME")];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount({ invoke, hash: "#/notebooks/notebook-old" });
      status = { authenticated: true, session: session("new") };
      await act(async () => { await mountedCase.poll(); await Promise.resolve(); });
      oldContext.resolve(context("notebook-old", "old-source"));
      await act(async () => { await oldContext.promise; await Promise.resolve(); });
      assert.doesNotMatch(mountedCase.container.textContent, /OLD SELECTED|old-source/u);
      assert.equal(window.location.hash, "#/notebooks");
      await unmount();
    }

    // C. 이전 create가 Logout 뒤 완료되어도 카드와 선택 Hash를 만들지 않는다.
    {
      const oldCreate = deferred();
      let status = { authenticated: true, session: session("old") };
      const invoke = async (command) => {
        if (command === "native_session_status") return status;
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [];
        if (command === "notebook_create") return oldCreate.promise;
        if (command === "native_logout") { status = { authenticated: false, session: null }; return status; }
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount({ invoke });
      await act(async () => { findElements(mountedCase.container, (node) => node.tagName === "BUTTON" && node.textContent.includes("새 Notebook"))[0].dispatchEvent(new MinimalEvent("click")); });
      const dialog = findElements(mountedCase.container, (node) => node.getAttribute("role") === "dialog")[0];
      const input = findElements(dialog, (node) => node.tagName === "INPUT")[0];
      input.value = "OLD CREATED";
      const inputProps = Object.keys(input).find((key) => key.startsWith("__reactProps$"));
      await act(async () => { input[inputProps].onChange({ currentTarget: input, target: input }); });
      const form = findElements(dialog, (node) => node.tagName === "FORM")[0];
      const formProps = Object.keys(form).find((key) => key.startsWith("__reactProps$"));
      let createPromise;
      await act(async () => { createPromise = form[formProps].onSubmit({ preventDefault() {} }); await Promise.resolve(); });
      await act(async () => { buttonByText(mountedCase.container, "⚙ 설정").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      await act(async () => { buttonByText(mountedCase.container, "로그아웃").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      oldCreate.resolve(view("notebook-old-created", "OLD CREATED"));
      await act(async () => { await createPromise; await Promise.resolve(); });
      assert.doesNotMatch(mountedCase.container.textContent, /OLD CREATED/u);
      assert.notEqual(window.location.hash, "#/notebooks/notebook-old-created");
      await unmount();
    }

    // D. popstate 응답 역순에서는 마지막 Target만 선택한다.
    {
      const oldContext = deferred();
      const newGet = deferred();
      const newContext = deferred();
      const status = { authenticated: true, session: session("same") };
      const invoke = async (command, args) => {
        if (command === "native_session_status") return status;
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [];
        if (command === "notebook_get") return args.input.notebook_id === "notebook-old" ? view("notebook-old", "OLD TARGET") : newGet.promise;
        if (command === "notebook_context") return args.input.notebook_id === "notebook-old" ? oldContext.promise : newContext.promise;
        if (command === "workspace_list_sources" || command === "workspace_list_studio_outputs") return [];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount({ invoke });
      window.location.hash = "#/notebooks/notebook-old";
      await act(async () => { window.dispatchEvent(new MinimalEvent("popstate")); await Promise.resolve(); });
      window.location.hash = "#/notebooks/notebook-new";
      await act(async () => { window.dispatchEvent(new MinimalEvent("popstate")); await Promise.resolve(); });
      newGet.resolve(view("notebook-new", "NEW TARGET"));
      await act(async () => { await newGet.promise; await Promise.resolve(); });
      newContext.resolve(context("notebook-new"));
      await act(async () => { await newContext.promise; await Promise.resolve(); });
      oldContext.resolve(context("notebook-old", "old-source"));
      await act(async () => { await oldContext.promise; await Promise.resolve(); });
      assert.match(mountedCase.container.textContent, /NEW TARGET/u);
      assert.doesNotMatch(mountedCase.container.textContent, /OLD TARGET|old-source/u);
      assert.equal(window.location.hash, "#/notebooks/notebook-new");
      await unmount();
    }
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    dom.restore();
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    await rm(bundleRoot, { recursive: true, force: true });
  }
});

test("Desktop session revalidate epoch는 reverse status/context 응답 A-F를 latest-only로 적용한다", async (t) => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".phase-e-revalidate-react-"));
  const dom = installMinimalDom();
  const priorNodeEnv = process.env.NODE_ENV;
  let root;
  const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  };
  const session = (suffix) => ({
    user_id: `user-${suffix}`, tenant_id: `tenant-${suffix}`, workspace_id: `workspace-${suffix}`,
    session_id: `session-${suffix}`, device_id: `device-${suffix}`, expires_at: "2026-08-20T23:59:59Z",
  });
  const view = (notebookId, title, sourceCount = 0) => ({
    notebook_id: notebookId, title, source_count: sourceCount, output_count: 0,
    updated_at: "2026-08-20T01:02:03Z", status: sourceCount ? "active" : "empty",
  });
  const emptyContext = (notebookId) => ({
    notebook_id: notebookId, sources: [], knowledge_context_ids: [], conversation_thread_ids: [],
    studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [], conversation: null,
  });
  const richContext = (notebookId) => ({
    notebook_id: notebookId,
    sources: [{ source_id: "source-old", source_version_id: "version-old" }],
    knowledge_context_ids: [], conversation_thread_ids: ["thread-old"], studio_output_ids: ["output-old"],
    output_version_ids: ["output-version-old"], generation_settings_ids: [], source_deletion_requests: [],
    conversation: {
      conversation_thread_id: "thread-old",
      answer: {
        run_id: "run-old", run_result_id: "result-old", answer: "OLD ANSWER MUST NOT RENDER", insufficient: false,
        citations: [{ citation_id: "citation-old", source_id: "source-old", source_version_id: "version-old", evidence_span_id: "span-old", page: 7, origin: "raw_source", context_item_id: "source-old", locator: { kind: "page", value: "7" } }],
      },
    },
  });
  try {
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { build } = await import("vite");
    await build({
      configFile: false, logLevel: "silent", root: repositoryRoot,
      build: {
        outDir: bundleRoot, emptyOutDir: false,
        lib: { entry: path.join(repositoryRoot, "apps/desktop/src/desktop-shell.jsx"), formats: ["es"], fileName: "desktop-shell-revalidate" },
        rollupOptions: { external: ["react", "react-dom", "react-dom/client"] },
      },
    });
    const bundleEntry = (await readdir(bundleRoot)).find((name) => name.startsWith("desktop-shell-revalidate") && /\.(?:m?js)$/u.test(name));
    const { DesktopShell } = await import(`${pathToFileURL(path.join(bundleRoot, bundleEntry)).href}?revalidate=${Date.now()}`);
    const mount = async (invoke) => {
      const container = dom.document.createElement("div");
      dom.document.body.appendChild(container);
      let poll;
      window.location.hash = "#/notebooks";
      root = createRoot(container);
      await act(async () => {
        root.render(createElement(DesktopShell, { nativeInvoke: invoke, sessionWatchOptions: { schedule: (next) => { poll = next; return 1; }, cancel: () => {} } }));
        await Promise.resolve();
      });
      return { container, poll: () => poll() };
    };
    const unmount = async () => { await act(async () => { root.unmount(); }); root = null; };
    const dispatchRevalidate = async () => {
      await act(async () => { window.dispatchEvent(new MinimalEvent("popstate")); await Promise.resolve(); });
    };

    await t.test("A old authenticated status는 newer unauthenticated 완료 뒤 identity를 복원하지 않는다", async () => {
      const oldStatus = deferred();
      const queue = [{ authenticated: true, session: session("old") }, oldStatus.promise, { authenticated: false, session: null }];
      const invoke = async (command) => {
        if (command === "native_session_status") return queue.shift();
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount(invoke);
      await dispatchRevalidate();
      await dispatchRevalidate();
      oldStatus.resolve({ authenticated: true, session: session("old") });
      await act(async () => { await oldStatus.promise; await Promise.resolve(); });
      assert.match(mountedCase.container.textContent, /Daon 사용자 프로그램/u);
      assert.doesNotMatch(mountedCase.container.textContent, /user-old|OLD/u);
      await unmount();
    });

    await t.test("B old unauthenticated status는 newer authenticated Session을 logout시키지 않는다", async () => {
      const oldStatus = deferred();
      const queue = [{ authenticated: true, session: session("old") }, oldStatus.promise, { authenticated: true, session: session("new") }];
      const invoke = async (command, args) => {
        if (command === "native_session_status") return queue.shift();
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [view(`notebook-${args.input.workspace_id}`, "LATEST HOME")];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount(invoke);
      await dispatchRevalidate();
      await dispatchRevalidate();
      oldStatus.resolve({ authenticated: false, session: null });
      await act(async () => { await oldStatus.promise; await Promise.resolve(); });
      assert.match(mountedCase.container.textContent, /LATEST HOME/u);
      assert.doesNotMatch(mountedCase.container.textContent, /Daon 사용자 프로그램/u);
      await unmount();
    });

    await t.test("C old status rejection은 newer authenticated UI를 error/login으로 바꾸지 않는다", async () => {
      const oldStatus = deferred();
      const queue = [{ authenticated: true, session: session("old") }, oldStatus.promise, { authenticated: true, session: session("new") }];
      const invoke = async (command, args) => {
        if (command === "native_session_status") return queue.shift();
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [view(`notebook-${args.input.workspace_id}`, "LATEST AUTHENTICATED")];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount(invoke);
      await dispatchRevalidate();
      await dispatchRevalidate();
      oldStatus.reject(new Error("OLD_STATUS_REJECTED"));
      await act(async () => { await oldStatus.promise.catch(() => {}); await Promise.resolve(); });
      assert.match(mountedCase.container.textContent, /LATEST AUTHENTICATED/u);
      assert.doesNotMatch(mountedCase.container.textContent, /Daon 사용자 프로그램|NOTEBOOK_UNAVAILABLE/u);
      await unmount();
    });

    const runRichContextRace = async ({ assertInteractionZero }) => {
      const oldStatus = deferred();
      const oldContext = deferred();
      let status = { authenticated: true, session: session("old") };
      let statusReads = 0;
      let poll;
      const workspaceCalls = [];
      const invoke = async (command, args) => {
        if (command === "native_session_status") {
          statusReads += 1;
          if (statusReads === 1) return status;
          if (statusReads === 2) return oldStatus.promise;
          return status;
        }
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [view("notebook-new", "LATEST WORKSPACE")];
        if (command === "notebook_get") return view(args.input.notebook_id, "OLD RICH NOTEBOOK", 1);
        if (command === "notebook_context") return oldContext.promise;
        if (command.startsWith("workspace_")) { workspaceCalls.push(command); return []; }
        throw new Error(`unexpected:${command}`);
      };
      const container = dom.document.createElement("div");
      dom.document.body.appendChild(container);
      window.location.hash = "#/notebooks";
      root = createRoot(container);
      await act(async () => {
        root.render(createElement(DesktopShell, { nativeInvoke: invoke, sessionWatchOptions: { schedule: (next) => { poll = next; return 1; }, cancel: () => {} } }));
        await Promise.resolve();
      });
      window.location.hash = "#/notebooks/notebook-old";
      await dispatchRevalidate();
      status = { authenticated: true, session: session("new") };
      await act(async () => { await poll(); await Promise.resolve(); });
      oldStatus.resolve({ authenticated: true, session: session("old") });
      await act(async () => { await oldStatus.promise; await Promise.resolve(); });
      oldContext.resolve(richContext("notebook-old"));
      await act(async () => { await oldContext.promise; await Promise.resolve(); });
      assert.match(container.textContent, /LATEST WORKSPACE/u);
      assert.doesNotMatch(container.textContent, /OLD RICH NOTEBOOK|OLD ANSWER MUST NOT RENDER|citation-old|source-old/u);
      assert.equal(window.location.hash, "#/notebooks");
      if (assertInteractionZero) {
        assert.equal(workspaceCalls.filter((command) => /ask_question|studio/u.test(command)).length, 0);
        assert.equal(findElements(container, (node) => node.tagName === "TEXTAREA").length, 0);
      }
      await unmount();
    };

    await t.test("D old workspace status와 rich context 역순 완료는 old answer/citation/hash를 렌더하지 않는다", async () => {
      await runRichContextRace({ assertInteractionZero: false });
    });

    await t.test("E stale rich context는 old Question/Studio interaction과 Native command를 만들지 않는다", async () => {
      await runRichContextRace({ assertInteractionZero: true });
    });

    await t.test("F current valid Home 선택은 정상 3열 Context를 유지한다", async () => {
      const current = { authenticated: true, session: session("valid") };
      const invoke = async (command, args) => {
        if (command === "native_session_status") return current;
        if (command === "native_recovery_authorization_status") return { recovery_operations: [] };
        if (command === "notebook_list") return [view("notebook-valid", "VALID NOTEBOOK", 1)];
        if (command === "notebook_get") return view(args.input.notebook_id, "VALID NOTEBOOK", 1);
        if (command === "notebook_context") return emptyContext(args.input.notebook_id);
        if (command === "workspace_list_sources" || command === "workspace_list_studio_outputs") return [];
        throw new Error(`unexpected:${command}`);
      };
      const mountedCase = await mount(invoke);
      const card = findElements(mountedCase.container, (node) => node.tagName === "BUTTON" && node.getAttribute("aria-label")?.endsWith("Notebook 열기"))[0];
      await act(async () => { card.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      assert.match(mountedCase.container.textContent, /VALID NOTEBOOK|Source·지식·권위/u);
      assert.equal(window.location.hash, "#/notebooks/notebook-valid");
      await unmount();
    });
  } finally {
    if (root) await import("react").then(({ act }) => act(async () => { root.unmount(); }));
    dom.restore();
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    await rm(bundleRoot, { recursive: true, force: true });
  }
});
