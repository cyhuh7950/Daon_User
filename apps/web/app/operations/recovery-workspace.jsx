"use client";

import { OperationsRecoveryWorkspace } from "@daon-user/ui";
import { recoveryApi } from "../../lib/recovery-api.js";

export function RecoveryOperationsWorkspace({ routeId, screenId }) {
  return <OperationsRecoveryWorkspace
    initialScreen="operations"
    routeId={routeId}
    screenId={screenId}
    recoveryAdapter={recoveryApi}
  />;
}
