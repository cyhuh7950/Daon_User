import { redirect } from "next/navigation";

export default function LegacyWorkspaceDetailPage() {
  redirect("/notebooks");
}
