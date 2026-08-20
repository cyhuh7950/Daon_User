"use client";

import { EgressPolicyPane } from "@daon-user/ui/egress-policy";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { getEffectiveEgressPolicy, getOrganizationSettingsContext, saveOrganizationEgressPolicy, saveWorkspaceEgressPolicy } from "../../../lib/egress-policy-api.js";

const adapter = {
  loadContext: getOrganizationSettingsContext,
  load: getEffectiveEgressPolicy,
  saveOrganization: saveOrganizationEgressPolicy,
  saveWorkspace: saveWorkspaceEgressPolicy,
};
function OrganizationSettingsContent() {
  const searchParams = useSearchParams();
  return <main className="organization-settings-page"><EgressPolicyPane
    organizationId={searchParams.get("organization_id") || ""}
    workspaceId={searchParams.get("workspace_id") || ""} adapter={adapter} /></main>;
}

export default function OrganizationSettingsPage() {
  return <Suspense fallback={<p role="status">조직 설정을 불러오는 중입니다.</p>}>
    <OrganizationSettingsContent />
  </Suspense>;
}
