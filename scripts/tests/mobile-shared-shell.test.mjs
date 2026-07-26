import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const root = path.resolve(import.meta.dirname, "../..");
const mobileRoot = path.join(root, "apps/mobile");

async function importFresh(relativePath) {
  return import(`${pathToFileURL(path.join(root, relativePath)).href}?t=${Date.now()}-${Math.random()}`);
}

async function readJson(relativePath) {
  return JSON.parse(await readFile(path.join(root, relativePath), "utf8"));
}

async function listSourceFiles(directory) {
  const output = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) output.push(...await listSourceFiles(absolute));
    else if (entry.isFile() && /\.(?:js|jsx|ts|tsx|json)$/.test(entry.name)) output.push(absolute);
  }
  return output;
}

test("Android와 iOS는 Navigation 정본에서 같은 순서의 허용 Route 8개만 투영한다", async () => {
  const navigation = await importFresh("apps/mobile/src/domain/navigation.ts");
  const expected = ["Home", "WorkspaceList", "WorkspaceDetail", "Inbox", "RunHistory", "Notifications", "ModelConnections", "AccountSettings"];
  for (const clientType of ["android", "ios"]) {
    const result = navigation.projectNativeRoutes(clientType);
    assert.equal(result.ok, true);
    assert.deepEqual(result.routes.map((route) => route.nativeRouteKey), expected);
    assert.equal(result.routes.length, 8);
    assert.equal(result.routes.some((route) => ["organization_settings", "operations"].includes(route.routeId)), false);
  }
  assert.deepEqual(navigation.projectNativeRoutes("windows"), { ok: false, code: "UNKNOWN_NATIVE_CLIENT", routes: [] });
});

test("Route 선택·Deep Link·복귀는 허용 native_route_key만 받고 실패 시 현재 Route를 보존한다", async () => {
  const navigation = await importFresh("apps/mobile/src/domain/navigation.ts");
  let state = navigation.createNavigationState("android");
  assert.equal(state.currentNativeRouteKey, "Home");
  state = navigation.selectNativeRoute(state, "WorkspaceDetail");
  assert.deepEqual(state.history, ["Home", "WorkspaceDetail"]);
  const rejected = navigation.selectNativeRoute(state, "Operations");
  assert.equal(rejected.currentNativeRouteKey, "WorkspaceDetail");
  assert.equal(rejected.lastError.code, "NATIVE_ROUTE_NOT_ALLOWED");
  const deepLinkRejected = navigation.acceptNativeDeepLink(rejected, "UnknownRoute");
  assert.equal(deepLinkRejected.currentNativeRouteKey, "WorkspaceDetail");
  assert.equal(deepLinkRejected.lastError.code, "NATIVE_DEEP_LINK_NOT_ALLOWED");
  state = navigation.goBack(deepLinkRejected);
  assert.equal(state.currentNativeRouteKey, "Home");
});

test("Screen은 Contract의 일곱 상태만 허용하고 알 수 없는 상태를 ready로 승격하지 않는다", async () => {
  const screens = await importFresh("apps/mobile/src/domain/screens.ts");
  const expected = ["loading", "empty", "ready", "warning", "error", "forbidden", "unavailable"];
  assert.deepEqual(screens.getAllowedScreenStates("home"), expected);
  assert.equal(screens.normalizeScreenState("ready").state, "ready");
  assert.deepEqual(screens.normalizeScreenState("mystery"), { state: "error", code: "UNKNOWN_SCREEN_STATE" });
  assert.deepEqual(screens.projectScreen("android", "Operations"), { ok: false, code: "NATIVE_ROUTE_NOT_ALLOWED" });
});

test("PublicApiClient 기본값과 잘못된 Adapter 결과는 성공을 가장하지 않는다", async () => {
  const client = await importFresh("apps/mobile/src/domain/public-api-client.ts");
  const unavailable = await client.createUnavailablePublicApiClient().loadScreen({ clientType: "ios", routeId: "home" });
  assert.equal(unavailable.ok, false);
  assert.deepEqual(unavailable.error, {
    code: "NATIVE_PUBLIC_API_UNAVAILABLE",
    screenState: "unavailable",
    replacementOwner: "R1-M4-01"
  });
  const invalid = client.normalizePublicApiResult({ ok: true, data: { state: "unexpected", title: "허위 성공" } });
  assert.equal(invalid.ok, false);
  assert.equal(invalid.error.code, "NATIVE_ADAPTER_RESULT_INVALID");
  assert.notEqual(invalid.error.screenState, "ready");
});

