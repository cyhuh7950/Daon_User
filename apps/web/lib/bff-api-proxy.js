const MAX_REQUEST_BYTES = 65_536;
const MAX_ANDROID_AUTH_FORM_BYTES = 512;
const MAX_ANDROID_AUTH_RESPONSE_BYTES = 4_096;
const MAX_SOURCE_UPLOAD_BYTES = 25 * 1024 * 1024;
const MAX_NATIVE_RESPONSE_BYTES = 128 * 1024;
const MAX_NATIVE_RECOVERY_RESPONSE_BYTES = 1024 * 1024;
const MAX_SESSION_COOKIE_BYTES = 4_096;
const MIN_NATIVE_BEARER_BYTES = 16;
const MAX_NATIVE_BEARER_BYTES = 4_096;
const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const REQUEST_HEADERS = new Set([
  "content-type",
  "idempotency-key",
  "if-match",
  "traceparent",
  "x-trace-id",
  "x-source-filename",
  "x-notebook-id",
]);
const RESPONSE_HEADERS = new Set([
  "cache-control",
  "content-disposition",
  "content-type",
  "etag",
  "retry-after",
  "set-cookie",
  "x-trace-id",
  "x-citation-locator-kind",
  "x-citation-page",
  "x-citation-region",
  "x-content-type-options",
]);
const NATIVE_RESPONSE_HEADERS = new Set([
  "cache-control",
  "content-disposition",
  "content-type",
  "etag",
  "retry-after",
  "x-trace-id",
  "x-citation-locator-kind",
  "x-citation-page",
  "x-citation-region",
]);
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const SESSION_COOKIE_NAME = "__Host-daon_session";
const WSL_HTTP_SESSION_COOKIE_NAME = "daon_session";
const AUDIT_QUERY = new Set([
  "action",
  "cursor",
  "filter",
  "limit",
  "occurred_after",
  "occurred_before",
  "outcome",
  "search",
  "tenant_id",
  "trace_id",
  "workspace_id",
]);
const NOTIFICATION_QUERY = new Set(["cursor", "filter", "limit", "search"]);
const INBOX_QUERY = new Set(["cursor", "filter", "limit", "search"]);
const BACKUP_QUERY = new Set(["workspace_id"]);
const MODEL_SETTINGS_QUERY = new Set(["workspace_id"]);
const PROVIDER_CODES = new Set([
  "ANTHROPIC", "CEREBRAS", "GEMINI", "GROQ", "MISTRAL", "OLLAMA",
  "OPENAI", "OPENROUTER", "UPSTAGE", "OMNIROUTE", "EOUL_GATEWAY",
]);
const STUDIO_QUERY = new Set(["workspace_id", "notebook_id"]);
const STUDIO_NOTICE_QUERY = new Set(["notebook_id"]);
const STUDIO_LIFECYCLE_EMPTY_QUERY = new Set();
const RESTORE_ACTIONS = new Set(["execute", "cancel"]);
const GROUNDED_QUESTION_TIMEOUT_MS = null;

export class BffConfigurationError extends Error {
  constructor(code) {
    super(code);
    this.name = "BffConfigurationError";
    this.code = code;
  }
}

export function parseInternalApiBase(rawValue, profile = "production") {
  let parsed;
  try {
    parsed = new URL(rawValue);
  } catch {
    throw new BffConfigurationError("BFF_INTERNAL_API_URL_INVALID");
  }
  const cleanOrigin = parsed.pathname === "/" && !parsed.search && !parsed.hash;
  if (!cleanOrigin || parsed.username || parsed.password) {
    throw new BffConfigurationError("BFF_INTERNAL_API_ORIGIN_REQUIRED");
  }
  if (profile === "production" || profile === "wsl_http_qa") {
    // Browser traffic remains same-origin HTTPS. The isolated Docker service
    // is reached server-side over its fixed Compose DNS name and private
    // network, so only that exact internal origin may use HTTP.
    const isolatedInternalHttp = parsed.protocol === "http:" && parsed.hostname === "api";
    if (parsed.protocol !== "https:" && !isolatedInternalHttp) {
      throw new BffConfigurationError("BFF_INTERNAL_API_HTTPS_REQUIRED");
    }
  } else {
    const loopback = new Set(["127.0.0.1", "[::1]", "localhost"]);
    if (parsed.protocol !== "http:" || !loopback.has(parsed.hostname)) {
      throw new BffConfigurationError("BFF_DEVELOPMENT_API_MUST_BE_LOOPBACK");
    }
  }
  return parsed;
}

export function parsePublicGatewayOrigin(rawValue, profile = "production") {
  let parsed;
  try {
    parsed = new URL(rawValue);
  } catch {
    throw new BffConfigurationError("BFF_PUBLIC_GATEWAY_URL_INVALID");
  }
  const localTestHttp = profile === "local_test"
    && parsed.protocol === "http:"
    && new Set(["localhost", "127.0.0.1"]).has(parsed.hostname)
    && parsed.port !== "";
  const octets = parsed.hostname.split(".").map((part) => Number(part));
  const privateIpv4 = octets.length === 4
    && octets.every((part, index) => /^\d{1,3}$/.test(parsed.hostname.split(".")[index]) && part >= 0 && part <= 255)
    && (
      octets[0] === 10
      || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31)
      || (octets[0] === 192 && octets[1] === 168)
    );
  const wslHttpQa = profile === "wsl_http_qa"
    && parsed.protocol === "http:"
    && privateIpv4
    && parsed.port !== "";
  if (
    (profile === "local_test" ? !localTestHttp : profile === "wsl_http_qa" ? !wslHttpQa : parsed.protocol !== "https:")
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || parsed.username
    || parsed.password
  ) {
    throw new BffConfigurationError("BFF_PUBLIC_GATEWAY_HTTPS_ORIGIN_REQUIRED");
  }
  return parsed;
}

export function browserSessionCookieName(profile = "production") {
  return profile === "wsl_http_qa" ? WSL_HTTP_SESSION_COOKIE_NAME : SESSION_COOKIE_NAME;
}

