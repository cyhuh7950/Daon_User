"use client";

import { useEffect, useState } from "react";
import { OperationsRecoveryWorkspace } from "@daon-user/ui";
import { recoveryApi, resolveRecoverySession } from "../../lib/recovery-api.js";

export function RecoveryOperationsWorkspace({ routeId, screenId }) {
  const [session, setSession] = useState({ status: "loading", context: null, code: null });

  useEffect(() => {
    let active = true;
    resolveRecoverySession(recoveryApi)
      .then((context) => {
        if (active) setSession({ status: "ready", context, code: null });
      })
      .catch((error) => {
        const code = error?.code === "ACCESS_INVALID" || error?.code === "AUTHENTICATION_REQUIRED"
          ? "AUTHENTICATION_REQUIRED"
          : "RESOURCE_UNAVAILABLE";
        if (active) setSession({ status: "failed", context: null, code });
      });
    return () => { active = false; };
  }, []);

  if (session.status !== "ready") {
    return <main className="operations-shell"><h1>운영 상태·복구</h1><p className="operations-visible-warning">{session.status === "loading" ? "운영 세션 확인 중" : session.code}</p></main>;
  }

  return <OperationsRecoveryWorkspace
    initialScreen="operations"
    routeId={routeId}
    screenId={screenId}
    sessionContext={session.context}
    recoveryAdapter={recoveryApi}
  />;
}
