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
    history: { pushState: (_state, _title, pathname) => { window.location.pathname = pathname; } },
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

test("desktop shell directly consumes shared UI, tokens, and contracts", async () => {
  const source = await read("apps/desktop/src/desktop-shell.jsx");
  assert.match(source, /@daon-user\/ui/);
  assert.match(source, /@daon-user\/contracts\/navigation\.json/);
  assert.match(source, /@daon-user\/contracts\/screens\.json/);
  assert.match(source, /@daon-user\/design-tokens\/tokens\.css/);
  assert.doesNotMatch(source, /apps\/web|next\/|NEXT_PUBLIC_/);
});

test("desktop shell은 Native Session과 Windows Recovery Adapter를 한 번 생성해 Operations에만 주입한다", async () => {
  const source = await read("apps/desktop/src/desktop-shell.jsx");
  const authPanel = await read("apps/desktop/src/native-auth-panel.jsx");
  assert.match(source, /createNativeSessionBridge/);
  assert.match(source, /new WindowsRecoveryAdapter/);
  assert.match(source, /useMemo\(\(\) => createNativeSessionBridge/);
  assert.match(source, /useMemo\(\(\) => new WindowsRecoveryAdapter/);
  assert.match(source, /recoveryAdapter=\{recoveryAdapter\}/);
  assert.match(source, /sessionContext=\{sessionContext\}/);
  assert.match(source, /NativeAuthPanel/);
  assert.match(source, /recoveryAuthorizationStatus/);
  assert.match(source, /recoveryOperations:/);
  assert.match(source, /authorizationRevision: request \* 2/);
  assert.match(source, /authorizationRevision: request \* 2 \+ 1/);
  assert.match(source, /sessionId: nativeSession\.sessionId/);
  assert.match(authPanel, /type="password"/);
  assert.match(authPanel, /defaultValue=""/);
  assert.doesNotMatch(authPanel, /setPassword|useState\([^)]*password|localStorage|sessionStorage|console\./i);
  assert.doesNotMatch(source, /fetch\s*\(|XMLHttpRequest|WebSocket|https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_/i);
});

test("실제 React Tree는 Login 실패·성공·권한 없음·Logout 경쟁을 fail-close한다", async () => {
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const bundleRoot = await mkdtemp(path.join(repositoryRoot, ".c10-r02-react-"));
  const dom = installMinimalDom();
  let root;
  let reactAct;
  const priorNodeEnv = process.env.NODE_ENV;
  try {
    const { act, createElement } = await import("react");
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
    const invoke = async (command) => {
      calls.push(command);
      if (command === "native_session_status") return { authenticated: false, session: null };
      if (command === "native_login") return { authenticated: true, session };
      if (command === "native_recovery_authorization_status") return { recovery_operations: operations };
      if (command === "recovery_cloud_list_backups") return { data: [], etag: null };
      if (command === "local_service_status") return { state: "ready", retryable: false, error_code: null };
      throw new Error(`unexpected:${command}`);
    };
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(createElement(DesktopShell, { nativeInvoke: invoke, sessionWatchOptions: { schedule: (poll) => { scheduledPoll = poll; return 1; }, cancel: () => {} } }));
    });
    assert.ok(scheduledPoll, "제품 Tree가 주입된 watch scheduler를 사용해야 한다");
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
    assert.match(container.textContent, /인증됨 · user-1 · workspace-1/);
    const shell = findElements(container, (node) => node.getAttribute("data-client-type") === "windows")[0];
    assert.equal(shell.getAttribute("data-session-tree-key"), "session-1:5");
    assert.equal(calls.filter((command) => command === "recovery_cloud_list_backups").length, 1);

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
      const cloudButtons = ["목록 새로고침", "전용 Fixture Backup 요청"].map((label) => buttonByText(deniedContainer, label));
      assert.ok(cloudButtons.every(Boolean), `${authorizationMode}: Cloud 버튼이 렌더되어야 한다`);
      assert.ok(cloudButtons.every((button) => button.disabled), `${authorizationMode}: Cloud 버튼은 권한 없이 비활성이어야 한다`);
      await act(async () => { for (const button of cloudButtons) button.dispatchEvent(new MinimalEvent("click")); });
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
    const raceInvoke = async (command) => {
      raceCalls.push(command);
      if (command === "native_session_status") return logoutStarted ? { authenticated: true, session } : { authenticated: false, session: null };
      if (command === "native_login") return { authenticated: true, session };
      if (command === "native_recovery_authorization_status") {
        authorizationCount += 1;
        if (authorizationCount === 1) return { recovery_operations: operations };
        return new Promise((resolve) => { resolveLateAuthorization = resolve; });
      }
      if (command === "native_logout") { logoutStarted = true; return new Promise((resolve) => { resolveLogout = resolve; }); }
      if (command === "recovery_cloud_list_backups") return { data: [], etag: null };
      if (command === "local_service_status") return { state: "ready", retryable: false, error_code: null };
      throw new Error(`unexpected:${command}`);
    };
    const raceContainer = dom.document.createElement("div");
    dom.document.body.appendChild(raceContainer);
    root = createRoot(raceContainer);
    await act(async () => { root.render(createElement(DesktopShell, { nativeInvoke: raceInvoke, sessionWatchOptions: { schedule: (poll) => { latePoll = poll; return 1; }, cancel: () => {} } })); });
    const raceLoginId = findElements(raceContainer, (node) => (node.getAttribute("name") ?? node.name) === "login-id")[0];
    const racePassword = findElements(raceContainer, (node) => (node.getAttribute("name") ?? node.name) === "password")[0];
    raceLoginId.value = "user-1";
    racePassword.value = "password-value";
    await act(async () => { findElements(raceContainer, (node) => node.tagName === "FORM")[0].dispatchEvent(new MinimalEvent("submit")); });
    const recoveryBeforeLogout = raceCalls.filter((command) => command.startsWith("recovery_")).length;
    await act(async () => { buttonByText(raceContainer, "Operations").dispatchEvent(new MinimalEvent("click")); });
    assert.ok(resolveLateAuthorization, "Operations 진입의 늦은 Authorization이 필요하다");
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

test("native navigation exposes only approved Windows routes by stable key", async () => {
  const { createWindowsNavigation } = await import("../../apps/desktop/src/desktop-shell-model.js");
  const navigation = JSON.parse(await read("packages/contracts/navigation.json"));
  const routes = createWindowsNavigation(navigation.routes);
  assert.ok(routes.length > 0);
  assert.ok(routes.every((route) => route.clients.includes("windows")));
  assert.ok(routes.every((route) => route.key === route.native_route_key));
  assert.deepEqual(
    ["Home", "WorkspaceDetail", "AccountSettings", "OrganizationSettings", "Operations", "Notifications"].filter((key) => routes.some((route) => route.key === key)),
    ["Home", "WorkspaceDetail", "AccountSettings", "OrganizationSettings", "Operations", "Notifications"]
  );
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
  assert.match(wrapperSource, /isInstaller\s*\?\s*\{\}\s*:\s*\{\s*TAURI_CONFIG/u);
  assert.match(wrapperSource, /externalBin:\s*\[\]/u);
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
  assert.equal(lock.packages["node_modules/next"].version, "16.3.0-canary.93");
  assert.equal(lock.packages["node_modules/vite"].version, "8.1.5");
  assert.equal(lock.packages["node_modules/postcss"].version, "8.5.23");
  assert.equal(lock.packages["node_modules/vite/node_modules/postcss"], undefined);

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const listing = spawnSync(npmCommand, ["ls", "next", "vite", "postcss", "--all", "--json"], {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: process.platform === "win32"
  });
  assert.equal(listing.status, 1, listing.stderr || listing.stdout);
  const listingJson = JSON.parse(listing.stdout);
  assert.deepEqual(
    listingJson.problems,
    [`invalid: postcss@8.5.23 ${fileURLToPath(new URL("../../node_modules/postcss", import.meta.url))}`]
  );
  assert.equal(listingJson.error?.code, "ELSPROBLEMS");

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
  assert.deepEqual([...invalidReasons], ['"8.5.10" from node_modules/next']);
  assert.deepEqual(problemKinds, []);

  const nextPostcss = listingJson.dependencies["@daon-user/web"].dependencies.next.dependencies.postcss;
  const vitePostcss = listingJson.dependencies["@daon-user/desktop"].dependencies.vite.dependencies.postcss;
  assert.equal(nextPostcss.version, "8.5.23");
  assert.equal(vitePostcss.version, "8.5.23");
  assert.equal(nextPostcss.invalid, '"8.5.10" from node_modules/next');
  assert.equal(vitePostcss.invalid, '"8.5.10" from node_modules/next');
});
