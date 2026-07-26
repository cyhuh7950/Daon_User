import { AppState, Linking, NativeModules, type AppStateStatus } from "react-native";
import { parseApprovedAndroidDeepLink } from "./android-deep-link.ts";

export { APPROVED_ANDROID_DEEP_LINK_PREFIX, parseApprovedAndroidDeepLink } from "./android-deep-link.ts";

export type AndroidPermissionKind = "camera" | "microphone" | "notification";
export type AndroidPermissionState = "GRANTED" | "REQUESTED" | "NOT_REQUESTED" | "DENIED_CAN_ASK_AGAIN" | "PERMANENTLY_DENIED";

type DaonAndroidHostNativeModule = {
  saveNavigationRoute(route: string): Promise<boolean>;
  restoreNavigationRoute(): Promise<string | null>;
  getLifecycleState(): Promise<string>;
  consumePendingDeepLink(): Promise<string | null>;
  requestPermission(kind: AndroidPermissionKind): Promise<AndroidPermissionState>;
  checkPermission(kind: AndroidPermissionKind): Promise<AndroidPermissionState>;
  openApplicationSettings(): Promise<boolean>;
};

const nativeHost = NativeModules.DaonAndroidHost as DaonAndroidHostNativeModule | undefined;

export const ANDROID_DEEP_LINK_STATUS = "APPROVED_SINSAN_DAON_APP_ROUTE" as const;

export async function saveNavigationRoute(route: string): Promise<void> {
  await nativeHost?.saveNavigationRoute(route);
}

export async function restoreNavigationRoute(): Promise<string | null> {
  return nativeHost ? nativeHost.restoreNavigationRoute() : null;
}

export async function requestPermission(kind: AndroidPermissionKind): Promise<AndroidPermissionState> {
  if (!nativeHost) return "NOT_REQUESTED";
  return nativeHost.requestPermission(kind);
}

export async function checkPermission(kind: AndroidPermissionKind): Promise<AndroidPermissionState> {
  if (!nativeHost) return "NOT_REQUESTED";
  return nativeHost.checkPermission(kind);
}

export async function openApplicationSettings(): Promise<void> {
  await nativeHost?.openApplicationSettings();
}

export function subscribeAndroidDeepLinks(onRoute: (route: string) => void): () => void {
  const accept = (value: string | null) => {
    const route = parseApprovedAndroidDeepLink(value);
    if (route) onRoute(route);
  };
  void Linking.getInitialURL().then(accept);
  void nativeHost?.consumePendingDeepLink().then(accept);
  const subscription = Linking.addEventListener("url", ({ url }) => accept(url));
  return () => subscription.remove();
}

export function subscribeAndroidLifecycle(onState: (state: AppStateStatus) => void): () => void {
  const subscription = AppState.addEventListener("change", onState);
  return () => subscription.remove();
}

export const androidPermissionAdapter = { requestPermission, checkPermission, openApplicationSettings };
