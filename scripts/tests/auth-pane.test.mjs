import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import * as authEntry from "../../apps/web/lib/auth-api.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const entrySession = { user_id: "qa-admin", tenant_id: "tenant-qa", workspace_id: null,
  session_id: "session-qa", device_id: "device-qa", client_kind: "web", delivery: "same_origin_secure_cookie",
  expires_at: "2099-01-01T00:00:00Z", recovery_operations: [], password_change_required: false, is_system_admin: true };

test("S44 인증 진입은 엄격한 현재 세션으로 비밀번호·시스템·Notebook 경로를 결정한다", async () => {
  assert.equal(typeof authEntry.getCurrentAuthSession, "function");
  for (const [session, target] of [[entrySession, "/operations"],
    [{ ...entrySession, password_change_required: true }, "/password-change"],
    [{ ...entrySession, workspace_id: "workspace-qa" }, "/notebooks"],
    [{ ...entrySession, workspace_id: "workspace-qa", is_system_admin: false }, "/notebooks"]]) {
    const calls = [];
    const selected = await authEntry.getCurrentAuthSession({ fetchImpl: async (url, options) => {
      calls.push([url, options.credentials, options.cache]);
      return Response.json({ data: session, meta: { trace_id: "trace-qa" } });
    } });
    assert.equal(authEntry.authEntryPath(selected), target);
    assert.deepEqual(calls, [["/bff/api/session", "same-origin", "no-store"], ["/bff/api/session", "same-origin", "no-store"]]);
  }
});

test("S44 인증 진입은 비관리자 null·위조형태·철회·계정교체·로그인주체 불일치를 거부한다", async () => {
  assert.equal(typeof authEntry.getCurrentAuthSession, "function");
  for (const data of [{ ...entrySession, is_system_admin: false }, { ...entrySession, workspace_id: "" },
    { ...entrySession, is_system_admin: "true" }, { ...entrySession, recovery_operations: ["cloud_backup_list"] },
    { ...entrySession, secret: "private" }, { ...entrySession, session_id: "unsafe/id" }]) {
    await assert.rejects(authEntry.getCurrentAuthSession({ fetchImpl: async () => Response.json({ data, meta: { trace_id: "trace-qa" } }) }), /SESSION_RESPONSE_INVALID/u);
  }
  let reads = 0;
  await assert.rejects(authEntry.getCurrentAuthSession({ fetchImpl: async () => Response.json({
    data: { ...entrySession, session_id: ++reads === 1 ? "session-qa" : "other-session" }, meta: { trace_id: "trace-qa" } }) }), /SESSION_CHANGED/u);
  await assert.rejects(authEntry.getCurrentAuthSession({ expectedIdentity: { user_id: "other-user", tenant_id: "tenant-qa" },
    fetchImpl: async () => Response.json({ data: entrySession, meta: { trace_id: "trace-qa" } }) }), /SESSION_CHANGED/u);
  await assert.rejects(authEntry.getCurrentAuthSession({ fetchImpl: async () => Response.json({}, { status: 401 }) }), /AUTHENTICATION_REQUIRED/u);
});

async function renderEntry(entry, name, props, fetchImpl) {
  const output = await mkdtemp(path.join(tmpdir(), "daon-s44-auth-"));
  const dom = installMinimalDom(), priorFetch = globalThis.fetch, redirects = [];
  window.location = { assign: (value) => redirects.push(value), replace: (value) => redirects.push(value) };
  globalThis.fetch = fetchImpl;
  const { build } = await import("vite"), { act, createElement } = await import("react"), { createRoot } = await import("react-dom/client");
  let reactRoot;
  try {
    await build({ configFile: false, logLevel: "silent", root, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, entry), formats: ["es"], fileName: "entry" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: { react: import.meta.resolve("react") } } } } });
    const built = (await readdir(output)).find((file) => /^entry.*\.m?js$/u.test(file));
    const module = await import(`${pathToFileURL(path.join(output, built)).href}?v=${Date.now()}`);
    const container = document.createElement("div"); document.body.appendChild(container); reactRoot = createRoot(container);
    const cleanup = async () => { await act(async () => reactRoot.unmount()); globalThis.fetch = priorFetch; dom.restore(); await rm(output, { recursive: true, force: true }); };
    await act(async () => { reactRoot.render(createElement(module[name], props)); await Promise.resolve(); });
    return { container, redirects, act, cleanup, unmount: () => act(async () => reactRoot.unmount()) };
  } catch (error) {
    if (reactRoot) await act(async () => reactRoot.unmount());
    globalThis.fetch = priorFetch; dom.restore(); await rm(output, { recursive: true, force: true }); throw error;
  }
}

