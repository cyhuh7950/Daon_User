import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

import { ActualWorkspace } from "../../../components/actual-workspace.jsx";

const workspaceRoute = navigation.routes.find((route) => route.route_id === "workspace_detail");
const workspaceScreen = screens.screens.find((screen) => screen.screen_id === "workspace_detail");

export default async function WorkspaceDetailPage({ params }) {
  const { workspace_id: workspaceId } = await params;
  return <ActualWorkspace workspaceId={workspaceId} routeId={workspaceRoute.route_id} screenId={workspaceScreen.screen_id} />;
}
