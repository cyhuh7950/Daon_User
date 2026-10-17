import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const require = createRequire(import.meta.url);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const item = (id = "notice-a") => ({ id, workspace_id: "workspace-a", recipient_id: "member-a", kind: "review", severity: "info",
  title: `비공개 ${id}`, summary: "합성 업무 알림", source_event_id: "event-a", resource_type: "output_version", resource_id: "version-a",
  deep_link: "/workspaces/workspace-a", audit_event_id: "audit-a", trace_id: "trace-a", delivery_state: "delivered", read_state: "unread",
  created_at: "2026-10-17T00:00:00+00:00", delivered_at: "2026-10-17T00:00:00+00:00", read_at: null, version: 1 });
const listed = (items = [item()]) => ({ ok: true, status: 200, payload: { data: { items, unread_count: items.filter(row => row.read_state !== "read").length, next_cursor: null }, meta: { trace_id: "trace-a" } } });
const inboxItem = { request_id: "new-inbox", request_kind: "review", status: "pending", workspace_id: "workspace-a", actor_id: "member-a", due_at: null,
  resource_type: "output_version", resource_id: "version-a", deep_link: "/workspaces/workspace-a" };

async function render(api, mode = "notifications") {
  const root = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(tmpdir(), "daon-s42-u1-react-"));
  const dom = installMinimalDom();
  const { createElement, act } = await import("react"), { createRoot } = await import("react-dom/client");
  let reactRoot;
  const cleanup = async () => { try { if (reactRoot) await act(async () => reactRoot.unmount()); } finally { dom.restore(); await rm(output, { recursive: true, force: true }); } };
  try {
    const { build } = await import("vite");
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "packages/ui/src/notification-inbox-pane.jsx"), formats: ["es"], fileName: "notification-inbox" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: id => pathToFileURL(require.resolve(id)).href } } } });
    const built = (await readdir(output)).find(file => /\.m?js$/u.test(file));
    const module = await import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    const rerender = async (nextApi = api, nextMode = mode) => { await act(async () => { reactRoot.render(createElement(module.NotificationInboxWorkspace, { api: nextApi, mode: nextMode })); }); };
    await rerender();
    return { container, dom, act, cleanup, rerender };
  } catch (error) { await cleanup(); throw error; }
}
async function click(view, label, twice = false) {
  const button = buttonByText(view.container, label); assert.ok(button, label);
  await view.act(async () => { button.dispatchEvent(new MinimalEvent("click")); if (twice) button.dispatchEvent(new MinimalEvent("click")); });
}

test("S42 실제 React는 정상 목록·미읽음·읽음 후 재조회를 보존한다", async () => {
  let read = false, reads = 0;
  const view = await render({ list: async mode => { assert.equal(mode, "notifications"); reads++; return listed([{ ...item(), read_state: read ? "read" : "unread", version: read ? 2 : 1, read_at: read ? "2026-10-17T01:00:00+00:00" : null }]); },
    markRead: async row => { assert.equal(row.id, "notice-a"); read = true; return { ok: true, status: 200 }; } });
  try { assert.match(view.container.textContent, /비공개 notice-a/u); await click(view, "읽음 처리"); assert.ok(buttonByText(view.container, "읽음").disabled); assert.equal(reads, 2); }
  finally { await view.cleanup(); }
});

test("S42 같은 flush의 읽음 두 번 클릭은 실제 adapter 호출을 한 번만 전송한다", async () => {
  const pending = deferred(); let writes = 0;
  const view = await render({ list: async () => listed(), markRead: async () => { writes++; return pending.promise; } });
  try { await click(view, "읽음 처리", true); assert.equal(writes, 1); }
  finally { await view.act(async () => { pending.resolve({ ok: true }); }); await view.cleanup(); }
});

test("S42 승인된 원업무 링크는 실제 React에서 정확한 상대경로로 열린다", async () => {
  const links = [
    "/notebooks/notebook-001?output_version_id=version-001",
    "/notebooks/notebook-001?source_version_id=source-version-001",
    "/notebooks/notebook-001?run_id=run-001",
    "/settings/account",
  ];
  const rows = links.map((deep_link, index) => ({ ...item(`target-${index}`), deep_link }));
  const view = await render({ list: async () => listed(rows), markRead: async () => { throw new Error("읽음 시험 아님"); } });
  try {
    const anchors = findElements(view.container, node => node.tagName === "A" && node.textContent === "원 요청 열기");
    assert.deepEqual(anchors.map(node => node.getAttribute("href")), links);
    assert.doesNotMatch(view.container.textContent, /CURRENT_ACCESS_DENIED/u);
  } finally { await view.cleanup(); }
});

