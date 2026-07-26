#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { runBuild } from "metro";
import { loadConfig } from "metro-config";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const mobileRoot = path.join(root, "apps/mobile");
const platformIndex = process.argv.indexOf("--platform");
const platform = platformIndex >= 0 ? process.argv[platformIndex + 1] : null;
if (platform !== "android" && platform !== "ios") throw new Error("MOBILE_BUNDLE_PLATFORM_MUST_BE_ANDROID_OR_IOS");

const temporaryRoot = await mkdtemp(path.join(tmpdir(), `daon-mobile-${platform}-`));
const outputPath = path.join(temporaryRoot, `${platform}.bundle.js`);
try {
  const config = await loadConfig({ cwd: mobileRoot, config: path.join(mobileRoot, "metro.config.cjs") });
  await runBuild(config, { entry: "index.js", out: outputPath, platform, dev: false, minify: true, sourceMap: false });
  const bundle = await readFile(outputPath);
  console.log(JSON.stringify({ platform, status: "PASS", entry: "apps/mobile/index.js", production: true, bytes: bundle.byteLength, sha256: createHash("sha256").update(bundle).digest("hex").toUpperCase(), native_build: `Deferred R1-M3-${platform === "android" ? "05" : "06"}` }));
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
