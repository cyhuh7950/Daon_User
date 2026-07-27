import React, { useEffect, useMemo, useState } from "react";
import { Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from "react-native";

import { acceptNativeDeepLink, createNavigationState, goBack, projectNativeRoutes, selectNativeRoute, type NavigationState, type NativeClientType } from "./domain/navigation.ts";
import { createUnavailablePublicApiClient, normalizePublicApiResult, type PublicApiClient, type PublicApiResult } from "./domain/public-api-client.ts";
import { projectScreen, type ScreenState } from "./domain/screens.ts";
import { mobileTokens } from "./platform/design-token-adapter.ts";

export interface InfoActionAdapter {
  show(input: { clientType: NativeClientType; routeId: string; accessibilityLabel: string }): Promise<{ ok: true } | { ok: false; code: string; state: "unavailable" }>;
}

export interface HostNavigationAdapter {
  onRouteChanged(input: { clientType: NativeClientType; nativeRouteKey: string }): void;
}

export interface NativePermissionAdapter {
  requestPermission(kind: "camera" | "microphone" | "notification"): Promise<string>;
  checkPermission(kind: "camera" | "microphone" | "notification"): Promise<string>;
  openApplicationSettings(): Promise<void>;
}

type MobileShellProps = {
  clientType: unknown;
  publicApiClient?: PublicApiClient;
  infoActionAdapter?: InfoActionAdapter;
  hostNavigationAdapter?: HostNavigationAdapter;
  permissionAdapter?: NativePermissionAdapter;
  androidPermissionAdapter?: NativePermissionAdapter;
  initialNativeRouteKey?: string | null;
  requestedNativeRouteKey?: string | null;
};

const unavailableInfoAction: InfoActionAdapter = {
  async show() { return { ok: false, code: "INFO_ACTION_HOST_UNAVAILABLE", state: "unavailable" }; }
};

const statusSignal: Record<ScreenState, { icon: string; label: string; color: string }> = {
  loading: { icon: "…", label: "불러오는 중", color: mobileTokens.color.action },
  empty: { icon: "○", label: "표시할 항목 없음", color: mobileTokens.color.secondaryText },
  ready: { icon: "✓", label: "사용 가능", color: mobileTokens.color.success },
  warning: { icon: "!", label: "주의 필요", color: mobileTokens.color.warning },
  error: { icon: "×", label: "오류", color: mobileTokens.color.danger },
  forbidden: { icon: "⊘", label: "접근 금지", color: mobileTokens.color.danger },
  unavailable: { icon: "—", label: "연결 전", color: mobileTokens.color.warning }
};

function UnsupportedNativeClient() {
  return (
    <SafeAreaView style={styles.safeArea} accessibilityLabel="지원하지 않는 Native Client">
      <View style={styles.centeredState}>
        <Text allowFontScaling style={styles.title}>Daon Mobile</Text>
        <Text allowFontScaling style={styles.errorText}>× UNKNOWN_NATIVE_CLIENT · android 또는 ios를 Host에서 명시해야 합니다.</Text>
      </View>
    </SafeAreaView>
  );
}

export function MobileShell({ clientType, publicApiClient, infoActionAdapter, hostNavigationAdapter, permissionAdapter, androidPermissionAdapter, initialNativeRouteKey, requestedNativeRouteKey }: MobileShellProps) {
  const projection = projectNativeRoutes(clientType);
  if (!projection.ok) return <UnsupportedNativeClient />;
  return <ValidatedMobileShell clientType={clientType as NativeClientType} publicApiClient={publicApiClient} infoActionAdapter={infoActionAdapter} hostNavigationAdapter={hostNavigationAdapter} permissionAdapter={permissionAdapter ?? androidPermissionAdapter} initialNativeRouteKey={initialNativeRouteKey} requestedNativeRouteKey={requestedNativeRouteKey} />;
}

function ValidatedMobileShell({ clientType, publicApiClient = createUnavailablePublicApiClient(), infoActionAdapter = unavailableInfoAction, hostNavigationAdapter, permissionAdapter, initialNativeRouteKey, requestedNativeRouteKey }: MobileShellProps & { clientType: NativeClientType }) {
  const [navigation, setNavigation] = useState<NavigationState>(() => {
    const initial = createNavigationState(clientType);
    return initialNativeRouteKey ? selectNativeRoute(initial, initialNativeRouteKey) : initial;
  });
  const [screenResult, setScreenResult] = useState<PublicApiResult>({ ok: false, error: { code: "NATIVE_PUBLIC_API_UNAVAILABLE", screenState: "unavailable", replacementOwner: "R1-M4-01" } });
  const [infoState, setInfoState] = useState<string | null>(null);
  const [permissionState, setPermissionState] = useState<string | null>(null);
  const currentRoute = navigation.routes.find((route) => route.nativeRouteKey === navigation.currentNativeRouteKey) ?? navigation.routes[0];
  const screen = useMemo(() => projectScreen(clientType, currentRoute.nativeRouteKey), [clientType, currentRoute.nativeRouteKey]);

  useEffect(() => {
    let active = true;
    setScreenResult({ ok: true, data: { state: "loading", title: currentRoute.nativeRouteKey } });
    publicApiClient.loadScreen({ clientType, routeId: currentRoute.routeId })
      .then((result) => { if (active) setScreenResult(normalizePublicApiResult(result)); })
      .catch(() => { if (active) setScreenResult({ ok: false, error: { code: "NATIVE_ADAPTER_RESULT_INVALID", screenState: "error", replacementOwner: "R1-M4-01" } }); });
    return () => { active = false; };
  }, [clientType, currentRoute.nativeRouteKey, currentRoute.routeId, publicApiClient]);

  useEffect(() => {
    if (!initialNativeRouteKey) return;
    setNavigation((current) => selectNativeRoute(current, initialNativeRouteKey));
  }, [initialNativeRouteKey]);

  useEffect(() => {
    if (!requestedNativeRouteKey) return;
    setNavigation((current) => {
      const next = acceptNativeDeepLink(current, requestedNativeRouteKey);
      if (!next.lastError) hostNavigationAdapter?.onRouteChanged({ clientType, nativeRouteKey: requestedNativeRouteKey });
      return next;
    });
  }, [clientType, hostNavigationAdapter, requestedNativeRouteKey]);

  const screenState = screenResult.ok ? screenResult.data.state : screenResult.error.screenState;
  const signal = statusSignal[screenState];
  const selectRoute = (nativeRouteKey: string) => {
    setNavigation((current) => {
      const next = selectNativeRoute(current, nativeRouteKey);
      if (!next.lastError) hostNavigationAdapter?.onRouteChanged({ clientType, nativeRouteKey });
      return next;
    });
    setInfoState(null);
  };

  const openInfo = async () => {
    if (!screen.ok) return;
    const result = await infoActionAdapter.show({ clientType, routeId: screen.screen.routeId, accessibilityLabel: `${screen.screen.nativeRouteKey} 설명` });
    setInfoState(result.ok ? "INFO_ACTION_OPENED" : `${result.code} · ${result.state}`);
  };

  return (
    <SafeAreaView style={styles.safeArea} accessibilityLabel={`Daon ${clientType} 공용 Shell`}>
      <View style={styles.header}>
        <Text allowFontScaling style={styles.title}>Daon Mobile</Text>
        <Text allowFontScaling style={styles.auxiliary}>{clientType} · React Native 공용 Shell</Text>
      </View>
      <ScrollView horizontal accessibilityLabel="공용 Navigation" contentContainerStyle={styles.navigation}>
        {navigation.routes.map((route) => {
          const selected = route.nativeRouteKey === navigation.currentNativeRouteKey;
          return (
            <Pressable
              key={route.routeId}
              accessibilityLabel={`${route.nativeRouteKey} 화면 열기`}
              accessibilityRole="button"
              accessibilityState={{ selected }}
              onPress={() => selectRoute(route.nativeRouteKey)}
              style={({ pressed }) => [styles.navigationButton, selected && styles.navigationSelected, pressed && styles.pressed]}
            >
              <Text allowFontScaling style={[styles.navigationText, selected && styles.navigationSelectedText]}>{route.nativeRouteKey}</Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <ScrollView accessibilityLabel="화면 내용" contentContainerStyle={styles.content}>
        <View style={styles.screenHeader}>
          <View style={styles.screenHeadingText}>
            <Text allowFontScaling accessibilityRole="header" style={styles.screenTitle}>{currentRoute.nativeRouteKey}</Text>
            <Text allowFontScaling style={styles.description}>{screen.ok ? screen.screen.purpose : "허용된 Screen Contract를 찾을 수 없습니다."}</Text>
          </View>
          <Pressable accessibilityLabel={`${currentRoute.nativeRouteKey} 설명 열기`} accessibilityRole="button" onPress={openInfo} style={styles.infoButton}>
            <Text allowFontScaling style={styles.infoButtonText}>i</Text>
          </Pressable>
        </View>
        <View accessibilityLabel={`${signal.label} 상태`} style={[styles.statusCard, { borderLeftColor: signal.color }]}>
          <Text allowFontScaling style={[styles.statusText, { color: signal.color }]}>{signal.icon} {signal.label}</Text>
          <Text allowFontScaling style={styles.bodyText}>{screenResult.ok ? (screenResult.data.message ?? "Adapter 응답 상태") : `${screenResult.error.code} · 교체 Owner ${screenResult.error.replacementOwner}`}</Text>
        </View>
        {navigation.lastError ? <Text allowFontScaling accessibilityRole="alert" style={styles.errorText}>× {navigation.lastError.code}</Text> : null}
        {infoState ? <Text allowFontScaling accessibilityRole="alert" style={styles.warningText}>! {infoState}</Text> : null}
        {permissionAdapter ? <View accessibilityLabel={`${clientType} 권한 제어`} style={styles.permissionControls}>
          {(["camera", "microphone", "notification"] as const).map((kind) => <Pressable key={kind} accessibilityLabel={`${kind} 권한 요청`} accessibilityRole="button" onPress={() => { void permissionAdapter.requestPermission(kind).then((state) => setPermissionState(`${kind}:${state}`)); }} style={styles.backButton}><Text allowFontScaling style={styles.bodyText}>{kind} 권한 요청</Text></Pressable>)}
          <Pressable accessibilityLabel="앱 권한 설정 열기" accessibilityRole="button" onPress={() => { void permissionAdapter.openApplicationSettings(); }} style={styles.backButton}><Text allowFontScaling style={styles.bodyText}>앱 권한 설정</Text></Pressable>
          {permissionState ? <Text allowFontScaling accessibilityLabel={`${permissionState.replace(":", " 권한 결과 ")}`} accessibilityRole="text" accessibilityLiveRegion="polite" style={styles.warningText}>{permissionState}</Text> : null}
        </View> : null}
        <Pressable accessibilityLabel="이전 화면으로 돌아가기" accessibilityRole="button" disabled={navigation.history.length <= 1} onPress={() => setNavigation((current) => goBack(current))} style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}>
          <Text allowFontScaling style={styles.bodyText}>← 이전 화면</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: mobileTokens.color.canvas },
  header: { paddingHorizontal: mobileTokens.spacing[4], paddingVertical: mobileTokens.spacing[3], backgroundColor: mobileTokens.color.surface, borderBottomWidth: 1, borderBottomColor: mobileTokens.color.border },
  title: { color: mobileTokens.color.primaryText, fontSize: mobileTokens.typography.screenTitle, fontWeight: "700" },
  auxiliary: { color: mobileTokens.color.secondaryText, fontSize: mobileTokens.typography.auxiliary },
  navigation: { gap: mobileTokens.spacing[2], padding: mobileTokens.spacing[3] },
  navigationButton: { minHeight: mobileTokens.targetSize.touchControl, justifyContent: "center", paddingHorizontal: mobileTokens.spacing[3], borderWidth: 1, borderColor: mobileTokens.color.border, borderRadius: mobileTokens.radius[1], backgroundColor: mobileTokens.color.surface },
  navigationSelected: { borderWidth: 2, borderColor: mobileTokens.color.focus, backgroundColor: mobileTokens.color.action },
  navigationText: { color: mobileTokens.color.primaryText, fontSize: mobileTokens.typography.body },
  navigationSelectedText: { color: mobileTokens.color.surface, fontWeight: "700" },
  pressed: { opacity: 0.72 },
  content: { gap: mobileTokens.spacing[3], padding: mobileTokens.spacing[4] },
  screenHeader: { flexDirection: "row", alignItems: "center", gap: mobileTokens.spacing[2] },
  screenHeadingText: { flex: 1, gap: mobileTokens.spacing[1] },
  screenTitle: { color: mobileTokens.color.primaryText, fontSize: mobileTokens.typography.screenTitle, fontWeight: "700" },
  description: { color: mobileTokens.color.secondaryText, fontSize: mobileTokens.typography.description },
  infoButton: { minWidth: mobileTokens.targetSize.touchControl, minHeight: mobileTokens.targetSize.touchControl, alignItems: "center", justifyContent: "center", borderWidth: 1, borderColor: mobileTokens.color.focus, borderRadius: mobileTokens.radius[3], backgroundColor: mobileTokens.color.surface },
  infoButtonText: { color: mobileTokens.color.focus, fontSize: mobileTokens.typography.sidebarTitle, fontWeight: "700" },
  statusCard: { gap: mobileTokens.spacing[2], padding: mobileTokens.spacing[4], borderLeftWidth: 4, borderRadius: mobileTokens.radius[1], backgroundColor: mobileTokens.color.surface },
  statusText: { fontSize: mobileTokens.typography.body, fontWeight: "700" },
  bodyText: { color: mobileTokens.color.primaryText, fontSize: mobileTokens.typography.body },
  errorText: { color: mobileTokens.color.danger, fontSize: mobileTokens.typography.body, fontWeight: "700" },
  warningText: { color: mobileTokens.color.warning, fontSize: mobileTokens.typography.body, fontWeight: "700" },
  backButton: { minHeight: mobileTokens.targetSize.touchControl, justifyContent: "center", paddingHorizontal: mobileTokens.spacing[3], borderWidth: 1, borderColor: mobileTokens.color.border, borderRadius: mobileTokens.radius[1], backgroundColor: mobileTokens.color.surface },
  centeredState: { flex: 1, justifyContent: "center", gap: mobileTokens.spacing[3], padding: mobileTokens.spacing[4] },
  permissionControls: { gap: mobileTokens.spacing[2] }
});
