import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { after, before } from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, findElements, installMinimalDom } from "./product-studio-dom.mjs";

// 실제 컴포넌트/Vite/React 경로이며 HTTP 포트만 대역이다. DB·외부 복구 증거가 아니다.
const require = createRequire(import.meta.url);
const canary = "S42_표시금지_합성원문_목적지_자격정보";
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const response = data => ({ payload: { data, meta: { trace_id: "trace-recovery-read" } }, etag: '"recovery-read-1"' });
// 실제 GET/session 공개 필드. primary Workspace의 권한을 대상 Workspace 권한으로 추측하지 않는다.
const session = (changes = {}) => response({
  user_id: "reader-a", login_id: "reader-a", tenant_id: "tenant-a", workspace_id: "workspace-a",
  session_id: "session-a", device_id: "device-a", client_kind: "web", delivery: "same_origin_secure_cookie",
  expires_at: "2099-01-01T00:00:00Z", password_change_required: false, is_system_admin: false,
  recovery_operations: ["cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel",
    "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"], ...changes,
});
const backup = (changes = {}) => ({
  backup_id: "backup-a", tenant_id: "tenant-a", workspace_id: "workspace-a", state: "ready", version: 3,
  trigger: "manual", created_at: "2026-10-18T00:00:00Z", verified_at: "2026-10-18T00:00:01Z",
  schema_revision: "0068", retention_watermark: "watermark-a", manifest_digest: "a".repeat(64),
  object_count: 1, transitions: ["queued", "capturing", "verifying", "ready"], ...changes,
});
const restore = (changes = {}) => ({
  request_id: "restore-a", backup_id: "backup-a", tenant_id: "tenant-a", workspace_id: "workspace-a",
  state: "completed", version: 6, verification_digest: "b".repeat(64),
  preview: { version: 1, included_object_ids: ["object-a"], excluded_object_ids: [], exclusion_reasons: [],
    destination: { tenant_id: "destination-tenant", workspace_id: "destination-workspace", namespace: "fixture-target" },
    created_at: "2026-10-18T00:00:00Z" },
  transitions: ["requested", "preview_ready", "authorized", "restoring", "verifying", "completed"], ...changes,
});
function failure(status, code = "RESOURCE_UNAVAILABLE") {
  return Object.assign(new Error(canary), { status, code, traceId: "trace-safe-error" });
}
function port(overrides = {}) {
  const calls = [], writes = [];
  const api = {
    getSession: async () => { calls.push(["session"]); return session(); },
    getBackup: async id => { calls.push(["backup", id]); return response(backup()); },
    getRestore: async id => { calls.push(["restore", id]); return response(restore()); },
    ...overrides,
  };
  for (const name of ["listBackups", "createBackup", "previewRestore", "executeRestore", "cancelRestore"]) {
    api[name] = () => { writes.push(name); throw new Error("읽기 선택에서 호출 금지"); };
  }
  return { api, calls, writes };
}
function visibleText(node) {
  if (!node || node.hidden || node.inert || node.style?.display === "none"
    || node.style?.visibility === "hidden" || node.getAttribute?.("aria-hidden") === "true") return "";
  return node.nodeType === 3 ? node.nodeValue : [...node.childNodes].map(visibleText).join("");
}
function noMutations(view, writes = []) {
  assert.deepEqual(writes, []);
  const controls = findElements(view.container, node => ["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA", "FORM"].includes(node.tagName));
  assert.equal(controls.some(node => /생성|실행|취소|설정|복구 시작|백업 시작/u.test(node.textContent)), false);
  assert.doesNotMatch(view.container.textContent, /S42_표시금지|destination-tenant|destination-workspace|fixture-target|object-a/u);
}

let output, component;
before(async () => {
  output = await mkdtemp(path.join(tmpdir(), "daon-s42-recovery-target-react-"));
});
async function loadComponent() {
  if (component) return;
  const root = path.resolve(import.meta.dirname, "../..");
  const priorNodeEnv = process.env.NODE_ENV;
  try {
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/app/operations/recovery-workspace.jsx"), formats: ["es"], fileName: "recovery-target" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"],
        output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(name => /^recovery-target.*\.m?js$/u.test(name));
    assert.ok(built, "실제 RecoveryOperationsWorkspace 번들");
    component = (await import(pathToFileURL(path.join(output, built)).href)).RecoveryOperationsWorkspace;
  } finally {
    if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
  }
}
after(async () => { if (output) await rm(output, { recursive: true, force: true }); });

