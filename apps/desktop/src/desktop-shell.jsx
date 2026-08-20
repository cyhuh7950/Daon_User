import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { createProductWorkspaceState } from "@daon-user/ui/product-workspace-model";
import { ProductWorkspaceShell } from "@daon-user/ui/product-workspace-shell";
import { NotebookHome } from "@daon-user/ui/notebook-home";
import { createNotebookContextWorkspaceAdapter } from "@daon-user/ui/notebook-context-adapter";
import navigation from "@daon-user/contracts/navigation.json";
import "@daon-user/design-tokens/tokens.css";
import "./desktop-shell.css";
import { createWindowsNavigation, selectNativeRoute } from "./desktop-shell-model.js";
import { describeLocalServiceState, retryLocalService, watchLocalServiceStatus } from "./local-service-bridge.js";
import { createNativeSessionBridge } from "./native-session-bridge.js";
import { createNativeNotebookBridge } from "./native-notebook-bridge.js";
import { concealProtectedDesktop, revealProtectedDesktop } from "./desktop-protected-route.js";
import { NativeAuthPanel } from "./native-auth-panel.jsx";
import { createWindowsWorkspaceAdapter } from "./windows-workspace-adapter.js";
import { createOfflineStudioState, reduceOfflineStudioState } from "./offline-studio-model.js";
import { WorkspaceOperationsModal } from "./workspace-operations-modal.jsx";
import { WorkspaceSettingsModal } from "./workspace-settings-modal.jsx";
import { createScreenPreferencesBridge } from "./screen-preferences-bridge.js";
import "./workspace-visual-tokens.css";

const LABELS = {
  WorkspaceDetail: "Workspace",
  AccountSettings: "Account",
  OrganizationSettings: "Organization",
  Operations: "Operations",
  Notifications: "Notifications"
};

function DesktopWorkspaceRoute({ workspaceId, workspaceAdapter, sessionKey, nativeInvoke, offlineState, dispatchOffline }) {
  return <ProductWorkspaceShell key={sessionKey} workspaceId={workspaceId} adapter={workspaceAdapter} state={createProductWorkspaceState({ status: workspaceAdapter ? "loading" : "unavailable", safeError: workspaceAdapter ? null : "WORKSPACE_ADAPTER_UNAVAILABLE" })} />;
}

function RouteSurface({ routeKey, workspaceId, workspaceAdapter, sessionKey, nativeInvoke, offlineState, dispatchOffline }) {
  if (routeKey === "WorkspaceDetail") {
    return <DesktopWorkspaceRoute workspaceId={workspaceId} workspaceAdapter={workspaceAdapter} sessionKey={sessionKey} nativeInvoke={nativeInvoke} offlineState={offlineState} dispatchOffline={dispatchOffline} />;
  }
  return <section className="desktop-safe-surface" role="status"><h2>{LABELS[routeKey]}</h2><p>실제 사용자 기능 연결 전에는 성공 데이터를 표시하지 않습니다.</p></section>;
}

const NOTEBOOK_HASH = /^#\/notebooks\/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})$/u;
const homeHash = () => "#/notebooks";
const selectedHash = (notebookId) => `#/notebooks/${encodeURIComponent(notebookId)}`;
const writeDesktopRoute = (method, hash) => {
  if (typeof window.history?.[method] === "function") window.history[method](null, "", hash);
  else window.location.hash = hash;
};