test("S42 새 원업무 링크의 중복·오염 대상은 React에서 클릭 링크로 만들지 않는다", async () => {
  const links = [
    "/notebooks/notebook-001",
    "/notebooks/notebook-001?run_id=run-001&run_id=run-002",
    "/notebooks/notebook-001?run_id=run-001&source_version_id=version-001",
    "/notebooks/notebook-001?run_id=run-001&redirect=https://evil.example",
    "/notebooks/notebook-001?unknown=run-001",
    "/notebooks/notebook-001?run_id=",
    "/notebooks/notebook-001?run_id=run-001#other",
    "/notebooks/notebook-001?run_id=run%0A001",
    "/notebooks/notebook-001%2Fother?run_id=run-001",
    "/notebooks/../settings?run_id=run-001",
    "/settings/account?redirect=https://evil.example",
    "/settings/account#other",
    "/settings/account/other",
  ];
  const rows = links.map((deep_link, index) => ({ ...item(`unsafe-${index}`), deep_link }));
  const view = await render({ list: async () => listed(rows), markRead: async () => { throw new Error("읽음 시험 아님"); } });
  try {
    assert.equal(findElements(view.container, node => node.tagName === "A" && node.textContent === "원 요청 열기").length, 0);
    for (const row of rows) assert.ok(view.container.textContent.includes(row.title));
    assert.match(view.container.textContent, /CURRENT_ACCESS_DENIED/u);
  } finally { await view.cleanup(); }
});

for (const status of [401, 403, 412, 503, "network"]) {
  test(`S42 읽음 실패 ${status}는 이전 본문·링크·미읽음 수를 폐기한다`, async () => {
    const view = await render({ list: async () => listed(), markRead: async () => { if (status === "network") throw new Error("민감한 내부 실패 원문"); return { ok: false, status }; } });
    try {
      await click(view, "읽음 처리");
      assert.doesNotMatch(view.container.textContent, /비공개 notice-a|민감한 내부 실패 원문/u);
      assert.match(view.container.textContent, /미읽음 0/u);
      assert.equal(findElements(view.container, node => node.tagName === "A" && node.textContent === "원 요청 열기").length, 0);
    } finally { await view.cleanup(); }
  });
}

test("S42 api 교체 뒤 늦은 이전 목록 성공은 새 본문을 덮지 않는다", async () => {
  const late = deferred();
  const view = await render({ list: () => late.promise });
  try {
    await view.rerender({ list: async () => listed([item("new-api")]) });
    await view.act(async () => { late.resolve(listed([item("old-api")])); });
    assert.match(view.container.textContent, /비공개 new-api/u); assert.doesNotMatch(view.container.textContent, /비공개 old-api/u);
  } finally { late.resolve(listed([])); await view.cleanup(); }
});

test("S42 mode 변경 로딩은 이전 본문을 즉시 지우고 늦은 읽음의 재조회를 무시한다", async () => {
  const write = deferred(), next = deferred(); const modes = [];
  const api = { list: async mode => { modes.push(mode); return mode === "notifications" ? listed() : next.promise; }, markRead: () => write.promise };
  const view = await render(api);
  try {
    await click(view, "읽음 처리"); await view.rerender(api, "inbox");
    assert.doesNotMatch(view.container.textContent, /비공개 notice-a/u); assert.match(view.container.textContent, /미읽음 0/u);
    await view.act(async () => { next.resolve({ ok: true, status: 200, payload: { data: { items: [inboxItem], next_cursor: null }, meta: { trace_id: "trace-a" } } }); });
    await view.act(async () => { write.resolve({ ok: true }); });
    assert.deepEqual(modes, ["notifications", "inbox"]);
    assert.match(view.container.textContent, /review · pending/u); assert.doesNotMatch(view.container.textContent, /비공개 notice-a/u);
  } finally { write.resolve({ ok: true }); next.resolve(listed([])); await view.cleanup(); }
});

