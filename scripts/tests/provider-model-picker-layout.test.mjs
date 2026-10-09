import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const stylesheetUrl = pathToFileURL(path.join(repoRoot, "apps/web/app/globals.css")).href;
const browserCandidates = process.platform === "win32"
  ? [
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
      "C:/Program Files/Google/Chrome/Application/chrome.exe",
    ]
  : ["/usr/bin/chromium", "/usr/bin/google-chrome"];
const browser = browserCandidates.find(existsSync);

async function measureLayout(fixture, { width = 1920, height = 1080 } = {}) {
  const profile = mkdtempSync(path.join(tmpdir(), "daon-layout-browser-"));
  const child = spawn(browser, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, pathToFileURL(fixture).href,
  ], { stdio: "ignore", windowsHide: true });
  let socket;
  let send;
  try {
    let port;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      try {
        port = Number(readFileSync(path.join(profile, "DevToolsActivePort"), "utf8").split(/\r?\n/u)[0]);
        break;
      } catch { /* Browser is still starting. */ }
    }
    assert.ok(port, "격리 브라우저의 CDP 포트가 열려야 합니다");
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    assert.equal(response.status, 200);
    const target = (await response.json()).find((entry) => entry.type === "page");
    assert.ok(target?.webSocketDebuggerUrl);
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    let sequence = 0;
    const pending = new Map();
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      const request = pending.get(message.id);
      if (!request) return;
      pending.delete(message.id);
      message.error ? request.reject(new Error(JSON.stringify(message.error))) : request.resolve(message.result);
    });
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timeout = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`CDP command timed out: ${method}`));
      }, 10_000);
      timeout.unref();
      pending.set(id, {
        resolve: (value) => { clearTimeout(timeout); resolve(value); },
        reject: (error) => { clearTimeout(timeout); reject(error); },
      });
      socket.send(JSON.stringify({ id, method, params }));
    });
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: pathToFileURL(fixture).href });
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const result = await send("Runtime.evaluate", {
        expression: "document.querySelector('#result')?.textContent || ''", returnByValue: true,
      });
      if (result.result.value) return JSON.parse(result.result.value);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.fail("브라우저가 실제 배치 크기를 출력해야 합니다");
  } finally {
    if (send) await Promise.race([
      send("Browser.close").catch(() => {}),
      new Promise((resolve) => setTimeout(resolve, 1000)),
    ]);
    socket?.close();
    child.kill();
    assert.ok(profile.startsWith(`${tmpdir()}${path.sep}`));
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      try {
        rmSync(profile, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 });
        break;
      } catch (error) {
        if (attempt === 19) throw error;
      }
    }
  }
}

