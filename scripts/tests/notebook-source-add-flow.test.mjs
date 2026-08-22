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
