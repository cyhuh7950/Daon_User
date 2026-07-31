import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";
import { RecoveryOperationsWorkspace } from "./recovery-workspace.jsx";

const route = navigation.routes.find((item) => item.route_id === "operations");
const screen = screens.screens.find((item) => item.screen_id === "operations");

export default function OperationsPrototypePage() {
  return <RecoveryOperationsWorkspace routeId={route.route_id} screenId={screen.screen_id} />;
}
