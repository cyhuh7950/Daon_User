import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { access, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");
const readBinary = (path) => readFile(new URL(`../../${path}`, import.meta.url));

test("desktop shell directly consumes shared UI, tokens, and contracts", async () => {
  const source = await read("apps/desktop/src/desktop-shell.jsx");
  assert.match(source, /@daon-user\/ui/);
  assert.match(source, /@daon-user\/contracts\/navigation\.json/);
  assert.match(source, /@daon-user\/contracts\/screens\.json/);
  assert.match(source, /@daon-user\/design-tokens\/tokens\.css/);
  assert.doesNotMatch(source, /apps\/web|next\/|NEXT_PUBLIC_/);
});

test("native navigation exposes only approved Windows routes by stable key", async () => {
  const { createWindowsNavigation } = await import("../../apps/desktop/src/desktop-shell-model.js");
  const navigation = JSON.parse(await read("packages/contracts/navigation.json"));
  const routes = createWindowsNavigation(navigation.routes);
  assert.ok(routes.length > 0);
  assert.ok(routes.every((route) => route.clients.includes("windows")));
  assert.ok(routes.every((route) => route.key === route.native_route_key));
  assert.deepEqual(
    ["Home", "WorkspaceDetail", "AccountSettings", "OrganizationSettings", "Operations", "Notifications"].filter((key) => routes.some((route) => route.key === key)),
    ["Home", "WorkspaceDetail", "AccountSettings", "OrganizationSettings", "Operations", "Notifications"]
  );
  assert.ok(routes.every((route) => !("capabilities" in route)));
});

test("500px desktop navigation wraps all routes without horizontal scrolling", async () => {
  const css = await read("apps/desktop/src/desktop-shell.css");
  const compactRule = css.match(/@media \(max-width: 600px\) \{([\s\S]*?)\n\}/)?.[1] ?? "";
  assert.match(compactRule, /\.desktop-navigation\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(compactRule, /\.desktop-navigation\s*\{[^}]*overflow-x:\s*(?:clip|hidden)/);
  assert.match(compactRule, /\.desktop-navigation button\s*\{[^}]*flex:\s*1 1 auto/);
});

test("production Tauri configuration is bundled and WebView remains fail-closed", async () => {
  const cargoManifest = await read("apps/desktop/src-tauri/Cargo.toml");
  assert.match(cargoManifest, /^default-run\s*=\s*"daon-user-desktop"$/m);

  const config = JSON.parse(await read("apps/desktop/src-tauri/tauri.conf.json"));
  assert.equal(config.build.frontendDist, "../dist");
  assert.equal("devUrl" in config.build, false);
  assert.equal(config.bundle.targets, "nsis");
  assert.deepEqual(config.bundle.icon, ["icons/icon.ico", "icons/icon.png"]);
  assert.equal(config.app.security.capabilities[0], "desktop-main");
  assert.match(config.app.security.csp, /connect-src 'none'/);
  assert.doesNotMatch(config.app.security.csp, /unsafe-eval|\*/);

  const capability = JSON.parse(await read("apps/desktop/src-tauri/capabilities/desktop-main.json"));
  assert.deepEqual(capability.windows, ["main"]);
  assert.deepEqual(capability.permissions, []);

  const rust = await read("apps/desktop/src-tauri/src/lib.rs");
  assert.match(rust, /local_service_status/);
  assert.match(rust, /local_service_retry/);
  assert.match(rust, /native_login/);
  assert.match(rust, /native_logout/);
  assert.match(rust, /native_session_status/);
  assert.match(rust, /generate_handler!/);
  assert.doesNotMatch(rust, /tauri_plugin_shell|plugin\(/);
  assert.deepEqual(await readdir(new URL("../../apps/desktop/src-tauri/icons/", import.meta.url)), ["icon.ico", "icon.png"]);
  await assert.rejects(access(new URL("../../apps/desktop/src-tauri/gen/", import.meta.url)));
});

test("native session commands retain a fixed HTTPS gateway and never expose credentials to the WebView", async () => {
  const rust = await read("apps/desktop/src-tauri/src/native_session.rs");
  const manifest = await read("apps/desktop/src-tauri/Cargo.toml");
  assert.match(manifest, /reqwest\s*=\s*\{\s*version\s*=\s*"=0\.13\.4",\s*default-features\s*=\s*false,\s*features\s*=\s*\["json",\s*"rustls"\]\s*\}/);
  assert.match(rust, /DaonUser\/NativeSession\/v1/);
  assert.match(rust, /https:\/\/daon-user\.sinsan\.kr/);
  assert.match(rust, /\/api\/v1\/auth\/native\/login/);
  assert.match(rust, /\/api\/v1\/session\/refresh/);
  assert.doesNotMatch(rust, /std::env|NEXT_PUBLIC_|localhost|127\.0\.0\.1|Authorization.*Debug/u);
});

test("desktop bundle includes valid Windows ICO and cross-platform square RGBA PNG", async () => {
  const ico = await readBinary("apps/desktop/src-tauri/icons/icon.ico");
  const png = await readBinary("apps/desktop/src-tauri/icons/icon.png");

  assert.deepEqual([...ico.subarray(0, 4)], [0x00, 0x00, 0x01, 0x00]);
  assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.equal(png.subarray(12, 16).toString("ascii"), "IHDR");

  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  assert.equal(width, height);
  assert.equal(width, 256);
  assert.equal(png[24], 8);
  assert.equal(png[25], 6);

  const iconCount = ico.readUInt16LE(4);
  const sourceFrame = Array.from({ length: iconCount }, (_, index) => 6 + index * 16)
    .map((offset) => ({
      width: ico[offset] || 256,
      height: ico[offset + 1] || 256,
      bytes: ico.readUInt32LE(offset + 8),
      dataOffset: ico.readUInt32LE(offset + 12)
    }))
    .find((entry) => entry.width === 256 && entry.height === 256);
  assert.ok(sourceFrame);
  assert.equal(ico.subarray(sourceFrame.dataOffset, sourceFrame.dataOffset + sourceFrame.bytes).equals(png), true);
});

test("desktop package pins production build and Tauri commands", async () => {
  const pkg = JSON.parse(await read("apps/desktop/package.json"));
  assert.equal(pkg.devDependencies["@tauri-apps/cli"], "2.11.4");
  assert.match(pkg.devDependencies.vite, /^\d+\.\d+\.\d+$/);
  assert.equal(pkg.scripts.build, "vite build");
  assert.equal(pkg.scripts["sidecar:build"], "node ../../scripts/build-local-service-sidecar.mjs");
  assert.equal(pkg.scripts["tauri:build"], "npm run sidecar:build && tauri build --bundles nsis");
});

test("quality gate registers only reproducible desktop runtime capabilities", async () => {
  const root = JSON.parse(await read("package.json"));
  const npmConfig = await read(".npmrc");
  const policy = JSON.parse(await read("quality-gate-policy.json"));
  const desktop = policy.components.find((component) => component.id === "apps/desktop");
  assert.deepEqual(desktop.capabilities.lint.command.command, ["npm", "run", "verify:desktop-lint"]);
  assert.deepEqual(desktop.capabilities.type.command.command, ["npm", "run", "verify:desktop-type"]);
  assert.deepEqual(desktop.capabilities.unit.command.command, ["npm", "run", "verify:desktop-unit"]);
  assert.deepEqual(desktop.capabilities.build.command.command, ["npm", "run", "verify:desktop-build"]);
  for (const script of ["verify:desktop-lint", "verify:desktop-type", "verify:desktop-unit", "verify:desktop-build"]) {
    assert.equal(typeof root.scripts[script], "string");
  }
  assert.equal("preverify:desktop-type" in root.scripts, false);
  assert.equal(root.scripts["verify:desktop-type"], "npm run verify:desktop-build && node scripts/run-isolated-desktop-cargo.mjs check");
  assert.match(npmConfig, /^ignore-scripts=true$/m);
  assert.equal(root.scripts["build:desktop-installer"], "node scripts/run-isolated-desktop-cargo.mjs installer");
});

test("isolated cargo wrapper propagates failure and removes only its exact target", async () => {
  const wrapperSource = await read("scripts/run-isolated-desktop-cargo.mjs");
  assert.match(wrapperSource, /npm_execpath/);
  assert.doesNotMatch(wrapperSource, /npm\.cmd/);
  const { runIsolatedCargo } = await import("../run-isolated-desktop-cargo.mjs");
  const parent = path.join(os.tmpdir(), "daon-user-wrapper-test-");
  let observedTarget = "";
  const result = await runIsolatedCargo({
    prefix: parent,
    keepOnSuccess: false,
    command: "cargo",
    args: ["check"],
    spawnImpl: (_command, _args, options) => {
      observedTarget = options.env.CARGO_TARGET_DIR;
      return { status: 23, signal: null };
    }
  });
  assert.equal(result.exitCode, 23);
  await assert.rejects(access(observedTarget));
  assert.ok(path.dirname(observedTarget).startsWith(os.tmpdir()));
});

test("non-installer Cargo can override only the generated bundle input contract", async () => {
  const wrapperSource = await read("scripts/run-isolated-desktop-cargo.mjs");
  const { runIsolatedCargo } = await import("../run-isolated-desktop-cargo.mjs");
  const tauriConfig = JSON.stringify({ bundle: { externalBin: [] } });
  let observedEnvironment;
  const result = await runIsolatedCargo({
    prefix: path.join(os.tmpdir(), "daon-user-wrapper-env-test-"),
    keepOnSuccess: false,
    command: "cargo",
    args: ["check"],
    envOverrides: { TAURI_CONFIG: tauriConfig },
    spawnImpl: (_command, _args, options) => {
      observedEnvironment = options.env;
      return { status: 0, signal: null };
    }
  });
  assert.equal(result.exitCode, 0);
  assert.equal(observedEnvironment.TAURI_CONFIG, tauriConfig);
  assert.match(wrapperSource, /isInstaller\s*\?\s*\{\}\s*:\s*\{\s*TAURI_CONFIG/u);
  assert.match(wrapperSource, /externalBin:\s*\[\]/u);
});

test("isolated cargo wrapper removes only gen created by its child and preserves pre-existing sentinels", async () => {
  const { runDesktopCargoSafely } = await import("../run-isolated-desktop-cargo.mjs");
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-user-wrapper-gen-fixture-"));
  const generatedDir = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "gen");
  const adjacentSentinel = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "adjacent.bin");
  const targetParent = path.join(fixtureRoot, "targets");
  const otherWorktreeGen = path.join(fixtureRoot, "other-worktree", "apps", "desktop", "src-tauri", "gen");
  const otherSentinel = path.join(otherWorktreeGen, "sentinel.bin");
  await mkdir(targetParent, { recursive: true });
  await mkdir(otherWorktreeGen, { recursive: true });
  await mkdir(path.dirname(adjacentSentinel), { recursive: true });
  await writeFile(adjacentSentinel, Buffer.from([0x11, 0x22, 0x33]));
  await writeFile(otherSentinel, Buffer.from([0x00, 0x23, 0xff, 0x41]));
  const adjacentHash = createHash("sha256").update(await readFile(adjacentSentinel)).digest("hex");
  const otherHash = createHash("sha256").update(await readFile(otherSentinel)).digest("hex");

  const runGeneratedScenario = async (status) => runDesktopCargoSafely({
    generatedDir,
    prefix: path.join(targetParent, "daon-user-desktop-check-"),
    keepOnSuccess: false,
    command: "fixture-cargo",
    args: [],
    spawnImpl: () => {
      mkdirSync(path.join(generatedDir, "schemas"), { recursive: true });
      writeFileSync(path.join(generatedDir, "schemas", "generated.json"), `status=${status}`);
      return { status, signal: null };
    }
  });

  try {
    const success = await runGeneratedScenario(0);
    assert.equal(success.exitCode, 0);
    await assert.rejects(access(generatedDir));
    assert.deepEqual(await readdir(targetParent), []);

    const failure = await runGeneratedScenario(23);
    assert.equal(failure.exitCode, 23);
    await assert.rejects(access(generatedDir));
    assert.deepEqual(await readdir(targetParent), []);

    const spawnError = await runDesktopCargoSafely({
      generatedDir,
      prefix: path.join(targetParent, "daon-user-desktop-check-"),
      keepOnSuccess: false,
      command: "missing-fixture-cargo",
      args: [],
      spawnImpl: () => {
        const error = new Error("fixture spawn error");
        error.code = "ENOENT";
        throw error;
      }
    });
    assert.equal(spawnError.exitCode, 2);
    await assert.rejects(access(generatedDir));
    assert.deepEqual(await readdir(targetParent), []);

    await mkdir(generatedDir, { recursive: true });
    const sentinel = path.join(generatedDir, "sentinel.bin");
    const sentinelBytes = Buffer.from([0xde, 0xad, 0x00, 0xbe, 0xef]);
    await writeFile(sentinel, sentinelBytes);
    const sentinelHash = createHash("sha256").update(sentinelBytes).digest("hex");
    let childCalls = 0;
    const preexisting = await runDesktopCargoSafely({
      generatedDir,
      prefix: path.join(targetParent, "daon-user-desktop-check-"),
      keepOnSuccess: false,
      command: "must-not-run",
      args: [],
      spawnImpl: () => {
        childCalls += 1;
        return { status: 0, signal: null };
      }
    });
    assert.equal(preexisting.exitCode, 2);
    assert.equal(preexisting.preexistingGeneratedDir, true);
    assert.equal(childCalls, 0);
    assert.equal(createHash("sha256").update(await readFile(sentinel)).digest("hex"), sentinelHash);
    assert.deepEqual(await readdir(targetParent), []);
    assert.equal(createHash("sha256").update(await readFile(adjacentSentinel)).digest("hex"), adjacentHash);
    assert.equal(createHash("sha256").update(await readFile(otherSentinel)).digest("hex"), otherHash);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("desktop cargo wrapper fails closed when gen state cannot be probed", async () => {
  const { runDesktopCargoSafely } = await import("../run-isolated-desktop-cargo.mjs");
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "daon-user-wrapper-probe-fixture-"));
  const generatedDir = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "gen");
  const adjacentSentinel = path.join(fixtureRoot, "repo", "apps", "desktop", "src-tauri", "adjacent.bin");
  const targetParent = path.join(fixtureRoot, "targets");
  await mkdir(path.dirname(adjacentSentinel), { recursive: true });
  await mkdir(targetParent, { recursive: true });
  const sentinelBytes = Buffer.from([0xe1, 0xac, 0xce, 0x55]);
  await writeFile(adjacentSentinel, sentinelBytes);
  const sentinelHash = createHash("sha256").update(sentinelBytes).digest("hex");

  try {
    for (const code of ["EACCES", "EIO"]) {
      let childCalls = 0;
      const result = await runDesktopCargoSafely({
        generatedDir,
        prefix: path.join(targetParent, "daon-user-desktop-check-"),
        keepOnSuccess: false,
        command: "must-not-run",
        args: [],
        probePathImpl: async () => {
          const error = new Error(`fixture ${code}`);
          error.code = code;
          throw error;
        },
        spawnImpl: () => {
          childCalls += 1;
          return { status: 0, signal: null };
        }
      });
      assert.equal(result.exitCode, 2);
      assert.equal(result.childStarted, false);
      assert.equal(result.targetDir, null);
      assert.equal(result.preexistingGeneratedDir, null);
      assert.equal(result.error.code, code);
      assert.equal(childCalls, 0);
      assert.deepEqual(await readdir(targetParent), []);
      await assert.rejects(access(generatedDir));
      assert.equal(createHash("sha256").update(await readFile(adjacentSentinel)).digest("hex"), sentinelHash);
    }
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("PostCSS 보정 이력은 고정 Successor Blob으로, 현재 Checkout은 핵심 Pin으로 검증한다", async () => {
  const root = JSON.parse(await read("package.json"));
  const lock = JSON.parse(await read("package-lock.json"));
  const successorCommit = "8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa";
  const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));
  const readSuccessorJson = (artifactPath) => {
    const result = spawnSync("git", ["show", `${successorCommit}:${artifactPath}`], { cwd: repositoryRoot, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  };
  const successorRoot = readSuccessorJson("package.json");
  const successorLock = readSuccessorJson("package-lock.json");

  assert.deepEqual(successorRoot.overrides, { postcss: "8.5.23" });
  assert.equal(successorLock.packages["node_modules/next"].version, "16.3.0-canary.93");
  assert.equal(successorLock.packages["node_modules/vite"].version, "8.1.5");
  assert.equal(successorLock.packages["node_modules/postcss"].version, "8.5.23");
  assert.equal(successorLock.packages["node_modules/vite/node_modules/postcss"], undefined);
  const successorNonPostcssPackages = Object.fromEntries(
    Object.entries(successorLock.packages).filter(([packagePath]) => !/(^|\/)node_modules\/postcss$/.test(packagePath))
  );
  assert.equal(
    createHash("sha256").update(JSON.stringify(successorNonPostcssPackages)).digest("hex"),
    "49a32ff6e416651358ef5638da18aa2be4de4e04d7f47268cc2ad5f5d1cfd0ca"
  );

  assert.deepEqual(root.overrides, { postcss: "8.5.23" });
  assert.equal(lock.packages["node_modules/next"].version, "16.3.0-canary.93");
  assert.equal(lock.packages["node_modules/vite"].version, "8.1.5");
  assert.equal(lock.packages["node_modules/postcss"].version, "8.5.23");
  assert.equal(lock.packages["node_modules/vite/node_modules/postcss"], undefined);

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const listing = spawnSync(npmCommand, ["ls", "next", "vite", "postcss", "--all", "--json"], {
    cwd: repositoryRoot,
    encoding: "utf8",
    shell: process.platform === "win32"
  });
  assert.equal(listing.status, 1, listing.stderr || listing.stdout);
  const listingJson = JSON.parse(listing.stdout);
  assert.deepEqual(
    listingJson.problems,
    [`invalid: postcss@8.5.23 ${fileURLToPath(new URL("../../node_modules/postcss", import.meta.url))}`]
  );
  assert.equal(listingJson.error?.code, "ELSPROBLEMS");

  const problemKinds = [];
  const invalidReasons = new Set();
  const visitListing = (value) => {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (key === "invalid" && typeof child === "string") invalidReasons.add(child);
      if ((key === "missing" || key === "extraneous") && child) problemKinds.push(key);
      if (key === "problems" && Array.isArray(child)) {
        for (const problem of child) {
          const kind = String(problem).split(":", 1)[0];
          if (kind !== "invalid") problemKinds.push(kind);
        }
      }
      visitListing(child);
    }
  };
  visitListing(listingJson.dependencies);
  assert.deepEqual([...invalidReasons], ['"8.5.10" from node_modules/next']);
  assert.deepEqual(problemKinds, []);

  const nextPostcss = listingJson.dependencies["@daon-user/web"].dependencies.next.dependencies.postcss;
  const vitePostcss = listingJson.dependencies["@daon-user/desktop"].dependencies.vite.dependencies.postcss;
  assert.equal(nextPostcss.version, "8.5.23");
  assert.equal(vitePostcss.version, "8.5.23");
  assert.equal(nextPostcss.invalid, '"8.5.10" from node_modules/next');
  assert.equal(vitePostcss.invalid, '"8.5.10" from node_modules/next');
});
