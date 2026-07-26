import { projectNativeRoutes } from "../domain/navigation.ts";

export const APPROVED_ANDROID_DEEP_LINK_PREFIX = "sinsan-daon://app/" as const;

const androidProjection = projectNativeRoutes("android");
const APPROVED_ANDROID_ROUTE_KEYS = new Set(
  androidProjection.ok ? androidProjection.routes.map((route) => route.nativeRouteKey) : []
);

export function parseApprovedAndroidDeepLink(value: string | null): string | null {
  if (typeof value !== "string" || !value.startsWith(APPROVED_ANDROID_DEEP_LINK_PREFIX)) return null;
  const route = value.slice(APPROVED_ANDROID_DEEP_LINK_PREFIX.length);
  return APPROVED_ANDROID_ROUTE_KEYS.has(route) ? route : null;
}
