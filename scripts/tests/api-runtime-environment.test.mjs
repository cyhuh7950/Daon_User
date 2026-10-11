import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../verify-api-runtime.mjs"), "utf8");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("API 런타임 검증은 공유 임시 경로 대신 작업 트리 전용 Python 환경을 기본값으로 사용한다", () => {
  assert.match(script, /UV_PROJECT_ENVIRONMENT:\s*process\.env\.UV_PROJECT_ENVIRONMENT\s*\?\?\s*path\.join\(apiRoot, "\.venv"\)/);
  assert.doesNotMatch(script, /daon-user-api-uv-env/);
});

test("API 인증·권한 검증기와 자식 검증기는 같은 작업 트리 전용 환경을 사용한다", () => {
  const scriptsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  for (const [name, expectedCount] of [
    ["verify-api-identity.mjs", 1],
    ["verify-api-authorization.mjs", 2],
  ]) {
    const source = readFileSync(path.join(scriptsRoot, name), "utf8");
    assert.doesNotMatch(source, /daon-user-api-uv-env/, name);
    assert.equal(
      source.match(/UV_PROJECT_ENVIRONMENT:\s*process\.env\.UV_PROJECT_ENVIRONMENT\s*\?\?\s*path\.join\(apiRoot, "\.venv"\)/g)?.length,
      expectedCount,
      name,
    );
  }
});

test("stale 루트 Python이 있어도 API 인증·권한 검증은 API 전용 환경에서 성공한다", (context) => {
  const rootPython = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const apiPython = path.join(root, "services/api/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (!existsSync(rootPython) || !existsSync(apiPython)) return context.skip("두 Python 환경이 모두 필요합니다.");
  const rootArgon2 = spawnSync(rootPython, ["-c", "import argon2"], { cwd: root, stdio: "ignore" });
  const apiArgon2 = spawnSync(apiPython, ["-c", "import argon2"], { cwd: root, stdio: "ignore" });
  if (rootArgon2.status === 0 || apiArgon2.status !== 0) return context.skip("stale 루트/API 정상 환경 조합이 아닙니다.");

  for (const override of [undefined, path.join(root, "services/api/.venv")]) {
    for (const name of ["verify-api-identity.mjs", "verify-api-authorization.mjs"]) {
      const env = { ...process.env };
      if (override) env.UV_PROJECT_ENVIRONMENT = override;
      else delete env.UV_PROJECT_ENVIRONMENT;
      const result = spawnSync(process.execPath, [path.join(root, "scripts", name), "--check-only"], {
        cwd: root,
        encoding: "utf8",
        env,
        maxBuffer: 16 * 1024 * 1024,
      });
      assert.equal(result.status, 0, `${name} override=${Boolean(override)}: ${(result.stdout + result.stderr).slice(-1500)}`);
    }
  }
  for (const name of ["verify-api-identity.mjs", "verify-api-authorization.mjs"]) {
    const result = spawnSync(process.execPath, [path.join(root, "scripts", name), "--check-only"], {
      cwd: root,
      encoding: "utf8",
      env: { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(root, ".venv") },
      maxBuffer: 16 * 1024 * 1024,
    });
    assert.equal(result.status, 1, `${name} ignored explicit stale override`);
    assert.match(result.stdout + result.stderr, /No module named 'argon2'/);
  }
});
