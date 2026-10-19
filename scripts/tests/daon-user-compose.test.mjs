import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parse } from "yaml";

const composeUrl = new URL("../../deploy/daon-user/compose.yaml", import.meta.url);

test("S44 API가 기대하는 문서 worker는 실제 Compose publisher 식별자와 일치한다", async () => {
  const document = parse(await readFile(composeUrl, "utf8"));
  const key = "DAON_DOCUMENT_WORKER_ID";
  for (const [project, expected] of [["daon_user", "daon_user-document-worker"], ["qa-isolated", "qa-isolated-document-worker"]]) {
    const resolved = name => document.services[name].environment[key]?.replaceAll("${COMPOSE_PROJECT_NAME}", project);
    assert.equal(resolved("api"), expected, `API expected worker for ${project}`);
    assert.equal(resolved("document-worker"), expected, `actual publisher for ${project}`);
  }
  for (const name of ["web", "studio-worker", "object-storage"]) {
    assert.equal(Object.hasOwn(document.services[name].environment || {}, key), false, name);
  }
});

test("S42 기존 알림 flag는 API·두 worker에만 default false로 전달된다", async () => {
  const document = parse(await readFile(composeUrl, "utf8"));
  const flag = "DAON_NOTIFICATION_INBOX_ENABLED";
  const callers = new Set(["api", "document-worker", "studio-worker"]);
  for (const [name, service] of Object.entries(document.services)) {
    const environment = service.environment || {};
    if (!callers.has(name)) {
      assert.equal(Object.hasOwn(environment, flag), false, name);
      continue;
    }
    assert.equal(environment[flag], "${DAON_NOTIFICATION_INBOX_ENABLED-false}", name);
    for (const [value, expected] of [[undefined, "false"], ["false", "false"], ["true", "true"]]) {
      const expanded = environment[flag].replace(/\$\{DAON_NOTIFICATION_INBOX_ENABLED-([^}]+)\}/u,
        (_match, fallback) => value === undefined ? fallback : value);
      assert.equal(expanded, expected);
    }
  }
});

test("S39 적용 flag는 API에만 전달하며 미설정 false·명시 true 계약을 보존한다", async () => {
  const document = parse(await readFile(composeUrl, "utf8"));
  const flag = "DAON_ORGANIZATION_ADMIN_V2_ENABLED";
  const expression = document.services.api.environment[flag];
  assert.equal(expression, "${DAON_ORGANIZATION_ADMIN_V2_ENABLED-false}");
  // Compose의 이 한 default 표현식 계약만 확인한다. 실제 Compose config/배포 시험은 아니다.
  for (const [provided, expected] of [[undefined, "false"], ["true", "true"], ["false", "false"]]) {
    const expanded = expression.replace(/\$\{DAON_ORGANIZATION_ADMIN_V2_ENABLED-([^}]+)\}/u, (_match, fallback) => provided === undefined ? fallback : provided);
    assert.equal(expanded, expected);
  }
  for (const [name, service] of Object.entries(document.services)) {
    if (name !== "api") assert.equal(Object.hasOwn(service.environment || {}, flag), false, name);
  }
});

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
