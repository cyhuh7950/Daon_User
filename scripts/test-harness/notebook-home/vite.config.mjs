import { appendFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.dirname(fileURLToPath(import.meta.url));
const initial = [
  { notebook_id: "notebook-strategy", title: "2026 디지털 헬스케어 전략", source_count: 97, output_count: 8, updated_at: "2026-08-16T08:24:00Z", status: "active" },
  { notebook_id: "notebook-compliance", title: "AI 의료기기 규제·준수 검토", source_count: 34, output_count: 5, updated_at: "2026-08-15T03:10:00Z", status: "attention" },
  { notebook_id: "notebook-knowledge", title: "Daon 지식 운영 기준", source_count: 18, output_count: 3, updated_at: "2026-08-13T11:45:00Z", status: "active" },
  { notebook_id: "notebook-empty", title: "새 사업 아이디어", source_count: 0, output_count: 0, updated_at: "2026-08-12T05:00:00Z", status: "empty" },
];
export default { root, plugins: [{ name: "notebook-evidence-same-origin", configureServer(server) { server.middlewares.use("/bff/api/workspaces/workspace-notebook-evidence/notebooks", (request, response) => {
  if (process.env.DAON_NOTEBOOK_NETWORK_LOG) appendFileSync(process.env.DAON_NOTEBOOK_NETWORK_LOG, `${JSON.stringify({ method: request.method, path: request.originalUrl, host: request.headers.host })}\n`);
  response.setHeader("Content-Type", "application/json"); response.setHeader("ETag", '"notebook:1"');
  if (request.method === "GET") { response.end(JSON.stringify({ data: initial, meta: { trace_id: "trace-evidence", workspace_id: "workspace-notebook-evidence" } })); return; }
  if (request.method === "POST") { let raw = ""; request.on("data", (chunk) => { raw += chunk; }); request.on("end", () => { const body = JSON.parse(raw); response.end(JSON.stringify({ data: { notebook_id: "notebook-created", title: body.title, source_count: 0, output_count: 0, updated_at: "2026-08-16T09:00:00Z", status: "empty" }, meta: { trace_id: "trace-evidence", workspace_id: "workspace-notebook-evidence" } })); }); return; }
  response.statusCode = 405; response.end(JSON.stringify({ error: { code: "METHOD_NOT_ALLOWED" } }));
}); } }], server: { host: "127.0.0.1", port: 4210, strictPort: true }, build: { emptyOutDir: true, outDir: path.resolve(root, ".dist") } };