test("S42 pagehide는 flush 전 DOM을 차단하고 늦은 목록 성공을 복원하지 않는다", async () => {
  const late = deferred(); let reads = 0;
  const view = await render({ list: () => ++reads === 1 ? Promise.resolve(listed()) : late.promise });
  try {
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
    await view.act(async () => {
      view.dom.document.dispatchEvent(new MinimalEvent("pagehide"));
      assert.equal(view.container.firstChild.hidden, true); assert.equal(view.container.firstChild.inert, true);
    });
    await view.act(async () => { late.resolve(listed([item("late-private")])); });
    assert.doesNotMatch(view.container.textContent, /비공개 notice-a|late-private/u);
  } finally { late.resolve(listed([])); await view.cleanup(); }
});

test("S42 암호 API 부재의 읽음 실패는 throw 원문 대신 안전 상태만 표시한다", async () => {
  const view = await render({ list: async () => listed(), markRead: async () => { throw new Error("SECURE_RANDOM_UNAVAILABLE"); } });
  try { await click(view, "읽음 처리"); assert.doesNotMatch(view.container.textContent, /비공개 notice-a|SECURE_RANDOM_UNAVAILABLE/u); assert.match(view.container.textContent, /서비스를 사용할 수 없습니다/u); }
  finally { await view.cleanup(); }
});

test("S42 조회 재검증 실패는 이전 본문·미읽음 수를 남기지 않는다", async () => {
  let reads = 0;
  const view = await render({ list: async () => ++reads === 1 ? listed() : { ok: false, status: 401 } });
  try {
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
    assert.doesNotMatch(view.container.textContent, /비공개 notice-a/u); assert.match(view.container.textContent, /미읽음 0/u);
    assert.match(view.container.textContent, /현재 권한/u);
  } finally { await view.cleanup(); }
});

test("S42 pagehide 뒤 같은 flush pageshow도 이전 DOM을 조회 완료 전에 열지 않는다", async () => {
  let reads = 0; const late = deferred();
  const view = await render({ list: () => ++reads === 1 ? Promise.resolve(listed()) : late.promise });
  try {
    await view.act(async () => {
      view.dom.document.dispatchEvent(new MinimalEvent("pagehide"));
      view.dom.document.dispatchEvent(new MinimalEvent("pageshow"));
      assert.equal(view.container.firstChild.hidden, true);
      assert.equal(view.container.firstChild.inert, true);
    });
    assert.doesNotMatch(view.container.textContent, /비공개 notice-a/u);
    await view.act(async () => { late.resolve(listed([item("fresh-page")])); });
    assert.equal(view.container.firstChild.hidden, false); assert.equal(view.container.firstChild.inert, false);
    assert.match(view.container.textContent, /비공개 fresh-page/u);
  } finally { late.resolve(listed([])); await view.cleanup(); }
});

test("S42 늦은 이전 읽음 실패는 교체된 api의 새 본문을 폐기하지 않는다", async () => {
  const late = deferred();
  const view = await render({ list: async () => listed(), markRead: () => late.promise });
  try {
    await click(view, "읽음 처리"); await view.rerender({ list: async () => listed([item("fresh-api")]) });
    await view.act(async () => { late.reject(new Error("이전 api의 실패")); });
    assert.match(view.container.textContent, /비공개 fresh-api/u); assert.doesNotMatch(view.container.textContent, /이전 api의 실패/u);
  } finally { late.resolve({ ok: true }); await view.cleanup(); }
});

