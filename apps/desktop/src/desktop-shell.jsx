import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { createProductWorkspaceState } from "@daon-user/ui/product-workspace-model";
import { ProductWorkspaceShell } from "@daon-user/ui/product-workspace-shell";
import navigation from "@daon-user/contracts/navigation.json";
import "@daon-user/design-tokens/tokens.css";
import "./desktop-shell.css";
import { createWindowsNavigation, selectNativeRoute } from "./desktop-shell-model.js";
import { describeLocalServiceState, retryLocalService, watchLocalServiceStatus } from "./local-service-bridge.js";
import { createNativeSessionBridge } from "./native-session-bridge.js";
import { NativeAuthPanel } from "./native-auth-panel.jsx";
import { createWindowsWorkspaceAdapter } from "./windows-workspace-adapter.js";
import { createOfflineStudioAdapter } from "./offline-studio-adapter.js";
import { createOfflineSyncAdapter } from "./offline-sync-adapter.js";
import { createOfflineStudioState, reduceOfflineStudioState } from "./offline-studio-model.js";
import { OfflineStudioPane } from "./offline-studio-pane.jsx";
import { WorkspaceOperationsModal } from "./workspace-operations-modal.jsx";
import { WorkspaceSettingsModal } from "./workspace-settings-modal.jsx";
import "./workspace-visual-tokens.css";

const LABELS = {
  WorkspaceDetail: "Workspace",
  AccountSettings: "Account",
  OrganizationSettings: "Organization",
  Operations: "Operations",
  Notifications: "Notifications"
};

function DesktopWorkspaceRoute({ workspaceId, workspaceAdapter, sessionKey, nativeInvoke, offlineState, dispatchOffline }) {
  const studioAdapter = useMemo(() => createOfflineStudioAdapter({ invoke: nativeInvoke }), [nativeInvoke]);
  const syncAdapter = useMemo(() => createOfflineSyncAdapter({ invoke: nativeInvoke }), [nativeInvoke]);
  return <ProductWorkspaceShell key={sessionKey} workspaceId={workspaceId} adapter={workspaceAdapter} state={createProductWorkspaceState({ status: workspaceAdapter ? "loading" : "unavailable", safeError: workspaceAdapter ? null : "WORKSPACE_ADAPTER_UNAVAILABLE" })} desktopOfflineStudio={{
    editor: (workspaceState) => offlineState.view === "settings" ? null : <OfflineStudioPane state={offlineState} dispatch={dispatchOffline} studioAdapter={studioAdapter} syncAdapter={syncAdapter} workspaceId={workspaceId} sources={workspaceState.sources} surface="editor" />,
    studio: (workspaceState) => <OfflineStudioPane state={offlineState} dispatch={dispatchOffline} studioAdapter={studioAdapter} syncAdapter={syncAdapter} workspaceId={workspaceId} sources={workspaceState.sources} surface="studio" />,
  }} />;
}

function RouteSurface({ routeKey, workspaceId, workspaceAdapter, sessionKey, nativeInvoke, offlineState, dispatchOffline }) {
  if (routeKey === "WorkspaceDetail") {
    return <DesktopWorkspaceRoute workspaceId={workspaceId} workspaceAdapter={workspaceAdapter} sessionKey={sessionKey} nativeInvoke={nativeInvoke} offlineState={offlineState} dispatchOffline={dispatchOffline} />;
  }
  return <section className="desktop-safe-surface" role="status"><h2>{LABELS[routeKey]}</h2><p>실제 사용자 기능 연결 전에는 성공 데이터를 표시하지 않습니다.</p></section>;
}

export function DesktopShell({ nativeInvoke, sessionWatchOptions } = {}) {
  const sessionBridge = useMemo(() => createNativeSessionBridge({ invoke: nativeInvoke }), [nativeInvoke]);
  const authorizationRequest = useRef(0);
  const currentSession = useRef({ authenticated: false });
  const [activeKey, setActiveKey] = useState("WorkspaceDetail");
  const [activeModal, setActiveModal] = useState(null);
  const [offlineState, dispatchOffline] = useReducer(
    reduceOfflineStudioState, undefined, () => createOfflineStudioState(),
  );
  const [nativeSession, setNativeSession] = useState({ authenticated: false, recoveryOperations: [], authorizationRevision: 0 });
  const [localService, setLocalService] = useState({
    state: "starting",
    retryable: false,
    error_code: null
  });

  useEffect(() => {
    return watchLocalServiceStatus(setLocalService);
  }, []);

  const applyNativeSession = useCallback(async (status) => {
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
  const workspaceAdapter = useMemo(
    () => nativeSession.authenticated
      ? createWindowsWorkspaceAdapter(nativeSession.workspaceId, { invoke: nativeInvoke })
      : null,
    [nativeInvoke, nativeSession.authenticated, nativeSession.sessionId, nativeSession.workspaceId]
  );

  const retry = async () => {
    setLocalService({ state: "retrying", retryable: false, error_code: null });
    setLocalService(await retryLocalService());
  };

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

  return (
    <div className="desktop-shell" data-visual-system="notebook-violet" data-client-type="windows" data-runtime-state={localService.state} data-session-tree-key={sessionTreeKey}>
      <header className="desktop-titlebar">
        <div>
          <p className="desktop-eyebrow">Windows App</p>
          <h1>Daon 사용자 프로그램</h1>
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
              <RouteSurface routeKey={route.key} workspaceId={nativeSession.workspaceId} workspaceAdapter={workspaceAdapter} sessionKey={nativeSession.sessionId} nativeInvoke={nativeInvoke} offlineState={offlineState} dispatchOffline={dispatchOffline} />
            </section>
        ))}
      </div>
      <WorkspaceOperationsModal open={activeModal === "operations"} onClose={() => setActiveModal(null)} localService={localService} offlineState={offlineState} cloudState={nativeSession.authenticated ? "authenticated" : "unavailable"} />
      <WorkspaceSettingsModal open={activeModal === "settings"} onClose={() => setActiveModal(null)} offlineState={offlineState} onSave={(deploymentId) => dispatchOffline({ type: "model_selected", deploymentId })} />
    </div>
  );
}
