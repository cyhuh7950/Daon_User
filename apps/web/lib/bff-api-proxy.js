const MAX_REQUEST_BYTES = 65_536;
const MAX_SESSION_COOKIE_BYTES = 4_096;
const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const REQUEST_HEADERS = new Set([
  "content-type",
  "idempotency-key",
  "if-match",
  "traceparent",
  "x-trace-id",
]);
const RESPONSE_HEADERS = new Set([
  "cache-control",
  "content-type",
  "etag",
  "retry-after",
  "set-cookie",
  "x-trace-id",
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
const RESTORE_ACTIONS = new Set(["execute", "cancel"]);

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

function routeFor(method, segments) {
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

function writeRequestIsSameOrigin(request) {
  if (!WRITE_METHODS.has(request.method)) return true;
  const origin = request.headers.get("origin");
  if (!origin || origin === "null" || origin.includes(",")) return false;
  let expectedOrigin;
  try {
    expectedOrigin = new URL(request.url).origin;
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

export function createBffProxy({ baseUrl, fetchImpl = fetch, timeoutMs = 10_000 }) {
  if (!(baseUrl instanceof URL)) {
    throw new BffConfigurationError("BFF_INTERNAL_API_URL_REQUIRED");
  }
  return async function proxy(request, pathSegments, providedTrace) {
    const trace = providedTrace ?? createBffTraceId(request);
    const route = routeFor(request.method, pathSegments);
    if (!route) return createBffSafeError(404, "RESOURCE_UNAVAILABLE", trace);
    if (route.methodRejected) return createBffSafeError(405, "METHOD_NOT_ALLOWED", trace);
    if (!writeRequestIsSameOrigin(request)) {
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
    const abortScope = createAbortScope(request.signal, timeoutMs);
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
        if (body.byteLength > MAX_REQUEST_BYTES) {
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
