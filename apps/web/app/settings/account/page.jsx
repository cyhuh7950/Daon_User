import { AccountSecurityWorkspace } from "@daon-user/ui";
import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

const route = navigation.routes.find((item) => item.route_id === "account_settings");
const screen = screens.screens.find((item) => item.screen_id === "account_settings");

export default function AccountSettingsPrototypePage() {
  return <AccountSecurityWorkspace initialScreen="account" routeId={route.route_id} screenId={screen.screen_id} />;
}