test("S44 세 인증 화면은 정상 세션으로 시스템 홈과 강제 비밀번호 변경을 선택한다", async () => {
  for (const [entry, name, props, click] of [
    ["apps/web/lib/auth-pane.jsx", "AuthPane", {}, true],
    ["apps/web/components/auth-landing.jsx", "AuthLanding", { hasSessionCookie: true }, false],
    ["apps/web/components/password-change-workspace.jsx", "PasswordChangeWorkspace", {}, false],
  ]) {
    for (const forced of [false, true]) {
      const data = { ...entrySession, password_change_required: forced };
      const view = await renderEntry(entry, name, props, async (url) => Response.json(url.endsWith("/login")
        ? { data: { user_id: data.user_id, tenant_id: data.tenant_id, workspace_id: null } }
        : { data, meta: { trace_id: "trace-qa" } }));
      try {
        if (click) await view.act(async () => { buttonByText(view.container, "로그인").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
        if (name === "PasswordChangeWorkspace" && forced) {
          assert.deepEqual(view.redirects, []); assert.match(view.container.textContent, /새 비밀번호 설정/u);
        } else assert.deepEqual(view.redirects, [forced ? "/password-change" : "/operations"], name);
      } finally { await view.cleanup(); }
    }
  }
});

test("S44 인증 화면은 unmount 뒤 도착한 세션으로 이동하지 않는다", async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const view = await renderEntry("apps/web/components/auth-landing.jsx", "AuthLanding", { hasSessionCookie: true }, async () => {
    await pending; return Response.json({ data: entrySession, meta: { trace_id: "trace-qa" } });
  });
  try {
    await view.unmount();
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.deepEqual(view.redirects, []);
  } finally { await view.cleanup(); }
});

test("S44 pending 로그인의 pagehide는 늦은 결과를 버리고 BFCache 복귀에 현재 세션을 재검사한다", async () => {
  let release, sessionReads = 0;
  const pending = new Promise((resolve) => { release = resolve; });
  const view = await renderEntry("apps/web/lib/auth-pane.jsx", "AuthPane", {}, async (url) => {
    if (url.endsWith("/login")) {
      await pending;
      return Response.json({ data: { user_id: entrySession.user_id, tenant_id: entrySession.tenant_id, workspace_id: null } });
    }
    sessionReads += 1;
    return Response.json({ data: entrySession, meta: { trace_id: "trace-qa" } });
  });
  try {
    await view.act(async () => { buttonByText(view.container, "로그인").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.equal(buttonByText(view.container, "로그인").disabled, true);
    await view.act(async () => { window.dispatchEvent?.(new MinimalEvent("pagehide")); document.dispatchEvent(new MinimalEvent("pagehide")); });
    await view.act(async () => { release(); await pending; await Promise.resolve(); });
    assert.deepEqual(view.redirects, []); assert.equal(sessionReads, 0);
    assert.equal(buttonByText(view.container, "로그인").disabled, false);
    const show = new MinimalEvent("pageshow"); show.persisted = true;
    await view.act(async () => { document.dispatchEvent(show); await Promise.resolve(); });
    assert.equal(sessionReads, 2);
    assert.deepEqual(view.redirects, ["/operations"]);
  } finally { await view.cleanup(); }
});

test("기존 계정은 로그인에서 이메일 인증 폼에 진입하고 토큰 확인·재전송·전환 정리를 수행한다", async () => {
  const output = await mkdtemp(path.join(tmpdir(), "daon-auth-pane-entry-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  let reactRoot;
  const calls = [];
  try {
    const { build } = await import("vite");
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    globalThis.fetch = async (url, init) => {
      calls.push({ url, body: JSON.parse(init.body) });
      return Response.json({ data: {} });
    };
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/lib/auth-pane.jsx"), formats: ["es"], fileName: "auth-pane" },
      rollupOptions: {
        external: ["react", "react-dom", "react-dom/client"],
        output: { paths: { react: import.meta.resolve("react") } },
      },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("auth-pane") && /\.m?js$/u.test(name));
    assert.ok(entry);
    const { AuthPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?entry=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    const input = (name) => findElements(container, (node) => node.tagName === "INPUT" && (node.getAttribute("name") ?? node.name) === name)[0];
    const type = async (name, value) => {
      const field = input(name);
      assert.ok(field, `${name} input이 필요하다`);
      field.value = value;
      await act(async () => { field.dispatchEvent(new MinimalEvent("input")); });
    };
    const click = async (label) => {
      const button = buttonByText(container, label);
      assert.ok(button, `${label} button이 필요하다`);
      await act(async () => { button.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    };
    await act(async () => { reactRoot.render(createElement(AuthPane)); });
    await type("login-id", "pending-qa");
    await type("password", "synthetic-password");
    await click("이메일 인증하기");
    assert.ok(input("verification-token"));
    assert.equal(input("password"), undefined);
    await type("verification-token", "synthetic-token");
    await click("이메일 인증");
    assert.deepEqual(calls.at(-1), { url: "/bff/api/auth/verify-email", body: { token: "synthetic-token" } });
    assert.equal(input("verification-token").value, "");
    await click("인증 재전송");
    assert.deepEqual(calls.at(-1), { url: "/bff/api/auth/resend-verification", body: { identifier: "pending-qa" } });
    await type("verification-token", "discard-this-token");
    await click("로그인으로 돌아가기");
    assert.equal(input("password").value, "");
    await click("이메일 인증하기");
    assert.equal(input("verification-token").value, "");
    assert.equal(calls.length, 2);
  } finally {
    if (reactRoot) {
      const { act } = await import("react");
      await act(async () => { reactRoot.unmount(); });
    }
    globalThis.fetch = priorFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});

test("가입은 token을 제외한 정확한 세 필드만 전송한다", async () => {
  const source = await readFile(path.join(root, "apps/web/lib/auth-pane.jsx"), "utf8");

  assert.match(
    source,
    /run\("signup",\s*\{\s*login_id:\s*form\.login_id,\s*email:\s*form\.email,\s*password:\s*form\.password\s*\}/s,
  );
  assert.doesNotMatch(source, /run\("signup",\s*form\b/);
});

test("로그인·이메일 인증·비밀번호 재설정 payload 계약을 보존한다", async () => {
  const source = await readFile(path.join(root, "apps/web/lib/auth-pane.jsx"), "utf8");

  assert.match(source, /run\("login",\s*\{\s*login_id:\s*form\.login_id,\s*password:\s*form\.password\s*\}/s);
  assert.match(source, /run\("verify-email",\s*\{\s*token:\s*form\.token\s*\}/s);
  assert.match(source, /run\("resend-verification",\s*\{\s*identifier:\s*form\.login_id\s*\|\|\s*form\.email\s*\}/s);
  assert.match(source, /run\("password-reset\/request",\s*\{\s*identifier:\s*form\.login_id\s*\|\|\s*form\.email\s*\}/s);
  assert.match(source, /run\("password-reset\/confirm",\s*\{\s*token:\s*form\.token,\s*new_password:\s*form\.password\s*\}/s);
});

test("비밀번호 입력은 서버 정책과 같은 최소 12자 HTML 제약을 제공한다", async () => {
  const source = await readFile(path.join(root, "apps/web/lib/auth-pane.jsx"), "utf8");

  assert.match(source, /type="password"[^>]*minLength=\{12\}/);
});

test("인증 화면은 공식 오류 코드만 표시하고 임의 서버 오류 내용은 숨긴다", async () => {
  const output = await mkdtemp(path.join(tmpdir(), "daon-auth-pane-errors-"));
  const dom = installMinimalDom();
  const priorFetch = globalThis.fetch;
  let reactRoot;
  try {
    const { build } = await import("vite");
    const { act, createElement } = await import("react");
    const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root, build: {
      outDir: output, emptyOutDir: false,
      lib: { entry: path.join(root, "apps/web/lib/auth-pane.jsx"), formats: ["es"], fileName: "auth-pane" },
      rollupOptions: { external: ["react", "react-dom", "react-dom/client"], output: { paths: { react: import.meta.resolve("react") } } },
    } });
    const entry = (await readdir(output)).find((name) => name.startsWith("auth-pane") && /\.m?js$/u.test(name));
    assert.ok(entry);
    const { AuthPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?errors=${Date.now()}`);
    const container = dom.document.createElement("div");
    dom.document.body.appendChild(container);
    reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(AuthPane)); });
    const login = buttonByText(container, "로그인");
    assert.ok(login);
    const submit = async (fetchImpl) => {
      globalThis.fetch = fetchImpl;
      await act(async () => { login.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
      return findElements(container, (node) => node.getAttribute?.("role") === "status")[0]?.textContent;
    };
    for (const code of ["AUTHENTICATION_REQUIRED", "EMAIL_TOKEN_INVALID", "PASSWORD_POLICY_FAILED", "PASSWORD_RESET_TOKEN_INVALID"]) {
      const message = await submit(async () => Response.json({ error: { code } }, { status: 400 }));
      assert.equal(message, code);
    }
    const generic = "처리 실패: 요청을 완료하지 못했습니다.";
    assert.equal(await submit(async () => Response.json({ error: { code: "http://internal:8000 secret stack" } }, { status: 500 })), generic);
    assert.equal(await submit(async () => { throw new Error("http://internal:8000 secret stack"); }), generic);
  } finally {
    if (reactRoot) {
      const { act } = await import("react");
      await act(async () => { reactRoot.unmount(); });
    }
    globalThis.fetch = priorFetch;
    dom.restore();
    await rm(output, { recursive: true, force: true });
  }
});


test("UI55 로그인 시안은 전용 카드·제품명·세로 입력·로그인과 하단 링크를 분리하고 가입에는 적용하지 않는다", async()=>{
  const view=await renderEntry('apps/web/lib/auth-pane.jsx','AuthPane',{},async()=>{throw Error('UNEXPECTED_AUTH_CALL');});
  try{
    const card=findElements(view.container,n=>n.tagName==='SECTION')[0];
    assert.ok(card.getAttribute('class').split(' ').includes('daon-auth-pane-login'));
    assert.equal(card.textContent.includes('Daon-User'),true);
    const inputs=findElements(card,n=>n.tagName==='INPUT');const props=n=>n[Object.keys(n).find(k=>k.startsWith('__reactProps'))];
    assert.deepEqual(inputs.map(n=>props(n).name),['login-id','password']);
    assert.equal(props(inputs[1]).type,'password');assert.equal(props(inputs[0]).autoComplete,'username');
    const links=findElements(card,n=>n.getAttribute('class')==='daon-auth-login-links')[0];assert.ok(links);
    assert.deepEqual(findElements(links,n=>n.tagName==='BUTTON').map(n=>n.textContent),['가입하기','이메일 인증하기','비밀번호를 잊으셨나요?']);
    await view.act(async()=>{buttonByText(view.container,'가입하기').dispatchEvent(new MinimalEvent('click'));});
    assert.equal(card.getAttribute('class'),'daon-auth-pane');assert.equal(card.textContent.includes('Daon-User'),false);
  }finally{await view.cleanup();}
});


test("UI55 로그인 submit 버튼의 클릭과 Enter submit이 이어져도 인증 요청은 한 번이다",async()=>{
  let release;const held=new Promise(resolve=>{release=resolve;});let logins=0;
  const view=await renderEntry('apps/web/lib/auth-pane.jsx','AuthPane',{},async url=>{
    if(url.endsWith('/login')){logins+=1;await held;return Response.json({data:{user_id:entrySession.user_id,tenant_id:entrySession.tenant_id,workspace_id:null}});}
    return Response.json({data:entrySession,meta:{trace_id:'trace-ui55'}});
  });
  try{
    const button=buttonByText(view.container,'로그인');const props=n=>n[Object.keys(n).find(k=>k.startsWith('__reactProps'))];
    assert.equal(props(button).type,'submit');
    await view.act(async()=>{button.dispatchEvent(new MinimalEvent('click'));findElements(view.container,n=>n.tagName==='FORM')[0].dispatchEvent(new MinimalEvent('submit'));await Promise.resolve();});
    assert.equal(logins,1);assert.equal(button.disabled,true);
    await view.act(async()=>{release();await held;await Promise.resolve();});assert.equal(logins,1);assert.deepEqual(view.redirects,['/operations']);
  }finally{release();await view.cleanup();}
});
