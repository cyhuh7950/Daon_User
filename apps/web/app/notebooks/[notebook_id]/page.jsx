import { NotebookProductWorkspace } from "../../../components/notebook-product-workspace.jsx";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { SESSION_COOKIE_NAME } from "../../../lib/session-cookie.js";

export default async function NotebookPage({ params }) {
  const cookieStore = await cookies();
  if (!cookieStore.get(SESSION_COOKIE_NAME)?.value) redirect("/");
  const { notebook_id: notebookId } = await params;
  return <NotebookProductWorkspace notebookId={notebookId} />;
}
