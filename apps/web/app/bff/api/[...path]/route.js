import {
  BffConfigurationError,
  createBffProxy,
  parseInternalApiBase,
} from "../../../../lib/bff-api-proxy.js";

function configurationFailure() {
  return Response.json({
    error: {
      code: "GATEWAY_CONFIGURATION_INVALID",
      message: "서비스 연결 설정을 확인할 수 없습니다.",
      stage: "gateway",
      impact: "request_not_completed",
      retryable: false,
      user_action: "관리자에게 문의하세요.",
      trace_id: "trace-bff-configuration",
      details: {},
    },
  }, { status: 503, headers: { "Cache-Control": "no-store" } });
}

async function handler(request, context) {
  try {
    const baseUrl = parseInternalApiBase(
      process.env.DAON_API_INTERNAL_URL,
      process.env.DAON_RUNTIME_PROFILE ?? "production",
    );
    const { path } = await context.params;
    return createBffProxy({ baseUrl })(request, path);
  } catch (error) {
    if (error instanceof BffConfigurationError) return configurationFailure();
    throw error;
  }
}

export const dynamic = "force-dynamic";
export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
