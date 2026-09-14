import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { PasswordChangeWorkspace } from "../../components/password-change-workspace.jsx";

export default async function PasswordChangePage() {
  const cookieStore = await cookies();
  if (!cookieStore.get("__Host-daon_session")?.value) redirect("/");
  return <PasswordChangeWorkspace />;
}
