import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const read = (file) => readFile(path.join(root, file), "utf8");

test("NotebookLM Source 추가 UI는 화면 배치를 유지한 채 입력 유형을 제공한다", async () => {
  const shell = await read("packages/ui/src/product-workspace-shell.jsx");
  for (const label of ["파일 업로드", "웹사이트", "Drive", "복사한 텍스트"]) assert.match(shell, new RegExp(label));
  assert.match(shell, /sourceAddMode/);
  assert.match(shell, /Source 등록·처리 중입니다/);
  assert.match(shell, /SOURCE_UPLOAD_RESPONSE_INVALID/);
  assert.equal(shell.includes('accept="application/pdf" onChange={uploadPdf}'), false);
});

test("Source 업로드 클라이언트는 same-origin generic Source 계약을 사용한다", async () => {
  const api = await read("apps/web/lib/source-upload-api.js");
  const workspace = await read("apps/web/components/actual-workspace.jsx");
  assert.match(api, /export async function uploadSource/);
  assert.match(api, /Content-Type\": file\.type/);
  assert.match(api, /\/bff\/api\/workspaces/);
  assert.doesNotMatch(api, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/u);
  assert.match(workspace, /uploadSource:/);
});

test("Notebook 컨텍스트 어댑터는 notebook 범위로 generic Source를 전달한다", async () => {
  const adapter = await read("packages/ui/src/notebook-context-adapter.js");
  assert.match(adapter, /uploadSource:/);
  assert.match(adapter, /notebookId: context\.notebook_id/);
});

test("연결형 Source는 실제 same-origin Adapter 계약으로 연결된다", async () => {
  const workspace = await read("apps/web/components/actual-workspace.jsx");
  const api = await read("apps/web/lib/product-workspace-api.js");
  const shell = await read("packages/ui/src/product-workspace-shell.jsx");
  assert.match(workspace, /listWorkspaceConnectors/);
  assert.match(workspace, /reconnectWorkspaceConnector/);
  assert.match(workspace, /listConnectors:/);
  assert.match(workspace, /reconnectConnector:/);
  assert.match(api, /\/bff\/api\/workspaces\/\$\{encodeURIComponent\(workspace\)\}\/connectors/);
  assert.doesNotMatch(api, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL/u);
  assert.match(shell, /연결형 Source/);
  assert.match(shell, /사용 불가/);
  assert.match(shell, /연결형 Source를 불러오지 못했습니다/);
});

test("Connector 오류코드는 민감정보 문자열 검증에 걸리지 않는다", async () => {
  const { listWorkspaceConnectors } = await import("../../apps/web/lib/product-workspace-api.js");
  const connector = {
    connector_id: "mcp-open-law-go-kr", kind: "mcp", name: "국가법령정보센터",
    status: "unavailable", source_count: 0, endpoint_label: "open.law.go.kr",
    last_checked_at: null, error_code: "CONNECTOR_CREDENTIAL_REQUIRED",
  };
  const result = await listWorkspaceConnectors("workspace-001", {
    fetchImpl: async () => new Response(JSON.stringify({
      data: { connectors: [connector] },
      meta: { trace_id: "trace-001", workspace_id: "workspace-001" },
    }), { status: 200, headers: { "content-type": "application/json" } }),
  });
  assert.equal(result[0].error_code, "CONNECTOR_CREDENTIAL_REQUIRED");
});
