import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const successStatus = "SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE";
const root = path.resolve(process.env.IOS_REPOSITORY_ROOT ?? path.resolve(import.meta.dirname, "../../../.."));
const evidenceDir = path.resolve(process.env.IOS_EVIDENCE_DIR ?? path.join(root, "artifacts/ios-phase-a/evidence"));
const requiredSteps = ["checkout", "setup_node", "xcode", "setup_uv", "node_npm", "cocoapods", "npm_ci", "portable_contracts", "pods", "simulator", "build", "ui_tests", "simulator_verification"];
const requiredFiles = [
  "apps/mobile/ios/Podfile",
  "apps/mobile/ios/Podfile.lock",
  "apps/mobile/ios/Daon.xcodeproj/project.pbxproj",
  "apps/mobile/ios/Daon/Info.plist",
  "apps/mobile/ios/Daon/AppDelegate.swift",
  "apps/mobile/ios/Daon/DaonIOSHost.swift",
  "apps/mobile/ios/DaonUITests/DaonUITests.swift",
  "apps/mobile/ios/ci/build-simulator.sh",
  "apps/mobile/ios/ci/verify-simulator.sh",
  ".github/workflows/release-1-ios-phase-a.yml"
];
const requiredResultBundles = ["DaonUITests.xcresult", "permission-grant-initial.xcresult", "permission-revoke.xcresult", "permission-grant-again.xcresult"];

await mkdir(evidenceDir, { recursive: true });

async function readText(absolutePath) {
  try { return await readFile(absolutePath, "utf8"); }
  catch (error) { if (error?.code === "ENOENT") return null; throw error; }
}

async function fileEvidence(relativePath) {
  const absolute = path.join(root, relativePath);
  try {
    const bytes = await readFile(absolute);
    return { path: relativePath.replaceAll("\\", "/"), bytes: (await stat(absolute)).size, sha256: createHash("sha256").update(bytes).digest("hex").toUpperCase(), missing: false };
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return { path: relativePath.replaceAll("\\", "/"), bytes: 0, sha256: null, missing: true };
  }
}

const outcomeText = await readText(path.join(evidenceDir, "workflow-outcomes.json"));
let outcomes = null;
try { outcomes = outcomeText ? JSON.parse(outcomeText) : null; } catch { outcomes = null; }
const phaseStatus = (await readText(path.join(evidenceDir, "phase-a-status.txt")))?.trim() ?? null;
const files = await Promise.all(requiredFiles.map(fileEvidence));
const xctestResultBundles = [];
for (const name of requiredResultBundles) {
  try { await stat(path.join(evidenceDir, name)); xctestResultBundles.push({ path: name, missing: false }); }
  catch (error) {
    if (error?.code !== "ENOENT") throw error;
    xctestResultBundles.push({ path: name, missing: true });
  }
}

const gitSha = process.env.GITHUB_SHA ?? "unknown";
const runner = { name: process.env.RUNNER_NAME ?? "unknown", image: process.env.ImageOS ?? "unknown", image_version: process.env.ImageVersion ?? "unknown" };
const toolchain = {
  node: process.version,
  npm: process.env.IOS_NPM_VERSION ?? "unknown",
  uv: process.env.IOS_UV_VERSION ?? "unknown",
  xcode: process.env.IOS_XCODE_VERSION ?? "unknown",
  xcode_build: process.env.IOS_XCODE_BUILD_VERSION ?? "unknown",
  sdk: process.env.IOS_SDK_VERSION ?? "unknown",
  cocoapods: process.env.IOS_COCOAPODS_VERSION ?? "unknown",
  ruby: process.env.IOS_RUBY_VERSION ?? "unknown",
  bundler: process.env.IOS_BUNDLER_VERSION ?? "unknown"
};
const simulator = {
  runtime: process.env.IOS_SIMULATOR_RUNTIME ?? "unknown",
  device: process.env.IOS_SIMULATOR_DEVICE ?? "unknown",
  udid: process.env.SIMULATOR_UDID ?? "unknown"
};
const isUnknown = (value) => !value || value === "unknown";
const failedSteps = requiredSteps.filter((step) => outcomes?.steps?.[step] === "failure");
const incompleteReasons = [];

if (!outcomes) incompleteReasons.push("workflow_outcomes:missing_or_invalid");
for (const step of requiredSteps) {
  const outcome = outcomes?.steps?.[step];
  if (!outcome) incompleteReasons.push(`step:${step}:missing`);
  else if (outcome !== "success" && outcome !== "failure") incompleteReasons.push(`step:${step}:${outcome}`);
}
if (!/^[0-9a-f]{40}$/i.test(gitSha)) incompleteReasons.push("git_sha:unknown_or_invalid");
if (outcomes?.git_sha && outcomes.git_sha !== gitSha) incompleteReasons.push("git_sha:mismatch");
for (const [key, value] of Object.entries(toolchain)) if (isUnknown(value)) incompleteReasons.push(`toolchain:${key}:unknown`);
for (const [key, value] of Object.entries(simulator)) if (isUnknown(value)) incompleteReasons.push(`simulator:${key}:unknown`);
for (const [key, value] of Object.entries(runner)) if (isUnknown(value)) incompleteReasons.push(`runner:${key}:unknown`);
for (const file of files) if (file.missing) incompleteReasons.push(`file:${file.path}:missing`);
for (const bundle of xctestResultBundles) if (bundle.missing) incompleteReasons.push(`xctest_result:${bundle.path}:missing`);
if (!phaseStatus) incompleteReasons.push("phase_status:missing");
else if (phaseStatus !== successStatus) incompleteReasons.push(`phase_status:invalid:${phaseStatus}`);

const verificationCompleted = failedSteps.length === 0 && incompleteReasons.length === 0;
const status = failedSteps.length > 0 ? "FAILED" : verificationCompleted ? successStatus : "INCOMPLETE";
const manifest = {
  schema_version: "1.1",
  status,
  verification_completed: verificationCompleted,
  failed_steps: failedSteps,
  incomplete_reasons: incompleteReasons,
  workflow_outcomes: outcomes?.steps ?? {},
  git_sha: gitSha,
  runner,
  toolchain,
  simulator,
  code_signing_allowed: false,
  signing_assets_created: false,
  xctest_result_bundles: xctestResultBundles,
  files
};
await writeFile(path.join(evidenceDir, "evidence-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
