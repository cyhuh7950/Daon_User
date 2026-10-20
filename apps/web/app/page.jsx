import { AuthLanding } from "../components/auth-landing.jsx";
import { cookies } from "next/headers";
import { webSessionCookieName } from "../lib/web-session-cookie.js";

export default async function HomePage() {
  const hasSessionCookie = Boolean((await cookies()).get(webSessionCookieName())?.value);
  return <AuthLanding hasSessionCookie={hasSessionCookie} />;
}
