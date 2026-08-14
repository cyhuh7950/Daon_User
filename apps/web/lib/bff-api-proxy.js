const MAX_REQUEST_BYTES = 65_536;
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
]);
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const SESSION_COOKIE_NAME = "__Host-daon_session";
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
  "OPENAI", "OPENROUTER", "UPSTAGE",
]);
const STUDIO_QUERY = new Set(["workspace_id"]);
const RESTORE_ACTIONS = new Set(["execute", "cancel"]);
const GROUNDED_QUESTION_TIMEOUT_MS = 100_000;

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
  if (profile === "production") {
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
  if (
    (profile === "local_test" ? !localTestHttp : parsed.protocol !== "https:")
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

function routeFor(method, segments) {
  if (segments.length === 2 && segments[0] === "session" && segments[1] === "step-up") {
    return method === "POST" ? { path: "/api/v1/session/step-up", query: null } : { methodRejected: true };
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
      ? { path: `/api/v1/studio-outputs/${encodeURIComponent(segments[1])}/versions/${encodeURIComponent(segments[3])}/exports/${segments[5]}`, query: STUDIO_QUERY }
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
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/operations/status`, query: null }
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
          query: null,
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
          query: null,
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
      return { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/sources`, query: null };
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
  } else if (segments.length === 2 && segments[0] === "session" && segments[1] === "refresh") {
    matched = method === "POST" ? route("/api/v1/session/refresh", { protected: false, requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 1 && segments[0] === "session") {
    matched = method === "GET" ? route("/api/v1/session") : { methodRejected: true };
  } else if (segments.length === 3 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "sources") {
    matched = new Set(["GET", "POST"]).has(method)
      ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/sources`, method === "POST" ? { maxRequestBytes: MAX_SOURCE_UPLOAD_BYTES, requestMediaType: "application/pdf" } : {})
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
      ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/citations/${encodeURIComponent(segments[3])}/content`, { expectsPdf: true, maxResponseBytes: MAX_SOURCE_UPLOAD_BYTES })
      : { methodRejected: true };
  } else if (segments.length === 4 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "studio" && segments[3] === "reports") {
    matched = method === "POST" ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio/reports`, { requestMediaType: "application/json" }) : { methodRejected: true };
  } else if (segments.length === 4 && segments[0] === "workspaces" && SAFE_SEGMENT.test(segments[1]) && segments[2] === "studio" && segments[3] === "outputs") {
    matched = method === "GET" ? route(`/api/v1/workspaces/${encodeURIComponent(segments[1])}/studio/outputs`) : { methodRejected: true };
  } else if (segments.length === 1 && segments[0] === "backups") {
    matched = new Set(["GET", "POST"]).has(method) ? route("/api/v1/backups", { maxResponseBytes: MAX_NATIVE_RECOVERY_RESPONSE_BYTES, ...(method === "POST" ? { requestMediaType: "application/json" } : {}) }) : { methodRejected: true };
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
  if (method === "GET" && segments.length === 1 && segments[0] === "backups") {
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

function nativeSensitiveBodyValues(body) {
  try {
    const parsed = JSON.parse(Buffer.from(body).toString("utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
    return ["password", "access_credential", "refresh_credential"]
      .map((key) => parsed[key])
      .filter((value) => typeof value === "string" && value.length > 0);
  } catch {
    return [];
  }
}

function exactMediaType(rawValue) {
  return rawValue?.split(";", 1)[0].trim().toLowerCase() ?? "";
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

function sessionCookie(request) {
  const rawCookie = request.headers.get("cookie");
  if (!rawCookie) return null;
  if (/[\r\n\0]/.test(rawCookie)) throw new Error("INVALID_SESSION_COOKIE");
  const matches = [];
  for (const item of rawCookie.split(";")) {
    const separator = item.indexOf("=");
    if (separator < 0) continue;
    const name = item.slice(0, separator).trim();
    if (name !== SESSION_COOKIE_NAME) continue;
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

  const timer = setTimeout(() => {
    if (controller.signal.aborted) return;
    cause = "timeout";
    controller.abort(new DOMException("Gateway timeout", "TimeoutError"));
  }, timeoutMs);
  timer.unref?.();
  return {
    signal: controller.signal,
    get cause() { return cause; },
    cleanup() {
      clearTimeout(timer);
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

export function createBffProxy({
  baseUrl, publicOrigin, fetchImpl = fetch, timeoutMs = 10_000,
  questionTimeoutMs = GROUNDED_QUESTION_TIMEOUT_MS,
}) {
  if (!(baseUrl instanceof URL)) {
    throw new BffConfigurationError("BFF_INTERNAL_API_URL_REQUIRED");
  }
  if (publicOrigin !== undefined && !(publicOrigin instanceof URL)) {
    throw new BffConfigurationError("BFF_PUBLIC_GATEWAY_URL_REQUIRED");
  }
  return async function proxy(request, pathSegments, providedTrace) {
    const trace = providedTrace ?? createBffTraceId(request);
    const route = routeFor(request.method, pathSegments);
    if (!route) return createBffSafeError(404, "RESOURCE_UNAVAILABLE", trace);
    if (route.methodRejected) return createBffSafeError(405, "METHOD_NOT_ALLOWED", trace);
    if (!writeRequestIsSameOrigin(request, publicOrigin)) {
      return createBffSafeError(403, "CSRF_VALIDATION_FAILED", trace);
    }

    const destination = new URL(route.path, baseUrl);
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
    let credential;
    try {
      credential = sessionCookie(request);
    } catch {
      return createBffSafeError(400, "INVALID_SESSION_COOKIE", trace);
    }
    if (credential) headers.set("cookie", credential);
    headers.set("x-trace-id", trace);
    headers.set("x-daon-bff-transport", "internal");

    const init = { method: request.method, headers, redirect: "manual" };
    const abortScope = createAbortScope(
      request.signal,
      route.groundedQuestion ? questionTimeoutMs : timeoutMs,
    );
    try {
      if (abortScope.signal.aborted) return cancellationError(abortScope, trace);
      if (request.method !== "GET" && request.method !== "HEAD") {
        let body;
        try {
          body = await waitWithAbort(request.arrayBuffer(), abortScope.signal);
        } catch {
          return cancellationError(abortScope, trace)
            ?? createBffSafeError(400, "INVALID_REQUEST_BODY", trace);
        }
        if (body.byteLength > (route.maxRequestBytes ?? MAX_REQUEST_BYTES)) {
          return createBffSafeError(413, "REQUEST_TOO_LARGE", trace);
        }
        init.body = body;
      }

      let upstream;
      try {
        upstream = await waitWithAbort(
          fetchImpl(destination, { ...init, signal: abortScope.signal }),
          abortScope.signal,
        );
      } catch {
        return cancellationError(abortScope, trace)
          ?? createBffSafeError(503, "GATEWAY_UNAVAILABLE", trace, true);
      }
      if (upstream.status >= 300 && upstream.status < 400) {
        return createBffSafeError(502, "UPSTREAM_REDIRECT_REJECTED", trace);
      }
      const responseHeaders = new Headers();
      for (const [key, value] of upstream.headers) {
        if (RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
      }
      responseHeaders.set("Cache-Control", "no-store");
      if (!responseHeaders.has("x-trace-id")) responseHeaders.set("x-trace-id", trace);
      let body = null;
      if (upstream.status !== 204) {
        try {
          body = await waitWithAbort(
            upstream.arrayBuffer(),
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
    if (route.query) destination.search = route.query.toString();
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
        sensitiveValues.push(...nativeSensitiveBodyValues(body));
        if (body.byteLength > 0 && exactMediaType(request.headers.get("content-type")) !== route.requestMediaType) {
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
        const expectedType = route.expectsPdf ? "application/pdf" : "application/json";
        if (exactMediaType(contentType) !== expectedType) return createBffSafeError(502, "GATEWAY_RESPONSE_REJECTED", trace);
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
      responseHeaders.set("Cache-Control", "no-store");
      if (!responseHeaders.has("x-trace-id")) responseHeaders.set("x-trace-id", trace);
      return new Response(body, { status: upstream.status, headers: responseHeaders });
    } finally {
      abortScope.cleanup();
    }
  };
}
