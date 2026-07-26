import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const cli = path.join(repoRoot, "scripts", "verify-repository-independence.mjs");
const policy = path.join(repoRoot, "independence-policy.json");

async function put(root, relative, content) {
  const file = path.join(root, relative);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, content);
}

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "daon-independence-"));
  const components = [
    { id: "apps/web", path: "apps/web", kind: "application", runtime: "browser-and-server-bff", allowed_dependencies: [], forbidden_dependencies: ["services/api"] },
    { id: "services/api", path: "services/api", kind: "service", runtime: "fastapi-cloud-service", allowed_dependencies: [], forbidden_dependencies: ["apps/web"] }
  ];
  await put(root, "repo-boundaries.json", JSON.stringify({ schema_version: "1.0", components }));
  await put(root, "package.json", JSON.stringify({ name: "@fixture/root", version: "0.0.0", private: true, workspaces: ["apps/*", "services/*"] }));
  await put(root, "apps/web/package.json", JSON.stringify({ name: "@fixture/web", version: "0.0.0", private: true }));
  await put(root, "services/api/package.json", JSON.stringify({ name: "@fixture/api", version: "0.0.0", private: true }));
  await put(root, "package-lock.json", JSON.stringify({
    name: "@fixture/root",
    version: "0.0.0",
    lockfileVersion: 3,
    packages: {
      "": { name: "@fixture/root", version: "0.0.0", workspaces: ["apps/*", "services/*"] },
      "apps/web": { name: "@fixture/web", version: "0.0.0" },
      "services/api": { name: "@fixture/api", version: "0.0.0" },
      "node_modules/@fixture/web": { resolved: "apps/web", link: true }
    }
  }));
  return root;
}

function run(root) {
  return spawnSync(process.execPath, [cli, "--root", root, "--policy", policy, "--no-write"], { encoding: "utf8" });
}

async function expectViolation(ruleId, mutate) {
  const root = await fixture();
  await mutate(root);
  const result = run(root);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, new RegExp(`\\b${ruleId}\\b`));
}

test("정상 Fixture는 Exit 0과 위반 0건이다", async () => {
  const result = run(await fixture());
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /components=2 .*violations=0/);
});

test("Python 테스트 Cache는 Source 독립성 검사 대상이 아니다", async () => {
  const root = await fixture();
  await put(
    root,
    "services/api/.pytest_cache/forbidden.py",
    "from daon2.internal import CacheArtifact\n"
  );
  const result = run(root);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /violations=0/);
});

test("pytest pythonpath 도구 설정을 Package 경로 의존으로 오인하지 않는다", async () => {
  const root = await fixture();
  await rm(path.join(root, "services", "api", "package.json"));
  await put(
    root,
    "services/api/pyproject.toml",
    [
      "[project]",
      'name = "fixture"',
      'version = "0.0.0"',
      "",
      "[tool.pytest.ini_options]",
      'pythonpath = ["src"]',
      ""
    ].join("\n")
  );
  const result = run(root);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /violations=0/);
});

test("Python Package의 실제 path 직접 의존은 계속 차단한다", async () => {
  await expectViolation("PACKAGE_DAON_INTERNAL", async (root) => {
    await rm(path.join(root, "services", "api", "package.json"));
    await put(
      root,
      "services/api/pyproject.toml",
      [
        "[project]",
        'name = "fixture"',
        'version = "0.0.0"',
        "",
        "[tool.uv.sources]",
        'legacy = { path = "../legacy" }',
        ""
      ].join("\n")
    );
  });
});

test("Dependency Graph 금지 간선을 차단한다", async () => {
  await expectViolation("DEP_GRAPH_BOUNDARY", async (root) => put(root, "apps/web/package.json", JSON.stringify({ name: "@fixture/web", version: "0.0.0", dependencies: { "@fixture/api": "0.0.0" } })));
});

test("다른 Daon 내부 Package를 차단한다", async () => {
  await expectViolation("PACKAGE_DAON_INTERNAL", async (root) => put(root, "apps/web/package.json", JSON.stringify({ name: "@fixture/web", version: "0.0.0", dependencies: { "@daon2/internal": "1.0.0" } })));
});

