import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const braces = require("../../vendor/braces");
const installedBraces = require("braces");
const nested = (depth) => `${"{".repeat(depth)}a,b${"}".repeat(depth)}`;

test("100 nested braces retain the existing parse, compile and expand behavior", () => {
  const pattern = nested(100);
  const ast = braces.parse(pattern);
  assert.equal(ast.type, "root");
  assert.deepEqual(braces.compile(pattern), braces.compile(ast));
  assert.deepEqual(braces.expand(pattern), [
    `${"{".repeat(99)}a${"}".repeat(99)}`,
    `${"{".repeat(99)}b${"}".repeat(99)}`
  ]);
});

test("101 nested braces are rejected before recursive compilation or expansion", () => {
  const pattern = nested(101);
  for (const operation of [braces.parse, braces.compile, braces.expand]) {
    assert.throws(() => operation(pattern), SyntaxError);
  }
});

test("normal and escaped brace patterns preserve their results", () => {
  assert.deepEqual(braces.expand("src/{a,b}.js"), ["src/a.js", "src/b.js"]);
  assert.deepEqual(braces.expand("src/\\{literal\\}.js"), ["src/{literal}.js"]);
  assert.deepEqual(braces.expand("{a,{b,c}}"), ["a", "b", "c"]);
  assert.deepEqual(braces.expand('src/{"a,b",c}.js'), ["src/a,b.js", "src/c.js"]);
  assert.deepEqual(braces.expand("src/{*.ts,*.tsx}"), ["src/*.ts", "src/*.tsx"]);
});

test("the existing maximum-length rejection remains active", () => {
  assert.throws(() => braces.parse("a".repeat(65_537)), SyntaxError);
});

test("the vendored distribution retains its upstream MIT license", async () => {
  const license = await readFile(path.resolve("vendor/braces/LICENSE"), "utf8");
  assert.match(license, /MIT License/);
  assert.match(license, /Jon Schlinkert/);
});

test("the vendored runtime does not pull upstream development tools", async () => {
  const manifest = JSON.parse(await readFile(path.resolve("vendor/braces/package.json"), "utf8"));
  assert.equal(manifest.devDependencies, undefined);
  assert.equal(manifest.scripts, undefined);
});

test("the lockfile resolves braces to the local patched source", async () => {
  const lock = JSON.parse(await readFile(path.resolve("package-lock.json"), "utf8"));
  const installed = lock.packages["node_modules/braces"];
  assert.equal(installed.resolved, "file:vendor/braces-3.0.3.tgz");
  assert.match(installed.integrity, /^sha512-/);
});

test("the installed consumer receives the same depth guard", () => {
  assert.throws(() => installedBraces.parse(nested(101)), SyntaxError);
});
