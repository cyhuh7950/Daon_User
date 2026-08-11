import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const evidenceRoot = path.join(root, "apps/evidence-hub");

async function read(relativePath) {
  return readFile(path.join(root, relativePath), "utf8");
}

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(candidate);
    return /\.(?:js|jsx|mjs|css|html)$/u.test(entry.name) ? [candidate] : [];
  }));
  return nested.flat();
}

test("Evidence Hub는 독립 Workspace이며 제품 UI export가 아니다", async () => {
  assert.equal(existsSync(path.join(evidenceRoot, "package.json")), true, "Evidence Hub workspace가 필요하다");
  assert.equal(existsSync(path.join(evidenceRoot, "src/evidence-hub.jsx")), true, "Evidence Hub React entry가 필요하다");

  const rootPackage = JSON.parse(await read("package.json"));
  const uiIndex = await read("packages/ui/src/index.js");
  assert.equal(rootPackage.scripts["dev:evidence-hub"], "npm run dev --workspace @daon-user/evidence-hub -- --host 127.0.0.1");
  assert.equal(rootPackage.scripts["verify:evidence-hub"], "node --test scripts/tests/evidence-hub-boundary.test.mjs scripts/tests/platform-prototype-evidence.test.mjs");
  assert.doesNotMatch(uiIndex, /ProductionBoundEvidenceHub|production-bound-evidence/);
  assert.equal(existsSync(path.join(root, "packages/ui/src/production-bound-evidence-pane.jsx")), false);
  assert.equal(existsSync(path.join(root, "packages/ui/src/production-bound-evidence-model.js")), false);
});

test("Evidence Hub는 8 Journey와 4 Client 계약을 내용 보존한다", async () => {
  const modelPath = path.join(evidenceRoot, "src/evidence-hub-model.js");
  assert.equal(existsSync(modelPath), true, "이동된 Evidence model이 필요하다");
  const model = await import(`${pathToFileURL(modelPath).href}?t=${Date.now()}`);
  const state = model.createProductionBoundEvidenceState();

  assert.deepEqual(state.journeys.map((journey) => journey.id), [
    "workspace_context", "knowledge_authority", "model_lineage", "studio_generation",
    "review_delivery_registration", "account_security", "operations_recovery", "negative_states"
  ]);
  assert.deepEqual(state.clients.map((client) => client.client_type), ["web", "windows", "android", "ios"]);
  assert.equal(state.platform_journey_matrix.length, 32);
});

test("Evidence Hub source는 sessionStorage 외 저장소와 외부 효과를 사용하지 않는다", async () => {
  const srcRoot = path.join(evidenceRoot, "src");
  assert.equal(existsSync(srcRoot), true, "Evidence Hub source directory가 필요하다");
  const files = await sourceFiles(srcRoot);
  const source = (await Promise.all(files.map((file) => readFile(file, "utf8")))).join("\n");

  assert.match(source, /개발·검증 전용 · 사용자 제품 아님 · 외부 API와 상태 변경 없음/);
  assert.match(source, /sessionStorage/);
  assert.doesNotMatch(source, /\bfetch\s*\(|XMLHttpRequest|WebSocket|@tauri-apps|\binvoke\s*\(|localStorage|indexedDB|\bauth\b|\brecovery\b|\bupload\b/i);
});

test("Evidence Hub package는 승인된 고정 의존성만 가진다", async () => {
  const manifestPath = path.join(evidenceRoot, "package.json");
  assert.equal(existsSync(manifestPath), true, "Evidence Hub manifest가 필요하다");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  assert.equal(manifest.name, "@daon-user/evidence-hub");
  assert.equal(manifest.private, true);
  assert.deepEqual(manifest.scripts, { dev: "vite", build: "vite build" });
  assert.deepEqual(manifest.dependencies, {
    "@daon-user/contracts": "0.0.0",
    "@daon-user/design-tokens": "0.0.0",
    react: "19.2.7",
    "react-dom": "19.2.7"
  });
  assert.deepEqual(manifest.devDependencies, { vite: "8.1.5" });
});

test("Evidence Hub 고유 CSS는 Evidence 앱에만 있고 Desktop 제품 산출물에는 없다", async () => {
  const evidenceCss = await read("apps/evidence-hub/src/evidence-hub.css");
  const productCss = await read("packages/ui/src/workspace.css");
  const desktopAssets = await sourceFiles(path.join(root, "apps/desktop/dist"));
  const desktopBundle = (await Promise.all(desktopAssets.map((file) => readFile(file, "utf8")))).join("\n");
  const evidenceOnlySelectors = [".evidence-hub", ".evidence-route-strip", ".evidence-journey-grid"];

  for (const selector of evidenceOnlySelectors) {
    assert.match(evidenceCss, new RegExp(selector.replace(".", "\\.")), `${selector}는 Evidence 앱 소유여야 한다`);
    assert.equal(productCss.includes(selector), false, `${selector}가 공용 제품 CSS에 남으면 안 된다`);
    assert.equal(desktopBundle.includes(selector), false, `${selector}가 Desktop Bundle에 남으면 안 된다`);
  }
});
