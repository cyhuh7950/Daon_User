import { AccountSecurityWorkspace } from "@daon-user/ui";
import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

const route = navigation.routes.find((item) => item.route_id === "organization_settings");
const screen = screens.screens.find((item) => item.screen_id === "organization_settings");

export default function OrganizationSettingsPrototypePage() {
  return <AccountSecurityWorkspace initialScreen="organization" routeId={route.route_id} screenId={screen.screen_id} />;
}