test("1920×1080 모델 목록은 페이지가 아닌 목록 내부에서 스크롤된다", { skip: !browser }, async () => {
  const tempDir = mkdtempSync(path.join(tmpdir(), "daon-model-picker-"));
  try {
    const models = Array.from({ length: 840 }, (_, index) =>
      `<label><input type="checkbox"><span>provider/model-${index}</span></label>`
    ).join("");
    const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="${stylesheetUrl}">
      <style>body{margin:0}.fixture{width:760px;margin-top:500px}</style></head><body>
      <div class="fixture"><fieldset class="provider-model-picker"><legend>사용 허용 모델</legend>
      <p>선택하지 않으면 auto를 사용합니다.</p><div class="provider-model-options">${models}</div>
      </fieldset></div><output id="result"></output><script>
      const list = document.querySelector('.provider-model-options');
      list.scrollTop = 200;
      document.querySelector('#result').textContent = JSON.stringify({
        viewportWidth: innerWidth,
        viewportHeight: innerHeight,
        pageHeight: document.documentElement.scrollHeight,
        listHeight: list.clientHeight,
        listContentHeight: list.scrollHeight,
        listScrollTop: list.scrollTop,
      });
      </script></body></html>`;
    const fixture = path.join(tempDir, "index.html");
    writeFileSync(fixture, html);
    const sizes = await measureLayout(fixture);
    assert.ok(sizes.viewportWidth >= 1800, JSON.stringify(sizes));
    assert.ok(sizes.viewportHeight >= 900, JSON.stringify(sizes));
    assert.ok(sizes.listHeight <= 320, `목록 높이: ${sizes.listHeight}px`);
    assert.ok(sizes.listContentHeight > sizes.listHeight, "긴 목록에 내부 overflow가 있어야 합니다");
    assert.ok(sizes.listScrollTop > 0, "목록 자체가 실제로 스크롤되어야 합니다");
    assert.ok(sizes.pageHeight <= sizes.viewportHeight, `페이지 높이: ${sizes.pageHeight}px`);
  } finally {
    rmSync(tempDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test("세 해상도에서 Provider 목록과 상세는 내부 스크롤을 사용한다", { skip: !browser }, async () => {
  const tempDir = mkdtempSync(path.join(tmpdir(), "daon-provider-list-"));
  try {
    const cards = Array.from({ length: 15 }, (_, index) =>
      `<button class="provider-card"><span class="provider-monogram">PR</span><span><strong>Provider ${index}</strong><small>공용 · 개인 Key 필요 · 사용 대기</small></span><span></span></button>`
    ).join("");
    const html = `<!doctype html><html><head><meta charset="utf-8">
      <link rel="stylesheet" href="${stylesheetUrl}">
      <link rel="stylesheet" href="${pathToFileURL(path.join(repoRoot, "apps/web/app/settings/model-connections/provider-settings.css")).href}">
      </head><body><main class="provider-settings-shell">
      <header class="provider-settings-header"><div><h1>모델·Provider 설정</h1><p>현재 Workspace 설정</p></div></header>
      <div class="provider-status ready" role="status">연결 시험에 성공했습니다.</div>
      <section class="provider-admin-panel"><div class="studio-section-heading"><div><span>SYSTEM ADMIN</span>
      <h2>시스템 Provider 연결</h2><small>활성화된 연결을 사용합니다.</small></div>
      <div class="provider-heading-actions"><label>상태 확인 주기<select><option>60분</option></select></label>
      <button>주기 저장</button><button>연결 추가</button></div></div>
      <div class="provider-settings-layout"><div class="provider-connection-list">${cards}</div>
      <div class="provider-detail"><header>선택된 연결</header><div class="provider-detail-grid">
      <label>연결 이름<input value="Provider"></label><fieldset class="provider-model-picker">
      <legend>사용 허용 모델</legend><div class="provider-model-options">${Array.from({ length: 840 }, (_, index) => `<label><input type="checkbox"><span>provider/model-${index}</span></label>`).join("")}</div>
      </fieldset>${Array.from({ length: 18 }, (_, index) => `<label>추가 설정 ${index}<input value="설정값"></label>`).join("")}
      </div><div class="provider-detail-actions"><button>연결 시험 및 저장</button></div>
      </div></div></section></main><output id="result"></output><script>
      const list = document.querySelector('.provider-connection-list');
      const card = document.querySelector('.provider-card');
      const status = document.querySelector('.provider-status');
      const detail = document.querySelector('.provider-detail');
      list.scrollTop = 200;
      detail.scrollTop = 200;
      document.querySelector('#result').textContent = JSON.stringify({
        viewportHeight: innerHeight,
        pageHeight: document.documentElement.scrollHeight,
        listHeight: list.clientHeight,
        listContentHeight: list.scrollHeight,
        listScrollTop: list.scrollTop,
        detailHeight: detail.clientHeight,
        detailContentHeight: detail.scrollHeight,
        detailScrollTop: detail.scrollTop,
        cardHeight: card.getBoundingClientRect().height,
        gapAfterStatus: card.getBoundingClientRect().top - status.getBoundingClientRect().bottom,
      });
      </script></body></html>`;
    const fixture = path.join(tempDir, "index.html");
    writeFileSync(fixture, html);
    for (const [width, height] of [[1920, 1080], [1440, 900], [430, 844]]) {
      const sizes = await measureLayout(fixture, { width, height });
      assert.ok(sizes.pageHeight <= sizes.viewportHeight, `${width}×${height} 페이지 높이: ${sizes.pageHeight}px`);
      assert.ok(sizes.listHeight <= 640, `${width}×${height} Provider 목록 높이: ${sizes.listHeight}px`);
      assert.ok(sizes.listContentHeight > sizes.listHeight, `${width}×${height} Provider 목록이 넘쳐야 합니다`);
      assert.ok(sizes.listScrollTop > 0, `${width}×${height} Provider 목록 자체가 스크롤되어야 합니다`);
      assert.ok(sizes.detailHeight <= 800, `${width}×${height} 상세 패널 높이: ${sizes.detailHeight}px`);
      assert.ok(sizes.detailContentHeight > sizes.detailHeight, `${width}×${height} 상세 패널에 내부 overflow가 있어야 합니다`);
      assert.ok(sizes.detailScrollTop > 0, `${width}×${height} 상세 패널 자체가 스크롤되어야 합니다`);
      assert.ok(sizes.cardHeight <= 70, `${width}×${height} 카드 높이: ${sizes.cardHeight}px`);
      assert.ok(sizes.gapAfterStatus <= 80, `${width}×${height} 상단 여백: ${sizes.gapAfterStatus}px`);
    }
    writeFileSync(fixture, html
      .replace('class="provider-settings-shell"', 'class="provider-settings-shell is-embedded"')
      .replace('<body><main', '<body><div style="height:60px">Workspace navigation</div><main'));
    for (const [width, height] of [[1920, 1080], [430, 844]]) {
      const sizes = await measureLayout(fixture, { width, height });
      assert.ok(sizes.pageHeight <= sizes.viewportHeight, `${width}×${height} 임베디드 페이지 높이: ${sizes.pageHeight}px`);
      assert.ok(sizes.listScrollTop > 0 && sizes.detailScrollTop > 0, `${width}×${height} 임베디드 내부 스크롤`);
    }
  } finally {
    rmSync(tempDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