export function DesktopShell({ nativeInvoke, sessionWatchOptions } = {}) {
  const sessionBridge = useMemo(() => createNativeSessionBridge({ invoke: nativeInvoke }), [nativeInvoke]);
  const screenPreferences = useMemo(() => {
    try { return createScreenPreferencesBridge({ invoke: nativeInvoke }); }
    catch { return null; }
  }, [nativeInvoke]);
  const authorizationRequest = useRef(0);
  const requestEpoch = useRef(0);
  const protectionEpoch = useRef(0);
  const desiredHash = useRef(homeHash());
  const mounted = useRef(false);
  const currentSession = useRef({ authenticated: false });
  const [activeKey, setActiveKey] = useState("WorkspaceDetail");
  const [activeModal, setActiveModal] = useState(null);
  const [screenTheme, setScreenTheme] = useState("system");
  const [screenPreferenceReady, setScreenPreferenceReady] = useState(false);
  const [systemDark, setSystemDark] = useState(false);
  const [offlineState, dispatchOffline] = useReducer(
    reduceOfflineStudioState, undefined, () => createOfflineStudioState(),
  );
  const [nativeSession, setNativeSession] = useState({ authenticated: false, recoveryOperations: [], authorizationRevision: 0 });
  const [notebookHome, setNotebookHome] = useState({ state: "loading", notebooks: [], errorCode: null });
  const [selectedNotebook, setSelectedNotebook] = useState(null);
  const [localService, setLocalService] = useState({
    state: "starting",
    retryable: false,
    error_code: null
  });

  useEffect(() => {
    return watchLocalServiceStatus(setLocalService);
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestEpoch.current += 1;
      protectionEpoch.current += 1;
    };
  }, []);

  useEffect(() => {
    let active = true;
    if (!screenPreferences) { setScreenPreferenceReady(true); return () => { active = false; }; }
    void screenPreferences.get().then(({ theme }) => {
      if (active) setScreenTheme(theme);
    }).catch(() => {}).finally(() => { if (active) setScreenPreferenceReady(true); });
    return () => { active = false; };
  }, [screenPreferences]);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return undefined;
    const update = () => setSystemDark(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const applyNativeSession = useCallback(async (status) => {
    const previous = currentSession.current;
    const identityChanged = previous.authenticated !== status.authenticated
      || previous.sessionId !== status.sessionId
      || previous.workspaceId !== status.workspaceId;
    if (identityChanged) {
      requestEpoch.current += 1;
      protectionEpoch.current += 1;
      desiredHash.current = homeHash();
      setSelectedNotebook(null);
      if (window.location.hash !== homeHash()) writeDesktopRoute("replaceState", homeHash());
    }
    const request = authorizationRequest.current + 1;
    authorizationRequest.current = request;
    currentSession.current = status;
    if (!status.authenticated) {
      setNativeSession({ authenticated: false, recoveryOperations: [], authorizationRevision: request * 2 });
      return;
    }
    setNativeSession((current) => ({
      ...status,
      recoveryOperations: current.authenticated && current.sessionId === status.sessionId
        ? current.recoveryOperations
        : [],
      authorizationRevision: request * 2
    }));
    try {
      const authorization = await sessionBridge.recoveryAuthorizationStatus();
      if (authorizationRequest.current !== request) return;
      setNativeSession({ ...status, recoveryOperations: authorization.recoveryOperations, authorizationRevision: request * 2 + 1 });
    } catch {
      if (authorizationRequest.current !== request) return;
      setNativeSession({ ...status, recoveryOperations: [], authorizationRevision: request * 2 + 1 });
    }
  }, [sessionBridge]);

  useEffect(() => sessionBridge.watch((status) => { void applyNativeSession(status); }, sessionWatchOptions), [sessionBridge, applyNativeSession, sessionWatchOptions]);

  useEffect(() => {
    if (activeKey === "Operations" && currentSession.current.authenticated) {
      void applyNativeSession(currentSession.current);
    }
  }, [activeKey, applyNativeSession]);

  const routes = useMemo(() => createWindowsNavigation(navigation.routes, {
    organization: false,
    operations: nativeSession.recoveryOperations.length > 0
  }), [nativeSession.recoveryOperations.length]);

  useEffect(() => {
    setActiveKey((current) => selectNativeRoute("WorkspaceDetail", current, routes));
  }, [routes]);

  const sessionTreeKey = nativeSession.authenticated
    ? `${nativeSession.sessionId}:${nativeSession.authorizationRevision}`
    : `unauthenticated:${nativeSession.authorizationRevision}`;
  const notebookBridge = useMemo(() => {
    try { return createNativeNotebookBridge({ invoke: nativeInvoke }); } catch { return null; }
  }, [nativeInvoke]);
  const workspaceAdapter = useMemo(() => {
    if (!nativeSession.authenticated || !selectedNotebook) return null;
    const base = createWindowsWorkspaceAdapter(nativeSession.workspaceId, {
      invoke: nativeInvoke, organizationId: nativeSession.tenantId, notebookId: selectedNotebook.notebookId,
    });
    return createNotebookContextWorkspaceAdapter(base, selectedNotebook.context);
  }, [nativeInvoke, nativeSession.authenticated, nativeSession.sessionId, nativeSession.tenantId, nativeSession.workspaceId, selectedNotebook]);

  const beginNotebookRequest = useCallback((targetHash) => {
    requestEpoch.current += 1;
    desiredHash.current = targetHash;
    return {
      epoch: requestEpoch.current,
      sessionId: nativeSession.sessionId,
      workspaceId: nativeSession.workspaceId,
      targetHash,
    };
  }, [nativeSession.sessionId, nativeSession.workspaceId]);

  const isNotebookRequestCurrent = useCallback((request) => mounted.current
    && requestEpoch.current === request.epoch
    && desiredHash.current === request.targetHash
    && currentSession.current.authenticated
    && currentSession.current.sessionId === request.sessionId
    && currentSession.current.workspaceId === request.workspaceId, []);

  const loadHome = useCallback(async ({ protectionRequest = null } = {}) => {
    if (protectionRequest === null) protectionEpoch.current += 1;
    const request = beginNotebookRequest(homeHash());
    setSelectedNotebook(null);
    if (window.location.hash !== homeHash()) writeDesktopRoute("replaceState", homeHash());
    if (!notebookBridge || !nativeSession.authenticated) return;
    setNotebookHome({ state: "loading", notebooks: [], errorCode: null });
    try {
      const notebooks = await notebookBridge.list(nativeSession.workspaceId);
      if (!isNotebookRequestCurrent(request)) return;
      setNotebookHome({ state: "ready", notebooks, errorCode: null });
    } catch (error) {
      if (!isNotebookRequestCurrent(request)) return;
      setNotebookHome({ state: "error", notebooks: [], errorCode: error?.code ?? "NOTEBOOK_UNAVAILABLE" });
    }
  }, [beginNotebookRequest, isNotebookRequestCurrent, notebookBridge, nativeSession.authenticated, nativeSession.workspaceId]);

  const openNotebook = useCallback(async ({ notebookId, replace = false, protectionRequest = null }) => {
    if (!notebookBridge || !nativeSession.authenticated) return false;
    if (protectionRequest === null) protectionEpoch.current += 1;
    const targetHash = selectedHash(notebookId);
    const request = beginNotebookRequest(targetHash);
    try {
      const notebook = await notebookBridge.get(nativeSession.workspaceId, notebookId);
      if (!isNotebookRequestCurrent(request)) return false;
      const context = await notebookBridge.context(nativeSession.workspaceId, notebookId);
      if (!isNotebookRequestCurrent(request)) return false;
      if (notebook.notebook_id !== context.notebook_id) throw new Error("NOTEBOOK_CONTEXT_INVALID");
      setSelectedNotebook({ notebookId, notebook, context });
      writeDesktopRoute(replace ? "replaceState" : "pushState", targetHash);
      return true;
    } catch {
      if (!isNotebookRequestCurrent(request)) return false;
      await loadHome({ protectionRequest });
      return false;
    }
  }, [beginNotebookRequest, isNotebookRequestCurrent, notebookBridge, nativeSession.authenticated, nativeSession.workspaceId, loadHome]);

  useEffect(() => {
    if (!nativeSession.authenticated || !notebookBridge) {
      setSelectedNotebook(null);
      return undefined;
    }
    const route = NOTEBOOK_HASH.exec(window.location.hash);
    if (route) void openNotebook({ notebookId: decodeURIComponent(route[1]), replace: true });
    else void loadHome();
    const revalidate = async () => {
      protectionEpoch.current += 1;
      const protectionRequest = {
        epoch: protectionEpoch.current,
        sessionId: nativeSession.sessionId,
        workspaceId: nativeSession.workspaceId,
        targetHash: window.location.hash || homeHash(),
      };
      const isRevalidationCurrent = () => mounted.current
        && protectionEpoch.current === protectionRequest.epoch
        && currentSession.current.authenticated
        && currentSession.current.sessionId === protectionRequest.sessionId
        && currentSession.current.workspaceId === protectionRequest.workspaceId
        && (window.location.hash || homeHash()) === protectionRequest.targetHash;
      concealProtectedDesktop();
      requestEpoch.current += 1;
      desiredHash.current = protectionRequest.targetHash;
      try {
        const status = await sessionBridge.status();
        if (!isRevalidationCurrent()) return;
        if (!status.authenticated || status.sessionId !== nativeSession.sessionId || status.workspaceId !== nativeSession.workspaceId) {
          if (!isRevalidationCurrent()) return;
          await applyNativeSession(status);
          return;
        }
        if (!isRevalidationCurrent()) return;
        const next = NOTEBOOK_HASH.exec(protectionRequest.targetHash);
        if (next) await openNotebook({ notebookId: decodeURIComponent(next[1]), replace: true, protectionRequest });
        else await loadHome({ protectionRequest });
      } catch {
        if (!isRevalidationCurrent()) return;
        await applyNativeSession({ authenticated: false });
      } finally {
        if (mounted.current && protectionEpoch.current === protectionRequest.epoch) revealProtectedDesktop();
      }
    };
    const pageHide = () => {
      requestEpoch.current += 1;
      protectionEpoch.current += 1;
      concealProtectedDesktop();
    };
    const pageShow = (event) => { if (event.persisted) void revalidate(); else revealProtectedDesktop(); };
    window.addEventListener("popstate", revalidate);
    window.addEventListener("hashchange", revalidate);
    window.addEventListener("pagehide", pageHide);
    window.addEventListener("pageshow", pageShow);
    return () => {
      requestEpoch.current += 1;
      protectionEpoch.current += 1;
      window.removeEventListener("popstate", revalidate);
      window.removeEventListener("hashchange", revalidate);
      window.removeEventListener("pagehide", pageHide);
      window.removeEventListener("pageshow", pageShow);
      revealProtectedDesktop();
    };
  }, [nativeSession.authenticated, nativeSession.sessionId, nativeSession.workspaceId, notebookBridge, openNotebook, loadHome, sessionBridge, applyNativeSession]);

  const createNotebook = useCallback(async (input) => {
    if (!notebookBridge) throw new Error("NOTEBOOK_UNAVAILABLE");
    const request = beginNotebookRequest(desiredHash.current || homeHash());
    const requestKey = `native-${crypto.randomUUID()}`;
    const notebook = await notebookBridge.create(nativeSession.workspaceId, input, requestKey);
    if (!isNotebookRequestCurrent(request)) return null;
    setNotebookHome((current) => ({ ...current, notebooks: [notebook, ...current.notebooks.filter((item) => item.notebook_id !== notebook.notebook_id)] }));
    return notebook;
  }, [beginNotebookRequest, isNotebookRequestCurrent, notebookBridge, nativeSession.workspaceId]);

  const logout = useCallback(() => {
    requestEpoch.current += 1;
    protectionEpoch.current += 1;
    desiredHash.current = homeHash();
    setSelectedNotebook(null);
    void applyNativeSession({ authenticated: false });
    void sessionBridge.logout().then((status) => applyNativeSession(status)).catch(() => applyNativeSession({ authenticated: false }));
  }, [applyNativeSession, sessionBridge]);

  const retry = async () => {
    setLocalService({ state: "retrying", retryable: false, error_code: null });
    setLocalService(await retryLocalService());
  };

  const effectiveScreenTheme = screenTheme === "system"
    ? (systemDark ? "dark" : "light")
    : screenTheme;

  if (!nativeSession.authenticated) {
    return (
      <main className="desktop-login-shell" data-client-type="windows" data-runtime-state={localService.state} data-session-tree-key={sessionTreeKey}>
        <div className="desktop-login-card">
          <p className="desktop-eyebrow">Windows App</p>
          <h1>Daon 사용자 프로그램</h1>
          <NativeAuthPanel sessionBridge={sessionBridge} sessionStatus={nativeSession} onSessionChange={(status) => { void applyNativeSession(status); }} />
        </div>
      </main>
    );
  }

  if (!screenPreferenceReady) {
    return <main className="desktop-login-shell" data-theme={effectiveScreenTheme} data-client-type="windows" aria-busy="true" />;
  }
  if (!selectedNotebook) {
    return <div className="desktop-notebook-home" data-theme={effectiveScreenTheme} data-client-type="windows" data-session-tree-key={sessionTreeKey}>
      <NotebookHome
        state={notebookHome.state}
        notebooks={notebookHome.notebooks}
        errorCode={notebookHome.errorCode}
        onReload={loadHome}
        onCreate={createNotebook}
        onOpenNotebook={({ notebookId }) => { void openNotebook({ notebookId }); }}
        onOpenSetting={() => setActiveModal("settings")}
        onLogout={logout}
      />
      <WorkspaceSettingsModal open={activeModal === "settings"} onClose={() => setActiveModal(null)} offlineState={offlineState} nativeInvoke={nativeInvoke} onScreenTheme={setScreenTheme} onSave={(deploymentId) => dispatchOffline({ type: "model_selected", deploymentId })} />
    </div>;
  }
  return (
    <div className="desktop-shell" data-visual-system="notebook-violet" data-theme={effectiveScreenTheme} data-client-type="windows" data-runtime-state={localService.state} data-session-tree-key={sessionTreeKey}>
      <header className="desktop-titlebar">
        <div>
          <p className="desktop-eyebrow">Windows App</p>
          <h1><button className="desktop-home-back" type="button" onClick={() => { void loadHome(); }}>← Notebook 홈</button>{selectedNotebook.notebook.title}</h1>
        </div>
        <div>
          <span className="desktop-runtime-badge" role="status" aria-label={describeLocalServiceState(localService)}>
            {localService.state === "ready" ? "정상" : localService.retryable ? "오류" : "주의"} · Offline · Cloud 인증됨
          </span>
          {localService.retryable ? (
            <button type="button" onClick={retry}>다시 시도</button>
          ) : null}
        </div>
        <div className="desktop-app-actions">
          <button type="button" onClick={() => setActiveModal("operations")}>운영상태</button>
          <button type="button" onClick={() => setActiveModal("settings")}>설정</button>
        </div>
        <NativeAuthPanel sessionBridge={sessionBridge} sessionStatus={nativeSession} onSessionChange={(status) => { void applyNativeSession(status); }} />
      </header>
      <nav className="desktop-navigation" aria-label="Windows 주 탐색">
        {routes.map((route) => (
          <button
            key={route.key}
            type="button"
            aria-current={activeKey === route.key ? "page" : undefined}
            data-native-route-key={route.key}
            onClick={() => setActiveKey((current) => selectNativeRoute(current, route.key, routes))}
          >
            {LABELS[route.key]}
          </button>
        ))}
      </nav>
      <div className="desktop-content" inert={activeModal ? "" : undefined} aria-hidden={activeModal ? "true" : undefined}>
        {routes.map((route) => (
            <section key={route.key} hidden={route.key !== activeKey} aria-label={LABELS[route.key]}>
              <RouteSurface routeKey={route.key} workspaceId={nativeSession.workspaceId} workspaceAdapter={workspaceAdapter} sessionKey={`${nativeSession.sessionId}:${selectedNotebook.notebookId}`} nativeInvoke={nativeInvoke} offlineState={offlineState} dispatchOffline={dispatchOffline} />
            </section>
        ))}
      </div>
      <WorkspaceOperationsModal open={activeModal === "operations"} onClose={() => setActiveModal(null)} localService={localService} offlineState={offlineState} cloudState={nativeSession.authenticated ? "authenticated" : "unavailable"} />
      <WorkspaceSettingsModal open={activeModal === "settings"} onClose={() => setActiveModal(null)} offlineState={offlineState} nativeInvoke={nativeInvoke} onScreenTheme={setScreenTheme} onSave={(deploymentId) => dispatchOffline({ type: "model_selected", deploymentId })} />
    </div>
  );
}
