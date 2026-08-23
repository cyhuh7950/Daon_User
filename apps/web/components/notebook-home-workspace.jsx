"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { NotebookHome } from "@daon-user/ui/notebook-home";
import { createNotebook, getCurrentNotebookSession, listNotebooks, requestNotebookDeletion, getNotebookDeletion } from "../lib/notebook-api.js";
import { logoutCurrentSession } from "../lib/auth-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["NOTEBOOK_UNAVAILABLE", "SESSION_UNAVAILABLE", "SESSION_RESPONSE_INVALID"]);

export function NotebookHomeWorkspace() {
  const logoutPending = useRef(false);
  const protectedRoot = useRef(null);
  const [sessionValidated, setSessionValidated] = useState(false);
  const [state, setState] = useState("loading");
  const [notebooks, setNotebooks] = useState([]);
  const [workspaceId, setWorkspaceId] = useState(null);
  const [errorCode, setErrorCode] = useState(null);

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
    setState("loading");
    setErrorCode(null);
    try {
      const session = await getCurrentNotebookSession({ signal });
      const result = await listNotebooks(session.workspace_id, { signal });
      setWorkspaceId(session.workspace_id);
      setNotebooks(result.data);
      setState("ready");
      reveal();
    } catch (error) {
      if (signal?.aborted) return;
      if (error?.message === "AUTHENTICATION_REQUIRED") {
        window.location.replace("/");
        return;
      }
      setWorkspaceId(null);
      setNotebooks([]);
      setErrorCode(SAFE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_UNAVAILABLE");
      setState("error");
      reveal();
    }
  }, [conceal, reveal]);

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

  const handleCreate = async (input) => {
    if (!workspaceId) throw new Error("NOTEBOOK_UNAVAILABLE");
    const result = await createNotebook(workspaceId, input, { idempotencyKey: `notebook-${crypto.randomUUID()}` });
    setNotebooks((current) => [result.data, ...current.filter((item) => item.notebook_id !== result.data.notebook_id)]);
    return result.data;
  };

  const handleDelete = async (notebook, titleConfirmation) => {
    if (!workspaceId) throw new Error("NOTEBOOK_UNAVAILABLE");
    const result = await requestNotebookDeletion(workspaceId, notebook.notebook_id, titleConfirmation, {
      idempotencyKey: `notebook-delete-${crypto.randomUUID()}`, etag: notebook.etag,
    });
    let current = result.data;
    while (current.status === "accepted" || current.status === "deleting") {
      await new Promise((resolve) => setTimeout(resolve, 500));
      current = (await getNotebookDeletion(workspaceId, notebook.notebook_id, current.deletion_request_id)).data;
    }
    if (current.status !== "completed") throw new Error(current.safe_error_code || "NOTEBOOK_DELETE_FAILED");
    setNotebooks((items) => items.filter((item) => item.notebook_id !== notebook.notebook_id));
  };

  const openNotebook = ({ notebookId }) => {
    window.location.assign(`/notebooks/${encodeURIComponent(notebookId)}`);
  };

  const handleOpenSetting = (settingId) => {
    const routes = Object.freeze({
      screen: "/settings/screen",
      license: "/settings/license",
      manual: "/settings/manual",
      "organization-join": "/organization/join",
    });
    const route = routes[settingId];
    if (route) window.location.assign(route);
  };

  const handleLogout = async () => {
    if (logoutPending.current) return;
    logoutPending.current = true;
    try {
      await logoutCurrentSession();
      setWorkspaceId(null);
      setNotebooks([]);
      window.location.replace("/");
    } finally {
      logoutPending.current = false;
    }
  };

  return <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
    aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
  <NotebookHome
    state={state}
    notebooks={notebooks}
    errorCode={errorCode}
    onReload={() => void load()}
    onCreate={handleCreate}
    onDelete={handleDelete}
    onOpenNotebook={openNotebook}
    onOpenSetting={handleOpenSetting}
    onLogout={() => void handleLogout()}
  /></div>;
}
