import assert from "node:assert/strict";
import test from "node:test";

import {
  listWorkspaceKnowledgePackages,
  listWorkspaceSources,
} from "../../apps/web/lib/product-workspace-api.js";
import { uploadPdfSource } from "../../apps/web/lib/source-upload-api.js";

const source = Object.freeze({
  source_id: "source-1", source_version_id: "version-1", filename: "approved.pdf",
  source_state: "ready", processing_state: "completed", job_state: "completed",
});

const canonSourceStates = Object.freeze([
  "registered", "security_check", "processing", "indexing", "ready", "waiting_model",
  "partial_understanding", "needs_review", "failed", "expired", "disabled", "deleting", "deleted",
]);

test("Source 목록은 same-origin GET과 exact Safe DTO만 허용한다", async () => {
  const calls = [];
  const result = await listWorkspaceSources("workspace-1", {
    notebookId: "notebook-1",
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return Response.json({
        data: { sources: [source] },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
      });
    },
  });
  assert.deepEqual(result, [source]);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/sources?notebook_id=notebook-1");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("Source 목록은 unknown field와 Workspace 불일치를 거부한다", async () => {
  for (const payload of [
    { data: { sources: [{ ...source, fixture: true }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } },
    { data: { sources: [source] }, meta: { trace_id: "trace-1", workspace_id: "workspace-other" } },
  ]) {
    await assert.rejects(
      listWorkspaceSources("workspace-1", { notebookId: "notebook-1", fetchImpl: async () => Response.json(payload) }),
      { message: "SOURCE_LIST_RESPONSE_INVALID" },
    );
  }
});

test("Source 목록은 Canon의 진행·분기·종료 상태를 모두 안전 DTO로 반환한다", async () => {
  for (const source_state of canonSourceStates) {
    const projected = { ...source, source_state };
    const result = await listWorkspaceSources("workspace-1", {
      notebookId: "notebook-1",
      fetchImpl: async () => Response.json({
        data: { sources: [projected] },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
      }),
    });
    assert.deepEqual(result, [projected]);
  }
});

test("Source 목록은 Canon 밖의 unknown state를 계속 거부한다", async () => {
  await assert.rejects(
    listWorkspaceSources("workspace-1", {
      notebookId: "notebook-1",
      fetchImpl: async () => Response.json({
        data: { sources: [{ ...source, source_state: "unexpected" }] },
        meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
      }),
    }),
    { message: "SOURCE_LIST_RESPONSE_INVALID" },
  );
});

test("Source 목록은 서버의 safe retryable 판정만 Error에 보존한다", async () => {
  for (const retryable of [true, false]) {
    await assert.rejects(
      listWorkspaceSources("workspace-1", {
        notebookId: "notebook-1",
        fetchImpl: async () => Response.json({
          error: {
            code: "STUDIO_DATABASE_UNAVAILABLE", message: "요청을 처리하지 못했습니다.",
            retryable, user_action: "잠시 후 다시 시도하세요.", trace_id: "trace-source-retry-1",
          },
        }, { status: 503 }),
      }),
      (error) => error?.message === "STUDIO_DATABASE_UNAVAILABLE" && error?.retryable === retryable,
    );
  }
});

test("PDF upload은 same-origin PDF 계약과 processing metadata를 보존한다", async () => {
  const calls = [];
  const file = new File(["%PDF-1.4"], "guide.pdf", { type: "application/pdf" });
  const result = await uploadPdfSource("workspace-1", file, {
    notebookId: "notebook-1", idempotencyKey: "upload-source-contract-0001",
    fetchImpl: async () => Response.json({
      data: {
        source_id: "source-1", source_version_id: "version-1", object_id: "a".repeat(32),
        digest_sha256: "b".repeat(64), byte_size: 8, status: "accepted", replayed: false,
        processing_run_id: "processing-1", processing_state: "processing", job_state: "queued",
      }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" },
    }, { status: 202 }),
  });
  assert.equal(result.processing_run_id, "processing-1");
  assert.equal(result.status, "accepted");
});

