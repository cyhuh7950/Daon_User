import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
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

test("LaunchScreen은 승인 Template의 Interface Builder 문서 계약과 ID 참조 무결성을 가진다", async () => {
  const storyboard = await read("apps/mobile/ios/Daon/LaunchScreen.storyboard");
  const provenance = await readJson("apps/mobile/ios/template-provenance.json");
  assert.equal(provenance.commit, "4d7c716d7afddc03ed73ca49c1102a92a0a9ff71");
  const { XMLValidator } = await import("fast-xml-parser");
  assert.equal(XMLValidator.validate(storyboard), true);
  assert.match(storyboard, /<document [^>]*version="3\.0"[^>]*toolsVersion="15702"[^>]*targetRuntime="iOS\.CocoaTouch"[^>]*propertyAccessControl="none"[^>]*useAutolayout="YES"[^>]*launchScreen="YES"[^>]*useTraitCollections="YES"[^>]*useSafeAreas="YES"[^>]*colorMatched="YES"[^>]*initialViewController="01J-lp-oVM">/);
  assert.match(storyboard, /<dependencies>\s*<deployment identifier="iOS"\/>\s*<plugIn identifier="com\.apple\.InterfaceBuilder\.IBCocoaTouchPlugin" version="15704"\/>\s*<capability name="Safe area layout guides" minToolsVersion="9\.0"\/>\s*<capability name="documents saved in the Xcode 8 format" minToolsVersion="8\.0"\/>\s*<\/dependencies>/);
  assert.doesNotMatch(storyboard, /systemVersion=|sourceToolsVersion=/);

  const ids = [...storyboard.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  const references = [...storyboard.matchAll(/\s(?:initialViewController|firstItem|secondItem)="([^"]+)"/g)].map((match) => match[1]);
  for (const reference of references) assert.ok(ids.includes(reference), `missing storyboard reference: ${reference}`);
  assert.match(storyboard, /<label [^>]*text="Daon"[^>]*textAlignment="center"[^>]*id="GJd-Yh-RWb">/);
  assert.match(storyboard, /<fontDescription [^>]*type="boldSystem" pointSize="16"\/>/);
  assert.match(storyboard, /<color key="backgroundColor" systemColor="systemBackgroundColor"\/>/);
  assert.match(storyboard, /<constraint firstItem="GJd-Yh-RWb" firstAttribute="centerX" secondItem="Bcu-3y-fUS" secondAttribute="centerX"\/>/);
  assert.match(storyboard, /<constraint firstItem="GJd-Yh-RWb" firstAttribute="centerY" secondItem="Bcu-3y-fUS" secondAttribute="centerY"\/>/);
});

test("Swift Native Module의 React Bridge Type은 App Target Debug·Release에만 노출된다", async () => {
  const project = await read("apps/mobile/ios/Daon.xcodeproj/project.pbxproj");
  const bridge = await read("apps/mobile/ios/Daon/Daon-Bridging-Header.h");
  const host = await read("apps/mobile/ios/Daon/DaonIOSHost.swift");
  assert.equal(bridge.trim(), "#import <React/RCTBridgeModule.h>");
  assert.match(bridge, /RCTBridgeModule/);
  for (const type of ["RCTPromiseResolveBlock", "RCTPromiseRejectBlock"]) {
    assert.match(host, new RegExp(type));
  }
  for (const id of ["AC0000000000000000000001", "AC0000000000000000000002"]) {
    assert.match(project, new RegExp(`${id}[^\\n]*SWIFT_OBJC_BRIDGING_HEADER = Daon/Daon-Bridging-Header\\.h;`));
  }
  assert.equal((project.match(/SWIFT_OBJC_BRIDGING_HEADER = Daon\/Daon-Bridging-Header\.h;/g) ?? []).length, 2);
  for (const id of ["AC0000000000000000000003", "AC0000000000000000000004", "AC0000000000000000000005", "AC0000000000000000000006"]) {
    assert.doesNotMatch(project, new RegExp(`${id}[^\\n]*SWIFT_OBJC_BRIDGING_HEADER`));
  }
  assert.doesNotMatch(project, /HEADER_SEARCH_PATHS|LIBRARY_SEARCH_PATHS/);
  assert.match(host, /@objc\(DaonIOSHost\)/);
  assert.doesNotMatch(host, /NSObject,\s*RCTBridgeModule|static func moduleName/);
  assert.match(host, /@objc\s+static func requiresMainQueueSetup\(\) -> Bool/);
});

test("Swift Native Module 외부 Bridge는 승인 8개 Selector만 App Target에 1:1 Export한다", async () => {
  const bridge = await read("apps/mobile/ios/Daon/DaonIOSHostBridge.m");
  const host = await read("apps/mobile/ios/Daon/DaonIOSHost.swift");
  const project = await read("apps/mobile/ios/Daon.xcodeproj/project.pbxproj");
  const expectedMethods = [
    "saveNavigationRoute:(NSString *)route resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "restoreNavigationRoute:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "getLifecycleState:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "consumePendingDeepLink:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "checkPermission:(NSString *)kind resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "requestPermission:(NSString *)kind resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "openApplicationSettings:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject",
    "openNotificationSettings:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject"
  ];
  const selector = (signature) => [...signature.matchAll(/([A-Za-z][A-Za-z0-9]*):/g)].map((match) => match[1]).join(":") + ":";
  const isValid = (bridgeSource, swiftSource, projectSource) => {
    const externMethods = bridgeSource.split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.startsWith("RCT_EXTERN_METHOD("))
      .map((line) => line.slice("RCT_EXTERN_METHOD(".length, -1));
    const swiftSelectors = [...swiftSource.matchAll(/@objc\(([^)]+:[^)]*)\)/g)].map((match) => match[1]).sort();
    const appSources = projectSource.match(/A60000000000000000000001 \/\* Sources \*\/ = \{[^\n]*files = \(([^)]*)\)/)?.[1] ?? "";
    const uiTestSources = projectSource.match(/A60000000000000000000002 \/\* Sources \*\/ = \{[^\n]*files = \(([^)]*)\)/)?.[1] ?? "";
    return (bridgeSource.match(/RCT_EXTERN_MODULE\(DaonIOSHost, NSObject\)/g) ?? []).length === 1
      && (bridgeSource.match(/RCT_EXTERN_METHOD\(/g) ?? []).length === 8
      && expectedMethods.every((method) => externMethods.includes(method))
      && JSON.stringify(externMethods.map(selector).sort()) === JSON.stringify(swiftSelectors)
      && (projectSource.match(/DaonIOSHostBridge\.m in Sources \*\/ = \{isa = PBXBuildFile;/g) ?? []).length === 1
      && (projectSource.match(/DaonIOSHostBridge\.m \*\/ = \{isa = PBXFileReference;/g) ?? []).length === 1
      && /DaonIOSHostBridge\.m in Sources/.test(appSources)
      && !/DaonIOSHostBridge\.m in Sources/.test(uiTestSources)
      && !/Pods[^\n]*DaonIOSHostBridge|DaonIOSHostBridge[^\n]*Pods/.test(projectSource);
  };

  assert.equal(isValid(bridge, host, project), true);
  assert.equal(isValid(bridge.replace("consumePendingDeepLink", "consumeDeepLink"), host, project), false);
  assert.equal(isValid(bridge.replace(" resolver:(RCTPromiseResolveBlock)resolve", ""), host, project), false);
  assert.equal(isValid(`${bridge}\nRCT_EXTERN_MODULE(DaonIOSHost, NSObject)`, host, project), false);
  assert.equal(isValid(bridge, host, project.replace(/A1[0-9A-F]+ \/\* DaonIOSHostBridge\.m in Sources \*\//, "")), false);
});

test("Podfile Autolinking은 호출 CWD와 무관하게 Monorepo Mobile App Root를 사용한다", async () => {
  const podfile = await read("apps/mobile/ios/Podfile");
  assert.match(podfile, /app_root = File\.expand_path\('\.\.', __dir__\)/);
  assert.match(podfile, /require\.resolve\(\s*"@react-native-community\/cli",\s*\{paths: \[process\.argv\[1\]\]\},?\s*\)/);
  assert.match(podfile, /process\.chdir\(process\.argv\[1\]\)/);
  assert.match(podfile, /use_native_modules!\(autolinking_command\)/);
  assert.match(podfile, /:path => config\[:reactNativePath\]/);
  assert.match(podfile, /react_native_post_install\(\s*installer,\s*config\[:reactNativePath\]/);
  assert.doesNotMatch(podfile, /[A-Za-z]:[\\/]|\/Users\/|\/home\//);

  const cli = path.join(root, "node_modules/@react-native-community/cli/build/bin.js");
  const config = JSON.parse(execFileSync(process.execPath, [cli, "config"], {
    cwd: path.join(root, "apps/mobile"),
    encoding: "utf8"
  }));
  assert.equal(path.resolve(config.root), path.join(root, "apps/mobile"));
  assert.equal(path.resolve(config.project.ios.sourceDir), iosRoot);
  assert.equal(path.resolve(config.reactNativePath), path.join(root, "node_modules/react-native"));
  assert.deepEqual(config.dependencies, {});
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

test("iOS 알림 설정 진입은 공개 URL과 iOS 15.1 generic fallback을 사용한다", async () => {
  const nativeHost = await read("apps/mobile/ios/Daon/DaonIOSHost.swift");
  const bridge = await read("apps/mobile/ios/Daon/DaonIOSHostBridge.m");
  const host = await read("apps/mobile/src/platform/ios-host.ts");
  assert.match(bridge, /RCT_EXTERN_METHOD\(openNotificationSettings:\(RCTPromiseResolveBlock\)resolve rejecter:\(RCTPromiseRejectBlock\)reject\)/);
  assert.match(nativeHost, /@objc\(openNotificationSettings:rejecter:\)/);
  const methodStart = nativeHost.indexOf("@objc(openNotificationSettings:rejecter:)");
  const methodEnd = nativeHost.indexOf("static func recordLifecycleState", methodStart);
  const method = methodStart >= 0 && methodEnd > methodStart ? nativeHost.slice(methodStart, methodEnd) : "";
  assert.ok(method);
  assert.match(method, /if #available\(iOS 16\.0, \*\)/);
  assert.match(method, /UIApplication\.openNotificationSettingsURLString/);
  assert.match(method, /UIApplication\.openSettingsURLString/);
  assert.match(method, /IOS_NOTIFICATION_SETTINGS_URL_UNAVAILABLE/);
  assert.match(method, /UIApplication\.shared\.open\(url, options: \[:\]\) \{ opened in[\s\S]*?resolve\(opened\)/);
  assert.doesNotMatch(method, /App-Prefs|prefs:|TCC|simctl|defaults\s+(?:write|delete)/i);
  assert.match(host, /openNotificationSettings\(\): Promise<boolean>/);
  assert.match(host, /export async function openIOSNotificationSettings\(\): Promise<void>/);
  assert.match(host, /await nativeHost\?\.openNotificationSettings\(\)/);
  assert.match(host, /openNotificationSettings: openIOSNotificationSettings/);
});

test("iOS 알림 설정 Open은 직전 권한 상태와 Bool 결과만 고정 Marker로 기록한다", async () => {
  const nativeHost = await read("apps/mobile/ios/Daon/DaonIOSHost.swift");
  const methodStart = nativeHost.indexOf("@objc(openNotificationSettings:rejecter:)");
  const methodEnd = nativeHost.indexOf("static func recordLifecycleState", methodStart);
  const method = methodStart >= 0 && methodEnd > methodStart ? nativeHost.slice(methodStart, methodEnd) : "";
  assert.ok(method);
  const settingsRead = method.indexOf("UNUserNotificationCenter.current().getNotificationSettings");
  const normalizedAuth = method.indexOf("Self.notificationPermissionState(settings.authorizationStatus)");
  const urlSelection = method.indexOf("if #available(iOS 16.0, *)");
  const openCall = method.indexOf("UIApplication.shared.open(url, options: [:])");
  const marker = method.indexOf("DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=%@ AUTH=%@");
  const resolve = method.indexOf("resolve(opened)");
  assert.ok(settingsRead >= 0 && normalizedAuth > settingsRead && urlSelection > normalizedAuth && openCall > urlSelection && marker > openCall && resolve > marker);
  assert.match(method, /let openResult = opened \? "OPENED" : "FAILED"/);
  assert.match(method, /NSLog\("DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=%@ AUTH=%@", openResult, authorizationState\)/);
  assert.equal((method.match(/DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=/g) ?? []).length, 1);
  assert.doesNotMatch(method, /registerForRemoteNotifications|APNs|authorizationStatus\.rawValue|App-Prefs|prefs:|TCC/i);
});

test("Mobile Shell은 선택적 알림 설정 버튼과 기존 generic 설정 버튼을 함께 보존한다", async () => {
  const shell = await read("apps/mobile/src/MobileShell.tsx");
  assert.match(shell, /openNotificationSettings\?\(\): Promise<void>/);
  assert.match(shell, /permissionAdapter\.openNotificationSettings \? <Pressable accessibilityLabel="알림 설정 열기"/);
  assert.match(shell, /void permissionAdapter\.openNotificationSettings\?\.\(\)/);
  assert.match(shell, />알림 설정<\/Text>/);
  assert.match(shell, /accessibilityLabel="앱 권한 설정 열기"[\s\S]*?permissionAdapter\.openApplicationSettings\(\)[\s\S]*?>앱 권한 설정<\/Text>/);
  assert.equal((shell.match(/\["camera", "microphone", "notification"\]/g) ?? []).length, 1);
});

test("iOS Restore Adapter는 Native 문자열을 보존하고 null·undefined를 null로 정규화한다", async () => {
  const host = await read("apps/mobile/src/platform/ios-host.ts");
  assert.match(host, /if \(!nativeHost\) return null;[\s\S]*?const restoredRoute: unknown = await nativeHost\.restoreNavigationRoute\(\);[\s\S]*?return typeof restoredRoute === "string" \? restoredRoute : null;/);
  const expression = host.match(/return (typeof restoredRoute === "string" \? restoredRoute : null);/)?.[1];
  assert.ok(expression);
  const normalize = new Function("restoredRoute", `return ${expression};`);
  assert.equal(normalize("Notifications"), "Notifications");
  assert.equal(normalize(null), null);
  assert.equal(normalize(undefined), null);
  assert.equal(normalize(0), null);
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

test("iOS App은 Listener 설치 뒤 Null Restore의 기본 Home만 기존 Route 계약으로 저장한다", async () => {
  const app = await read("apps/mobile/src/App.tsx");
  const iosBranch = app.slice(app.indexOf('if (Platform.OS === "ios")'), app.indexOf("  }, []);"));
  const subscribeDeepLinksIndex = iosBranch.indexOf("subscribeIOSDeepLinks(setRequestedNativeRouteKey)");
  const subscribeLifecycleIndex = iosBranch.indexOf("subscribeIOSLifecycle(() => undefined)");
  const restoreIndex = iosBranch.indexOf("restoreIOSNavigationRoute().then");
  const defaultSaveIndex = iosBranch.indexOf('saveIOSNavigationRoute("Home")');
  assert.ok(subscribeDeepLinksIndex >= 0 && subscribeDeepLinksIndex < restoreIndex);
  assert.ok(subscribeLifecycleIndex >= 0 && subscribeLifecycleIndex < restoreIndex);
  assert.ok(restoreIndex < defaultSaveIndex);
  assert.match(iosBranch, /restoreIOSNavigationRoute\(\)\.then\(\(restoredRoute\) => \{[\s\S]*?setInitialNativeRouteKey\(restoredRoute\);[\s\S]*?if \(restoredRoute === null\) \{[\s\S]*?void saveIOSNavigationRoute\("Home"\);[\s\S]*?\}/);
  assert.equal((app.match(/saveIOSNavigationRoute\("Home"\)/g) ?? []).length, 1);
  assert.doesNotMatch(app, /saveAndroidNavigationRoute\("Home"\)/);
});

test("iOS Simulator Build 계약은 Signing 자산을 요구하거나 저장하지 않는다", async () => {
  const project = await read("apps/mobile/ios/Daon.xcodeproj/project.pbxproj");
  const workflow = await read(".github/workflows/release-1-ios-phase-a.yml");
  const uiTestRunner = await read("apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh");
  const files = await listFiles(iosRoot);
  const relative = files.map((file) => path.relative(iosRoot, file).replaceAll("\\", "/"));
  assert.doesNotMatch(project, /DEVELOPMENT_TEAM|PROVISIONING_PROFILE|CODE_SIGN_IDENTITY/);
  assert.match(`${workflow}\n${uiTestRunner}`, /CODE_SIGNING_ALLOWED=NO/);
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
  const uiTestRunner = await read("apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh");
  const simulatorPreparation = await read("apps/mobile/ios/ci/prepare-simulator.mjs");
  assert.deepEqual(workflow.on.pull_request.branches, ["codex/release-1"]);
  assert.deepEqual(workflow.permissions, { contents: "read" });
  const job = workflow.jobs["ios-phase-a"];
  assert.equal(job["runs-on"], "macos-26");
  assert.ok(job["timeout-minutes"] > 0);
  const serialized = `${JSON.stringify(workflow)}\n${uiTestRunner}`;
  for (const token of ["fetch-depth", "Xcode_26.6.app", "1.16.2", "24.18.0", "11.12.1", "CODE_SIGNING_ALLOWED=NO", "upload-artifact@v6", "if-no-files-found", "always()", "github.sha"]) {
    assert.match(serialized, new RegExp(token.replaceAll(".", "\\.")));
  }
  assert.match(simulatorPreparation, /simctl[\s\S]*create/);
  assert.match(simulatorPreparation, /simctl[\s\S]*boot/);
  const simulatorVerification = await read("apps/mobile/ios/ci/verify-simulator.sh");
  for (const action of ["install", "launch", "terminate"]) assert.match(simulatorVerification, new RegExp(`simctl ${action}`));
  assert.doesNotMatch(simulatorVerification, /simctl openurl/);
  assert.doesNotMatch(serialized, /macos-latest|xcode-select.*latest|continue-on-error/);
});

test("UI Test Runner는 원래 Exit를 보존하고 Cleanup 전에 현대 xcresult·정확 구간 Log·Crash 진단을 수집한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const runner = await read("apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh");
  const steps = workflow.jobs["ios-phase-a"].steps;
  const uiTestsIndex = steps.findIndex((step) => step.id === "ui-tests");
  const cleanupIndex = steps.findIndex((step) => step.name === "Shutdown simulator");
  const uploadIndex = steps.findIndex((step) => step.name === "Upload exact-SHA Phase A evidence");
  assert.ok(uiTestsIndex >= 0 && uiTestsIndex < cleanupIndex && cleanupIndex < uploadIndex);
  assert.equal(steps[cleanupIndex].if, "${{ always() }}");
  assert.equal(steps[uploadIndex].if, "${{ always() }}");
  assert.match(steps[uiTestsIndex].run, /run-ui-tests-with-diagnostics\.sh/);
  assert.match(runner, /TEST_EXIT_CODE=\$\?/);
  assert.match(runner, /exit "\$\{TEST_EXIT_CODE\}"/);
  assert.match(runner, /xcresulttool get test-results summary/);
  assert.match(runner, /xcresulttool get test-results tests/);
  assert.match(runner, /xcresulttool export attachments/);
  assert.doesNotMatch(runner, /xcresulttool get object|--legacy/);
  assert.match(runner, /simctl spawn "\$\{SIMULATOR_UDID\}" log show/);
  assert.match(runner, /--start "\$\{TEST_START_UTC\}" --end "\$\{TEST_END_UTC\}"/);
  assert.match(runner, /process == "Daon"/);
  assert.match(runner, /DiagnosticReports/);
  assert.match(runner, /diagnostic-status\.txt/);
  assert.match(JSON.stringify(steps[uploadIndex]), /artifacts\/ios-phase-a/);
});

test("UI Test 진단 오류 Fixture는 원래 XCTest Exit 65를 성공으로 바꾸지 않는다", async () => {
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-ios-ui-diagnostics-"));
  const evidenceDir = path.join(fixtureRoot, "evidence");
  const resultBundle = path.join(evidenceDir, "DaonUITests.xcresult");
  const runner = path.join(iosRoot, "ci/run-ui-tests-with-diagnostics.sh");
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  await mkdir(resultBundle, { recursive: true });
  try {
    const fixture = spawnSync(bash, ["-c", 'xcodebuild(){ return 65; }; xcrun(){ return 44; }; export -f xcodebuild xcrun; bash "$1"', "fixture", runner], {
      cwd: fixtureRoot,
      env: {
        ...process.env,
        SIMULATOR_UDID: "11111111-2222-3333-4444-555555555555",
        IOS_EVIDENCE_DIR: "evidence",
        IOS_DERIVED_DATA: "DerivedData",
        IOS_RESULT_BUNDLE: "evidence/DaonUITests.xcresult",
        IOS_DIAGNOSTIC_REPORTS_DIR: "DiagnosticReports",
        RUNNER_TEMP: "."
      },
      encoding: "utf8"
    });
    assert.equal(fixture.status, 65, fixture.stderr);
    const status = await readFile(path.join(evidenceDir, "diagnostics/diagnostic-status.txt"), "utf8");
    assert.match(status, /^test_exit_code=65$/m);
    assert.match(status, /^xcresult_summary=failure:44$/m);
    assert.match(status, /^simulator_unified_log=failure:44$/m);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("UI Test는 각 Scenario 전에 접근성 Root와 runningForeground를 Fail-close 확인한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  assert.match(uiTests, /private func launchAndRequireRootReady/);
  assert.match(uiTests, /Daon ios 공용 Shell/);
  assert.match(uiTests, /wait\(for: \.runningForeground/);
  assert.equal((uiTests.match(/launchAndRequireRootReady\(app\)/g) ?? []).length, 6);
  assert.match(uiTests, /continueAfterFailure\s*=\s*false/);
  assert.doesNotMatch(uiTests, /XCTSkip|continueAfterFailure\s*=\s*true/);
});

test("UI Test는 두 Scroll Host의 고정 Identifier Query와 기존 Swipe 의미를 유지한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  assert.equal((uiTests.match(/app\.otherElements\["공용 Navigation"\]/g) ?? []).length, 1);
  assert.equal((uiTests.match(/app\.otherElements\["화면 내용"\]/g) ?? []).length, 2);
  assert.doesNotMatch(uiTests, /app\.scrollViews\["(?:공용 Navigation|화면 내용)"\]/);
  assert.match(uiTests, /let navigation = app\.otherElements\["공용 Navigation"\][\s\S]*?XCTAssertTrue\(navigation\.waitForExistence\(timeout: 10\)[\s\S]*?for _ in 0\.\.<8 where !button\.isHittable \{ navigation\.swipeLeft\(\) \}/);
  assert.equal((uiTests.match(/XCTAssertTrue\(content\.waitForExistence\(timeout: 10\)/g) ?? []).length, 2);
  assert.equal((uiTests.match(/0\.\.<8 where !/g) ?? []).length, 4);
  assert.match(uiTests, /navigation\.swipeLeft\(\)/);
  assert.equal((uiTests.match(/content\.swipeUp\(\)/g) ?? []).length, 3);
  assert.doesNotMatch(uiTests, /coordinate\(|firstMatch|sleep\(|Thread\.sleep/);
});

test("UI Test는 Apple System Open으로 Warm 7종과 비정상 5종을 단일 Session의 foreground·화면에서 검증한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const runner = await read("apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh");
  const warmRoutes = ["WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings"];
  const rejectedLinks = [
    "sinsan-daon://app/UnknownRoute",
    "sinsan-daon://app/%48ome",
    "sinsan-daon://app/Home%2Fextra",
    "sinsan-daon://app/Home?route=Inbox",
    "sinsan-daon://app/Home#Inbox"
  ];
  const testStart = uiTests.indexOf("func testSystemOpenDeepLinksPreserveForegroundAndRoute() {");
  const testEnd = uiTests.indexOf("func testPermissionControlsAndSettingsBoundary() {", testStart);
  const testBody = testStart >= 0 && testEnd > testStart ? uiTests.slice(testStart, testEnd) : "";
  assert.ok(testBody);
  assert.match(testBody, /guard #available\(iOS 26\.0, \*\) else[\s\S]*?XCTFail/);
  assert.equal((testBody.match(/launchAndRequireRootReady\(app\)/g) ?? []).length, 1);
  assert.equal((testBody.match(/XCUIDevice\.shared\.system\.open\(url\)/g) ?? []).length, 2);
  assert.equal((testBody.match(/requireRootReady\(app\)/g) ?? []).length, 2);
  assert.equal((testBody.match(/"sinsan-daon:\/\/app\//g) ?? []).length, 12);
  assert.match(testBody, /app\.staticTexts\[route\]\.waitForExistence/);
  assert.match(testBody, /app\.staticTexts\["AccountSettings"\]\.waitForExistence/);
  assert.doesNotMatch(testBody, /app\.launch\(\)[\s\S]*app\.launch\(\)|XCTSkip|launchArguments|launchEnvironment/);
  assert.match(testBody, /URL\(string: rawURL\)/);
  for (const route of warmRoutes) {
    assert.equal((testBody.match(new RegExp(`"${route}"`, "g")) ?? []).length >= 1, true, `missing warm route ${route}`);
    assert.ok(testBody.includes(`"sinsan-daon://app/${route}"`), `missing exact warm URL ${route}`);
  }
  for (const link of rejectedLinks) assert.match(testBody, new RegExp(link.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(runner, /only-testing:DaonUITests\/DaonUITests\/testSystemOpenDeepLinksPreserveForegroundAndRoute/);
});

test("CI Script는 8 Route·비정상 Deep Link·Lifecycle·권한·Crash/Secret·종료를 Fail-close 검증한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const combined = `${script}\n${uiTests}`;
  for (const route of ["Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings"]) {
    assert.match(combined, new RegExp(route));
  }
  for (const token of ["UnknownRoute", "%48ome", "Home%2Fextra", "Home?route=Inbox", "Home#Inbox", "terminate", "launch", "Crash", "secret", "SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE"]) {
    assert.match(combined, new RegExp(token.replaceAll("?", "\\?").replaceAll(".", "\\."), "i"));
  }
  for (const permission of ["camera", "microphone"]) {
    assert.match(script, new RegExp(`privacy[^\\n]*${permission}`, "i"));
  }
  assert.doesNotMatch(script, /simctl privacy[^\n]*notifications/i);
  assert.match(script, /run_permission_phase grant-initial grant/);
  assert.match(script, /run_permission_phase revoke revoke/);
  assert.match(script, /run_permission_phase grant-again grant/);
});

test("Simulator 검증은 Route Key만 초기화하고 Home 준비·복원을 검증하며 URL 발송을 XCUITest에 위임한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  assert.match(script, /clear_navigation_route\(\)[\s\S]*?get_app_container[\s\S]*?plutil -remove native_route_key "\$\{container\}\/Library\/Preferences\/\$\{BUNDLE_ID\}\.plist"/);
  assert.doesNotMatch(script, /plutil -remove (?!native_route_key)|defaults delete|erase|uninstall|rm\s/);
  const installIndex = script.indexOf('simctl install "${SIMULATOR_UDID}" "${APP_PATH}"');
  const clearIndex = script.indexOf("clear_navigation_route", installIndex);
  const launchIndex = script.indexOf('simctl launch "${SIMULATOR_UDID}" "${BUNDLE_ID}"', clearIndex);
  const homeReadyIndex = script.indexOf("wait_for_route_with_evidence Home", launchIndex);
  assert.ok(installIndex >= 0 && installIndex < clearIndex && clearIndex < launchIndex && launchIndex < homeReadyIndex);
  assert.doesNotMatch(script, /simctl openurl|warm_routes=|rejected_links=/);
  assert.equal((script.match(/wait_for_route_with_evidence Home/g) ?? []).length, 2);
  assert.equal((script.match(/for _ in \{1\.\.20\}/g) ?? []).length, 1);
});

test("Simulator 검증은 설치 뒤 exact Daon Process를 종료하고 새 Process에서 Home 준비를 확인한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const exactTerminate = 'xcrun simctl terminate "${SIMULATOR_UDID}" "${BUNDLE_ID}"';
  const allowedInitialTerminate = `${exactTerminate} >/dev/null 2>&1 || true`;
  const installIndex = script.indexOf('xcrun simctl install "${SIMULATOR_UDID}" "${APP_PATH}"');
  const initialTerminateIndex = script.indexOf(allowedInitialTerminate, installIndex);
  const clearIndex = script.indexOf("clear_navigation_route", installIndex);
  const launchIndex = script.indexOf('xcrun simctl launch "${SIMULATOR_UDID}" "${BUNDLE_ID}"', clearIndex);
  const homeReadyIndex = script.indexOf("wait_for_route_with_evidence Home", launchIndex);
  const simulatorLogIndex = script.indexOf('log show --last 10m', homeReadyIndex);
  const finalTerminateIndex = script.indexOf(exactTerminate, simulatorLogIndex);
  assert.ok(installIndex >= 0 && installIndex < initialTerminateIndex && initialTerminateIndex < clearIndex && clearIndex < launchIndex && launchIndex < homeReadyIndex);
  assert.ok(simulatorLogIndex >= 0 && simulatorLogIndex < finalTerminateIndex);
  assert.doesNotMatch(script, /simctl (?:shutdown|erase|uninstall)|\b(?:kill|killall|pkill)\b/);
});

test("모든 Route Wait 실패는 승인 Route 기반 Daon Log를 보존하고 허용 Marker만 출력하며 원 Exit를 유지한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_approved_route() {");
  const helperEnd = script.indexOf("is_allowed_permission_failure_code() {", helperStart);
  const contract = helperStart >= 0 && helperEnd > helperStart ? script.slice(helperStart, helperEnd) : "";
  assert.ok(contract);
  assert.match(contract, /Home\|WorkspaceList\|WorkspaceDetail\|Inbox\|RunHistory\|Notifications\|ModelConnections\|AccountSettings/);
  assert.match(contract, /route-wait-failure-\$\{expected\}\.log/);
  assert.match(contract, /--predicate 'process == "Daon"'/);
  assert.match(contract, /DAON_ROUTE_WAIT_EXPECTED=%s/);
  assert.match(contract, /DAON_ROUTE_WAIT_ACTUAL=%s/);
  assert.match(contract, /DAON_PENDING_DEEP_LINK_RECEIVED\|DAON_ROUTE_SAVED=/);
  assert.match(contract, /DAON_LIFECYCLE_STATE=/);
  assert.doesNotMatch(contract, /cat "\$\{evidence_file\}"|grep -Ev|echo[^\n]*\$\{actual\}/);
  assert.equal((script.match(/wait_for_route_with_evidence Home/g) ?? []).length, 2);
  assert.equal((script.match(/wait_for_route_with_evidence "\$\{route\}"/g) ?? []).length, 0);
  assert.equal((script.match(/wait_for_route_with_evidence AccountSettings/g) ?? []).length, 0);
  assert.equal((script.match(/^[ \t]*wait_for_route(?: |$)/gm) ?? []).length, 1);

  const failureLogIndex = script.indexOf('route-wait-failure-${expected}.log');
  const firstHomeIndex = script.indexOf("wait_for_route_with_evidence Home");
  assert.ok(failureLogIndex >= 0 && failureLogIndex < firstHomeIndex);
  assert.equal((script.match(/--predicate 'process == "Daon"'/g) ?? []).length, 2);

  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-ios-route-failure-"));
  const evidenceDir = path.join(fixtureRoot, "evidence");
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  await mkdir(evidenceDir, { recursive: true });
  try {
    for (const expected of ["Home", "WorkspaceList"]) {
      const fixture = spawnSync(bash, ["-c", `set -euo pipefail\nread_preference(){ printf '%s' Home; }\nwait_for_route(){ return 23; }\nxcrun(){ printf '%s\\n' '2026 marker DAON_PENDING_DEEP_LINK_RECEIVED' 'DAON_ROUTE_SAVED=Home' 'DAON_LIFECYCLE_STATE=active' 'PRIVATE_DIAGNOSTIC'; }\n${contract}\nwait_for_route_with_evidence "${expected}"`], {
        cwd: fixtureRoot,
        env: { ...process.env, EVIDENCE_DIR: "evidence", SIMULATOR_UDID: "11111111-2222-3333-4444-555555555555" },
        encoding: "utf8"
      });
      assert.equal(fixture.status, 23, fixture.stderr);
      assert.match(fixture.stderr, new RegExp(`DAON_ROUTE_WAIT_EXPECTED=${expected}`));
      assert.match(fixture.stderr, /DAON_ROUTE_WAIT_ACTUAL=Home/);
      assert.match(fixture.stderr, /DAON_PENDING_DEEP_LINK_RECEIVED/);
      assert.match(fixture.stderr, /DAON_ROUTE_SAVED=Home/);
      assert.match(fixture.stderr, /DAON_LIFECYCLE_STATE=active/);
      assert.doesNotMatch(fixture.stderr, /PRIVATE_DIAGNOSTIC|2026 marker/);
      const saved = await readFile(path.join(evidenceDir, `route-wait-failure-${expected}.log`), "utf8");
      assert.match(saved, /2026 marker DAON_PENDING_DEEP_LINK_RECEIVED/);
      assert.match(saved, /PRIVATE_DIAGNOSTIC/);
    }

    const success = spawnSync(bash, ["-c", `set -euo pipefail\nwait_for_route(){ return 0; }\nxcrun(){ return 91; }\n${contract}\nwait_for_route_with_evidence Inbox\nprintf '%s\\n' continued`], {
      cwd: fixtureRoot,
      env: { ...process.env, EVIDENCE_DIR: "evidence", SIMULATOR_UDID: "11111111-2222-3333-4444-555555555555" },
      encoding: "utf8"
    });
    assert.equal(success.status, 0, success.stderr);
    assert.match(success.stdout, /continued/);
    await assert.rejects(readFile(path.join(evidenceDir, "route-wait-failure-Inbox.log"), "utf8"), { code: "ENOENT" });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Simulator ERR 진단은 allowlist 현재 단계와 숫자 원 Exit만 출력하고 성공에는 표식을 만들지 않는다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const allowedStages = [
    "APP_ARTIFACT", "BOOT_STATUS", "INSTALL", "INITIAL_TERMINATE", "ROUTE_CLEAR", "LAUNCH", "HOME_READY",
    "PERMISSION_GRANT_INITIAL", "PERMISSION_REVOKE", "PERMISSION_GRANT_AGAIN",
    "LIFECYCLE_APPEARANCE", "LIFECYCLE_TERMINATE", "LIFECYCLE_RELAUNCH", "LIFECYCLE_READY", "LIFECYCLE_STATE",
    "FINAL_LOG_CAPTURE", "FINAL_LOG_SCAN", "FINAL_BINARY_SCAN", "FINAL_TERMINATE", "FINAL_PROCESS_CHECK", "STATUS_WRITE"
  ];
  const contractStart = script.indexOf('DAON_SIM_STAGE="INITIALIZE"');
  const trapLine = "trap report_simulator_failure ERR";
  const contractEnd = script.indexOf(trapLine, contractStart);
  const contract = contractStart >= 0 && contractEnd > contractStart ? script.slice(contractStart, contractEnd + trapLine.length) : "";
  assert.ok(contract);
  assert.match(contract, /DAON_SIM_FAILED_STAGE=%s\\n/);
  assert.match(contract, /DAON_SIM_FAILED_EXIT=%d\\n/);
  assert.match(contract, /UNCLASSIFIED/);
  assert.doesNotMatch(contract, /SIMULATOR_UDID|EVIDENCE_DIR|APP_PATH|BUNDLE_ID|rawURL|secret|https?:\/\//i);
  for (const stage of allowedStages) assert.match(script, new RegExp(`DAON_SIM_STAGE="${stage}"`));

  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const failure = spawnSync(bash, ["-c", `set -euo pipefail\n${contract}\nDAON_SIM_STAGE="INSTALL"\nfail_stage(){ return 23; }\nfail_stage\nprintf '%s\\n' unexpected`], { encoding: "utf8" });
  assert.equal(failure.status, 23, failure.stderr);
  assert.equal(failure.stderr, "DAON_SIM_FAILED_STAGE=INSTALL\nDAON_SIM_FAILED_EXIT=23\n");
  assert.doesNotMatch(failure.stdout, /unexpected/);

  const success = spawnSync(bash, ["-c", `set -euo pipefail\n${contract}\nDAON_SIM_STAGE="INSTALL"\ntrue\nprintf '%s\\n' continued`], { encoding: "utf8" });
  assert.equal(success.status, 0, success.stderr);
  assert.equal(success.stderr, "");
  assert.match(success.stdout, /continued/);
});

test("Permission Phase ERR 진단은 함수 내부 실패 서비스만 allowlist 표식으로 추가하고 원 Exit를 보존한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  assert.match(script, /^#!\/usr\/bin\/env bash\r?\nset -Eeuo pipefail\r?\n/);

  const contractStart = script.indexOf('DAON_SIM_STAGE="INITIALIZE"');
  const trapLine = "trap report_simulator_failure ERR";
  const contractEnd = script.indexOf(trapLine, contractStart);
  const contract = contractStart >= 0 && contractEnd > contractStart ? script.slice(contractStart, contractEnd + trapLine.length) : "";
  assert.ok(contract);
  assert.match(contract, /DAON_SIM_PERMISSION_SERVICE=""/);
  assert.match(contract, /camera\|microphone\|notifications\) return 0/);
  assert.match(contract, /DAON_SIM_FAILED_PERMISSION_SERVICE=%s\\n/);
  assert.doesNotMatch(contract, /permission_service=.*\$1|eval|printf[^\n]*DAON_SIM_PERMISSION_SERVICE[^\n]*%q/);

  const permissionStart = script.indexOf("run_permission_phase() {");
  const permissionEnd = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStart);
  const permissionContract = permissionStart >= 0 && permissionEnd > permissionStart ? script.slice(permissionStart, permissionEnd + 2) : "";
  assert.ok(permissionContract);
  const orderedServices = [...permissionContract.matchAll(/DAON_SIM_PERMISSION_SERVICE="(camera|microphone|notifications)"/g)].map((match) => match[1]);
  assert.deepEqual(orderedServices, ["camera", "microphone"]);
  for (const service of orderedServices) {
    assert.match(permissionContract, new RegExp(`DAON_SIM_PERMISSION_SERVICE="${service}"\\r?\\n\\s*xcrun simctl privacy "\\$\\{SIMULATOR_UDID\\}" "\\$\\{privacy_action\\}" ${service} "\\$\\{BUNDLE_ID\\}"`));
  }
  assert.match(permissionContract, /case "\$\{phase\}" in[\s\S]*permission_test_method="testPermissionGrantInitial"[\s\S]*permission_test_method="testPermissionRevoke"[\s\S]*permission_test_method="testPermissionGrantAgain"[\s\S]*esac/);
  assert.doesNotMatch(permissionContract, /DAON_PERMISSION_(?:PHASE|EXPECTED)=/);
  assert.doesNotMatch(permissionContract, /simctl privacy[^\n]*notifications/i);
  assert.doesNotMatch(permissionContract, /simctl privacy[^\n]*(?:\|\| true|retry)|for\s+[^\n]*camera|sleep/);

  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const permissionFailure = spawnSync(bash, ["-c", `set -Eeuo pipefail\n${contract}\nDAON_SIM_STAGE="PERMISSION_GRANT_INITIAL"\npermission_failure(){\n  DAON_SIM_PERMISSION_SERVICE="microphone"\n  return 23\n}\npermission_failure\nprintf '%s\\n' unexpected`], { encoding: "utf8" });
  assert.equal(permissionFailure.status, 23, permissionFailure.stderr);
  assert.equal(permissionFailure.stderr, "DAON_SIM_FAILED_STAGE=PERMISSION_GRANT_INITIAL\nDAON_SIM_FAILED_PERMISSION_SERVICE=microphone\nDAON_SIM_FAILED_EXIT=23\n");
  assert.doesNotMatch(permissionFailure.stdout, /unexpected/);

  const outsideFailure = spawnSync(bash, ["-c", `set -Eeuo pipefail\n${contract}\nDAON_SIM_STAGE="INSTALL"\nDAON_SIM_PERMISSION_SERVICE=""\nreturn_failure(){ return 23; }\nreturn_failure`], { encoding: "utf8" });
  assert.equal(outsideFailure.status, 23, outsideFailure.stderr);
  assert.equal(outsideFailure.stderr, "DAON_SIM_FAILED_STAGE=INSTALL\nDAON_SIM_FAILED_EXIT=23\n");
  assert.doesNotMatch(outsideFailure.stderr, /DAON_SIM_FAILED_PERMISSION_SERVICE/);

  const unlistedFailure = spawnSync(bash, ["-c", `set -Eeuo pipefail\n${contract}\nDAON_SIM_STAGE="PERMISSION_REVOKE"\nDAON_SIM_PERMISSION_SERVICE="PRIVATE_DIAGNOSTIC"\nreturn_failure(){ return 23; }\nreturn_failure`], { encoding: "utf8" });
  assert.equal(unlistedFailure.status, 23, unlistedFailure.stderr);
  assert.equal(unlistedFailure.stderr, "DAON_SIM_FAILED_STAGE=PERMISSION_REVOKE\nDAON_SIM_FAILED_EXIT=23\n");
  assert.doesNotMatch(unlistedFailure.stderr, /PRIVATE_DIAGNOSTIC|DAON_SIM_FAILED_PERMISSION_SERVICE/);
});

test("Permission XCTest 실패는 Raw Log를 보존하고 allowlist Code·Phase·원 Exit만 단일 Annotation한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_allowed_permission_failure_code() {");
  const permissionStart = script.indexOf("run_permission_phase() {", helperStart);
  const permissionEnd = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStart);
  const contract = helperStart >= 0 && permissionStart > helperStart && permissionEnd > permissionStart
    ? script.slice(helperStart, permissionEnd + 2)
    : "";
  assert.ok(contract);
  assert.match(contract, /permission-\$\{phase\}\.log/);
  assert.match(contract, /2>&1\s*\|\s*tee/);
  assert.match(contract, /PIPESTATUS\[@\]/);
  assert.match(contract, /::error::CODE=%s PHASE=%s EXIT=%d\\n/);
  assert.match(contract, /DAON_SETTINGS_ACCESSIBILITY_SUMMARY=/);
  assert.match(contract, /::notice::%s\\n/);
  assert.match(contract, /UNKNOWN_XCTEST_FAILURE/);
  assert.match(contract, /ALERT_TITLE_MISSING/);
  assert.match(contract, /ALERT_COUNT_MISMATCH/);
  assert.match(contract, /ALERT_ALLOW_MISSING/);
  assert.match(contract, /ALERT_DISMISSAL_FAILED/);
  assert.match(contract, /SETTINGS_FOREGROUND_FAILED/);
  assert.match(contract, /SETTINGS_NOTIFICATION_ROW_MISSING/);
  assert.match(contract, /SETTINGS_SWITCH_MISSING/);
  assert.match(contract, /SETTINGS_SWITCH_VALUE_FAILED/);
  assert.match(contract, /APP_RETURN_ROOT_FAILED/);
  assert.match(contract, /PRODUCTION_RESULT_MISSING/);
  assert.doesNotMatch(contract, /printf[^\n]*(?:raw|log_file|SIMULATOR_UDID|REPOSITORY_ROOT|https?:\/\/)/i);

  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-permission-annotation-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = `set -Eeuo pipefail\nSIMULATOR_UDID=11111111-2222-3333-4444-555555555555\nBUNDLE_ID=com.sinsan.daon\nREPOSITORY_ROOT=/repo\nDERIVED_DATA=/derived\nDAON_SIM_PERMISSION_SERVICE=""\nxcrun(){ return 0; }\nxcodebuild(){ printf '%s\\n' "\${XCODE_RAW}"; return "\${XCODE_EXIT}"; }\n${contract}\nrun_permission_phase "\${FIXTURE_PHASE}" grant GRANTED`;
  const fixturePath = path.join(fixtureRoot, "permission-annotation-fixture.sh");
  await writeFile(fixturePath, fixture, "utf8");
  const run = (phase, exit, raw) => spawnSync(bash, [fixturePath], {
    cwd: fixtureRoot,
    env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), FIXTURE_PHASE: phase, XCODE_EXIT: String(exit), XCODE_RAW: raw },
    encoding: "utf8"
  });
  const annotations = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::error::"));
  const notices = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::notice::"));

  try {
    const knownRaw = "XCTAssertEqual failed - expected one Notification system alert /Users/private/result.xcresult";
    const known = run("grant-initial", 65, knownRaw);
    assert.equal(known.status, 65, known.stderr);
    assert.deepEqual(annotations(known.stdout), ["::error::CODE=ALERT_COUNT_MISMATCH PHASE=grant-initial EXIT=65"]);
    assert.deepEqual(notices(known.stdout), []);
    assert.match(known.stdout, /expected one Notification system alert/);
    assert.equal(await readFile(path.join(fixtureRoot, "permission-grant-initial.log"), "utf8"), `${knownRaw}\n`);

    const unknownRaw = "PRIVATE_USER=/Users/private UDID=SECRET https://internal.invalid/raw failure";
    const unknown = run("revoke", 65, unknownRaw);
    assert.equal(unknown.status, 65, unknown.stderr);
    assert.deepEqual(annotations(unknown.stdout), ["::error::CODE=UNKNOWN_XCTEST_FAILURE PHASE=revoke EXIT=65"]);
    assert.deepEqual(notices(unknown.stdout), []);
    assert.doesNotMatch(annotations(unknown.stdout)[0], /PRIVATE_USER|Users|UDID|SECRET|https|internal|raw failure/);
    assert.equal(await readFile(path.join(fixtureRoot, "permission-revoke.log"), "utf8"), `${unknownRaw}\n`);

    const successRaw = "Test Suite 'DaonUITests' passed";
    const success = run("grant-again", 0, successRaw);
    assert.equal(success.status, 0, success.stderr);
    assert.deepEqual(annotations(success.stdout), []);
    assert.deepEqual(notices(success.stdout), []);
    assert.match(success.stdout, /DaonUITests.*passed/);
    assert.equal(await readFile(path.join(fixtureRoot, "permission-grant-again.log"), "utf8"), `${successRaw}\n`);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Permission XCTest 최종 Settings 진단은 검증된 마지막 한 줄만 Notice로 공개한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_allowed_permission_failure_code() {");
  const permissionStart = script.indexOf("run_permission_phase() {", helperStart);
  const permissionEnd = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStart);
  const contract = helperStart >= 0 && permissionStart > helperStart && permissionEnd > permissionStart
    ? script.slice(helperStart, permissionEnd + 2)
    : "";
  assert.ok(contract);
  assert.match(contract, /report_settings_accessibility_notice\(\)/);
  assert.match(contract, /tail -n 1/);
  assert.match(contract, /4096/);
  assert.match(contract, /16/);
  assert.match(contract, /80/);
  assert.match(contract, /elementType=\(cell\|button\|staticText\|switch\)/);
  assert.doesNotMatch(contract, /eval|debugDescription|printenv|env\s|::notice::\$\{?[^\n]*raw/i);

  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-settings-notice-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = `set -Eeuo pipefail\nSIMULATOR_UDID=11111111-2222-3333-4444-555555555555\nBUNDLE_ID=com.sinsan.daon\nREPOSITORY_ROOT=/repo\nDERIVED_DATA=/derived\nDAON_SIM_PERMISSION_SERVICE=""\nxcrun(){ return 0; }\nxcodebuild(){ printf '%s\\n' "\${XCODE_RAW}"; return "\${XCODE_EXIT}"; }\n${contract}\nrun_permission_phase revoke revoke DENIED`;
  const fixturePath = path.join(fixtureRoot, "settings-notice-fixture.sh");
  await writeFile(fixturePath, fixture, "utf8");
  const run = (exit, raw) => spawnSync(bash, [fixturePath], {
    cwd: fixtureRoot,
    env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), XCODE_EXIT: String(exit), XCODE_RAW: raw },
    encoding: "utf8"
  });
  const notices = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::notice::"));
  const errors = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::error::"));
  const validOne = "DAON_SETTINGS_ACCESSIBILITY_SUMMARY=v1|count=1|items=elementType=cell,label=Notifications,identifier=_empty_,isHittable=1";
  const validLast = "DAON_SETTINGS_ACCESSIBILITY_SUMMARY=v1|count=1|items=elementType=switch,label=Allow_Notifications,identifier=AllowNotifications,isHittable=1";

  try {
    const absent = run(65, "missing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]");
    assert.equal(absent.status, 65, absent.stderr);
    assert.deepEqual(notices(absent.stdout), []);
    assert.deepEqual(errors(absent.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO PHASE=revoke EXIT=65"]);

    const multiple = run(65, `${validOne}\n${validLast}\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]`);
    assert.equal(multiple.status, 65, multiple.stderr);
    assert.deepEqual(notices(multiple.stdout), [`::notice::${validLast}`]);
    assert.deepEqual(errors(multiple.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO PHASE=revoke EXIT=65"]);

    const injected = run(65, `${validOne}::error::SECRET\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]`);
    assert.equal(injected.status, 65, injected.stderr);
    assert.deepEqual(notices(injected.stdout), []);

    const oversizedField = run(65, `DAON_SETTINGS_ACCESSIBILITY_SUMMARY=v1|count=1|items=elementType=cell,label=${"A".repeat(81)},identifier=_empty_,isHittable=1\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]`);
    assert.equal(oversizedField.status, 65, oversizedField.stderr);
    assert.deepEqual(notices(oversizedField.stdout), []);

    const tooMany = run(65, "DAON_SETTINGS_ACCESSIBILITY_SUMMARY=v1|count=17|items=_none_\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]");
    assert.equal(tooMany.status, 65, tooMany.stderr);
    assert.deepEqual(notices(tooMany.stdout), []);

    const mismatchedCount = run(65, `${validOne.replace("count=1", "count=2")}\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]`);
    assert.equal(mismatchedCount.status, 65, mismatchedCount.stderr);
    assert.deepEqual(notices(mismatchedCount.stdout), []);

    const success = run(0, validOne);
    assert.equal(success.status, 0, success.stderr);
    assert.deepEqual(notices(success.stdout), []);
    assert.deepEqual(errors(success.stdout), []);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Permission 실패는 마지막 알림 설정 Open Marker만 안전 검증해 Notice로 공개한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_allowed_permission_failure_code() {");
  const permissionStart = script.indexOf("run_permission_phase() {", helperStart);
  const permissionEnd = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStart);
  const contract = helperStart >= 0 && permissionStart > helperStart && permissionEnd > permissionStart
    ? script.slice(helperStart, permissionEnd + 2)
    : "";
  assert.ok(contract);
  assert.match(contract, /report_notification_settings_open_notice\(\)/);
  assert.match(contract, /log show --last 5m --style compact/);
  assert.match(contract, /process == "Daon"/);
  assert.match(contract, /DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=/);
  assert.match(contract, /\(OPENED\|FAILED\).*\(GRANTED\|DENIED\|NOT_REQUESTED\|RESTRICTED\)/);
  assert.match(contract, /tail -n 1/);
  assert.match(contract, /mktemp/);
  assert.match(contract, /unlink/);
  assert.doesNotMatch(contract, /::notice::[^\n]*(?:log_file|SIMULATOR_UDID|REPOSITORY_ROOT|DERIVED_DATA)|printenv|eval/i);

  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-open-result-notice-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = `set -Eeuo pipefail\nSIMULATOR_UDID=11111111-2222-3333-4444-555555555555\nBUNDLE_ID=com.sinsan.daon\nREPOSITORY_ROOT=/repo\nDERIVED_DATA=/derived\nDAON_SIM_PERMISSION_SERVICE=""\nxcrun(){\n  if [[ "\$*" == *"log show"* ]]; then\n    printf '%s\\n' "\${UNIFIED_LOG}"\n    return "\${LOG_EXIT}"\n  fi\n  return 0\n}\nxcodebuild(){ printf '%s\\n' "\${XCODE_RAW}"; return "\${XCODE_EXIT}"; }\n${contract}\nrun_permission_phase revoke revoke DENIED`;
  const fixturePath = path.join(fixtureRoot, "open-result-notice-fixture.sh");
  await writeFile(fixturePath, fixture, "utf8");
  const run = ({ xcodeExit = 65, raw = "missing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]", unified = "", logExit = 0 } = {}) => spawnSync(bash, [fixturePath], {
    cwd: fixtureRoot,
    env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), XCODE_EXIT: String(xcodeExit), XCODE_RAW: raw, UNIFIED_LOG: unified, LOG_EXIT: String(logExit) },
    encoding: "utf8"
  });
  const notices = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::notice::DAON_NOTIFICATION_SETTINGS_OPEN_RESULT="));
  const errors = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::error::"));

  try {
    for (const auth of ["GRANTED", "DENIED", "NOT_REQUESTED", "RESTRICTED"]) {
      const marker = `DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=${auth}`;
      const result = run({ unified: `timestamp Daon[1] ${marker}` });
      assert.equal(result.status, 65, result.stderr);
      assert.deepEqual(notices(result.stdout), [`::notice::${marker}`]);
      assert.deepEqual(errors(result.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO PHASE=revoke EXIT=65"]);
    }

    const failedMarker = "DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=FAILED AUTH=DENIED";
    const failed = run({ unified: `prefix ${failedMarker}` });
    assert.equal(failed.status, 65, failed.stderr);
    assert.deepEqual(notices(failed.stdout), [`::notice::${failedMarker}`]);

    const absent = run();
    assert.equal(absent.status, 65, absent.stderr);
    assert.deepEqual(notices(absent.stdout), []);

    const validFirst = "DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=DENIED";
    const validLast = "DAON_NOTIFICATION_SETTINGS_OPEN_RESULT=FAILED AUTH=RESTRICTED";
    const multiple = run({ unified: `${validFirst}\n${validLast}` });
    assert.equal(multiple.status, 65, multiple.stderr);
    assert.deepEqual(notices(multiple.stdout), [`::notice::${validLast}`]);

    for (const invalidLast of [
      `${validFirst}\nDAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=PRIVATE`,
      `${validFirst}\nDAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=DENIED::error::SECRET`,
      `${validFirst}\nDAON_NOTIFICATION_SETTINGS_OPEN_RESULT=OPENED AUTH=DENIED %0A PRIVATE`
    ]) {
      const invalid = run({ unified: invalidLast });
      assert.equal(invalid.status, 65, invalid.stderr);
      assert.deepEqual(notices(invalid.stdout), []);
      assert.doesNotMatch(invalid.stdout, /SECRET|PRIVATE/);
    }

    const collectionFailure = run({ unified: validFirst, logExit: 23 });
    assert.equal(collectionFailure.status, 65, collectionFailure.stderr);
    assert.deepEqual(notices(collectionFailure.stdout), []);
    assert.deepEqual(errors(collectionFailure.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO PHASE=revoke EXIT=65"]);

    const success = run({ xcodeExit: 0, unified: validFirst });
    assert.equal(success.status, 0, success.stderr);
    assert.deepEqual(notices(success.stdout), []);
    assert.deepEqual(errors(success.stdout), []);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Permission XCTest 실패는 Assertion Code 우선·마지막 허용 Stage 차선·Unknown 최종으로 분류한다", async () => {
  const swift = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const permissionStart = swift.indexOf("func testPermissionGrantInitial() throws {");
  const permissionEnd = swift.indexOf("\n  private func notificationSwitchIsEnabled", permissionStart);
  const permissionContract = permissionStart >= 0 && permissionEnd > permissionStart
    ? swift.slice(permissionStart, permissionEnd)
    : "";
  assert.ok(permissionContract);
  const stages = [
    ["PHASE_EXPECTED_BINDING", "phaseExpectedBinding"], ["PHASE_EXPECTED_MATCHED", "phaseExpectedMatched"],
    ["APP_LAUNCH_ROOT", "appLaunchRoot"],
    ["CAMERA_REQUEST", "cameraRequest"], ["CAMERA_RESULT", "cameraResult"],
    ["MICROPHONE_REQUEST", "microphoneRequest"], ["MICROPHONE_RESULT", "microphoneResult"],
    ["NOTIFICATION_REQUEST", "notificationRequest"], ["ALERT_TITLE", "alertTitle"],
    ["ALERT_COUNT", "alertCount"], ["ALERT_ALLOW", "alertAllow"],
    ["ALERT_DISMISSAL", "alertDismissal"], ["SETTINGS_FOREGROUND", "settingsForeground"],
    ["SETTINGS_NOTIFICATION_ROW", "settingsNotificationRow"],
    ["SETTINGS_NOTIFICATION_QUERY_CREATED", "settingsNotificationQueryCreated"],
    ["SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED", "settingsNotificationQueryWaitCompleted"],
    ["SETTINGS_NOTIFICATION_SCROLL_SEARCH", "settingsNotificationScrollSearch"],
    ["SETTINGS_NOTIFICATION_COUNT_SINGLE", "settingsNotificationCountSingle"],
    ["SETTINGS_NOTIFICATION_ELEMENT_READY", "settingsNotificationElementReady"],
    ["SETTINGS_NOTIFICATION_ROW_TAP_PENDING", "settingsNotificationRowTapPending"],
    ["SETTINGS_SWITCH_READ", "settingsSwitchRead"],
    ["SETTINGS_SWITCH_TOGGLE", "settingsSwitchToggle"], ["SETTINGS_SWITCH_VERIFY", "settingsSwitchVerify"],
    ["APP_RETURN_ROOT", "appReturnRoot"], ["NOTIFICATION_RESULT", "notificationResult"]
  ];
  for (const [, swiftCase] of stages) assert.match(permissionContract, new RegExp(`permissionXCTestStage\\(\\.${swiftCase}\\)`));
  assert.match(permissionContract, /permissionXCTestStage\(\.phaseExpectedBinding\)[\s\S]*?XCTAssertEqual\(expected, phaseExpected[\s\S]*?guard expected == phaseExpected else \{ return \}\s*permissionXCTestStage\(\.phaseExpectedMatched\)\s*let app/);
  const markerOutputLines = permissionContract.split(/\r?\n/).filter((line) => line.includes("DAON_PERMISSION_XCTEST_STAGE="));
  assert.deepEqual(markerOutputLines.map((line) => line.trim()), ['print("DAON_PERMISSION_XCTEST_STAGE=\\(stage.rawValue)")']);

  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_allowed_permission_failure_code() {");
  const permissionStartShell = script.indexOf("run_permission_phase() {", helperStart);
  const permissionEndShell = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStartShell);
  const contract = helperStart >= 0 && permissionStartShell > helperStart && permissionEndShell > permissionStartShell
    ? script.slice(helperStart, permissionEndShell + 2)
    : "";
  assert.ok(contract);
  for (const [stage] of stages) assert.match(contract, new RegExp(`STAGE_${stage}`));
  for (const code of ["SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO", "SETTINGS_NOTIFICATION_COMPOSITE_ROW_AMBIGUOUS", "SETTINGS_NOTIFICATION_LABEL_ZERO", "SETTINGS_NOTIFICATION_LABEL_NONHITTABLE", "SETTINGS_NOTIFICATION_ROW_ZERO", "SETTINGS_NOTIFICATION_ROW_AMBIGUOUS", "SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO", "SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS"]) assert.match(contract, new RegExp(code));
  const parserStart = contract.indexOf("permission_failure_code_from_log() {");
  const parser = parserStart >= 0 ? contract.slice(parserStart) : "";
  const compositeZeroParser = parser.indexOf('code="SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO"');
  const compositeAmbiguousParser = parser.indexOf('code="SETTINGS_NOTIFICATION_COMPOSITE_ROW_AMBIGUOUS"');
  const labelZeroParser = parser.indexOf('code="SETTINGS_NOTIFICATION_LABEL_ZERO"');
  const labelNonhittableParser = parser.indexOf('code="SETTINGS_NOTIFICATION_LABEL_NONHITTABLE"');
  const zeroParser = parser.indexOf('code="SETTINGS_NOTIFICATION_ROW_ZERO"');
  const ambiguousParser = parser.indexOf('code="SETTINGS_NOTIFICATION_ROW_AMBIGUOUS"');
  const semanticZeroParser = parser.indexOf('code="SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO"');
  const semanticAmbiguousParser = parser.indexOf('code="SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS"');
  const genericRowParser = parser.indexOf('code="SETTINGS_NOTIFICATION_ROW_MISSING"');
  assert.ok([compositeZeroParser, compositeAmbiguousParser, labelZeroParser, labelNonhittableParser, zeroParser, ambiguousParser, semanticZeroParser, semanticAmbiguousParser].every((index) => index >= 0 && index < genericRowParser), "composite, label, direct and semantic assertion codes must be classified before the generic row code");

  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-permission-stage-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = `set -Eeuo pipefail\nSIMULATOR_UDID=11111111-2222-3333-4444-555555555555\nBUNDLE_ID=com.sinsan.daon\nREPOSITORY_ROOT=/repo\nDERIVED_DATA=/derived\nDAON_SIM_PERMISSION_SERVICE=""\nxcrun(){ return 0; }\nxcodebuild(){ printf '%s\\n' "\${XCODE_RAW}"; return 65; }\n${contract}\nrun_permission_phase grant-initial grant GRANTED`;
  const fixturePath = path.join(fixtureRoot, "permission-stage-fixture.sh");
  await writeFile(fixturePath, fixture, "utf8");
  const run = (raw) => spawnSync(bash, [fixturePath], {
    cwd: fixtureRoot,
    env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), XCODE_RAW: raw },
    encoding: "utf8"
  });
  const annotation = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::error::"));

  try {
    const assertionFirst = run("DAON_PERMISSION_XCTEST_STAGE=ALERT_TITLE\nexpected one Notification system alert");
    assert.equal(assertionFirst.status, 65, assertionFirst.stderr);
    assert.deepEqual(annotation(assertionFirst.stdout), ["::error::CODE=ALERT_COUNT_MISMATCH PHASE=grant-initial EXIT=65"]);

    const lastAllowed = run("DAON_PERMISSION_XCTEST_STAGE=CAMERA_REQUEST\nDAON_PERMISSION_XCTEST_STAGE=PRIVATE_RAW_VALUE\nDAON_PERMISSION_XCTEST_STAGE=ALERT_ALLOW\nPRIVATE_USER=/Users/private https://internal.invalid");
    assert.equal(lastAllowed.status, 65, lastAllowed.stderr);
    assert.deepEqual(annotation(lastAllowed.stdout), ["::error::CODE=STAGE_ALERT_ALLOW PHASE=grant-initial EXIT=65"]);
    assert.doesNotMatch(annotation(lastAllowed.stdout)[0], /PRIVATE|Users|https|internal/);

    const bindingLast = run("DAON_PERMISSION_XCTEST_STAGE=PHASE_EXPECTED_BINDING\nDAON_PERMISSION_XCTEST_STAGE=PHASE_EXPECTED_MATCHED");
    assert.equal(bindingLast.status, 65, bindingLast.stderr);
    assert.deepEqual(annotation(bindingLast.stdout), ["::error::CODE=STAGE_PHASE_EXPECTED_MATCHED PHASE=grant-initial EXIT=65"]);

    const notificationBoundary = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_ROW\nDAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_CREATED\nDAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nDAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_COUNT_SINGLE\nDAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_ELEMENT_READY\nDAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_ROW_TAP_PENDING");
    assert.equal(notificationBoundary.status, 65, notificationBoundary.stderr);
    assert.deepEqual(annotation(notificationBoundary.stdout), ["::error::CODE=STAGE_SETTINGS_NOTIFICATION_ROW_TAP_PENDING PHASE=grant-initial EXIT=65"]);

    const notificationAssertionFirst = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_ROW_TAP_PENDING\nmissing or ambiguous exact system element: Notification settings row");
    assert.equal(notificationAssertionFirst.status, 65, notificationAssertionFirst.stderr);
    assert.deepEqual(annotation(notificationAssertionFirst.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_ROW_MISSING PHASE=grant-initial EXIT=65"]);

    const notificationZero = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [ZERO]");
    assert.equal(notificationZero.status, 65, notificationZero.stderr);
    assert.deepEqual(annotation(notificationZero.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_ROW_ZERO PHASE=grant-initial EXIT=65"]);

    const notificationAmbiguous = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [AMBIGUOUS]");
    assert.equal(notificationAmbiguous.status, 65, notificationAmbiguous.stderr);
    assert.deepEqual(annotation(notificationAmbiguous.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_ROW_AMBIGUOUS PHASE=grant-initial EXIT=65"]);

    const semanticZero = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [SEMANTIC_ZERO]");
    assert.equal(semanticZero.status, 65, semanticZero.stderr);
    assert.deepEqual(annotation(semanticZero.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO PHASE=grant-initial EXIT=65"]);

    const semanticAmbiguous = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [SEMANTIC_AMBIGUOUS]");
    assert.equal(semanticAmbiguous.status, 65, semanticAmbiguous.stderr);
    assert.deepEqual(annotation(semanticAmbiguous.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS PHASE=grant-initial EXIT=65"]);

    const labelZero = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [LABEL_ZERO]");
    assert.equal(labelZero.status, 65, labelZero.stderr);
    assert.deepEqual(annotation(labelZero.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_LABEL_ZERO PHASE=grant-initial EXIT=65"]);

    const labelNonhittable = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [LABEL_NONHITTABLE]");
    assert.equal(labelNonhittable.status, 65, labelNonhittable.stderr);
    assert.deepEqual(annotation(labelNonhittable.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_LABEL_NONHITTABLE PHASE=grant-initial EXIT=65"]);

    const compositeZero = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_ZERO]");
    assert.equal(compositeZero.status, 65, compositeZero.stderr);
    assert.deepEqual(annotation(compositeZero.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO PHASE=grant-initial EXIT=65"]);

    const compositeAmbiguous = run("DAON_PERMISSION_XCTEST_STAGE=SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED\nmissing or ambiguous exact system element: Notification settings row [COMPOSITE_AMBIGUOUS]");
    assert.equal(compositeAmbiguous.status, 65, compositeAmbiguous.stderr);
    assert.deepEqual(annotation(compositeAmbiguous.stdout), ["::error::CODE=SETTINGS_NOTIFICATION_COMPOSITE_ROW_AMBIGUOUS PHASE=grant-initial EXIT=65"]);

    const unknown = run("DAON_PERMISSION_XCTEST_STAGE=PRIVATE_RAW_VALUE\nDAON_PERMISSION_XCTEST_STAGE=ALERT_ALLOW_PRIVATE");
    assert.equal(unknown.status, 65, unknown.stderr);
    assert.deepEqual(annotation(unknown.stdout), ["::error::CODE=UNKNOWN_XCTEST_FAILURE PHASE=grant-initial EXIT=65"]);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Permission Phase는 환경 상속 없이 세 고정 XCTest Method를 exact 매핑한다", async () => {
  const swift = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const entries = [
    ["testPermissionGrantInitial", "grantInitial", "GRANTED"],
    ["testPermissionRevoke", "revoke", "DENIED"],
    ["testPermissionGrantAgain", "grantAgain", "GRANTED"]
  ];
  for (const [method, phase, expected] of entries) {
    assert.match(swift, new RegExp(`func ${method}\\(\\) throws \\{\\s*try runPermissionPhase\\(\\.${phase}, expected: "${expected}"\\)\\s*\\}`));
  }
  assert.equal((swift.match(/func testPermission(?:GrantInitial|Revoke|GrantAgain)\(\) throws/g) ?? []).length, 3);
  assert.match(swift, /private func runPermissionPhase\(_ phase: PermissionPhase, expected: String\) throws/);
  assert.doesNotMatch(swift, /ProcessInfo\.processInfo\.environment\["DAON_PERMISSION_(?:PHASE|EXPECTED)"\]/);
  assert.doesNotMatch(script, /DAON_PERMISSION_(?:PHASE|EXPECTED)=/);
  assert.doesNotMatch(script, /testPermissionRequestReflectsOSDecision|testPermission\$\{|testPermission\$\(phase\)/);

  const permissionStart = script.indexOf("run_permission_phase() {");
  const permissionEnd = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStart);
  const permissionContract = permissionStart >= 0 && permissionEnd > permissionStart
    ? script.slice(permissionStart, permissionEnd + 2)
    : "";
  assert.ok(permissionContract);
  for (const [method] of entries) assert.match(permissionContract, new RegExp(`permission_test_method="${method}"`));
  assert.match(permissionContract, /-only-testing:DaonUITests\/DaonUITests\/\$\{permission_test_method\}/);

  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-permission-method-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = `set -Eeuo pipefail\nSIMULATOR_UDID=11111111-2222-3333-4444-555555555555\nBUNDLE_ID=com.sinsan.daon\nREPOSITORY_ROOT=/repo\nDERIVED_DATA=/derived\nDAON_SIM_PERMISSION_SERVICE=""\nxcrun(){ return 0; }\nxcodebuild(){ printf '%s\\n' "$*"; return 0; }\n${permissionContract}\nrun_permission_phase "\${FIXTURE_PHASE}" grant`;
  try {
    for (const [method, phase] of entries) {
      const shellPhase = phase === "grantInitial" ? "grant-initial" : phase === "grantAgain" ? "grant-again" : phase;
      const result = spawnSync(bash, ["-c", fixture], {
        cwd: fixtureRoot,
        env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), FIXTURE_PHASE: shellPhase },
        encoding: "utf8"
      });
      assert.equal(result.status, 0, result.stderr);
      assert.equal((result.stdout.match(/-only-testing:/g) ?? []).length, 1);
      assert.match(result.stdout, new RegExp(`-only-testing:DaonUITests/DaonUITests/${method}(?:\\s|$)`));
    }
    const invalid = spawnSync(bash, ["-c", fixture], {
      cwd: fixtureRoot,
      env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), FIXTURE_PHASE: "grant-initial/private" },
      encoding: "utf8"
    });
    assert.equal(invalid.status, 64, invalid.stderr);
    assert.doesNotMatch(invalid.stdout, /-only-testing:/);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
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
  const requiredIds = ["checkout", "setup-node", "xcode", "setup-uv", "node-npm", "cocoapods", "npm-ci", "portable-contracts", "pods", "simulator", "build", "ui-tests", "simulator-verification"];
  for (const id of requiredIds) assert.ok(steps.some((step) => step.id === id), `missing required workflow step id: ${id}`);
  const serialized = JSON.stringify(workflow);
  for (const token of ["IOS_STEP_CHECKOUT", "IOS_STEP_SETUP_NODE", "IOS_STEP_SETUP_UV", "IOS_STEP_NPM_CI", "verification_completed", "workflow-outcomes.json", "phase-a-status.txt"]) {
    assert.match(`${serialized}\n${writer}`, new RegExp(token.replaceAll(".", "\\.")));
  }
  assert.match(writer, /FAILED/);
  assert.match(writer, /INCOMPLETE/);
  assert.match(writer, /run-ui-tests-with-diagnostics\.sh/);
  const embeddedNodePrograms = steps.flatMap((step) => [...(step.run ?? "").matchAll(/node -e '([^']+)'/g)].map((match) => match[1]));
  assert.ok(embeddedNodePrograms.length >= 2);
  for (const source of embeddedNodePrograms) assert.doesNotThrow(() => new Function(source));
});

test("macOS Workflow는 승인 uv Pin을 Toolchain 검증 전에 설치·검증하고 Manifest에 결속한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const writer = await read("apps/mobile/ios/ci/write-evidence.mjs");
  const toolchains = await readJson("toolchain-versions.json");
  const steps = workflow.jobs["ios-phase-a"].steps;
  const setupUvIndex = steps.findIndex((step) => step.id === "setup-uv");
  const toolchainIndex = steps.findIndex((step) => step.id === "node-npm");
  assert.ok(setupUvIndex >= 0 && setupUvIndex < toolchainIndex);
  const setupUv = steps[setupUvIndex];
  assert.equal(setupUv.uses, "astral-sh/setup-uv@v7");
  assert.equal(setupUv.with.version, "${{ steps.toolchain-pins.outputs.uv }}");
  const pinLoader = workflow.jobs["ios-phase-a"].steps.find((step) => step.id === "toolchain-pins");
  assert.match(pinLoader?.run ?? "", /toolchain-versions\.json/);
  assert.match(pinLoader?.run ?? "", /toolchains\.uv/);
  assert.equal(toolchains.toolchains.uv, "0.11.2");
  const verification = steps[toolchainIndex].run;
  assert.match(verification, /toolchain-versions\.json/);
  assert.match(verification, /uv --version/);
  assert.match(verification, /npm run verify:toolchain/);
  assert.match(JSON.stringify(workflow), /IOS_UV_VERSION/);
  assert.match(writer, /setup_uv/);
  assert.match(writer, /IOS_UV_VERSION/);
});

test("macOS Workflow는 uv Metadata를 보존하면서 두 번째 버전 토큰만 승인 Pin과 엄격 비교한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const steps = workflow.jobs["ios-phase-a"].steps;
  const verification = steps.find((step) => step.id === "node-npm")?.run ?? "";
  const contractLines = verification.split("\n").filter((line) =>
    /^UV_PIN=/.test(line)
    || /^test "\$\{UV_PIN\}" = "0\.11\.2"$/.test(line)
    || /^UV_VERSION=/.test(line)
    || /^test "\$\{UV_VERSION\}" = "\$\{UV_PIN\}"$/.test(line)
  );
  assert.equal(contractLines.length, 4);
  assert.match(contractLines[2], /uv --version[^\n]*awk '\{print \$2\}'/);

  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const contract = `set -euo pipefail\nuv() { printf '%s\\n' "\${UV_OUTPUT}"; }\n${contractLines.join("\n")}`;
  const run = (output) => spawnSync(bash, ["-c", contract], {
    cwd: root,
    env: { ...process.env, UV_OUTPUT: output },
    encoding: "utf8"
  });
  assert.equal(run("uv 0.11.2 (02036a8ba 2026-03-26 aarch64-apple-darwin)").status, 0);
  assert.notEqual(run("uv 0.11.3 (different-build-metadata)").status, 0);

  const manifest = steps.find((step) => step.id === "manifest")?.run ?? "";
  assert.match(manifest, /IOS_UV_VERSION="\$\(uv --version 2>\/dev\/null \|\| true\)"/);
});

test("macOS Workflow는 승인 CocoaPods를 Runner 전역과 분리한 Gem Home에서 실행한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const steps = workflow.jobs["ios-phase-a"].steps;
  const install = steps.find((step) => step.id === "cocoapods")?.run ?? "";
  const pods = steps.find((step) => step.id === "pods")?.run ?? "";
  const manifest = steps.find((step) => step.id === "manifest")?.run ?? "";
  assert.match(install, /POD_GEM_HOME="\$\{RUNNER_TEMP\}\//);
  assert.match(install, /POD_GEM_BIN="\$\{RUNNER_TEMP\}\//);
  assert.match(install, /gem env path/);
  assert.match(install, /--install-dir "\$\{POD_GEM_HOME\}" --bindir "\$\{POD_GEM_BIN\}"/);
  assert.match(install, /export GEM_HOME="\$\{POD_GEM_HOME\}"/);
  assert.match(install, /export GEM_PATH="\$\{POD_GEM_HOME\}:\$\{DEFAULT_GEM_PATH\}"/);
  assert.match(install, /GITHUB_ENV/);
  assert.match(pods, /test "\$\(gem exec -g cocoapods -v 1\.16\.2 pod --version\)" = "1\.16\.2"/);
  assert.doesNotMatch(install, /gem uninstall|sudo|rm\s+-[rf]/);
  assert.match(manifest, /IOS_COCOAPODS_VERSION="\$\(gem exec -g cocoapods -v 1\.16\.2 pod --version 2>\/dev\/null \|\| true\)"/);
});

test("macOS Workflow는 CocoaPods Gem 이름과 pod 실행 파일을 RubyGems 버전 지정 실행으로 결속한다", async () => {
  const workflow = await readJson(".github/workflows/release-1-ios-phase-a.yml");
  const steps = workflow.jobs["ios-phase-a"].steps;
  const install = steps.find((step) => step.id === "cocoapods")?.run ?? "";
  const pods = steps.find((step) => step.id === "pods")?.run ?? "";
  const manifest = steps.find((step) => step.id === "manifest")?.run ?? "";

  const versionContract = install.split("\n").filter((line) => /^DAON_POD_VERSION=/.test(line) || /^printf 'CocoaPods version:/.test(line) || /^test "\$\{DAON_POD_VERSION\}"/.test(line));
  assert.equal(versionContract.length, 3);
  assert.match(versionContract[0], /gem exec -g cocoapods -v 1\.16\.2 pod --version/);
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const contract = `set -euo pipefail\ngem() {\n  test "\$1" = exec\n  test "\$2" = -g\n  test "\$3" = cocoapods\n  test "\$4" = -v\n  test "\$5" = 1.16.2\n  test "\$6" = pod\n  test "\$7" = --version\n  test "\$#" = 7\n  printf '%s\\n' "\${POD_VERSION_OUTPUT}"\n}\n${versionContract.join("\n")}`;
  const run = (version) => spawnSync(bash, ["-c", contract], { env: { ...process.env, POD_VERSION_OUTPUT: version }, encoding: "utf8" });
  const approved = run("1.16.2");
  assert.equal(approved.status, 0, approved.stderr);
  assert.match(approved.stdout, /CocoaPods version: 1\.16\.2/);
  assert.notEqual(run("1.17.0").status, 0);

  assert.match(pods, /test "\$\(gem exec -g cocoapods -v 1\.16\.2 pod --version\)" = "1\.16\.2"/);
  assert.equal((pods.match(/gem exec -g cocoapods -v 1\.16\.2 pod install/g) ?? []).length, 2);

  assert.match(manifest, /IOS_COCOAPODS_VERSION="\$\(gem exec -g cocoapods -v 1\.16\.2 pod --version 2>\/dev\/null \|\| true\)"/);
  assert.equal((install.match(/gem exec -g cocoapods -v 1\.16\.2 pod --version/g) ?? []).length, 1);
  assert.equal((manifest.match(/gem exec -g cocoapods -v 1\.16\.2 pod --version/g) ?? []).length, 1);
  for (const source of [install, pods, manifest]) {
    assert.doesNotMatch(source, /gem exec -v 1\.16\.2 pod|gem exec(?: -g cocoapods)? -v 1\.16\.2 -- pod|(^|\n)pod(?:\s|$)|\$\(pod\s|DAON_POD_(?:BIN|SCRIPT)|"\$\{POD_GEM_BIN\}\/pod"|ruby[^\n]*cocoapods[^\n]*\/bin\/pod/);
  }
});

test("Settings Notifications 행은 direct exact Hittable 우선·semantic Cell fallback을 사용한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const helperStart = uiTests.indexOf("private func requireExactNotificationSettingsRow");
  const helperEnd = uiTests.indexOf("private func approveExpectedNotificationAlert", helperStart);
  const helper = helperStart >= 0 && helperEnd > helperStart ? uiTests.slice(helperStart, helperEnd) : "";
  assert.ok(helper, "Notifications exact Label row helper is required");
  assert.match(helper, /let exactLabelPredicate = NSPredicate\(format: "label == %@ OR label == %@", "Notifications", "알림"\)/);
  assert.match(helper, /let directQuery = settings\.descendants\(matching: \.any\)\.matching\(exactLabelPredicate\)/);
  assert.match(helper, /let semanticCellQuery = settings\.cells\.containing\(exactLabelPredicate\)/);
  assert.equal((helper.match(/settings\.cells\.containing\(exactLabelPredicate\)/g) ?? []).length, 1);
  assert.doesNotMatch(helper, /\.containing\(\.staticText, predicate:/);
  assert.doesNotMatch(helper, /NSPredicate\(format:[^\n]*isHittable/);
  assert.match(helper, /let exactLabelElements = directQuery\.allElementsBoundByAccessibilityElement\s*let directElements = exactLabelElements\.filter\s*\{\s*\$0\.isHittable\s*\}/);
  assert.match(helper, /semanticCellQuery\.allElementsBoundByAccessibilityElement\.filter\s*\{\s*\$0\.isHittable\s*\}/);
  const approvedCompositePredicate = /let compositeLabelPredicate = NSPredicate\(format: "label == %@ OR label BEGINSWITH %@ OR label == %@ OR label BEGINSWITH %@", "Notifications", "Notifications,", "알림", "알림,"\)/;
  assert.match(helper, approvedCompositePredicate);
  assert.match(helper, /let compositeCellQuery = settings\.cells\.matching\(compositeLabelPredicate\)/);
  assert.match(helper, /let compositeCells = exactLabelElements\.isEmpty && semanticCells\.isEmpty\s*\? compositeCellQuery\.allElementsBoundByAccessibilityElement\.filter\s*\{\s*\$0\.isHittable\s*\}\s*: \[\]/);
  assert.match(helper, /if candidates\.exactLabels\.isEmpty && candidates\.direct\.isEmpty && candidates\.semantic\.isEmpty && candidates\.composite\.isEmpty\s*\{\s*permissionXCTestStage\(\.settingsNotificationScrollSearch\)\s*for _ in 0\.\.<4\s*\{\s*settings\.swipeUp\(\)\s*candidates = collectNotificationCandidates\(\)/);
  assert.match(helper, /if !candidates\.exactLabels\.isEmpty \|\| !candidates\.direct\.isEmpty \|\| !candidates\.semantic\.isEmpty \|\| !candidates\.composite\.isEmpty\s*\{\s*break\s*\}/);
  assert.equal((helper.match(/settings\.swipeUp\(\)/g) ?? []).length, 1, "bounded loop must contain the only Settings swipe source call");
  assert.doesNotMatch(helper, /sleep|usleep|Thread\.sleep|while\s|repeat\s*\{/i);
  const directCollection = helper.lastIndexOf("let exactLabelElements = directQuery.allElementsBoundByAccessibilityElement");
  const collector = helper.indexOf("func collectNotificationCandidates()");
  const initialCandidates = helper.indexOf("var candidates = collectNotificationCandidates()");
  const scrollStage = helper.indexOf("permissionXCTestStage(.settingsNotificationScrollSearch)");
  const directAmbiguous = helper.indexOf("if candidates.direct.count > 1");
  const directSingle = helper.indexOf("if candidates.direct.count == 1");
  const semanticCollection = helper.lastIndexOf("semanticCellQuery.allElementsBoundByAccessibilityElement.filter");
  const semanticAmbiguous = helper.indexOf("guard candidates.semantic.count == 1");
  const exactNonhittable = helper.indexOf("else if !candidates.exactLabels.isEmpty");
  const compositeZero = helper.indexOf("if candidates.composite.isEmpty");
  const compositeAmbiguous = helper.indexOf("guard candidates.composite.count == 1");
  const elementAccess = helper.indexOf("let element = selectedElements.popLast()");
  const elementReturn = helper.indexOf("return element");
  assert.ok(collector >= 0 && directCollection > collector && semanticCollection > directCollection && initialCandidates > semanticCollection && scrollStage > initialCandidates && directAmbiguous > scrollStage && directSingle > directAmbiguous && semanticAmbiguous > directSingle && exactNonhittable > semanticAmbiguous && compositeZero > exactNonhittable && compositeAmbiguous > compositeZero && elementAccess > compositeAmbiguous && elementReturn > elementAccess, "same-query collection and bounded scroll must finish before priority-preserving resolution");
  assert.match(helper, /if candidates\.direct\.count > 1\s*\{[\s\S]*?Notification settings row \[AMBIGUOUS\]/);
  assert.match(helper, /if candidates\.direct\.count == 1\s*\{\s*selectedElements = candidates\.direct/);
  assert.match(helper, /guard candidates\.semantic\.count == 1 else \{[\s\S]*?Notification settings row \[SEMANTIC_AMBIGUOUS\]/);
  assert.match(helper, /else if !candidates\.exactLabels\.isEmpty\s*\{[\s\S]*?Notification settings row \[LABEL_NONHITTABLE\]/);
  assert.match(helper, /if candidates\.composite\.isEmpty\s*\{[\s\S]*?Notification settings row \[COMPOSITE_ZERO\]/);
  assert.match(helper, /guard candidates\.composite\.count == 1 else \{[\s\S]*?Notification settings row \[COMPOSITE_AMBIGUOUS\]/);
  assert.match(helper, /selectedElements = candidates\.composite/);
  const queryCreated = helper.indexOf("permissionXCTestStage(.settingsNotificationQueryCreated)");
  const waitCompleted = helper.indexOf("permissionXCTestStage(.settingsNotificationQueryWaitCompleted)");
  const countSingle = helper.indexOf("permissionXCTestStage(.settingsNotificationCountSingle)");
  const elementReady = helper.indexOf("permissionXCTestStage(.settingsNotificationElementReady)");
  assert.ok(queryCreated > helper.indexOf("let semanticCellQuery =") && queryCreated < helper.indexOf("let appeared ="));
  assert.ok(helper.indexOf("directQuery.allElementsBoundByAccessibilityElement.filter") < helper.indexOf("semanticCellQuery.allElementsBoundByAccessibilityElement.filter"), "wait must prefer direct candidates before semantic cells");
  assert.ok(directCollection > waitCompleted && directCollection < directAmbiguous, "exact Label elements must be collected after wait without changing selection order");
  assert.ok(waitCompleted > helper.indexOf("XCTWaiter.wait") && waitCompleted < directCollection);
  assert.ok(countSingle > compositeAmbiguous && countSingle < elementAccess);
  assert.ok(elementReady > elementAccess && elementReady < elementReturn);
  const helperWithoutApprovedCompositePredicate = helper.replace(approvedCompositePredicate, "");
  assert.doesNotMatch(helperWithoutApprovedCompositePredicate, /settings\.cells\[|\.buttons|\.links|identifier|CONTAINS|BEGINSWITH|ENDSWITH|MATCHES|firstMatch|element\(boundBy:|coordinate\(/i);

  const settingsStart = uiTests.indexOf("private func setNotificationAuthorization");
  const settingsEnd = uiTests.indexOf("private func notificationSwitchIsEnabled", settingsStart);
  const settingsHelper = settingsStart >= 0 && settingsEnd > settingsStart ? uiTests.slice(settingsStart, settingsEnd) : "";
  assert.ok(settingsHelper);
  assert.match(settingsHelper, /let notificationsRow = try requireExactNotificationSettingsRow\(in: settings, allowAbsent: true\)/);
  assert.match(settingsHelper, /XCTAssertTrue\(notificationsRow\.isHittable,[^\n]+\)\s*permissionXCTestStage\(\.settingsNotificationRowTapPending\)\s*notificationsRow\.tap\(\)/);
  assert.doesNotMatch(settingsHelper, /settings\.cells\["(?:Notifications|알림)"\]/);
});

test("Settings Notifications 최종 0건은 제한된 접근성 구조 요약을 정확히 한 번 출력한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const diagnosticStart = uiTests.indexOf("private func settingsDiagnosticToken");
  const helperStart = uiTests.indexOf("private func requireExactNotificationSettingsRow");
  const helperEnd = uiTests.indexOf("private func approveExpectedNotificationAlert", helperStart);
  const diagnostic = diagnosticStart >= 0 && helperStart > diagnosticStart ? uiTests.slice(diagnosticStart, helperStart) : "";
  const helper = helperStart >= 0 && helperEnd > helperStart ? uiTests.slice(helperStart, helperEnd) : "";
  assert.ok(diagnostic);
  assert.ok(helper);
  assert.match(diagnostic, /private func emitSettingsAccessibilitySummary/);
  assert.match(diagnostic, /DAON_SETTINGS_ACCESSIBILITY_SUMMARY=/);
  assert.match(diagnostic, /maximumElementCount\s*=\s*16/);
  assert.match(diagnostic, /maximumTokenLength\s*=\s*80/);
  assert.match(diagnostic, /maximumSummaryLength\s*=\s*4096/);
  for (const query of ["settings.cells", "settings.buttons", "settings.staticTexts", "settings.switches"]) assert.match(diagnostic, new RegExp(query.replace(".", "\\.")));
  for (const field of ["element.label", "element.identifier", "element.isHittable"]) assert.match(diagnostic, new RegExp(field.replace(".", "\\.")));
  for (const [type, query] of [["cell", "cells"], ["button", "buttons"], ["staticText", "staticTexts"], ["switch", "switches"]]) {
    assert.match(diagnostic, new RegExp(`\\("${type}", settings\\.${query}\\.allElementsBoundByAccessibilityElement\\)`));
  }
  assert.match(diagnostic, /_empty_/);
  assert.doesNotMatch(diagnostic, /debugDescription|ProcessInfo|environment|\/Users\/|dump\(|coordinate\(|element\(boundBy:|firstMatch/i);
  assert.equal((helper.match(/emitSettingsAccessibilitySummary\(in: settings\)/g) ?? []).length, 1);
  assert.match(helper, /if candidates\.composite\.isEmpty\s*\{\s*emitSettingsAccessibilitySummary\(in: settings\)\s*if allowAbsent\s*\{\s*throw PermissionUIContractError\.notificationSettingsRowAbsent\s*\}\s*XCTFail\("missing or ambiguous exact system element: Notification settings row \[COMPOSITE_ZERO\]"\)/);
});

test("권한 Phase A는 동일 설치에서 System Alert 승인과 Production Settings OFF·ON을 직접 검증한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const shell = await read("apps/mobile/src/MobileShell.tsx");
  const permissionStart = uiTests.indexOf("func testPermissionGrantInitial()");
  const permissionBody = permissionStart >= 0 ? uiTests.slice(permissionStart) : "";
  assert.ok(permissionBody);

  for (const kind of ["camera", "microphone", "notification"]) assert.match(uiTests, new RegExp(`"${kind}"`));
  assert.match(uiTests, /\\\(kind\) 권한 요청/);
  for (const state of ["GRANTED", "DENIED"]) assert.match(uiTests, new RegExp(state));
  assert.match(permissionBody, /grant-initial/);
  assert.match(permissionBody, /revoke/);
  assert.match(permissionBody, /grant-again/);
  assert.match(permissionBody, /XCUIApplication\(bundleIdentifier: "com\.apple\.springboard"\)/);
  assert.match(permissionBody, /alerts\.count/);
  assert.match(permissionBody, /Would Like to Send You Notifications|알림을 보내고자 합니다/);
  assert.match(permissionBody, /"Allow"/);
  assert.match(permissionBody, /"허용"/);
  const alertHelperStart = permissionBody.indexOf("private func approveExpectedNotificationAlert()");
  const alertHelperEnd = permissionBody.indexOf("private func setNotificationAuthorization", alertHelperStart);
  const alertHelper = alertHelperStart >= 0 && alertHelperEnd > alertHelperStart ? permissionBody.slice(alertHelperStart, alertHelperEnd) : "";
  assert.ok(alertHelper);
  const titleWaitIndex = alertHelper.indexOf("_ = try requireExactElement([");
  const alertCountIndex = alertHelper.indexOf("XCTAssertEqual(springboard.alerts.count, 1");
  const allowButtonIndex = alertHelper.indexOf("let allowButton = try requireExactElement([");
  assert.ok(titleWaitIndex >= 0 && titleWaitIndex < alertCountIndex && alertCountIndex < allowButtonIndex, "expected title wait must complete before alert count and Allow lookup");
  assert.match(permissionBody, /app\.buttons\["알림 설정 열기"\]/);
  assert.match(permissionBody, /XCUIApplication\(bundleIdentifier: "com\.apple\.Preferences"\)/);
  assert.match(permissionBody, /"Allow Notifications"/);
  assert.match(permissionBody, /"알림 허용"/);
  const settingsHelperStart = permissionBody.indexOf("private func setNotificationAuthorization");
  const settingsHelperEnd = permissionBody.indexOf("private func notificationSwitchIsEnabled", settingsHelperStart);
  const settingsHelper = settingsHelperStart >= 0 && settingsHelperEnd > settingsHelperStart ? permissionBody.slice(settingsHelperStart, settingsHelperEnd) : "";
  assert.ok(settingsHelper);
  const directSwitch = settingsHelper.indexOf("findExactNotificationSwitch(in: settings)");
  const fallbackRow = settingsHelper.indexOf("requireExactNotificationSettingsRow(in: settings");
  assert.ok(directSwitch >= 0 && fallbackRow > directSwitch, "notification switch must be queried before the iOS 15.1 row fallback");
  assert.match(settingsHelper, /if let directSwitch = try findExactNotificationSwitch\(in: settings\)[\s\S]*?allowNotifications = directSwitch[\s\S]*?else[\s\S]*?requireExactNotificationSettingsRow/);
  assert.doesNotMatch(settingsHelper, /settings\.wait\(for: \.runningForeground[^\n]*\)\s*permissionXCTestStage\(\.appReturnRoot\)/);
  assert.match(permissionBody, /notificationSwitchIsEnabled/);
  assert.match(permissionBody, /app\.activate\(\)[\s\S]*requireRootReady\(app\)/);
  assert.match(permissionBody, /권한 결과/);
  assert.match(shell, /requestPermission\(kind\)/);
  assert.match(shell, /권한 결과/);
  for (const phase of ["grant-initial", "revoke", "grant-again"]) assert.match(script, new RegExp(phase));
  assert.match(script, /grant-initial\|revoke\|grant-again/);
  assert.match(script, /permission_test_method="testPermissionGrantInitial"/);
  assert.match(script, /permission_test_method="testPermissionRevoke"/);
  assert.match(script, /permission_test_method="testPermissionGrantAgain"/);
  assert.match(script, /-only-testing:DaonUITests\/DaonUITests\/\$\{permission_test_method\}/);
  assert.doesNotMatch(`${script}\n${uiTests}`, /DAON_PERMISSION_(?:PHASE|EXPECTED)/);
  assert.equal((script.match(/simctl privacy[^\n]*camera/g) ?? []).length, 1);
  assert.equal((script.match(/simctl privacy[^\n]*microphone/g) ?? []).length, 1);
  assert.doesNotMatch(script, /simctl privacy[^\n]*notifications/i);
  assert.equal((script.match(/xcrun simctl install "\$\{SIMULATOR_UDID\}" "\$\{APP_PATH\}"/g) ?? []).length, 1);
  assert.match(script, /test-without-building/);
  assert.match(script, /\.xcresult/);
  assert.doesNotMatch(`${script}\n${uiTests}`, /coordinate\(|firstMatch|element\(boundBy:|App-Prefs|prefs:|TCC\.db|defaults\s+(?:write|delete)|simctl\s+(?:uninstall|erase)/i);
});
test("iOS 26 Simulator Settings Search fallback은 direct·row 뒤 exact 단일 접근성 계약만 사용한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = uiTests.indexOf("private func openDaonNotificationSettingsViaIOS26Search");
  const helperEnd = uiTests.indexOf("private func notificationSwitchIsEnabled", helperStart);
  const helper = helperStart >= 0 && helperEnd > helperStart ? uiTests.slice(helperStart, helperEnd) : "";
  assert.ok(helper);
  assert.match(helper, /#available\(iOS 26\.0, \*\)/);
  assert.match(helper, /identifier == %@[^\n]*com\.apple\.settings\.search/);
  assert.match(helper, /settings\.buttons\.matching\(searchButtonPredicate\)/);
  assert.match(helper, /settings\.searchFields\.allElementsBoundByAccessibilityElement\.filter \{ \$0\.isHittable \}/);
  assert.match(helper, /maximumExistingSearchTextLength\s*=\s*128/);
  assert.match(helper, /XCUIKeyboardKey\.delete\.rawValue/);
  assert.match(helper, /searchField\.typeText\("Daon"\)/);
  assert.match(helper, /label == %@[^\n]*"Daon"/);
  assert.match(helper, /settings\.cells\.matching\(exactDaonPredicate\)/);
  assert.match(helper, /settings\.descendants\(matching: \.any\)\.matching\(exactDaonPredicate\)/);
  assert.match(helper, /if daonCells\.count == 1[\s\S]*?else if daonCells\.isEmpty[\s\S]*?exactDaonElements\.count == 1/);
  assert.match(helper, /findExactNotificationSwitch\(in: settings\)[\s\S]*?requireExactNotificationSettingsRow\(in: settings(?:, allowAbsent: true)?\)/);
  for (const stageCase of ["settingsSearchButton", "settingsSearchField", "settingsSearchResult", "settingsSearchAppSurface"]) {
    assert.match(uiTests, new RegExp(`case ${stageCase} =`));
    assert.match(helper, new RegExp(`permissionXCTestStage\\(\\.${stageCase}\\)`));
  }
  const authorizationStart = uiTests.indexOf("private func setNotificationAuthorization");
  const authorizationEnd = uiTests.indexOf("private func notificationSwitchIsEnabled", authorizationStart);
  const authorization = authorizationStart >= 0 && authorizationEnd > authorizationStart ? uiTests.slice(authorizationStart, authorizationEnd) : "";
  const direct = authorization.indexOf("findExactNotificationSwitch(in: settings)");
  const row = authorization.indexOf("requireExactNotificationSettingsRow(in: settings");
  const search = authorization.indexOf("openDaonNotificationSettingsViaIOS26Search(in: settings)");
  assert.ok(direct >= 0 && row > direct && search > row, "Search fallback must follow direct switch and exact Notifications row");
  assert.match(authorization, /catch PermissionUIContractError\.notificationSettingsRowAbsent[\s\S]*?#available\(iOS 26\.0, \*\)[\s\S]*?openDaonNotificationSettingsViaIOS26Search/);
  assert.doesNotMatch(`${helper}\n${authorization}`, /coordinate\(|firstMatch|element\(boundBy:|label (?:CONTAINS|BEGINSWITH|ENDSWITH|MATCHES)|sleep\(|App-Prefs|prefs:|TCC|defaults\s+(?:write|delete)|simctl\s+(?:uninstall|erase)/i);

  for (const code of ["SETTINGS_SEARCH_BUTTON_MISSING", "SETTINGS_SEARCH_FIELD_MISSING", "SETTINGS_SEARCH_RESULT_MISSING", "SETTINGS_SEARCH_APP_SURFACE_MISSING"]) {
    assert.match(script, new RegExp(code));
  }
  for (const stage of ["SETTINGS_SEARCH_BUTTON", "SETTINGS_SEARCH_FIELD", "SETTINGS_SEARCH_RESULT", "SETTINGS_SEARCH_APP_SURFACE"]) {
    assert.match(script, new RegExp(`STAGE_${stage}`));
    assert.match(script, new RegExp(`DAON_PERMISSION_XCTEST_STAGE=.*${stage}`));
  }
  assert.doesNotMatch(script, /simctl privacy[^\n]*notifications/i);
});
test("C44 Search assertion과 Stage는 Bash allowlist에서 원 Exit 65로 분류된다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_allowed_permission_failure_code() {");
  const permissionStart = script.indexOf("run_permission_phase() {", helperStart);
  const permissionEnd = script.indexOf('\n}\n\nDAON_SIM_STAGE="APP_ARTIFACT"', permissionStart);
  const contract = helperStart >= 0 && permissionStart > helperStart && permissionEnd > permissionStart
    ? script.slice(helperStart, permissionEnd + 2)
    : "";
  assert.ok(contract);
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-ios26-search-parser-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = `set -Eeuo pipefail\nSIMULATOR_UDID=11111111-2222-3333-4444-555555555555\nBUNDLE_ID=com.sinsan.daon\nREPOSITORY_ROOT=/repo\nDERIVED_DATA=/derived\nDAON_SIM_PERMISSION_SERVICE=""\nxcrun(){ return 0; }\nxcodebuild(){ printf '%s\\n' "\${XCODE_RAW}"; return 65; }\n${contract}\nrun_permission_phase grant-initial grant GRANTED`;
  const fixturePath = path.join(fixtureRoot, "ios26-search-parser-fixture.sh");
  await writeFile(fixturePath, fixture, "utf8");
  const run = (raw) => spawnSync(bash, [fixturePath], {
    cwd: fixtureRoot,
    env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), XCODE_RAW: raw },
    encoding: "utf8"
  });
  const annotation = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::error::"));
  const assertions = [
    ["missing or ambiguous exact system element: Settings search button", "SETTINGS_SEARCH_BUTTON_MISSING"],
    ["missing or ambiguous exact system element: Settings search field", "SETTINGS_SEARCH_FIELD_MISSING"],
    ["missing or ambiguous exact system element: Settings search result", "SETTINGS_SEARCH_RESULT_MISSING"],
    ["missing or ambiguous exact system element: Daon notification settings surface", "SETTINGS_SEARCH_APP_SURFACE_MISSING"]
  ];
  const stages = ["SETTINGS_SEARCH_BUTTON", "SETTINGS_SEARCH_FIELD", "SETTINGS_SEARCH_RESULT", "SETTINGS_SEARCH_APP_SURFACE"];
  try {
    for (const [raw, code] of assertions) {
      const result = run(raw);
      assert.equal(result.status, 65, result.stderr);
      assert.deepEqual(annotation(result.stdout), [`::error::CODE=${code} PHASE=grant-initial EXIT=65`]);
    }
    for (const stage of stages) {
      const result = run(`DAON_PERMISSION_XCTEST_STAGE=${stage}`);
      assert.equal(result.status, 65, result.stderr);
      assert.deepEqual(annotation(result.stdout), [`::error::CODE=STAGE_${stage} PHASE=grant-initial EXIT=65`]);
    }
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
test("C45 recoverable COMPOSITE_ZERO는 선행 XCTest failure 없이 두 경로에만 전달된다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const rowStart = uiTests.indexOf("private func requireExactNotificationSettingsRow");
  const rowEnd = uiTests.indexOf("private func approveExpectedNotificationAlert", rowStart);
  const rowHelper = rowStart >= 0 && rowEnd > rowStart ? uiTests.slice(rowStart, rowEnd) : "";
  const searchStart = uiTests.indexOf("private func openDaonNotificationSettingsViaIOS26Search");
  const authorizationStart = uiTests.indexOf("private func setNotificationAuthorization", searchStart);
  const helperEnd = uiTests.indexOf("private func notificationSwitchIsEnabled", authorizationStart);
  const searchHelper = searchStart >= 0 && authorizationStart > searchStart ? uiTests.slice(searchStart, authorizationStart) : "";
  const authorization = authorizationStart >= 0 && helperEnd > authorizationStart ? uiTests.slice(authorizationStart, helperEnd) : "";
  assert.ok(rowHelper && searchHelper && authorization);
  assert.match(rowHelper, /private func requireExactNotificationSettingsRow\(in settings: XCUIApplication, allowAbsent: Bool = false\)/);
  const zeroStart = rowHelper.indexOf("if candidates.composite.isEmpty");
  const zeroEnd = rowHelper.indexOf("guard candidates.composite.count == 1", zeroStart);
  const zeroBranch = zeroStart >= 0 && zeroEnd > zeroStart ? rowHelper.slice(zeroStart, zeroEnd) : "";
  assert.match(zeroBranch, /emitSettingsAccessibilitySummary\(in: settings\)[\s\S]*?if allowAbsent\s*\{\s*throw PermissionUIContractError\.notificationSettingsRowAbsent\s*\}[\s\S]*?XCTFail\("missing or ambiguous exact system element: Notification settings row \[COMPOSITE_ZERO\]"\)/);
  assert.equal((uiTests.match(/requireExactNotificationSettingsRow\(in: settings, allowAbsent: true\)/g) ?? []).length, 2);
  assert.doesNotMatch(searchHelper, /Notification settings row \[COMPOSITE_ZERO\]/);
  assert.match(searchHelper, /requireExactNotificationSettingsRow\(in: settings, allowAbsent: true\)[\s\S]*?catch PermissionUIContractError\.notificationSettingsRowAbsent[\s\S]*?XCTFail\("missing or ambiguous exact system element: Daon notification settings surface"\)/);
  assert.match(authorization, /requireExactNotificationSettingsRow\(in: settings, allowAbsent: true\)[\s\S]*?catch PermissionUIContractError\.notificationSettingsRowAbsent[\s\S]*?if #available\(iOS 26\.0, \*\)[\s\S]*?openDaonNotificationSettingsViaIOS26Search[\s\S]*?else\s*\{[\s\S]*?XCTFail\("missing or ambiguous exact system element: Notification settings row \[COMPOSITE_ZERO\]"\)[\s\S]*?throw PermissionUIContractError\.notificationSettingsRowAbsent/);
  for (const boundary of ["[AMBIGUOUS]", "[SEMANTIC_AMBIGUOUS]", "[LABEL_NONHITTABLE]", "[COMPOSITE_AMBIGUOUS]"]) {
    assert.ok(rowHelper.includes(boundary), `existing fail-close boundary missing: ${boundary}`);
  }
});
test("C46 Search input 접근성 진단은 bounded sanitized 후보만 failure guard에서 한 번 출력한다", async () => {
  const uiTests = await read("apps/mobile/ios/DaonUITests/DaonUITests.swift");
  const diagnosticStart = uiTests.indexOf("private func emitSettingsSearchAccessibilitySummary");
  const rowStart = uiTests.indexOf("private func requireExactNotificationSettingsRow", diagnosticStart);
  const diagnostic = diagnosticStart >= 0 && rowStart > diagnosticStart ? uiTests.slice(diagnosticStart, rowStart) : "";
  const searchStart = uiTests.indexOf("private func openDaonNotificationSettingsViaIOS26Search");
  const searchEnd = uiTests.indexOf("private func setNotificationAuthorization", searchStart);
  const searchHelper = searchStart >= 0 && searchEnd > searchStart ? uiTests.slice(searchStart, searchEnd) : "";
  assert.ok(diagnostic && searchHelper);
  assert.match(diagnostic, /DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY=v1\|count=/);
  assert.match(diagnostic, /maximumElementCount\s*=\s*16/);
  assert.match(diagnostic, /maximumTokenLength\s*=\s*48/);
  assert.match(diagnostic, /\("searchField", settings\.searchFields\.allElementsBoundByAccessibilityElement\)/);
  assert.match(diagnostic, /\("textField", settings\.textFields\.allElementsBoundByAccessibilityElement\)/);
  for (const field of ["element.label", "element.identifier", "element.value", "element.isHittable"]) assert.match(diagnostic, new RegExp(field.replace(".", "\\.")));
  assert.match(diagnostic, /elementType=\\\(group\.type\),label=\\\(label\),identifier=\\\(identifier\),value=\\\(value\),isHittable=\\\(hittable\)/);
  assert.match(uiTests.slice(uiTests.indexOf("private func settingsDiagnosticToken"), rowStart), /return token\.isEmpty \? "_empty_"/);
  assert.doesNotMatch(diagnostic, /debugDescription|frame|pid|ProcessInfo|environment|\/Users\/|path=|keyboard|dump\(|coordinate\(|element\(boundBy:|firstMatch/i);
  assert.equal((searchHelper.match(/emitSettingsSearchAccessibilitySummary\(in: settings\)/g) ?? []).length, 1);
  assert.match(searchHelper, /guard searchFields\.count == 1, let searchField = searchFields\.popLast\(\) else\s*\{\s*emitSettingsSearchAccessibilitySummary\(in: settings\)\s*XCTFail\("missing or ambiguous exact system element: Settings search field"\)\s*throw PermissionUIContractError\.missingExactElement\("Settings search field"\)/);
  assert.match(searchHelper, /settings\.searchFields\.allElementsBoundByAccessibilityElement\.filter \{ \$0\.isHittable \}/);
  assert.match(searchHelper, /searchField\.typeText\("Daon"\)/);
});
test("C46 Search input Summary Notice는 strict schema만 공개하고 원 Exit 65를 보존한다", async () => {
  const script = await read("apps/mobile/ios/ci/verify-simulator.sh");
  const helperStart = script.indexOf("is_allowed_permission_failure_code() {");
  const permissionStart = script.indexOf("run_permission_phase() {", helperStart);
  const permissionEnd = script.indexOf("\n}\n\nDAON_SIM_STAGE=\"APP_ARTIFACT\"", permissionStart);
  const contract = helperStart >= 0 && permissionStart > helperStart && permissionEnd > permissionStart ? script.slice(helperStart, permissionEnd + 2) : "";
  assert.ok(contract);
  assert.match(contract, /report_settings_search_accessibility_notice\(\)/);
  assert.match(contract, /DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY=/);
  assert.match(contract, /elementType=\(searchField\|textField\)/);
  assert.match(contract, /\{1,48\}/);
  assert.doesNotMatch(contract, /eval|debugDescription|printenv|::notice::\$\{?[^\n]*raw/i);
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-search-accessibility-notice-"));
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  const fixture = [
    "set -Eeuo pipefail",
    "SIMULATOR_UDID=11111111-2222-3333-4444-555555555555",
    "BUNDLE_ID=com.sinsan.daon",
    "REPOSITORY_ROOT=/repo",
    "DERIVED_DATA=/derived",
    "DAON_SIM_PERMISSION_SERVICE=\"\"",
    "xcrun(){ return 0; }",
    "xcodebuild(){ echo \"${XCODE_RAW}\"; return \"${XCODE_EXIT}\"; }",
    contract,
    "run_permission_phase revoke revoke DENIED"
  ].join("\n");
  const fixturePath = path.join(fixtureRoot, "search-accessibility-notice-fixture.sh");
  await writeFile(fixturePath, fixture, "utf8");
  const run = (exit, raw) => spawnSync(bash, [fixturePath], { cwd: fixtureRoot, env: { ...process.env, EVIDENCE_DIR: fixtureRoot.replaceAll("\\", "/"), XCODE_EXIT: String(exit), XCODE_RAW: raw }, encoding: "utf8" });
  const notices = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::notice::DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY="));
  const errors = (output) => output.split(/\r?\n/).filter((line) => line.startsWith("::error::"));
  const failure = "missing or ambiguous exact system element: Settings search field";
  const prefix = "DAON_SETTINGS_SEARCH_ACCESSIBILITY_SUMMARY=v1|count=";
  const valid = prefix + "2|items=elementType=searchField,label=Search,identifier=_empty_,value=_empty_,isHittable=0;elementType=textField,label=_empty_,identifier=com.apple.settings.search,value=Search,isHittable=1";
  try {
    const rejected = [
      failure,
      valid + "\n" + valid + "\n" + failure,
      valid + "::error::SECRET\n" + failure,
      prefix + "1|items=elementType=button,label=Search,identifier=_empty_,value=_empty_,isHittable=1\n" + failure,
      prefix + "1|items=elementType=searchField,label=" + "A".repeat(49) + ",identifier=_empty_,value=_empty_,isHittable=1\n" + failure,
      prefix + "17|items=_none_\n" + failure,
      valid.replace("count=2", "count=1") + "\n" + failure
    ];
    for (const raw of rejected) {
      const result = run(65, raw);
      assert.equal(result.status, 65, result.stderr);
      assert.deepEqual(notices(result.stdout), []);
      assert.deepEqual(errors(result.stdout), ["::error::CODE=SETTINGS_SEARCH_FIELD_MISSING PHASE=revoke EXIT=65"]);
    }
    const accepted = run(65, valid + "\n" + failure);
    assert.equal(accepted.status, 65, accepted.stderr);
    assert.deepEqual(notices(accepted.stdout), ["::notice::" + valid]);
    assert.deepEqual(errors(accepted.stdout), ["::error::CODE=SETTINGS_SEARCH_FIELD_MISSING PHASE=revoke EXIT=65"]);
    const success = run(0, valid);
    assert.equal(success.status, 0, success.stderr);
    assert.deepEqual(notices(success.stdout), []);
    assert.deepEqual(errors(success.stdout), []);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
