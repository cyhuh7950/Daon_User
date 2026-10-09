import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { runLocalServiceTool } from "../run-local-service-tool.mjs";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("Local Service 기본 검사는 작업 트리 전용 Python 환경을 사용한다", () => {
  const previous = process.env.UV_PROJECT_ENVIRONMENT;
  delete process.env.UV_PROJECT_ENVIRONMENT;
  let projectEnvironment;
  try {
    const result = runLocalServiceTool("lint", {
      spawnImpl(command, args, options) {
        assert.equal(command, "uv");
        assert.deepEqual(args.slice(0, 4), ["run", "--project", "services/local-service", "--frozen"]);
        projectEnvironment = options.env.UV_PROJECT_ENVIRONMENT;
        return { status: 0, stdout: "", stderr: "" };
      }
    });
    assert.equal(result.exitCode, 0);
    assert.equal(projectEnvironment, path.join(repositoryRoot, "services", "local-service", ".venv"));
  } finally {
    if (previous === undefined) delete process.env.UV_PROJECT_ENVIRONMENT;
    else process.env.UV_PROJECT_ENVIRONMENT = previous;
  }
});
