import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
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

test("1920×1080 모델 목록은 페이지가 아닌 목록 내부에서 스크롤된다", { skip: !browser }, () => {
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
    const result = spawnSync(browser, [
      "--headless", "--disable-gpu", "--disable-software-rasterizer", "--no-sandbox",
      "--no-first-run", "--no-default-browser-check", "--disable-extensions",
      `--user-data-dir=${path.join(tempDir, "browser")}`,
      "--window-size=1920,1080", "--dump-dom", pathToFileURL(fixture).href,
    ], { encoding: "utf8", timeout: 20_000 });
    assert.equal(result.status, 0, result.stderr);
    const match = result.stdout.match(/<output id="result">([^<]+)<\/output>/);
    assert.ok(match, "브라우저가 실제 배치 크기를 출력해야 합니다");
    const sizes = JSON.parse(match[1].replaceAll("&quot;", '"'));
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

test("1920×1080 Provider 목록은 압축된 카드와 내부 스크롤을 사용한다", { skip: !browser }, () => {
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
    const result = spawnSync(browser, [
      "--headless", "--disable-gpu", "--disable-software-rasterizer", "--no-sandbox",
      "--no-first-run", "--no-default-browser-check", "--disable-extensions",
      `--user-data-dir=${path.join(tempDir, "browser")}`,
      "--window-size=1920,1080", "--dump-dom", pathToFileURL(fixture).href,
    ], { encoding: "utf8", timeout: 20_000 });
    assert.equal(result.status, 0, result.stderr);
    const match = result.stdout.match(/<output id="result">([^<]+)<\/output>/);
    assert.ok(match, "브라우저가 실제 배치 크기를 출력해야 합니다");
    const sizes = JSON.parse(match[1].replaceAll("&quot;", '"'));
    assert.ok(sizes.listHeight <= 760, `목록 높이: ${sizes.listHeight}px`);
    assert.ok(sizes.listContentHeight > sizes.listHeight, "Provider 목록이 넘쳐야 합니다");
    assert.ok(sizes.listScrollTop > 0, "Provider 목록 자체가 스크롤되어야 합니다");
    assert.ok(sizes.detailHeight <= 800, `상세 패널 높이: ${sizes.detailHeight}px`);
    assert.ok(sizes.detailContentHeight > sizes.detailHeight, "상세 패널에 내부 overflow가 있어야 합니다");
    assert.ok(sizes.detailScrollTop > 0, "상세 패널 자체가 스크롤되어야 합니다");
    assert.ok(sizes.cardHeight <= 70, `카드 높이: ${sizes.cardHeight}px`);
    assert.ok(sizes.gapAfterStatus <= 80, `상단 여백: ${sizes.gapAfterStatus}px`);
  } finally {
    rmSync(tempDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
