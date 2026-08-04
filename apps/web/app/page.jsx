import { ProductionBoundEvidenceHub } from "@daon-user/ui";
import { AuthPane } from "../lib/auth-pane.jsx";
import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

const homeRoute = navigation.routes.find((route) => route.route_id === "home");
const homeScreen = screens.screens.find((screen) => screen.screen_id === "home");

export default function HomePrototypePage() {
  return <>
    <ProductionBoundEvidenceHub route={homeRoute} screen={homeScreen} />
    <AuthPane />
  </>;
}
