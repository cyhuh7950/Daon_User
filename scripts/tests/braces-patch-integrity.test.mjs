import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixtureParent = path.join(repositoryRoot, "dist");

async function fixture(t) {
  await mkdir(fixtureParent, { recursive: true });
  const root = await mkdtemp(path.join(fixtureParent, ".braces-integrity-"));
  if (!root.startsWith(`${fixtureParent}${path.sep}`)) throw new Error("fixture escaped dist");
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "vendor"), { recursive: true });
  await mkdir(path.join(root, "node_modules"), { recursive: true });
  await cp(path.join(repositoryRoot, "vendor/braces"), path.join(root, "vendor/braces"), { recursive: true });
  await cp(path.join(repositoryRoot, "vendor/braces-3.0.3.tgz"), path.join(root, "vendor/braces-3.0.3.tgz"));
  await cp(path.join(repositoryRoot, "node_modules/braces"), path.join(root, "node_modules/braces"), { recursive: true });
  await cp(path.join(repositoryRoot, "package.json"), path.join(root, "package.json"));
  await cp(path.join(repositoryRoot, "package-lock.json"), path.join(root, "package-lock.json"));
  return root;
}

const tree = { name: "@daon-user/root", dependencies: { braces: { version: "3.0.3", resolved: "file:vendor/braces-3.0.3.tgz" } } };
const paths = (root) => [path.join(root, "node_modules/braces")];

test("verified source, tarball, lock and installed bytes are accepted", async (t) => {
  const root = await fixture(t);
  const { verifyBracesPatch } = await import("../verify-braces-patch.mjs");
  const result = await verifyBracesPatch({ root, npmTree: tree, npmPaths: paths(root) });
  assert.equal(result.installCount, 1);
  assert.ok(result.fileCount >= 8);
});

test("a changed installed parser is rejected", async (t) => {
  const root = await fixture(t);
  await writeFile(path.join(root, "node_modules/braces/lib/parse.js"), "module.exports = () => null;\n");
  const { verifyBracesPatch } = await import("../verify-braces-patch.mjs");
  await assert.rejects(verifyBracesPatch({ root, npmTree: tree, npmPaths: paths(root) }), /installed.*mismatch/i);
});

test("a changed source or tarball is rejected against the lock integrity", async (t) => {
  const root = await fixture(t);
  const source = path.join(root, "vendor/braces/lib/parse.js");
  await writeFile(source, `${await readFile(source, "utf8")}\n// tampered\n`);
  const { verifyBracesPatch } = await import("../verify-braces-patch.mjs");
  await assert.rejects(verifyBracesPatch({ root, npmTree: tree, npmPaths: paths(root) }), /source.*mismatch/i);
});

test("an unpatched duplicate installation is rejected", async (t) => {
  const root = await fixture(t);
  const duplicate = path.join(root, "node_modules/other/node_modules/braces");
  await cp(path.join(repositoryRoot, "vendor/braces"), duplicate, { recursive: true });
  await writeFile(path.join(duplicate, "lib/parse.js"), "module.exports = () => null;\n");
  const { verifyBracesPatch } = await import("../verify-braces-patch.mjs");
  await assert.rejects(verifyBracesPatch({ root, npmTree: tree, npmPaths: paths(root) }), /duplicate|installed.*mismatch/i);
});

test("missing override, registry lock or malformed npm tree is rejected", async (t) => {
  const root = await fixture(t);
  const { verifyBracesPatch } = await import("../verify-braces-patch.mjs");
  const manifestPath = path.join(root, "package.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  delete manifest.overrides.braces;
  await writeFile(manifestPath, JSON.stringify(manifest));
  await assert.rejects(verifyBracesPatch({ root, npmTree: tree, npmPaths: paths(root) }), /override/i);
  manifest.overrides.braces = "file:vendor/braces-3.0.3.tgz";
  await writeFile(manifestPath, JSON.stringify(manifest));
  const lockPath = path.join(root, "package-lock.json");
  const lock = JSON.parse(await readFile(lockPath, "utf8"));
  lock.packages["node_modules/braces"].resolved = "https://registry.npmjs.org/braces/-/braces-3.0.3.tgz";
  await writeFile(lockPath, JSON.stringify(lock));
  await assert.rejects(verifyBracesPatch({ root, npmTree: tree, npmPaths: paths(root) }), /lock/i);
  lock.packages["node_modules/braces"].resolved = "file:vendor/braces-3.0.3.tgz";
  await writeFile(lockPath, JSON.stringify(lock));
  await assert.rejects(verifyBracesPatch({ root, npmTree: { problems: ["invalid"] }, npmPaths: paths(root) }), /npm tree/i);
});

test("the command-line verifier runs without unsafe shell argument warnings", () => {
  const result = spawnSync(process.execPath, ["scripts/verify-braces-patch.mjs"], {
    cwd: repositoryRoot, encoding: "utf8", timeout: 30_000
  });
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(result.stderr, /DEP0190/);
});