function routeFor(method, segments) {
  if (segments.length === 2 && segments[0] === "account" && new Set(["profile", "devices", "sessions"]).has(segments[1])) {
    const profile = segments[1] === "profile";
    return (method === "GET" || profile && method === "PATCH")
      ? { path: `/api/v1/account/${segments[1]}`, query: profile ? new Set() : new Set(["limit", "cursor"]),
        csrfProvenanceRequired: method === "PATCH", strictQuery: true, profileSessionBinding: profile && method === "PATCH" } : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "devices" && SAFE_SEGMENT.test(segments[1]) && new Set(["trust", "revoke"]).has(segments[2])) {
    return method === "POST" ? { path: `/api/v1/devices/${encodeURIComponent(segments[1])}/${segments[2]}`,
      query: new Set(), csrfProvenanceRequired: true, strictQuery: true } : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "session" && segments[1] === "revoke") {
    return method === "POST" ? { path: "/api/v1/session/revoke", query: new Set(), csrfProvenanceRequired: true, strictQuery: true } : { methodRejected: true };
  }
  if(segments.length===5 && segments[0]==='workspaces' && SAFE_SEGMENT.test(segments[1])
    && segments[2]==='connectors' && segments[3]==='mcp-open-law-go-kr' && new Set(['law-search','law-preview','law-imports']).has(segments[4])){
    return method==='POST'?{path:`/api/v1/workspaces/${encodeURIComponent(segments[1])}/connectors/mcp-open-law-go-kr/${segments[4]}`,
      query:null,requiredQuery:new Set(),law:true,lawPost:true,csrfProvenanceRequired:true,maxRequestBytes:32768}:{methodRejected:true};
  }
  if(segments.length===5 && segments[0]==='workspaces' && SAFE_SEGMENT.test(segments[1]) && segments[2]==='source-versions'
    && SAFE_SEGMENT.test(segments[3]) && segments[4]==='law-snapshot'){
    return method==='GET'?{path:`/api/v1/workspaces/${encodeURIComponent(segments[1])}/source-versions/${encodeURIComponent(segments[3])}/law-snapshot`,
      query:new Set(['notebook_id']),requiredQuery:new Set(['notebook_id']),law:true}:{methodRejected:true};
  }
  if (segments.length === 3 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "website-imports") {
    return method === "POST" ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/website-imports`,
      query: null, requiredQuery: new Set(), website: true, websiteImport: true, csrfProvenanceRequired: true }
      : { methodRejected: true };
  }
  if (segments.length === 5 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1])
      && segments[2] === "source-versions" && SAFE_SEGMENT.test(segments[3]) && segments[4] === "website-snapshot") {
    return method === "GET" ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/source-versions/${encodeURIComponent(segments[3])}/website-snapshot`,
      query: new Set(["notebook_id"]), requiredQuery: new Set(["notebook_id"]), website: true } : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "auth" && segments[1] === "password" && segments[2] === "change") {
    return method === "POST"
      ? { path: "/api/v1/auth/password/change", query: null, csrfProvenanceRequired: true }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "admin" && segments[1] === "users") {
    return new Set(["GET", "POST"]).has(method)
      ? { path: "/api/v1/admin/users", query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "admin" && segments[1] === "tenants") {
    return method === "GET" ? { path: "/api/v1/admin/tenants", query: null } : { methodRejected: true };
  }
  if (segments.length === 6 && segments[0] === "admin" && segments[1] === "tenants"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "users"
    && SAFE_SEGMENT.test(segments[4]) && segments[5] === "memberships") {
    return method === "GET"
      ? { path: `/api/v1/admin/tenants/${encodeURIComponent(segments[2])}/users/${encodeURIComponent(segments[4])}/memberships`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 8 && segments[0] === "admin" && segments[1] === "tenants"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "workspaces"
    && SAFE_SEGMENT.test(segments[4]) && segments[5] === "memberships"
    && SAFE_SEGMENT.test(segments[6]) && segments[7] === "role") {
    return method === "PATCH"
      ? { path: `/api/v1/admin/tenants/${encodeURIComponent(segments[2])}/workspaces/${encodeURIComponent(segments[4])}/memberships/${encodeURIComponent(segments[6])}/role`, query: null, csrfProvenanceRequired: true }
      : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "provider-credentials") {
    return method === "GET"
      ? { path: "/api/v1/provider-credentials", query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 2 && segments[0] === "provider-credentials"
    && SAFE_SEGMENT.test(segments[1])
  ) {
    return new Set(["PUT", "DELETE"]).has(method)
      ? { path: `/api/v1/provider-credentials/${encodeURIComponent(segments[1])}`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "admin" && segments[1] === "provider-connections") {
    return new Set(["GET", "POST"]).has(method)
      ? { path: "/api/v1/admin/provider-connections", query: null }
      : { methodRejected: true };
  }
  if (method === "POST" && segments.length === 3 && segments[0] === "admin" && segments[1] === "provider-connections" && segments[2] === "model-preview") {
    return { path: "/api/v1/admin/provider-connections/model-preview", query: null };
  }
  if (segments.length === 2 && segments[0] === "admin" && segments[1] === "provider-health-settings") {
    return new Set(["GET", "PATCH"]).has(method)
      ? { path: "/api/v1/admin/provider-health-settings", query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "admin" && segments[1] === "provider-connections"
    && SAFE_SEGMENT.test(segments[2])
  ) {
    return new Set(["PUT", "DELETE"]).has(method)
      ? { path: `/api/v1/admin/provider-connections/${encodeURIComponent(segments[2])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "admin" && segments[1] === "provider-connections"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "credential"
  ) {
    return new Set(["POST", "DELETE"]).has(method)
      ? { path: `/api/v1/admin/provider-connections/${encodeURIComponent(segments[2])}/credential`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "admin" && segments[1] === "provider-catalog"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "refresh"
  ) {
    return method === "POST"
      ? { path: `/api/v1/admin/provider-catalog/${encodeURIComponent(segments[2])}/refresh`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 5 && segments[0] === "admin" && segments[1] === "provider-models"
    && SAFE_SEGMENT.test(segments[2]) && SAFE_SEGMENT.test(segments[3])
    && segments[4] === "capabilities"
  ) {
    return method === "PATCH"
      ? { path: `/api/v1/admin/provider-models/${encodeURIComponent(segments[2])}/${encodeURIComponent(segments[3])}/capabilities`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "admin" && segments[1] === "users"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "state"
  ) {
    return method === "PATCH"
      ? { path: `/api/v1/admin/users/${encodeURIComponent(segments[2])}/state`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "admin" && segments[1] === "users"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "password-reset"
  ) {
    return method === "POST"
      ? { path: `/api/v1/admin/users/${encodeURIComponent(segments[2])}/password-reset`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "admin" && segments[1] === "users"
    && SAFE_SEGMENT.test(segments[2])
  ) {
    return new Set(["PATCH", "DELETE"]).has(method)
      ? { path: `/api/v1/admin/users/${encodeURIComponent(segments[2])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "admin" && segments[1] === "users"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "approve"
  ) {
    return method === "POST"
      ? { path: `/api/v1/admin/users/${encodeURIComponent(segments[2])}/approve`, query: null }
      : { methodRejected: true };
  }
  // Organization workflow and administrator console contracts. The browser
  // only sees /bff/api; the internal /api/v1 origin remains server-side.
  if (segments.length === 3 && segments[0] === "organization" && segments[1] === "admin" && segments[2] === "tenants") {
    return method === "GET" ? { path: "/api/v1/organization/admin/tenants", query: new Set(), strictQuery: true } : { methodRejected: true };
  }
  if (segments.length === 4 && segments[0] === "organization" && segments[1] === "tenants"
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "workspaces") {
    return method === "GET" ? { path: `/api/v1/organization/tenants/${encodeURIComponent(segments[2])}/workspaces`, query: new Set(), strictQuery: true } : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "organization"
    && segments[1] === "admin" && segments[2] === "directory") {
    return method === "GET"
      ? { path: "/api/v1/admin/directory", query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "organization"
    && new Set(["creation-requests", "join-requests"]).has(segments[1])) {
    if (segments[1] === "join-requests") {
      return new Set(["GET", "POST"]).has(method)
        ? { path: `/api/v1/organization/${segments[1]}`, query: new Set(method === "GET" ? ["tenant_id"] : []), strictQuery: method === "POST", orgSessionBinding: method === "POST" }
        : { methodRejected: true };
    }
    return new Set(["GET", "POST"]).has(method)
      ? { path: `/api/v1/organization/${segments[1]}`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 4 && segments[0] === "organization"
    && new Set(["creation-requests", "join-requests"]).has(segments[1])
    && SAFE_SEGMENT.test(segments[2]) && segments[3] === "decision") {
    return method === "POST"
      ? { path: `/api/v1/organization/${segments[1]}/${encodeURIComponent(segments[2])}/decision`, query: null, orgSessionBinding: segments[1] === "join-requests" }
      : { methodRejected: true };
  }
  if (segments.length === 4 && segments[0] === "organization"
    && segments[1] === "tenants" && SAFE_SEGMENT.test(segments[2])
    && segments[3] === "invitations") {
    return new Set(["GET", "POST"]).has(method)
      ? { path: `/api/v1/organization/tenants/${encodeURIComponent(segments[2])}/invitations`, query: new Set(), strictQuery: true, orgSessionBinding: method === "POST" }
      : { methodRejected: true };
  }
  if (segments.length === 5 && segments[0] === "organization"
    && segments[1] === "tenants" && SAFE_SEGMENT.test(segments[2])
    && segments[3] === "invitations" && SAFE_SEGMENT.test(segments[4])) {
    return method === "DELETE"
      ? { path: `/api/v1/organization/tenants/${encodeURIComponent(segments[2])}/invitations/${encodeURIComponent(segments[4])}`, query: new Set(["expected_version"]), organizationVersionQuery: true, orgSessionBinding: true }
      : { methodRejected: true };
  }
  if (segments.length === 4 && segments[0] === "organization"
    && segments[1] === "tenants" && SAFE_SEGMENT.test(segments[2])
    && segments[3] === "members") {
    return method === "GET"
      ? { path: `/api/v1/organization/tenants/${encodeURIComponent(segments[2])}/members`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 6 && segments[0] === "organization"
    && segments[1] === "tenants" && SAFE_SEGMENT.test(segments[2])
    && segments[3] === "members" && SAFE_SEGMENT.test(segments[4])
    && new Set(["role", "state"]).has(segments[5])) {
    return method === "PATCH"
      ? { path: `/api/v1/organization/tenants/${encodeURIComponent(segments[2])}/members/${encodeURIComponent(segments[4])}/${segments[5]}`, query: segments[5] === "state" ? new Set(["active"]) : null, orgSessionBinding: true }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "studio"
    && segments[3] === "reports"
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio/reports`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "studio"
    && segments[3] === "outputs"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio/outputs`, query: new Set(["notebook_id"]) }
      : { methodRejected: true };
  }
  if (segments.length === 5 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1])
      && segments[2] === "notebooks" && SAFE_SEGMENT.test(segments[3]) && segments[4] === "model-settings") {
    return ["GET","PATCH"].includes(method) ? {path:`/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks/${encodeURIComponent(segments[3])}/model-settings`,query:new Set(),strictQuery:true} : {methodRejected:true};
  }
  if (segments.length === 5 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1])
      && segments[2] === "notebooks" && SAFE_SEGMENT.test(segments[3]) && segments[4] === "conversation-turns") {
    return method === "GET" ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks/${encodeURIComponent(segments[3])}/conversation-turns`,
      query:new Set(["limit","before"]),strictQuery:true,notebookConversation:true } : { methodRejected:true };
  }
  if (
    segments.length === 5 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "notebooks"
    && SAFE_SEGMENT.test(segments[3]) && segments[4] === "context"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks/${encodeURIComponent(segments[3])}/context`,
          query: new Set(["run_id", "studio_job_id"]), strictQuery: true, notebookContextSelection: true }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "sources"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "deletion-requests"
  ) {
    return method === "POST"
      ? { path: `/api/v1/sources/${encodeURIComponent(segments[1])}/deletion-requests`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "deletion-requests" && SAFE_SEGMENT.test(segments[1])) {
    return method === "GET"
      ? { path: `/api/v1/deletion-requests/${encodeURIComponent(segments[1])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "deletion-requests"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "cancel"
  ) {
    return method === "POST"
      ? { path: `/api/v1/deletion-requests/${encodeURIComponent(segments[1])}/cancel`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "notebooks"
  ) {
    return new Set(["GET", "POST"]).has(method)
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "connectors"
    && SAFE_SEGMENT.test(segments[3])
  ) {
    return method === "DELETE"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/connectors/${encodeURIComponent(segments[3])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "connectors"
  ) {
    return new Set(["GET", "POST"]).has(method)
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/connectors`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 5 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "connectors"
    && SAFE_SEGMENT.test(segments[3]) && new Set(["reconnect", "disconnect"]).has(segments[4])
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/connectors/${encodeURIComponent(segments[3])}/${segments[4]}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 5 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "connectors"
    && SAFE_SEGMENT.test(segments[3]) && segments[4] === "sources"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/connectors/${encodeURIComponent(segments[3])}/sources`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "notebooks" && SAFE_SEGMENT.test(segments[3])
  ) {
    return new Set(["GET", "PATCH", "DELETE"]).has(method)
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks/${encodeURIComponent(segments[3])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 6 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "notebooks" && SAFE_SEGMENT.test(segments[3])
    && segments[4] === "deletion-requests" && SAFE_SEGMENT.test(segments[5])
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks/${encodeURIComponent(segments[3])}/deletion-requests/${encodeURIComponent(segments[5])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 5 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "notebooks"
    && SAFE_SEGMENT.test(segments[3]) && segments[4] === "source-unbindings"
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/notebooks/${encodeURIComponent(segments[3])}/source-unbindings`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "license"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/license`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "organizations"
    && SAFE_SEGMENT.test(segments[1]) && segments[2] === "license"
  ) {
    return method === "POST"
      ? { path: `/api/v1/organizations/${encodeURIComponent(segments[1])}/license`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "preferences" && segments[1] === "screen") {
    return new Set(["GET", "PUT"]).has(method)
      ? { path: "/api/v1/preferences/screen", query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "session" && segments[1] === "step-up") {
    return method === "POST" ? { path: "/api/v1/session/step-up", query: null } : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "studio-generation-jobs" && SAFE_SEGMENT.test(segments[1])) {
    return method === "GET"
      ? { path: `/api/v1/studio-generation-jobs/${encodeURIComponent(segments[1])}`, query: STUDIO_QUERY, requiredQuery: STUDIO_QUERY }
      : { methodRejected: true };
  }
  if(segments.length===2&&segments[0]==="studio-generation-requests"&&segments[1]==="authorization"){
    return method==="POST"?{path:"/api/v1/studio-generation-requests/authorization",query:null}:{methodRejected:true};
  }
  if (segments.length === 1 && new Set([
    "studio-generation-requests", "studio-outputs", "reviews", "approval-requests",
    "approvals", "deliveries", "knowledge-registrations",
  ]).has(segments[0])) {
    if (method === "GET" && segments[0] === "studio-outputs") return { path: `/api/v1/${segments[0]}`, query: STUDIO_QUERY };
    return method === "POST" && segments[0] !== "studio-outputs"
      ? { path: `/api/v1/${segments[0]}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3 && segments[0] === "studio-outputs" && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "versions"
  ) {
    if (method === "POST") return { path: `/api/v1/studio-outputs/${encodeURIComponent(segments[1])}/versions`, query: null };
    return method === "GET"
      ? { path: `/api/v1/studio-outputs/${encodeURIComponent(segments[1])}/versions`, query: STUDIO_QUERY }
      : { methodRejected: true };
  }
  if (
    segments.length === 6 && segments[0] === "studio-outputs" && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "versions" && SAFE_SEGMENT.test(segments[3]) && segments[4] === "exports"
    && new Set(["docx", "pdf", "xlsx", "csv", "json", "svg", "png"]).has(segments[5])
  ) {
    return method === "GET"
      ? { path: `/api/v1/studio-outputs/${encodeURIComponent(segments[1])}/versions/${encodeURIComponent(segments[3])}/exports/${segments[5]}`, query: STUDIO_QUERY, studioExport: true }
      : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "approval-requests" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "withdraw") {
    return method === "POST" ? { path: `/api/v1/approval-requests/${encodeURIComponent(segments[1])}/withdraw`, query: STUDIO_LIFECYCLE_EMPTY_QUERY, requiredQuery: STUDIO_LIFECYCLE_EMPTY_QUERY } : { methodRejected: true };
  }
  if (segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1] ?? "") && segments[2] === "studio") {
    const prefix = `/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio`;
    if (segments.length === 4 && segments[3] === "approval-policy") {
      return ["GET", "PATCH"].includes(method) ? { path: `${prefix}/approval-policy`, query: STUDIO_LIFECYCLE_EMPTY_QUERY, requiredQuery: STUDIO_LIFECYCLE_EMPTY_QUERY } : { methodRejected: true };
    }
    if (segments.length === 4 && segments[3] === "approval-notices") {
      return method === "GET" ? { path: `${prefix}/approval-notices`, query: STUDIO_NOTICE_QUERY, requiredQuery: STUDIO_NOTICE_QUERY } : { methodRejected: true };
    }
    if (segments.length === 5 && segments[3] === "approval-notices" && SAFE_SEGMENT.test(segments[4])) {
      return method === "PATCH" ? { path: `${prefix}/approval-notices/${encodeURIComponent(segments[4])}`, query: STUDIO_LIFECYCLE_EMPTY_QUERY, requiredQuery: STUDIO_LIFECYCLE_EMPTY_QUERY } : { methodRejected: true };
    }
  }
  if (segments.length === 3 && segments[0] === "sources" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "processing-runs") {
    return method === "POST" ? { path: `/api/v1/sources/${encodeURIComponent(segments[1])}/processing-runs`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 4
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "questions"
    && segments[3] === "authorization"
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/questions/authorization`, query: null, groundedQuestion: true }
      : { methodRejected: true };
  }
  if (
    segments.length === 4
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "operations"
    && segments[3] === "status"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/operations/status`, query: new Set(["scope","include_readiness","include_capacity"]), strictQuery: true, operationsStatus: true }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "output-version-settings"
  ) {
    return new Set(["GET", "PATCH"]).has(method)
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/output-version-settings`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "model-defaults"
  ) {
    return new Set(["GET", "PATCH"]).has(method)
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/model-defaults`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "sync-operations"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/sync-operations`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "sync-operations"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "approve"
  ) {
    return method === "POST"
      ? { path: `/api/v1/sync-operations/${encodeURIComponent(segments[1])}/approve`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "egress-policy"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/egress-policy`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && new Set(["organizations", "workspaces"]).has(segments[0])
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "egress-policy-versions"
  ) {
    return method === "POST"
      ? { path: `/api/v1/${segments[0]}/${encodeURIComponent(segments[1])}/egress-policy-versions`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "questions"
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/questions`, query: null, groundedQuestion: true }
      : { methodRejected: true };
  }
  if (
    segments.length === 5
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "citations"
    && SAFE_SEGMENT.test(segments[3])
    && segments[4] === "content"
  ) {
    return method === "GET"
      ? {
          path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/citations/${encodeURIComponent(segments[3])}/content`,
          query: new Set(["notebook_id", "representation"]),
        }
      : { methodRejected: true };
  }
  if (
    segments.length === 4
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "processing-runs"
    && SAFE_SEGMENT.test(segments[3])
  ) {
    return method === "GET"
      ? {
          path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/processing-runs/${encodeURIComponent(segments[3])}`,
          query: new Set(["notebook_id"]),
        }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "sources"
  ) {
    if (method === "GET") {
      return { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/sources`, query: new Set(["notebook_id", "website_source_version_id"]), websiteSourceQuery: true };
    }
    return method === "POST" ? {
          path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/sources`,
          query: null,
          maxRequestBytes: MAX_SOURCE_UPLOAD_BYTES,
        }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "knowledge-packages"
  ) {
    return method === "GET"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/knowledge-packages`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 1 && new Set(["model-profiles", "model-deployments"]).has(segments[0])) {
    if (method === "GET") return { path: `/api/v1/${segments[0]}`, query: MODEL_SETTINGS_QUERY };
    return method === "POST"
      ? { path: `/api/v1/${segments[0]}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "model-profiles"
    && PROVIDER_CODES.has(segments[1])
    && segments[2] === "connection-check"
  ) {
    return method === "GET"
      ? { path: `/api/v1/model-profiles/${segments[1]}/connection-check`, query: MODEL_SETTINGS_QUERY }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "model-policy"
  ) {
    return new Set(["GET", "PATCH"]).has(method)
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/model-policy`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "auth" && new Set(["signup", "login", "verify-email", "resend-verification"]).has(segments[1])) {
    return method === "POST" ? { path: `/api/v1/auth/${segments[1]}`, query: null } : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "auth" && segments[1] === "password-reset" && new Set(["request", "confirm"]).has(segments[2])) {
    return method === "POST" ? { path: `/api/v1/auth/password-reset/${segments[2]}`, query: null } : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "backups") {
    if (method === "GET") return { path: "/api/v1/backups", query: BACKUP_QUERY };
    return method === "POST"
      ? { path: "/api/v1/backups", query: null }
      : { methodRejected: true };
  }
  if (segments.length === 3 && segments.join("/") === "system/operations/status") {
    return method === "GET" ? { path: "/api/v1/system/operations/status", query: new Set(['include_capacity']), strictQuery: true, operationsStatus:true } : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "backups" && segments[1] === "selection-previews") {
    return method === "POST" ? { path: "/api/v1/backups/selection-previews", query: new Set(), strictQuery: true } : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "backups" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "restore-destinations") {
    return method === "GET" ? { path: `/api/v1/backups/${encodeURIComponent(segments[1])}/restore-destinations`, query: new Set(), strictQuery: true } : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "backups" && SAFE_SEGMENT.test(segments[1])) {
    return method === "GET"
      ? { path: `/api/v1/backups/${encodeURIComponent(segments[1])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "backups"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "restore-previews"
  ) {
    return method === "POST"
      ? { path: `/api/v1/backups/${encodeURIComponent(segments[1])}/restore-previews`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 2
    && segments[0] === "restore-requests"
    && SAFE_SEGMENT.test(segments[1])
  ) {
    return method === "GET"
      ? { path: `/api/v1/restore-requests/${encodeURIComponent(segments[1])}`, query: null }
      : { methodRejected: true };
  }
  if (
    segments.length === 3
    && segments[0] === "restore-requests"
    && SAFE_SEGMENT.test(segments[1])
    && RESTORE_ACTIONS.has(segments[2])
  ) {
    return method === "POST"
      ? { path: `/api/v1/restore-requests/${encodeURIComponent(segments[1])}/${segments[2]}`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "session") {
    return method === "GET" ? { path: "/api/v1/session", query: null } : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "session" && segments[1] === "tenants") {
    return method === "GET" ? { path: "/api/v1/session/tenants", query: null } : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "session" && segments[1] === "tenant") {
    return method === "POST"
      ? { path: "/api/v1/session/tenant", query: null, csrfProvenanceRequired: true }
      : { methodRejected: true };
  }
  if (segments.length === 3 && segments[0] === "session" && segments[1] === "android" && segments[2] === "authorize") {
    return method === "POST"
      ? { path: "/api/v1/session/android/authorize", query: null, csrfProvenanceRequired: true,
        requiresSessionCookie: true, androidFormAuthorization: true, maxRequestBytes: MAX_ANDROID_AUTH_FORM_BYTES }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "session" && segments[1] === "logout") {
    return method === "POST"
      ? { path: "/api/v1/session/logout", query: null, csrfProvenanceRequired: true }
      : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "access-decisions") {
    return method === "POST" ? { path: "/api/v1/access-decisions", query: null } : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "audit-events") {
    return method === "GET" ? { path: "/api/v1/audit-events", query: AUDIT_QUERY } : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "notifications") {
    return method === "GET"
      ? { path: "/api/v1/notifications", query: NOTIFICATION_QUERY }
      : { methodRejected: true };
  }
  if (segments.length === 2 && segments[0] === "notifications" && SAFE_SEGMENT.test(segments[1])) {
    return new Set(["GET", "PATCH"]).has(method)
      ? { path: `/api/v1/notifications/${encodeURIComponent(segments[1])}`, query: null }
      : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "inbox") {
    return method === "GET"
      ? { path: "/api/v1/inbox", query: INBOX_QUERY }
      : { methodRejected: true };
  }
  if (
    segments.length === 4
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "authorization"
    && segments[3] === "evaluations"
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/authorization/evaluations`, query: null }
      : { methodRejected: true };
  }
  return null;
}

function nativeRouteFor(method, segments, requestUrl) {
  const route = (path, options = {}) => ({ path, protected: true, ...options });
  let matched = null;
  if (segments.length === 3 && segments[0] === "auth" && segments[1] === "native" && segments[2] === "login") {
    matched = method === "POST" ? route("/api/v1/auth/native/login", { protected: false, requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "session" && segments[1] === "android"
    && new Set(["transactions", "exchange"]).has(segments[2])) {
    matched = method === "POST"
      ? route(`/api/v1/session/android/${segments[2]}`, { protected: false, requestMediaType: "application/json" })
      : { methodRejected: true };
  } else if (segments.length === 2 && segments[0] === "session" && segments[1] === "refresh") {
    matched = method === "POST" ? route("/api/v1/session/refresh", { protected: false, requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 1 && segments[0] === "session") {
    matched = method === "GET" ? route("/api/v1/session") : { methodRejected: true };
  } else if (segments.length === 2 && segments[0] === "session" && segments[1] === "tenants") {
    matched = method === "GET" ? route("/api/v1/session/tenants") : { methodRejected: true };
  } else if (segments.length === 2 && segments[0] === "session" && segments[1] === "tenant") {
    matched = method === "POST"
      ? route("/api/v1/session/tenant", { requestMediaType: "application/json" })
      : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "sources") {
    matched = new Set(["GET", "POST"]).has(method)
      ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/sources`, method === "POST" ? { maxRequestBytes: MAX_SOURCE_UPLOAD_BYTES, requestMediaTypes: ["application/pdf", "text/plain", "text/markdown", "text/csv", "image/png", "image/jpeg", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "audio/wav"] } : {})
      : { methodRejected: true };
  } else if (segments.length === 4 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "processing-runs" && SAFE_SEGMENT.test(segments[3])) {
    matched = method === "GET"
      ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/processing-runs/${encodeURIComponent(segments[3])}`)
      : { methodRejected: true };
  } else if (segments.length === 4 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "questions" && segments[3] === "authorization") {
    matched = method === "POST" ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/questions/authorization`, { requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "questions") {
    matched = method === "POST" ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/questions`, { requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 5 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "citations" && SAFE_SEGMENT.test(segments[3]) && segments[4] === "content") {
    matched = method === "GET"
      ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/citations/${encodeURIComponent(segments[3])}/content`, { responseMediaTypes: ["application/pdf", "text/plain", "image/png", "image/jpeg", "application/json", "audio/wav"], maxResponseBytes: MAX_SOURCE_UPLOAD_BYTES })
      : { methodRejected: true };
  } else if (segments.length === 4 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "studio" && segments[3] === "reports") {
    matched = method === "POST" ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio/reports`, { requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 4 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "studio" && segments[3] === "outputs") {
    matched = method === "GET" ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio/outputs`) : { methodRejected: true };
  } else if (segments.length === 3 && segments.join("/") === "system/operations/status") {
    matched = method === "GET" ? route("/api/v1/system/operations/status") : { methodRejected: true };
  } else if (segments.length === 1 && segments[0] === "backups") {
    matched = new Set(["GET", "POST"]).has(method) ? route("/api/v1/backups", { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES, ...(method === "POST" ? { requestMediaType: "application/json" } : {}) }) : { methodRejected: true };
  } else if (segments.length === 2 && segments[0] === "backups" && segments[1] === "selection-previews") {
    matched = method === "POST" ? route("/api/v1/backups/selection-previews", { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES, requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "backups" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "restore-destinations") {
    matched = method === "GET" ? route(`/api/v1/backups/${encodeURIComponent(segments[1])}/restore-destinations`, { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES }) : { methodRejected: true };
  } else if (segments.length === 2 && segments[0] === "backups" && SAFE_SEGMENT.test(segments[1])) {
    matched = method === "GET" ? route(`/api/v1/backups/${encodeURIComponent(segments[1])}`, { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES }) : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "backups" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "restore-previews") {
    matched = method === "POST" ? route(`/api/v1/backups/${encodeURIComponent(segments[1])}/restore-previews`, { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES, requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 2 && segments[0] === "restore-requests" && SAFE_SEGMENT.test(segments[1])) {
    matched = method === "GET" ? route(`/api/v1/restore-requests/${encodeURIComponent(segments[1])}`, { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES }) : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "restore-requests" && SAFE_SEGMENT.test(segments[1]) && RESTORE_ACTIONS.has(segments[2])) {
    matched = method === "POST" ? route(`/api/v1/restore-requests/${encodeURIComponent(segments[1])}/${segments[2]}`, { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES, requestMediaType: "application/json" }) : { methodRejected: true };
  }
  if (!matched || matched.methodRejected) return matched;

  const incoming = new URL(requestUrl);
  const notebookScopedRead = method === "GET" && (
    (segments.length === 3 && segments[0] === "workspaces" && segments[2] === "sources")
    || (segments.length === 4 && segments[0] === "workspaces" && segments[2] === "processing-runs")
    || (segments.length === 5 && segments[0] === "workspaces" && segments[2] === "citations" && segments[4] === "content")
    || (segments.length === 4 && segments[0] === "workspaces" && segments[2] === "studio" && segments[3] === "outputs")
  );
  if (notebookScopedRead) {
    const entries = [...incoming.searchParams];
    const audio=segments.length===5 && segments[2]==="citations" && incoming.searchParams.has("representation");
    const allowed=audio?new Set(["notebook_id","representation"]):new Set(["notebook_id"]);
    if(entries.length!==(audio?2:1) || new Set(entries.map(([key])=>key)).size!==entries.length
        || entries.some(([key])=>!allowed.has(key)) || !SAFE_SEGMENT.test(incoming.searchParams.get("notebook_id")??"")
        || (audio && incoming.searchParams.get("representation")!=="audio"))return null;
    matched.query = new URLSearchParams(entries);
  } else if (method === "GET" && segments.length === 1 && segments[0] === "backups") {
    const entries = [...incoming.searchParams];
    if (entries.length !== 1 || entries[0][0] !== "workspace_id" || !SAFE_SEGMENT.test(entries[0][1])) return null;
    matched.query = new URLSearchParams([["workspace_id", entries[0][1]]]);
  } else if (incoming.search) {
    return null;
  }
  return matched;
}

function nativeBearer(request) {
  const raw = request.headers.get("authorization");
  const match = raw?.match(/^Bearer ([^\x00-\x20\x7f,]+)$/);
  if (!match) return null;
  const bytes = Buffer.byteLength(match[1], "utf8");
  return bytes >= MIN_NATIVE_BEARER_BYTES && bytes <= MAX_NATIVE_BEARER_BYTES ? match[1] : null;
}

function nativeSensitiveBodyValues(body, routePath) {
  try {
    const parsed = JSON.parse(Buffer.from(body).toString("utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
    const sensitiveKeys = ["password", "credential", "access_credential", "refresh_credential"];
    if (routePath === "/api/v1/session/android/exchange") sensitiveKeys.push("code", "code_verifier");
    return sensitiveKeys
      .map((key) => parsed[key])
      .filter((value) => typeof value === "string" && value.length > 0);
  } catch {
    return [];
  }
}

function exactMediaType(rawValue) {
  return rawValue?.split(";", 1)[0].trim().toLowerCase() ?? "";
}

function androidAuthorizationLocation(value) {
  if (typeof value !== "string" || value.includes("#") || /[\x00-\x1f\x7f]/u.test(value)) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !url.hostname || url.username || url.password || url.hash
      || url.searchParams.getAll("code").length !== 1 || !url.searchParams.get("code")
      || url.searchParams.getAll("state").length !== 1 || !url.searchParams.get("state")) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function createBffTraceId(request) {
  const supplied = request?.headers?.get("x-trace-id");
  return supplied && SAFE_SEGMENT.test(supplied)
    ? supplied
    : `trace-bff-${crypto.randomUUID()}`;
}

export function createBffSafeError(status, code, trace, retryable = false, message = "요청을 처리하지 못했습니다.") {
  return Response.json({
    error: {
      code,
      message,
      stage: "gateway",
      impact: "request_not_completed",
      retryable,
      user_action: retryable ? "잠시 후 다시 시도하세요." : "요청을 확인하세요.",
      trace_id: trace,
      details: {},
    },
  }, {
    status,
    headers: { "Cache-Control": "no-store", "X-Trace-Id": trace },
  });
}

function sessionCookie(request, browserCookieName) {
  const rawCookie = request.headers.get("cookie");
  if (!rawCookie) return null;
  if (/[\r\n\0]/.test(rawCookie)) throw new Error("INVALID_SESSION_COOKIE");
  const matches = [];
  for (const item of rawCookie.split(";")) {
    const separator = item.indexOf("=");
    if (separator < 0) continue;
    const name = item.slice(0, separator).trim();
    if (name !== browserCookieName) continue;
    const value = item.slice(separator + 1).trim();
    if (
      !value
      || Buffer.byteLength(value, "utf8") > MAX_SESSION_COOKIE_BYTES
      || /[\x00-\x20\x7f;,]/.test(value)
    ) {
      throw new Error("INVALID_SESSION_COOKIE");
    }
    matches.push(value);
  }
  if (matches.length > 1) throw new Error("INVALID_SESSION_COOKIE");
  return matches.length === 1 ? `${SESSION_COOKIE_NAME}=${matches[0]}` : null;
}

function browserSetCookie(value, browserCookieName) {
  if (browserCookieName === SESSION_COOKIE_NAME) return value;
  const parts = value.split(";").map((part) => part.trim());
  const prefix = `${SESSION_COOKIE_NAME}=`;
  if (!parts[0]?.startsWith(prefix)) throw new Error("INVALID_SESSION_COOKIE");
  const cookieValue = parts[0].slice(prefix.length);
  if (/[\r\n\0;,]/.test(cookieValue) || Buffer.byteLength(cookieValue, "utf8") > MAX_SESSION_COOKIE_BYTES) {
    throw new Error("INVALID_SESSION_COOKIE");
  }
  return [
    `${browserCookieName}=${cookieValue}`,
    ...parts.slice(1).filter((part) => part.toLowerCase() !== "secure"),
  ].join("; ");
}

function writeRequestIsSameOrigin(request, publicOrigin) {
  if (!WRITE_METHODS.has(request.method)) return true;
  const origin = request.headers.get("origin");
  if (!origin || origin === "null" || origin.includes(",")) return false;
  let expectedOrigin;
  try {
    expectedOrigin = publicOrigin?.origin ?? new URL(request.url).origin;
  } catch {
    return false;
  }
  if (origin !== expectedOrigin) return false;
  const fetchSite = request.headers.get("sec-fetch-site");
  return !fetchSite || fetchSite === "same-origin";
}

function csrfRefererIsSameOrigin(request, publicOrigin) {
  const referer = request.headers.get("referer");
  if (!referer) return false;
  try {
    const expectedOrigin = publicOrigin?.origin ?? new URL(request.url).origin;
    const parsed = new URL(referer);
    return parsed.origin === expectedOrigin && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function createAbortScope(clientSignal, timeoutMs) {
  const controller = new AbortController();
  let cause = null;
  const abortForClient = () => {
    if (controller.signal.aborted) return;
    cause = "client";
    controller.abort(new DOMException("Client request aborted", "AbortError"));
  };
  if (clientSignal?.aborted) abortForClient();
  else clientSignal?.addEventListener("abort", abortForClient, { once: true });

  const timer = timeoutMs === null ? null : setTimeout(() => {
    if (controller.signal.aborted) return;
    cause = "timeout";
    controller.abort(new DOMException("Gateway timeout", "TimeoutError"));
  }, timeoutMs);
  return {
    signal: controller.signal,
    get cause() { return cause; },
    cleanup() {
      if (timer !== null) clearTimeout(timer);
      clientSignal?.removeEventListener("abort", abortForClient);
    },
  };
}

async function waitWithAbort(operation, signal, onAbort) {
  const pending = Promise.resolve(operation);
  if (signal.aborted) {
    pending.catch(() => undefined);
    await onAbort?.();
    throw signal.reason;
  }
  let abortListener;
  const aborted = new Promise((_resolve, reject) => {
    abortListener = () => {
      Promise.resolve()
        .then(() => onAbort?.())
        .catch(() => undefined)
        .finally(() => reject(signal.reason));
    };
    signal.addEventListener("abort", abortListener, { once: true });
  });
  try {
    return await Promise.race([pending, aborted]);
  } finally {
    signal.removeEventListener("abort", abortListener);
  }
}

function cancellationError(scope, trace) {
  if (scope.cause === "client") return createBffSafeError(499, "CLIENT_REQUEST_ABORTED", trace);
  if (scope.cause === "timeout") return createBffSafeError(504, "GATEWAY_TIMEOUT", trace, true);
  return null;
}

async function boundedWebsiteResponse(upstream, maximum, signal) {
  const declared = upstream.headers.get("content-length");
  if (declared !== null && (!/^\d+$/u.test(declared) || Number(declared) > maximum)) {
    await upstream.body?.cancel(); throw new Error("UPSTREAM_RESPONSE_INVALID");
  }
  if (!upstream.body) return new Uint8Array();
  const reader = upstream.body.getReader(); const chunks = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await waitWithAbort(reader.read(), signal, () => reader.cancel());
      if (done) break;
      if (value.byteLength > maximum - size) throw new Error("UPSTREAM_RESPONSE_INVALID");
      chunks.push(value); size += value.byteLength;
    }
    const body = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    return body;
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

// Only the browser question POST uses this transport. Node's fetch otherwise
// imposes its own headers/body deadlines even when our abort timer is absent.
async function requestQuestionWithoutFetchTimeout(destination, init) {
  const { request } = destination.protocol === 'https:' ? await import('node:https') : await import('node:http');
  const { Readable, pipeline } = await import('node:stream');
  return new Promise((resolve, reject) => {
    const headers = new Headers(init.headers);
    headers.set('accept-encoding', 'identity');
    const outgoing = request(destination, {
      method: init.method, headers: Object.fromEntries(headers), signal: init.signal,
      agent: false, timeout: 0,
    }, incoming => {
      // Response errors can arrive while the optional decoder is being loaded.
      incoming.on('error', reject);
      const respond = async () => {
        const responseHeaders = new Headers();
        for (let index = 0; index < incoming.rawHeaders.length; index += 2) {
          responseHeaders.append(incoming.rawHeaders[index], incoming.rawHeaders[index + 1]);
        }
        const status = incoming.statusCode;
        if (new Set([204, 205, 304]).has(status) || (status >= 300 && status < 400)) {
          incoming.destroy();
          return new Response(null, { status, headers: responseHeaders });
        }
        let stream = incoming;
        const encoding = responseHeaders.get('content-encoding')?.toLowerCase().trim();
        if (encoding && encoding !== 'identity') {
          const { createGunzip, createInflate, createBrotliDecompress } = await import('node:zlib');
          const decoder = encoding === 'gzip' ? createGunzip() : encoding === 'deflate' ? createInflate()
            : encoding === 'br' ? createBrotliDecompress() : null;
          if (!decoder) throw new Error('UPSTREAM_RESPONSE_INVALID');
          // pipeline propagates socket errors and stream cancellation in both directions.
          pipeline(incoming, decoder, () => {});
          stream = decoder;
          responseHeaders.delete('content-encoding');
          responseHeaders.delete('content-length');
        }
        return new Response(Readable.toWeb(stream), { status, headers: responseHeaders });
      };
      respond().then(resolve, error => { incoming.destroy(); reject(error); });
    });
    outgoing.on('socket', socket => socket.setTimeout(0));
    outgoing.on('upgrade', (_incoming, socket) => {
      socket.destroy();
      reject(new Error('UPSTREAM_UPGRADE_REJECTED'));
    });
    outgoing.on('error', reject);
    outgoing.end(init.body === undefined ? undefined : Buffer.from(init.body));
  });
}

export function createBffProxy({
  baseUrl, publicOrigin, fetchImpl, timeoutMs = 10_000,
  questionTimeoutMs = GROUNDED_QUESTION_TIMEOUT_MS,
  browserCookieName = SESSION_COOKIE_NAME,
}) {
  if (!(baseUrl instanceof URL)) {
    throw new BffConfigurationError("BFF_INTERNAL_API_URL_REQUIRED");
  }
  if (questionTimeoutMs !== null && (typeof questionTimeoutMs !== 'number' || !Number.isFinite(questionTimeoutMs)
      || questionTimeoutMs <= 0 || questionTimeoutMs > 2_147_483_647)) {
    throw new BffConfigurationError('BFF_QUESTION_TIMEOUT_INVALID');
  }
  if (publicOrigin !== undefined && !(publicOrigin instanceof URL)) {
    throw new BffConfigurationError("BFF_PUBLIC_GATEWAY_URL_REQUIRED");
  }
  if (!new Set([SESSION_COOKIE_NAME, WSL_HTTP_SESSION_COOKIE_NAME]).has(browserCookieName)) {
    throw new BffConfigurationError("BFF_SESSION_COOKIE_NAME_INVALID");
  }
  const defaultFetch = fetchImpl === undefined;
  const forwardFetch = defaultFetch ? fetch : fetchImpl;
  return async function proxy(request, pathSegments, providedTrace) {
    const trace = providedTrace ?? createBffTraceId(request);
    const route = routeFor(request.method, pathSegments);
    if (!route) return createBffSafeError(404, "RESOURCE_UNAVAILABLE", trace);
    if (route.methodRejected) return createBffSafeError(405, "METHOD_NOT_ALLOWED", trace);
    if (!writeRequestIsSameOrigin(request, publicOrigin)) {
      return createBffSafeError(403, "CSRF_VALIDATION_FAILED", trace);
    }
    if (route.csrfProvenanceRequired && !csrfRefererIsSameOrigin(request, publicOrigin)) {
      return createBffSafeError(403, "CSRF_VALIDATION_FAILED", trace);
    }
    if (route.notebookConversation) {
      const query=new URL(request.url).searchParams;
      if ((query.has("limit") && (!/^[1-9][0-9]{0,2}$/u.test(query.get("limit")) || Number(query.get("limit"))>100))
          || (query.has("before") && !/^[A-Za-z0-9_-]{1,2048}$/u.test(query.get("before")))) {
        return createBffSafeError(400,"INVALID_REQUEST",trace);
      }
    }
    if (route.notebookContextSelection) {
      const values = [...new URL(request.url).searchParams];
      if (values.length > 1 || values.some(([key, value]) => !route.query.has(key)
        || !SAFE_SEGMENT.test(value) || /\s/u.test(value))) return createBffSafeError(400, "INVALID_REQUEST", trace);
    }
    if (route.requiredQuery) {
      const query = new URL(request.url).searchParams;
      if ([...query.keys()].some((key) => !route.requiredQuery.has(key))
        || [...route.requiredQuery].some((key) => query.getAll(key).length !== 1 || !SAFE_SEGMENT.test(query.get(key)))) {
        return createBffSafeError(400, "INVALID_REQUEST", trace);
      }
    }
    const websiteQuery = route.websiteSourceQuery && new URL(request.url).searchParams.has("website_source_version_id");
    if (websiteQuery) {
      const query = new URL(request.url).searchParams;
      if ([...query.keys()].some(key => !route.query.has(key)) || ["notebook_id", "website_source_version_id"].some(key => query.getAll(key).length !== 1 || !SAFE_SEGMENT.test(query.get(key)))) {
        return createBffSafeError(400, "INVALID_REQUEST", trace);
      }
    }
    if ((route.websiteImport || route.lawPost) && exactMediaType(request.headers.get("content-type")) !== "application/json") {
      return createBffSafeError(415, "UNSUPPORTED_MEDIA_TYPE", trace);
    }
    if (route.strictQuery) {
      const query = new URL(request.url).searchParams;
      if ([...query.keys()].some((key) => !route.query.has(key) || query.getAll(key).length !== 1)) {
        return createBffSafeError(400, "INVALID_REQUEST", trace);
      }
      if (route.operationsStatus && ((query.has("scope") && !["self","workspace_visible"].includes(query.get("scope")))
          || (query.has("include_readiness") && query.get("include_readiness")!=="1")
          || (query.has('include_capacity') && query.get('include_capacity')!=='1'))) {
        return createBffSafeError(400, "INVALID_REQUEST", trace);
      }
    }
    if (route.organizationVersionQuery) {
      const query = new URL(request.url).searchParams;
      const version = query.get("expected_version");
      if ([...query.keys()].some(key => key !== "expected_version") || query.getAll("expected_version").length !== 1
        || !/^[1-9][0-9]*$/u.test(version || "") || !Number.isSafeInteger(Number(version))) {
        return createBffSafeError(422, "INVALID_REQUEST", trace);
      }
    }
    const expectedProfileSession = (route.profileSessionBinding || route.orgSessionBinding) ? request.headers.get("x-daon-expected-session-id") : null;
    if ((route.profileSessionBinding || route.orgSessionBinding) && (typeof expectedProfileSession !== "string" || !SAFE_SEGMENT.test(expectedProfileSession))) {
      return createBffSafeError(422, "ACCOUNT_INPUT_INVALID", trace);
    }
    const destination = new URL(route.path, baseUrl);
    if (new Set(["/api/v1/session/tenants", "/api/v1/session/tenant"]).has(route.path)) {
      destination.search = new URL(request.url).search;
    }
    if (route.query) {
      const incoming = new URL(request.url);
      for (const [key, value] of incoming.searchParams) {
        if (route.query.has(key)) destination.searchParams.append(key, value);
      }
    }
    const headers = new Headers();
    for (const [key, value] of request.headers) {
      if (REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
    }
    if (route.profileSessionBinding || route.orgSessionBinding) headers.set("X-Daon-Expected-Session-Id", expectedProfileSession);
    if (route.studioExport && request.headers.has("x-daon-step-up-authorization")) {
      headers.set("x-daon-step-up-authorization", request.headers.get("x-daon-step-up-authorization"));
    }
    let credential;
    const nativeQuestion = defaultFetch && request.method === 'POST'
      && route.groundedQuestion && route.path.endsWith('/questions');
    let upstream;
    try {
      credential = sessionCookie(request, browserCookieName);
    } catch {
      return createBffSafeError(400, "INVALID_SESSION_COOKIE", trace);
    }
    if (route.requiresSessionCookie && !credential) {
      return createBffSafeError(401, "AUTHENTICATION_REQUIRED", trace);
    }
    if (route.androidFormAuthorization && (
      request.headers.get("sec-fetch-mode") !== "navigate"
      || request.headers.get("sec-fetch-dest") !== "document"
    )) return createBffSafeError(403, "CSRF_VALIDATION_FAILED", trace);
    if (route.androidFormAuthorization && new URL(request.url).search) {
      return createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
    }
    if (credential) headers.set("cookie", credential);
    headers.set("x-trace-id", trace);
    headers.set("x-daon-bff-transport", "internal");
    if (route.csrfProvenanceRequired) {
      headers.set("x-daon-csrf-origin", publicOrigin?.origin ?? new URL(request.url).origin);
      headers.set("x-daon-csrf-referer", request.headers.get("referer"));
    }

    const init = { method: request.method, headers, redirect: "manual" };
    const abortScope = createAbortScope(
      request.signal,
      route.law ? 30_000 : route.websiteImport || websiteQuery ? 25_000 : route.groundedQuestion
        ? (route.path.endsWith('/questions') ? questionTimeoutMs : questionTimeoutMs ?? 100_000) : timeoutMs,
    );
    try {
      if (abortScope.signal.aborted) return cancellationError(abortScope, trace);
      if (request.method !== "GET" && request.method !== "HEAD") {
        let body;
        try {
          body = await waitWithAbort(route.law ? boundedWebsiteResponse(request,32768,abortScope.signal) : request.arrayBuffer(), abortScope.signal);
        } catch {
          return cancellationError(abortScope, trace)
            ?? createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
        }
        if (body.byteLength > (route.maxRequestBytes ?? MAX_REQUEST_BYTES)) {
          return createBffSafeError(413, "REQUEST_TOO_LARGE", trace);
        }
        if (route.androidFormAuthorization) {
          if (exactMediaType(request.headers.get("content-type")) !== "application/x-www-form-urlencoded") {
            return createBffSafeError(415, "UNSUPPORTED_MEDIA_TYPE", trace);
          }
          let entries;
          try {
            entries = [...new URLSearchParams(new TextDecoder("utf-8", { fatal: true }).decode(body))];
          } catch {
            return createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
          }
          if (entries.length !== 1 || entries[0][0] !== "transaction_id" || !SAFE_SEGMENT.test(entries[0][1])) {
            return createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
          }
          headers.set("content-type", "application/json");
          init.body = JSON.stringify({ transaction_id: entries[0][1] });
        } else {
          init.body = body;
          const providerRegistration = request.method === "POST" && route.path === "/api/v1/admin/provider-connections"
            || request.method === "PUT" && /^\/api\/v1\/admin\/provider-connections\/[^/]+$/u.test(route.path);
          if (providerRegistration) {
            let value;
            try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body)); } catch { /* 기존 API 본문 오류 처리를 유지한다. */ }
            if (value && !Array.isArray(value) && value.base_url === "server-managed-asr") {
              if (value.adapter_type !== "asr_base_native" || (value.provider_code !== undefined && value.provider_code !== "CUSTOM")
                || (request.method === "POST" && value.provider_code !== "CUSTOM")
                || value.credential_requirement !== "none" || value.access_mode !== "public"
                || Object.hasOwn(value, "credential") || Object.hasOwn(value, "test_credential")) {
                return createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
              }
              init.body = JSON.stringify({ ...value, base_url: "http://asr-base:8080" });
            }
          }
        }
      }

      try {
        upstream = await waitWithAbort(
          (nativeQuestion ? requestQuestionWithoutFetchTimeout : forwardFetch)(destination, { ...init, signal: abortScope.signal }),
          abortScope.signal,
        );
      } catch {
        return cancellationError(abortScope, trace)
          ?? createBffSafeError(503, "GATEWAY_UNAVAILABLE", trace, true);
      }
      if (upstream.status >= 300 && upstream.status < 400) {
        return createBffSafeError(502, "UPSTREAM_REDIRECT_REJECTED", trace);
      }
      if (route.androidFormAuthorization) {
        if (upstream.status !== 200) {
          const status = new Set([400, 401, 403, 404, 409, 410, 422, 429]).has(upstream.status)
            ? upstream.status : 502;
          return createBffSafeError(status, "ANDROID_AUTHORIZATION_FAILED", trace);
        }
        if (upstream.headers.has("set-cookie")
          || exactMediaType(upstream.headers.get("content-type")) !== "application/json") {
          return createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        }
        const declaredLength = upstream.headers.get("content-length");
        if (declaredLength !== null && (!/^\d+$/u.test(declaredLength)
          || Number(declaredLength) > MAX_ANDROID_AUTH_RESPONSE_BYTES)) {
          return createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        }
        let payload;
        try {
          const body = await waitWithAbort(upstream.arrayBuffer(), abortScope.signal, () => upstream.body?.cancel());
          if (body.byteLength > MAX_ANDROID_AUTH_RESPONSE_BYTES) throw new Error("ANDROID_AUTH_RESPONSE_TOO_LARGE");
          payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
        } catch {
          return cancellationError(abortScope, trace)
            ?? createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        }
        const location = androidAuthorizationLocation(payload?.data?.authorization_url);
        if (!location) return createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        return new Response(null, { status: 303, headers: {
          Location: location, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
          "X-Trace-Id": trace,
        } });
      }
      const responseHeaders = new Headers();
      for (const [key, value] of upstream.headers) {
        if (!RESPONSE_HEADERS.has(key.toLowerCase())) continue;
        try {
          responseHeaders.set(
            key,
            key.toLowerCase() === "set-cookie" ? browserSetCookie(value, browserCookieName) : value,
          );
        } catch {
          return createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        }
      }
      responseHeaders.set("Cache-Control", responseHeaders.get("Content-Type")?.split(";",1)[0].trim()==="audio/wav" ? "private, no-store" : "no-store");
      if (!responseHeaders.has("x-trace-id")) responseHeaders.set("x-trace-id", trace);
      let body = null;
      if (upstream.status !== 204) {
        try {
          body = await waitWithAbort(
            route.law || route.website || websiteQuery ? boundedWebsiteResponse(upstream, route.law ? 4_194_304 : websiteQuery ? 8_388_608 : 32_768, abortScope.signal) : upstream.arrayBuffer(),
            abortScope.signal,
            () => upstream.body?.cancel(),
          );
        } catch {
          return cancellationError(abortScope, trace)
            ?? createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        }
      }
      return new Response(body, { status: upstream.status, headers: responseHeaders });
    } finally {
      if (nativeQuestion && upstream?.body && !upstream.body.locked) {
        await upstream.body.cancel().catch(() => {});
      }
      abortScope.cleanup();
    }
  };
}

export function createNativeBffProxy({ baseUrl, publicOrigin, fetchImpl = fetch, timeoutMs = 10_000 }) {
  if (!(baseUrl instanceof URL)) throw new BffConfigurationError("BFF_INTERNAL_API_URL_REQUIRED");
  if (publicOrigin !== undefined && !(publicOrigin instanceof URL)) {
    throw new BffConfigurationError("BFF_PUBLIC_GATEWAY_URL_REQUIRED");
  }
  return async function nativeProxy(request, pathSegments, providedTrace) {
    const trace = providedTrace ?? createBffTraceId(request);
    const route = nativeRouteFor(request.method, pathSegments, request.url);
    if (!route) return createBffSafeError(404, "RESOURCE_UNAVAILABLE", trace);
    if (route.methodRejected) return createBffSafeError(405, "METHOD_NOT_ALLOWED", trace);
    if (request.headers.has("cookie")) return createBffSafeError(400, "COOKIE_NOT_ALLOWED", trace);

    const bearer = route.protected ? nativeBearer(request) : null;
    if (route.protected && !bearer) return createBffSafeError(401, "AUTHENTICATION_REQUIRED", trace);
    const destination = new URL(route.path, baseUrl);
    if (new Set(["/api/v1/session/tenants", "/api/v1/session/tenant"]).has(route.path)) {
      destination.search = new URL(request.url).search;
    }
    if (route.query) {
      const incoming = new URL(request.url);
      for (const [key, value] of incoming.searchParams) {
        if (route.query.has(key)) destination.searchParams.append(key, value);
      }
    }
    const headers = new Headers();
    for (const [key, value] of request.headers) {
      if (REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
    }
    if (bearer) headers.set("authorization", `Bearer ${bearer}`);
    headers.set("x-trace-id", trace);
    headers.set("x-daon-bff-transport", "internal");

    const init = { method: request.method, headers, redirect: "manual" };
    const sensitiveValues = bearer ? [bearer] : [];
    const abortScope = createAbortScope(request.signal, timeoutMs);
    try {
      if (abortScope.signal.aborted) return cancellationError(abortScope, trace);
      if (request.method !== "GET" && request.method !== "HEAD") {
        let body;
        try {
          body = await waitWithAbort(request.arrayBuffer(), abortScope.signal);
        } catch {
          return cancellationError(abortScope, trace) ?? createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
        }
        if (body.byteLength > (route.maxRequestBytes ?? MAX_REQUEST_BYTES)) {
          return createBffSafeError(413, "REQUEST_TOO_LARGE", trace);
        }
        sensitiveValues.push(...nativeSensitiveBodyValues(body, route.path));
        if (body.byteLength > 0 && !(route.requestMediaTypes ?? [route.requestMediaType]).includes(exactMediaType(request.headers.get("content-type")))
          && route.path !== "/api/v1/session/tenant") {
          return createBffSafeError(415, "UNSUPPORTED_MEDIA_TYPE", trace);
        }
        init.body = body;
      }

      let upstream;
      try {
        upstream = await waitWithAbort(fetchImpl(destination, { ...init, signal: abortScope.signal }), abortScope.signal);
      } catch {
        return cancellationError(abortScope, trace) ?? createBffSafeError(503, "GATEWAY_UNAVAILABLE", trace, true);
      }
      if (upstream.status >= 300 && upstream.status < 400) {
        return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
      }
      if (upstream.headers.has("set-cookie")) return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
      for (const [key, value] of upstream.headers) {
        if (
          NATIVE_RESPONSE_HEADERS.has(key.toLowerCase())
          && (value.includes(baseUrl.origin) || sensitiveValues.some((sensitive) => value.includes(sensitive)))
        ) {
          return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
        }
      }
      const transferEncoding = upstream.headers.get("transfer-encoding");
      const rawLength = upstream.headers.get("content-length");
      if (transferEncoding && rawLength) return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
      if (transferEncoding && transferEncoding.toLowerCase() !== "chunked") {
        return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
      }
      const declaredLength = rawLength === null || /^\d+$/u.test(rawLength) ? Number(rawLength) : Number.NaN;
      const maxResponseBytes = route.maxResponseBytes ?? MAX_NATIVE_RESPONSE_BYTES;
      if (!Number.isFinite(declaredLength) || declaredLength > maxResponseBytes) {
        return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
      }

      let body = null;
      if (upstream.status !== 204) {
        const contentType = upstream.headers.get("content-type")?.toLowerCase() ?? "";
        const expectedTypes = route.responseMediaTypes ?? [route.expectsPdf ? "application/pdf" : "application/json"];
        if (!expectedTypes.includes(exactMediaType(contentType))) return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
        try {
          body = await waitWithAbort(upstream.arrayBuffer(), abortScope.signal, () => upstream.body?.cancel());
        } catch {
          return cancellationError(abortScope, trace) ?? createBffSafeError(502, "UPSTREAM_RESPONSE_INVALID", trace);
        }
        if (body.byteLength > maxResponseBytes || (rawLength !== null && body.byteLength !== declaredLength)) {
          return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
        }
        const textBody = Buffer.from(body).toString("utf8");
        if (textBody.includes(baseUrl.origin) || sensitiveValues.some((value) => textBody.includes(value))) {
          return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
        }
      }

      const responseHeaders = new Headers();
      for (const [key, value] of upstream.headers) {
        if (NATIVE_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
      }
      responseHeaders.set("Content-Length", String(body?.byteLength ?? 0));
      responseHeaders.set("Cache-Control", responseHeaders.get("Content-Type")?.split(";",1)[0].trim()==="audio/wav" ? "private, no-store" : "no-store");
      if (!responseHeaders.has("x-trace-id")) responseHeaders.set("x-trace-id", trace);
      return new Response(body, { status: upstream.status, headers: responseHeaders });
    } finally {
      abortScope.cleanup();
    }
  };
}
