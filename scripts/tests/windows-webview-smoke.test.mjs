import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import {
  buildSmokeConfig,
  classifySmokeState,
  createSmokeExecutionPlan,
  resolveRepositoryRoot,
  selectOwnedSmokeCandidate,
} from "../run-windows-webview-smoke.mjs";

test("smoke config는 production crate의 단일 1920x1080 window와 loopback devUrl을 exact 결속한다", () => {
  const config = buildSmokeConfig({ frontendPort: 4241, title: "Daon WebView Smoke" });

  assert.deepEqual(config.build, {
    devUrl: "http://127.0.0.1:4241",
    frontendDist: "../dist",
  });
  assert.deepEqual(config.bundle, { externalBin: [] });
  assert.deepEqual(config.app.windows, [{
    label: "main",
    title: "Daon WebView Smoke",
    width: 1920,
    height: 1080,
    resizable: true,
    devtools: false,
  }]);
});

test("첫 smoke 가설은 auth bootstrap과 입력 없이 120초 안에서만 실행한다", () => {
  assert.deepEqual(createSmokeExecutionPlan("production-config-no-bootstrap"), {
    hypothesis: "production-config-no-bootstrap",
    featureArguments: [],
    contractBootstrap: false,
    credentialAccess: false,
    userInput: false,
    timeoutMs: 120_000,
  });
});

test("두 번째 smoke 가설은 같은 crate의 minimal Builder feature 하나만 바꾼다", () => {
  assert.deepEqual(createSmokeExecutionPlan("minimal-builder"), {
    hypothesis: "minimal-builder",
    featureArguments: ["--features", "webview-smoke"],
    contractBootstrap: false,
    credentialAccess: false,
    userInput: false,
    timeoutMs: 120_000,
  });
});

test("세 번째 smoke 가설은 동일 minimal Builder를 GUI 허용 경계에서만 재사용한다", () => {
  assert.deepEqual(createSmokeExecutionPlan("minimal-builder-unsandboxed"), {
    hypothesis: "minimal-builder-unsandboxed",
    featureArguments: ["--features", "webview-smoke"],
    contractBootstrap: false,
    credentialAccess: false,
    userInput: false,
    timeoutMs: 120_000,
  });
});

test("smoke PASS는 parent liveness와 targetable window와 direct WebView2 child를 모두 요구한다", () => {
  assert.deepEqual(
    classifySmokeState({ parentAlive: true, targetableWindow: true, directWebViewChildren: 1 }),
    { status: "PASS", safeCode: null },
  );
  assert.equal(classifySmokeState({ parentAlive: false, targetableWindow: false, directWebViewChildren: 0 }).safeCode, "SMOKE_PARENT_NOT_ALIVE");
  assert.equal(classifySmokeState({ parentAlive: true, targetableWindow: false, directWebViewChildren: 0 }).safeCode, "SMOKE_WINDOW_NOT_TARGETABLE");
  assert.equal(classifySmokeState({ parentAlive: true, targetableWindow: true, directWebViewChildren: 0 }).safeCode, "SMOKE_WEBVIEW_CHILD_MISSING");
});

test("공백이 있는 Windows file URL도 실제 repository root로 복원한다", () => {
  assert.equal(
    resolveRepositoryRoot("file:///C:/Users/example/Desktop/D%20Driver/Project/Daon_User/scripts/run-windows-webview-smoke.mjs"),
    "C:\\Users\\example\\Desktop\\D Driver\\Project\\Daon_User",
  );
});

test("minimal WebView smoke feature는 production startup과 분리되고 default에서 비활성이다", async () => {
  const [cargo, lib] = await Promise.all([
    readFile(new URL("../../apps/desktop/src-tauri/Cargo.toml", import.meta.url), "utf8"),
    readFile(new URL("../../apps/desktop/src-tauri/src/lib.rs", import.meta.url), "utf8"),
  ]);
  assert.match(cargo, /webview-smoke\s*=\s*\[\]/u);
  assert.doesNotMatch(cargo, /default\s*=\s*\[[^\]]*webview-smoke/u);
  assert.match(lib, /#\[cfg\(feature = "webview-smoke"\)\]\s*pub fn run\(\)/u);
  assert.match(lib, /#\[cfg\(not\(feature = "webview-smoke"\)\)\][\s\S]{0,80}?pub fn run\(\)/u);
});

test("smoke target은 exact title과 launcher lineage가 모두 일치해야 한다", () => {
  const processes = [
    { pid: 10, parentProcessId: 0, title: "launcher" },
    { pid: 11, parentProcessId: 10, title: "cargo" },
    { pid: 12, parentProcessId: 11, title: "Daon WebView Smoke" },
    { pid: 20, parentProcessId: 0, title: "Daon WebView Smoke" },
    { pid: 13, parentProcessId: 11, title: "다른 Daon 창" },
  ];
  assert.deepEqual(selectOwnedSmokeCandidate(processes, "Daon WebView Smoke", 10), processes[2]);
  assert.equal(selectOwnedSmokeCandidate(processes, "없는 제목", 10), null);
  assert.equal(selectOwnedSmokeCandidate(processes, "Daon WebView Smoke", 99), null);
});

test("smoke runner는 이름 기반 fallback과 global process cleanup을 금지한다", async () => {
  const runner = await readFile(new URL("../run-windows-webview-smoke.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(runner, /if\(-not \$app\)\{\$app=\$apps\|Select-Object -First 1\}/u);
  assert.doesNotMatch(runner, /for \(const pid of await current(?:Desktop|OwnedProcess)Pids\(\)\)/u);
  assert.match(runner, /probeSmoke\(title, child\.pid\)/u);
  assert.match(runner, /finally \{\s*await stopOwnedProcess\(tauri\.child\.pid\)/u);
});
