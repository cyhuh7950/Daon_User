"use client";

import { useEffect, useState } from "react";
import { AuthPane } from "../lib/auth-pane.jsx";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";

export function AuthLanding() {
  const [state, setState] = useState("checking");
  useEffect(() => {
    const controller = new AbortController();
    getCurrentNotebookSession({ signal: controller.signal }).then(() => {
      window.location.replace("/notebooks");
    }).catch((error) => {
      if (controller.signal.aborted) return;
      setState(error?.message === "AUTHENTICATION_REQUIRED" ? "login" : "unavailable");
    });
    return () => controller.abort();
  }, []);
  if (state === "login") return <AuthPane />;
  if (state === "unavailable") return <section className="daon-auth-pane" role="alert"><p>인증 상태를 확인하지 못했습니다.</p><button type="button" onClick={() => window.location.reload()}>다시 시도</button></section>;
  return <section className="daon-auth-pane" aria-busy="true" role="status">인증 상태를 확인하는 중입니다.</section>;
}
