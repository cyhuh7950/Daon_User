import path from "node:path";
import { fileURLToPath } from "node:url";
import { appendFileSync } from "node:fs";

const root = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.env.DAON_LICENSE_EVIDENCE_DIST;
if (!outDir) throw new Error("DAON_LICENSE_EVIDENCE_DIST is required");
const networkLog = process.env.DAON_LICENSE_NETWORK_LOG;

function licenseView(mode) {
  const admin = mode === "admin";
  const expired = mode === "expired";
  const limited = mode === "limit";
  return {
    product: "daon-user", edition: "enterprise", license_id_hint: "…1-001",
    issued_at: "2026-08-01T00:00:00Z",
    expires_at: expired ? "2026-08-14T00:00:00Z" : "2027-08-15T00:00:00Z",
    status: expired ? "expired" : limited ? "limit_reached" : "active",
    features: ["citation", "studio_generation", "knowledge_sync"],
    resources: [
      { resource: "notebooks", limit: 100, used: 34, remaining: 66, status: "available" },
      { resource: "generation_runs", limit: 1000, used: limited ? 1000 : 417, remaining: limited ? 0 : 583, status: limited ? "limit_reached" : "available" },
      { resource: "storage_bytes", limit: 107374182400, used: 32212254720, remaining: 75161927680, status: "available" },
    ],
    warning: expired
      ? { code: "LICENSE_EXPIRED", action: "새 생성은 중단되며 기존 자료 조회와 Export는 계속 사용할 수 있습니다." }
      : limited
        ? { code: "LICENSE_RESOURCE_LIMIT_REACHED", action: "생성 실행 한도에 도달했습니다. 조직 관리자에게 증설을 요청하세요." }
        : null,
    creation_allowed: !expired && !limited,
    existing_read_allowed: true,
    existing_export_allowed: true,
    can_apply: admin,
  };
}

export default {
  root,
  plugins: [{
    name: "license-evidence-same-origin-bff",
    configureServer(server) {
      server.middlewares.use("/bff/api/workspaces/workspace-license-evidence/license", (request, response) => {
        if (networkLog) appendFileSync(networkLog, `${JSON.stringify({
          method: request.method,
          path: request.originalUrl,
          host: request.headers.host,
        })}`, { encoding: "utf8" });
        const referer = new URL(request.headers.referer ?? "http://evidence.invalid/");
        const mode = referer.searchParams.get("state") ?? "readonly";
        response.statusCode = 200;
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({ data: licenseView(mode), meta: { trace_id: "trace-license-evidence", workspace_id: "workspace-license-evidence" } }));
      });
    },
  }],
  build: { emptyOutDir: true, outDir: path.resolve(outDir) },
};
