import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const iosRoot = path.join(root, "apps/mobile/ios");

async function read(relativePath) {
  return readFile(path.join(root, relativePath), "utf8");
}

async function readJson(relativePath) {
  return JSON.parse(await read(relativePath));
}

async function listFiles(directory) {
  const output = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) output.push(...await listFiles(absolute));
    else if (entry.isFile()) output.push(absolute);
  }
  return output;
}

test("iOS Project는 승인 Bundle ID·표시명·Template Deployment Target을 고정한다", async () => {
  const project = await read("apps/mobile/ios/Daon.xcodeproj/project.pbxproj");
  const info = await read("apps/mobile/ios/Daon/Info.plist");
  const appJson = await readJson("apps/mobile/app.json");
  assert.match(project, /PRODUCT_BUNDLE_IDENTIFIER = com\.sinsan\.daon;/);
  assert.match(project, /IPHONEOS_DEPLOYMENT_TARGET = 15\.1;/);
  assert.match(project, /PRODUCT_NAME = Daon;/);
  assert.match(info, /<key>CFBundleDisplayName<\/key>\s*<string>Daon<\/string>/);
  assert.deepEqual(appJson, { name: "Daon", displayName: "Daon" });
});

test("iOS Project는 승인 Community Template Commit과 RN Pin을 기록한다", async () => {
  const provenance = await readJson("apps/mobile/ios/template-provenance.json");
  assert.deepEqual(provenance, {
    repository: "react-native-community/template",
    branch: "0.86-stable",
    commit: "4d7c716d7afddc03ed73ca49c1102a92a0a9ff71",
    react_native: "0.86.0"
  });
  const podfile = await read("apps/mobile/ios/Podfile");
  assert.match(podfile, /target 'Daon'/);
  assert.match(podfile, /prepare_react_native_project!/);
  assert.match(podfile, /use_react_native!/);
});

test("Info.plist는 최소 권한 설명과 URL Scheme만 선언하고 내부 Network 예외를 금지한다", async () => {
  const info = await read("apps/mobile/ios/Daon/Info.plist");
  for (const key of ["NSCameraUsageDescription", "NSMicrophoneUsageDescription", "CFBundleURLTypes", "sinsan-daon"]) {
    assert.match(info, new RegExp(key));
  }
  assert.doesNotMatch(info, /NSPhotoLibraryUsageDescription|NSLocationWhenInUseUsageDescription|NSAllowsArbitraryLoads|NSAllowsLocalNetworking/);
});

