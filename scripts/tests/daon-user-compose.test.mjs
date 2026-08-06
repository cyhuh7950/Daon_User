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

test("dedicated object bucket provisioning is explicit in the API container", async () => {
  const source = await readFile(composeUrl, "utf8");
  assert.match(source, /DAON_OBJECT_STORAGE_PROVISION_BUCKET:\s*true/);
});

test("document worker is internal and receives the same server-only dependencies", async () => {
  const source = await readFile(composeUrl, "utf8");
  const worker = source.split("  document-worker:", 2)[1]?.split("\n  web:", 1)[0] ?? "";

  assert.ok(worker, "document-worker service is missing");
  assert.match(worker, /command:\s*\["python", "-m", "daon_user_api\.document_processing_worker"\]/);
  assert.match(worker, /DAON_CLOUD_DATABASE_DSN:/);
  assert.match(worker, /DAON_OBJECT_STORAGE_ENDPOINT:/);
  assert.match(worker, /UPSTAGE_API_KEY:/);
  assert.match(worker, /object_access_key/);
  assert.match(worker, /DAON_DOCUMENT_WORKER_LEASE_SECONDS:\s*\$\{DAON_DOCUMENT_WORKER_LEASE_SECONDS-600\}/);
  assert.match(worker, /healthcheck:\s*\n\s+disable:\s*true/);
  assert.doesNotMatch(worker, /\n\s+ports:/);
  assert.doesNotMatch(worker, /NEXT_PUBLIC_/);
});
