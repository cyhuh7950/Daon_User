import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const writer = path.join(root, "apps/mobile/ios/ci/write-evidence.mjs");
const requiredFiles = [
  "apps/mobile/ios/Podfile", "apps/mobile/ios/Podfile.lock", "apps/mobile/ios/Daon.xcodeproj/project.pbxproj",
  "apps/mobile/ios/Daon/Info.plist", "apps/mobile/ios/Daon/AppDelegate.swift", "apps/mobile/ios/Daon/DaonIOSHost.swift",
  "apps/mobile/ios/DaonUITests/DaonUITests.swift", "apps/mobile/ios/ci/build-simulator.sh",
  "apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh", "apps/mobile/ios/ci/verify-simulator.sh", ".github/workflows/release-1-ios-phase-a.yml"
];
const requiredSteps = ["checkout", "setup_node", "xcode", "setup_uv", "node_npm", "cocoapods", "npm_ci", "portable_contracts", "pods", "simulator", "build", "ui_tests", "simulator_verification"];

async function runFixture({ stepOverrides = {}, envOverrides = {}, phaseStatus = "SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE" } = {}) {
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-ios-evidence-"));
  const evidenceDir = path.join(fixtureRoot, "artifacts/ios-phase-a/evidence");
  await mkdir(evidenceDir, { recursive: true });
  for (const relativePath of requiredFiles) {
    const absolute = path.join(fixtureRoot, relativePath);
    await mkdir(path.dirname(absolute), { recursive: true });
    await writeFile(absolute, `fixture:${relativePath}\n`);
  }
  for (const bundle of ["DaonUITests.xcresult", "permission-grant-initial.xcresult", "permission-revoke.xcresult", "permission-grant-again.xcresult"]) {
    await mkdir(path.join(evidenceDir, bundle), { recursive: true });
  }
  const steps = Object.fromEntries(requiredSteps.map((step) => [step, "success"]));
  Object.assign(steps, stepOverrides);
  await writeFile(path.join(evidenceDir, "workflow-outcomes.json"), `${JSON.stringify({ git_sha: "a".repeat(40), steps })}\n`);
  if (phaseStatus !== null) await writeFile(path.join(evidenceDir, "phase-a-status.txt"), `${phaseStatus}\n`);
  try {
    execFileSync(process.execPath, [writer], { cwd: root, env: {
      ...process.env, IOS_REPOSITORY_ROOT: fixtureRoot, IOS_EVIDENCE_DIR: evidenceDir,
      GITHUB_SHA: "a".repeat(40), RUNNER_NAME: "GitHub Actions 1", ImageOS: "macos26", ImageVersion: "20260720.1",
      IOS_NPM_VERSION: "11.12.1", IOS_UV_VERSION: "uv 0.11.2", IOS_XCODE_VERSION: "26.6", IOS_XCODE_BUILD_VERSION: "17G86", IOS_SDK_VERSION: "26.0",
      IOS_COCOAPODS_VERSION: "1.16.2", IOS_RUBY_VERSION: "ruby 3.3.0", IOS_BUNDLER_VERSION: "Bundler version 2.6.0",
      IOS_SIMULATOR_RUNTIME: "iOS 26.0", IOS_SIMULATOR_DEVICE: "iPhone 17 Pro",
      SIMULATOR_UDID: "11111111-2222-3333-4444-555555555555", ...envOverrides
    }});
    return JSON.parse(await readFile(path.join(evidenceDir, "evidence-manifest.json"), "utf8"));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
}

test("iOS Evidence Manifest는 모든 필수 Outcome·식별자·상태 파일이 유효할 때만 성공한다", async () => {
  const manifest = await runFixture();
  assert.equal(manifest.status, "SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE");
  assert.equal(manifest.verification_completed, true);
  assert.deepEqual(manifest.failed_steps, []);
  assert.deepEqual(manifest.incomplete_reasons, []);
  assert.equal(manifest.toolchain.uv, "uv 0.11.2");
});

test("iOS Evidence Manifest는 setup_uv 실패·Skip과 uv unknown을 성공으로 기록하지 않는다", async () => {
  const failed = await runFixture({ stepOverrides: { setup_uv: "failure" } });
  assert.equal(failed.status, "FAILED");
  assert.equal(failed.verification_completed, false);
  assert.deepEqual(failed.failed_steps, ["setup_uv"]);

  const skipped = await runFixture({ stepOverrides: { setup_uv: "skipped" } });
  assert.equal(skipped.status, "INCOMPLETE");
  assert.equal(skipped.verification_completed, false);
  assert.ok(skipped.incomplete_reasons.includes("step:setup_uv:skipped"));

  const unknown = await runFixture({ envOverrides: { IOS_UV_VERSION: "unknown" } });
  assert.equal(unknown.status, "INCOMPLETE");
  assert.equal(unknown.verification_completed, false);
  assert.ok(unknown.incomplete_reasons.includes("toolchain:uv:unknown"));
});

test("iOS Evidence Manifest는 CocoaPods 버전 증거가 비면 성공하지 않는다", async () => {
  const manifest = await runFixture({ envOverrides: { IOS_COCOAPODS_VERSION: "" } });
  assert.equal(manifest.status, "INCOMPLETE");
  assert.equal(manifest.verification_completed, false);
  assert.ok(manifest.incomplete_reasons.includes("toolchain:cocoapods:unknown"));
});

test("iOS Evidence Manifest는 필수 Step 실패를 FAILED로 기록하고 성공 상태를 금지한다", async () => {
  const manifest = await runFixture({ stepOverrides: { build: "failure" } });
  assert.equal(manifest.status, "FAILED");
  assert.equal(manifest.verification_completed, false);
  assert.deepEqual(manifest.failed_steps, ["build"]);
  assert.notEqual(manifest.status, "SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE");
});

test("iOS Evidence Manifest는 누락 Outcome·unknown 식별자·상태 파일 누락을 INCOMPLETE로 기록한다", async () => {
  const manifest = await runFixture({ stepOverrides: { ui_tests: undefined }, envOverrides: { IOS_SDK_VERSION: "unknown" }, phaseStatus: null });
  assert.equal(manifest.status, "INCOMPLETE");
  assert.equal(manifest.verification_completed, false);
  assert.ok(manifest.incomplete_reasons.includes("step:ui_tests:missing"));
  assert.ok(manifest.incomplete_reasons.includes("toolchain:sdk:unknown"));
  assert.ok(manifest.incomplete_reasons.includes("phase_status:missing"));
});
