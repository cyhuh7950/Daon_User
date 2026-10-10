import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../verify-api-runtime.mjs"), "utf8");

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
