#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const REQUIRED_PATHS = Object.freeze([
  "/api/v1/system/operations/status",
  "/api/v1/organization/admin/tenants",
  "/api/v1/organization/tenants/{tenant_id}/workspaces",
  "/api/v1/organization/tenants/{tenant_id}/invitations",
  "/api/v1/organization/tenants/{tenant_id}/invitations/{invitation_id}",
  "/api/v1/organization/tenants/{tenant_id}/members/{user_id}/role",
  "/api/v1/organization/tenants/{tenant_id}/members/{user_id}/state",
  "/api/v1/organization/join-requests",
  "/api/v1/organization/join-requests/{request_id}/decision",
  "/api/v1/auth/native/login",
  "/api/v1/preferences/screen",
  "/api/v1/workspaces/{id}/license",
  "/api/v1/workspaces/{id}/notebooks",
  "/api/v1/workspaces/{id}/notebooks/{notebook_id}",
  "/api/v1/workspaces/{id}/notebooks/{notebook_id}/context",
  "/api/v1/workspaces/{id}/notebooks/{notebook_id}/source-unbindings",
  "/api/v1/organizations/{id}/license",
  "/api/v1/session",
  "/api/v1/session/logout",
  "/api/v1/session/native/logout",
  "/api/v1/session/step-up",
  "/api/v1/session/android/transactions",
  "/api/v1/session/android/authorize",
  "/api/v1/session/android/exchange",
  "/api/v1/session/oidc/transactions",
  "/api/v1/session/oidc/callback",
  "/api/v1/session/refresh",
  "/api/v1/session/revoke",
  "/api/v1/account/profile",
  "/api/v1/account/devices",
  "/api/v1/account/sessions",
  "/api/v1/devices/{id}/trust",
  "/api/v1/devices/{id}/revoke",
  "/api/v1/tenants",
  "/api/v1/workspaces",
  "/api/v1/workspaces/{id}/members",
  "/api/v1/workspaces/{id}/sources",
  "/api/v1/workspaces/{id}/website-imports",
  "/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-search",
  "/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-preview",
  "/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-imports",
  "/api/v1/workspaces/{id}/source-versions/{version_id}/law-snapshot",
  "/api/v1/workspaces/{id}/source-versions/{version_id}/website-snapshot",
  "/api/v1/workspaces/{id}/knowledge-scope",
  "/api/v1/workspaces/{id}/weight-profile",
  "/api/v1/workspaces/{id}/model-policy",
  "/api/v1/workspaces/{id}/model-defaults",
  "/api/v1/workspaces/{id}/egress-policy",
  "/api/v1/workspaces/{id}/questions/authorization",
  "/api/v1/organizations/{id}/egress-policy-versions",
  "/api/v1/workspaces/{id}/egress-policy-versions",
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
  "/api/v1/workspaces/{id}/processing-runs/{processing_run_id}",
  "/api/v1/workspaces/{id}/questions",
  "/api/v1/workspaces/{id}/citations/{citation_id}/content",
  "/api/v1/workspaces/{id}/studio/reports",
  "/api/v1/workspaces/{id}/studio/outputs",
  "/api/v1/sources/{id}/processing-runs",
  "/api/v1/sources/{id}/transcripts",
  "/api/v1/studio-generation-requests",
  "/api/v1/studio-outputs",
  "/api/v1/studio-outputs/{id}/versions",
  "/api/v1/studio-outputs/{id}/versions/{version_id}/exports/{format_name}",
  "/api/v1/reviews",
  "/api/v1/approval-requests",
  "/api/v1/workspaces/{id}/studio/approval-policy",
  "/api/v1/approval-requests/{id}/withdraw",
  "/api/v1/workspaces/{id}/studio/approval-notices",
  "/api/v1/workspaces/{id}/studio/approval-notices/{notification_id}",
  "/api/v1/approvals",
  "/api/v1/deliveries",
  "/api/v1/knowledge-registrations",
  "/api/v1/model-profiles",
  "/api/v1/model-profiles/{provider_code}/connection-check",
  "/api/v1/model-deployments",
  "/api/v1/admin/provider-connections",
  "/api/v1/admin/provider-connections/{connection_id}",
  "/api/v1/admin/provider-connections/{connection_id}/credential",
  "/api/v1/admin/provider-catalog/{connection_id}/refresh",
  "/api/v1/admin/provider-models/{connection_id}/{model_id}/capabilities",
  "/api/v1/model-routing/preview",
  "/api/v1/local-nodes",
  "/api/v1/model-installations",
  "/api/v1/connectors",
  "/api/v1/audit-events",
  "/api/v1/notifications",
  "/api/v1/notifications/{id}",
  "/api/v1/inbox",
  "/api/v1/workspaces/{id}/knowledge-packages",
  "/api/v1/workspaces/{id}/operations/status",
  "/api/v1/workspaces/{id}/output-version-settings",
  "/api/v1/workspaces/{id}/knowledge-packages/{package_id}/offline-copies",
  "/api/v1/offline-knowledge-copies/{copy_id}/content",
  "/api/v1/workspaces/{id}/sync-operations",
  "/api/v1/sync-operations/{id}",
  "/api/v1/sync-operations/{id}/approve",
  "/api/v1/sync-operations/{id}/transfer-batches",
  "/api/v1/sync-operations/{id}/conflicts/{conflict_id}/resolution",
  "/api/v1/sources/{id}/deletion-requests",
  "/api/v1/deletion-requests/{id}",
  "/api/v1/deletion-requests/{id}/cancel",
  "/api/v1/deletion-requests/{id}/purge",
  "/api/v1/sources/{id}/legal-holds",
  "/api/v1/legal-holds/{id}/release",
  "/api/v1/backups",
  "/api/v1/backups/selection-previews",
  "/api/v1/backups/{id}",
  "/api/v1/backups/{id}/restore-previews",
  "/api/v1/backups/{id}/restore-destinations",
  "/api/v1/restore-requests/{id}",
  "/api/v1/restore-requests/{id}/execute",
  "/api/v1/restore-requests/{id}/cancel"
]);

const HTTP_METHODS = new Set(["get", "post", "put", "patch", "delete"]);
const ANDROID_BROWSER_AUTH_POST_PATHS = new Set([
  "/api/v1/session/android/transactions",
  "/api/v1/session/android/authorize",
  "/api/v1/session/android/exchange",
]);
const COMMON_ERROR_STATUSES = ["400", "401", "403", "404", "409", "412", "500"];
const BODY_VERSIONED_MUTATIONS = new Set([
  "PATCH /api/v1/organization/tenants/{tenant_id}/members/{user_id}/role",
  "PATCH /api/v1/organization/tenants/{tenant_id}/members/{user_id}/state",
  "PATCH /api/v1/account/profile",
  "DELETE /api/v1/admin/provider-connections/{connection_id}",
  "PATCH /api/v1/admin/provider-models/{connection_id}/{model_id}/capabilities",
  "PATCH /api/v1/workspaces/{id}/studio/approval-policy",
]);
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
    const arrayIndex=Array.isArray(current)&&/^(?:0|[1-9][0-9]*)$/.test(token)&&Number.isSafeInteger(Number(token))&&Number(token)<current.length;
    if ((!isObject(current)&&!arrayIndex)||!Object.hasOwn(current,token)) fail(`missing $ref ${reference}`);
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

function requestSchema(document, operation) {
  const requestBody = operation.requestBody?.$ref
    ? resolveRef(document, operation.requestBody.$ref)
    : operation.requestBody;
  const schema = requestBody?.content?.["application/json"]?.schema;
  return schema?.$ref ? resolveRef(document, schema.$ref) : schema;
}

function validateResponse(document, response, operationId, status) {
  const resolved = responseObject(document, response);
  if (!isObject(resolved)) fail(`${operationId} response ${status} is invalid`);
  if (!isObject(resolved.headers) || !("X-Trace-Id" in resolved.headers)) {
    fail(`${operationId} response ${status} missing X-Trace-Id`);
  }
}

function validateSourceDeletionContract(document) {
  const schemas = document.components?.schemas;
  const schema = schemas?.DeletionCreateRequest;
  const { description: _description, ...body } = schema ?? {};
  const expected = { type: "object", additionalProperties: false, required: ["notebook_id", "step_up_authorization_id"], properties: {
    notebook_id: { $ref: "#/components/schemas/OpaqueId" },
    step_up_authorization_id: { type: "string", minLength: 1, maxLength: 4096, writeOnly: true },
  } };
  if (canonicalizeDocument(body) !== canonicalizeDocument(expected)) fail("S16 Source deletion exact confirmation body");
  const operation = document.paths["/api/v1/sources/{id}/deletion-requests"];
  if (JSON.stringify(Object.keys(operation ?? {}).filter(key => HTTP_METHODS.has(key))) !== '["post"]'
      || operation.post?.requestBody?.required !== true
      || operation.post?.requestBody?.content?.["application/json"]?.schema?.$ref !== "#/components/schemas/DeletionCreateRequest"
      || operation.post?.responses?.["403"]?.$ref !== "#/components/responses/Forbidden") fail("S16 Source deletion guarded POST");
  const states = schemas?.NotebookSelectedContext?.properties?.source_deletion_requests?.items?.properties?.state?.enum;
  if (JSON.stringify(states) !== JSON.stringify(["grace_period", "blocked_by_hold", "awaiting_ack", "cleanup_pending", "failed", "purged"])) {
    fail("S16 Source deletion exact durable projection states");
  }
}

