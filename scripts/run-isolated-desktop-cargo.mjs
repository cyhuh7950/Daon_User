import { lstat, mkdtemp, realpath, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SAFE_PREFIX = "daon-user-desktop-";
const GENERATED_TAURI_DIR = path.join(repositoryRoot, "apps", "desktop", "src-tauri", "gen");

export async function runIsolatedCargo({
  prefix = path.join(os.tmpdir(), `${SAFE_PREFIX}cargo-`),
  keepOnSuccess,
  command,
  args,
  spawnImpl = spawnSync
}) {
  const targetDir = await mkdtemp(prefix);
  let result;
  try {
    result = spawnImpl(command, args, {
      cwd: repositoryRoot,
      env: { ...process.env, CARGO_TARGET_DIR: targetDir },
      shell: false,
      stdio: "inherit"
    });
  } catch (error) {
    await rm(targetDir, { recursive: true, force: true });
    throw error;
  }

  if (result.error) {
    await rm(targetDir, { recursive: true, force: true });
    return { exitCode: 2, signal: null, targetDir, kept: false, error: result.error };
  }
  const exitCode = Number.isInteger(result.status) ? result.status : result.signal ? 1 : 2;
  const kept = exitCode === 0 && keepOnSuccess;
  if (!kept) await rm(targetDir, { recursive: true, force: true });
  return { exitCode, signal: result.signal ?? null, targetDir, kept };
}

export async function cleanupIsolatedTarget(candidate) {
  if (!candidate) throw new Error("cleanup target is required");
  const tempRoot = await realpath(os.tmpdir());
  const resolvedParent = await realpath(path.dirname(path.resolve(candidate)));
  const name = path.basename(candidate);
  if (resolvedParent !== tempRoot || !name.startsWith(SAFE_PREFIX)) {
    throw new Error("refusing to remove a non-isolated desktop target");
  }
  await rm(path.join(resolvedParent, name), { recursive: true, force: true });
}

async function pathExists(candidate) {
  try {
    await lstat(candidate);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

export async function runDesktopCargoSafely({
  generatedDir = GENERATED_TAURI_DIR,
  probePathImpl = pathExists,
  ...cargoOptions
}) {
  try {
    if (await probePathImpl(generatedDir)) {
      return {
        exitCode: 2,
        signal: null,
        targetDir: null,
        kept: false,
        childStarted: false,
        preexistingGeneratedDir: true,
        error: new Error("refusing to run while the desktop Tauri gen path already exists")
      };
    }
  } catch (error) {
    return {
      exitCode: 2,
      signal: null,
      targetDir: null,
      kept: false,
      childStarted: false,
      preexistingGeneratedDir: null,
      error
    };
  }

  let result;
  try {
    result = await runIsolatedCargo(cargoOptions);
  } catch (error) {
    result = {
      exitCode: 2,
      signal: null,
      targetDir: null,
      kept: false,
      error
    };
  }

  try {
    await rm(generatedDir, { recursive: true, force: true });
  } catch (error) {
    return {
      ...result,
      exitCode: 2,
      signal: null,
      kept: false,
      childStarted: true,
      preexistingGeneratedDir: false,
      error
    };
  }
  return {
    ...result,
    childStarted: true,
    preexistingGeneratedDir: false
  };
}

async function main() {
  const [mode, candidate] = process.argv.slice(2);
  if (mode === "cleanup") {
    await cleanupIsolatedTarget(candidate);
    console.log(`DESKTOP_CARGO_TARGET_REMOVED=${candidate}`);
    return;
  }

  const isInstaller = mode === "installer";
  if (!isInstaller && mode !== "check") throw new Error(`unsupported mode: ${mode ?? ""}`);
  const npmExecPath = process.env.npm_execpath;
  if (isInstaller && !npmExecPath) throw new Error("npm_execpath is required for installer mode");
  const command = isInstaller ? process.execPath : "cargo";
  const args = isInstaller
    ? [npmExecPath, "run", "tauri:build", "--workspace", "@daon-user/desktop"]
    : ["check", "--manifest-path", "apps/desktop/src-tauri/Cargo.toml", "--locked"];
  const prefix = path.join(os.tmpdir(), `${SAFE_PREFIX}${isInstaller ? "installer" : "check"}-`);
  const result = await runDesktopCargoSafely({
    prefix,
    keepOnSuccess: isInstaller,
    command,
    args
  });
  if (result.error) console.error(`DESKTOP_CARGO_CHILD_ERROR ${result.error.code ?? result.error.message}`);
  if (result.kept) {
    console.log(`DESKTOP_CARGO_TARGET=${result.targetDir}`);
    console.log(`DESKTOP_INSTALLER_ROOT=${path.join(result.targetDir, "release", "bundle", "nsis")}`);
  }
  if (result.signal) process.kill(process.pid, result.signal);
  process.exitCode = result.exitCode;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`DESKTOP_CARGO_WRAPPER_ERROR ${error?.message ?? "unknown"}`);
    process.exitCode = 2;
  });
}
