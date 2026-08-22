import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

import { ProviderSettingsWorkspace } from "../../../components/provider-settings-workspace.jsx";
import "./provider-settings.css";

const route = navigation.routes.find((item) => item.route_id === "model_connections");
const screen = screens.screens.find((item) => item.screen_id === "model_connections");

export default function ModelConnectionsPage() {
  return <ProviderSettingsWorkspace routeId={route.route_id} screenId={screen.screen_id} />;
}
