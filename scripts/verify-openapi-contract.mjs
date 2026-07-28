#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const REQUIRED_PATHS = Object.freeze([
  "/api/v1/session",
  "/api/v1/session/step-up",
  "/api/v1/session/oidc/transactions",
  "/api/v1/session/oidc/callback",
  "/api/v1/session/refresh",
  "/api/v1/session/revoke",
  "/api/v1/devices/{id}/trust",
  "/api/v1/devices/{id}/revoke",
  "/api/v1/tenants",
  "/api/v1/workspaces",
  "/api/v1/workspaces/{id}/members",
  "/api/v1/workspaces/{id}/sources",
  "/api/v1/workspaces/{id}/knowledge-scope",
  "/api/v1/workspaces/{id}/weight-profile",
  "/api/v1/workspaces/{id}/model-policy",
  "/api/v1/workspaces/{id}/authorization/evaluations",
  "/api/v1/access-decisions",
  "/api/v1/rulesets",
  "/api/v1/workspaces/{id}/ruleset-bindings",
  "/api/v1/ruleset-bindings/{id}",
  "/api/v1/conversations",
  "/api/v1/conversations/{id}/messages",
  "/api/v1/runs/{id}",
  "/api/v1/runs/{id}/events",
  "/api/v1/runs/{id}/routing-decision",
  "/api/v1/runs/{id}/rule-evaluations",
  "/api/v1/conflicts/{id}",
  "/api/v1/sources/{id}/processing-runs",
  "/api/v1/sources/{id}/transcripts",
  "/api/v1/studio-generation-requests",
  "/api/v1/studio-outputs",
  "/api/v1/studio-outputs/{id}/versions",
  "/api/v1/reviews",
  "/api/v1/approval-requests",
  "/api/v1/approvals",
  "/api/v1/deliveries",
  "/api/v1/knowledge-registrations",
  "/api/v1/model-profiles",
  "/api/v1/model-deployments",
  "/api/v1/model-routing/preview",
  "/api/v1/local-nodes",
  "/api/v1/model-installations",
  "/api/v1/connectors",
  "/api/v1/audit-events"
]);

const HTTP_METHODS = new Set(["get", "post", "patch", "delete"]);
const COMMON_ERROR_STATUSES = ["400", "401", "403", "404", "409", "412", "500"];
const REQUIRED_ERROR_CODES = [
  "COST_LIMIT_EXCEEDED",
  "STEP_UP_REQUIRED",
  "CURRENT_ACCESS_DENIED",
  "IMPORTANT_KNOWLEDGE_CONFLICT",
  "NO_AVAILABLE_UNDERSTANDING_MODEL",
  "NO_AVAILABLE_DEPLOYMENT"
];
const FORBIDDEN_SOURCE_TOKENS = [
  /https?:\/\//i,
  /\blocalhost\b/i,
  /\b127\.0\.0\.1\b/,
  /stack[_-]?trace/i,
  /db[_-]?host/i,
  /internal[_-]?host/i,
  /provider[_-]?raw[_-]?error/i,
  /api[_-]?key/i,
  /client[_-]?secret/i,
  /password/i
];

