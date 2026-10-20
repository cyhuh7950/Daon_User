import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { readFileSync } from "node:fs";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(import.meta.dirname, "../..");
const generator = path.join(root, "scripts/generate-android-assetlinks.mjs");
const fingerprint = Array.from({ length: 32 }, (_, index) => index.toString(16).padStart(2, "0").toUpperCase()).join(":");

test("Asset Links는 공개 package·SHA-256 인증서 지문만으로 로컬 JSON을 생성한다", () => {
  const result = spawnSync(process.execPath, [generator, "--package", "com.sinsan.daon", "--sha256-cert", fingerprint],
    { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), [{
    relation: ["delegate_permission/common.handle_all_urls"],
    target: { namespace: "android_app", package_name: "com.sinsan.daon", sha256_cert_fingerprints: [fingerprint] },
  }]);
});

test("Asset Links는 임의 package·불완전 지문을 게시 가능한 결과로 승격하지 않는다", () => {
  for (const args of [
    ["--package", "com.other.app", "--sha256-cert", fingerprint],
    ["--package", "com.sinsan.daon", "--sha256-cert", "AA:BB"],
  ]) {
    const result = spawnSync(process.execPath, [generator, ...args], { cwd: root, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /ANDROID_ASSETLINKS_INPUT_INVALID/u);
  }
});

test("Android 인증 App Link는 별도 네이티브 Activity에서만 받고 JS에 비밀을 전달하지 않는다", () => {
  const manifest = readFileSync(path.join(root, "apps/mobile/android/app/src/main/AndroidManifest.xml"), "utf8");
  const native = readFileSync(path.join(root, "apps/mobile/android/app/src/main/java/com/sinsan/daon/DaonAndroidAuthModule.kt"), "utf8");
  const app = readFileSync(path.join(root, "apps/mobile/src/App.tsx"), "utf8");
  const screen = readFileSync(path.join(root, "apps/mobile/src/AndroidAuthScreen.tsx"), "utf8");
  const adapter = readFileSync(path.join(root, "apps/mobile/src/android-auth-adapter.ts"), "utf8");
  assert.match(manifest, /DaonAndroidAuthCallbackActivity/u);
  assert.match(manifest, /android:autoVerify="true"/u);
  assert.match(native, /DOMAIN_STATE_VERIFIED/u);
  assert.match(native, /AndroidKeyStore/u);
  assert.match(native, /CustomTabsClient\.getPackageName/u);
  assert.match(native, /Intent\.CATEGORY_APP_BROWSER/u);
  assert.match(native, /Intent\.ACTION_VIEW/u);
  assert.match(native, /tabs\.intent\.setPackage\(browserPackage\)/u);
  assert.match(native, /@ReactMethod fun beginLogin/u);
  assert.match(native, /@ReactMethod fun consumeAuthorizationLink/u);
  assert.match(native, /@ReactMethod fun getSessionState/u);
  assert.match(native, /@ReactMethod fun logout/u);
  assert.doesNotMatch(native, /promise\.resolve\((?:code|verifier|access|refresh)\)/u);
  assert.match(app, /AndroidAuthScreen/u);
  assert.match(native, /AndroidAuthFlow\.status\(false,/u);
  assert.match(screen, /createAndroidAuthRestorer/u);
  assert.match(screen, /workspace_unavailable[\s\S]*androidAuth\.getSessionState\(\)/u);
  const unavailableUi = screen.split('{state?.status === "workspace_unavailable" && <>')[1]?.split('</>}')[0];
  assert.ok(unavailableUi);
  assert.match(unavailableUi, /onPress=\{\(\) => void leave\(\)\}/u);
  assert.match(adapter, /consumeAuthorizationLink\(""\)/u);
  assert.doesNotMatch(`${screen}\n${adapter}`, /access_credential|refresh_credential|code_verifier|fetch\(/u);
});

test("초기 복구와 AppState 복귀가 겹쳐도 callback은 한 번만 소비하고 성공 상태를 유지한다", async () => {
  const { createAndroidAuthRestorer } = await import(pathToFileURL(
    path.join(root, "apps/mobile/src/android-auth-restorer.ts")
  ).href);
  let finishConsume;
  const consume = new Promise((resolve) => { finishConsume = resolve; });
  let stateReads = 0;
  let exchanges = 0;
  const states = [];
  const errors = [];
  const restorer = createAndroidAuthRestorer({
    getSessionState: async () => { stateReads += 1; return { status: "callback_ready" }; },
    consumePendingAuthorization: async () => { exchanges += 1; return consume; },
  }, (state) => states.push(state), (error) => errors.push(error));
  const first = restorer.restore();
  const second = restorer.restore();
  await Promise.resolve();
  assert.equal(stateReads, 1);
  assert.equal(exchanges, 1);
  finishConsume({ status: "authenticated", workspaces: [] });
  await Promise.all([first, second]);
  assert.equal(states.at(-1).status, "authenticated");
  assert.deepEqual(errors, [false]);
  restorer.dispose();
});

test("인증된 Android만 공용 Shell에 진입하고 Workspace 경로만 Native 조회를 사용한다", async () => {
  const { canEnterAndroidShell, createAndroidWorkspacePublicApiClient } = await import(
    pathToFileURL(path.join(root, "apps/mobile/src/android-workspace-client.ts")).href
  );
  assert.equal(canEnterAndroidShell({ status: "authenticated", workspaces: [] }), true);
  assert.equal(canEnterAndroidShell({ status: "workspace_unavailable" }), false);
  assert.equal(canEnterAndroidShell({ status: "signed_out" }), false);
  let calls = 0;
  const client = createAndroidWorkspacePublicApiClient(async () => {
    calls += 1;
    return { status: "authenticated", workspaces: [
      { tenant_id: "t-1", workspace_id: "w-1", display_name: "합성 Workspace" },
    ] };
  });
  assert.deepEqual(await client.loadScreen({ clientType: "android", routeId: "home" }), {
    ok: false, error: { code: "NATIVE_PUBLIC_API_UNAVAILABLE", screenState: "unavailable", replacementOwner: "R1-M4-01" },
  });
  assert.equal(calls, 0);
  assert.deepEqual(await client.loadScreen({ clientType: "android", routeId: "workspace_list" }), {
    ok: true, data: { state: "ready", title: "WorkspaceList", message: "합성 Workspace" },
  });
  assert.equal(calls, 1);
  const app = readFileSync(path.join(root, "apps/mobile/src/App.tsx"), "utf8");
  assert.match(app, /authenticatedContent=\{<MobileShell/u);
  assert.match(app, /publicApiClient=\{androidWorkspaceClient\}/u);
});

test("저장된 공용 Navigation과 무관하게 인증 직후 Workspace 표시명을 안전하게 보여준다", async () => {
  const { workspaceSummary } = await import(pathToFileURL(
    path.join(root, "apps/mobile/src/android-workspace-client.ts")
  ).href);
  assert.equal(workspaceSummary({ status: "authenticated", tenant_id: "t-1", workspace_id: "w-1", workspaces: [
    { tenant_id: "t-1", workspace_id: "w-1", display_name: "합성 Workspace" },
  ] }), "합성 Workspace");
  assert.equal(workspaceSummary({ status: "signed_out" }), null);
  const screen = readFileSync(path.join(root, "apps/mobile/src/AndroidAuthScreen.tsx"), "utf8");
  assert.match(screen, /workspaceSummary\(state\)/u);
});

test("Android Workspace 전환은 Native 세션 회전과 재조회 뒤에만 선택 상태를 표시한다", async () => {
  const native = readFileSync(path.join(root, "apps/mobile/android/app/src/main/java/com/sinsan/daon/DaonAndroidAuthModule.kt"), "utf8");
  const adapter = readFileSync(path.join(root, "apps/mobile/src/android-auth-adapter.ts"), "utf8");
  const screen = readFileSync(path.join(root, "apps/mobile/src/AndroidAuthScreen.tsx"), "utf8");
  assert.match(native, /@ReactMethod fun switchWorkspace/u);
  assert.match(native, /request\("\/api\/v1\/session\/tenant", JSONObject\(\)\.put\("tenant_id", tenantId\), session\.access\)/u);
  assert.match(native, /if \(it\.sessionId != session\.sessionId\) store\(it\)/u);
  assert.match(native, /workspaces\(next\)/u);
  assert.match(adapter, /switchWorkspace: \(tenantId: string\)/u);
  assert.match(screen, /androidAuth\.switchWorkspace\(tenantId\)/u);
  assert.match(screen, /setState\(null\);[\s\S]*androidAuth\.switchWorkspace\(tenantId\)/u);
  assert.match(screen, /catch \{ setState\(\{ status: "workspace_unavailable" \}\); \}/u);
  assert.match(screen, /접근 가능한 소속별 기본 Workspace/u);
  assert.match(screen, /state\?\.session_id/u);
  assert.doesNotMatch(`${screen}\n${adapter}`, /access_credential|refresh_credential|code_verifier|fetch\(/u);
});

test("Workspace 오류 화면의 로그아웃 실패 안내와 signed_out 로그인 실패 안내를 구분한다", () => {
  const screen = readFileSync(path.join(root, "apps/mobile/src/AndroidAuthScreen.tsx"), "utf8");
  assert.match(screen, /catch \{ setLogoutFailed\(true\); setError\(true\); \}/u);
  assert.match(screen, /error && <Text accessibilityRole="alert">\{logoutFailed \? "로그아웃을 완료할 수 없습니다\. 연결을 확인한 뒤 다시 시도해 주세요\." : "인증을 완료할 수 없습니다\. 새 거래로 다시 시도해 주세요\."\}<\/Text>/u);
  assert.match(screen, /state\?\.status === "signed_out"/u);
});
