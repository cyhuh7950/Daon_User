import assert from "node:assert/strict";
import test from "node:test";

import {
  cancelSourceDeletionRequest,
  getSourceDeletionRequest,
  requestSourceDeletion,
} from "../../apps/web/lib/source-retention-api.js";

function response(data, etag = '"deletion:request-1:1"') {
  return new Response(JSON.stringify({ data, meta: { trace_id: "trace-1" } }), {
    status: 200,
    headers: { "Content-Type": "application/json", ETag: etag },
  });
}

const VIEW = Object.freeze({
  request_id: "request-1", tenant_id: "tenant-1", workspace_id: "workspace-1",
  source_id: "source-1", state: "grace_period", version: 1,
  requested_at: "2026-08-21T00:00:00Z", grace_until: "2026-09-20T00:00:00Z",
  source_active: false,
  cleanup_items: ["original_content", "index", "preview", "cache", "known_local_copy", "sync_reference"].map((kind) => ({
    kind, reference_id: `inventory-${kind}`, state: "pending", attempt_count: 0, evidence: null,
  })),
  completed_references: [], source_version_mutations: 0,
});

test("Source 삭제 요청 client는 inventory 없이 same-origin request/get/cancel만 호출한다", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    return response(VIEW);
  };
  await requestSourceDeletion("source-1", { fetchImpl, idempotencyKey: "idempotency-delete-0001" });
  await getSourceDeletionRequest("request-1", { fetchImpl });
  await cancelSourceDeletionRequest("request-1", {
    fetchImpl, idempotencyKey: "idempotency-cancel-0001", etag: '"deletion:request-1:1"',
  });
  assert.deepEqual(calls.map((call) => [call.url, call.options.method, call.options.body]), [
    ["/bff/api/sources/source-1/deletion-requests", "POST", "{}"],
    ["/bff/api/deletion-requests/request-1", "GET", undefined],
    ["/bff/api/deletion-requests/request-1/cancel", "POST", "{}"],
  ]);
  assert.equal(calls.every((call) => call.options.credentials === "same-origin"), true);
  assert.equal(calls.some((call) => /inventory|purge/u.test(call.options.body ?? "")), false);
});

test("Source 삭제 요청 client는 invalid ID/ETag를 network 전에 차단한다", async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return response(VIEW); };
  await assert.rejects(requestSourceDeletion("../source", { fetchImpl, idempotencyKey: "idempotency-delete-0001" }), /DELETION_INPUT_INVALID/u);
  await assert.rejects(cancelSourceDeletionRequest("request-1", { fetchImpl, idempotencyKey: "idempotency-cancel-0001", etag: "*" }), /DELETION_INPUT_INVALID/u);
  assert.equal(calls, 0);
});

test("Source 삭제 요청 client는 exact DTO identity/state/time/ETag를 fail-close 검증한다", async () => {
  const invalid = [
    { ...VIEW, state: "unknown" },
    { ...VIEW, grace_until: "not-a-time" },
    { ...VIEW, internal_path: "/secret" },
    { ...VIEW, source_id: "other-source" },
  ];
  for (const data of invalid) {
    await assert.rejects(
      requestSourceDeletion("source-1", {
        fetchImpl: async () => response(data), idempotencyKey: "idempotency-delete-0001",
      }),
      /DELETION_RESPONSE_INVALID/u,
    );
  }
  await assert.rejects(
    getSourceDeletionRequest("request-1", {
      fetchImpl: async () => response(VIEW, '"deletion:other-request:1"'),
    }),
    /DELETION_RESPONSE_INVALID/u,
  );
});
