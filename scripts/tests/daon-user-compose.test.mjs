import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const composeUrl = new URL("../../deploy/daon-user/compose.yaml", import.meta.url);

test("provider credentials use one read-only secret reference in provider callers", async () => {
  const source = await readFile(composeUrl, "utf8");
  const api = source.split("  api:", 2)[1]?.split("\n  object-storage:", 1)[0] ?? "";
  const worker = source.split("  document-worker:", 2)[1]?.split("\n  studio-worker:", 1)[0] ?? "";
  const studioWorker = source.split("  studio-worker:", 2)[1]?.split("\n  web:", 1)[0] ?? "";

  assert.match(api, /DAON_PROVIDER_CREDENTIAL_KEY_FILE:\s*\/run\/secrets\/provider_credential_key/);
  assert.match(api, /\n\s+- provider_credential_key/);
  assert.match(worker, /DAON_PROVIDER_CREDENTIAL_KEY_FILE:\s*\/run\/secrets\/provider_credential_key/);
  assert.match(worker, /\n\s+- provider_credential_key/);
  assert.doesNotMatch(studioWorker, /provider_credential_key/);
  assert.match(
    source,
    /provider_credential_key:\s*\n\s+file:\s*\$\{DAON_PROVIDER_CREDENTIAL_KEY_FILE:\?provider credential key reference required\}/,
  );
  assert.doesNotMatch(
    source,
    /\b(?:CEREBRAS|GROQ|MISTRAL|OPENAI|UPSTAGE|GEMINI|OPENROUTER|ANTHROPIC)_API_KEY:/,
  );
  assert.doesNotMatch(source, /\bOLLAMA_BASE_URL:/);
  assert.doesNotMatch(source, /NEXT_PUBLIC_[A-Z_]*(?:API_KEY|OLLAMA|API_BASE_URL)/);
});

test("dedicated object bucket provisioning is explicit in the API container", async () => {
  const source = await readFile(composeUrl, "utf8");
  assert.match(source, /DAON_OBJECT_STORAGE_PROVISION_BUCKET:\s*true/);
});

test("document worker is internal and receives the same server-only dependencies", async () => {
  const source = await readFile(composeUrl, "utf8");
  const worker = source.split("  document-worker:", 2)[1]?.split("\n  studio-worker:", 1)[0] ?? "";

  assert.ok(worker, "document-worker service is missing");
  assert.match(worker, /command:\s*\["python", "-m", "daon_user_api\.document_processing_worker"\]/);
  assert.match(worker, /DAON_CLOUD_DATABASE_DSN:/);
  assert.match(worker, /DAON_OBJECT_STORAGE_ENDPOINT:/);
  assert.match(worker, /DAON_PROVIDER_CREDENTIAL_KEY_FILE:/);
  assert.match(worker, /object_access_key/);
  assert.match(worker, /DAON_DOCUMENT_WORKER_LEASE_SECONDS:\s*\$\{DAON_DOCUMENT_WORKER_LEASE_SECONDS-600\}/);
  assert.match(worker, /healthcheck:\s*\n\s+disable:\s*true/);
  assert.doesNotMatch(worker, /\n\s+ports:/);
  assert.doesNotMatch(worker, /NEXT_PUBLIC_/);
});

test("example environment exposes only the provider credential key file", async () => {
  const source = await readFile(new URL("../../.env.example", import.meta.url), "utf8");
  assert.match(source, /^DAON_PROVIDER_CREDENTIAL_KEY_FILE=/m);
  assert.doesNotMatch(
    source,
    /^(?:CEREBRAS|GROQ|MISTRAL|OPENAI|UPSTAGE|GEMINI|OPENROUTER|ANTHROPIC)_API_KEY=/m,
  );
  assert.doesNotMatch(source, /^OLLAMA_BASE_URL=/m);
});

test("legacy credential import is explicit, idempotent, and secret-safe", async () => {
  const source = await readFile(new URL("../import-provider-credentials.py", import.meta.url), "utf8");
  const dockerfile = await readFile(new URL("../../services/api/Dockerfile", import.meta.url), "utf8");
  assert.match(source, /encrypted_credential IS NULL/);
  assert.match(source, /DAON_PROVIDER_CREDENTIAL_KEY_FILE/);
  assert.match(source, /DAON_CLOUD_DATABASE_DSN/);
  assert.match(source, /PROVIDER_CREDENTIAL_IMPORT_COMPLETED/);
  assert.match(source, /system_provider_audit_outbox WHERE event_id/);
  assert.doesNotMatch(source, /print\([^\n]*(?:credential|api_key|secret)/i);
  assert.match(dockerfile, /COPY scripts\/import-provider-credentials\.py \.\/scripts\/import-provider-credentials\.py/);
  assert.match(dockerfile, /cryptography==50\.0\.0/);
});
