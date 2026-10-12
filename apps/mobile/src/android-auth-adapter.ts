import { NativeModules } from "react-native";

export type AndroidAuthWorkspace = { tenant_id: string; workspace_id: string; display_name: string; kind?: "personal" | "organization" };
export type AndroidAuthState = {
  status: "unavailable" | "signed_out" | "awaiting_browser" | "callback_ready" | "authenticated" | "workspace_unavailable";
  user_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  session_id?: string;
  device_id?: string;
  transaction_id?: string;
  workspaces?: AndroidAuthWorkspace[];
};

type AndroidAuthNative = {
  beginLogin(): Promise<AndroidAuthState>;
  consumeAuthorizationLink(url: string): Promise<AndroidAuthState>;
  getSessionState(): Promise<AndroidAuthState>;
  switchWorkspace(tenantId: string): Promise<AndroidAuthState>;
  logout(): Promise<AndroidAuthState>;
};

const module = NativeModules.DaonAndroidAuth as AndroidAuthNative | undefined;
const unavailable = (): Promise<AndroidAuthState> => Promise.resolve({ status: "unavailable" });

export const androidAuth = {
  beginLogin: () => module?.beginLogin() ?? unavailable(),
  // The callback URL stays in the native Activity; JS never receives its code or state.
  consumePendingAuthorization: () => module?.consumeAuthorizationLink("") ?? unavailable(),
  getSessionState: () => module?.getSessionState() ?? unavailable(),
  switchWorkspace: (tenantId: string) => module?.switchWorkspace(tenantId) ?? unavailable(),
  logout: () => module?.logout() ?? unavailable(),
};
