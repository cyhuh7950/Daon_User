import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const safeFileCoverage =
  process.platform === "win32"
    ? "daon_user_local_service_safe_file_win32"
    : "daon_user_local_service_safe_file_posix";
const commands = Object.freeze({
  lint: ["-m", "ruff", "check", "services/local-service"],
  type: ["-m", "mypy", "services/local-service/src", "services/local-service/tests"],
  unit: [
    "-m",
    "pytest",
    "-q",
    "services/local-service/tests",
    "--cov=daon_user_local_service",
    `--cov=${safeFileCoverage}`,
    "--cov-report=term-missing",
    "--cov-fail-under=85"
  ],
  contract: [
    "-m",
    "pytest",
    "-q",
    "services/local-service/tests/test_protocol.py",
    "services/local-service/tests/test_app.py"
  ],
  build: ["-m", "compileall", "-q", "services/local-service/src"],
  security: [
    "-m",
    "pip_audit",
    "--local",
    "--no-deps",
    "--disable-pip",
    "--progress-spinner",
    "off"
  ]
});

export function runLocalServiceTool(action, { spawnImpl = spawnSync } = {}) {
  const pytestTempRoot = mkdtempSync(path.join(os.tmpdir(), "daon-user-pytest-root-"));
  const environment = {
    ...process.env,
    UV_CACHE_DIR:
      process.env.UV_CACHE_DIR ?? path.join(os.tmpdir(), "daon-user-local-service-uv-cache"),
    UV_PYTHON_INSTALL_DIR:
      process.env.UV_PYTHON_INSTALL_DIR ??
      path.join(os.tmpdir(), "daon-user-local-service-uv-python"),
    UV_PROJECT_ENVIRONMENT:
      process.env.UV_PROJECT_ENVIRONMENT ??
      path.join(os.tmpdir(), `${path.basename(repositoryRoot)}-local-service-uv-env-r1`),
    TMP: pytestTempRoot,
    TEMP: pytestTempRoot,
    TMPDIR: pytestTempRoot,
  };
  if (action === "security") {
    const auditDirectory = mkdtempSync(path.join(os.tmpdir(), "daon-user-full-env-audit-"));
    const requirements = path.join(auditDirectory, "requirements.txt");
    const environmentPython =
      process.platform === "win32"
        ? path.join(environment.UV_PROJECT_ENVIRONMENT, "Scripts", "python.exe")
        : path.join(environment.UV_PROJECT_ENVIRONMENT, "bin", "python");
    try {
      const synced = spawnImpl(
        "uv",
        ["sync", "--project", "services/local-service", "--frozen"],
        { cwd: repositoryRoot, env: environment, encoding: "utf8", windowsHide: true }
      );
      if (synced.stderr) process.stderr.write(synced.stderr);
      if (synced.status !== 0 || synced.error) {
        return {
          exitCode: Number.isInteger(synced.status) ? synced.status : 2,
          error: synced.error?.code ?? null
        };
      }
      const frozen = spawnImpl(
        "uv",
        ["pip", "freeze", "--python", environmentPython],
        {
          cwd: repositoryRoot,
          env: environment,
          encoding: "utf8",
          windowsHide: true
        }
      );
      if (frozen.stderr) process.stderr.write(frozen.stderr);
      if (frozen.status !== 0 || frozen.error) {
        return {
          exitCode: Number.isInteger(frozen.status) ? frozen.status : 2,
          error: frozen.error?.code ?? null
        };
      }
      // The interpreter's bootstrap pip is not an application dependency and
      // must not make the local-service dependency gate fail independently of
      // the locked project packages.
      const applicationRequirements = String(frozen.stdout ?? "")
        .split(/\r?\n/u)
        .filter((line) => !/^pip==/iu.test(line))
        .join("\n");
      writeFileSync(requirements, applicationRequirements, "utf8");
      const audited = spawnImpl(
        "uv",
        [
          "run",
          "--project",
          "services/local-service",
          "--frozen",
          "--no-sync",
          "python",
          "-m",
          "pip_audit",
          "--requirement",
          requirements,
          "--no-deps",
          "--disable-pip",
          "--vulnerability-service",
          "osv",
          "--progress-spinner",
          "off"
        ],
        {
          cwd: repositoryRoot,
          env: environment,
          encoding: "utf8",
          windowsHide: true
        }
      );
      if (audited.stdout) process.stdout.write(audited.stdout);
      if (audited.stderr) process.stderr.write(audited.stderr);
      return {
        exitCode: Number.isInteger(audited.status) ? audited.status : 2,
        error: audited.error?.code ?? null
      };
    } finally {
      rmSync(auditDirectory, { recursive: true, force: true });
      rmSync(pytestTempRoot, { recursive: true, force: true });
    }
  }
  const args = commands[action];
  if (!args) {
    rmSync(pytestTempRoot, { recursive: true, force: true });
    return { exitCode: 2, error: "unsupported action" };
  }
  const result = spawnImpl("uv", [
    "run",
    "--project",
    "services/local-service",
    "--frozen",
    "python",
    ...args
  ], {
    cwd: repositoryRoot,
    env: environment,
    encoding: "utf8",
    windowsHide: true
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  rmSync(pytestTempRoot, { recursive: true, force: true });
  return {
    exitCode: Number.isInteger(result.status) ? result.status : 2,
    error: result.error?.code ?? null
  };
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  const result = runLocalServiceTool(process.argv[2]);
  if (result.error) console.error(`LOCAL_SERVICE_TOOL_ERROR ${result.error}`);
  process.exitCode = result.exitCode;
}
