import React, { useEffect, useRef, useState } from "react";
import { AppState, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from "react-native";
import { androidAuth, type AndroidAuthState } from "./android-auth-adapter.ts";
import { canEnterAndroidShell, workspaceSummary } from "./android-workspace-client.ts";
import { createAndroidAuthRestorer } from "./android-auth-restorer.ts";

export function AndroidAuthScreen({ authenticatedContent }: { authenticatedContent: React.ReactNode }) {
  const [state, setState] = useState<AndroidAuthState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  const restorerRef = useRef<ReturnType<typeof createAndroidAuthRestorer> | null>(null);
  const switchingRef = useRef(false);

  useEffect(() => {
    const restorer = createAndroidAuthRestorer(androidAuth, setState, setError);
    restorerRef.current = restorer;
    void restorer.restore();
    const listener = AppState.addEventListener("change", (next) => {
      if (next === "active" && !switchingRef.current) { setLogoutFailed(false); void restorer.restore(); }
    });
    return () => { restorer.dispose(); restorerRef.current = null; listener.remove(); };
  }, []);

  const start = async () => {
    restorerRef.current?.invalidate();
    setBusy(true);
    setError(false);
    setLogoutFailed(false);
    try { setState(await androidAuth.beginLogin()); }
    catch { setError(true); }
    finally { setBusy(false); }
  };
  const leave = async () => {
    restorerRef.current?.invalidate();
    setBusy(true);
    setLogoutFailed(false);
    try { setState(await androidAuth.logout()); setError(false); }
    catch { setLogoutFailed(true); setError(true); }
    finally { setBusy(false); }
  };

  const selectWorkspace = async (tenantId: string) => {
    restorerRef.current?.invalidate();
    switchingRef.current = true;
    setState(null);
    setBusy(true);
    setError(false);
    setLogoutFailed(false);
    try { setState(await androidAuth.switchWorkspace(tenantId)); }
    catch { setState({ status: "workspace_unavailable" }); }
    finally { switchingRef.current = false; setBusy(false); }
  };

  if (canEnterAndroidShell(state)) return <SafeAreaView style={styles.page}>
    <View style={styles.authenticatedHeader}>
      <Text>현재 소속: {workspaceSummary(state)}</Text>
      <Pressable disabled={busy} accessibilityRole="button" onPress={() => void leave()} style={styles.button}>
        <Text style={styles.buttonText}>로그아웃</Text>
      </Pressable>
    </View>
    <Text>접근 가능한 소속별 기본 Workspace</Text>
    <ScrollView style={styles.workspaceListScroll} contentContainerStyle={styles.workspaceList}>
      {state?.workspaces?.map((workspace) => <Pressable key={workspace.tenant_id}
        disabled={busy || workspace.tenant_id === state?.tenant_id}
        accessibilityRole="button"
        accessibilityState={{ selected: workspace.tenant_id === state?.tenant_id,
          disabled: busy || workspace.tenant_id === state?.tenant_id }}
        onPress={() => void selectWorkspace(workspace.tenant_id)} style={styles.button}>
        <Text style={styles.buttonText}>{workspace.display_name}{workspace.tenant_id === state?.tenant_id ? " · 선택됨" : ""}</Text>
      </Pressable>)}
    </ScrollView>
    <View key={state?.session_id ?? "authenticated"} style={styles.shell}>{authenticatedContent}</View>
    {error && <Text accessibilityRole="alert">로그아웃을 완료할 수 없습니다.</Text>}
  </SafeAreaView>;

  return <SafeAreaView style={styles.page}><ScrollView contentContainerStyle={styles.content}>
    <Text style={styles.title}>Daon Workspace</Text>
    {!state && <Text>인증 상태를 확인하는 중입니다.</Text>}
    {state?.status === "unavailable" && <Text>Android 인증 설정을 사용할 수 없습니다.</Text>}
    {state?.status === "signed_out" && <>
      <Text>기존 계정으로 로그인하려면 안전한 외부 브라우저를 엽니다.</Text>
      <Pressable disabled={busy} accessibilityRole="button" onPress={() => void start()} style={styles.button}>
        <Text style={styles.buttonText}>브라우저에서 로그인</Text>
      </Pressable>
    </>}
    {state?.status === "awaiting_browser" && <Text>브라우저에서 로그인을 완료한 뒤 앱으로 돌아오세요.</Text>}
    {state?.status === "callback_ready" && <Text>인증 결과를 확인하는 중입니다.</Text>}
    {state?.status === "workspace_unavailable" && <>
      <Text>Workspace를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.</Text>
      <Pressable disabled={busy} accessibilityRole="button" onPress={() => {
        restorerRef.current?.invalidate();
        setBusy(true);
        setLogoutFailed(false);
        void androidAuth.getSessionState().then((result) => { setState(result); setError(false); })
          .catch(() => setError(true)).finally(() => setBusy(false));
      }} style={styles.button}><Text style={styles.buttonText}>Workspace 다시 확인</Text></Pressable>
      <Pressable disabled={busy} accessibilityRole="button" onPress={() => void leave()} style={styles.button}>
        <Text style={styles.buttonText}>로그아웃</Text>
      </Pressable>
    </>}
    {busy && <Text>처리 중입니다.</Text>}
    {error && <Text accessibilityRole="alert">{logoutFailed ? "로그아웃을 완료할 수 없습니다. 연결을 확인한 뒤 다시 시도해 주세요." : "인증을 완료할 수 없습니다. 새 거래로 다시 시도해 주세요."}</Text>}
  </ScrollView></SafeAreaView>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#fff" },
  content: { padding: 20, gap: 16 },
  title: { fontSize: 20, fontWeight: "700" },
  authenticatedHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 8 },
  shell: { flex: 1 },
  workspaceListScroll: { flexGrow: 0, maxHeight: 180 },
  workspaceList: { padding: 8, gap: 8 },
  button: { backgroundColor: "#1d356a", padding: 12, borderRadius: 4 },
  buttonText: { color: "#fff", textAlign: "center" },
});
