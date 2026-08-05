import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("provider settings helper uses same-origin relative BFF paths only", async () => {
  const source = await read("apps/web/lib/provider-settings-api.js");
  for (const path of ["/bff/api/model-profiles", "/bff/api/model-deployments", "/model-policy"]) assert.match(source, new RegExp(path.replaceAll("/", "\\/")));
  assert.match(source, /credentials:\s*["']same-origin["']/);
  assert.doesNotMatch(source, /["'`]\/api\/v1\//);
  assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|api[_-]?key|secret_value/i);
});

test("model connections screen edits approved providers, models, roles and safe credential presence", async () => {
  const [page, pane] = await Promise.all([
    read("apps/web/app/settings/model-connections/page.jsx"),
    read("apps/web/components/provider-settings-workspace.jsx")
  ]);
  assert.match(page, /ProviderSettingsWorkspace/);
  for (const provider of ["CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI", "OPENROUTER", "ANTHROPIC", "OLLAMA"]) assert.match(pane, new RegExp(provider));
  for (const label of ["Base URL", "모델 ID", "역할 매핑", "활성", "선택", "Credential 설정됨"]) assert.match(pane, new RegExp(label));
  assert.doesNotMatch(pane, /api[_-]?key|secret_value|type=["']password["']/i);
});