test("iOS Host Adapter는 Route·Lifecycle·Deep Link·권한·Settings 경계를 제공한다", async () => {
  const nativeHost = await read("apps/mobile/ios/Daon/DaonIOSHost.swift");
  const appDelegate = await read("apps/mobile/ios/Daon/AppDelegate.swift");
  const host = await read("apps/mobile/src/platform/ios-host.ts");
  for (const token of ["saveNavigationRoute", "restoreNavigationRoute", "consumePendingDeepLink", "requestPermission", "checkPermission", "openApplicationSettings", "UserDefaults", "AVCaptureDevice", "UNUserNotificationCenter"]) {
    assert.match(nativeHost, new RegExp(token));
  }
  for (const token of ["applicationDidEnterBackground", "applicationWillEnterForeground", "applicationDidBecomeActive", "open url", "recordPendingDeepLink"]) {
    assert.match(appDelegate, new RegExp(token));
  }
  for (const token of ["AppState", "Linking", "restoreNavigationRoute", "saveNavigationRoute", "requestPermission"]) {
    assert.match(host, new RegExp(token));
  }
  assert.doesNotMatch(`${nativeHost}\n${appDelegate}\n${host}`, /fetch\s*\(|https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("UserDefaults에는 승인 Route와 비민감 Lifecycle 상태만 저장한다", async () => {
  const nativeHost = await read("apps/mobile/ios/Daon/DaonIOSHost.swift");
  assert.match(nativeHost, /allowedNativeRoutes/);
  assert.match(nativeHost, /native_route_key/);
  assert.match(nativeHost, /lifecycle_state/);
  assert.doesNotMatch(nativeHost, /credential|password|secret|api[_-]?key|source[_-]?content|auth[_-]?token|provider[_-]?url/i);
});

test("iOS App 진입점은 공용 Shell에 iOS Adapter를 주입하고 Android Adapter 동작을 보존한다", async () => {
  const app = await read("apps/mobile/src/App.tsx");
  const mobileShell = await read("apps/mobile/src/MobileShell.tsx");
  for (const token of ["iosPermissionAdapter", "subscribeIOSDeepLinks", "subscribeIOSLifecycle", "Platform.OS === \"ios\""]) {
    assert.match(app, new RegExp(token));
  }
  assert.match(app, /Platform\.OS === "android"/);
  assert.match(mobileShell, /permissionAdapter/);
  assert.doesNotMatch(mobileShell, /ios\/|android\//);
});

test("iOS Simulator Build 계약은 Signing 자산을 요구하거나 저장하지 않는다", async () => {
  const project = await read("apps/mobile/ios/Daon.xcodeproj/project.pbxproj");
  const workflow = await read(".github/workflows/release-1-ios-phase-a.yml");
  const files = await listFiles(iosRoot);
  const relative = files.map((file) => path.relative(iosRoot, file).replaceAll("\\", "/"));
  assert.doesNotMatch(project, /DEVELOPMENT_TEAM|PROVISIONING_PROFILE|CODE_SIGN_IDENTITY/);
  assert.match(workflow, /CODE_SIGNING_ALLOWED=NO/);
  assert.equal(relative.some((file) => /\.p12$|\.mobileprovision$|\.cer$|private[_-]?key/i.test(file)), false);
  assert.doesNotMatch(workflow, /secrets\.|DEVELOPMENT_TEAM|PROVISIONING_PROFILE|CODE_SIGN_IDENTITY/);
});

test("iOS 전용 Gate와 macOS Build 진입점은 기존 Mobile 표준 명령을 보존한다", async () => {
  const rootPackage = await readJson("package.json");
  const mobilePackage = await readJson("apps/mobile/package.json");
  assert.equal(rootPackage.scripts["verify:ios-native"], "node --test scripts/tests/ios-native-shell.test.mjs scripts/tests/native-deep-link.test.mjs scripts/tests/ios-phase-a-evidence.test.mjs");
  assert.equal(rootPackage.scripts["build:ios-simulator"], "bash apps/mobile/ios/ci/build-simulator.sh");
  assert.equal(mobilePackage.scripts.ios, "npm --prefix ../.. run build:ios-simulator");
  for (const name of ["lint", "type", "unit", "contract", "build"]) assert.ok(mobilePackage.scripts[name]);
  assert.match(rootPackage.scripts["verify:mobile"], /verify:ios-native/);
});

test("GitHub macOS Workflow는 exact Pin·Simulator 검증·실패 Artifact 계약을 고정한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const simulatorPreparation = await read("apps/mobile/ios/ci/prepare-simulator.mjs");
  assert.deepEqual(workflow.on.pull_request.branches, ["codex/release-1"]);
  assert.deepEqual(workflow.permissions, { contents: "read" });
  const job = workflow.jobs["ios-phase-a"];
  assert.equal(job["runs-on"], "macos-26");
  assert.ok(job["timeout-minutes"] > 0);
  const serialized = JSON.stringify(workflow);
  for (const token of ["fetch-depth", "Xcode_26.6.app", "1.16.2", "24.18.0", "11.12.1", "CODE_SIGNING_ALLOWED=NO", "upload-artifact@v6", "if-no-files-found", "always()", "github.sha"]) {
    assert.match(serialized, new RegExp(token.replaceAll(".", "\\.")));
  }
  assert.match(simulatorPreparation, /simctl[\s\S]*create/);
  assert.match(simulatorPreparation, /simctl[\s\S]*boot/);
  const simulatorVerification = await read("apps/mobile/ios/ci/verify-simulator.sh");
  for (const action of ["install", "launch", "openurl", "terminate"]) assert.match(simulatorVerification, new RegExp(`simctl ${action}`));
  assert.doesNotMatch(serialized, /macos-latest|xcode-select.*latest|continue-on-error/);
});

test("CI Script는 8 Route·비정상 Deep Link·Lifecycle·권한·Crash/Secret·종료를 Fail-close 검증한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  for (const route of ["Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings"]) {
    assert.match(script, new RegExp(route));
  }
  for (const token of ["UnknownRoute", "%48ome", "Home%2Fextra", "Home?route=Inbox", "Home#Inbox", "terminate", "launch", "Crash", "secret", "SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE"]) {
    assert.match(script, new RegExp(token.replaceAll("?", "\\?").replaceAll(".", "\\."), "i"));
  }
  for (const permission of ["camera", "microphone", "notifications"]) {
    assert.match(script, new RegExp(`privacy[^\\n]*${permission}`, "i"));
  }
  assert.match(script, /run_permission_phase grant-initial grant GRANTED/);
  assert.match(script, /run_permission_phase revoke revoke DENIED/);
  assert.match(script, /run_permission_phase grant-again grant GRANTED/);
});

test("Binary 금지 Pattern은 Source 자기탐지 없이 Runtime에서 기존 Client 내부 API 토큰을 탐지한다", async () => {
  const scriptPath = path.join(iosRoot, "ci/verify-simulator.sh");
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const forbiddenClientToken = ["NEXT", "PUBLIC", "API", "BASE", "URL"].join("_");
  assert.equal(script.includes(forbiddenClientToken), false);
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const runtimePattern = execFileSync(bash, [scriptPath, "--print-binary-scan-pattern"], { encoding: "utf8" }).trim();
  assert.equal(new RegExp(runtimePattern, "i").test(`bundle:${forbiddenClientToken}`), true);
  for (const retained of ["localhost", "127\\.0\\.0\\.1", "host\\.docker\\.internal", "api[_-]?key", "client[_-]?secret"]) {
    assert.equal(runtimePattern.includes(retained), true);
  }
  assert.match(script, /strings "\$\{APP_PATH\}\/Daon" \| grep -Eiq "\$\{BINARY_FORBIDDEN_PATTERN\}"/);
});

test("GitHub macOS Workflow와 Manifest는 모든 필수 Step Outcome을 Fail-close 연결한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const writer = await read("apps/mobile/ios/ci/write-evidence.mjs");
  const steps = workflow.jobs["ios-phase-a"].steps;
  const requiredIds = ["checkout", "setup-node", "xcode", "node-npm", "cocoapods", "npm-ci", "portable-contracts", "pods", "simulator", "build", "ui-tests", "simulator-verification"];
  for (const id of requiredIds) assert.ok(steps.some((step) => step.id === id), `missing required workflow step id: ${id}`);
  const serialized = JSON.stringify(workflow);
  for (const token of ["IOS_STEP_CHECKOUT", "IOS_STEP_SETUP_NODE", "IOS_STEP_NPM_CI", "verification_completed", "workflow-outcomes.json", "phase-a-status.txt"]) {
    assert.match(`${serialized}\n${writer}`, new RegExp(token.replaceAll(".", "\\.")));
  }
  assert.match(writer, /FAILED/);
  assert.match(writer, /INCOMPLETE/);
  const embeddedNodePrograms = steps.flatMap((step) => [...(step.run ?? "").matchAll(/node -e '([^']+)'/g)].map((match) => match[1]));
  assert.ok(embeddedNodePrograms.length >= 2);
  for (const source of embeddedNodePrograms) assert.doesNotThrow(() => new Function(source));
});

test("권한 Phase A는 Production 요청 버튼의 GRANTED·DENIED·재GRANTED를 XCTest Artifact로 검증한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const shell = await read("apps/mobile/src/MobileShell.tsx");
  for (const kind of ["camera", "microphone", "notification"]) assert.match(uiTests, new RegExp(`"${kind}"`));
  assert.match(uiTests, /\\\(kind\) 권한 요청/);
  for (const state of ["GRANTED", "DENIED"]) assert.match(uiTests, new RegExp(state));
  assert.match(uiTests, /권한 결과|DAON_PERMISSION_EXPECTED/);
  assert.match(shell, /requestPermission\(kind\)/);
  assert.match(shell, /권한 결과/);
  for (const phase of ["grant-initial", "revoke", "grant-again"]) assert.match(script, new RegExp(phase));
  assert.match(script, /test-without-building/);
  assert.match(script, /\.xcresult/);
});