function validateOpenLawContract(document) {
  const schemas=document.components.schemas,prefix='/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/';
  const check=(valid,label)=>{if(!valid)fail(`Open law contract ${label}`);};
  const same=(a,b)=>JSON.stringify(canonicalizeDocument(a))===JSON.stringify(canonicalizeDocument(b));
  const ref=name=>({$ref:`#/components/schemas/${name}`}),id=ref('OpaqueId');
  const object=properties=>({type:'object',additionalProperties:false,required:Object.keys(properties),properties});
  const digest={type:'string',pattern:'^[0-9a-f]{64}$'},date={type:'string',pattern:'^[0-9]{8}$'};
  const numeric={type:'string',pattern:'^[0-9]{1,20}$'},article={type:['string','null'],pattern:'^[0-9]{6}$'};
  const text={type:'string',minLength:1,maxLength:524288},stamp={type:'string',format:'date-time'};
  const law={mst:numeric,law_id:numeric,title:{type:'string',minLength:1,maxLength:300},effective_date:date,
    promulgation_date:{...date,type:['string','null']},ministry:{type:['string','null'],maxLength:200}};
  const selection={notebook_id:id,mst:numeric,effective_date:date,article_code:article};
  const envelope=data=>object({data,meta:object({trace_id:id})});
  const expected={
    OpenLawSearchRequest:object({notebook_id:id,query:{type:'string',minLength:1,maxLength:100},page:{type:'integer',minimum:1,maximum:100},public_query_confirmed:{type:'boolean',const:true}}),
    OpenLawPreviewRequest:object(selection),OpenLawImportRequest:object({...selection,expected_raw_sha256:digest,expected_text_sha256:digest}),
    OpenLawSearchItem:object(law),OpenLaw:object({...law,article_code:article}),
    OpenLawSearchEnvelope:envelope(object({items:{type:'array',maxItems:20,items:ref('OpenLawSearchItem')},page:{type:'integer',minimum:1,maximum:100},total_count:{type:'integer',minimum:0}})),
    OpenLawPreviewEnvelope:envelope(object({law:ref('OpenLaw'),text,raw_sha256:digest,text_sha256:digest,fetched_at:stamp,extraction_version:{type:'string',const:'open-law-text-v1'}})),
    OpenLawImportResult:object({source_id:id,source_version_id:id,snapshot_id:id,source_state:{type:'string',const:'ready'},replayed:{type:'boolean'}}),
    OpenLawImportEnvelope:envelope(object({kind:{type:'string',const:'open_law_import'},id,version:{type:'string',const:'1'},attributes:ref('OpenLawImportResult')})),
    OpenLawSnapshotView:object({snapshot_id:id,connector_id:{type:'string',const:'mcp-open-law-go-kr'},source_id:id,source_version_id:id,...law,article_code:article,
      origin_uri:{type:'string',format:'uri',maxLength:2048,pattern:'^https:\\/\\/www\\.law\\.go\\.kr/LSW/lsInfoP\\.do\\?lsiSeq=[0-9]{1,20}&efYd=[0-9]{8}$'},fetched_at:stamp,
      raw_sha256:digest,text_sha256:digest,extraction_version:{type:'string',const:'open-law-text-v1'},license_status:{type:'string',const:'unknown'}}),
    OpenLawSnapshotEnvelope:envelope(object({snapshot:ref('OpenLawSnapshotView'),text})),
  };
  for(const [name,value] of Object.entries(expected))check(same(schemas[name],value),`${name} exact schema mismatch`);
  for(const [tail,operationId,requestName,envelopeName] of [
    ['law-search','searchOpenLaw','OpenLawSearchRequest','OpenLawSearchEnvelope'],
    ['law-preview','previewOpenLaw','OpenLawPreviewRequest','OpenLawPreviewEnvelope'],
    ['law-imports','importOpenLaw','OpenLawImportRequest','OpenLawImportEnvelope'],
    ['law-snapshot','getOpenLawSnapshot',null,'OpenLawSnapshotEnvelope'],
  ]){
    const get=requestName===null,pathItem=document.paths[get?'/api/v1/workspaces/{id}/source-versions/{version_id}/law-snapshot':prefix+tail];
    check(same(Object.keys(pathItem??{}),[get?'get':'post']),`${tail} exact method mismatch`);
    const op=pathItem[get?'get':'post'];check(op.operationId===operationId,`${tail} operation mismatch`);
    const parameters=[{$ref:'#/components/parameters/ResourceId'},...(get?
      [{name:'version_id',in:'path',required:true,schema:id},{name:'notebook_id',in:'query',required:true,schema:id}]:
      tail==='law-imports'?[{$ref:'#/components/parameters/IdempotencyKey'}]:[])];
    check(same(op.parameters,parameters),`${tail} parameters mismatch`);
    check(get?!op.requestBody:same(op.requestBody,{required:true,content:{'application/json':{schema:ref(requestName)}}}),`${tail} body mismatch`);
    for(const status of tail==='law-imports'?['200','201']:['200'])check(same(responseObject(document,op.responses[status])?.content?.['application/json']?.schema,ref(envelopeName)),`${tail} response mismatch`);
  }
}

