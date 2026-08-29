import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const androidRoot = path.join(root, "apps/mobile/android");

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

test("Android Project는 승인 Application ID·표시명·Android 12+ 계약을 고정한다", async () => {
  const appGradle = await read("apps/mobile/android/app/build.gradle");
  const rootGradle = await read("apps/mobile/android/build.gradle");
  const strings = await read("apps/mobile/android/app/src/main/res/values/strings.xml");
  const appJson = await readJson("apps/mobile/app.json");
  assert.match(appGradle, /namespace\s+"com\.sinsan\.daon"/);
  assert.match(appGradle, /applicationId\s+"com\.sinsan\.daon"/);
  assert.match(rootGradle, /minSdkVersion\s*=\s*31/);
  assert.match(strings, /<string name="app_name">Daon<\/string>/);
  assert.deepEqual(appJson, { name: "Daon", displayName: "Daon" });
});

test("0.86-stable Template 출처와 설치 Toolchain에 맞춘 정확 Pin을 기록한다", async () => {
  const provenance = await readJson("apps/mobile/android/template-provenance.json");
  const rootGradle = await read("apps/mobile/android/build.gradle");
  const wrapper = await read("apps/mobile/android/gradle/wrapper/gradle-wrapper.properties");
  assert.deepEqual(provenance, {
    repository: "react-native-community/template",
    branch: "0.86-stable",
    commit: "4d7c716d7afddc03ed73ca49c1102a92a0a9ff71",
    react_native: "0.86.3"
  });
  assert.match(rootGradle, /buildToolsVersion\s*=\s*"36\.1\.0"/);
  assert.match(rootGradle, /compileSdkVersion\s*=\s*36/);
  assert.match(rootGradle, /targetSdkVersion\s*=\s*36/);
  assert.match(rootGradle, /ndkVersion\s*=\s*"28\.2\.13676358"/);
  assert.match(rootGradle, /kotlinVersion\s*=\s*"2\.1\.20"/);
  assert.match(wrapper, /gradle-9\.3\.1-bin\.zip/);
  const appGradle = await read("apps/mobile/android/app/build.gradle");
  assert.match(appGradle, /hermesCommand\s*=\s*"\.\.\/\.\.\/node_modules\/hermes-compiler\/hermesc\/%OS-BIN%\/hermesc"/);
  assert.match(appGradle, /debuggableVariants\s*=\s*\[\]/);
});

test("Manifest는 Camera·Microphone·Notification 최소 권한만 선언하고 Storage 권한을 금지한다", async () => {
  const manifest = await read("apps/mobile/android/app/src/main/AndroidManifest.xml");
  for (const permission of ["android.permission.CAMERA", "android.permission.RECORD_AUDIO", "android.permission.POST_NOTIFICATIONS"]) {
    assert.match(manifest, new RegExp(permission.replaceAll(".", "\\.")));
  }
  assert.doesNotMatch(manifest, /READ_EXTERNAL_STORAGE|WRITE_EXTERNAL_STORAGE|MANAGE_EXTERNAL_STORAGE|READ_MEDIA_(?:IMAGES|VIDEO|AUDIO)/);
  assert.match(manifest, /android:name="android\.hardware\.camera" android:required="false"/);
  assert.match(manifest, /android:name="android\.hardware\.microphone" android:required="false"/);
  assert.match(manifest, /android:allowBackup="false"/);
  assert.match(manifest, /android:usesCleartextTraffic="false"/);
  assert.match(manifest, /android:name="\.MainActivity"[\s\S]*?android:exported="true"/);
  assert.match(manifest, /android:name="android\.intent\.action\.VIEW"/);
  assert.match(manifest, /android:name="android\.intent\.category\.BROWSABLE"/);
  assert.match(manifest, /android:scheme="sinsan-daon"/);
  assert.match(manifest, /android:host="app"/);
  assert.match(manifest, /android:pathPrefix="\/"/);
});

test("Permission Adapter는 사용 시점 요청·거부·재요청·영구 거부 Settings 경계를 제공한다", async () => {
  const host = await read("apps/mobile/android/app/src/main/java/com/sinsan/daon/DaonAndroidHostModule.kt");
  for (const token of ["CAMERA", "RECORD_AUDIO", "POST_NOTIFICATIONS", "requestPermission", "checkPermission", "shouldShowRequestPermissionRationale", "PERMANENTLY_DENIED", "openApplicationSettings"]) {
    assert.match(host, new RegExp(token));
  }
  assert.doesNotMatch(host, /READ_EXTERNAL_STORAGE|WRITE_EXTERNAL_STORAGE|MANAGE_EXTERNAL_STORAGE|READ_MEDIA_/);
});