test("PDF upload 응답이 invalid이면 안전하게 fail-close한다", async () => {
  const file = new File(["%PDF-1.4"], "guide.pdf", { type: "application/pdf" });
  await assert.rejects(
    () => uploadPdfSource("workspace-1", file, {
      notebookId: "notebook-1", idempotencyKey: "upload-source-invalid-0001",
      fetchImpl: async () => Response.json({ data: { source_id: "source-1" }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } }, { status: 202 }),
    }),
    { message: "PDF_UPLOAD_RESPONSE_INVALID" },
  );
});

test("PDF upload HTTP 오류는 safe code와 retryable을 보존한다", async () => {
  const file = new File(["%PDF-1.4"], "guide.pdf", { type: "application/pdf" });
  await assert.rejects(
    () => uploadPdfSource("workspace-1", file, {
      notebookId: "notebook-1", idempotencyKey: "upload-source-error-0001",
      fetchImpl: async () => Response.json({ error: { code: "SOURCE_STORAGE_UNAVAILABLE", retryable: true } }, { status: 503 }),
    }),
    (error) => error?.message === "SOURCE_STORAGE_UNAVAILABLE" && error?.retryable === true,
  );
});

test("PDF upload은 PDF가 아닌 파일과 최대 크기 초과를 전송 전에 차단한다", async () => {
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return Response.json({}); };
  await assert.rejects(
    () => uploadPdfSource("workspace-1", new File(["text"], "notes.txt", { type: "text/plain" }), {
      notebookId: "notebook-1", fetchImpl,
    }),
    { message: "PDF_UPLOAD_INPUT_INVALID" },
  );
  const oversized = { name: "large.pdf", type: "application/pdf", size: 25 * 1024 * 1024 + 1 };
  await assert.rejects(
    () => uploadPdfSource("workspace-1", oversized, { notebookId: "notebook-1", fetchImpl }),
    { message: "PDF_UPLOAD_INPUT_INVALID" },
  );
  assert.equal(calls, 0);
});

const knowledgePackage = Object.freeze({
  package_id: "knowledge-package-1",
  producer: "daon2_5",
  producer_version: "2.5.7",
  knowledge_registration_id: "knowledge-registration-1",
  output_version_id: "output-version-7",
  authority: "approved",
  registration_state: "registered",
  review_state: "approved",
  digest_sha256: "a".repeat(64),
  byte_size: 4096,
  content_type: "application/vnd.daon.knowledge+json",
  effective_at: "2026-08-14T00:00:00Z",
  expires_at: "2027-08-14T00:00:00Z",
});

test("검증된 Knowledge Snapshot 목록은 same-origin exact DTO로만 수용한다", async () => {
  const calls = [];
  const result = await listWorkspaceKnowledgePackages("workspace-1", {
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return Response.json({ data: { items: [knowledgePackage] }, meta: { trace_id: "trace-knowledge-1" } });
    },
  });
  assert.deepEqual(result, [knowledgePackage]);
  assert.equal(calls[0].url, "/bff/api/workspaces/workspace-1/knowledge-packages");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.credentials, "same-origin");
});

test("Knowledge Snapshot 목록은 미승인·만료·unknown field를 fail-close한다", async () => {
  for (const item of [
    { ...knowledgePackage, authority: "user_context" },
    { ...knowledgePackage, review_state: "pending" },
    { ...knowledgePackage, expires_at: "2025-08-14T00:00:00Z" },
    { ...knowledgePackage, internal_url: "http://internal.invalid" },
  ]) {
    await assert.rejects(
      listWorkspaceKnowledgePackages("workspace-1", {
        now: () => Date.parse("2026-08-15T00:00:00Z"),
        fetchImpl: async () => Response.json({ data: { items: [item] }, meta: { trace_id: "trace-knowledge-1" } }),
      }),
      { message: "KNOWLEDGE_PACKAGE_LIST_RESPONSE_INVALID" },
    );
  }
});
