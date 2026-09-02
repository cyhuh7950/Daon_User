import { NotebookProductWorkspace } from "../../../components/notebook-product-workspace.jsx";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export default async function NotebookPage({ params }) {
  const cookieStore = await cookies();
  if (!cookieStore.get("__Host-daon_session")?.value) redirect("/");
  const { notebook_id: notebookId } = await params;
  return <NotebookProductWorkspace notebookId={notebookId} />;
}
