import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(".");
const verifierPath = path.join(root, "scripts/verify-product-ui-boundary.mjs");

async function loadVerifier() {
  assert.equal(existsSync(verifierPath), true, "Product UI boundary verifier가 필요하다");
  return import(`${pathToFileURL(verifierPath).href}?t=${Date.now()}`);
}

test("검증기는 Source와 Bundle의 각 금지 Token을 실제 파일에서 탐지한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-"));
  try {
    const sourceRoot = path.join(fixtureRoot, "source");
    const bundleRoot = path.join(fixtureRoot, "bundle");
    await mkdir(sourceRoot);
    await mkdir(bundleRoot);
    for (const [index, token] of verifier.FORBIDDEN_PRODUCT_UI_TOKENS.entries()) {
      const targetRoot = index % 2 === 0 ? sourceRoot : bundleRoot;
      await writeFile(path.join(targetRoot, `token-${index}.js`), `export default ${JSON.stringify(token)};\n`, "utf8");
    }

    const result = await verifier.scanProductUiBoundary({ sourceRoots: [sourceRoot], bundleRoots: [bundleRoot], commonSourceFiles: [] });
    assert.deepEqual(new Set(result.violations.map((item) => item.token)), new Set(verifier.FORBIDDEN_PRODUCT_UI_TOKENS));
    assert.equal(result.ok, false);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("검증기는 Evidence 앱을 제품 Root로 추론하지 않고 깨끗한 Fixture를 통과시킨다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-clean-"));
  try {
    const productRoot = path.join(fixtureRoot, "apps/product");
    const evidenceRoot = path.join(fixtureRoot, "apps/evidence-hub");
    await mkdir(productRoot, { recursive: true });
    await mkdir(evidenceRoot, { recursive: true });
    await writeFile(path.join(productRoot, "main.js"), "export const state = 'loading';\n", "utf8");
    await writeFile(path.join(evidenceRoot, "main.js"), "export const label = 'ProductionBoundEvidenceHub';\n", "utf8");

    const result = await verifier.scanProductUiBoundary({ sourceRoots: [productRoot], bundleRoots: [], commonSourceFiles: [] });
    assert.deepEqual(result, { ok: true, scannedFiles: 1, violations: [], boundaryErrors: [] });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("검증기는 필수 Root·대표 Asset 부재와 Symlink·부분 Build를 fail-close한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-incomplete-"));
  try {
    const sourceRoot = path.join(fixtureRoot, "source");
    const emptyBundle = path.join(fixtureRoot, "empty-bundle");
    const realBundle = path.join(fixtureRoot, "real-bundle");
    const linkedBundle = path.join(fixtureRoot, "linked-bundle");
    await mkdir(sourceRoot);
    await mkdir(emptyBundle);
    await mkdir(realBundle);
    await writeFile(path.join(sourceRoot, "main.js"), "export const state = 'loading';\n", "utf8");
    await writeFile(path.join(realBundle, "app.js"), "export const state = 'ready';\n", "utf8");
    await symlink(realBundle, linkedBundle, "junction");

    for (const bundleRoot of [path.join(fixtureRoot, "missing-bundle"), emptyBundle, linkedBundle]) {
      const result = await verifier.scanProductUiBoundary({ sourceRoots: [sourceRoot], bundleRoots: [bundleRoot], commonSourceFiles: [] });
      assert.equal(result.ok, false, `${bundleRoot}는 fail-close해야 한다`);
      assert.ok(result.boundaryErrors.length > 0, `${bundleRoot}의 구조 오류가 필요하다`);
    }
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

async function createDefaultFixture(fixtureRoot) {
  const files = {
    "apps/web/app/page.jsx": "export default function Page() { return null; }\n",
    "apps/web/app/settings/account/page.jsx": "export default function AccountSettingsPage() { return null; }\n",
    "apps/web/app/settings/organization/page.jsx": "export default function OrganizationSettingsPage() { return null; }\n",
    "apps/web/components/actual-workspace.jsx": "export function ActualWorkspace() { return null; }\n",
    "apps/web/lib/auth-pane.jsx": "export function AuthPane() { return null; }\n",
    "apps/web/.next/BUILD_ID": "fixture-build\n",
    "apps/web/.next/build-manifest.json": JSON.stringify({ rootMainFiles: ["static/chunks/app.js"] }),
    "apps/web/.next/static/chunks/app.js": "export const client = 'clean';\n",
    "apps/web/.next/static/chunks/app.css": ".web-app { display: block; }\n",
    "apps/web/.next/server/app-paths-manifest.json": JSON.stringify({ "/page": "app/page.js" }),
    "apps/web/.next/server/app/page.js": "export const page = 'clean';\n",
    "apps/web/.next/server/app/page_client-reference-manifest.js": "globalThis.__RSC_MANIFEST = { chunks: ['static/chunks/app.js'], css: [{ path: 'static/chunks/app.css' }] };\n",
    "apps/web/.next/server/chunks/shared.js": "export const server = 'clean';\n",
    "apps/desktop/src/main.jsx": "export const desktop = 'clean';\n",
    "apps/desktop/dist/index.html": "<div id=\"root\"></div><script type=\"module\" src=\"/assets/app.js\"></script><link rel=\"stylesheet\" href=\"/assets/app.css\">\n",
    "apps/desktop/dist/assets/app.js": "export const desktop = 'clean';\n",
    "apps/desktop/dist/assets/app.css": ".desktop-shell { display: block; }\n",
    "packages/ui/src/index.js": "export { ProductWorkspaceShell } from './product-workspace-shell.jsx';\n",
    "packages/ui/src/product-workspace-shell.jsx": "export function ProductWorkspaceShell() { return null; }\n",
    "packages/ui/src/product-workspace-model.js": "export const state = 'loading';\n",
    "packages/ui/src/workspace.css": ".adaptive-workspace { display: grid; }\n"
  };
  for (const [relativePath, content] of Object.entries(files)) {
    const target = path.join(fixtureRoot, relativePath);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, content, "utf8");
  }
}