function fail(message) {
  throw new Error(`OPENAPI_CONTRACT_INVALID ${message}`);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function decodePointerToken(token) {
  return token.replaceAll("~1", "/").replaceAll("~0", "~");
}

function resolveRef(document, reference) {
  if (typeof reference !== "string" || !reference.startsWith("#/")) fail(`unsupported $ref ${String(reference)}`);
  let current = document;
  for (const token of reference.slice(2).split("/").map(decodePointerToken)) {
    if (!isObject(current) || !(token in current)) fail(`missing $ref ${reference}`);
    current = current[token];
  }
  return current;
}

function walk(value, visit) {
  visit(value);
  if (Array.isArray(value)) for (const item of value) walk(item, visit);
  else if (isObject(value)) for (const item of Object.values(value)) walk(item, visit);
}

function parameterRefs(operation) {
  return new Set((operation.parameters ?? []).map((parameter) => parameter.$ref).filter(Boolean));
}

function responseObject(document, response) {
  return response?.$ref ? resolveRef(document, response.$ref) : response;
}

function validateResponse(document, response, operationId, status) {
  const resolved = responseObject(document, response);
  if (!isObject(resolved)) fail(`${operationId} response ${status} is invalid`);
  if (!isObject(resolved.headers) || !("X-Trace-Id" in resolved.headers)) {
    fail(`${operationId} response ${status} missing X-Trace-Id`);
  }
}

function validateTypedErrorDetails(document) {
  const schemas = document.components.schemas;
  const requiredFields = {
    CostLimitExceededDetails: ["limit", "currency", "accrued_cost", "estimated_next_cost", "retryable", "user_action"],
    StepUpRequiredDetails: ["required_field", "action", "target_id"],
    CurrentAccessDeniedDetails: ["access_state", "resource_id"],
    NoAvailableDeploymentDetails: ["required_role", "retryable"]
  };
  for (const [schemaName, fields] of Object.entries(requiredFields)) {
    const schema = schemas[schemaName];
    if (!isObject(schema)) fail(`missing typed error schema ${schemaName}`);
    for (const field of fields) if (!(schema.required ?? []).includes(field)) fail(`${schemaName} missing required ${field}`);
  }
  if (schemas.StepUpRequiredDetails.properties?.required_field?.const !== "step_up_authorization_id") {
    fail("STEP_UP_REQUIRED must name step_up_authorization_id");
  }
  const accessStateSchema = schemas.CurrentAccessDeniedDetails.properties?.access_state;
  const accessStates = accessStateSchema?.$ref
    ? resolveRef(document, accessStateSchema.$ref)?.enum ?? []
    : accessStateSchema?.enum ?? [];
  for (const state of ["available", "partially_redacted", "access_blocked"]) {
    if (!accessStates.includes(state)) fail(`CURRENT_ACCESS_DENIED missing access_state ${state}`);
  }
  const roles = schemas.NoAvailableDeploymentDetails.properties?.required_role?.enum ?? [];
  for (const role of ["text", "vision", "audio_understanding", "speech_to_text", "embedding", "reranker"]) {
    if (!roles.includes(role)) fail(`NO_AVAILABLE_DEPLOYMENT missing required_role ${role}`);
  }
}

function validateAuthorizationContract(document) {
  const schemas = document.components?.schemas ?? {};
  const exactEnums = {
    AuthorizationRole: [
      "personal_owner", "organization_admin", "workspace_admin", "editor",
      "reviewer", "approver", "viewer"
    ],
    AuthorizationPermission: [
      "external_llm", "internet_search", "local_internal_llm", "daon_knowledge",
      "file_download_share", "production_knowledge_registration", "data_area_move",
      "final_approval_external_delivery"
    ],
    AccessState: ["available", "partially_redacted", "access_blocked"]
  };
  for (const [name, expected] of Object.entries(exactEnums)) {
    if (JSON.stringify(schemas[name]?.enum) !== JSON.stringify(expected)) {
      fail(`${name} enum must match the R1-M4-04 contract`);
    }
  }
  const effective = schemas.EffectivePermission;
  for (const field of ["permission", "requested", "effective", "locked_by", "reason", "policy_version"]) {
    if (!(effective?.required ?? []).includes(field)) fail(`EffectivePermission missing ${field}`);
  }
  const decision = schemas.AccessDecision;
  for (const field of [
    "decision_id", "actor_id", "action", "resource_id", "workspace_id",
    "membership_version", "acl_version", "policy_version", "evaluated_at", "state",
    "reason_codes", "allowed_reference_ids", "masked_reference_ids",
    "allowed_segment_ids", "masked_segment_ids"
  ]) {
    if (!(decision?.required ?? []).includes(field)) fail(`AccessDecision missing ${field}`);
  }
  if (decision?.properties?.state?.$ref !== "#/components/schemas/AccessState") {
    fail("AccessDecision state must use AccessState");
  }
  const operations = {
    "/api/v1/workspaces/{id}/authorization/evaluations": "AuthorizationEvaluationResponse",
    "/api/v1/access-decisions": "AccessDecisionResponse"
  };
  for (const [apiPath, responseName] of Object.entries(operations)) {
    const operation = document.paths?.[apiPath]?.post;
    if (!isObject(operation)) fail(`missing authorization operation POST ${apiPath}`);
    const success = operation.responses?.["200"] ?? operation.responses?.["201"];
    if (success?.$ref !== `#/components/responses/${responseName}`) {
      fail(`authorization operation response mismatch POST ${apiPath}`);
    }
  }
}

function validateAuditContract(document) {
  const schemas = document.components?.schemas ?? {};
  const auditChange = schemas.AuditChange;
  if (!isObject(auditChange) || auditChange.type !== "object" || auditChange.additionalProperties !== true) {
    fail("AuditChange safe projection schema is invalid");
  }
  const auditEvent = schemas.AuditEvent;
  const required = [
    "sequence", "event_id", "occurred_at", "actor_id", "actor_type", "tenant_id",
    "workspace_id", "action", "target_type", "target_id", "outcome", "trace_id",
    "policy_version", "before", "after", "metadata", "previous_event_hash", "event_hash"
  ];
  if (!isObject(auditEvent) || auditEvent.type !== "object" || auditEvent.additionalProperties !== false) {
    fail("AuditEvent schema is invalid");
  }
  for (const field of required) if (!(auditEvent.required ?? []).includes(field)) fail(`AuditEvent missing ${field}`);
  for (const field of ["event_id", "actor_id", "tenant_id", "workspace_id", "target_id", "trace_id"]) {
    const property = auditEvent.properties?.[field];
    const refs = property?.oneOf ?? [property];
    if (!refs.some((item) => item?.$ref === "#/components/schemas/OpaqueId")) fail(`AuditEvent ${field} must use OpaqueId`);
  }
  if (auditEvent.properties?.occurred_at?.format !== "date-time") fail("AuditEvent occurred_at must be date-time");
  for (const field of ["previous_event_hash", "event_hash"]) {
    if (auditEvent.properties?.[field]?.pattern !== "^[0-9a-f]{64}$") fail(`AuditEvent ${field} must be SHA-256 hex`);
  }
  for (const field of ["before", "after"]) {
    const options = auditEvent.properties?.[field]?.oneOf ?? [];
    if (!options.some((item) => item?.$ref === "#/components/schemas/AuditChange") || !options.some((item) => item?.type === "null")) {
      fail(`AuditEvent ${field} must be nullable AuditChange`);
    }
  }

  const operation = document.paths?.["/api/v1/audit-events"]?.get;
  if (operation?.responses?.["200"]?.$ref !== "#/components/responses/AuditEventListResponse") {
    fail("listAuditEvents must use AuditEventListResponse");
  }
  const refs = parameterRefs(operation);
  for (const name of ["AuditTenantId", "AuditWorkspaceId", "AuditAction", "AuditOutcome", "AuditTraceId", "AuditOccurredAfter", "AuditOccurredBefore"]) {
    if (!refs.has(`#/components/parameters/${name}`)) fail(`listAuditEvents missing ${name}`);
  }
  const listSchema = schemas.AuditEventPage;
  if (listSchema?.properties?.items?.items?.$ref !== "#/components/schemas/AuditEvent") {
    fail("AuditEventPage must contain AuditEvent items");
  }
}

function validateIdentityContract(document) {
  const schemas = document.components?.schemas ?? {};
  for (const name of [
    "IdentitySession", "OidcLoginStartRequest", "OidcLoginStart",
    "OidcCallbackRequest", "NativeRefreshRequest", "StepUpAuthorizationRequest",
    "StepUpAuthorization", "DeviceRevokeRequest", "DeviceRevocation",
    "SessionRevokeRequest", "SessionRevocation"
  ]) {
    if (!isObject(schemas[name])) fail(`missing identity schema ${name}`);
  }
  if (schemas.OidcLoginStart?.properties?.code_challenge_method?.const !== "S256") {
    fail("OIDC start must use PKCE S256");
  }
  if (schemas.StepUpAuthorizationRequest?.properties?.ttl_seconds?.maximum !== 600) {
    fail("Step-up TTL maximum must be 600 seconds");
  }
  if (schemas.NativeRefreshRequest?.properties?.refresh_credential?.writeOnly !== true) {
    fail("Native refresh credential must be writeOnly");
  }
  if (schemas.DeviceRevokeRequest?.properties?.step_up_authorization?.writeOnly !== true) {
    fail("Device revoke Step-up value must be writeOnly");
  }
  if (schemas.SessionRevokeRequest?.properties?.step_up_authorization?.writeOnly !== true) {
    fail("Session revoke Step-up value must be writeOnly");
  }
  const requiredOperations = {
    "/api/v1/session": ["get", "IdentitySessionResponse"],
    "/api/v1/session/step-up": ["post", "StepUpAuthorizationResponse"],
    "/api/v1/session/oidc/transactions": ["post", "OidcLoginStartResponse"],
    "/api/v1/session/oidc/callback": ["post", "IdentitySessionResponse"],
    "/api/v1/session/refresh": ["post", "IdentitySessionResponse"],
    "/api/v1/session/revoke": ["post", "SessionRevocationResponse"],
    "/api/v1/devices/{id}/trust": ["post", "SuccessResponse"],
    "/api/v1/devices/{id}/revoke": ["post", "DeviceRevocationResponse"]
  };
  for (const [apiPath, [method, responseName]] of Object.entries(requiredOperations)) {
    const operation = document.paths?.[apiPath]?.[method];
    if (!isObject(operation)) fail(`missing identity operation ${method.toUpperCase()} ${apiPath}`);
    const success = operation.responses?.["201"] ?? operation.responses?.["200"];
    if (success?.$ref !== `#/components/responses/${responseName}`) {
      fail(`identity operation response mismatch ${method.toUpperCase()} ${apiPath}`);
    }
  }
  const callbackDescription = document.paths?.["/api/v1/session/oidc/callback"]?.post?.description ?? "";
  if (!callbackDescription.includes("same-origin") || !callbackDescription.includes("HTTPS")) {
    fail("OIDC callback must distinguish Web same-origin and Native HTTPS delivery");
  }
}

export function validateOpenApiDocument(document) {
  if (!isObject(document)) fail("document must be an object");
  if (!/^3\.1\.\d+$/.test(document.openapi ?? "")) fail("OpenAPI version must be 3.1.x");
  if (document.info?.version !== "1.0.0") fail("info.version must be 1.0.0");

  if (document.servers !== undefined) {
    if (!Array.isArray(document.servers)) fail("servers must be an array");
    for (const server of document.servers) {
      if (typeof server?.url !== "string" || !server.url.startsWith("/") || server.url.startsWith("//")) {
        fail("server must use same-origin relative URL");
      }
    }
  }

  const serialized = JSON.stringify(document);
  for (const pattern of FORBIDDEN_SOURCE_TOKENS) if (pattern.test(serialized)) fail(`forbidden token ${pattern}`);

  if (!isObject(document.paths)) fail("paths must be an object");
  const actualPaths = Object.keys(document.paths);
  for (const requiredPath of REQUIRED_PATHS) if (!(requiredPath in document.paths)) fail(`required path missing ${requiredPath}`);
  for (const actualPath of actualPaths) if (!REQUIRED_PATHS.includes(actualPath)) fail(`unapproved path ${actualPath}`);

  walk(document, (value) => {
    if (isObject(value) && "$ref" in value) resolveRef(document, value.$ref);
  });

  const operationIds = new Set();
  for (const [apiPath, pathItem] of Object.entries(document.paths)) {
    if (!isObject(pathItem)) fail(`path item invalid ${apiPath}`);
    const operations = Object.entries(pathItem).filter(([method]) => HTTP_METHODS.has(method));
    if (operations.length === 0) fail(`path has no operation ${apiPath}`);
    for (const [method, operation] of operations) {
      if (!isObject(operation)) fail(`${method.toUpperCase()} ${apiPath} operation invalid`);
      const operationId = operation.operationId;
      if (typeof operationId !== "string" || operationId.length === 0) fail(`${method.toUpperCase()} ${apiPath} missing operationId`);
      if (operationIds.has(operationId)) fail(`duplicate operationId ${operationId}`);
      operationIds.add(operationId);
      if (!Array.isArray(operation.tags) || operation.tags.length === 0) fail(`${operationId} missing tags`);
      if (typeof operation.summary !== "string" || operation.summary.length === 0) fail(`${operationId} missing summary`);
      if (typeof operation["x-implementation-owner"] !== "string" || operation["x-implementation-owner"].length === 0) {
        fail(`${operationId} missing implementation owner`);
      }

      const refs = parameterRefs(operation);
      if (apiPath.includes("{id}") && !refs.has("#/components/parameters/ResourceId")) fail(`${operationId} missing opaque ResourceId`);
      if (method === "post" && !refs.has("#/components/parameters/IdempotencyKey")) fail(`${operationId} missing Idempotency-Key`);
      if ((method === "patch" || method === "delete") && !refs.has("#/components/parameters/IfMatch")) fail(`${operationId} missing If-Match`);
      if (operation["x-list-operation"] === true) {
        for (const name of ["Cursor", "Limit", "Filter", "Search"]) {
          if (!refs.has(`#/components/parameters/${name}`)) fail(`${operationId} missing list parameter ${name}`);
        }
      }

      if (!isObject(operation.responses)) fail(`${operationId} missing responses`);
      for (const [status, response] of Object.entries(operation.responses)) validateResponse(document, response, operationId, status);
      for (const status of COMMON_ERROR_STATUSES) if (!(status in operation.responses)) fail(`${operationId} missing common error ${status}`);

      const success = Object.entries(operation.responses).find(([status]) => /^2\d\d$/.test(status));
      if (!success) fail(`${operationId} missing success response`);
      const [successStatus, successResponse] = success;
      const resolvedSuccess = responseObject(document, successResponse);
      const isSse = apiPath === "/api/v1/runs/{id}/events" && method === "get";
      if (!isSse && !isObject(resolvedSuccess.headers?.ETag)) fail(`${operationId} response ${successStatus} missing ETag`);
    }
  }

  const opaqueId = document.components?.schemas?.OpaqueId;
  if (!isObject(opaqueId) || opaqueId.type !== "string" || "format" in opaqueId || "pattern" in opaqueId) {
    fail("OpaqueId must not fix UUID, ULID or semantic format");
  }
  const safeError = document.components?.schemas?.SafeError;
  for (const field of ["code", "message", "stage", "impact", "retryable", "user_action", "trace_id", "details"]) {
    if (!(safeError?.required ?? []).includes(field)) fail(`SafeError missing required ${field}`);
  }
  const codes = document.components?.schemas?.SafeErrorCode?.enum ?? [];
  for (const code of REQUIRED_ERROR_CODES) if (!codes.includes(code)) fail(`required error code missing ${code}`);
  validateTypedErrorDetails(document);
  validateAuditContract(document);
  validateIdentityContract(document);
  validateAuthorizationContract(document);

  const eventOperation = document.paths["/api/v1/runs/{id}/events"]?.get;
  if (!parameterRefs(eventOperation).has("#/components/parameters/LastEventId")) fail("SSE missing Last-Event-ID reconnect cursor");
  const eventResponse = responseObject(document, eventOperation.responses?.["200"]);
  if (!eventResponse?.content?.["text/event-stream"]) fail("SSE response missing text/event-stream content");
  const eventRequired = document.components?.schemas?.RunEvent?.required ?? [];
  for (const field of ["id", "type", "occurred_at", "trace_id", "payload"]) if (!eventRequired.includes(field)) fail(`RunEvent missing ${field}`);

  return { operationCount: operationIds.size };
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!isObject(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])]));
}

