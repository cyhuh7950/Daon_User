import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { listSessionTenants, switchSessionTenant } from "../../apps/web/lib/auth-api.js";

test("Web session tenant helper uses same-origin BFF and accepts only safe projections", async () => {
  const requests = [];
  const fetchImpl = async (path, init) => {
    requests.push({ path, method: init.method, credentials: init.credentials, body: init.body });
    if (init.method === "GET") return Response.json({
      data: { current_tenant_id: "tenant-personal", tenants: [
        { tenant_id: "tenant-personal", display_name: "개인", kind: "personal", workspace_id: "workspace-personal" },
        { tenant_id: "tenant-org", display_name: "조직", kind: "organization", workspace_id: "workspace-org" },
      ] }, meta: { trace_id: "trace-001" },
    });
    return Response.json({
      data: { user_id: "user-001", tenant_id: "tenant-org", workspace_id: "workspace-org", session_id: "session-new" },
      meta: { trace_id: "trace-002" },
    });
  };
  const listed = await listSessionTenants({ fetchImpl });
  const switched = await switchSessionTenant("tenant-org", { fetchImpl });

  assert.equal(listed.current_tenant_id, "tenant-personal");
  assert.equal(listed.tenants[1].display_name, "조직");
  assert.deepEqual(switched, { user_id: "user-001", tenant_id: "tenant-org",
    workspace_id: "workspace-org", session_id: "session-new" });
  assert.deepEqual(requests, [
    { path: "/bff/api/session/tenants", method: "GET", credentials: "same-origin", body: undefined },
    { path: "/bff/api/session/tenant", method: "POST", credentials: "same-origin",
      body: '{"tenant_id":"tenant-org"}' },
  ]);
});

