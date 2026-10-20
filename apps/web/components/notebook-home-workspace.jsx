"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { NotebookHome } from "@daon-user/ui/notebook-home";
import { createNotebook, getCurrentNotebookSession, listNotebooks, requestNotebookDeletion, getNotebookDeletion } from "../lib/notebook-api.js";
import { listSessionTenants, logoutCurrentSession, switchSessionTenant } from "../lib/auth-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["NOTEBOOK_UNAVAILABLE", "SESSION_UNAVAILABLE", "SESSION_RESPONSE_INVALID"]);
const replaceLocation = (path) => window.location.replace(path);
const sameSession = (left, right) => left && right
  && ["user_id", "session_id", "tenant_id", "workspace_id", "device_id"].every(key => left[key] === right[key]);

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
  const loadEpoch = useRef(0);
  const loadController = useRef(null);
  const currentSession = useRef(null);
  const scopeOperation = useRef(null);
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

  const load = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    const { signal } = controller;
    const epoch = ++loadEpoch.current;
    const current = () => !signal.aborted && epoch === loadEpoch.current;
    // 전송 이후 미확정인 작업만 새 본인 세션 조회로 확인한다. 전송 중 조회는 해제 근거가 아니다.
    const verifying = scopeOperation.current?.phase === "unknown" ? scopeOperation.current : null;
    conceal();
    setNotebooks([]);
    setWorkspaceId(null);
    setTenantScope(null);
    setState("loading");
    setErrorCode(null);
    try {
      const session = await getSession({ signal });
      if (!current()) return;
      if (session.password_change_required) {
        navigate("/password-change");
        return;
      }
      const [scopeResult, notebookResult] = await Promise.allSettled([
        listSessionTenants({ signal }), getNotebooks(session.workspace_id, { signal }),
      ]);
      if (!current()) return;
      if ([scopeResult, notebookResult].some((result) => result.status === "rejected"
        && result.reason?.message === "AUTHENTICATION_REQUIRED")) {
        navigate("/");
        return;
      }
      setWorkspaceId(session.workspace_id);
      setUserId(session.login_id ?? session.user_id);
      setIsSystemAdmin(session.is_system_admin);
      const scopeValid = scopeResult.status === "fulfilled" && scopeResult.value.current_tenant_id === session.tenant_id;
      setTenantScope(scopeValid ? scopeResult.value : null);
      setScopeError(scopeResult.status === "rejected"
        ? scopeResult.reason?.message ?? "TENANT_SCOPE_FAILED" : null);
      currentSession.current = session;
      if (scopeValid && scopeOperation.current && (
        !sameSession(session, scopeOperation.current.source)
        || (verifying && scopeOperation.current === verifying))) {
        scopeOperation.current = null;
        setSwitchingScope(false);
        setScopeError(null);
      }
      setNotebooks(scopeValid && notebookResult.status === "fulfilled" ? notebookResult.value.data : []);
      setErrorCode(notebookResult.status === "rejected"
        ? SAFE_ERRORS.has(notebookResult.reason?.message)
          ? notebookResult.reason.message : "NOTEBOOK_UNAVAILABLE" : null);
      setState(scopeValid && notebookResult.status === "fulfilled" ? "ready" : "error");
      reveal();
    } catch (error) {
      if (!current()) return;
      if (error?.message === "AUTHENTICATION_REQUIRED") {
        navigate("/");
        return;
      }
      setWorkspaceId(null);
      setUserId(null);
      setIsSystemAdmin(false);
      setNotebooks([]);
      setTenantScope(null);
      setScopeError(scopeOperation.current ? "TENANT_SWITCH_UNCERTAIN" : "TENANT_SCOPE_FAILED");
      setErrorCode(SAFE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_UNAVAILABLE");
      setState("error");
      reveal();
    }
  }, [conceal, getNotebooks, getSession, navigate, reveal]);

  useEffect(() => {
    const invalidate = () => {
      ++loadEpoch.current;
      loadController.current?.abort();
      conceal();
      setNotebooks([]);
      setWorkspaceId(null);
      setTenantScope(null);
    };
    const revalidate = () => { void load(); };
    const onPageShow = () => revalidate();
    const onPageHide = () => invalidate();
    const onVisible = () => { if (document.visibilityState === "visible") revalidate(); else invalidate(); };
    void load();
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("popstate", revalidate);
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      ++loadEpoch.current;
      loadController.current?.abort();
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("popstate", revalidate);
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [conceal, load]);

  const handleCreate = async (input) => {
    const epoch = loadEpoch.current;
    const source = currentSession.current;
    if (!workspaceId || !source || source.workspace_id !== workspaceId || scopeOperation.current) throw new Error("NOTEBOOK_UNAVAILABLE");
    const result = await createNotebook(workspaceId, input, { idempotencyKey: notebookOperationKey("notebook") });
    // 목록 변경과 호출자의 자동 열기는 같은 검증 뒤에만 허용한다. 서버 저장의 소급 취소는 아니다.
    if (epoch !== loadEpoch.current || !sameSession(source, currentSession.current)) throw new Error("SESSION_RESPONSE_INVALID");
    const verified = await getSession();
    if (epoch !== loadEpoch.current || !sameSession(source, currentSession.current)
      || !sameSession(source, verified) || verified.password_change_required) {
      if (epoch === loadEpoch.current) {
        conceal();
        setNotebooks([]);
        setWorkspaceId(null);
        void load();
      }
      throw new Error("SESSION_RESPONSE_INVALID");
    }
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
    if (scopeOperation.current || !currentSession.current) return;
    const operation = { phase: "checking", source: currentSession.current, target: tenantId };
    scopeOperation.current = operation;
    const epoch = loadEpoch.current;
    setSwitchingScope(true);
    setScopeError(null);
    try {
      const session = await getSession();
      if (epoch !== loadEpoch.current) return;
      if (!sameSession(session, operation.source)) {
        scopeOperation.current = null;
        await load();
        return;
      }
      operation.phase = "sending";
      const result = await switchSessionTenant(operation.target);
      if (scopeOperation.current !== operation) return;
      if (result.user_id !== operation.source.user_id) throw new Error("SESSION_RESPONSE_INVALID");
      const wasVisible = protectedRoot.current?.getAttribute("data-session-validated") === "true";
      const stale = epoch !== loadEpoch.current;
      scopeOperation.current = null;
      ++loadEpoch.current;
      loadController.current?.abort();
      conceal();
      setNotebooks([]);
      setWorkspaceId(null);
      setTenantScope(null);
      if (stale && !wasVisible) return;
      window.location.replace("/notebooks");
    } catch (error) {
      if (scopeOperation.current !== operation) return;
      if (["TENANT_SCOPE_403", "TENANT_SCOPE_404", "TENANT_SCOPE_422"].includes(error?.message)) {
        scopeOperation.current = null;
        setScopeError(error.message);
        return;
      }
      if (operation.phase === "sending") operation.phase = "unknown";
      else scopeOperation.current = null;
      if (epoch !== loadEpoch.current) {
        if (operation.phase === "unknown" && sameSession(currentSession.current, operation.source)) {
          setScopeError("TENANT_SWITCH_UNCERTAIN");
        }
        return;
      }
      setScopeError(operation.phase === "unknown" ? "TENANT_SWITCH_UNCERTAIN" : "TENANT_SCOPE_FAILED");
      await load();
    } finally {
      if (scopeOperation.current === operation && operation.phase === "checking") scopeOperation.current = null;
      setSwitchingScope(Boolean(scopeOperation.current));
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