test("정상 Root Manifest와 Workspace Lockfile은 Exit 0이다", async () => {
  const result = run(await fixture());
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("Root package.json의 다른 Daon 내부 Package를 차단한다", async () => {
  await expectViolation("PACKAGE_DAON_INTERNAL", async (root) => put(root, "package.json", JSON.stringify({ name: "@fixture/root", dependencies: { "@daon2/internal": "1.0.0" } })));
});

test("Lockfile의 다른 Daon 내부 Package identity를 차단한다", async () => {
  await expectViolation("PACKAGE_DAON_INTERNAL", async (root) => put(root, "package-lock.json", JSON.stringify({ lockfileVersion: 3, packages: { "": {}, "node_modules/@daon3/internal": { version: "1.0.0" } } })));
});

test("Lockfile의 금지 로컬 경로 의존을 차단한다", async () => {
  await expectViolation("PACKAGE_DAON_INTERNAL", async (root) => put(root, "package-lock.json", JSON.stringify({ lockfileVersion: 3, packages: { "": { dependencies: { legacy: "file:../Daon2/internal" } } } })));
});

test("구성요소 간 Source 직접 Import를 차단한다", async () => {
  await expectViolation("SOURCE_IMPORT_BOUNDARY", async (root) => put(root, "apps/web/client/main.ts", "import '../../../services/api/src/internal.py';\n"));
});

test("Python의 다른 Daon 제품 Module Import를 차단한다", async () => {
  await expectViolation("SOURCE_IMPORT_BOUNDARY", async (root) => put(root, "services/api/src/main.py", "from daon2.internal import Client\n"));
});

test("JavaScript 재수출의 구성요소 Source 직접 참조를 차단한다", async () => {
  await expectViolation("SOURCE_IMPORT_BOUNDARY", async (root) => put(root, "apps/web/client/reexport.ts", "export { value } from '../../../services/api/src/internal.js';\n"));
});

test("Python 주석·일반 문자열·정상 외부 Package Import는 오탐하지 않는다", async () => {
  const root = await fixture();
  await put(root, "services/api/src/main.py", "# from daon2.internal import Client\nNOTE = 'import daon3.internal'\nimport requests\n");
  const result = run(root);
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("외부 절대 경로를 차단한다", async () => {
  await expectViolation("PATH_EXTERNAL_ABSOLUTE", async (root) => put(root, "apps/web/client/config.ts", "export const source = 'D:\\\\Project\\\\Daon2\\\\data';\n"));
});

test("다른 Daon Runtime Image를 차단한다", async () => {
  await expectViolation("RUNTIME_IMAGE_DAON", async (root) => put(root, "Dockerfile", "FROM registry.example/daon3-api:latest\n"));
});

test("Browser의 API 절대주소 호출을 차단한다", async () => {
  await expectViolation("BROWSER_DIRECT_API", async (root) => put(root, "apps/web/client/page.tsx", "'use client';\nfetch('http://localhost:8000/api/items');\n"));
});

test("승인 Adapter 밖 Connector 우회를 차단한다", async () => {
  await expectViolation("CONNECTOR_BYPASS", async (root) => put(root, "apps/web/client/connector.ts", "import client from 'daon-internal-client';\n"));
});

test("Policy Schema 오류는 Exit 2이다", async () => {
  const root = await fixture();
  await put(root, "bad-policy.json", "{}");
  const result = spawnSync(process.execPath, [cli, "--root", root, "--policy", path.join(root, "bad-policy.json"), "--no-write"], { encoding: "utf8" });
  assert.equal(result.status, 2, result.stdout + result.stderr);
  assert.match(result.stderr, /POLICY_OR_SCAN_ERROR/);
});

test("손상된 Lockfile은 Exit 2이다", async () => {
  const root = await fixture();
  await put(root, "package-lock.json", "{not-json");
  const result = run(root);
  assert.equal(result.status, 2, result.stdout + result.stderr);
  assert.match(result.stderr, /POLICY_OR_SCAN_ERROR/);
});

test("승인 기준 Lockfile 누락은 Exit 2이다", async () => {
  const root = await fixture();
  await rm(path.join(root, "package-lock.json"));
  const result = run(root);
  assert.equal(result.status, 2, result.stdout + result.stderr);
  assert.match(result.stderr, /POLICY_OR_SCAN_ERROR/);
});
