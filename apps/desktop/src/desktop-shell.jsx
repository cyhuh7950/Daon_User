import { useMemo, useState } from "react";
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

const LABELS = {
  Home: "Home",
  WorkspaceDetail: "Workspace",
  AccountSettings: "Account",
  OrganizationSettings: "Organization",
  Operations: "Operations",
  Notifications: "Notifications"
};

function RouteSurface({ routeKey, route, screen }) {
  if (routeKey === "Home") return <ProductionBoundEvidenceHub route={route} screen={screen} />;
  if (routeKey === "WorkspaceDetail") return <AdaptiveWorkspace routeId={route.route_id} screenId={screen.screen_id} />;
  if (routeKey === "AccountSettings") return <AccountSecurityWorkspace initialScreen="account" />;
  if (routeKey === "OrganizationSettings") return <AccountSecurityWorkspace initialScreen="organization" />;
  if (routeKey === "Operations") return <OperationsRecoveryWorkspace initialScreen="operations" clientType="windows" />;
  return <OperationsRecoveryWorkspace initialScreen="notifications" clientType="windows" />;
}

export function DesktopShell() {
  const routes = useMemo(() => createWindowsNavigation(navigation.routes), []);
  const [activeKey, setActiveKey] = useState("Home");

  return (
    <div className="desktop-shell" data-client-type="windows" data-runtime-state="deferred_actual">
      <header className="desktop-titlebar">
        <div>
          <p className="desktop-eyebrow">Windows App</p>
          <h1>Daon 사용자 프로그램</h1>
        </div>
        <span className="desktop-runtime-badge" role="status">연결 기능 deferred_actual</span>
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
              <RouteSurface routeKey={route.key} route={route} screen={screen} />
            </section>
          );
        })}
      </div>
    </div>
  );
}
