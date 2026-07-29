import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { access, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const targetTriple = "x86_64-pc-windows-msvc";
const executableName = "daon-user-local-service.exe";
export const generatedSidecarPath = path.join(
  repositoryRoot,
  "apps",
  "desktop",
  "src-tauri",
  "binaries",
  `daon-user-local-service-${targetTriple}.exe`
);

async function assertAbsent(candidate) {
  try {
    await access(candidate);
    throw new Error("refusing to overwrite an existing generated sidecar");
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

export async function buildLocalServiceSidecar({ spawnImpl = spawnSync } = {}) {
  if (process.platform !== "win32") {
    throw new Error("Windows sidecar build requires a Windows host");
  }
  await assertAbsent(generatedSidecarPath);
  const isolated = await mkdtemp(path.join(os.tmpdir(), "daon-user-local-service-build-"));
  const dist = path.join(isolated, "dist");
  const work = path.join(isolated, "work");
  try {
    const result = spawnImpl(
      "uv",
      [
        "run",
        "--project",
        "services/local-service",
        "--frozen",
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--collect-all",
        "sqlite_vec",
        "--name",
        "daon-user-local-service",
        "--distpath",
        dist,
        "--workpath",
        work,
        "--specpath",
        isolated,
        "--paths",
        "services/local-service/src",
        "services/local-service/entrypoint.py"
      ],
      {
        cwd: repositoryRoot,
        env: {
          ...process.env,
          UV_CACHE_DIR:
            process.env.UV_CACHE_DIR ??
            path.join(os.tmpdir(), "daon-user-local-service-uv-cache"),
          UV_PYTHON_INSTALL_DIR:
            process.env.UV_PYTHON_INSTALL_DIR ??
            path.join(os.tmpdir(), "daon-user-local-service-uv-python"),
          UV_PROJECT_ENVIRONMENT:
            process.env.UV_PROJECT_ENVIRONMENT ??
            path.join(
              os.tmpdir(),
              `${path.basename(repositoryRoot)}-local-service-uv-env`
            )
        },
        encoding: "utf8",
        windowsHide: true
      }
    );
    if (result.error || result.status !== 0) {
      if (result.stdout) process.stdout.write(result.stdout);
      if (result.stderr) process.stderr.write(result.stderr);
      throw new Error(`pyinstaller failed with exit ${result.status ?? "spawn"}`);
    }
    const built = path.join(dist, executableName);
    const staged = spawnImpl(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        path.join(repositoryRoot, "scripts", "stage-local-service-sidecar.ps1"),
        "-Source",
        built,
        "-DestinationRoot",
        path.dirname(generatedSidecarPath),
        "-WorkspaceRoot",
        repositoryRoot
      ],
      {
        cwd: repositoryRoot,
        encoding: "utf8",
        windowsHide: true
      }
    );
    if (staged.error || staged.status !== 0) {
      if (staged.stdout) process.stdout.write(staged.stdout);
      if (staged.stderr) process.stderr.write(staged.stderr);
      throw new Error(`sidecar staging failed with exit ${staged.status ?? "spawn"}`);
    }
    const bytes = (await stat(generatedSidecarPath)).size;
    const sha256 = createHash("sha256")
      .update(await readFile(generatedSidecarPath))
      .digest("hex")
      .toUpperCase();
    console.log(
      JSON.stringify({
        schema_version: "1.0",
        artifact: path.relative(repositoryRoot, generatedSidecarPath).split(path.sep).join("/"),
        bytes,
        sha256,
        target_triple: targetTriple
      })
    );
    return { path: generatedSidecarPath, bytes, sha256 };
  } finally {
    await rm(isolated, { recursive: true, force: true });
  }
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  buildLocalServiceSidecar().catch((error) => {
    console.error(`LOCAL_SERVICE_BUILD_ERROR ${error.message}`);
    process.exitCode = 1;
  });
}
