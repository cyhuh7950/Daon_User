import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { logoutCurrentSession } from "../../apps/web/lib/auth-api.js";

const read = (path) => readFile(path, "utf8");

test("login success enters Notebook Home without selecting a Notebook", async () => {
  const auth = await read("apps/web/lib/auth-pane.jsx");
  assert.match(auth, /window\.location\.assign\("\/notebooks"\)/u);
  assert.doesNotMatch(auth, /window\.location\.assign\(`\/workspaces\//u);
});

test("root landing sends an existing authenticated session to Notebook Home", async () => {
  const page = await read("apps/web/app/page.jsx");
  const landing = await read("apps/web/components/auth-landing.jsx");
  assert.match(page, /AuthLanding/u);
  assert.match(landing, /getCurrentNotebookSession/u);
  assert.match(landing, /window\.location\.replace\("\/notebooks"\)/u);
  assert.match(landing, /AUTHENTICATION_REQUIRED/u);
  assert.doesNotMatch(landing, /localStorage|sessionStorage|document\.cookie|https?:\/\/|localhost|127\.0\.0\.1/iu);
});

test("production exposes authenticated Notebook Home but no evidence harness route", async () => {
  const page = await read("apps/web/app/notebooks/page.jsx");
  const client = await read("apps/web/components/notebook-home-workspace.jsx");
  assert.match(page, /NotebookHomeWorkspace/u);
  assert.match(client, /getCurrentNotebookSession/u);
  assert.match(client, /listNotebooks/u);
  assert.match(client, /window\.location\.replace\("\/"\)/u);
  assert.match(client, /window\.location\.assign\(`\/notebooks\/\$\{encodeURIComponent\(notebookId\)\}`\)/u);
  assert.doesNotMatch(`${page}\n${client}`, /test-harness|fixture|localhost|127\.0\.0\.1/iu);
});

test("selected Notebook production route assembles only the approved scoped Context adapter", async () => {
  const page = await read("apps/web/app/notebooks/[notebook_id]/page.jsx");
  const client = await read("apps/web/components/notebook-product-workspace.jsx");
  assert.match(page, /NotebookProductWorkspace/u);
  assert.match(client, /getCurrentNotebookSession/u);
  assert.match(client, /getNotebookContext/u);
  assert.match(client, /createNotebookContextWorkspaceAdapter/u);
  assert.match(client, /ActualWorkspace/u);
  assert.match(client, /window\.location\.replace\("\/"\)/u);
  assert.doesNotMatch(`${page}\n${client}`, /test-harness|fixture|localhost|127\.0\.0\.1/iu);
});

test("legacy workspace URL cannot open an unscoped three-column workspace", async () => {
  const legacy = await read("apps/web/app/workspaces/[workspace_id]/page.jsx");
  assert.match(legacy, /redirect\("\/notebooks"\)/u);
  assert.doesNotMatch(legacy, /ActualWorkspace|ProductWorkspaceShell/u);
});

test("Home과 선택 Notebook은 current-session logout 뒤 login replace만 수행한다", async () => {
  const api = await read("apps/web/lib/auth-api.js");
  const home = await read("apps/web/components/notebook-home-workspace.jsx");
  const product = await read("apps/web/components/notebook-product-workspace.jsx");
  const actual = await read("apps/web/components/actual-workspace.jsx");
  const homeUi = await read("packages/ui/src/notebook-home.jsx");
  const shell = await read("packages/ui/src/product-workspace-shell.jsx");
  assert.match(api, /\/bff\/api\/session\/logout/u);
  assert.match(api, /credentials:\s*"same-origin"/u);
  for (const source of [home, product]) {
    assert.match(source, /logoutCurrentSession/u);
    assert.match(source, /window\.location\.replace\("\/"\)/u);
    assert.doesNotMatch(source, /localStorage|sessionStorage|document\.cookie/iu);
  }
  assert.match(actual, /onLogout/u);
  assert.match(homeUi, /로그아웃/u);
  assert.match(shell, /로그아웃/u);
});

test("logout client는 same-origin empty POST와 exact safe projection만 수용한다", async () => {
  const calls = [];
  const result = await logoutCurrentSession({ fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return Response.json({ data: { status: "logged_out", replayed: false }, meta: { trace_id: "trace-safe" } });
  } });
  assert.deepEqual(result, { status: "logged_out", replayed: false });
  assert.equal(calls[0].url, "/bff/api/session/logout");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal("body" in calls[0].options, false);
  await assert.rejects(
    logoutCurrentSession({ fetchImpl: async () => Response.json({ data: { status: "logged_out", replayed: "false" } }) }),
    /LOGOUT_RESPONSE_INVALID/u,
  );
  for (const invalid of [
    { data: { status: "logged_out", replayed: false, secret: "blocked" }, meta: { trace_id: "trace-safe" } },
    { data: { status: "logged_out", replayed: false }, meta: { trace_id: "trace-safe", internal: true } },
    { data: { status: "logged_out", replayed: false }, meta: { trace_id: "trace-safe" }, extra: true },
  ]) {
    await assert.rejects(
      logoutCurrentSession({ fetchImpl: async () => Response.json(invalid) }),
      /LOGOUT_RESPONSE_INVALID/u,
    );
  }
});

test("logout 뒤 BFCache back/forward는 Home과 3열 모두 session을 재검증한다", async () => {
  for (const path of [
    "apps/web/components/notebook-home-workspace.jsx",
    "apps/web/components/notebook-product-workspace.jsx",
  ]) {
    const source = await read(path);
    assert.match(source, /pageshow/u);
    assert.match(source, /pagehide/u);
    assert.match(source, /popstate/u);
    assert.match(source, /concealProtectedRoute/u);
    assert.match(source, /getCurrentNotebookSession/u);
    assert.match(source, /window\.location\.replace\("\/"\)/u);
  }
});