function validateWebsiteContract(document) {
  const imports = document.paths["/api/v1/workspaces/{id}/website-imports"];
  const snapshot = document.paths["/api/v1/workspaces/{id}/source-versions/{version_id}/website-snapshot"];
  const sources = document.paths["/api/v1/workspaces/{id}/sources"].get;
  const schemas = document.components.schemas;
  const check = (valid, label) => { if (!valid) fail(`Website contract ${label}`); };
  const keys = (value, expected) => isObject(value)
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());
  const exactObject = (schema, required, optional = []) => isObject(schema)
    && schema.type === "object" && schema.additionalProperties === false
    && Array.isArray(schema.required)
    && JSON.stringify([...schema.required].sort()) === JSON.stringify([...required].sort())
    && keys(schema.properties, [...required, ...optional]);
  const opaque = (value) => value?.$ref === "#/components/schemas/OpaqueId";
  const parameters = (operation, expected) => Array.isArray(operation?.parameters)
    && operation.parameters.length === expected.length && expected.every(([name, location, required]) => {
      const matches = operation.parameters.map(p => p.$ref ? resolveRef(document, p.$ref) : p)
        .filter(p => p.name === name && p.in === location);
      return matches.length === 1 && matches[0].required === required && opaque(matches[0].schema);
    });
  const responseSchema = (operation, status) => responseObject(document, operation?.responses?.[status])
    ?.content?.["application/json"]?.schema?.$ref;
  check(keys(imports, ["post"]) && keys(snapshot, ["get"]), "method mismatch");
  check(imports.post.operationId === "importWebsiteSource" && snapshot.get.operationId === "getWebsiteSnapshot", "operation mismatch");
  check(imports.post.requestBody?.required === true
    && imports.post.requestBody.content?.["application/json"]?.schema?.$ref === "#/components/schemas/WebsiteImportRequest", "import body mismatch");
  check(imports.post.parameters?.length === 2
    && parameterRefs(imports.post).has("#/components/parameters/ResourceId")
    && parameterRefs(imports.post).has("#/components/parameters/IdempotencyKey"), "import parameters mismatch");
  const input = schemas.WebsiteImportRequest;
  check(exactObject(input, ["notebook_id", "url", "public_page_confirmed"])
    && opaque(input.properties.notebook_id) && input.properties.url.type === "string"
    && input.properties.url.minLength === 1 && input.properties.url.maxLength === 2048 && input.properties.url.format === "uri"
    && input.properties.public_page_confirmed.type === "boolean" && input.properties.public_page_confirmed.const === true, "import input mismatch");
  check(["200", "201"].every(status => responseSchema(imports.post, status) === "#/components/schemas/WebsiteImportEnvelope"), "import response mismatch");
  const result = schemas.WebsiteImportResult;
  check(exactObject(result, ["snapshot_id", "source_id", "source_version_id", "source_state", "replayed"])
    && ["snapshot_id", "source_id", "source_version_id"].every(key => opaque(result.properties[key]))
    && result.properties.source_state.type === "string" && result.properties.source_state.const === "ready"
    && result.properties.replayed.type === "boolean", "import result mismatch");
  const envelope = schemas.WebsiteImportEnvelope;
  const traceMeta = (meta) => exactObject(meta, ["trace_id"]) && opaque(meta.properties.trace_id);
  check(exactObject(envelope, ["data", "meta"]) && traceMeta(envelope.properties.meta)
    && exactObject(envelope.properties.data, ["kind", "id", "version", "attributes"])
    && envelope.properties.data.properties.kind.const === "website_import"
    && envelope.properties.data.properties.version.const === "1" && opaque(envelope.properties.data.properties.id)
    && envelope.properties.data.properties.attributes.$ref === "#/components/schemas/WebsiteImportResult", "import envelope mismatch");
  check(parameters(snapshot.get, [["id", "path", true], ["version_id", "path", true], ["notebook_id", "query", true]])
    && responseSchema(snapshot.get, "200") === "#/components/schemas/WebsiteSnapshotEnvelope", "snapshot query/response mismatch");
  const fields = ["snapshot_id", "source_id", "source_version_id", "requested_url", "final_url", "title", "published_at", "fetched_at", "license_status", "license_url", "raw_sha256", "text_sha256", "extraction_version"];
  const view = schemas.WebsiteSnapshotView;
  check(exactObject(view, fields), "snapshot exact13 mismatch");
  const p = view.properties;
  check(["snapshot_id", "source_id", "source_version_id"].every(key => opaque(p[key]))
    && ["requested_url", "final_url"].every(key => p[key].type === "string" && p[key].format === "uri" && p[key].maxLength === 2048)
    && p.title.type === "string" && p.title.minLength === 1 && p.title.maxLength === 255
    && JSON.stringify(p.published_at.type) === JSON.stringify(["string", "null"]) && p.published_at.format === "date-time"
    && p.fetched_at.type === "string" && p.fetched_at.format === "date-time"
    && p.license_status.type === "string" && JSON.stringify(p.license_status.enum) === JSON.stringify(["unknown", "publisher_declared"])
    && JSON.stringify(p.license_url.type) === JSON.stringify(["string", "null"]) && p.license_url.format === "uri" && p.license_url.maxLength === 2048
    && ["raw_sha256", "text_sha256"].every(key => p[key].type === "string" && p[key].pattern === "^[0-9a-f]{64}$")
    && p.extraction_version.type === "string" && p.extraction_version.const === "website-text-v1", "snapshot fields mismatch");
  const snapshotEnvelope = schemas.WebsiteSnapshotEnvelope;
  check(exactObject(snapshotEnvelope, ["data", "meta"]) && traceMeta(snapshotEnvelope.properties.meta)
    && snapshotEnvelope.properties.data.$ref === "#/components/schemas/WebsiteSnapshotView", "snapshot envelope mismatch");
  check(parameters(sources, [["id", "path", true], ["notebook_id", "query", true], ["website_source_version_id", "query", false]]), "Source query mismatch");
  const projection = schemas.WorkspaceSourceProjection;
  check(exactObject(projection, ["source_id", "source_version_id", "filename", "source_state", "processing_state", "job_state"], ["website"])
    && projection.properties.website.$ref === "#/components/schemas/WebsiteSourceOriginal", "Source default6/optional mismatch");
  const original = schemas.WebsiteSourceOriginal;
  check(exactObject(original, [...fields, "text"])
    && fields.every(key => original.properties[key].$ref === `#/components/schemas/WebsiteSnapshotView/properties/${key}`)
    && original.properties.text.type === "string" && original.properties.text.minLength === 1
    && original.properties.text.maxLength === 524288, "Source original mismatch");
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
    TenantRole: ["personal_owner", "organization_admin"],
    WorkspaceRole: ["workspace_admin", "editor", "reviewer", "approver", "viewer"],
    RoleScope: ["tenant", "workspace"],
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
    "decision_id", "actor_id", "action", "resource_id", "workspace_id", "role_scope",
    "membership_version", "acl_version", "policy_version", "evaluated_at", "state",
    "reason_codes", "allowed_reference_ids", "masked_reference_ids",
    "allowed_segment_ids", "masked_segment_ids"
  ]) {
    if (!(decision?.required ?? []).includes(field)) fail(`AccessDecision missing ${field}`);
  }
  if (decision?.properties?.state?.$ref !== "#/components/schemas/AccessState") {
    fail("AccessDecision state must use AccessState");
  }
  if (!(schemas.AuthorizationEvaluation?.required ?? []).includes("role_scope")
      || schemas.AuthorizationEvaluation?.properties?.role_scope?.$ref !== "#/components/schemas/RoleScope") {
    fail("AuthorizationEvaluation must expose RoleScope");
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

function validateLineageContract(document) {
  const schemas=document.components.schemas;
  const check=(value,reason)=>{if(!value)fail(`R1-C lineage ${reason}`);};
  const same=(actual,expected)=>canonicalizeDocument(actual)===canonicalizeDocument(expected);
  const id=(maximum)=>({type:"string",minLength:1,maxLength:maximum,pattern:`^[A-Za-z0-9][A-Za-z0-9._:-]{0,${maximum-1}}(?![\\s\\S])`});
  const ids=(maximum,minimum=0)=>({type:"array",minItems:minimum,maxItems:100,uniqueItems:true,items:id(maximum)});
  const exact=(name,properties)=>check(same(schemas[name],{type:"object",additionalProperties:false,required:Object.keys(properties),properties}),`${name} exact shape`);
  const preview={workspace_id:id(128),notebook_id:id(128),source_version_ids:ids(128,1),lineage_mode:{type:"string",const:"exact"}};
  exact("LineageSelectionPreviewRequest",preview);
  exact("LineageClosureConsent",{include_prior_versions:{type:"boolean",const:true},closure_fingerprint:{type:"string",pattern:"^[0-9a-f]{64}$"}});
  exact("LineageBackupCreateRequest",{...preview,trigger:{type:"string",enum:["manual"]},closure_consent:{$ref:"#/components/schemas/LineageClosureConsent"}});
  exact("LineageSelectionPreview",{
    root_source_version_ids:ids(128,1),dependency_source_version_ids:ids(256),dependency_source_ids:ids(256),
    source_version_count:{type:"integer",minimum:1,maximum:100},source_count:{type:"integer",minimum:1,maximum:100},
    unique_object_count:{type:"integer",minimum:1,maximum:100},total_bytes:{type:"integer",minimum:0,maximum:536870912},
    metadata_bytes:{type:"integer",minimum:1,maximum:33554432},closure_fingerprint:{type:"string",pattern:"^[0-9a-f]{64}$"}
  });
  exact("LineageSelectionPreviewEnvelope",{data:{$ref:"#/components/schemas/LineageSelectionPreview"},meta:{
    type:"object",additionalProperties:false,required:["trace_id"],properties:{trace_id:{$ref:"#/components/schemas/OpaqueId"}}
  }});
  check(same(schemas.BackupCreateRequest,{oneOf:["BackupLegacyCreateRequest","BackupSelectionCreateRequest","LineageBackupCreateRequest"].map(name=>({$ref:`#/components/schemas/${name}`}))}),"three disjoint create branches");
  const path=document.paths["/api/v1/backups/selection-previews"],op=path?.post;
  check(same(Object.keys(path??{}),["post"])&&op.operationId==="previewLineageSelection","exact read POST");
  check(op.requestBody?.required===true&&requestSchema(document,op)===schemas.LineageSelectionPreviewRequest,"required exact request");
  check(same(op.parameters,[]),"preflight has no caller scope headers/query or idempotency write contract");
  check(op.responses?.["200"]?.$ref==="#/components/responses/LineageSelectionPreviewResponse","exact response");
  const response=document.components.responses.LineageSelectionPreviewResponse;
  check(response?.content?.["application/json"]?.schema?.$ref==="#/components/schemas/LineageSelectionPreviewEnvelope","exact envelope");
  check(Boolean(op.responses?.["413"])&&Boolean(op.responses?.["503"]),"limits and unavailable responses");
}

function validateRegisteredRestoreContract(document){
  const schemas=document.components.schemas;
  const check=(condition,why)=>{if(!condition)fail(`R4 registered restore ${why}`);};
  const same=(a,b)=>canonicalizeDocument(a)===canonicalizeDocument(b);
  const id={type:"string",minLength:1,maxLength:128,pattern:"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}(?![\\s\\S])"};
  const token={type:"string",minLength:1,maxLength:512,writeOnly:true};
  const time={type:"string",format:"date-time"},digest={type:"string",pattern:"^[0-9a-f]{64}$"};
  const exact=(name,properties)=>check(same(schemas[name],{type:"object",additionalProperties:false,required:Object.keys(properties),properties}),`${name} exact`);
  const ref=name=>({$ref:`#/components/schemas/${name}`});
  const count=minimum=>({type:"integer",minimum,maximum:100});
  exact("LegacyRestorePreviewRequest",{destination:ref("RestoreDestination"),step_up_authorization_id:token});
  exact("RegisteredRestorePreviewRequest",{workspace_id:id,destination_id:id,destination_version:{type:"integer",minimum:1,maximum:2147483647},step_up_authorization_id:token});
  exact("RestoreExecuteRequest",{preview_version:{type:"integer",minimum:1},step_up_authorization_id:token});
  check(same(schemas.RestorePreviewRequest,{oneOf:[ref("LegacyRestorePreviewRequest"),ref("RegisteredRestorePreviewRequest")]}),"disjoint preview bodies");
  exact("RegisteredDestinationSummary",{destination_id:id,version:{type:"integer",minimum:1,maximum:2147483647},display_name:{type:"string",minLength:1,maxLength:80},expires_at:time});
  exact("RegisteredDestinationsEnvelope",{data:{type:"array",maxItems:1000,items:ref("RegisteredDestinationSummary")},meta:ref("OrganizationMeta")});
  exact("LegacyRestorePreview",{version:{type:"integer",minimum:1},included_object_ids:{type:"array",uniqueItems:true,items:ref("OpaqueId")},
    excluded_object_ids:{type:"array",uniqueItems:true,items:ref("OpaqueId")},exclusion_reasons:{type:"array",items:{type:"array",minItems:2,maxItems:2,
      prefixItems:[ref("OpaqueId"),{type:"string",enum:["purged","held","tombstoned"]}]}},destination:ref("RestoreDestination"),created_at:time});
  exact("RegisteredRestorePreview",{kind:{type:"string",const:"selected_source_lineage"},version:{type:"integer",minimum:1},created_at:time,expires_at:time,
    destination:ref("RegisteredDestinationSummary"),source_manifest_digest:digest,closure_fingerprint:digest,included_root_count:count(1),excluded_root_count:count(0),
    exclusion_counts:{type:"object",additionalProperties:false,required:["purged","held","tombstoned"],properties:{purged:count(0),held:count(0),tombstoned:count(0)}}});
  const states=["requested","preview_ready","authorized","restoring","verifying","completed","failed","cancelled"];
  exact("RestoreRequestView",{request_id:ref("OpaqueId"),backup_id:ref("OpaqueId"),tenant_id:ref("OpaqueId"),workspace_id:ref("OpaqueId"),state:{type:"string",enum:states},
    version:{type:"integer",minimum:1},preview:{oneOf:[ref("LegacyRestorePreview"),ref("RegisteredRestorePreview")]},transitions:{type:"array",items:{type:"string",enum:states}},
    verification_digest:{oneOf:[digest,{type:"null"}]}});
  exact("RestoreEnvelope",{data:ref("RestoreRequestView"),meta:ref("OrganizationMeta")});
  const path=document.paths["/api/v1/backups/{id}/restore-destinations"],op=path?.get;
  check(same(Object.keys(path??{}),["get"])&&op?.operationId==="listRestoreDestinations","exact GET literal");
  check(same(op.parameters,[{$ref:"#/components/parameters/ResourceId"}]),"no caller registry query/body");
  check(!op.requestBody&&op.responses?.["200"]?.$ref==="#/components/responses/RegisteredDestinationsResponse"&&Boolean(op.responses["503"]),"destination response/off");
  check(document.components.responses.RegisteredDestinationsResponse?.content?.["application/json"]?.schema?.$ref==="#/components/schemas/RegisteredDestinationsEnvelope","safe4 envelope");
  check(document.components.responses.RestoreResponse?.content?.["application/json"]?.schema?.$ref==="#/components/schemas/RestoreEnvelope","closed restore response");
  check(requestSchema(document,document.paths["/api/v1/backups/{id}/restore-previews"].post)===schemas.RestorePreviewRequest,"preview request binding");
}

function validateIdentityContract(document) {
  const schemas = document.components?.schemas ?? {};
  const recoveryOperations = [
    "cloud_backup_create",
    "cloud_backup_get",
    "cloud_backup_list",
    "cloud_restore_cancel",
    "cloud_restore_execute",
    "cloud_restore_get",
    "cloud_restore_preview"
  ];
  for (const name of [
    "IdentitySession", "NativeLocalLoginRequest", "NativeCredentialSession",
    "OidcLoginStartRequest", "OidcLoginStart",
    "OidcCallbackRequest", "NativeRefreshRequest", "StepUpAuthorizationRequest",
    "StepUpAuthorization", "DeviceRevokeRequest", "DeviceRevocation",
    "SessionRevokeRequest", "SessionRevocation", "CurrentSessionLogout"
  ]) {
    if (!isObject(schemas[name])) fail(`missing identity schema ${name}`);
  }
  if (schemas.OidcLoginStart?.properties?.code_challenge_method?.const !== "S256") {
    fail("OIDC start must use PKCE S256");
  }
  if (schemas.StepUpAuthorizationRequest?.properties?.ttl_seconds?.maximum !== 600) {
    fail("Step-up TTL maximum must be 600 seconds");
  }
  if (
    !schemas.StepUpAuthorizationRequest?.required?.includes("password")
    || schemas.StepUpAuthorizationRequest?.properties?.password?.writeOnly !== true
    || schemas.StepUpAuthorizationRequest?.properties?.password?.minLength !== 12
    || schemas.StepUpAuthorizationRequest?.properties?.password?.maxLength !== 1024
  ) fail("Step-up must require an approved writeOnly reauthentication password");
  if (schemas.NativeRefreshRequest?.properties?.refresh_credential?.writeOnly !== true) {
    fail("Native refresh credential must be writeOnly");
  }
  const identitySession = schemas.IdentitySession;
  if (
    identitySession?.type !== "object" || identitySession.additionalProperties !== false
    || JSON.stringify(identitySession.required) !== JSON.stringify([
      "user_id", "tenant_id", "workspace_id", "session_id", "device_id", "client_kind",
      "delivery", "expires_at", "recovery_operations"
    ])
    || identitySession.properties?.workspace_id?.$ref !== "#/components/schemas/OpaqueId"
    || identitySession.properties?.recovery_operations?.type !== "array"
    || identitySession.properties?.recovery_operations?.uniqueItems !== true
    || identitySession.properties?.recovery_operations?.items?.type !== "string"
    || JSON.stringify(identitySession.properties?.recovery_operations?.items?.enum)
      !== JSON.stringify(recoveryOperations)
  ) {
    fail("Identity session recovery operation projection must use the exact safe allowlist");
  }
  if (
    document.paths?.["/api/v1/session"]?.get?.responses?.["503"]?.$ref
      !== "#/components/responses/ServiceUnavailable"
  ) {
    fail("GET /api/v1/session must declare ServiceUnavailable 503");
  }
  const nativeRefresh = schemas.NativeRefreshRequest;
  if (
    nativeRefresh?.type !== "object" || nativeRefresh.additionalProperties !== false
    || JSON.stringify(nativeRefresh.required) !== JSON.stringify(["refresh_credential"])
    || JSON.stringify(Object.keys(nativeRefresh.properties ?? {}).sort()) !== JSON.stringify(["refresh_credential"])
    || nativeRefresh.properties?.refresh_credential?.writeOnly !== true
    || "default" in nativeRefresh.properties?.refresh_credential
    || "example" in nativeRefresh.properties?.refresh_credential
  ) {
    fail("Native refresh must expose only the approved writeOnly refresh credential input");
  }
  const nativeLogin = schemas.NativeLocalLoginRequest;
  if (
    nativeLogin?.type !== "object" || nativeLogin.additionalProperties !== false
    || JSON.stringify(nativeLogin.required) !== JSON.stringify(["login_id", "password"])
    || JSON.stringify(Object.keys(nativeLogin.properties ?? {}).sort()) !== JSON.stringify(["login_id", "password"])
    || nativeLogin.properties?.password?.writeOnly !== true
    || "default" in nativeLogin.properties?.password || "example" in nativeLogin.properties?.password
  ) {
    fail("Native local login must expose only the approved writeOnly password input");
  }
  const nativeCredential = schemas.NativeCredentialSession;
  if (
    nativeCredential?.properties?.client_kind?.const !== "native"
    || nativeCredential?.properties?.delivery?.const !== "native_https_opaque_bearer"
    || nativeCredential?.properties?.access_credential?.writeOnly !== true
    || nativeCredential?.properties?.refresh_credential?.writeOnly !== true
    || "example" in nativeCredential.properties?.access_credential
    || "default" in nativeCredential.properties?.access_credential
    || "example" in nativeCredential.properties?.refresh_credential
    || "default" in nativeCredential.properties?.refresh_credential
  ) {
    fail("Native credential session must retain opaque credential boundaries");
  }
  if (schemas.DeviceRevokeRequest?.properties?.step_up_authorization?.writeOnly !== true) {
    fail("Device revoke Step-up value must be writeOnly");
  }
  if (schemas.SessionRevokeRequest?.properties?.step_up_authorization?.writeOnly !== true) {
    fail("Session revoke Step-up value must be writeOnly");
  }
  const currentLogout = schemas.CurrentSessionLogout;
  if (
    currentLogout?.type !== "object" || currentLogout.additionalProperties !== false
    || JSON.stringify(currentLogout.required) !== JSON.stringify(["status", "replayed"])
    || currentLogout.properties?.status?.const !== "logged_out"
    || currentLogout.properties?.replayed?.type !== "boolean"
  ) fail("Current session logout must expose only the safe idempotent result");
  const requiredOperations = {
    "/api/v1/auth/native/login": ["post", "NativeCredentialSessionResponse"],
    "/api/v1/session": ["get", "IdentitySessionResponse"],
    "/api/v1/session/logout": ["post", "CurrentSessionLogoutResponse"],
    "/api/v1/session/native/logout": ["post", "NativeSelfLogoutResponse"],
    "/api/v1/session/step-up": ["post", "StepUpAuthorizationResponse"],
    "/api/v1/session/oidc/transactions": ["post", "OidcLoginStartResponse"],
    "/api/v1/session/oidc/callback": ["post", "IdentitySessionResponse"],
    "/api/v1/session/refresh": ["post", "NativeCredentialSessionResponse"],
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

function validateLicenseContract(document) {
  const schemas = document.components?.schemas ?? {};
  const view = schemas.LicenseView;
  const required = [
    "product", "edition", "license_id_hint", "issued_at", "expires_at", "status",
    "features", "resources", "warning", "creation_allowed", "existing_read_allowed",
    "existing_export_allowed", "can_apply"
  ];
  if (view?.type !== "object" || view.additionalProperties !== false) fail("LicenseView must be an exact safe projection");
  for (const field of required) if (!(view?.required ?? []).includes(field)) fail(`LicenseView missing ${field}`);
  for (const forbidden of ["signature", "claims", "claims_digest", "signing_key_id", "organization_id"]) {
    if (forbidden in (view?.properties ?? {})) fail(`LicenseView exposes forbidden ${forbidden}`);
  }
  const apply = schemas.LicenseApplyRequest;
  if (
    apply?.type !== "object" || apply.additionalProperties !== false
    || JSON.stringify(apply.required) !== JSON.stringify(["document", "step_up_authorization_id"])
    || apply.properties?.document?.writeOnly !== true
    || apply.properties?.step_up_authorization_id?.writeOnly !== true
  ) fail("LicenseApplyRequest must keep signed document and Step-up writeOnly");
  const operations = {
    "/api/v1/workspaces/{id}/license": ["get", "LicenseResponse"],
    "/api/v1/organizations/{id}/license": ["post", "LicenseResponse"]
  };
  for (const [apiPath, [method, responseName]] of Object.entries(operations)) {
    const operation = document.paths?.[apiPath]?.[method];
    if (!isObject(operation)) fail(`missing license operation ${method.toUpperCase()} ${apiPath}`);
    const success = operation.responses?.[method === "post" ? "201" : "200"];
    if (success?.$ref !== `#/components/responses/${responseName}`) fail(`license response mismatch ${apiPath}`);
  }
}

function validateStudioApprovalLifecycle(document) {
  for(const code of ['APPROVAL_REQUEST_EXPIRED','APPROVAL_REQUEST_PENDING','APPROVAL_REQUEST_REQUIRED','PRECONDITION_FAILED']) {
    if(!document.components?.schemas?.SafeErrorCode?.enum?.includes(code))fail(`Studio approval lifecycle missing safe code ${code}`);
  }
  const ref=name=>({$ref:`#/components/schemas/${name}`}), id=ref('OpaqueId');
  const time={type:'string',format:'date-time'}, nullableTime={type:['string','null'],format:'date-time'};
  const exact=properties=>({type:'object',additionalProperties:false,required:Object.keys(properties),properties});
  const expected={
    StudioApprovalPolicy:exact({ttl_days:{type:'integer',minimum:1,maximum:30},policy_version:{type:'integer',minimum:0},can_manage:{type:'boolean'},step_up_target_id:{type:'string',pattern:'^studio-approval-policy-[a-f0-9]{64}$'}}),
    StudioApprovalPolicyRequest:exact({ttl_days:{type:'integer',minimum:1,maximum:30},expected_policy_version:{type:'integer',minimum:0},step_up_authorization:{type:'string',minLength:1,maxLength:4096}}),
    StudioApprovalWithdrawRequest:exact({workspace_id:id,notebook_id:id,output_version_id:id}),
    StudioApprovalWithdrawReceipt:exact({record_id:id,action:{type:'string',enum:['withdraw']},status:{type:'string',enum:['withdrawn']},output_version_id:id}),
    StudioApprovalNotice:exact({notification_id:id,approval_request_id:id,output_version_id:id,event_kind:{type:'string',enum:['due_24h','expired','withdrawn']},scheduled_at:time,created_at:time,read_at:nullableTime,version:{type:'integer',minimum:1}}),
    StudioApprovalNotices:exact({items:{type:'array',maxItems:100,items:ref('StudioApprovalNotice')},unread_count:{type:'integer',minimum:0,maximum:100}}),
    StudioApprovalNoticeReadRequest:exact({notebook_id:id,state:{type:'string',enum:['read']}}),
    StudioApprovalNoticeReadReceipt:exact({notification_id:id,version:{type:'integer',minimum:2},read_at:time}),
  };
  const equal=(actual,want,label)=>{if(canonicalizeDocument(actual)!==canonicalizeDocument(want))fail(`Studio approval lifecycle ${label} mismatch`);};
  for(const [name,schema] of Object.entries(expected))equal(document.components?.schemas?.[name],schema,name);
  const param=name=>({$ref:`#/components/parameters/${name}`});
  const rows=[
    ['/api/v1/workspaces/{id}/studio/approval-policy','get','StudioApprovalPolicy',null,[param('ResourceId')]],
    ['/api/v1/workspaces/{id}/studio/approval-policy','patch','StudioApprovalPolicy','StudioApprovalPolicyRequest',[param('ResourceId'),param('IdempotencyKey')]],
    ['/api/v1/approval-requests/{id}/withdraw','post','StudioApprovalWithdrawReceipt','StudioApprovalWithdrawRequest',[param('ResourceId'),param('IdempotencyKey')]],
    ['/api/v1/workspaces/{id}/studio/approval-notices','get','StudioApprovalNotices',null,[param('ResourceId'),{name:'notebook_id',in:'query',required:true,schema:id}]],
    ['/api/v1/workspaces/{id}/studio/approval-notices/{notification_id}','patch','StudioApprovalNoticeReadReceipt','StudioApprovalNoticeReadRequest',[param('ResourceId'),{name:'notification_id',in:'path',required:true,schema:id},param('IfMatch'),param('IdempotencyKey')]],
  ];
  for(const p of new Set(rows.map(row=>row[0])))equal(Object.keys(document.paths[p]??{}),rows.filter(row=>row[0]===p).map(row=>row[1]),p+' methods');
  for(const [p,method,result,body,parameters] of rows){
    const operation=document.paths[p][method],mutating=method!=='get';
    equal(operation.parameters,parameters,p+' parameters');
    equal(operation.requestBody,body?{required:true,content:{'application/json':{schema:ref(body)}}}:undefined,p+' body');
    const name=result+(mutating?'MutationEnvelope':'Envelope');
    equal(operation.responses?.['200']?.content?.['application/json']?.schema,ref(name),p+' response');
    equal(document.components.schemas[name],exact({data:ref(result),meta:exact({trace_id:{type:'string'},workspace_id:id,...(mutating?{replayed:{type:'boolean'}}:{})})}),name);
  }
  const version=document.components.schemas.StudioVersionProjection;
  equal(version.required,['output_version_id','content_version','previous_version_id','status','content','revision_type','change_reason','settings_snapshot_id','citations','review_request_id','approval_request_id','approval_id','delivery_id','knowledge_registration_id','output_format','approval_request_state','approval_expires_at'],'Version fields');
  equal(Object.keys(version.properties),version.required,'Version properties');
  equal(version.properties.approval_request_state,{type:['string','null'],enum:['pending','approved','rejected','expired','withdrawn',null]},'Version request state');
  equal(version.properties.approval_expires_at,nullableTime,'Version deadline');
}

function validateAccountSettings(document) {
  const schemas = document.components?.schemas ?? {};
  const exactSchema = (name, fields) => {
    const schema = schemas[name];
    if (schema?.type !== "object" || schema.additionalProperties !== false
        || JSON.stringify(Object.keys(schema.properties ?? {}).sort()) !== JSON.stringify([...fields].sort())
        || JSON.stringify([...(schema.required ?? [])].sort()) !== JSON.stringify([...fields].sort())) fail(`S36 exact schema ${name}`);
    return schema;
  };
  exactSchema("AccountMeta", ["trace_id"]);
  exactSchema("AccountProfile", ["display_name", "version"]);
  exactSchema("AccountProfileMutation", ["display_name", "version", "replayed"]);
  exactSchema("AccountProfilePatch", ["display_name", "expected_version"]);
  exactSchema("AccountDevice", ["device_id", "platform", "state", "last_seen_at", "is_current"]);
  exactSchema("AccountSession", ["session_id", "device_id", "client_kind", "state", "expires_at", "is_current"]);
  for (const [path, methods, envelopeName] of [["profile", ["get", "patch"], "AccountProfileEnvelope"], ["devices", ["get"], "AccountDevicesEnvelope"], ["sessions", ["get"], "AccountSessionsEnvelope"]]) {
    const route = document.paths?.[`/api/v1/account/${path}`];
    if (JSON.stringify(Object.keys(route ?? {}).sort()) !== JSON.stringify(methods)) fail(`S36 methods ${path}`);
    if (route.get.responses?.["200"]?.content?.["application/json"]?.schema?.$ref !== `#/components/schemas/${envelopeName}`) fail(`S36 response ${path}`);
    const parameters = route.get.parameters ?? [];
    if (path === "profile" ? parameters.length !== 0 : JSON.stringify(parameters.map((p) => [p.name, p.in]).sort()) !== JSON.stringify([["cursor", "query"], ["limit", "query"]])) fail(`S36 own scope ${path}`);
    if (path !== "profile") {
      const limit = parameters.find((p) => p.name === "limit")?.schema;
      if (limit?.type !== "integer" || limit.minimum !== 1 || limit.maximum !== 100 || limit.default !== 20) fail(`S36 pagination ${path}`);
    }
  }
  if (schemas.AccountDevice.properties.last_seen_at.format !== "date-time" || schemas.AccountSession.properties.expires_at.format !== "date-time") fail("S36 RFC3339 projections");
  if (schemas.AccountProfilePatch.properties.display_name.maxLength !== 80 || schemas.AccountProfilePatch.properties.display_name.minLength !== 1
      || schemas.AccountProfilePatch.properties.expected_version.minimum !== 0 || schemas.AccountProfileMutation.properties.replayed.type !== "boolean") fail("S36 profile bounds");
  const patch = document.paths["/api/v1/account/profile"].patch;
  if (!parameterRefs(patch).has("#/components/parameters/IdempotencyKey") || requestSchema(document, patch) !== schemas.AccountProfilePatch
      || patch.responses?.["200"]?.content?.["application/json"]?.schema?.$ref !== "#/components/schemas/AccountProfileMutationEnvelope") fail("S36 profile mutation");
  const expectedSession = document.components?.parameters?.AccountExpectedSessionId;
  if (!parameterRefs(patch).has("#/components/parameters/AccountExpectedSessionId")
      || expectedSession?.name !== "X-Daon-Expected-Session-Id" || expectedSession.in !== "header" || expectedSession.required !== true
      || expectedSession.schema?.$ref !== "#/components/schemas/OpaqueId" || !patch.responses?.["422"] || !patch.responses?.["409"]) fail("S36 required profile session binding");
  for (const [path, route] of Object.entries(document.paths ?? {})) for (const [method, operation] of Object.entries(route)) {
    if (path === "/api/v1/account/profile" && method === "patch") continue;
    if ((operation?.parameters ?? []).some((parameter) => parameter.$ref === "#/components/parameters/AccountExpectedSessionId"
      || parameter.name?.toLowerCase() === "x-daon-expected-session-id")) fail("S36 session binding must be profile PATCH only");
  }
  for (const [name, dataRef] of [["AccountProfileEnvelope", "AccountProfile"], ["AccountProfileMutationEnvelope", "AccountProfileMutation"], ["AccountDevicesEnvelope", "AccountDevicesPage"], ["AccountSessionsEnvelope", "AccountSessionsPage"]]) {
    const schema = exactSchema(name, ["data", "meta"]);
    if (schema.properties.data.$ref !== `#/components/schemas/${dataRef}` || schema.properties.meta.$ref !== "#/components/schemas/AccountMeta") fail(`S36 envelope ${name}`);
  }
  for (const [name, item] of [["AccountDevicesPage", "AccountDevice"], ["AccountSessionsPage", "AccountSession"]]) {
    const page = exactSchema(name, ["items", "next_cursor"]);
    if (page.properties.items.items?.$ref !== `#/components/schemas/${item}` || page.properties.items.maxItems !== 100
        || JSON.stringify(page.properties.next_cursor.type) !== JSON.stringify(["string", "null"])) fail(`S36 page ${name}`);
  }
}

function validateOrganizationContract(document) {
  const base = "/api/v1/organization/tenants/{tenant_id}";
  const paths = {
    "/api/v1/organization/admin/tenants": ["get"], [base+"/workspaces"]: ["get"],
    [base+"/invitations"]: ["get", "post"], [base+"/invitations/{invitation_id}"]: ["delete"],
    [base+"/members/{user_id}/role"]: ["patch"], [base+"/members/{user_id}/state"]: ["patch"],
    "/api/v1/organization/join-requests": ["get", "post"], "/api/v1/organization/join-requests/{request_id}/decision": ["post"],
  };
  const same = (actual, expected) => canonicalizeDocument(actual) === canonicalizeDocument(expected);
  const schemas = document.components?.schemas ?? {};
  const fields = (name, names, required = names) => {
    const value = schemas[name];
    if (value?.type !== "object" || value.additionalProperties !== false
      || !same(Object.keys(value.properties ?? {}).sort(), [...names].sort())
      || !same([...(value.required ?? [])].sort(), [...required].sort())) fail(`S39 exact schema ${name}`);
  };
  fields("OrganizationInvitation", ["invitation_id", "tenant_id", "workspace_id", "created_by", "expires_at", "max_uses", "used_count", "state", "version", "created_at", "updated_at"]);
  fields("OrganizationMember", ["tenant_id", "user_id", "role", "state", "version", "updated_at", "join_workspace_id", "access_role", "access_state", "access_version"]);
  fields("OrganizationInvitationCreate", ["workspace_id", "code", "expires_at", "max_uses"]);
  fields("OrganizationRolePatch", ["role", "workspace_id", "expected_version", "expected_auth_version", "reason"], ["role", "workspace_id", "expected_version", "expected_auth_version"]);
  fields("OrganizationStatePatch", ["expected_version", "reason"], ["expected_version"]);
  fields("OrganizationJoinCreate", ["invitation_code"]);
  if (!same(schemas.OrganizationJoinCreate.properties.invitation_code, { type: "string", minLength: 1, maxLength: 160, writeOnly: true })) fail("S40 exact invitation-only input");
  for (const name of ["OrganizationManagedTenant", "OrganizationManagedWorkspace"]) fields(name, [name.endsWith("Tenant") ? "tenant_id" : "workspace_id", "display_name"]);
  if (!same(schemas.OrganizationRolePatch.properties.role.enum, ["viewer", "editor", "reviewer", "approver"])) fail("S39 role enum");
  for (const name of ["expected_version", "expected_auth_version"]) {
    if (!same(schemas.OrganizationRolePatch.properties[name], { type: "integer", minimum: 1 })) fail("S39 role version");
  }
  for (const name of ["created_at", "updated_at", "expires_at"]) if (!same(schemas.OrganizationInvitation.properties[name], { type: "string", format: "date-time" })) fail("S39 invitation UTC datetime");
  if (schemas.OrganizationInvitationCreate.properties.code.writeOnly !== true
    || schemas.OrganizationInvitationCreate.properties.max_uses.maximum !== 1000000) fail("S39 invitation input bounds");
  const expectedHeader = document.components?.parameters?.OrganizationExpectedSessionId;
  if (expectedHeader?.name !== "X-Daon-Expected-Session-Id" || expectedHeader.in !== "header" || expectedHeader.required !== true
    || expectedHeader.schema?.$ref !== "#/components/schemas/OpaqueId") fail("S39 session header");
  for (const [path, methods] of Object.entries(paths)) {
    if (!same(Object.keys(document.paths?.[path] ?? {}).filter(key => HTTP_METHODS.has(key)), methods)) fail(`S39 exact methods ${path}`);
    for (const method of methods) {
      const operation = document.paths[path][method];
      const parameters = operation.parameters ?? [];
      if (method === "get" && parameters.some(parameter => parameter.in !== "path")) fail("S39 GET query forbidden");
      if (method !== "get" && ["#/components/parameters/IdempotencyKey", "#/components/parameters/OrganizationExpectedSessionId"].some(reference => !parameterRefs(operation).has(reference))) fail("S39 required mutation headers");
      if (method === "delete") {
        const query = parameters.filter(parameter => parameter.in === "query");
        if (!same(query, [{ name: "expected_version", in: "query", required: true, schema: { type: "integer", minimum: 1 } }])) fail("S39 exact revoke version query");
      }
    }
  }
}

function validateSourceReprocessingContract(document){
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),require=ok=>{if(!ok)fail("R4 Source reprocessing exact contract");};
  const op=document.paths?.["/api/v1/sources/{id}/processing-runs"]?.post,s=document.components?.schemas;
  require(same(op?.requestBody,{required:true,content:{"application/json":{schema:{$ref:"#/components/schemas/SourceReprocessingMutation"}}}}));
  require(op.responses?.["201"]?.content?.["application/json"]?.schema?.$ref==="#/components/schemas/SourceReprocessingEnvelope");
  const ids=["workspace_id","notebook_id","source_version_id"],mode={type:"string",enum:["auto","local_only","pinned"]};
  require(same(s.SourceReprocessingMutation,{type:"object",additionalProperties:false,required:["kind","attributes"],properties:{
    kind:{const:"processing_run"},step_up_authorization_id:{type:"null"},
    attributes:{type:"object",additionalProperties:false,required:ids,properties:{
      ...Object.fromEntries(ids.map(id=>[id,{$ref:"#/components/schemas/OpaqueId",maxLength:128}])),processing_mode:mode}}}}));
  const fields=["processing_run_id","source_id","source_version_id","retry_of_processing_run_id","processing_state","source_state","job_state","replayed"];
  const expected={type:"object",additionalProperties:false,required:fields,properties:{
    ...Object.fromEntries(fields.slice(0,4).map(id=>[id,{$ref:"#/components/schemas/OpaqueId"}])),
    processing_state:{enum:["vision_llm_understanding","speech_to_text"]},source_state:{const:"processing"},job_state:{const:"pending"},
    replayed:{type:"boolean"},processing_mode:mode,auto_requeue_configured:{type:"boolean"}},
    dependentRequired:{processing_mode:["auto_requeue_configured"],auto_requeue_configured:["processing_mode"]},
    allOf:[{if:{required:["processing_mode"],properties:{processing_mode:{const:"pinned"}}},then:{properties:{auto_requeue_configured:{const:false}}}}]};
  require(same(s.SourceReprocessingResult,expected));
  require(same(s.SourceReprocessingEnvelope,{type:"object",additionalProperties:false,required:["data","trace_id"],properties:{
    data:{type:"object",additionalProperties:false,required:["id","kind","version","attributes"],properties:{
      id:{$ref:"#/components/schemas/OpaqueId"},kind:{const:"processing_run"},version:{const:"2"},attributes:{$ref:"#/components/schemas/SourceReprocessingResult"}}},
    trace_id:{$ref:"#/components/schemas/OpaqueId"}}}));
}

function validateOperationsCapacityContract(document){
  const s=document.components?.schemas,same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const require=ok=>{if(!ok)fail('S44 host capacity exact opt-in contract');};
  const ref=x=>({$ref:'#/components/schemas/'+x});
  const host=[['cpu_logical_millicores','millicores',1000,32768000],['cpu_usage_basis_points','basis_points',0,10000],
    ['memory_total_bytes','bytes',1,Number.MAX_SAFE_INTEGER],['memory_available_bytes','bytes',0,Number.MAX_SAFE_INTEGER]];
  const properties={state:{enum:['fresh','stale','not_collected','unavailable']},observed_at:ref('SystemObservationTime'),
    received_at:ref('SystemObservationTime'),safe_code:{type:'string'},worker_kind:{const:'server_host'},
    metric_id:{enum:host.map(r=>r[0])},value:{type:['integer','null'],minimum:0,maximum:Number.MAX_SAFE_INTEGER},
    unit:{enum:['millicores','basis_points','bytes']}};
  const rules=[ref('SystemObservationTiming'),
    {if:{properties:{state:{enum:['not_collected','unavailable']}}},then:{properties:{value:{type:'null'}}},else:{properties:{value:{type:'integer'}}}},
    {oneOf:host.map(([metric,unit,minimum,maximum],i)=>({properties:{metric_id:{const:metric},unit:{const:unit},
      value:{type:['integer','null'],minimum,maximum,...(i===0?{multipleOf:1000}:{})}}}))}];
  require(same(s.SystemHostCapacitySample,{type:'object',additionalProperties:false,
    required:['state','observed_at','received_at','safe_code','worker_kind','metric_id','value','unit'],properties,allOf:rules}));
  require(same(s.WorkspaceHostCapacitySample,{type:'object',additionalProperties:false,
    required:['state','observed_at','received_at','coverage','safe_code','worker_kind','metric_id','value','unit'],
    properties:{...Object.fromEntries(Object.keys(properties).map(k=>[k,ref('SystemHostCapacitySample/properties/'+k)])),coverage:{const:'server_host_observation'}},
    allOf:[0,1,2].map(i=>ref('SystemHostCapacitySample/allOf/'+i))}));
  const capacity=(base,resource)=>({type:'array',minItems:16,maxItems:16,items:false,prefixItems:[
    ...Array.from({length:12},(_,i)=>ref(base+'/properties/resource_samples/prefixItems/'+i)),
    ...host.map(([metric,unit])=>({allOf:[ref(resource),{properties:{worker_kind:{const:'server_host'},metric_id:{const:metric},unit:{const:unit}}}]}))]});
  for(const [name,base,readiness] of [['SystemOperationsStatusWithCapacity','SystemOperationsStatus',false],
    ['OperationsStatusWithCapacity','OperationsStatus',false],['OperationsStatusWithReadinessCapacity','OperationsStatus',true]]){
    require(same(s[name],{type:'object',additionalProperties:false,required:s[base].required,
      properties:Object.fromEntries(s[base].required.map(k=>[k,k==='resource_samples'
        ?capacity(base,base==='SystemOperationsStatus'?'SystemHostCapacitySample':'WorkspaceHostCapacitySample')
        :ref((k==='event_counts'&&readiness?'OperationsStatusWithReadiness':base)+'/properties/'+k)]))}));
  }
  for(const [name,data,base] of [['SystemOperationsStatusCapacityEnvelope','SystemOperationsStatusWithCapacity','SystemOperationsStatusEnvelope'],
    ['OperationsStatusCapacityEnvelope','OperationsStatusWithCapacity','OperationsStatusEnvelope'],
    ['OperationsStatusReadinessCapacityEnvelope','OperationsStatusWithReadinessCapacity','OperationsStatusEnvelope']]){
    require(same(s[name],{type:'object',additionalProperties:false,required:['data','meta'],
      properties:{data:ref(data),meta:ref(base+'/properties/meta')}}));
  }
}

function validateWorkspaceOperationsContract(document){
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),require=ok=>{if(!ok)fail("M2 workspace operations exact contract");};
  const s=document.components?.schemas,op=document.paths?.["/api/v1/workspaces/{id}/operations/status"]?.get;
  require(op?.parameters?.length===4&&op.parameters[0].$ref==="#/components/parameters/ResourceId"&&!op.requestBody);
  require(same(op.parameters[1],{name:"scope",in:"query",required:false,schema:{type:"string",enum:["self","workspace_visible"],default:"self"}}));
  require(same(op.parameters[2],{name:"include_readiness",in:"query",required:false,schema:{type:"string",enum:["1"]}}));
  require(same(op.parameters[3],{name:'include_capacity',in:'query',required:false,schema:{type:'string',enum:['1']}}));
  require(same(op.responses?.["200"]?.content?.["application/json"]?.schema,{oneOf:[
    {$ref:"#/components/schemas/OperationsStatusEnvelope"},{$ref:"#/components/schemas/OperationsStatusReadinessEnvelope"},
    {$ref:'#/components/schemas/OperationsStatusCapacityEnvelope'},{$ref:'#/components/schemas/OperationsStatusReadinessCapacityEnvelope'}]}));
  const exact=(name,keys)=>{const r=s?.[name];require(r?.type==="object"&&r.additionalProperties===false&&same(r.required,keys)&&same(Object.keys(r.properties).sort(),[...keys].sort()));return r;};
  const common=["state","observed_at","received_at","coverage","safe_code"];
  const data=exact("OperationsStatus",["workspace_id","overall_status","checked_at","components","observations","worker_samples","resource_samples","event_counts","cost_summary","deployment_summary","unimplemented"]);
  const worker=exact("WorkspaceWorkerSample",[...common,"worker_kind","process_state","poll_started_at","poll_finished_at","active_started_at"]);
  const resource=exact("WorkspaceResourceSample",[...common,"worker_kind","metric_id","value","unit"]);
  const event=exact("WorkspaceEventCount",[...common,"metric_id","value","unit","window_start","window_end"]);
  const cost=exact("WorkspaceCostSummary",[...common,"known_amounts","unknown_attempt_count","unconfirmed_run_count","coverage_start","coverage_end"]);
  const deployment=exact("WorkspaceDeploymentSummary",[...common,"operation","code_revision","image_digest","previous_image_digest","result"]);
  for(const [row,coverage] of [[worker,"workspace_operational_counts"],[resource,"workspace_operational_counts"],[event,"actor_accessible_records"],[cost,"actor_accessible_records"],[deployment,"actor_accessible_records"]]){
    require(row.properties.coverage.const===coverage&&row.allOf?.[0]?.$ref==="#/components/schemas/SystemObservationTiming");
  }
  require(same(worker.properties.state.enum,["fresh","stale","not_collected","unavailable"])&&same(resource.properties.state.enum,worker.properties.state.enum));
  for(const row of [resource,event])require(same(row.properties.value.type,["integer","null"])&&row.properties.value.minimum===0&&row.properties.value.maximum===Number.MAX_SAFE_INTEGER);
  const tuples=(key,properties,name)=>{const r=data.properties[key];require(r.type==="array"&&r.minItems===properties.length&&r.maxItems===properties.length&&r.items===false&&r.prefixItems?.length===properties.length);
    require(same(r.prefixItems,properties.map(p=>({allOf:[{$ref:`#/components/schemas/${name}`},{properties:p}]}))));};
  const kinds=["document","studio","object"],metrics=["cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes"];
  tuples("worker_samples",kinds.map(worker_kind=>({worker_kind:{const:worker_kind}})),"WorkspaceWorkerSample");
  tuples("resource_samples",kinds.flatMap(worker_kind=>metrics.map((metric_id,i)=>({worker_kind:{const:worker_kind},metric_id:{const:metric_id},unit:{const:i===0?"millicores":"bytes"}}))),"WorkspaceResourceSample");
  const ids=["manual_reprocess_pending","readiness_reprocess_pending","retry_wait","backoff_wait","step_up_denied_24h","step_up_expired_24h","access_blocked_24h","access_masked_24h","egress_denied_24h"];
  tuples("event_counts",ids.map((metric_id,i)=>({metric_id:{const:metric_id},...(i<4?{window_start:{type:"null"},window_end:{type:"null"}}:{})})),"WorkspaceEventCount");
  require(same(event.properties.metric_id.enum,ids)&&event.properties.unit.const==="count");
  // The opt-in branch is separate: never widen the default nine-row response.
  const readinessIds=["waiting_text_generation","waiting_image_understanding","waiting_document_parsing","waiting_speech_to_text","waiting_role_unknown","retry_suppressed_contexts_24h"];
  const readinessEvent=exact("WorkspaceReadinessEventCount",event.required);
  for(const key of event.required){
    require(same(readinessEvent.properties[key],key==="metric_id"?{enum:readinessIds}:{$ref:`#/components/schemas/WorkspaceEventCount/properties/${key}`}));
  }
  require(same(readinessEvent.allOf,[0,1,2].map(i=>({$ref:`#/components/schemas/WorkspaceEventCount/allOf/${i}`}))));
  // The referenced safety constraints must remain closed, too.
  require(same(event.properties.state.enum,["measured","partial","not_collected","unavailable"]));
  require(same(event.allOf,[{$ref:"#/components/schemas/SystemObservationTiming"},
    {if:{properties:{state:{enum:["not_collected","not_reported","unavailable"]}}},then:{properties:{value:{type:"null"},window_start:{type:"null"},window_end:{type:"null"}}}},
    {if:{properties:{state:{enum:["measured","partial"]}}},then:{properties:{value:{type:"integer"}}}}]));
  const extended=exact("OperationsStatusWithReadiness",data.required);
  for(const key of data.required.filter(key=>key!=="event_counts"))require(same(extended.properties[key],{$ref:`#/components/schemas/OperationsStatus/properties/${key}`}));
  const readinessTuple={type:"array",minItems:15,maxItems:15,items:false,prefixItems:[
    ...ids.map((_,i)=>({$ref:`#/components/schemas/OperationsStatus/properties/event_counts/prefixItems/${i}`})),
    ...readinessIds.map((metric_id,i)=>({allOf:[{$ref:"#/components/schemas/WorkspaceReadinessEventCount"},
      {properties:{metric_id:{const:metric_id},...(i<5?{window_start:{type:"null"},window_end:{type:"null"}}:{})}}],...(i===5?{"x-window-seconds":86400}:{})}))]};
  require(same(extended.properties.event_counts,readinessTuple));
  const envelope=exact("OperationsStatusReadinessEnvelope",["data","meta"]);
  require(same(envelope.properties,{data:{$ref:"#/components/schemas/OperationsStatusWithReadiness"},meta:{$ref:"#/components/schemas/OperationsStatusEnvelope/properties/meta"}}));
  const amounts=cost.properties.known_amounts;
  require(amounts.type==="array"&&amounts.items.additionalProperties===false&&same(amounts.items.required,["currency","amount"])
    &&amounts.items.properties.currency.type==="string"&&amounts.items.properties.currency.pattern==="^[A-Z]{3}$"
    &&amounts.items.properties.amount.type==="string"&&amounts.items.properties.amount.pattern==="^(?:0|[1-9][0-9]{0,23})(?:\\.[0-9]{1,9})?$");
  require(data.properties.cost_summary.$ref==="#/components/schemas/WorkspaceCostSummary"&&data.properties.deployment_summary.$ref==="#/components/schemas/WorkspaceDeploymentSummary");
  require(cost.allOf[1].then.properties.known_amounts.maxItems===0&&cost.allOf[2].then.properties.unknown_attempt_count.type==="integer");
  require(same(deployment.properties.state.enum,["measured","not_collected","unavailable"])&&deployment.allOf[2].then.properties.image_digest.type==="string"&&deployment.allOf[3].then.properties.previous_image_digest.type==="string");
  const u=data.properties.unimplemented;
  require(u.type==="array"&&u.maxItems===7&&u.uniqueItems===true&&u.items.additionalProperties===false&&same(u.items.required,["producer_id","safe_code","impact_code"])
    &&same(u.items.properties.producer_id.enum,["waiting_role_producer","readiness_event_producer","retry_suppression_receipt","node_capacity_producer","provider_capacity_reporter","deployment_receipt_writer","cost_receipt_writer"])
    &&u.items.properties.safe_code.const==="PRODUCER_NOT_IMPLEMENTED"&&u.items.oneOf?.length===7);
}

function validateSystemOperationsContract(document) {
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),require=(ok)=>{if(!ok)fail("M2-S system operations exact contract");};
  const path=document.paths?.["/api/v1/system/operations/status"],op=path?.get,s=document.components?.schemas;
  require(same(Object.keys(path??{}),["get"])&&op.operationId==="getSystemOperationsStatus"
    &&same(op.parameters,[{name:'include_capacity',in:'query',required:false,schema:{type:'string',enum:['1']}}])&&!op.requestBody);
  require(same(op.responses?.['200']?.content?.['application/json']?.schema,{oneOf:[
    {$ref:'#/components/schemas/SystemOperationsStatusEnvelope'},{$ref:'#/components/schemas/SystemOperationsStatusCapacityEnvelope'}]}));
  const exact=(name,fields)=>{const row=s?.[name];require(row?.type==="object"&&row.additionalProperties===false&&same(row.required,fields)
    &&same(Object.keys(row.properties).sort(),[...fields].sort()));return row;};
  const common=["state","observed_at","received_at","safe_code"];
  const data=exact("SystemOperationsStatus",["schema_version","checked_at","health","worker_samples","resource_samples","deployment_summary","unimplemented"]);
  const envelope=exact("SystemOperationsStatusEnvelope",["data","meta"]);
  require(envelope.properties.data.$ref==="#/components/schemas/SystemOperationsStatus"&&envelope.properties.meta.additionalProperties===false
    &&same(envelope.properties.meta.required,["trace_id"])&&same(Object.keys(envelope.properties.meta.properties),["trace_id"]));
  exact("SystemOperationsHealth",["component_id","state","checked_at","safe_code"]);
  const worker=exact("SystemWorkerSample",[...common,"worker_kind","process_state","poll_started_at","poll_finished_at","active_started_at"]);
  const resource=exact("SystemResourceSample",[...common,"worker_kind","metric_id","value","unit"]);
  const deployment=exact("SystemDeploymentSummary",[...common,"operation","code_revision","image_digest","previous_image_digest","result"]);
  require(data.properties.schema_version.const===1&&worker.allOf?.length===3&&resource.allOf?.length===2&&deployment.allOf?.length===2);
  require(same(worker.properties.state.enum,["fresh","stale","not_collected","unavailable"]));
  require(same(resource.properties.value.type,["integer","null"])&&resource.properties.value.minimum===0&&resource.properties.value.maximum===Number.MAX_SAFE_INTEGER);
  const timing=s.SystemObservationTiming;
  require(same(timing?.required,common)&&timing.allOf?.length===2);
  const states=["fresh","stale","measured","partial","not_collected","not_reported","unavailable"];
  require(same(timing.allOf[1].oneOf?.map(r=>[r.properties.state.const,r.properties.safe_code.const]),states.map(v=>[v,`TELEMETRY_${v.toUpperCase()}`])));
  require(timing.allOf[0].then?.properties?.observed_at?.type==="null"&&timing.allOf[0].then?.properties?.received_at?.type==="null");
  const tuple=(key,expected,ref)=>{
    const a=data.properties[key];require(a.type==="array"&&a.minItems===expected.length&&a.maxItems===expected.length&&a.items===false&&a.prefixItems?.length===expected.length);
    require(same(a.prefixItems.map(r=>[r.allOf?.[0]?.$ref,r.allOf?.[1]?.properties]),expected.map(properties=>[`#/components/schemas/${ref}`,properties])));
  };
  tuple("health",["api","database","object_storage"].map(component_id=>({component_id:{const:component_id}})),"SystemOperationsHealth");
  tuple("worker_samples",["document","studio","object"].map(worker_kind=>({worker_kind:{const:worker_kind}})),"SystemWorkerSample");
  tuple("resource_samples",["document","studio","object"].flatMap(worker_kind=>["cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes"].map((metric_id,i)=>
    ({worker_kind:{const:worker_kind},metric_id:{const:metric_id},unit:{const:i===0?"millicores":"bytes"}}))),"SystemResourceSample");
  require(same(s.SystemOperationsHealth.oneOf?.map(r=>[r.properties.component_id.const,r.properties.state.const,r.properties.safe_code.const]),
    ["api","database","object_storage"].flatMap(id=>["ready","unavailable"].map(state=>[id,state,`${id.toUpperCase()}_${state.toUpperCase()}`]))));
  for(const row of [worker,resource,deployment])require(row.allOf[0].$ref==="#/components/schemas/SystemObservationTiming");
  require(same(worker.allOf[1].then?.properties?.process_state,{const:"unknown"})&&worker.allOf[2].then?.properties?.active_started_at?.type==="string"
    &&resource.allOf[1].then?.properties?.value?.type==="null"&&deployment.allOf[1].then?.properties?.result?.type==="null");
  const u=data.properties.unimplemented;require(u.type==="array"&&u.maxItems===3&&u.uniqueItems===true&&u.items.additionalProperties===false
    &&same(u.items.required,["producer_id","safe_code","impact_code"])&&u.items.oneOf?.length===3);
}

