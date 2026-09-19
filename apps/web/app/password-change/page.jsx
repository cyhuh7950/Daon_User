import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE_NAME } from "../../lib/session-cookie.js";
import { PasswordChangeWorkspace } from "../../components/password-change-workspace.jsx";

export default async function PasswordChangePage() {
  const cookieStore = await cookies();
  if (!cookieStore.get(SESSION_COOKIE_NAME)?.value) redirect("/");
  return <PasswordChangeWorkspace />;
}
