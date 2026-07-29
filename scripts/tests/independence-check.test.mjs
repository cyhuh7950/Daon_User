import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
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
  await put(root, "apps/web/package.json", JSON.stringify({ name: "@fixture/web", version: "0.0.0", private: true }));
  await put(root, "services/api/package.json", JSON.stringify({ name: "@fixture/api", version: "0.0.0", private: true }));
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

test("Dependency Graph 금지 간선을 차단한다", async () => {
  await expectViolation("DEP_GRAPH_BOUNDARY", async (root) => put(root, "apps/web/package.json", JSON.stringify({ name: "@fixture/web", version: "0.0.0", dependencies: { "@fixture/api": "0.0.0" } })));
});

test("다른 Daon 내부 Package를 차단한다", async () => {
  await expectViolation("PACKAGE_DAON_INTERNAL", async (root) => put(root, "apps/web/package.json", JSON.stringify({ name: "@fixture/web", version: "0.0.0", dependencies: { "@daon2/internal": "1.0.0" } })));
});

test("구성요소 간 Source 직접 Import를 차단한다", async () => {
  await expectViolation("SOURCE_IMPORT_BOUNDARY", async (root) => put(root, "apps/web/client/main.ts", "import '../../../services/api/src/internal.py';\n"));
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