async function render(search = "", api) {
  const dom = installMinimalDom(), browser = dom.document.defaultView;
  const priorLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  const location = new URL(`https://app.example/operations${search}`);
  browser.location = location;
  browser.history = { replaceState: (_state, _title, url) => { location.href = new URL(url, location).href; } };
  browser.dispatchEvent = event => dom.document.dispatchEvent(event);
  Object.defineProperty(globalThis, "location", { configurable: true, value: location });
  dom.document.visibilityState = "visible";
  let root, container, createElement, act;
  let mounted = false;
  const rerender = async nextApi => { await act(async () => {
    root.render(createElement(component, nextApi === undefined ? {} : { api: nextApi }));
  }); };
  const unmount = async () => { if (root && mounted) { mounted = false; await act(async () => root.unmount()); } };
  const cleanup = async () => {
    try { await unmount(); } finally {
      if (priorLocation) Object.defineProperty(globalThis, "location", priorLocation); else delete globalThis.location;
      dom.restore();
    }
  };
  try {
    // 기존 정상 하네스처럼 DOM과 React/renderer를 Vite build보다 먼저 초기화한다.
    // Vite의 build 기본 NODE_ENV=production 때문에 첫 React import에서 act가 사라지는 것을 방지한다.
    const priorNodeEnv = process.env.NODE_ENV;
    let createRoot;
    try {
      process.env.NODE_ENV = "test";
      ({ createElement, act } = await import("react"));
      assert.equal(typeof act, "function", "React 테스트 act 초기화");
      ({ createRoot } = await import("react-dom/client"));
    } finally {
      if (priorNodeEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = priorNodeEnv;
    }
    await loadComponent();
    container = dom.document.createElement("div"); dom.document.body.appendChild(container);
    root = createRoot(container); mounted = true;
    await rerender(api);
  } catch (error) {
    // 초기 import/build/render 오류가 있으면 정리는 수행하되 2차 정리 오류로 원오류를 가리지 않는다.
    try { await cleanup(); } catch { /* 최초 오류는 아래에서 그대로 실패 처리한다. */ }
    throw error;
  }
  return { container, dom, browser, act, rerender, unmount, cleanup,
    navigate: async next => { await act(async () => {
      browser.history.replaceState(null, "", `/operations${next}`);
      browser.dispatchEvent(new MinimalEvent("popstate"));
    }); } };
}

test("S42 복구 선택 query가 없으면 기존 placeholder와 무호출을 보존한다", async () => {
  const p = port(), view = await render("", p.api);
  try {
    assert.match(view.container.textContent, /운영 상태·복구/u);
    assert.match(view.container.textContent, /RESOURCE_UNAVAILABLE/u);
    assert.match(view.container.textContent, /실제 관리자 운영 기능은 후속 Stage C 권한·연결 Gate 완료 후 제공됩니다\./u);
    assert.deepEqual(p.calls, []); noMutations(view, p.writes);
  } finally { await view.cleanup(); }
});

test("S42 기본 recoveryApi로 Session 뒤 정확 backup GET만 읽고 민감 추가필드는 표시하지 않는다", async () => {
  const priorFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([url, init.method, init.credentials, init.cache]);
    if (url === "/bff/api/session") return Response.json(session().payload);
    assert.equal(url, "/bff/api/backups/backup-a");
    return Response.json({ data: backup({ raw_text: canary, secret: canary, destination: canary }), meta: { trace_id: "trace-read" } });
  };
  let view;
  try {
    view = await render("?backup_id=backup-a");
    assert.deepEqual(calls, [["/bff/api/session", "GET", "same-origin", "no-store"],
      ["/bff/api/backups/backup-a", "GET", "same-origin", "no-store"],
      ["/bff/api/session", "GET", "same-origin", "no-store"]]);
    assert.match(visibleText(view.container), /backup-a/u);
    assert.match(visibleText(view.container), /tenant-a/u); assert.match(visibleText(view.container), /workspace-a/u);
    assert.match(visibleText(view.container), /버전\s*[:·]?\s*3|version\s*[:·]?\s*3/iu);
    assert.match(visibleText(view.container), /ready|준비|완료/u); noMutations(view);
  } finally { if (view) await view.cleanup(); globalThis.fetch = priorFetch; }
});

