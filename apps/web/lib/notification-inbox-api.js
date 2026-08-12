const JSON_HEADERS = Object.freeze({ "Content-Type": "application/json" });

async function asResult(response) {
  return {
    ok: response.ok,
    status: response.status,
    payload: response.ok ? await response.json() : null,
  };
}

export const notificationInboxApi = Object.freeze({
  async list(mode) {
    const path = mode === "notifications"
      ? "/bff/api/notifications?limit=50"
      : "/bff/api/inbox?limit=50";
    return asResult(await fetch(path, { credentials: "same-origin", cache: "no-store" }));
  },

  async markRead(item) {
    const response = await fetch(`/bff/api/notifications/${encodeURIComponent(item.id)}`, {
      method: "PATCH",
      credentials: "same-origin",
      headers: {
        ...JSON_HEADERS,
        "If-Match": `"notification-${item.version}"`,
        "Idempotency-Key": `notification-read-${crypto.randomUUID()}`,
      },
      body: JSON.stringify({ state: "read" }),
    });
    return asResult(response);
  },
});
