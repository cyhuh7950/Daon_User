import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { getWorkspaceOperationsStatus } from "../../apps/web/lib/product-workspace-api.js";

const ids = ["document_pending", "document_retry_wait", "document_leased", "document_dead_letter", "object_pending", "object_retry_wait", "object_leased", "object_dead_letter", "object_oldest_pending_seconds", "source_waiting_model", "index_records_current", "source_storage_bytes", "reported_prompt_tokens", "reported_completion_tokens", "reported_total_tokens", "attempts_without_reported_usage"];
const observations = () => ids.map((metric_id, index) => ({ metric_id, state: "not_collected", value: null,
  unit: index === 8 ? "seconds" : index === 11 ? "bytes" : index >= 12 && index <= 14 ? "tokens" : "count",
  observed_at: null, coverage: index < 9 ? "workspace_operational_counts" : "actor_accessible_records", safe_code: "METRIC_NOT_COLLECTED" }));

const payload = {
  data: {
    workspace_id: "workspace-1",
    overall_status: "warning",
    checked_at: "2026-08-15T00:00:00Z",
    components: [
      { component_id: "provider", status: "ready", safe_code: "PROVIDER_READY", pending_count: 0, recovery_action: "none" },
      { component_id: "api", status: "ready", safe_code: "API_READY", pending_count: 0, recovery_action: "none" },
      { component_id: "storage", status: "ready", safe_code: "STORAGE_READY", pending_count: 0, recovery_action: "none" },
      { component_id: "sync", status: "warning", safe_code: "SYNC_PENDING", pending_count: 2, recovery_action: "open_sync_settings" },
      { component_id: "queue", status: "warning", safe_code: "QUEUE_ATTENTION_REQUIRED", pending_count: 3, recovery_action: "refresh_status" },
    ],
    observations: observations(),
  },
  meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
};

test("운영상태 Adapter는 exact 5개 안전 상태만 same-origin으로 수용한다", async () => {
  const calls = [];
  const result = await getWorkspaceOperationsStatus("workspace-1", {
    fetchImpl: async (url, init) => {
      calls.push({ url, method: init.method, credentials: init.credentials });
      return Response.json(payload);
    },
  });
  assert.equal(result.overall_status, "warning");
  assert.deepEqual(result.components.map((item) => item.component_id), ["provider", "api", "storage", "sync", "queue"]);
  assert.equal(result.observations.length, 16);
  assert.deepEqual(calls, [{
    url: "/bff/api/workspaces/workspace-1/operations/status",
    method: "GET",
    credentials: "same-origin",
  }]);
});

test("관측 Adapter는 exact16·null/partial/단위/범위/UTC를 엄격 검증한다", async () => {
  // 정확 DTO facet 대조이며 전체 JSON Schema validator 실행 증거는 아니다.
  const schemas = JSON.parse(await readFile(new URL("../../packages/contracts/openapi/v1/openapi.json", import.meta.url), "utf8")).components.schemas;
  const array = schemas.OperationsStatus.properties.observations;
  assert.deepEqual([array.minItems, array.maxItems, array.items], [16, 16, false]);
  assert.deepEqual(array.prefixItems.map(item => item.allOf[1].properties.metric_id.const), ids);
  assert.equal(schemas.OperationsObservation.additionalProperties, false);
  assert.deepEqual(schemas.OperationsObservation.required, ["metric_id", "state", "value", "unit", "observed_at", "coverage", "safe_code"]);
  assert.equal(schemas.OperationsObservation.properties.value.maximum, Number.MAX_SAFE_INTEGER);
  const valid = structuredClone(payload);
  valid.data.observations[12] = { ...valid.data.observations[12], state: "partial", value: 7,
    observed_at: "2026-10-19T00:00:00Z", safe_code: "METRIC_PARTIAL" };
  const result = await getWorkspaceOperationsStatus("workspace-1", { fetchImpl: async () => Response.json(valid) });
  assert.equal(result.observations[12].value, 7);
  assert.equal(result.observations[13].value, null);
  for (const mutate of [
    p => p.data.observations.pop(), p => p.data.observations.push(p.data.observations[0]),
    p => { p.data.observations[1] = p.data.observations[0]; },
    p => { p.data.observations[0].metric_id = "worker_heartbeat"; },
    p => { p.data.observations[0].value = 0; },
    p => { p.data.observations[0].unit = "bytes"; },
    p => { p.data.observations[0].coverage = "actor_accessible_records"; },
    p => { p.data.observations[12].value = true; }, p => { p.data.observations[12].value = -1; },
    p => { p.data.observations[12].state = ["partial"]; },
    p => { p.data.observations[12].observed_at = null; },
    p => { p.data.observations[12].observed_at = "2026-02-30T00:00:00Z"; },
    p => { p.data.observations[12].observed_at = "2026-10-19T09:00:00+09:00"; },
    p => { p.data.observations[12].safe_code = "METRIC_RECORDED"; },
    p => { p.data.observations[0].raw_content = "S44_NOT_FOR_DISPLAY"; },
  ]) {
    const invalid = structuredClone(valid); mutate(invalid);
    await assert.rejects(getWorkspaceOperationsStatus("workspace-1", { fetchImpl: async () => Response.json(invalid) }), /OPERATIONS_STATUS_RESPONSE_INVALID/u);
  }
});

test("운영상태 Adapter는 unknown 필드와 내부값을 거부한다", async () => {
  await assert.rejects(
    getWorkspaceOperationsStatus("workspace-1", {
      fetchImpl: async () => Response.json({
        ...payload,
        data: { ...payload.data, internal_url: "http://database:5432" },
      }),
    }),
    /OPERATIONS_STATUS_RESPONSE_INVALID/u,
  );
});