test("Lifecycle Adapter는 최소 Route만 복원하고 Credential·Secret·Source 내용을 저장하지 않는다", async () => {
  const host = await read("apps/mobile/android/app/src/main/java/com/sinsan/daon/DaonAndroidHostModule.kt");
  const activity = await read("apps/mobile/android/app/src/main/java/com/sinsan/daon/MainActivity.kt");
  assert.match(host, /saveNavigationRoute/);
  assert.match(host, /restoreNavigationRoute/);
  assert.match(host, /ALLOWED_NATIVE_ROUTES/);
  assert.match(activity, /onNewIntent/);
  assert.match(activity, /onPause|onResume/);
  assert.doesNotMatch(`${host}\n${activity}`, /credential|password|secret|api[_-]?key|source[_-]?content|auth[_-]?token/i);
});

test("Android Host UI는 공용 8 Route·7 State·15 Action을 바꾸지 않고 권한·Lifecycle Adapter만 주입한다", async () => {
  const app = await read("apps/mobile/src/App.tsx");
  const host = await read("apps/mobile/src/platform/android-host.ts");
  const navigation = await readJson("packages/contracts/navigation.json");
  const screens = await readJson("packages/contracts/screens.json");
  const actions = await readJson("packages/contracts/mobile-studio-actions.json");
  assert.equal(navigation.routes.filter((route) => route.clients.includes("android") && route.native_route_key).length, 8);
  assert.equal(new Set(screens.screens.flatMap((screen) => screen.states)).size, 7);
  assert.equal(actions.actions.length, 15);
  assert.match(app, /Platform\.OS === "android"/);
  for (const token of ["requestPermission", "restoreNavigationRoute", "saveNavigationRoute", "AppState", "Linking"] ) assert.match(host, new RegExp(token));
  assert.doesNotMatch(host, /fetch\s*\(|https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/i);
});

test("Release Build는 Debug Keystore로 서명하지 않으며 Keystore·Password를 생성하거나 기록하지 않는다", async () => {
  const appGradle = await read("apps/mobile/android/app/build.gradle");
  const files = await listFiles(androidRoot);
  const relative = files.map((file) => path.relative(androidRoot, file).replaceAll("\\", "/"));
  const releaseBlock = appGradle.match(/release\s*\{([\s\S]*?)\n\s*\}/)?.[1] ?? "";
  assert.doesNotMatch(releaseBlock, /signingConfig\s+signingConfigs\.debug/);
  assert.equal(relative.some((file) => /release.*\.keystore$|upload.*\.jks$|\.p12$/i.test(file)), false);
  assert.doesNotMatch(appGradle, /UPLOAD_STORE|UPLOAD_KEY|releaseKeystore|uploadKeystore/i);
});

test("Android 전용 Gate는 기존 Mobile 표준 다섯 명령과 분리해 Fail-close로 연결된다", async () => {
  const rootPackage = await readJson("package.json");
  const mobilePackage = await readJson("apps/mobile/package.json");
  const policy = await readJson("quality-gate-policy.json");
  const mobile = policy.components.find((component) => component.id === "apps/mobile");
  assert.equal(rootPackage.scripts["verify:android-native"], "node --test scripts/tests/android-native-shell.test.mjs scripts/tests/android-deep-link.test.mjs");
  assert.equal(rootPackage.scripts["build:android-debug"], "node scripts/run-android-gradle.mjs assembleDebug");
  assert.equal(mobilePackage.scripts.android, "npm --prefix ../.. run build:android-debug");
  assert.equal(mobilePackage.scripts.contract, "npm --prefix ../.. run verify:mobile-contract");
  assert.deepEqual(mobile.capabilities.contract.command.command, ["npm", "run", "contract", "--workspace", "@daon-user/mobile"]);
  assert.match(rootPackage.scripts["verify:mobile"], /verify:android-native/);
});

test("Android Source와 Manifest에 내부 API·Provider URL·Secret이 없다", async () => {
  const files = (await listFiles(path.join(androidRoot, "app/src/main/java"))).filter((file) => /\.(?:kt|java)$/.test(file));
  files.push(path.join(androidRoot, "app/src/main/AndroidManifest.xml"));
  const source = (await Promise.all(files.map((file) => readFile(file, "utf8")))).join("\n").replace("http://schemas.android.com/apk/res/android", "");
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|host\.docker\.internal|NEXT_PUBLIC_API_BASE_URL|api[_-]?key\s*[:=]|client[_-]?secret\s*[:=]/i);
});
