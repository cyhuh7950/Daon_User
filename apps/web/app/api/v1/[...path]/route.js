import {
  BffConfigurationError,
  createBffSafeError,
  createBffTraceId,
  createNativeBffProxy,
  parseInternalApiBase,
  parsePublicGatewayOrigin,
} from "../../../../lib/bff-api-proxy.js";

function configurationFailure(trace) {
  return createBffSafeError(
    503,
    "GATEWAY_CONFIGURATION_INVALID",
    trace,
    false,
    "서비스 연결 설정을 확인할 수 없습니다.",
  );
}

async function handler(request, context) {
  const trace = createBffTraceId(request);
  try {
    const baseUrl = parseInternalApiBase(
      process.env.DAON_API_INTERNAL_URL,
      process.env.DAON_RUNTIME_PROFILE ?? "production",
    );
    const publicOrigin = parsePublicGatewayOrigin(
      process.env.DAON_PUBLIC_GATEWAY_URL,
      process.env.DAON_BFF_PROFILE ?? "production",
    );
    const { path } = await context.params;
    return createNativeBffProxy({ baseUrl, publicOrigin })(request, path, trace);
  } catch (error) {
    const errorTrace = createBffTraceId();
    if (error instanceof BffConfigurationError) return configurationFailure(errorTrace);
    return createBffSafeError(500, "GATEWAY_UNEXPECTED_ERROR", errorTrace);
  }
}

export const dynamic = "force-dynamic";
export const GET = handler;
export const POST = handler;