test("S42 restore_request_id는 정상 Session 확정 전 GET하지 않고 정확 request_id를 읽기 선택한다", async () => {
  const gate = deferred(), confirmation = deferred(); let sessions = 0;
  const p = port({ getSession: () => { p.calls.push(["session"]); return ++sessions === 1 ? gate.promise : confirmation.promise; } });
  const view = await render("?restore_request_id=restore-a", p.api);
  try {
    assert.deepEqual(p.calls, [["session"]]); assert.doesNotMatch(visibleText(view.container), /restore-a/u);
    const current = session({ workspace_id: "primary-other-workspace", recovery_operations: [] });
    await view.act(async () => gate.resolve(current));
    assert.deepEqual(p.calls, [["session"], ["restore", "restore-a"], ["session"]]);
    assert.doesNotMatch(visibleText(view.container), /restore-a/u, "대상 GET 성공만으로 현재 Session 재확인 전에 표시하지 않음");
    await view.act(async () => confirmation.resolve(current));
    assert.match(visibleText(view.container), /restore-a/u);
    assert.match(visibleText(view.container), /workspace-a/u, "실제 대상 Workspace를 표시함");
    assert.match(visibleText(view.container), /버전\s*[:·]?\s*6|version\s*[:·]?\s*6/iu);
    assert.match(visibleText(view.container), /completed|완료/u); noMutations(view, p.writes);
  } finally { gate.resolve(session()); confirmation.resolve(session()); await view.cleanup(); }
});

