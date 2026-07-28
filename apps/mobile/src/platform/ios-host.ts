import { AppState, Linking, NativeModules, type AppStateStatus } from "react-native";
import { parseApprovedNativeDeepLink } from "./native-deep-link.ts";

export type IOSPermissionKind = "camera" | "microphone" | "notification";
export type IOSPermissionState = "GRANTED" | "REQUESTED" | "NOT_REQUESTED" | "DENIED" | "RESTRICTED";

type DaonIOSHostNativeModule = {
  saveNavigationRoute(route: string): Promise<boolean>;
  restoreNavigationRoute(): Promise<string | null | undefined>;
  getLifecycleState(): Promise<string>;
  consumePendingDeepLink(): Promise<string | null>;
  requestPermission(kind: IOSPermissionKind): Promise<IOSPermissionState>;
  checkPermission(kind: IOSPermissionKind): Promise<IOSPermissionState>;
  openApplicationSettings(): Promise<boolean>;
  openNotificationSettings(): Promise<boolean>;
};

const nativeHost = NativeModules.DaonIOSHost as DaonIOSHostNativeModule | undefined;

export async function saveIOSNavigationRoute(route: string): Promise<void> {
  await nativeHost?.saveNavigationRoute(route);
}

export async function restoreIOSNavigationRoute(): Promise<string | null> {
  if (!nativeHost) return null;
  const restoredRoute: unknown = await nativeHost.restoreNavigationRoute();
  return typeof restoredRoute === "string" ? restoredRoute : null;
}

export async function requestIOSPermission(kind: IOSPermissionKind): Promise<IOSPermissionState> {
  return nativeHost ? nativeHost.requestPermission(kind) : "NOT_REQUESTED";
}

export async function checkIOSPermission(kind: IOSPermissionKind): Promise<IOSPermissionState> {
  return nativeHost ? nativeHost.checkPermission(kind) : "NOT_REQUESTED";
}

export async function openIOSApplicationSettings(): Promise<void> {
  await nativeHost?.openApplicationSettings();
}

export async function openIOSNotificationSettings(): Promise<void> {
  await nativeHost?.openNotificationSettings();
}

export function subscribeIOSDeepLinks(onRoute: (route: string) => void): () => void {
  const accept = (value: string | null) => {
    const route = parseApprovedNativeDeepLink(value);
    if (route) onRoute(route);
  };
  void Linking.getInitialURL().then(accept);
  void nativeHost?.consumePendingDeepLink().then(accept);
  const subscription = Linking.addEventListener("url", ({ url }) => accept(url));
  return () => subscription.remove();
}

export function subscribeIOSLifecycle(onState: (state: AppStateStatus) => void): () => void {
  const subscription = AppState.addEventListener("change", onState);
  return () => subscription.remove();
}

export const iosPermissionAdapter = {
  requestPermission: requestIOSPermission,
  checkPermission: checkIOSPermission,
  openApplicationSettings: openIOSApplicationSettings,
  openNotificationSettings: openIOSNotificationSettings
};
