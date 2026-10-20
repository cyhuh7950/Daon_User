"use client";

import { useEffect, useState } from "react";
import { AuthPane } from "../lib/auth-pane.jsx";
import { authEntryPath, getCurrentAuthSession } from "../lib/auth-api.js";

export function AuthLanding({ hasSessionCookie }) {
  const [state, setState] = useState(hasSessionCookie ? "checking" : "login");
  useEffect(() => {
    if (!hasSessionCookie) return;
    const controller = new AbortController();
    getCurrentAuthSession({ signal: controller.signal }).then((session) => {
      if (!controller.signal.aborted) window.location.replace(authEntryPath(session));
    }).catch((error) => {
      if (controller.signal.aborted) return;
      setState(error?.message === "AUTHENTICATION_REQUIRED" ? "login" : "unavailable");
    });
    return () => controller.abort();
  }, [hasSessionCookie]);
  if (state === "login") return <AuthPane />;
  if (state === "unavailable") return <section className="daon-auth-pane" role="alert"><p>인증 상태를 확인하지 못했습니다.</p><button type="button" onClick={() => window.location.reload()}>다시 시도</button></section>;
  return <section className="daon-auth-pane" aria-busy="true" role="status">인증 상태를 확인하는 중입니다.</section>;
}
