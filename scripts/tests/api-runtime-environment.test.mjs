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
