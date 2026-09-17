import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { PasswordChangeWorkspace } from "../../components/password-change-workspace.jsx";
import { webSessionCookieName } from "../../lib/web-session-cookie.js";

export default async function PasswordChangePage() {
  const cookieStore = await cookies();
  if (!cookieStore.get(webSessionCookieName())?.value) redirect("/");
  return <PasswordChangeWorkspace />;
}
