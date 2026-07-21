import { AdaptiveWorkspace } from "@daon-user/ui";
import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

const workspaceRoute = navigation.routes.find((route) => route.route_id === "workspace_detail");
const workspaceScreen = screens.screens.find((screen) => screen.screen_id === "workspace_detail");

export default function WorkspaceDetailPrototypePage() {
  return <AdaptiveWorkspace routeId={workspaceRoute.route_id} screenId={workspaceScreen.screen_id} />;
}