async function testS41OverlayBrowser(t) {
  const temp = tmpdir();
  const candidates = process.platform === "win32"
    ? ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Google/Chrome/Application/chrome.exe"]
    : ["/usr/bin/chromium", "/usr/bin/google-chrome"];
  const browser = candidates.find(existsSync);
  assert.ok(browser, "기존 브라우저가 없으면 배치 검증을 PASS로 대체하지 않는다");
  const root = path.resolve(import.meta.dirname, "../.."), output = await mkdtemp(path.join(temp, "overlay-"));
  const profile = path.join(output, "profile");
  let child, socket, send;
  try {
    const { build } = await import("vite");
    const entry = `import React from ${JSON.stringify(path.join(root, "node_modules/react/index.js").replaceAll("\\", "/"))};
      import {createRoot} from ${JSON.stringify(path.join(root, "node_modules/react-dom/client.js").replaceAll("\\", "/"))};
      import ${JSON.stringify(path.join(root, "packages/design-tokens/tokens.css").replaceAll("\\", "/"))};
      import ${JSON.stringify(path.join(root, "apps/web/app/globals.css").replaceAll("\\", "/"))};
      import {WebShellRuntimeStatus} from ${JSON.stringify(path.join(root, "packages/ui/src/web-shell-runtime-status.jsx").replaceAll("\\", "/"))};
      import {NotebookHome} from ${JSON.stringify(path.join(root, "packages/ui/src/notebook-home.jsx").replaceAll("\\", "/"))};
      window.__chosen=[];
      window.fetch=async()=>new URLSearchParams(location.search).get('status')==='unavailable'
        ? new Response('{}',{status:503}) : Response.json({code:'WEB_SHELL_READY',ready:true,downstream_ready:false,shell_version:'fixture',build_id:'fixture',observed_at:'2026-10-17T00:00:00Z'});
      createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
        new URLSearchParams(location.search).get('status')==='absent'?null:React.createElement(WebShellRuntimeStatus),React.createElement(NotebookHome,{displayIdentity:'합성 사용자',
          notebooks:Array.from({length:40},(_,i)=>({notebook_id:'fixture-'+i,title:'합성 Notebook '+i,status:'empty',source_count:0,output_count:0,updated_at:'2026-10-17T00:00:00Z'})),
          tenantScope:{current_tenant_id:'personal',tenants:[{tenant_id:'personal',display_name:'개인',kind:'personal',workspace_id:'personal-workspace'},{tenant_id:'organization',display_name:'승인 조직',kind:'organization',workspace_id:'org-workspace'}]},
          onSwitchTenant:id=>window.__chosen.push(id)})));`;
    const entryFile = path.join(output, "entry.jsx"); await writeFile(entryFile, entry);
    await build({ configFile: false, logLevel: "silent", root, define: { "process.env.NODE_ENV": JSON.stringify("production") },
      build: { outDir: output, emptyOutDir: false, lib: { entry: entryFile, name: "S41Overlay", formats: ["iife"], fileName: "overlay" } } });
    const files = await readdir(output), script = files.find(file => /\.js$/u.test(file)), styles = files.filter(file => /\.css$/u.test(file));
    const html = `<!doctype html><html><head><meta charset="utf-8"><style>${(await Promise.all(styles.map(file => readFile(path.join(output, file), "utf8")))).join("\n")}</style></head><body><div id="root"></div><script src="${pathToFileURL(path.join(output, script)).href}"></script></body></html>`;
    const fixture = path.join(output, "index.html"); await writeFile(fixture, html);
    child = spawn(browser, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore", windowsHide: true });
    let port;
    for (let attempt = 0; attempt < 50 && !port; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 200));
      try { port = Number((await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split(/\r?\n/u)[0]); } catch { /* 자기 profile의 기동만 기다린다. */ }
    }
    assert.ok(port, "격리 브라우저 기동 확인");
    const target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item => item.type === "page");
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
    let sequence = 0; const pending = new Map(), runtimeErrors = [];
    socket.addEventListener("message", ({ data }) => { const message = JSON.parse(data);
      if (message.method === "Runtime.exceptionThrown") runtimeErrors.push(message.params.exceptionDetails.exception?.description ?? message.params.exceptionDetails.text);
      const request = pending.get(message.id); if (!request) return; pending.delete(message.id); message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result); });
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP_TIMEOUT:${method}`)); }, 10000);
      pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
      socket.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async expression => { const result = await send("Runtime.evaluate", { expression, returnByValue: true }); assert.equal(result.exceptionDetails, undefined, "시험 페이지 실행 오류"); return result.result.value; };
    const pointer = async selector => {
      const point = await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,hit:e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}})()`);
      assert.equal(point.hit, true, `${selector} 실제 hit-test: ${JSON.stringify(point)}`);
      for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: point.x, y: point.y, button: "left", clickCount: 1 });
      await new Promise(resolve => setTimeout(resolve, 30));
    };
    await send("Page.enable");
    await send("Runtime.enable");
    for (const [width, height] of [[1265, 744], [1920, 1080], [430, 844]]) for (const status of ["ready", "unavailable"]) {
      let fixtureFailure = null;
      await t.test(`${width}×${height}/${status}`, async () => {
        await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
        await send("Page.navigate", { url: `${pathToFileURL(fixture).href}?status=${status}` });
        let ready = false;
        for (let attempt = 0; attempt < 50 && !ready; attempt++) {
          ready = await evaluate(`document.querySelector('aside')?.dataset.shellStatus === '${status}' && !!document.querySelector('.notebook-toolbar-button')`);
          if (!ready) await new Promise(resolve => setTimeout(resolve, 100));
        }
        if (!ready) fixtureFailure = `실제 React 렌더링 실패: ${JSON.stringify(runtimeErrors)}`;
        assert.ok(ready, fixtureFailure);
        await pointer(".notebook-toolbar-button");
        assert.equal(await evaluate("!!document.querySelector('.notebook-settings-menu')"), true);
        await pointer(".notebook-scope-menu button:last-child");
        assert.deepEqual(await evaluate("window.__chosen"), ["organization"]);
        await pointer(".web-shell-runtime-info");
        assert.equal(await evaluate("!!document.querySelector('#web-shell-runtime-details')"), true);
        await pointer(".web-shell-runtime-info");
        assert.equal(await evaluate("!!document.querySelector('#web-shell-runtime-details')"), false);
        assert.equal(await evaluate("getComputedStyle(document.querySelector('aside')).position"), "fixed");
        assert.match(await evaluate("document.querySelector('aside').textContent"), status === "ready" ? /Web Shell 준비/u : /상태 확인 불가/u);
        if (status === "unavailable") await pointer(".web-shell-runtime-retry");
        await evaluate("window.scrollTo(0,200)");
        await new Promise(resolve => setTimeout(resolve, 30));
        await pointer(".notebook-toolbar-button");
        assert.equal(await evaluate("!!document.querySelector('.notebook-settings-menu')"), true);
        const bounds = await evaluate("(()=>{const r=document.querySelector('.notebook-toolbar-button').getBoundingClientRect();return {left:r.left,right:r.right,width:innerWidth}})()");
        assert.ok(bounds.left >= 0 && bounds.right <= bounds.width, JSON.stringify(bounds));
      });
      if (fixtureFailure) throw new Error(fixtureFailure);
    }
    await t.test("동일 화면 resize는 배너 점유 하단을 다시 측정한다", async () => {
      await pointer(".notebook-toolbar-button");
      await send("Emulation.setDeviceMetricsOverride", { width: 1265, height: 744, deviceScaleFactor: 1, mobile: false });
      let measured = false;
      for (let attempt = 0; attempt < 30 && !measured; attempt++) {
        measured = await evaluate("Math.abs(parseFloat(getComputedStyle(document.querySelector('.notebook-home')).paddingBlockStart)-document.querySelector('aside').getBoundingClientRect().bottom)<0.1");
        if (!measured) await new Promise(resolve => setTimeout(resolve, 30));
      }
      assert.ok(measured, "고정 여백이 아닌 실제 배너 하단과 일치");
      await pointer(".notebook-toolbar-button");
      assert.equal(await evaluate("!!document.querySelector('.notebook-settings-menu')"), true);
    });
    await t.test("상태 배너가 없는 Notebook 사용처에는 점유 공간을 추가하지 않는다", async () => {
      await send("Page.navigate", { url: `${pathToFileURL(fixture).href}?status=absent` });
      let rendered = false;
      for (let attempt = 0; attempt < 50 && !rendered; attempt++) {
        rendered = await evaluate("!!document.querySelector('.notebook-toolbar-button') && !document.querySelector('aside')");
        if (!rendered) await new Promise(resolve => setTimeout(resolve, 50));
      }
      assert.ok(rendered);
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.notebook-home')).paddingBlockStart"), "0px");
      assert.equal(await evaluate("getComputedStyle(document.querySelector('.notebook-home-header')).top"), "0px");
      await pointer(".notebook-toolbar-button");
      assert.equal(await evaluate("!!document.querySelector('.notebook-settings-menu')"), true);
    });
  } finally {
    if (send) await send("Browser.close").catch(() => {});
    socket?.close();
    if (child && child.exitCode === null) { child.kill(); await new Promise(resolve => child.once("exit", resolve)); }
    assert.ok(path.resolve(output).startsWith(`${path.resolve(temp)}${path.sep}`));
    await rm(output, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
}

if (process.env.DAON_S41_OVERLAY_BROWSER_TEST === "1") {
  test("S41 상태 배너와 설정은 현재폭·운영폭·모바일에서 실제 포인터로 독립 조작된다", testS41OverlayBrowser);
}
