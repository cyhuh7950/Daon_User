"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createNotebookContextWorkspaceAdapter } from "@daon-user/ui/notebook-context-adapter";
import { ActualWorkspace, createWebProductWorkspaceAdapter } from "./actual-workspace.jsx";
import { getCurrentNotebookSession, getNotebook, getNotebookContext } from "../lib/notebook-api.js";
import { logoutCurrentSession } from "../lib/auth-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const SAFE_ERRORS = new Set(["NOTEBOOK_NOT_FOUND", "NOTEBOOK_UNAVAILABLE", "NOTEBOOK_CONTEXT_INVALID"]);

export function NotebookProductWorkspace({ notebookId }) {
  const logoutPending = useRef(false);
  const protectedRoot = useRef(null);
  const [sessionValidated, setSessionValidated] = useState(false);
  const [view, setView] = useState({ state: "loading", workspaceId: null, context: null, error: null });

  const conceal = useCallback(() => {
    concealProtectedRoute(protectedRoot.current);
    setSessionValidated(false);
  }, []);
  const reveal = useCallback(() => {
    revealProtectedRoute(protectedRoot.current);
    setSessionValidated(true);
  }, []);

  const load = useCallback(async (signal) => {
    conceal();
    if (!SAFE_ID.test(notebookId || "")) {
      setView({ state: "error", workspaceId: null, context: null, error: "NOTEBOOK_NOT_FOUND" });
      reveal();
      return;
    }
    setView((current) => ({ ...current, state: "loading", error: null }));
    try {
      const session = await getCurrentNotebookSession({ signal });
      await getNotebook(session.workspace_id, notebookId, { signal });
      const selected = await getNotebookContext(session.workspace_id, notebookId, { signal });
      if (!signal?.aborted) setView({
        state: "ready", workspaceId: session.workspace_id, context: selected.data, error: null,
      });
      if (!signal?.aborted) reveal();
    } catch (error) {
      if (signal?.aborted) return;
      if (error?.message === "AUTHENTICATION_REQUIRED") {
        window.location.replace("/");
        return;
      }
      setView({
        state: "error", workspaceId: null, context: null,
        error: SAFE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_UNAVAILABLE",
      });
      reveal();
    }
  }, [conceal, notebookId, reveal]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    let controller = null;
    const revalidate = () => {
      conceal();
      controller?.abort();
      controller = new AbortController();
      void load(controller.signal);
    };
    const onPageShow = (event) => { if (event.persisted) revalidate(); };
    const onPageHide = () => concealProtectedRoute(protectedRoot.current);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("popstate", revalidate);
    return () => {
      controller?.abort();
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("popstate", revalidate);
    };
  }, [conceal, load]);

  const adapter = useMemo(() => view.state === "ready"
    ? createNotebookContextWorkspaceAdapter(
      createWebProductWorkspaceAdapter(view.workspaceId, view.context.notebook_id), view.context,
    )
    : null, [view]);

  const handleLogout = async () => {
    if (logoutPending.current) return;
    logoutPending.current = true;
    try {
      await logoutCurrentSession();
      setView({ state: "loading", workspaceId: null, context: null, error: null });
      window.location.replace("/");
    } finally {
      logoutPending.current = false;
    }
  };

  if (view.state === "loading") return <main className="notebook-route-state" aria-busy="true">Notebook을 불러오는 중입니다.</main>;
  if (view.state === "error") return <main className="notebook-route-state" role="alert">
    <p>{view.error}</p>
    <button type="button" onClick={() => window.location.assign("/notebooks")}>Notebook 홈으로</button>
  </main>;
  return <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
    aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
    <ActualWorkspace workspaceId={view.workspaceId} notebookId={view.context.notebook_id} adapter={adapter} onLogout={() => void handleLogout()} />
  </div>;
}
