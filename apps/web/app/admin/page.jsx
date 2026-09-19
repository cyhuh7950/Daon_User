import { cookies } from "next/headers";
import { forbidden, redirect } from "next/navigation";
import { AdminUserConsole } from "../../components/admin-user-console.jsx";
import { getServerAdminAccess } from "../../lib/server-admin-access.js";
import { SESSION_COOKIE_NAME } from "../../lib/session-cookie.js";

export default async function AdminPage() {
  const cookieStore = await cookies();
  const sessionCookie = cookieStore.get(SESSION_COOKIE_NAME)?.value;
  if (!sessionCookie) redirect("/");
  const access = await getServerAdminAccess(sessionCookie);
  if (access === "unauthenticated") redirect("/");
  if (access === "password_change_required") redirect("/password-change");
  if (access !== "authorized") forbidden();
  return <AdminUserConsole />;
}
