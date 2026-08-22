import { appendFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const cookieName = "daon_phase_e_session";
let validSession = false;
let sessionDelayMs = 0;
const notebook = { notebook_id: "notebook-existing", title: "검증된 지식 Notebook", source_count: 1, output_count: 1, updated_at: "2026-08-20T04:00:00Z", status: "active" };
const meta = { trace_id: "trace-phase-e", workspace_id: "workspace-phase-e" };
const json = (response, status, payload, headers = {}) => {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json");
  for (const [name, value] of Object.entries(headers)) response.setHeader(name, value);
  response.end(JSON.stringify(payload));
};
const authenticated = (request) => validSession && String(request.headers.cookie || "").split(/;\s*/u).includes(`${cookieName}=valid`);
const log = (request) => {
  if (process.env.DAON_PHASE_E_NETWORK_LOG) appendFileSync(process.env.DAON_PHASE_E_NETWORK_LOG, `${JSON.stringify({ method: request.method, path: request.url, host: request.headers.host })}\n`);
};

export default {
  root,
  plugins: [{ name: "phase-e-product-same-origin", configureServer(server) {
    server.middlewares.use((request, response, next) => {
      if (!request.url?.startsWith("/bff/") && !request.url?.startsWith("/__phase_e/")) return next();
      log(request);
      if (request.url === "/__phase_e/expire" && request.method === "POST") {
        validSession = false;
        response.setHeader("Set-Cookie", `${cookieName}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`);
        return json(response, 200, { status: "expired" });
      }
      if (request.url?.startsWith("/__phase_e/session-delay?") && request.method === "POST") {
        const value = new URL(request.url, "http://127.0.0.1").searchParams.get("ms");
        sessionDelayMs = Math.max(0, Math.min(2_000, Number.parseInt(value || "0", 10) || 0));
        return json(response, 200, { status: "configured", delay_ms: sessionDelayMs });
      }
      if (request.url === "/bff/api/auth/login" && request.method === "POST") {
        let raw = "";
        request.on("data", (chunk) => { raw += chunk; });
        request.on("end", () => {
          let body = {};
          try { body = JSON.parse(raw); } catch { /* fail below */ }
          if (body.login_id !== "phase-e-user" || body.password !== "Phase-E-Test-Only-42") return json(response, 401, { error: { code: "AUTHENTICATION_REQUIRED" } });
          validSession = true;
          response.setHeader("Set-Cookie", `${cookieName}=valid; Path=/; HttpOnly; SameSite=Lax`);
          return json(response, 200, { data: { user_id: "user-phase-e", tenant_id: "tenant-phase-e", workspace_id: "workspace-phase-e" }, meta: { trace_id: "trace-phase-e" } });
        });
        return undefined;
      }
      if (!authenticated(request)) return json(response, 401, { error: { code: "AUTHENTICATION_REQUIRED" }, meta: { trace_id: "trace-phase-e" } });
      if (request.url === "/bff/api/session/logout" && request.method === "POST") {
        const origin = request.headers.origin;
        const referer = request.headers.referer;
        if (origin !== "http://127.0.0.1:4220" || !String(referer || "").startsWith(`${origin}/`)) {
          return json(response, 403, { error: { code: "CSRF_VALIDATION_FAILED" }, meta: { trace_id: "trace-phase-e" } });
        }
        validSession = false;
        response.setHeader("Set-Cookie", `${cookieName}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`);
        return json(response, 200, { data: { status: "logged_out", replayed: false }, meta: { trace_id: "trace-phase-e" } });
      }
      if (request.url === "/bff/api/session" && request.method === "GET") {
        const send = () => json(response, 200, { data: {
        user_id: "user-phase-e", tenant_id: "tenant-phase-e", workspace_id: "workspace-phase-e",
        session_id: "session-phase-e", device_id: "device-phase-e", client_kind: "web",
        delivery: "same_origin_secure_cookie", expires_at: "2026-08-21T04:00:00Z", recovery_operations: [],
        }, meta: { trace_id: "trace-phase-e" } });
        return sessionDelayMs > 0 ? setTimeout(send, sessionDelayMs) : send();
      }
      if (request.url === "/bff/api/workspaces/workspace-phase-e/notebooks" && request.method === "GET") return json(response, 200, { data: [notebook], meta });
      if (request.url === "/bff/api/workspaces/workspace-phase-e/notebooks/notebook-existing" && request.method === "GET") return json(response, 200, { data: notebook, meta }, { ETag: '"notebook:1"' });
      if (request.url === "/bff/api/workspaces/workspace-phase-e/notebooks/notebook-existing/context" && request.method === "GET") return json(response, 200, { data: {
        notebook_id: "notebook-existing", sources: [{ source_id: "source-phase-e", source_version_id: "source-version-phase-e" }],
        knowledge_context_ids: [], conversation_thread_ids: ["conversation-phase-e"],
        studio_output_ids: ["studio-output-phase-e"], output_version_ids: ["output-version-phase-e"],
        generation_settings_ids: ["settings-phase-e"], conversation: { conversation_thread_id: "conversation-phase-e", answer: {
          run_id: "run-phase-e", run_result_id: "result-phase-e", answer: "선택한 Notebook의 보존된 대화입니다.", insufficient: false, citations: [],
        } },
      }, meta });
      if (request.url === "/bff/api/workspaces/workspace-phase-e/sources?notebook_id=notebook-existing" && request.method === "GET") return json(response, 200, { data: { sources: [{
        source_id: "source-phase-e", source_version_id: "source-version-phase-e", filename: "검증된-지식.pdf",
        source_state: "ready", processing_state: "completed", job_state: "completed",
      }] }, meta });
      if (request.url === "/bff/api/studio-outputs?workspace_id=workspace-phase-e&notebook_id=notebook-existing" && request.method === "GET") return json(response, 200, { data: { outputs: [{
        studio_output_id: "studio-output-phase-e", output_version_id: "output-version-phase-e", output_type: "evidence_report",
        title: "보존된 근거 보고서", purpose: "검증", status: "draft", content: "보고서 내용",
        run_id: "run-phase-e", run_result_id: "result-phase-e", citations: [{
          citation_id: "citation-phase-e", source_id: "source-phase-e", source_version_id: "source-version-phase-e",
          evidence_span_id: "span-phase-e", page: 1,
        }],
      }], studio_locks: [] }, meta });
      return json(response, 404, { error: { code: "NOT_FOUND" }, meta: { trace_id: "trace-phase-e" } });
    });
  } }],
  server: { host: "127.0.0.1", port: 4220, strictPort: true },
  build: { emptyOutDir: true, outDir: path.resolve(root, ".dist") },
};
