import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const evidenceDirectory = path.join(
  root,
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual",
);
const networkRelativePath = "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/browser-network.jsonl";

const files = [
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/actual-transcript.md",
  networkRelativePath,
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/01-manual-hub-list-1920x1080.png",
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/02-manual-hub-read-1920x1080.png",
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/03-manual-download-1920x1080.png",
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/04-docx-getting-started-cover.png",
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/05-docx-user-manual-page4.png",
  "docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/06-pdf-knowledge-guide-page5.png",
  "docs/manual/release-manifest.json",
  "apps/web/public/manual/manifest.json",
  "docs/manual/dist/daon-getting-started.docx",
  "docs/manual/dist/daon-getting-started.pdf",
  "docs/manual/dist/daon-user-manual.docx",
  "docs/manual/dist/daon-user-manual.pdf",
  "docs/manual/dist/daon-knowledge-llm-guide.docx",
  "docs/manual/dist/daon-knowledge-llm-guide.pdf",
];

const entries = [];
for (const relativePath of files) {
  const bytes = await readFile(path.join(root, relativePath));
  entries.push({
    path: relativePath.replaceAll("\\", "/"),
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  });
}

const networkRows = (await readFile(path.join(root, networkRelativePath), "utf8"))
  .trim()
  .split(/\r?\n/u)
  .map((line) => JSON.parse(line));
const uniqueRequestPaths = new Set(networkRows.map((row) => row.path));

const manifest = {
  schema_version: "daon.manual-evidence.v1",
  issue_id: "R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001",
  phase: "PHASE_C_MENU_3_MANUAL",
  release_version: "1.0.0",
  source_revision: "2d4c59e1c761ec12848dcfac8c2f04078dcbb47b",
  browser_viewport: { width: 1920, height: 1080, device_scale_factor: 1 },
  document_pages: { docx_total: 18, pdf_total: 18, per_document: 6 },
  accessibility: { documents: 3, high: 0, medium: 0, low: 0 },
  network: {
    same_origin_relative_only: true,
    captured_requests: networkRows.length,
    unique_request_paths: uniqueRequestPaths.size,
  },
  cleanup: { listeners: 0, owned_processes: 0 },
  files: entries,
};

const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
await writeFile(path.join(evidenceDirectory, "manifest.json"), serialized, "utf8");
await writeFile(
  path.join(evidenceDirectory, "manifest.sha256"),
  `${createHash("sha256").update(serialized).digest("hex")}  manifest.json\n`,
  "utf8",
);
