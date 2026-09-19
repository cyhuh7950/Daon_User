import { NotebookHomeWorkspace } from "../../components/notebook-home-workspace.jsx";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE_NAME } from "../../lib/session-cookie.js";

export default async function NotebooksPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get(SESSION_COOKIE_NAME)?.value) redirect("/");
  return <NotebookHomeWorkspace />;
}
