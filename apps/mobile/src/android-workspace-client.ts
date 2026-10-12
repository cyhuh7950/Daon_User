import type { AndroidAuthState } from "./android-auth-adapter.ts";
import type { PublicApiClient, PublicApiResult } from "./domain/public-api-client.ts";

export function canEnterAndroidShell(state: AndroidAuthState | null): boolean {
  return state?.status === "authenticated";
}

export function workspaceSummary(state: AndroidAuthState | null): string | null {
  if (!canEnterAndroidShell(state)) return null;
  const selected = state?.workspaces?.find((item) => item.tenant_id === state.tenant_id &&
    item.workspace_id === state.workspace_id);
  return selected?.display_name ?? "Workspace 없음";
}

const unavailable = (): PublicApiResult => ({
  ok: false,
  error: { code: "NATIVE_PUBLIC_API_UNAVAILABLE", screenState: "unavailable", replacementOwner: "R1-M4-01" },
});

export function createAndroidWorkspacePublicApiClient(
  getSessionState: () => Promise<AndroidAuthState>,
): PublicApiClient {
  return {
    async loadScreen(request): Promise<PublicApiResult> {
      if (request.clientType !== "android" || request.routeId !== "workspace_list") return unavailable();
      try {
        const state = await getSessionState();
        if (state.status === "signed_out") return {
          ok: false,
          error: { code: "NATIVE_SESSION_INVALID", screenState: "forbidden", replacementOwner: "R1-M4-01" },
        };
        if (state.status !== "authenticated" || !Array.isArray(state.workspaces)) return unavailable();
        const names = state.workspaces.map((item) => item.display_name);
        return { ok: true, data: {
          state: names.length ? "ready" : "empty", title: "WorkspaceList",
          message: names.length ? names.join(" · ") : "표시할 Workspace가 없습니다.",
        } };
      } catch { return unavailable(); }
    },
  };
}
