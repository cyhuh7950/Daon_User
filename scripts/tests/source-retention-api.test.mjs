import assert from "node:assert/strict";
import test from "node:test";
import * as retentionApi from "../../apps/web/lib/source-retention-api.js";

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

test("S16 삭제 POST는 현재 Notebook과 Source 추가 인증을 같은 요청 키로 결속한다", async () => {
  const calls = [];
  const controller = new AbortController();
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return response({ ...VIEW, state: "cleanup_pending", grace_until: VIEW.requested_at });
  };
  for (const stepUpAuthorization of ["opaque-grant-first", "opaque-grant-after-login"]) {
    const result = await requestSourceDeletion("source-1", {
      notebookId: "notebook-1", stepUpAuthorization,
      fetchImpl, signal: controller.signal, idempotencyKey: "idempotency-delete-s16",
    });
    assert.equal(result.data.state, "cleanup_pending");
  }
  assert.deepEqual(calls.map(({ url, options }) => ({ url, body: JSON.parse(options.body), key: options.headers["Idempotency-Key"] })), [
    { url: "/bff/api/sources/source-1/deletion-requests", body: { notebook_id: "notebook-1", step_up_authorization_id: "opaque-grant-first" }, key: "idempotency-delete-s16" },
    { url: "/bff/api/sources/source-1/deletion-requests", body: { notebook_id: "notebook-1", step_up_authorization_id: "opaque-grant-after-login" }, key: "idempotency-delete-s16" },
  ]);
  assert.ok(calls.every(({ options }) => options.signal === controller.signal && options.credentials === "same-origin"));
});

test("S16 삭제의 Notebook 또는 추가 인증 누락은 network 전에 거부한다", async () => {
  let calls = 0;
  for (const input of [{}, { notebookId: "notebook-1" }, { stepUpAuthorization: "opaque-grant" },
    { notebookId: "../notebook", stepUpAuthorization: "opaque-grant" }]) {
    await assert.rejects(requestSourceDeletion("source-1", {
      ...input, idempotencyKey: "idempotency-delete-s16",
      fetchImpl: async () => { calls += 1; return response(VIEW); },
    }), /DELETION_INPUT_INVALID/u);
  }
  assert.equal(calls, 0);
});

test("S16 Source 추가 인증은 기존 그룹과 정확 Source에만 결속하고 응답을 검증한다", async () => {
  const calls = [];
  const controller = new AbortController();
  const password = "fixture-confirmation-not-real";
  const authorization = await retentionApi.issueSourceDeletionStepUp("source-1", password, {
    idempotencyKey: "s16-source-confirmation", signal: controller.signal,
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return Response.json({ data: { step_up_authorization: "opaque-source-grant" } });
    },
  });
  assert.equal(authorization, "opaque-source-grant");
  assert.equal(calls[0].url, "/bff/api/session/step-up");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    action_group: "permanent_delete_or_restore_rollback", target_id: "source-1",
    password, ttl_seconds: 300,
  });
  assert.equal(calls[0].init.signal, controller.signal);
  assert.equal(calls[0].init.credentials, "same-origin");
  await assert.rejects(retentionApi.issueSourceDeletionStepUp("../bad", password, {
    idempotencyKey: "s16-source-confirmation", fetchImpl: async () => { throw Error("network forbidden"); },
  }), /DELETION_INPUT_INVALID/u);
  await assert.rejects(retentionApi.issueSourceDeletionStepUp("source-1", password, {
    idempotencyKey: "s16-source-confirmation", fetchImpl: async () => Response.json({ data: {} }),
  }), /STEP_UP_RESPONSE_INVALID/u);
});

test("S16 cleanup 실패는 성공으로 바꾸지 않고 기존 exact DTO로 전달한다", async () => {
  const result = await getSourceDeletionRequest("request-1", { fetchImpl: async () => response({
    ...VIEW, state: "failed", cleanup_items: VIEW.cleanup_items.map(item => ({ ...item, state: "failed", attempt_count: 1 })),
  }) });
  assert.equal(result.data.state, "failed");
  assert.ok(result.data.cleanup_items.every(item => item.state === "failed"));
});

test("S16 Source 삭제 요청 client는 inventory 없이 same-origin request/get/cancel만 호출한다", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    return response(VIEW);
  };
  await requestSourceDeletion("source-1", { fetchImpl, idempotencyKey: "idempotency-delete-0001", notebookId: "notebook-1", stepUpAuthorization: "opaque-grant" });
  await getSourceDeletionRequest("request-1", { fetchImpl });
  await cancelSourceDeletionRequest("request-1", {
    fetchImpl, idempotencyKey: "idempotency-cancel-0001", etag: '"deletion:request-1:1"',
  });
  assert.deepEqual(calls.map((call) => [call.url, call.options.method, call.options.body]), [
    ["/bff/api/sources/source-1/deletion-requests", "POST", '{"notebook_id":"notebook-1","step_up_authorization_id":"opaque-grant"}'],
    ["/bff/api/deletion-requests/request-1", "GET", undefined],
    ["/bff/api/deletion-requests/request-1/cancel", "POST", "{}"],
  ]);
  assert.equal(calls.every((call) => call.options.credentials === "same-origin"), true);
  assert.equal(calls.some((call) => /inventory|purge/u.test(call.options.body ?? "")), false);
});

test("S16 Source 삭제 요청 client는 invalid ID/ETag를 network 전에 차단한다", async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return response(VIEW); };
  await assert.rejects(requestSourceDeletion("../source", { fetchImpl, idempotencyKey: "idempotency-delete-0001" }), /DELETION_INPUT_INVALID/u);
  await assert.rejects(cancelSourceDeletionRequest("request-1", { fetchImpl, idempotencyKey: "idempotency-cancel-0001", etag: "*" }), /DELETION_INPUT_INVALID/u);
  assert.equal(calls, 0);
});

test("S16 Source 삭제 요청 client는 exact DTO identity/state/time/ETag를 fail-close 검증한다", async () => {
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
        notebookId: "notebook-1", stepUpAuthorization: "opaque-grant",
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