for (const [name, payload] of [
  ["payload 누락", null], ["items 누락", { data: { unread_count: 0, next_cursor: null } }],
  ["items 객체", { data: { items: {}, unread_count: 0, next_cursor: null } }],
  ["미읽음 음수", { data: { items: [], unread_count: -1, next_cursor: null } }],
  ["미읽음 누락", { data: { items: [], next_cursor: null } }],
  ["제목 객체", { data: { items: [{ ...item(), title: { private: "invalid" } }], unread_count: 1, next_cursor: null } }],
  ["대상 id 누락", { data: { items: [{ ...item(), id: undefined }], unread_count: 1, next_cursor: null } }],
  ["읽음 version 문자열", { data: { items: [{ ...item(), version: "1" }], unread_count: 1, next_cursor: null } }],
  ["읽음 상태 오염", { data: { items: [{ ...item(), read_state: "unknown" }], unread_count: 1, next_cursor: null } }],
  ["생성시각 오류", { data: { items: [{ ...item(), created_at: "bad-date" }], unread_count: 1, next_cursor: null } }],
]) {
  test(`S42 malformed 성공 ${name}는 빈 목록 성공 대신 안전 오류이며 이전 본문을 폐기한다`, async () => {
    let reads = 0;
    const responsePayload = payload === null ? null : { ...payload, meta: { trace_id: "trace-valid-envelope" } };
    const view = await render({ list: async () => ++reads === 1 ? listed() : { ok: true, status: 200, payload: responsePayload } });
    try {
      await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
      assert.equal(view.container.firstChild.getAttribute("data-view-state"), "error");
      assert.doesNotMatch(view.container.textContent, /비공개 notice-a|표시할 항목이 없습니다/u); assert.match(view.container.textContent, /미읽음 0/u);
    } finally { await view.cleanup(); }
  });
}

test("S42 실제 빈 목록 응답은 오류가 아닌 빈 상태로 표시한다", async () => {
  const view = await render({ list: async () => listed([]) });
  try { assert.equal(view.container.firstChild.getAttribute("data-view-state"), "empty"); assert.match(view.container.textContent, /표시할 항목이 없습니다/u); }
  finally { await view.cleanup(); }
});

test("S42 현재 OpaqueId 계약의 식별자를 임의 정규식이나 128자 제한으로 축소하지 않는다", async () => {
  const row = { ...item(`opaque/${"n".repeat(150)}`), title: "불투명 알림" };
  const view = await render({ list: async () => listed([row]) });
  try { assert.equal(view.container.firstChild.getAttribute("data-view-state"), "ready"); assert.match(view.container.textContent, /불투명 알림/u); }
  finally { await view.cleanup(); }
});

test("S42 새 load 직후 flush 전의 이전 읽음 버튼도 전송할 수 없다", async () => {
  const late = deferred(); let reads = 0, writes = 0;
  const view = await render({ list: () => ++reads === 1 ? Promise.resolve(listed()) : late.promise, markRead: async () => { writes++; return { ok: false, status: 403 }; } });
  try {
    const button = buttonByText(view.container, "읽음 처리");
    await view.act(async () => {
      view.dom.document.dispatchEvent(new MinimalEvent("focus"));
      button.dispatchEvent(new MinimalEvent("click"));
    });
    assert.equal(writes, 0);
    await view.act(async () => { late.resolve(listed([])); });
    assert.doesNotMatch(view.container.textContent, /비공개 notice-a/u);
  } finally { late.resolve(listed([])); await view.cleanup(); }
});

test("S42 같은 scope PATCH를 가로지른 commit 전 GET은 최종 확인이 아니며 완료 후 read v2를 조회한다", async () => {
  const patch = deferred(), confirm = deferred(); let reads = 0, writes = 0;
  const api = { list: async () => ++reads <= 2 ? listed() : confirm.promise,
    markRead: async () => { writes++; return patch.promise; } };
  const view = await render(api);
  try {
    await click(view, "읽음 처리");
    await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
    assert.equal(reads, 2); assert.equal(writes, 1);
    assert.equal(Boolean(buttonByText(view.container, "읽음 처리")), false);
    await view.act(async () => { patch.resolve({ ok: true, status: 200 }); });
    assert.equal(reads, 3);
    assert.equal(Boolean(buttonByText(view.container, "읽음 처리")), false);
    await view.act(async () => { confirm.resolve(listed([{ ...item(), read_state: "read", read_at: "2026-10-17T01:00:00+00:00", version: 2 }])); });
    assert.ok(buttonByText(view.container, "읽음").disabled); assert.match(view.container.textContent, /미읽음 0/u);
    assert.equal(writes, 1);
  } finally { patch.resolve({ ok: true }); confirm.resolve(listed([])); await view.cleanup(); }
});

