"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { NotebookHome } from "@daon-user/ui/notebook-home";
import { createNotebook, getCurrentNotebookSession, listNotebooks, requestNotebookDeletion, getNotebookDeletion } from "../lib/notebook-api.js";
import { listSessionTenants, logoutCurrentSession, switchSessionTenant } from "../lib/auth-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["NOTEBOOK_UNAVAILABLE", "SESSION_UNAVAILABLE", "SESSION_RESPONSE_INVALID"]);
const replaceLocation = (path) => window.location.replace(path);

function notebookOperationKey(prefix) {
  if (typeof globalThis.crypto?.getRandomValues !== "function") throw new Error("NOTEBOOK_CRYPTO_UNAVAILABLE");
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  return `${prefix}-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

export function NotebookHomeWorkspace({
  getSession = getCurrentNotebookSession,
  getNotebooks = listNotebooks,
  navigate = replaceLocation,
} = {}) {
  const logoutPending = useRef(false);
  const protectedRoot = useRef(null);
  const [sessionValidated, setSessionValidated] = useState(false);
  const [state, setState] = useState("loading");
  const [notebooks, setNotebooks] = useState([]);
  const [workspaceId, setWorkspaceId] = useState(null);
  const [isSystemAdmin, setIsSystemAdmin] = useState(false);
  const [userId, setUserId] = useState(null);
  const [errorCode, setErrorCode] = useState(null);
  const [tenantScope, setTenantScope] = useState(null);
  const [scopeError, setScopeError] = useState(null);
  const [switchingScope, setSwitchingScope] = useState(false);

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
      const session = await getSession({ signal });
      if (session.password_change_required) {
        navigate("/password-change");
        return;
      }
      const [scopeResult, notebookResult] = await Promise.allSettled([
        listSessionTenants({ signal }), getNotebooks(session.workspace_id, { signal }),
      ]);
      if (signal?.aborted) return;
      if ([scopeResult, notebookResult].some((result) => result.status === "rejected"
        && result.reason?.message === "AUTHENTICATION_REQUIRED")) {
        navigate("/");
        return;
      }
      setWorkspaceId(session.workspace_id);
      setUserId(session.login_id ?? session.user_id);
      setIsSystemAdmin(session.is_system_admin);
      setTenantScope(scopeResult.status === "fulfilled" ? scopeResult.value : null);
      setScopeError(scopeResult.status === "rejected"
        ? scopeResult.reason?.message ?? "TENANT_SCOPE_FAILED" : null);
      setNotebooks(notebookResult.status === "fulfilled" ? notebookResult.value.data : []);
      setErrorCode(notebookResult.status === "rejected"
        ? SAFE_ERRORS.has(notebookResult.reason?.message)
          ? notebookResult.reason.message : "NOTEBOOK_UNAVAILABLE" : null);
      setState(notebookResult.status === "fulfilled" ? "ready" : "error");
      reveal();
    } catch (error) {
      if (signal?.aborted) return;
      if (error?.message === "AUTHENTICATION_REQUIRED") {
        navigate("/");
        return;
      }
      setWorkspaceId(null);
      setUserId(null);
      setIsSystemAdmin(false);
      setNotebooks([]);
      setTenantScope(null);
      setScopeError(null);
      setErrorCode(SAFE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_UNAVAILABLE");
      setState("error");
      reveal();
    }
  }, [conceal, getNotebooks, getSession, navigate, reveal]);

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
    const result = await createNotebook(workspaceId, input, { idempotencyKey: notebookOperationKey("notebook") });
    setNotebooks((current) => [result.data, ...current.filter((item) => item.notebook_id !== result.data.notebook_id)]);
    return result.data;
  };

  const handleDelete = async (notebook, titleConfirmation) => {
    if (!workspaceId) throw new Error("NOTEBOOK_UNAVAILABLE");
    const result = await requestNotebookDeletion(workspaceId, notebook.notebook_id, titleConfirmation, {
      idempotencyKey: notebookOperationKey("notebook-delete"), etag: notebook.etag,
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
      "model-connections": "/settings/model-connections",
      "organization-policy": "/settings/organization",
      "user-management": "/admin",
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

  const handleSwitchTenant = async (tenantId) => {
    if (switchingScope) return;
    setSwitchingScope(true);
    setScopeError(null);
    try {
      await switchSessionTenant(tenantId);
      conceal();
      setNotebooks([]);
      setWorkspaceId(null);
      setTenantScope(null);
      window.location.replace("/notebooks");
    } catch (error) {
      if (["TENANT_SCOPE_403", "TENANT_SCOPE_404", "TENANT_SCOPE_422"].includes(error?.message)) {
        setScopeError(error.message);
        return;
      }
      conceal();
      setNotebooks([]);
      setWorkspaceId(null);
      setTenantScope(null);
      setUserId(null);
      setIsSystemAdmin(false);
      setScopeError(null);
      window.location.replace("/");
    } finally {
      setSwitchingScope(false);
    }
  };

  return <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
    aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
  <NotebookHome
    state={state}
    notebooks={notebooks}
    errorCode={errorCode}
    showUserManagement={isSystemAdmin}
    showOrganizationPolicy={isSystemAdmin}
    displayIdentity={userId ? `${userId}${isSystemAdmin ? " · 시스템 관리자" : ""}` : null}
    tenantScope={tenantScope}
    scopeError={scopeError}
    switchingScope={switchingScope}
    onSwitchTenant={(tenantId) => void handleSwitchTenant(tenantId)}
    onReload={() => void load()}
    onCreate={handleCreate}
    onDelete={handleDelete}
    onOpenNotebook={openNotebook}
    onOpenSetting={handleOpenSetting}
    onLogout={() => void handleLogout()}
  /></div>;
}