test("Web account·organization Route는 누락되면 필수 Product Entry 오류로 fail-close한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-required-entry-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await rm(path.join(fixtureRoot, "apps/web/app/settings/account/page.jsx"));
    await rm(path.join(fixtureRoot, "apps/web/app/settings/organization/page.jsx"));

    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(result.ok, false);
    assert.deepEqual(
      result.boundaryErrors
        .filter((item) => item.code === "REQUIRED_PRODUCT_ENTRY_MISSING")
        .map((item) => item.path)
        .sort(),
      [
        "apps/web/app/settings/account/page.jsx",
        "apps/web/app/settings/organization/page.jsx"
      ]
    );
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Web 필수 Product Entry의 Symlink 대체는 fail-close한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-required-symlink-"));
  try {
    await createDefaultFixture(fixtureRoot);
    const accountEntry = path.join(fixtureRoot, "apps/web/app/settings/account/page.jsx");
    const linkedDirectory = path.join(fixtureRoot, "linked-account-entry");
    await rm(accountEntry);
    await mkdir(linkedDirectory);
    await symlink(linkedDirectory, accountEntry, "junction");

    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(result.ok, false);
    assert.ok(result.boundaryErrors.some((item) => (
      item.code === "REQUIRED_PRODUCT_ENTRY_SYMLINK"
      && item.path === "apps/web/app/settings/account/page.jsx"
    )));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("제품 Entry의 신규 explicit UI subpath와 재귀 전이 Source를 자동 추적한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-import-graph-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await writeFile(
      path.join(fixtureRoot, "packages/ui/package.json"),
      JSON.stringify({ name: "@daon-user/ui", type: "module", exports: { "./new-product": "./src/new-product.jsx" } }),
      "utf8"
    );
    await writeFile(
      path.join(fixtureRoot, "apps/web/app/page.jsx"),
      "import { NewProduct } from '@daon-user/ui/new-product'; export default function Page() { return NewProduct(); }\n",
      "utf8"
    );
    await writeFile(
      path.join(fixtureRoot, "packages/ui/src/new-product.jsx"),
      "export { nestedProduct } from './nested-product.js'; export function NewProduct() { return null; }\n",
      "utf8"
    );
    await writeFile(
      path.join(fixtureRoot, "packages/ui/src/nested-product.js"),
      "export const nestedProduct = 'deferred_actual';\n",
      "utf8"
    );

    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(result.ok, false);
    assert.ok(result.violations.some((item) => item.file === "packages/ui/src/nested-product.js" && item.token === "deferred_actual"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Next와 Vite manifest가 참조한 route·chunk·CSS 일부 누락을 fail-close한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-manifest-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await writeFile(
      path.join(fixtureRoot, "apps/web/.next/server/app/page_client-reference-manifest.js"),
      "globalThis.__RSC_MANIFEST = { chunks: ['static/chunks/missing-client.js'], css: [{ path: 'static/chunks/missing.css' }] };\n",
      "utf8"
    );
    await writeFile(
      path.join(fixtureRoot, "apps/desktop/dist/index.html"),
      "<script type=\"module\" src=\"/assets/missing-entry.js\"></script><link rel=\"stylesheet\" href=\"/assets/missing.css\">\n",
      "utf8"
    );

    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot });
    assert.equal(result.ok, false);
    assert.ok(result.boundaryErrors.some((item) => item.code === "MANIFEST_ASSET_MISSING" && item.path.endsWith("missing-client.js")));
    assert.ok(result.boundaryErrors.some((item) => item.code === "MANIFEST_ASSET_MISSING" && item.path.endsWith("missing-entry.js")));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Next client reference의 missing CSS는 MANIFEST_ASSET_MISSING으로 독립 검출된다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-css-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await writeFile(
      path.join(fixtureRoot, "apps/web/.next/server/app/page_client-reference-manifest.js"),
      "globalThis.__RSC_MANIFEST = { chunks: ['static/chunks/app.js'], css: [{ path: 'static/chunks/missing-route.css' }] };\n",
      "utf8"
    );
    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(result.ok, false);
    assert.ok(result.boundaryErrors.some((item) => item.code === "MANIFEST_ASSET_MISSING" && item.path.endsWith("static/chunks/missing-route.css")));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Next app-path route의 missing artifact는 MANIFEST_ASSET_MISSING으로 독립 검출된다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-app-path-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await writeFile(
      path.join(fixtureRoot, "apps/web/.next/server/app-paths-manifest.json"),
      JSON.stringify({ "/page": "app/missing-route/page.js" }),
      "utf8"
    );
    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(result.ok, false);
    assert.ok(result.boundaryErrors.some((item) => item.code === "MANIFEST_ASSET_MISSING" && item.path.endsWith("server/app/missing-route/page.js")));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Next NFT의 missing reference는 MANIFEST_ASSET_MISSING으로 독립 검출된다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-nft-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await writeFile(
      path.join(fixtureRoot, "apps/web/.next/server/app/page.js.nft.json"),
      JSON.stringify({ version: 1, files: ["../chunks/missing-nft.js"] }),
      "utf8"
    );
    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(result.ok, false);
    assert.ok(result.boundaryErrors.some((item) => item.code === "MANIFEST_ASSET_MISSING" && item.path.endsWith("server/chunks/missing-nft.js")));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("기본 검증기는 Web server chunks와 공용 Product CSS 전이 Source를 실제 검사한다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-default-"));
  try {
    await createDefaultFixture(fixtureRoot);
    await writeFile(path.join(fixtureRoot, "apps/web/.next/server/chunks/shared.js"), "export const leaked = 'deferred_actual';\n", "utf8");
    await writeFile(path.join(fixtureRoot, "packages/ui/src/workspace.css"), ".evidence-hub { display: block; }\n", "utf8");

    const result = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot });
    assert.equal(result.ok, false);
    assert.deepEqual(new Set(result.violations.map((item) => item.token)), new Set(["deferred_actual", ".evidence-hub"]));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("BFF server chunk 예외는 exact route와 NFT referencer 계보가 모두 일치할 때만 허용된다", async () => {
  const verifier = await loadVerifier();
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-product-boundary-bff-"));
  try {
    await createDefaultFixture(fixtureRoot);
    const routeRoot = path.join(fixtureRoot, "apps/web/.next/server/app/bff/shell/runtime");
    const chunkRoot = path.join(fixtureRoot, "apps/web/.next/server/chunks");
    const chunkName = "exact-bff-runtime.js";
    await mkdir(routeRoot, { recursive: true });
    await writeFile(path.join(routeRoot, "route.js"), `R.c("server/chunks/${chunkName}")\n`, "utf8");
    await writeFile(path.join(routeRoot, "route.js.nft.json"), JSON.stringify({ version: 1, files: [`../../../../chunks/${chunkName}`] }), "utf8");
    await writeFile(path.join(chunkRoot, chunkName), "export const state = 'deferred_actual';\n", "utf8");
    await writeFile(path.join(chunkRoot, `${chunkName}.map`), JSON.stringify({ version: 3, sources: ["../../../../../apps/web/lib/web-shell-runtime.js"] }), "utf8");

    const allowed = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(allowed.ok, true, JSON.stringify(allowed));

    await writeFile(path.join(routeRoot, "route.js.nft.json"), JSON.stringify({ version: 1, files: [] }), "utf8");
    const rejected = await verifier.scanDefaultProductUiBoundary({ root: fixtureRoot, target: "web" });
    assert.equal(rejected.ok, false);
    assert.ok(rejected.violations.some((item) => item.file.endsWith(chunkName) && item.token === "deferred_actual"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Web production build는 Next 산출 직후 Web 제품 경계 Gate를 실행한다", async () => {
  const manifest = JSON.parse(await readFile(path.join(root, "apps/web/package.json"), "utf8"));
  assert.equal(manifest.scripts.build, "next build && node ../../scripts/verify-product-ui-boundary.mjs --target web");
});

test("account·organization 실제 React Route는 Safe 화면을 렌더하고 Network·Adapter·Tauri 호출이 0이다", async () => {
  const output = await mkdtemp(path.join(root, ".task8-c01-react-"));
  const originalFetch = globalThis.fetch;
  const originalTauri = globalThis.__TAURI_INTERNALS__;
  let networkCalls = 0;
  let adapterCalls = 0;
  let tauriCalls = 0;
  try {
    const { build } = await import("vite");
    const { createElement } = await import("react");
    const { renderToStaticMarkup } = await import("react-dom/server");
    await build({
      configFile: false,
      logLevel: "silent",
      root,
      build: {
        outDir: output,
        emptyOutDir: false,
        lib: {
          entry: {
            account: path.join(root, "apps/web/app/settings/account/page.jsx"),
            organization: path.join(root, "apps/web/app/settings/organization/page.jsx")
          },
          formats: ["es"]
        },
        rollupOptions: { external: ["react", "react-dom", "react-dom/server"] }
      }
    });

    globalThis.fetch = async () => {
      networkCalls += 1;
      throw new Error("NETWORK_CALL_NOT_ALLOWED");
    };
    globalThis.__TAURI_INTERNALS__ = {
      invoke: async () => {
        tauriCalls += 1;
        throw new Error("TAURI_CALL_NOT_ALLOWED");
      }
    };
    const adapter = new Proxy({}, { get() { adapterCalls += 1; return () => {}; } });
    const outputFiles = await readdir(output);
    const accountFile = outputFiles.find((name) => name.startsWith("account") && /\.m?js$/u.test(name));
    const organizationFile = outputFiles.find((name) => name.startsWith("organization") && /\.m?js$/u.test(name));
    assert.ok(accountFile, "account Route build output이 필요하다");
    assert.ok(organizationFile, "organization Route build output이 필요하다");
    const accountModule = await import(`${pathToFileURL(path.join(output, accountFile)).href}?safe=${Date.now()}`);
    const organizationModule = await import(`${pathToFileURL(path.join(output, organizationFile)).href}?safe=${Date.now()}`);
    const accountHtml = renderToStaticMarkup(createElement(accountModule.default, { adapter }));
    const organizationHtml = renderToStaticMarkup(createElement(organizationModule.default, { adapter }));

    assert.match(accountHtml, /계정 설정/);
    assert.match(organizationHtml, /조직 설정/);
    for (const html of [accountHtml, organizationHtml]) {
      assert.match(html, /RESOURCE_UNAVAILABLE/);
      assert.doesNotMatch(html, /Evidence|Prototype|Mock|prototype_fixture|deferred_actual/);
    }
    assert.deepEqual({ networkCalls, adapterCalls, tauriCalls }, { networkCalls: 0, adapterCalls: 0, tauriCalls: 0 });
  } finally {
    globalThis.fetch = originalFetch;
    if (originalTauri === undefined) delete globalThis.__TAURI_INTERNALS__;
    else globalThis.__TAURI_INTERNALS__ = originalTauri;
    await rm(output, { recursive: true, force: true });
  }
});

test("현재 Web·Desktop 제품 Source와 존재하는 Build Bundle에는 금지 Token이 없다", async () => {
  const verifier = await loadVerifier();
  const result = await verifier.scanDefaultProductUiBoundary();
  assert.deepEqual(result.violations, []);
  assert.equal(result.ok, true);
});
