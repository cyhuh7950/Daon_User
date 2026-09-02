import { NotebookHomeWorkspace } from "../../components/notebook-home-workspace.jsx";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export default async function NotebooksPage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("__Host-daon_session")?.value) redirect("/");
  return <NotebookHomeWorkspace />;
}
