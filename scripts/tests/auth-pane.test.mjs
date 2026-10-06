import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

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