test("React Native Style 값은 Design Token 정본의 px와 색상에서만 변환된다", async () => {
  const tokens = await importFresh("apps/mobile/src/platform/design-token-adapter.ts");
  assert.deepEqual(tokens.mobileTokens.typography, { body: 12, form: 12, description: 10, auxiliary: 9, sidebarTitle: 14, screenTitle: 16 });
  assert.equal(tokens.mobileTokens.targetSize.touchControl, 44);
  assert.equal(tokens.mobileTokens.color.action, "#2563EB");
  assert.equal(tokens.mobileTokens.status.colorOnlyForbidden, true);
  assert.throws(() => tokens.toDeviceIndependentPixels("1rem"), /UNSUPPORTED_DESIGN_TOKEN_LENGTH/);
});

test("모바일 Studio 15개 Matrix는 M2 Domain 정본에서 생성된 플랫폼 중립 계약과 일치한다", async () => {
  const generated = await readJson("packages/contracts/mobile-studio-actions.json");
  const m2 = await importFresh("packages/ui/src/studio-workflow-model.js");
  const mobile = await importFresh("apps/mobile/src/domain/mobile-studio-actions.ts");
  assert.equal(generated.schema_version, "1.0");
  assert.equal(generated.generated_from, "packages/ui/src/studio-workflow-model.js");
  assert.deepEqual(generated.actions.map((item) => item.action), m2.MOBILE_STUDIO_ACTIONS);
  assert.equal(generated.actions.length, 15);
  for (const item of generated.actions) {
    assert.deepEqual(item.decision, m2.evaluateMobileAction(item.action));
    assert.deepEqual(mobile.evaluateMobileStudioAction(item.action), item.decision);
  }
  assert.equal(mobile.evaluateMobileStudioAction("unknown").code, "MOBILE_STUDIO_ACTION_UNKNOWN");
});

test("Mobile Production Source는 DOM UI·Browser API·내부 주소·Secret과 플랫폼별 화면 복제를 포함하지 않는다", async () => {
  const sourceRoot = path.join(mobileRoot, "src");
  const files = await listSourceFiles(sourceRoot);
  assert.ok(files.length >= 6, "React Native 공용 Source가 필요하다");
  const source = (await Promise.all(files.map((file) => readFile(file, "utf8")))).join("\n");
  assert.doesNotMatch(source, /packages[\\/]ui|@daon-user\/ui|react-dom|next\/|document\.|window\.|localStorage|sessionStorage|fetch\s*\(|NEXT_PUBLIC_API_BASE_URL/i);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|host\.docker\.internal|api[_-]?key\s*[:=]|client[_-]?secret\s*[:=]/i);
  assert.equal(files.some((file) => /[\\/](?:android|ios)[\\/]/i.test(file)), false);
});

test("공용 Shell Source는 RN 기본 Component·접근성·44px·선택·비색상 상태 신호를 명시한다", async () => {
  const source = await readFile(path.join(mobileRoot, "src/MobileShell.tsx"), "utf8");
  for (const component of ["SafeAreaView", "ScrollView", "View", "Text", "Pressable", "StyleSheet"]) assert.match(source, new RegExp(`\\b${component}\\b`));
  for (const contract of ["accessibilityLabel", "accessibilityRole", "accessibilityState", "allowFontScaling", "touchControl", "statusSignal", "InfoActionAdapter", "unavailable"]) assert.match(source, new RegExp(contract));
  assert.doesNotMatch(source, /from\s+["'](?:@daon-user\/ui|react-dom|next\/)/);
});

test("Mobile Workspace 표준 다섯 명령은 Root 검증을 Shell 비종속으로 호출하고 Gate가 직접 실행한다", async () => {
  const mobilePackage = await readJson("apps/mobile/package.json");
  const policy = await readJson("quality-gate-policy.json");
  const mobile = policy.components.find((component) => component.id === "apps/mobile");
  const commands = {
    lint: "verify:mobile-lint",
    type: "verify:mobile-type",
    unit: "verify:mobile-unit",
    contract: "verify:mobile-contract",
    build: "verify:mobile-build"
  };
  for (const [name, rootScript] of Object.entries(commands)) {
    assert.equal(mobilePackage.scripts[name], `npm --prefix ../.. run ${rootScript}`);
    assert.doesNotMatch(mobilePackage.scripts[name], /--workspace\s+@daon-user\/root/);
    assert.deepEqual(mobile.capabilities[name].command.command, ["npm", "run", name, "--workspace", "@daon-user/mobile"]);
  }
});