export function canonicalizeDocument(document) {
  return JSON.stringify(canonicalValue(document));
}

export function buildSummary(document) {
  const { operationCount } = validateOpenApiDocument(document);
  const canonical = canonicalizeDocument(document);
  return {
    schema_version: "1.0",
    contract_version: document.info.version,
    canonical_sha256: createHash("sha256").update(canonical).digest("hex").toUpperCase(),
    path_count: Object.keys(document.paths).length,
    operation_count: operationCount,
    schema_count: Object.keys(document.components.schemas).length,
    error_code_count: document.components.schemas.SafeErrorCode.enum.length
  };
}

export async function verifyOpenApiContract({ root, write = false } = {}) {
  const repositoryRoot = root ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const contractPath = path.join(repositoryRoot, "packages/contracts/openapi/v1/openapi.json");
  const evidencePath = path.join(repositoryRoot, "docs/03_evidence/release_1/R1-M4-01/openapi-contract-summary.json");
  const document = JSON.parse(await readFile(contractPath, "utf8"));
  const summary = buildSummary(document);
  const expected = `${JSON.stringify(summary, null, 2)}\n`;
  if (write) {
    await mkdir(path.dirname(evidencePath), { recursive: true });
    await writeFile(evidencePath, expected, "utf8");
  } else {
    const actual = (await readFile(evidencePath, "utf8")).replaceAll("\r\n", "\n");
    if (actual !== expected) fail("deterministic evidence does not match; run with --write");
  }
  return summary;
}

async function main() {
  const args = process.argv.slice(2).filter((argument) => argument !== "--");
  const write = args.includes("--write");
  if (write && args.includes("--no-write")) fail("--write and --no-write are mutually exclusive");
  const summary = await verifyOpenApiContract({ write });
  console.log(`openapi contract verified: paths=${summary.path_count} operations=${summary.operation_count} schemas=${summary.schema_count} errors=${summary.error_code_count} sha256=${summary.canonical_sha256}`);
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
