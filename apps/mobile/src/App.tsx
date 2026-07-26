import React, { useEffect, useMemo, useState } from "react";
import { Platform } from "react-native";
import { MobileShell } from "./MobileShell.tsx";
import { androidPermissionAdapter, restoreNavigationRoute, saveNavigationRoute, subscribeAndroidDeepLinks, subscribeAndroidLifecycle } from "./platform/android-host.ts";

export type MobileHostProps = { clientType?: unknown };

export default function App({ clientType }: MobileHostProps) {
  const resolvedClientType = clientType ?? Platform.OS;
  const [initialNativeRouteKey, setInitialNativeRouteKey] = useState<string | null>(null);
  const [requestedNativeRouteKey, setRequestedNativeRouteKey] = useState<string | null>(null);
  const hostNavigationAdapter = useMemo(() => Platform.OS === "android" ? {
    onRouteChanged: ({ nativeRouteKey }: { nativeRouteKey: string }) => { void saveNavigationRoute(nativeRouteKey); }
  } : undefined, []);

  useEffect(() => {
    if (Platform.OS !== "android") return;
    void restoreNavigationRoute().then(setInitialNativeRouteKey);
    const removeDeepLinks = subscribeAndroidDeepLinks(setRequestedNativeRouteKey);
    const removeLifecycle = subscribeAndroidLifecycle(() => undefined);
    return () => { removeDeepLinks(); removeLifecycle(); };
  }, []);

  return <MobileShell clientType={resolvedClientType} initialNativeRouteKey={initialNativeRouteKey} requestedNativeRouteKey={requestedNativeRouteKey} hostNavigationAdapter={hostNavigationAdapter} androidPermissionAdapter={Platform.OS === "android" ? androidPermissionAdapter : undefined} />;
}