test("S42 표준 URL 인코딩된 안전 대상 ID도 정확한 원 ID로 조회한다", async () => {
  for (const [query, id, kind] of [["?backup_id=backup%3Aread", "backup:read", "backup"],
    ["?restore_request_id=restore%3Aread", "restore:read", "restore"]]) {
    const p = port({
      getBackup: async value => { p.calls.push(["backup", value]); return response(backup({ backup_id: id })); },
      getRestore: async value => { p.calls.push(["restore", value]); return response(restore({ request_id: id })); },
    });
    const view = await render(query, p.api);
    try {
      assert.deepEqual(p.calls, [["session"], [kind, id], ["session"]]);
      assert.match(visibleText(view.container), new RegExp(id, "u"));
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 중복·동시·미정의 query 및 오염 ID는 target GET을 보내지 않는다", async () => {
  for (const search of ["?backup_id=a&backup_id=b", "?backup_id=a&restore_request_id=b", "?unknown=a",
    "?backup_id=a&redirect=https://evil.example", "?restore_request_id=", "?backup_id=https://evil.example/a",
    "?backup_id=..", "?backup_id=a%2Fb", "?backup_id=a%5Cb", "?backup_id=a%0Ab", "?backup_id=%20a",
    "?restore_request_id=a%23b"]) {
    const p = port(), view = await render(search, p.api);
    try {
      assert.equal(p.calls.some(([method]) => method !== "session"), false, search);
      assert.doesNotMatch(visibleText(view.container), /backup-a|restore-a/u); noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 대상 404·401·403·서버 장애를 구별하고 오류 원문·이전 본문을 표시하지 않는다", async () => {
  for (const [status, code, label] of [[404, "RESOURCE_UNAVAILABLE", /찾을 수 없|존재하지 않|대상.*없|404/u],
    [401, "ACCESS_INVALID", /로그인|인증|401/u], [403, "FORBIDDEN", /권한|접근.*거부|403/u],
    [503, "RESOURCE_UNAVAILABLE", /서비스|일시|잠시|503/u],
    ["network", "RESOURCE_UNAVAILABLE", /서비스|일시|잠시|503/u]]) {
    const priorFetch = globalThis.fetch, calls = [];
    globalThis.fetch = async (url, init = {}) => {
      calls.push([url, init.method]);
      if (url === "/bff/api/session") return Response.json(session().payload);
      assert.equal(url, "/bff/api/backups/backup-a");
      if (status === "network") throw new Error(canary);
      // 실제 recoveryApi가 status를 잃으면 동일 코드의 404/503 표시 구별이 실패해야 한다.
      return Response.json({ error: { code, message: canary, details: { raw_text: canary }, trace_id: "trace-safe-error" } }, { status });
    };
    let view;
    try {
      view = await render("?backup_id=backup-a");
      assert.deepEqual(calls, [["/bff/api/session", "GET"], ["/bff/api/backups/backup-a", "GET"]]);
      assert.match(visibleText(view.container), label);
      assert.doesNotMatch(view.container.textContent, /S42_표시금지|manifest_digest/u); noMutations(view);
    } finally { if (view) await view.cleanup(); globalThis.fetch = priorFetch; }
  }
});

test("S42 ID·tenant·Workspace 불일치와 잘못된 DTO는 정상 대상 본문이 아니다", async () => {
  for (const data of [backup({ backup_id: "wrong-target" }), backup({ tenant_id: "other-tenant" }),
    backup({ workspace_id: "https://evil.example" }), backup({ state: canary }), backup({ version: "3" }), null]) {
    const p = port({ getBackup: async id => { p.calls.push(["backup", id]); return response(data); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls.slice(0, 2), [["session"], ["backup", "backup-a"]]);
      assert.match(visibleText(view.container), /RESOURCE_UNAVAILABLE|응답|서비스|확인할 수 없/u);
      assert.doesNotMatch(view.container.textContent, /wrong-target|other-tenant|https:\/\/evil\.example|S42_표시금지/u);
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 Session 거부·잘못된 scope는 GET 전에 차단하고 target 뒤 주체 변경도 표시하지 않는다", async () => {
  for (const getSession of [async () => session({ user_id: "" }), async () => session({ tenant_id: null }),
    async () => session({ workspace_id: "" }),
    async () => { throw failure(401, "ACCESS_INVALID"); }]) {
    const p = port({ getSession: async () => { p.calls.push(["session"]); return getSession(); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls, [["session"]]); assert.doesNotMatch(visibleText(view.container), /backup-a/u);
      noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
  for (const changes of [{ user_id: "reader-b" }, { tenant_id: "tenant-b" }, { session_id: "session-b" }]) {
    let reads = 0;
    const p = port({ getSession: async () => { p.calls.push(["session"]); return ++reads === 1 ? session() : session(changes); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls, [["session"], ["backup", "backup-a"], ["session"]]);
      assert.doesNotMatch(visibleText(view.container), /backup-a/u); noMutations(view, p.writes);
    } finally { await view.cleanup(); }
  }
});

test("S42 popstate의 새 선택 이후 늦은 이전 GET은 새 restore 본문을 덮지 않는다", async () => {
  const late = deferred(), p = port({ getBackup: id => { p.calls.push(["backup", id]); return late.promise; } });
  const view = await render("?backup_id=backup-a", p.api);
  try {
    await view.navigate("?restore_request_id=restore-a");
    assert.match(visibleText(view.container), /restore-a/u);
    await view.act(async () => late.resolve(response(backup())));
    assert.match(visibleText(view.container), /restore-a/u);
    assert.doesNotMatch(visibleText(view.container), /backup-a/u);
    assert.deepEqual(p.calls, [["session"], ["backup", "backup-a"], ["session"], ["restore", "restore-a"], ["session"]]);
    noMutations(view, p.writes);
  } finally { late.resolve(response(backup())); await view.cleanup(); }
});

test("S42 adapter 교체는 새 Session부터 확인하고 이전 adapter의 늦은 성공을 버린다", async () => {
  const late = deferred(), old = port({ getBackup: () => late.promise });
  const next = port({ getBackup: async id => { next.calls.push(["backup", id]); return response(backup({ state: "failed" })); } });
  const view = await render("?backup_id=backup-a", old.api);
  try {
    await view.rerender(next.api);
    assert.deepEqual(next.calls, [["session"], ["backup", "backup-a"], ["session"]]);
    assert.match(visibleText(view.container), /failed|실패/u);
    await view.act(async () => late.resolve(response(backup({ state: "ready", raw_text: canary }))));
    assert.match(visibleText(view.container), /failed|실패/u); noMutations(view, old.writes); noMutations(view, next.writes);
  } finally { late.resolve(response(backup())); await view.cleanup(); }
});

test("S42 pagehide 뒤 늦은 성공은 숨긴 본문을 되살리지 않으며 pageshow는 새 Session을 기다린다", async () => {
  const late = deferred(), gate = deferred(); let sessions = 0, targets = 0;
  const p = port({ getSession: () => ++sessions <= 2 ? Promise.resolve(session()) : gate.promise,
    getBackup: () => ++targets === 1 ? Promise.resolve(response(backup())) : late.promise });
  const view = await render("?backup_id=backup-a", p.api);
  try {
    assert.match(visibleText(view.container), /backup-a/u);
    await view.act(async () => {
      view.browser.dispatchEvent(new MinimalEvent("pagehide"));
      assert.doesNotMatch(visibleText(view.container), /backup-a/u, "flush 이전에도 숨김");
      view.browser.dispatchEvent(new MinimalEvent("pageshow"));
      assert.doesNotMatch(visibleText(view.container), /backup-a/u);
    });
    assert.equal(sessions, 3); assert.equal(targets, 1);
    await view.act(async () => gate.resolve(session()));
    await view.act(async () => view.browser.dispatchEvent(new MinimalEvent("pagehide")));
    await view.act(async () => late.resolve(response(backup())));
    assert.doesNotMatch(visibleText(view.container), /backup-a/u); noMutations(view, p.writes);
  } finally { gate.resolve(session()); late.resolve(response(backup())); await view.cleanup(); }
});

test("S42 visibility 재검증은 계정·tenant·Workspace 변경 즉시 이전 본문을 숨기고 새 scope 불일치를 거부한다", async () => {
  for (const changes of [{ user_id: "reader-b" }, { tenant_id: "tenant-b" },
    { session_id: "session-b" }, { workspace_id: "workspace-b" }]) {
    const gate = deferred(); let sessions = 0;
    const p = port({ getSession: () => ++sessions <= 2 ? Promise.resolve(session()) : gate.promise,
      getBackup: async id => { p.calls.push(["backup", id]); return response(backup()); } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.match(visibleText(view.container), /backup-a/u);
      await view.act(async () => {
        if (changes.session_id) {
          view.browser.dispatchEvent(new MinimalEvent("focus"));
          assert.doesNotMatch(visibleText(view.container), /backup-a/u, "focus 재확인 전 즉시 숨김");
        } else {
          view.dom.document.visibilityState = "hidden";
          view.dom.document.dispatchEvent(new MinimalEvent("visibilitychange"));
          assert.doesNotMatch(visibleText(view.container), /backup-a/u, "숨김 이벤트에서 즉시 폐기");
          view.dom.document.visibilityState = "visible";
          view.dom.document.dispatchEvent(new MinimalEvent("visibilitychange"));
        }
      });
      assert.equal(sessions, 3);
      assert.doesNotMatch(visibleText(view.container), /backup-a/u, "새 Session 미확정");
      // 계정만 바뀌면 서버 GET이 현재 권한을 결정한다. 여기서는 새 계정의 실제 거부 포트를 준다.
      if (changes.user_id || changes.session_id) p.api.getBackup = async () => { throw failure(403, "FORBIDDEN"); };
      await view.act(async () => gate.resolve(session(changes)));
      if (changes.workspace_id) {
        assert.match(visibleText(view.container), /backup-a/u, "primary Workspace 변경만으로 서버 허용 대상을 차단하지 않음");
        assert.match(visibleText(view.container), /workspace-a/u);
      } else assert.doesNotMatch(visibleText(view.container), /backup-a/u);
      noMutations(view, p.writes);
    } finally { gate.resolve(session(changes)); await view.cleanup(); }
  }
});

test("S42 unmount 뒤 Session·대상 지연 응답은 GET·본문 복원을 새로 만들지 않는다", async () => {
  for (const phase of ["session", "target"]) {
    const late = deferred(), p = port(phase === "session" ? {
      getSession: () => { p.calls.push(["session"]); return late.promise; },
    } : { getBackup: id => { p.calls.push(["backup", id]); return late.promise; } });
    const view = await render("?backup_id=backup-a", p.api);
    try {
      assert.deepEqual(p.calls, phase === "session" ? [["session"]] : [["session"], ["backup", "backup-a"]]);
      await view.unmount(); const before = [...p.calls];
      await view.act(async () => late.resolve(phase === "session" ? session() : response(backup())));
      assert.deepEqual(p.calls, before); assert.equal(view.container.textContent, ""); noMutations(view, p.writes);
    } finally { late.resolve(phase === "session" ? session() : response(backup())); await view.cleanup(); }
  }
});
