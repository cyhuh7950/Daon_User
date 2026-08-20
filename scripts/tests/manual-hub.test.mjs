import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

const manifest = {
  schema_version: 1,
  release_version: "1.0.0",
  released_at: "2026-08-16",
  language: "ko-KR",
  documents: [
    {
      document_id: "daon-getting-started",
      title: "Daon Getting Started",
      summary: "첫 작업 흐름",
      auth_scope: "public_and_authenticated",
      version: "1.0.0",
      language: "ko-KR",
      assets: {
        markdown: { filename: "daon-getting-started.md", href: "/manual/daon-getting-started.md", bytes: 4, sha256: "a".repeat(64), mime: "text/markdown; charset=utf-8" },
        docx: { filename: "daon-getting-started.docx", href: "/manual/daon-getting-started.docx", bytes: 4, sha256: "b".repeat(64), mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
        pdf: { filename: "daon-getting-started.pdf", href: "/manual/daon-getting-started.pdf", bytes: 4, sha256: "c".repeat(64), mime: "application/pdf" },
      },
    },
    {
      document_id: "daon-user-manual", title: "Daon 사용자 설명서", summary: "전체 절차", auth_scope: "public_and_authenticated", version: "1.0.0", language: "ko-KR",
      assets: {
        markdown: { filename: "daon-user-manual.md", href: "/manual/daon-user-manual.md", bytes: 4, sha256: "d".repeat(64), mime: "text/markdown; charset=utf-8" },
        docx: { filename: "daon-user-manual.docx", href: "/manual/daon-user-manual.docx", bytes: 4, sha256: "e".repeat(64), mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
        pdf: { filename: "daon-user-manual.pdf", href: "/manual/daon-user-manual.pdf", bytes: 4, sha256: "f".repeat(64), mime: "application/pdf" },
      },
    },
    {
      document_id: "daon-knowledge-llm-guide", title: "Daon 지식·LLM 활용 가이드", summary: "지식 활용", auth_scope: "public_and_authenticated", version: "1.0.0", language: "ko-KR",
      assets: {
        markdown: { filename: "daon-knowledge-llm-guide.md", href: "/manual/daon-knowledge-llm-guide.md", bytes: 4, sha256: "1".repeat(64), mime: "text/markdown; charset=utf-8" },
        docx: { filename: "daon-knowledge-llm-guide.docx", href: "/manual/daon-knowledge-llm-guide.docx", bytes: 4, sha256: "2".repeat(64), mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
        pdf: { filename: "daon-knowledge-llm-guide.pdf", href: "/manual/daon-knowledge-llm-guide.pdf", bytes: 4, sha256: "3".repeat(64), mime: "application/pdf" },
      },
    },
  ],
};

test("Manual Hub client는 allowlisted same-origin manifest만 읽고 bytes/hash/MIME를 검증한다", async () => {
  const api = await import("../../apps/web/lib/manual-api.js");
  const encoder = new TextEncoder();
  const markdownText = "# Daon Getting Started\n\n본문";
  const body = encoder.encode(markdownText);
  const digest = await crypto.subtle.digest("SHA-256", body);
  const sha256 = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
  const safeManifest = structuredClone(manifest);
  safeManifest.documents[0].assets.markdown.bytes = body.byteLength;
  safeManifest.documents[0].assets.markdown.sha256 = sha256;
  const requests = [];
  const fetchImpl = async (path) => {
    requests.push(path);
    if (path === "/manual/manifest.json") return Response.json(safeManifest);
    return new Response(body, { headers: { "Content-Type": "text/markdown; charset=utf-8" } });
  };
  const projected = await api.getManualManifest({ fetchImpl });
  const document = await api.readManualDocument("daon-getting-started", { fetchImpl, manifest: projected });
  assert.equal(document.text, markdownText);
  assert.deepEqual(requests, ["/manual/manifest.json", "/manual/daon-getting-started.md"]);

  for (const bad of [
    { ...safeManifest, release_version: "../1.0.0" },
    { ...safeManifest, documents: [{ ...safeManifest.documents[0], document_id: "../secret" }] },
    { ...safeManifest, documents: [{ ...safeManifest.documents[0], assets: { ...safeManifest.documents[0].assets, markdown: { ...safeManifest.documents[0].assets.markdown, href: "https://internal.invalid/manual.md" } } }] },
  ]) {
    await assert.rejects(api.getManualManifest({ fetchImpl: async () => Response.json(bad) }), /MANUAL_MANIFEST_INVALID/u);
  }

  const unapprovedRelease = structuredClone(safeManifest);
  unapprovedRelease.release_version = "9.9.9";
  for (const document of unapprovedRelease.documents) document.version = "9.9.9";
  await assert.rejects(
    api.getManualManifest({ fetchImpl: async () => Response.json(unapprovedRelease) }),
    /MANUAL_MANIFEST_INVALID/u,
  );

  const rogueDocument = structuredClone(safeManifest);
  const rogue = rogueDocument.documents[0];
  rogue.document_id = "rogue-document";
  for (const [kind, extension] of [["markdown", "md"], ["docx", "docx"], ["pdf", "pdf"]]) {
    rogue.assets[kind].filename = `rogue-document.${extension}`;
    rogue.assets[kind].href = `/manual/rogue-document.${extension}`;
  }
  await assert.rejects(
    api.getManualManifest({ fetchImpl: async () => Response.json(rogueDocument) }),
    /MANUAL_MANIFEST_INVALID/u,
  );

  const mixedVersion = structuredClone(safeManifest);
  mixedVersion.documents[1].version = "9.9.9";
  await assert.rejects(
    api.getManualManifest({ fetchImpl: async () => Response.json(mixedVersion) }),
    /MANUAL_MANIFEST_INVALID/u,
  );
  await assert.rejects(api.readManualDocument("unknown", { fetchImpl, manifest: projected }), /MANUAL_DOCUMENT_NOT_FOUND/u);
  await assert.rejects(api.readManualDocument("daon-getting-started", {
    manifest: projected,
    fetchImpl: async () => new Response(body, { headers: { "Content-Type": "text/html" } }),
  }), /MANUAL_CONTENT_INVALID/u);
});

test("Web 읽기는 caller duck manifest를 Network 전에 다시 검증한다", async () => {
  const api = await import("../../apps/web/lib/manual-api.js");
  for (const mutate of [
    (value) => { value.documents[0].assets.markdown.href = "https://internal.invalid/manual.md"; },
    (value) => { value.documents[0].assets.markdown.href = "/manual/../secret.md"; },
    (value) => { value.documents[0].document_id = "rogue-document"; },
    (value) => { value.documents[0].version = "9.9.9"; },
  ]) {
    const duckManifest = structuredClone(manifest);
    mutate(duckManifest);
    let fetchCount = 0;
    await assert.rejects(
      api.readManualDocument("daon-getting-started", {
        manifest: duckManifest,
        fetchImpl: async () => { fetchCount += 1; return new Response(); },
      }),
      /MANUAL_MANIFEST_INVALID/u,
    );
    assert.equal(fetchCount, 0);
  }
});

test("DOCX/PDF 다운로드는 caller duck manifest를 Network 전에 다시 검증한다", async () => {
  const api = await import("../../apps/web/lib/manual-api.js");
  for (const mutate of [
    (value) => { value.documents[0].assets.docx.mime = "application/octet-stream"; },
    (value) => { value.documents[0].assets.pdf.sha256 = "not-a-sha"; },
    (value) => { delete value.documents[0].assets.docx.bytes; },
    (value) => { value.release_version = "9.9.9"; },
  ]) {
    const duckManifest = structuredClone(manifest);
    mutate(duckManifest);
    let fetchCount = 0;
    await assert.rejects(
      api.downloadManualAsset("daon-getting-started", "docx", {
        manifest: duckManifest,
        fetchImpl: async () => { fetchCount += 1; return new Response(); },
      }),
      /MANUAL_MANIFEST_INVALID/u,
    );
    assert.equal(fetchCount, 0);
  }
});

test("Workspace 설정은 검색·Web 읽기·Release·DOCX/PDF 다운로드 Hub를 제공한다", async () => {
  const [shell, actual, css, dockerfile] = await Promise.all([
    read("packages/ui/src/product-workspace-shell.jsx"),
    read("apps/web/components/actual-workspace.jsx"),
    read("packages/ui/src/workspace.css"),
    read("apps/web/Dockerfile"),
  ]);
  assert.match(shell, /사용자 설명서/u);
  assert.match(shell, /manualSearch/u);
  assert.match(shell, /Release/u);
  assert.match(shell, /DOCX/u);
  assert.match(shell, /PDF/u);
  assert.match(actual, /getManualManifest/u);
  assert.match(actual, /readManualDocument/u);
  assert.match(actual, /downloadManualAsset/u);
  assert.match(css, /manual-hub/u);
  assert.match(dockerfile, /COPY --from=builder \/app\/apps\/web\/public \.\/apps\/web\/public/u);
});
