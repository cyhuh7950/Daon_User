import { projectNativeRoutes } from "../domain/navigation.ts";

export const APPROVED_NATIVE_DEEP_LINK_PREFIX = "sinsan-daon://app/" as const;

const approvedNativeRouteKeys = new Set(
  ["android", "ios"].flatMap((clientType) => {
    const projection = projectNativeRoutes(clientType);
    return projection.ok ? projection.routes.map((route) => route.nativeRouteKey) : [];
  })
);

export function parseApprovedNativeDeepLink(value: string | null): string | null {
  if (typeof value !== "string" || !value.startsWith(APPROVED_NATIVE_DEEP_LINK_PREFIX)) return null;
  const route = value.slice(APPROVED_NATIVE_DEEP_LINK_PREFIX.length);
  return approvedNativeRouteKeys.has(route) ? route : null;
}
