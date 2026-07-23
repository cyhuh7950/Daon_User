import { createWebShellRuntimeDescriptor, runtimeMethodNotAllowed } from "../../../../lib/web-shell-runtime.js";

export const dynamic = "force-dynamic";

const responseHeaders = { "Cache-Control": "no-store", "Content-Type": "application/json" };

export async function GET() {
  return Response.json(createWebShellRuntimeDescriptor(), { status: 200, headers: responseHeaders });
}

function rejectMethod() {
  return Response.json(runtimeMethodNotAllowed(), {
    status: 405,
    headers: { ...responseHeaders, Allow: "GET, HEAD" }
  });
}

export { rejectMethod as DELETE, rejectMethod as PATCH, rejectMethod as POST, rejectMethod as PUT };
