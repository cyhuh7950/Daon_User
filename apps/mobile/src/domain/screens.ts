import screensContract from "@daon-user/contracts/screens.json" with { type: "json" };
import { projectNativeRoutes } from "./navigation.ts";

export type ScreenState = "loading" | "empty" | "ready" | "warning" | "error" | "forbidden" | "unavailable";

type ContractScreen = {
  screen_id: string;
  route_id: string;
  purpose: string;
  clients: string[];
  states: string[];
  mock_boundary: { mode: string; adapter: string; replacement_owner: string };
  accessibility: { screen_reader_label_key: string; supports_os_text_scaling: boolean };
};

const contractScreens = screensContract.screens as ContractScreen[];
const knownStates = new Set(contractScreens.flatMap((screen) => screen.states));

export function getAllowedScreenStates(routeId: string): string[] {
  return [...(contractScreens.find((screen) => screen.route_id === routeId)?.states ?? [])];
}

export function normalizeScreenState(value: unknown): { state: ScreenState; code?: "UNKNOWN_SCREEN_STATE" } {
  if (typeof value === "string" && knownStates.has(value)) return { state: value as ScreenState };
  return { state: "error", code: "UNKNOWN_SCREEN_STATE" };
}

export function projectScreen(clientType: unknown, nativeRouteKey: string) {
  const routes = projectNativeRoutes(clientType);
  if (!routes.ok) return { ok: false as const, code: routes.code };
  const route = routes.routes.find((item) => item.nativeRouteKey === nativeRouteKey);
  if (!route) return { ok: false as const, code: "NATIVE_ROUTE_NOT_ALLOWED" as const };
  const screen = contractScreens.find((item) => item.route_id === route.routeId && item.clients.includes(clientType as string));
  if (!screen) return { ok: false as const, code: "NATIVE_SCREEN_NOT_AVAILABLE" as const };
  return {
    ok: true as const,
    screen: {
      screenId: screen.screen_id,
      routeId: screen.route_id,
      nativeRouteKey,
      purpose: screen.purpose,
      states: [...screen.states],
      adapterName: screen.mock_boundary.adapter,
      replacementOwner: screen.mock_boundary.replacement_owner,
      accessibilityLabelKey: screen.accessibility.screen_reader_label_key,
      supportsOsTextScaling: screen.accessibility.supports_os_text_scaling
    }
  };
}
