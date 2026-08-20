import { appendFileSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const repository = path.resolve(root, "../../..");
const publicManual = path.join(repository, "apps/web/public/manual");
const outDir = process.env.DAON_MANUAL_EVIDENCE_DIST;
if (!outDir) throw new Error("DAON_MANUAL_EVIDENCE_DIST is required");
const networkLog = process.env.DAON_MANUAL_NETWORK_LOG;
const allowlist = new Set([
  "manifest.json",
  "daon-getting-started.md", "daon-getting-started.docx", "daon-getting-started.pdf",
  "daon-user-manual.md", "daon-user-manual.docx", "daon-user-manual.pdf",
  "daon-knowledge-llm-guide.md", "daon-knowledge-llm-guide.docx", "daon-knowledge-llm-guide.pdf",
]);
const contentTypes = {
  ".json": "application/json",
  ".md": "text/markdown; charset=utf-8",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".pdf": "application/pdf",
};

export default {
  root,
  plugins: [{
    name: "manual-evidence-same-origin-assets",
    configureServer(server) {
      server.middlewares.use("/manual", (request, response) => {
        const filename = decodeURIComponent(new URL(request.originalUrl, "http://evidence.invalid").pathname.slice("/manual/".length));
        if (!allowlist.has(filename) || filename.includes("..") || filename.includes("/") || filename.includes("\\")) {
          response.statusCode = 404;
          response.end("NOT_FOUND");
          return;
        }
        if (networkLog) appendFileSync(networkLog, `${JSON.stringify({ method: request.method, path: request.originalUrl, host: request.headers.host })}\n`, { encoding: "utf8" });
        response.statusCode = 200;
        response.setHeader("Content-Type", contentTypes[path.extname(filename)]);
        response.end(readFileSync(path.join(publicManual, filename)));
      });
    },
  }],
  build: { emptyOutDir: true, outDir: path.resolve(outDir) },
};
