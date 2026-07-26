import { normalizeScreenState, type ScreenState } from "./screens.ts";
import type { NativeClientType } from "./navigation.ts";

export type ScreenRequest = { clientType: NativeClientType; routeId: string };
export type ScreenData = { state: ScreenState; title: string; message?: string };
export type PublicApiError = { code: string; screenState: Exclude<ScreenState, "ready">; replacementOwner: string };
export type PublicApiResult = { ok: true; data: ScreenData } | { ok: false; error: PublicApiError };

export interface PublicApiClient {
  loadScreen(request: ScreenRequest): Promise<PublicApiResult>;
}

const unavailableError = (): PublicApiError => ({
  code: "NATIVE_PUBLIC_API_UNAVAILABLE",
  screenState: "unavailable",
  replacementOwner: "R1-M4-01"
});

export function createUnavailablePublicApiClient(): PublicApiClient {
  return { async loadScreen() { return { ok: false, error: unavailableError() }; } };
}

export function normalizePublicApiResult(value: unknown): PublicApiResult {
  if (!value || typeof value !== "object") return { ok: false, error: { code: "NATIVE_ADAPTER_RESULT_INVALID", screenState: "error", replacementOwner: "R1-M4-01" } };
  const candidate = value as { ok?: unknown; data?: { state?: unknown; title?: unknown }; error?: unknown };
  if (candidate.ok === true && candidate.data && typeof candidate.data.title === "string") {
    const normalizedState = normalizeScreenState(candidate.data.state);
    if (!normalizedState.code) return { ok: true, data: { ...candidate.data, state: normalizedState.state } as ScreenData };
  }
  if (candidate.ok === false && candidate.error && typeof candidate.error === "object") {
    const error = candidate.error as Partial<PublicApiError>;
    const state = normalizeScreenState(error.screenState);
    if (typeof error.code === "string" && typeof error.replacementOwner === "string" && state.state !== "ready") {
      return { ok: false, error: { code: error.code, screenState: state.state as Exclude<ScreenState, "ready">, replacementOwner: error.replacementOwner } };
    }
  }
  return { ok: false, error: { code: "NATIVE_ADAPTER_RESULT_INVALID", screenState: "error", replacementOwner: "R1-M4-01" } };
}
