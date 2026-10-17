import { secureUuid } from "@daon-user/ui/secure-uuid";

const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

async function asResult(response) {
  return {
    ok: response.ok,
    status: response.status,
    payload: response.ok ? await response.json() : null,
  };
}

export const notificationInboxApi = Object.freeze({
  async list(mode, { signal } = {}) {
    const path = mode === "notifications"
      ? "/bff/api/notifications?limit=50"
      : "/bff/api/inbox?limit=50";
    return asResult(await fetch(path, { credentials: "same-origin", cache: "no-store", signal }));
  },

  async markRead(item, { signal } = {}) {
    const response = await fetch(`/bff/api/notifications/${encodeURIComponent(item.id)}`, {
      method: "PATCH",
      credentials: "same-origin",
      signal,
      headers: {
        ...JSON_HEADERS,
        "If-Match": `"notification-${item.version}"`,
        "Idempotency-Key": `notification-read-${secureUuid()}`,
      },
      body: JSON.stringify({ state: "read" }),
    });
    return asResult(response);
  },
});
