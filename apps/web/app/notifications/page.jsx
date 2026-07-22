import { OperationsRecoveryWorkspace } from "@daon-user/ui";
import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

const route = navigation.routes.find((item) => item.route_id === "notifications");
const screen = screens.screens.find((item) => item.screen_id === "notifications");

export default function NotificationsPrototypePage() {
  return <OperationsRecoveryWorkspace initialScreen="notifications" routeId={route.route_id} screenId={screen.screen_id} />;
}
