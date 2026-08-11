import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccountSecurityWorkspace,
  AdaptiveWorkspace,
  OperationsRecoveryWorkspace,
  ProductionBoundEvidenceHub
} from "@daon-user/ui";
import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";
import "@daon-user/design-tokens/tokens.css";
import "./desktop-shell.css";
import { createWindowsNavigation, selectNativeRoute } from "./desktop-shell-model.js";
import {
  describeLocalServiceState,
  retryLocalService,
  watchLocalServiceStatus
} from "./local-service-bridge.js";
import { createNativeSessionBridge } from "./native-session-bridge.js";
import { NativeAuthPanel } from "./native-auth-panel.jsx";
import { WindowsRecoveryAdapter } from "./windows-recovery-adapter.js";

const LABELS = {
  Home: "Home",
  WorkspaceDetail: "Workspace",
  AccountSettings: "Account",
  OrganizationSettings: "Organization",
  Operations: "Operations",
  Notifications: "Notifications"
};

function RouteSurface({ routeKey, route, screen, recoveryAdapter, sessionContext, sessionTreeKey }) {
  if (routeKey === "Home") return <ProductionBoundEvidenceHub route={route} screen={screen} />;
  if (routeKey === "WorkspaceDetail") return <AdaptiveWorkspace routeId={route.route_id} screenId={screen.screen_id} />;
  if (routeKey === "AccountSettings") return <AccountSecurityWorkspace initialScreen="account" />;
  if (routeKey === "OrganizationSettings") return <AccountSecurityWorkspace initialScreen="organization" />;
  if (routeKey === "Operations") return <OperationsRecoveryWorkspace key={sessionTreeKey} initialScreen="operations" clientType="windows" recoveryAdapter={recoveryAdapter} sessionContext={sessionContext} />;
  return <OperationsRecoveryWorkspace key={sessionTreeKey} initialScreen="notifications" clientType="windows" recoveryAdapter={recoveryAdapter} sessionContext={sessionContext} />;
}

export function DesktopShell({ nativeInvoke, sessionWatchOptions } = {}) {
  const routes = useMemo(() => createWindowsNavigation(navigation.routes), []);
  const sessionBridge = useMemo(() => createNativeSessionBridge({ invoke: nativeInvoke }), [nativeInvoke]);
  const recoveryAdapter = useMemo(() => new WindowsRecoveryAdapter({ invoke: nativeInvoke }), [nativeInvoke]);
  const authorizationRequest = useRef(0);
  const currentSession = useRef({ authenticated: false });
  const [activeKey, setActiveKey] = useState("Home");
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
    setNativeSession({ ...status, recoveryOperations: [], authorizationRevision: request * 2 });
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

  const sessionContext = nativeSession.authenticated ? {
    userId: nativeSession.userId,
    tenantId: nativeSession.tenantId,
    workspaceId: nativeSession.workspaceId,
    sessionId: nativeSession.sessionId,
    recoveryOperations: nativeSession.recoveryOperations,
    membership: null
  } : null;
  const sessionTreeKey = nativeSession.authenticated
    ? `${nativeSession.sessionId}:${nativeSession.authorizationRevision}`
    : `unauthenticated:${nativeSession.authorizationRevision}`;

  const retry = async () => {
    setLocalService({ state: "retrying", retryable: false, error_code: null });
    setLocalService(await retryLocalService());
  };

  return (
    <div className="desktop-shell" data-client-type="windows" data-runtime-state={localService.state} data-session-tree-key={sessionTreeKey}>
      <header className="desktop-titlebar">
        <div>
          <p className="desktop-eyebrow">Windows App</p>
          <h1>Daon 사용자 프로그램</h1>
        </div>
        <div>
          <span className="desktop-runtime-badge" role="status">
            {describeLocalServiceState(localService)}
          </span>
          {localService.retryable ? (
            <button type="button" onClick={retry}>다시 시도</button>
          ) : null}
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
      <div className="desktop-content">
        {routes.map((route) => {
          const screen = screens.screens.find((candidate) => candidate.screen_id === route.route_id)
            ?? screens.screens.find((candidate) => candidate.route_id === route.route_id)
            ?? { screen_id: route.route_id };
          return (
            <section key={route.key} hidden={route.key !== activeKey} aria-label={LABELS[route.key]}>
              <RouteSurface routeKey={route.key} route={route} screen={screen} recoveryAdapter={recoveryAdapter} sessionContext={sessionContext} sessionTreeKey={sessionTreeKey} />
            </section>
          );
        })}
      </div>
    </div>
  );
}
