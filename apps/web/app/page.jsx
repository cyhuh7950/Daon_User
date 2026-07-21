import navigation from "@daon-user/contracts/navigation.json";
import screens from "@daon-user/contracts/screens.json";

const homeRoute = navigation.routes.find((route) => route.route_id === "home");
const homeScreen = screens.screens.find((screen) => screen.screen_id === "home");

export default function HomePrototypePage() {
  return (
    <main className="home-shell" data-route-id={homeRoute.route_id} data-screen-id={homeScreen.screen_id}>
      <p className="home-eyebrow">Home · 프로토타입</p>
      <h1>Daon 사용자 프로그램</h1>
      <p>홈 업무 요약은 M2-03 이후 연결되며 현재 unavailable 상태입니다.</p>
      <a className="home-workspace-link" href="/workspaces/workspace-release-one">Release 1 Workspace 열기</a>
    </main>
  );
}
