import {
  createBffProxy,
  parseInternalApiBase,
  parsePublicGatewayOrigin,
} from "./bff-api-proxy.js";
import { SESSION_COOKIE_NAME } from "./session-cookie.js";

function validProjection(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    && typeof value.is_system_admin === "boolean"
    && typeof value.password_change_required === "boolean";
}

export async function getServerAdminAccess(sessionCookie, { fetchImpl = fetch } = {}) {
  const baseUrl = parseInternalApiBase(
    process.env.DAON_API_INTERNAL_URL,
    process.env.DAON_RUNTIME_PROFILE ?? "production",
  );
  const publicOrigin = parsePublicGatewayOrigin(
    process.env.DAON_PUBLIC_GATEWAY_URL,
    process.env.DAON_BFF_PROFILE ?? "production",
  );
  const proxy = createBffProxy({
    baseUrl,
    publicOrigin,
    fetchImpl: (url, init) => fetchImpl(url, { ...init, cache: "no-store" }),
  });
  const request = new Request(new URL("/bff/api/session", publicOrigin), {
    headers: { Cookie: `${SESSION_COOKIE_NAME}=${sessionCookie}` },
  });
  const response = await proxy(request, ["session"]);
  if (response.status === 401) return "unauthenticated";
  if (!response.ok) throw new Error("ADMIN_SESSION_UNAVAILABLE");
  let payload;
  try { payload = await response.json(); } catch { throw new Error("ADMIN_SESSION_UNAVAILABLE"); }
  if (!validProjection(payload?.data)) throw new Error("ADMIN_SESSION_UNAVAILABLE");
  if (payload.data.password_change_required) return "password_change_required";
  return payload.data.is_system_admin ? "authorized" : "forbidden";
}