test("S42 commit 전 GET이 PATCH 완료 뒤 늦게 도착해도 확인한 read v2를 덮지 않는다", async () => {
  const patch = deferred(), crossing = deferred(); let reads = 0;
  const view = await render({ list: async () => { reads++; if (reads === 2) return crossing.promise; return listed([{ ...item(), read_state: reads > 2 ? "read" : "unread", version: reads > 2 ? 2 : 1 }]); }, markRead: () => patch.promise });
  try {
    await click(view, "읽음 처리"); await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
    await view.act(async () => { patch.resolve({ ok: true, status: 200 }); }); assert.equal(reads, 3);
    await view.act(async () => { crossing.resolve(listed()); });
    assert.ok(buttonByText(view.container, "읽음").disabled); assert.match(view.container.textContent, /미읽음 0/u);
  } finally { crossing.resolve(listed([])); patch.resolve({ ok: true }); await view.cleanup(); }
});

for (const outcome of ["network", 403, 503]) {
  test(`S42 같은 scope 경합 뒤 PATCH ${outcome}는 불확정 안내를 유지하며 새 PATCH를 자동 전송하지 않는다`, async () => {
    const patch = deferred(); let reads = 0, writes = 0;
    const view = await render({ list: async () => { reads++; return listed(); }, markRead: () => { writes++; return patch.promise; } });
    try {
      await click(view, "읽음 처리"); await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
      await view.act(async () => { if (outcome === "network") patch.reject(new TypeError("응답 유실")); else patch.resolve({ ok: false, status: outcome }); });
      assert.match(view.container.textContent, /읽음.*결과.*미확정/u); assert.equal(writes, 1);
      await view.act(async () => { view.dom.document.dispatchEvent(new MinimalEvent("focus")); });
      assert.match(view.container.textContent, /읽음.*결과.*미확정/u);
      const button = buttonByText(view.container, "읽음 처리"); if (button) assert.ok(button.disabled);
      assert.equal(writes, 1); assert.equal(reads, 3);
    } finally { patch.resolve({ ok: true }); await view.cleanup(); }
  });
}

test("S42 미확정 읽음도 명시 GET의 read v2 확인 후에만 해제하고 새 PATCH는 보내지 않는다", async () => {
  let reads = 0, writes = 0;
  const view = await render({ list: async () => ++reads === 1 ? listed() : listed([{ ...item(), read_state: "read", version: 2, read_at: "2026-10-17T01:00:00+00:00" }]),
    markRead: async () => { writes++; throw new TypeError("저장 뒤 응답 유실"); } });
  try {
    await click(view, "읽음 처리"); assert.match(view.container.textContent, /읽음.*결과.*미확정/u);
    await click(view, "다시 시도");
    assert.ok(buttonByText(view.container, "읽음").disabled); assert.doesNotMatch(view.container.textContent, /결과 미확정/u);
    assert.equal(writes, 1); assert.equal(reads, 2);
  } finally { await view.cleanup(); }
});

for (const outcome of ["success", "failure"]) {
  test(`S42 api 실제 교체 후 이전 PATCH ${outcome}는 새 scope의 읽음·오류·확인 조회에 영향이 없다`, async () => {
    const old = deferred(); let newReads = 0, newWrites = 0;
    const view = await render({ list: async () => listed(), markRead: () => old.promise });
    const nextApi = { list: async () => { newReads++; return listed([{ ...item("new-scope"), read_state: newWrites ? "read" : "unread", version: newWrites ? 2 : 1 }]); },
      markRead: async () => { newWrites++; return { ok: true, status: 200 }; } };
    try {
      await click(view, "읽음 처리"); await view.rerender(nextApi);
      await click(view, "읽음 처리"); assert.equal(newWrites, 1); assert.equal(newReads, 2);
      await view.act(async () => { if (outcome === "success") old.resolve({ ok: true, status: 200 }); else old.reject(new TypeError("이전 scope 응답 유실")); });
      assert.equal(newReads, 2); assert.ok(buttonByText(view.container, "읽음").disabled);
      assert.match(view.container.textContent, /비공개 new-scope/u); assert.doesNotMatch(view.container.textContent, /미확정|이전 scope/u);
    } finally { old.resolve({ ok: true }); await view.cleanup(); }
  });
}
