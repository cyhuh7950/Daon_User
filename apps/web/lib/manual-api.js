const MANIFEST_PATH = "/manual/manifest.json";
const APPROVED_RELEASE_VERSION = "1.0.0";
const APPROVED_DOCUMENT_IDS = new Set([
  "daon-getting-started",
  "daon-user-manual",
  "daon-knowledge-llm-guide",
]);
const SAFE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/u;
const SAFE_VERSION = /^\d+\.\d+\.\d+$/u;
const SAFE_DATE = /^\d{4}-\d{2}-\d{2}$/u;
const SAFE_SHA256 = /^[0-9a-f]{64}$/u;
const SAFE_MIME = Object.freeze({
  markdown: "text/markdown; charset=utf-8",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pdf: "application/pdf",
});
const SAFE_EXT = Object.freeze({ markdown: "md", docx: "docx", pdf: "pdf" });
const SAFE_SCOPE = new Set(["public", "authenticated", "public_and_authenticated"]);

function hasExactKeys(value, expected) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const safe = [...expected].sort();
  return actual.length === safe.length && actual.every((key, index) => key === safe[index]);
}

function validAsset(asset, kind, documentId) {
  const extension = SAFE_EXT[kind];
  const expectedFilename = `${documentId}.${extension}`;
  return hasExactKeys(asset, ["filename", "href", "bytes", "sha256", "mime"])
    && asset.filename === expectedFilename
    && asset.href === `/manual/${expectedFilename}`
    && Number.isSafeInteger(asset.bytes) && asset.bytes > 0 && asset.bytes <= 32 * 1024 * 1024
    && SAFE_SHA256.test(asset.sha256)
    && asset.mime === SAFE_MIME[kind];
}

function projectManifest(value) {
  if (
    !hasExactKeys(value, ["schema_version", "release_version", "released_at", "language", "documents"])
    || value.schema_version !== 1
    || !SAFE_VERSION.test(value.release_version) || value.release_version !== APPROVED_RELEASE_VERSION
    || !SAFE_DATE.test(value.released_at)
    || value.language !== "ko-KR"
    || !Array.isArray(value.documents) || value.documents.length !== 3
  ) throw new Error("MANUAL_MANIFEST_INVALID");
  const seen = new Set();
  const documents = value.documents.map((document) => {
    if (
      !hasExactKeys(document, ["document_id", "title", "summary", "auth_scope", "version", "language", "assets"])
      || !SAFE_ID.test(document.document_id) || !APPROVED_DOCUMENT_IDS.has(document.document_id) || seen.has(document.document_id)
      || typeof document.title !== "string" || document.title.length < 3 || document.title.length > 100
      || typeof document.summary !== "string" || document.summary.length < 3 || document.summary.length > 240
      || !SAFE_SCOPE.has(document.auth_scope)
      || document.version !== value.release_version || document.language !== value.language
      || !hasExactKeys(document.assets, ["markdown", "docx", "pdf"])
      || !validAsset(document.assets.markdown, "markdown", document.document_id)
      || !validAsset(document.assets.docx, "docx", document.document_id)
      || !validAsset(document.assets.pdf, "pdf", document.document_id)
    ) throw new Error("MANUAL_MANIFEST_INVALID");
    seen.add(document.document_id);
    return Object.freeze({
      ...document,
      assets: Object.freeze(Object.fromEntries(Object.entries(document.assets).map(([key, asset]) => [key, Object.freeze({ ...asset })]))),
    });
  });
  if (seen.size !== APPROVED_DOCUMENT_IDS.size) throw new Error("MANUAL_MANIFEST_INVALID");
  return Object.freeze({ ...value, documents: Object.freeze(documents) });
}

async function sha256(bytes) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function exactContentType(response, expected) {
  return (response.headers.get("content-type") ?? "").toLowerCase() === expected.toLowerCase();
}

async function fetchAsset(documentId, kind, { fetchImpl, manifest, signal }) {
  if (!SAFE_ID.test(documentId) || !Object.hasOwn(SAFE_MIME, kind)) throw new Error("MANUAL_DOCUMENT_NOT_FOUND");
  const current = manifest === undefined
    ? await getManualManifest({ fetchImpl, signal })
    : projectManifest(manifest);
  const document = current.documents.find((item) => item.document_id === documentId);
  if (!document) throw new Error("MANUAL_DOCUMENT_NOT_FOUND");
  const asset = document.assets[kind];
  const response = await fetchImpl(asset.href, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok || !exactContentType(response, asset.mime)) throw new Error("MANUAL_CONTENT_INVALID");
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength !== asset.bytes || await sha256(bytes) !== asset.sha256) throw new Error("MANUAL_CONTENT_INVALID");
  return { document, asset, bytes };
}

export async function getManualManifest({ fetchImpl = fetch, signal } = {}) {
  const response = await fetchImpl(MANIFEST_PATH, { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok || !exactContentType(response, "application/json")) throw new Error("MANUAL_MANIFEST_UNAVAILABLE");
  try { return projectManifest(await response.json()); }
  catch (error) { if (error?.message === "MANUAL_MANIFEST_INVALID") throw error; throw new Error("MANUAL_MANIFEST_INVALID"); }
}

export async function readManualDocument(documentId, { fetchImpl = fetch, manifest, signal } = {}) {
  const { document, bytes } = await fetchAsset(documentId, "markdown", { fetchImpl, manifest, signal });
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  if (!text.startsWith(`# ${document.title}\n`) || text.includes("\u0000")) throw new Error("MANUAL_CONTENT_INVALID");
  return Object.freeze({ document_id: document.document_id, title: document.title, text });
}

export async function downloadManualAsset(documentId, format, { fetchImpl = fetch, manifest, signal } = {}) {
  if (format !== "docx" && format !== "pdf") throw new Error("MANUAL_FORMAT_INVALID");
  const { document, asset, bytes } = await fetchAsset(documentId, format, { fetchImpl, manifest, signal });
  return Object.freeze({
    document_id: document.document_id,
    filename: asset.filename,
    mime: asset.mime,
    blob: new Blob([bytes], { type: asset.mime }),
  });
}
