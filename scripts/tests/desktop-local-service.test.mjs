import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (relativePath) => readFile(new URL(`../../${relativePath}`, import.meta.url), "utf8");

test("browser bridge exposes only fixed Tauri commands and no loopback details", async () => {
  const bridge = await import("../../apps/desktop/src/local-service-bridge.js");
  const calls = [];
  const invoke = async (command) => {
    calls.push(command);
    return { state: "ready", retryable: false, error_code: null };
  };

  assert.deepEqual(await bridge.readLocalServiceStatus(invoke), {
    state: "ready",
    retryable: false,
    error_code: null
  });
  await bridge.retryLocalService(invoke);
  assert.deepEqual(calls, ["local_service_status", "local_service_retry"]);

  const source = await read("apps/desktop/src/local-service-bridge.js");
  assert.doesNotMatch(source, /localhost|127\.0\.0\.1|https?:\/\/|\btoken\b|\bport\b/i);
  assert.doesNotMatch(source, /shell|process|Command/);
});

test("non-Tauri browser reports unavailable without fabricating ready", async () => {
  const bridge = await import("../../apps/desktop/src/local-service-bridge.js");
  assert.deepEqual(await bridge.readLocalServiceStatus(), {
    state: "unavailable",
    retryable: false,
    error_code: "NOT_TAURI_RUNTIME"
  });
});

test("desktop bridge continuously polls status until its subscriber is disposed", async () => {
  const bridge = await import("../../apps/desktop/src/local-service-bridge.js");
  const statuses = [
    { state: "starting", retryable: false, error_code: null },
    { state: "ready", retryable: false, error_code: null }
  ];
  const observed = [];
  const scheduled = [];
  const cancelled = [];
  const dispose = bridge.watchLocalServiceStatus(
    (status) => observed.push(status),
    {
      invoke: async () => statuses.shift(),
      schedule: (callback, delay) => {
        scheduled.push({ callback, delay });
        return scheduled.length;
      },
      cancel: (timer) => cancelled.push(timer),
      intervalMs: 25
    }
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(observed.map(({ state }) => state), ["starting"]);
  assert.equal(scheduled[0].delay, 25);

  scheduled[0].callback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(observed.map(({ state }) => state), ["starting", "ready"]);
  dispose();
  assert.deepEqual(cancelled, [2]);
});

test("desktop bridge maps runtime error codes to stable user-visible Korean text", async () => {
  const bridge = await import("../../apps/desktop/src/local-service-bridge.js");
  assert.equal(
    bridge.describeLocalServiceState({
      state: "unavailable",
      retryable: true,
      error_code: "LOCAL_HEALTH_FAILED"
    }),
    "로컬 서비스 상태를 확인할 수 없습니다."
  );
  assert.equal(
    bridge.describeLocalServiceState({
      state: "unavailable",
      retryable: true,
      error_code: "UNRECOGNIZED_CODE"
    }),
    "로컬 서비스를 사용할 수 없습니다."
  );
  assert.equal(
    bridge.describeLocalServiceState({
      state: "retrying",
      retryable: false,
      error_code: null
    }),
    "로컬 서비스를 다시 시작하고 있습니다."
  );
});

test("desktop shell subscribes continuously and exposes mapped runtime text", async () => {
  const source = await read("apps/desktop/src/desktop-shell.jsx");
  assert.match(source, /watchLocalServiceStatus/);
  assert.match(source, /describeLocalServiceState/);
  assert.doesNotMatch(source, /readLocalServiceStatus\(\)\.then/);
});

test("desktop source keeps WebView network fail-closed", async () => {
  const config = JSON.parse(await read("apps/desktop/src-tauri/tauri.conf.json"));
  const capability = JSON.parse(
    await read("apps/desktop/src-tauri/capabilities/desktop-main.json")
  );
  assert.match(config.app.security.csp, /connect-src 'none'/);
  assert.deepEqual(capability.permissions, []);
  assert.doesNotMatch(JSON.stringify(capability.permissions), /shell|http|process|fs:/i);
});

test("Rust contract tests use the guarded isolated Cargo wrapper", async () => {
  const root = JSON.parse(await read("package.json"));
  const wrapper = await read("scripts/run-isolated-desktop-cargo.mjs");
  assert.equal(
    root.scripts["verify:desktop-rust-unit"],
    "node scripts/run-isolated-desktop-cargo.mjs test"
  );
  assert.match(wrapper, /mode === "test"/);
  assert.match(wrapper, /"--lib"/);
  assert.match(wrapper, /--test", "local_service_contract"/);
});

test("headless Rust lifecycle host is run only through the fixed guarded wrapper mode", async () => {
  const host = await read(
    "apps/desktop/src-tauri/src/bin/local-service-lifecycle-host.rs"
  );
  const wrapper = await read("scripts/run-isolated-desktop-cargo.mjs");
  assert.match(wrapper, /mode === "manager-runtime"/);
  assert.match(wrapper, /"--bin",\s+"local-service-lifecycle-host"/);
  assert.match(wrapper, /DAON_LOCAL_SERVICE_SIDECAR/);
  assert.match(host, /LocalServiceManager::with_sidecar_path/);
  assert.match(host, /retry_async/);
  assert.match(host, /shutdown/);
  assert.doesNotMatch(host, /token|app_instance_id|port/i);
});

test("Windows package declares a generated target-triple sidecar and cleanup", async () => {
  const root = JSON.parse(await read("package.json"));
  const desktop = JSON.parse(await read("apps/desktop/package.json"));
  const windowsConfig = JSON.parse(
    await read("apps/desktop/src-tauri/tauri.windows.conf.json")
  );
  const buildScript = await read("scripts/build-local-service-sidecar.mjs");
  const wrapper = await read("scripts/run-isolated-desktop-cargo.mjs");
  const ignore = await read(".gitignore");

  assert.equal(
    root.scripts["build:local-service-sidecar"],
    "node scripts/build-local-service-sidecar.mjs"
  );
  assert.equal(desktop.scripts["sidecar:build"], "node ../../scripts/build-local-service-sidecar.mjs");
  assert.equal(desktop.scripts["tauri:build"], "npm run sidecar:build && tauri build --bundles nsis");
  assert.deepEqual(windowsConfig.bundle.externalBin, ["binaries/daon-user-local-service"]);
  assert.match(buildScript, /x86_64-pc-windows-msvc/);
  assert.match(buildScript, /pyinstaller/);
  assert.match(wrapper, /cleanupGeneratedSidecar/);
  assert.match(wrapper, /refusing to run while the generated sidecar already exists/);
  assert.match(ignore, /apps\/desktop\/src-tauri\/binaries\//);
});

test("quality gate activates all Local Service source capabilities", async () => {
  const policy = JSON.parse(await read("quality-gate-policy.json"));
  const component = policy.components.find(({ id }) => id === "services/local-service");
  for (const category of ["lint", "type", "unit", "contract", "build"]) {
    assert.deepEqual(
      component.capabilities[category].command.command,
      ["node", "scripts/run-local-service-tool.mjs", category]
    );
  }
  const tool = await read("scripts/run-local-service-tool.mjs");
  assert.match(tool, /UV_CACHE_DIR/);
  assert.match(tool, /pip_audit/);
});
