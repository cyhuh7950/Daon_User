import React, { useEffect, useMemo, useState } from "react";
import { Platform } from "react-native";
import { MobileShell } from "./MobileShell.tsx";
import { androidPermissionAdapter, restoreNavigationRoute as restoreAndroidNavigationRoute, saveNavigationRoute as saveAndroidNavigationRoute, subscribeAndroidDeepLinks, subscribeAndroidLifecycle } from "./platform/android-host.ts";
import { iosPermissionAdapter, restoreIOSNavigationRoute, saveIOSNavigationRoute, subscribeIOSDeepLinks, subscribeIOSLifecycle } from "./platform/ios-host.ts";

export type MobileHostProps = { clientType?: unknown };

export default function App({ clientType }: MobileHostProps) {
  const resolvedClientType = clientType ?? Platform.OS;
  const [initialNativeRouteKey, setInitialNativeRouteKey] = useState<string | null>(null);
  const [requestedNativeRouteKey, setRequestedNativeRouteKey] = useState<string | null>(null);
  const hostNavigationAdapter = useMemo(() => Platform.OS === "android" ? {
    onRouteChanged: ({ nativeRouteKey }: { nativeRouteKey: string }) => { void saveAndroidNavigationRoute(nativeRouteKey); }
  } : Platform.OS === "ios" ? {
    onRouteChanged: ({ nativeRouteKey }: { nativeRouteKey: string }) => { void saveIOSNavigationRoute(nativeRouteKey); }
  } : undefined, []);

  useEffect(() => {
    if (Platform.OS === "android") {
      void restoreAndroidNavigationRoute().then(setInitialNativeRouteKey);
      const removeDeepLinks = subscribeAndroidDeepLinks(setRequestedNativeRouteKey);
      const removeLifecycle = subscribeAndroidLifecycle(() => undefined);
      return () => { removeDeepLinks(); removeLifecycle(); };
    }
    if (Platform.OS === "ios") {
      void restoreIOSNavigationRoute().then(setInitialNativeRouteKey);
      const removeDeepLinks = subscribeIOSDeepLinks(setRequestedNativeRouteKey);
      const removeLifecycle = subscribeIOSLifecycle(() => undefined);
      return () => { removeDeepLinks(); removeLifecycle(); };
    }
  }, []);

  const permissionAdapter = Platform.OS === "android" ? androidPermissionAdapter : Platform.OS === "ios" ? iosPermissionAdapter : undefined;
  return <MobileShell clientType={resolvedClientType} initialNativeRouteKey={initialNativeRouteKey} requestedNativeRouteKey={requestedNativeRouteKey} hostNavigationAdapter={hostNavigationAdapter} permissionAdapter={permissionAdapter} />;
}
