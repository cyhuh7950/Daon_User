import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const composeUrl = new URL("../../deploy/daon-user/compose.yaml", import.meta.url);

test("API container receives approved provider secret references without browser exposure", async () => {
  const source = await readFile(composeUrl, "utf8");
  for (const name of [
    "CEREBRAS_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENAI_API_KEY",
    "UPSTAGE_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL"
  ]) {
    assert.ok(source.includes(`      ${name}: \${${name}-}`), `${name} is not passed to API`);
  }
  assert.doesNotMatch(source, /NEXT_PUBLIC_[A-Z_]*(?:API_KEY|OLLAMA|API_BASE_URL)/);
});
