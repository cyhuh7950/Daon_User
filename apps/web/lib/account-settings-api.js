const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const isId = (value) => typeof value === "string" && ID.test(value);
const DATE = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/u;
const date = (value) => typeof value === "string" && DATE.test(value) && Number.isFinite(Date.parse(value));
const exact = (value, keys) => value !== null && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const invalid = () => { throw new Error("ACCOUNT_RESPONSE_INVALID"); };
const name = (value) => typeof value === "string" && value === value.trim()
  && [...value].length >= 1 && [...value].length <= 80 && !/\p{Cc}/u.test(value);
const version = (value) => Number.isSafeInteger(value) && value >= 0;
const profile = (value) => exact(value, ["display_name", "version"]) && version(value.version)
  && (value.version === 0 ? value.display_name === null : name(value.display_name));

async function request(path, { method = "GET", payload, ...options } = {}) {
  if (method !== "GET" && !isId(options.idempotencyKey)) throw new Error("ACCOUNT_INPUT_INVALID");
  let response, body;
  try {
    response = await (options.fetchImpl ?? fetch)(`/bff/api/${path}`, {
      method, credentials: "same-origin", cache: "no-store", signal: options.signal,
      ...(method === "GET" ? {} : { headers: { "Content-Type": "application/json", "Idempotency-Key": options.idempotencyKey,
        ...(path === "account/profile" && method === "PATCH" ? { "X-Daon-Expected-Session-Id": options.expectedSessionId } : {}) }, body: JSON.stringify(payload) }),
    });
    body = await response.json();
  } catch (error) {
    if (options.signal?.aborted) throw new Error("ACCOUNT_REQUEST_ABORTED");
    throw new Error(method === "GET" ? "ACCOUNT_READ_FAILED" : "ACCOUNT_RESULT_UNKNOWN");
  }
  if (path === "account/profile" && method === "PATCH" && response.status === 409 && body?.error?.code === "ACCOUNT_SESSION_CHANGED") throw new Error("ACCOUNT_SESSION_CHANGED");
  if (!response.ok) throw new Error(({ 401: "AUTHENTICATION_REQUIRED", 403: "ACCOUNT_FORBIDDEN", 409: "ACCOUNT_IDEMPOTENCY_CONFLICT",
    412: "ACCOUNT_VERSION_CONFLICT", 422: "ACCOUNT_INPUT_INVALID", 503: "ACCOUNT_UNAVAILABLE" })[response.status] ?? "ACCOUNT_REQUEST_FAILED");
  return body;
}
function envelope(body, valid, mutation = false) {
  if (!exact(body, ["data", "meta"]) || !exact(body.meta, mutation ? ["trace_id", "replayed"] : ["trace_id"])
      || !isId(body.meta.trace_id) || mutation && typeof body.meta.replayed !== "boolean" || !valid(body.data)) invalid();
  return Object.freeze({ ...body.data, ...(mutation ? { replayed: body.meta.replayed } : {}) });
}
export async function getAccountProfile(options = {}) {
  return envelope(await request("account/profile", options), profile);
}
export async function saveAccountProfile(input, options = {}) {
  if (!isId(options.expectedSessionId) || !exact(input, ["display_name", "expected_version"]) || !name(input.display_name) || !version(input.expected_version)
      || input.expected_version === Number.MAX_SAFE_INTEGER) throw new Error("ACCOUNT_INPUT_INVALID");
  return envelope(await request("account/profile", { ...options, method: "PATCH", payload: input }),
    (data) => exact(data, ["display_name", "version", "replayed"]) && profile({ display_name: data.display_name, version: data.version })
      && data.version > 0 && typeof data.replayed === "boolean");
}
async function list(kind, input, options) {
  if (!exact(input, Object.keys(input).filter((key) => ["limit", "cursor"].includes(key)))) throw new Error("ACCOUNT_INPUT_INVALID");
  const limit = input.limit ?? 20;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100 || input.cursor !== undefined && (typeof input.cursor !== "string" || !input.cursor || input.cursor.length > 2048)) throw new Error("ACCOUNT_INPUT_INVALID");
  const query = new URLSearchParams({ limit: String(limit) });
  if (input.cursor !== undefined) query.set("cursor", input.cursor);
  const device = kind === "devices";
  const fields = device ? ["device_id", "platform", "state", "last_seen_at", "is_current"] : ["session_id", "device_id", "client_kind", "state", "expires_at", "is_current"];
  const data = envelope(await request(`account/${kind}?${query}`, options), (value) => exact(value, ["items", "next_cursor"])
    && Array.isArray(value.items) && value.items.length <= limit && (value.next_cursor === null || typeof value.next_cursor === "string" && value.next_cursor.length > 0 && value.next_cursor.length <= 2048)
    && value.items.every((item) => exact(item, fields) && isId(item.device_id) && typeof item.is_current === "boolean"
      && (device ? ["web", "windows", "android", "ios"].includes(item.platform) && ["registered", "trusted", "revoked"].includes(item.state) && date(item.last_seen_at)
        : isId(item.session_id) && ["web", "native"].includes(item.client_kind) && ["active", "revoked"].includes(item.state) && date(item.expires_at)))
    && new Set(value.items.map((item) => item[device ? "device_id" : "session_id"])).size === value.items.length
    && value.items.filter((item) => item.is_current).length <= 1);
  return Object.freeze({ items: Object.freeze(data.items.map((item) => Object.freeze({ ...item }))), next_cursor: data.next_cursor });
}
export const listAccountDevices = (input = {}, options = {}) => list("devices", input, options);
export const listAccountSessions = (input = {}, options = {}) => list("sessions", input, options);
export async function requestAccountStepUp(targetId, password, options = {}) {
  if (!isId(targetId) || typeof password !== "string" || !password) throw new Error("ACCOUNT_INPUT_INVALID");
  return envelope(await request("session/step-up", { ...options, method: "POST", payload: { password,
    action_group: "device_session_or_sync_key_revoke", target_id: targetId } }),
  (data) => exact(data, ["step_up_authorization", "issued_at", "expires_at"]) && typeof data.step_up_authorization === "string"
    && data.step_up_authorization.length >= 40 && data.step_up_authorization.length <= 512 && date(data.issued_at) && date(data.expires_at));
}
export async function trustAccountDevice(deviceId, options = {}) {
  if (!isId(deviceId)) throw new Error("ACCOUNT_INPUT_INVALID");
  const body = await request(`devices/${encodeURIComponent(deviceId)}/trust`, { ...options, method: "POST", payload: {} });
  if (!exact(body, ["data", "trace_id"]) || !isId(body.trace_id) || !exact(body.data, ["id", "kind", "version", "attributes"])
      || body.data.id !== deviceId || body.data.kind !== "device" || !date(body.data.version)
      || !exact(body.data.attributes, ["state", "replayed"]) || body.data.attributes.state !== "trusted" || typeof body.data.attributes.replayed !== "boolean") invalid();
  return Object.freeze({ device_id: deviceId, state: "trusted", version: body.data.version, replayed: body.data.attributes.replayed });
}
async function revoke(kind, id, authorization, options) {
  if (!isId(id) || typeof authorization !== "string" || authorization.length < 40 || authorization.length > 512) throw new Error("ACCOUNT_INPUT_INVALID");
  const device = kind === "device";
  return envelope(await request(device ? `devices/${encodeURIComponent(id)}/revoke` : "session/revoke", { ...options, method: "POST",
    payload: { ...(!device ? { session_id: id } : {}), step_up_authorization: authorization } }),
  (data) => exact(data, device ? ["device_id", "state", "sync_key_revoke_required"] : ["session_id", "state"])
    && data[device ? "device_id" : "session_id"] === id && data.state === "revoked" && (!device || data.sync_key_revoke_required === true), true);
}
export const revokeAccountDevice = (id, authorization, options = {}) => revoke("device", id, authorization, options);
export const revokeAccountSession = (id, authorization, options = {}) => revoke("session", id, authorization, options);
