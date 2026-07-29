const MAX_REQUEST_BYTES = 65_536;
const SAFE_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const REQUEST_HEADERS = new Set([
  "cookie",
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
  "x-trace-id",
]);
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
    if (parsed.protocol !== "https:") {
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
  if (segments.length === 1 && segments[0] === "session") {
    return method === "GET" ? { path: "/api/v1/session", query: false } : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "access-decisions") {
    return method === "POST" ? { path: "/api/v1/access-decisions", query: false } : { methodRejected: true };
  }
  if (segments.length === 1 && segments[0] === "audit-events") {
    return method === "GET" ? { path: "/api/v1/audit-events", query: true } : { methodRejected: true };
  }
  if (
    segments.length === 4
    && segments[0] === "workspaces"
    && SAFE_SEGMENT.test(segments[1])
    && segments[2] === "authorization"
    && segments[3] === "evaluations"
  ) {
    return method === "POST"
      ? { path: `/api/v1/workspaces/${encodeURIComponent(segments[1])}/authorization/evaluations`, query: false }
      : { methodRejected: true };
  }
  return null;
}

function traceId(request) {
  const supplied = request.headers.get("x-trace-id");
  return supplied && SAFE_SEGMENT.test(supplied)
    ? supplied
    : `trace-bff-${crypto.randomUUID()}`;
}

function safeError(status, code, trace, retryable = false) {
  return Response.json({
    error: {
      code,
      message: "요청을 처리하지 못했습니다.",
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

export function createBffProxy({ baseUrl, fetchImpl = fetch, timeoutMs = 10_000 }) {
  if (!(baseUrl instanceof URL)) {
    throw new BffConfigurationError("BFF_INTERNAL_API_URL_REQUIRED");
  }
  return async function proxy(request, pathSegments) {
    const trace = traceId(request);
    const route = routeFor(request.method, pathSegments);
    if (!route) return safeError(404, "RESOURCE_UNAVAILABLE", trace);
    if (route.methodRejected) return safeError(405, "METHOD_NOT_ALLOWED", trace);

    const destination = new URL(route.path, baseUrl);
    if (route.query) {
      const incoming = new URL(request.url);
      for (const [key, value] of incoming.searchParams) {
        if (AUDIT_QUERY.has(key)) destination.searchParams.append(key, value);
      }
    }
    const headers = new Headers();
    for (const [key, value] of request.headers) {
      if (REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
    }
    headers.set("x-trace-id", trace);

    const init = { method: request.method, headers, redirect: "manual" };
    if (request.method !== "GET" && request.method !== "HEAD") {
      const body = await request.arrayBuffer();
      if (body.byteLength > MAX_REQUEST_BYTES) {
        return safeError(413, "REQUEST_TOO_LARGE", trace);
      }
      init.body = body;
    }

    let upstream;
    try {
      upstream = await fetchImpl(destination, {
        ...init,
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      const timeout = error?.name === "TimeoutError" || error?.name === "AbortError";
      return safeError(timeout ? 504 : 503, timeout ? "GATEWAY_TIMEOUT" : "GATEWAY_UNAVAILABLE", trace, true);
    }
    if (upstream.status >= 300 && upstream.status < 400) {
      return safeError(502, "UPSTREAM_REDIRECT_REJECTED", trace);
    }
    const responseHeaders = new Headers();
    for (const [key, value] of upstream.headers) {
      if (RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
    }
    responseHeaders.set("Cache-Control", "no-store");
    if (!responseHeaders.has("x-trace-id")) responseHeaders.set("x-trace-id", trace);
    const body = upstream.status === 204 ? null : await upstream.arrayBuffer();
    return new Response(body, { status: upstream.status, headers: responseHeaders });
  };
}
