import { NotebookHomeWorkspace } from "../../components/notebook-home-workspace.jsx";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { webSessionCookieName } from "../../lib/web-session-cookie.js";

export default async function NotebooksPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get(webSessionCookieName())?.value) redirect("/");
  return <NotebookHomeWorkspace />;
}