export function validateOpenApiDocument(document) {
  validateAccountSettings(document);
  validateStudioApprovalLifecycle(document);
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

  const secretScanDocument = structuredClone(document);
  const nativeLoginForScan = secretScanDocument.components?.schemas?.NativeLocalLoginRequest;
  if (isObject(nativeLoginForScan?.properties?.password)) {
    nativeLoginForScan.properties.native_local_login_input = nativeLoginForScan.properties.password;
    delete nativeLoginForScan.properties.password;
  }
  if (Array.isArray(nativeLoginForScan?.required)) {
    nativeLoginForScan.required = nativeLoginForScan.required.map((field) => (
      field === "password" ? "native_local_login_input" : field
    ));
  }
  const stepUpForScan = secretScanDocument.components?.schemas?.StepUpAuthorizationRequest;
  if (isObject(stepUpForScan?.properties?.password)) {
    stepUpForScan.properties.step_up_reauthentication_input = stepUpForScan.properties.password;
    delete stepUpForScan.properties.password;
  }
  if (Array.isArray(stepUpForScan?.required)) {
    stepUpForScan.required = stepUpForScan.required.map((field) => field === "password" ? "step_up_reauthentication_input" : field);
  }
  const questionAuthorizationForScan = secretScanDocument.components?.schemas?.GroundedQuestionAuthorizationRequest;
  if (isObject(questionAuthorizationForScan?.properties?.password)) {
    questionAuthorizationForScan.properties.question_reauthentication_input = questionAuthorizationForScan.properties.password;
    delete questionAuthorizationForScan.properties.password;
  }
  if (Array.isArray(questionAuthorizationForScan?.required)) {
    questionAuthorizationForScan.required = questionAuthorizationForScan.required.map((field) => field === "password" ? "question_reauthentication_input" : field);
  }
  const serialized = JSON.stringify(secretScanDocument);
  for (const pattern of FORBIDDEN_SOURCE_TOKENS) if (pattern.test(serialized)) fail(`forbidden token ${pattern}`);

  if (!isObject(document.paths)) fail("paths must be an object");
  const actualPaths = Object.keys(document.paths);
  for (const requiredPath of REQUIRED_PATHS) if (!(requiredPath in document.paths)) fail(`required path missing ${requiredPath}`);
  for (const actualPath of actualPaths) if (!REQUIRED_PATHS.includes(actualPath)) fail(`unapproved path ${actualPath}`);
  validateSystemOperationsContract(document);
  validateWorkspaceOperationsContract(document);
  validateOperationsCapacityContract(document);
  validateSourceReprocessingContract(document);
  validateOrganizationContract(document);
  validateWebsiteContract(document);
  validateOpenLawContract(document);
  validateSourceDeletionContract(document);
  for (const apiPath of ANDROID_BROWSER_AUTH_POST_PATHS) {
    const actualMethods = Object.keys(document.paths[apiPath]).filter((method) => HTTP_METHODS.has(method));
    if (JSON.stringify(actualMethods) !== JSON.stringify(["post"])) fail(`Android method mismatch ${apiPath}`);
  }
  const knowledgeMethods = new Map([
    ["/api/v1/workspaces/{id}/knowledge-packages", ["get"]],
    ["/api/v1/workspaces/{id}/operations/status", ["get"]],
    ["/api/v1/workspaces/{id}/output-version-settings", ["get", "patch"]],
    ["/api/v1/workspaces/{id}/sync-operations", ["get", "post"]],
    ["/api/v1/workspaces/{id}/knowledge-packages/{package_id}/offline-copies", ["post"]],
    ["/api/v1/offline-knowledge-copies/{copy_id}/content", ["get"]],
  ]);
  for (const [apiPath, expectedMethods] of knowledgeMethods) {
    const actualMethods = Object.keys(document.paths[apiPath]).filter((method) => HTTP_METHODS.has(method)).sort();
    if (JSON.stringify(actualMethods) !== JSON.stringify(expectedMethods)) fail(`Knowledge Package method mismatch ${apiPath}`);
  }
  const notebookMethods = new Map([
    ["/api/v1/workspaces/{id}/notebooks", ["get", "post"]],
    ["/api/v1/workspaces/{id}/notebooks/{notebook_id}", ["get", "patch"]],
    ["/api/v1/workspaces/{id}/notebooks/{notebook_id}/context", ["get"]],
    ["/api/v1/workspaces/{id}/notebooks/{notebook_id}/source-unbindings", ["post"]],
  ]);
  for (const [apiPath, expectedMethods] of notebookMethods) {
    const actualMethods = Object.keys(document.paths[apiPath]).filter((method) => HTTP_METHODS.has(method)).sort();
    if (JSON.stringify(actualMethods) !== JSON.stringify(expectedMethods)) fail(`Notebook method mismatch ${apiPath}`);
  }
  const syncItem = document.components?.schemas?.SyncItemInput;
  if (
    JSON.stringify(syncItem?.properties?.item_kind?.enum) !== JSON.stringify(["source_version", "output_version"])
    || syncItem?.properties?.item_kind?.default !== "source_version"
    || syncItem?.properties?.source_version_id?.type?.[1] !== "null"
    || syncItem?.properties?.output_version_id?.type?.[1] !== "null"
    || syncItem?.properties?.dependency_item_ids?.default?.length !== 0
  ) fail("versioned Sync item schema mismatch");
  if (document.components?.schemas?.OfflineKnowledgeCopyRequest?.additionalProperties !== false) {
    fail("Offline Knowledge copy request must be exact");
  }
  const studioRuntimeMethods = new Map([
    ["/api/v1/studio-generation-requests", ["post"]],
    ["/api/v1/studio-outputs", ["get"]],
    ["/api/v1/studio-outputs/{id}/versions", ["get", "post"]],
    ["/api/v1/reviews", ["post"]], ["/api/v1/approval-requests", ["post"]],
    ["/api/v1/approvals", ["post"]], ["/api/v1/deliveries", ["post"]],
    ["/api/v1/knowledge-registrations", ["post"]],
  ]);
  for (const [apiPath, expectedMethods] of studioRuntimeMethods) {
    const actualMethods = Object.keys(document.paths[apiPath]).filter((method) => HTTP_METHODS.has(method)).sort();
    if (JSON.stringify(actualMethods) !== JSON.stringify(expectedMethods)) fail(`Studio Runtime method mismatch ${apiPath}`);
  }
  const studioRequestBodies = new Map([
    ["/api/v1/studio-generation-requests", "#/components/requestBodies/StudioGeneration"],
    ["/api/v1/studio-outputs/{id}/versions", "#/components/requestBodies/StudioRevision"],
    ["/api/v1/reviews", "#/components/requestBodies/StudioAction"],
    ["/api/v1/approval-requests", "#/components/requestBodies/StudioAction"],
    ["/api/v1/approvals", "#/components/requestBodies/StudioAction"],
    ["/api/v1/deliveries", "#/components/requestBodies/StudioAction"],
    ["/api/v1/knowledge-registrations", "#/components/requestBodies/StudioAction"],
  ]);
  for (const [apiPath, expectedRef] of studioRequestBodies) {
    if (document.paths[apiPath]?.post?.requestBody?.$ref !== expectedRef) fail(`Studio Runtime request mismatch ${apiPath}`);
  }
  const studioResponses = new Map([
    ["/api/v1/studio-generation-requests", "#/components/responses/StudioGenerationMutationResponse"],
    ["/api/v1/studio-outputs/{id}/versions", "#/components/responses/StudioVersionMutationResponse"],
    ["/api/v1/reviews", "#/components/responses/StudioActionMutationResponse"],
    ["/api/v1/approval-requests", "#/components/responses/StudioActionMutationResponse"],
    ["/api/v1/approvals", "#/components/responses/StudioActionMutationResponse"],
    ["/api/v1/deliveries", "#/components/responses/StudioActionMutationResponse"],
    ["/api/v1/knowledge-registrations", "#/components/responses/StudioActionMutationResponse"],
  ]);
  for (const [apiPath, expectedRef] of studioResponses) {
    for (const status of ["200", "201"]) {
      if (document.paths[apiPath]?.post?.responses?.[status]?.$ref !== expectedRef) fail(`Studio Runtime response mismatch ${apiPath}`);
    }
  }
  if (document.paths["/api/v1/studio-outputs/{id}/versions"]?.get?.responses?.["200"]?.$ref
      !== "#/components/responses/StudioVersionHistoryResponse") {
    fail("Studio Version history response mismatch");
  }
  const exportMedia = Object.keys(document.components?.responses?.StudioExportResponse?.content ?? {}).sort();
  const requiredExportMedia = [
    "application/json", "application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/png", "image/svg+xml", "text/csv",
  ].sort();
  if (JSON.stringify(exportMedia) !== JSON.stringify(requiredExportMedia)) fail("Studio export media types mismatch Runtime");
  const exportProof = document.paths["/api/v1/studio-outputs/{id}/versions/{version_id}/exports/{format_name}"]?.get?.parameters ?? [];
  const requiredExportProof = [
    { name: "X-Daon-Step-Up-Authorization", in: "header", required: true, schema: { type: "string", minLength: 1, maxLength: 4096 } },
    { name: "Idempotency-Key", in: "header", required: true, schema: { type: "string", minLength: 16, maxLength: 128, pattern: "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$" } },
  ];
  if (JSON.stringify(exportProof.filter(parameter => parameter.in === "header")) !== JSON.stringify(requiredExportProof)) {
    fail("Studio export proof headers mismatch Runtime");
  }

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
      const idempotencyExemptPosts = new Set([
        "/api/v1/backups/selection-previews",
        "/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-search",
        "/api/v1/workspaces/{id}/connectors/mcp-open-law-go-kr/law-preview",
        "/api/v1/auth/native/login",
        "/api/v1/session/refresh",
        "/api/v1/session/logout",
        "/api/v1/session/native/logout"
      ]);
      if (method === "post" && !idempotencyExemptPosts.has(apiPath) && !ANDROID_BROWSER_AUTH_POST_PATHS.has(apiPath) && !refs.has("#/components/parameters/IdempotencyKey")) fail(`${operationId} missing Idempotency-Key`);
      if ((method === "patch" || method === "delete") && !refs.has("#/components/parameters/IfMatch")) {
        const mutationKey = `${method.toUpperCase()} ${apiPath}`;
        const schema = requestSchema(document, operation);
        const expectedVersionField = mutationKey === "PATCH /api/v1/workspaces/{id}/studio/approval-policy" ? "expected_policy_version" : "expected_version";
        const organizationVersionQuery = mutationKey === "DELETE /api/v1/organization/tenants/{tenant_id}/invitations/{invitation_id}";
        if (!organizationVersionQuery && (!BODY_VERSIONED_MUTATIONS.has(mutationKey) || !(schema?.required ?? []).includes(expectedVersionField))) {
          fail(`${operationId} missing If-Match or required expected_version`);
        }
      }
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
      if (!isSse && !(method === "post" && ANDROID_BROWSER_AUTH_POST_PATHS.has(apiPath)) && !isObject(resolvedSuccess.headers?.ETag)) fail(`${operationId} response ${successStatus} missing ETag`);
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
  validateLineageContract(document);
  validateRegisteredRestoreContract(document);
  validateAuthorizationContract(document);
  validateLicenseContract(document);

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

const LEGACY_EVIDENCE_SUMMARY = Object.freeze({
  schema_version: "1.0",
  contract_version: "1.0.0",
  canonical_sha256: "594AED28565CCDBA60F3A12565071F7EAE5239544D5632508BD612EA8D180E0A",
  path_count: 75,
  operation_count: 94,
  schema_count: 120,
  error_code_count: 31,
});

export function evidenceRelativePathForProfile(profile = "r1-m5-07") {
  if (profile === "r1-m5-07") {
    return "docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json";
  }
  if (profile === "r1-m8-10") {
    return "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/openapi-contract-summary.json";
  }
  fail(`unsupported evidence profile ${profile}`);
}

export async function verifyOpenApiContract({ root, write = false, evidenceProfile = "r1-m5-07" } = {}) {
  const repositoryRoot = root ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const contractPath = path.join(repositoryRoot, "packages/contracts/openapi/v1/openapi.json");
  const evidencePath = path.join(repositoryRoot, evidenceRelativePathForProfile(evidenceProfile));
  const document = JSON.parse(await readFile(contractPath, "utf8"));
  const summary = buildSummary(document);
  const evidenceSummary = evidenceProfile === "r1-m5-07" ? LEGACY_EVIDENCE_SUMMARY : summary;
  const expected = `${JSON.stringify(evidenceSummary, null, 2)}\n`;
  if (write && evidenceProfile === "r1-m5-07") fail("legacy evidence is immutable");
  if (write) {
    await mkdir(path.dirname(evidencePath), { recursive: true });
    await writeFile(evidencePath, expected, "utf8");
  } else {
    const actual = (await readFile(evidencePath, "utf8")).replaceAll("\r\n", "\n");
    if (actual !== expected) fail("deterministic evidence does not match; run with --write");
  }
  return evidenceSummary;
}

async function main() {
  const args = process.argv.slice(2).filter((argument) => argument !== "--");
  const write = args.includes("--write");
  if (write && args.includes("--no-write")) fail("--write and --no-write are mutually exclusive");
  const profileArgument = args.find((argument) => argument.startsWith("--evidence-profile="));
  const evidenceProfile = profileArgument?.slice("--evidence-profile=".length) ?? "r1-m5-07";
  const knownArguments = new Set(["--write", "--no-write", profileArgument].filter(Boolean));
  if (args.some((argument) => !knownArguments.has(argument))) fail("unsupported command argument");
  const summary = await verifyOpenApiContract({ write, evidenceProfile });
  console.log(`openapi contract verified: paths=${summary.path_count} operations=${summary.operation_count} schemas=${summary.schema_count} errors=${summary.error_code_count} sha256=${summary.canonical_sha256}`);
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
