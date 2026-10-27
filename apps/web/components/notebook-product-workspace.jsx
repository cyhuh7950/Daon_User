"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createNotebookContextWorkspaceAdapter } from "@daon-user/ui/notebook-context-adapter";
import { ActualWorkspace, createWebProductWorkspaceAdapter } from "./actual-workspace.jsx";
import { getCurrentNotebookSession, getNotebook, getNotebookContext } from "../lib/notebook-api.js";
import { logoutCurrentSession } from "../lib/auth-api.js";
import { getStudioGenerationJob, listWorkspaceSources, listProductStudioOutputs, listStudioVersions } from "../lib/product-workspace-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const SAFE_ERRORS = new Set(["NOTEBOOK_NOT_FOUND", "NOTEBOOK_UNAVAILABLE", "NOTEBOOK_CONTEXT_INVALID"]);
const replaceLocation = (path) => window.location.replace(path);
const locationSearch = () => window.location.search || "";
const SESSION_FIELDS = ["session_id", "user_id", "tenant_id", "workspace_id"];
const sameSession = (first, current) => SESSION_FIELDS.every((key) =>
  typeof first?.[key] === "string" && SAFE_ID.test(first[key]) && !/\s/u.test(first[key]) && first[key] === current?.[key]);

function notificationSelection(search) {
  const params = new URLSearchParams(search);
  const entries = [...params];
  if (!entries.length) return {};
  const keys = {run_id:"runId",studio_job_id:"studioJobId",source_version_id:"sourceVersionId",output_version_id:"outputVersionId"};
  if (entries.length !== 1 || !Object.hasOwn(keys, entries[0][0])
    || !SAFE_ID.test(entries[0][1]) || /\s/u.test(entries[0][1])) throw new Error("NOTEBOOK_CONTEXT_INVALID");
  return { [keys[entries[0][0]]]:entries[0][1] };
}

export function NotebookProductWorkspace({
  notebookId,
  getSession = getCurrentNotebookSession,
  getSelectedNotebook = getNotebook,
  getSelectedContext = getNotebookContext,
  getSelectedJob = getStudioGenerationJob,
  getLocationSearch = locationSearch,
  getTargetSources = listWorkspaceSources,
  getTargetOutputs = listProductStudioOutputs,
  getTargetVersions = listStudioVersions,
  createWorkspaceAdapter = createWebProductWorkspaceAdapter,
  navigate = replaceLocation,
}) {
  const logoutPending = useRef(false);
  const requestEpoch = useRef(0);
  const protectedRoot = useRef(null);
  const validatedView = useRef(null);
  const [sessionValidated, setSessionValidated] = useState(false);
  const [view, setView] = useState({ state: "loading", workspaceId: null, notebook: null, context: null, error: null });

  const conceal = useCallback(() => {
    concealProtectedRoute(protectedRoot.current);
    setSessionValidated(false);
  }, []);
  const reveal = useCallback(() => {
    revealProtectedRoute(protectedRoot.current);
    setSessionValidated(true);
  }, []);

  const load = useCallback(async (signal) => {
    const epoch = ++requestEpoch.current;
    const active = () => !signal?.aborted && requestEpoch.current === epoch;
    conceal();
    validatedView.current = null;
    if (!SAFE_ID.test(notebookId || "")) {
      setView({ state: "error", workspaceId: null, context: null, error: "NOTEBOOK_NOT_FOUND" });
      reveal();
      return;
    }
    setView((current) => ({ ...current, state: "loading", error: null }));
    try {
      const search = getLocationSearch();
      const selection = notificationSelection(search);
      const session = await getSession({ signal });
      if (!active()) return;
      if (session.password_change_required) {
        navigate("/password-change");
        return;
      }
      if (!sameSession(session, session)) throw new Error("NOTEBOOK_UNAVAILABLE");
      const notebook = await getSelectedNotebook(session.workspace_id, notebookId, { signal });
      if (!active()) return;
      let selected, notice = null, initialStudioOutput = null;
      const contextSelection = selection.runId ? {runId:selection.runId}
        : selection.studioJobId ? {studioJobId:selection.studioJobId} : {};
      try {
        selected = await getSelectedContext(session.workspace_id, notebookId, { signal, ...contextSelection });
      } catch (error) {
        if (!active()) return;
        if (!selection.runId || error?.message !== "QUESTION_FAILED") throw error;
        // 실패를 검증한 뒤에만 기존 정상 대화를 별도로 불러온다. 대상 선택 성공으로 가장하지 않는다.
        notice = "선택한 질문 처리가 실패했습니다. 새 질문으로 다시 요청해 주세요.";
        selected = await getSelectedContext(session.workspace_id, notebookId, { signal });
      }
      if (!active()) return;
      if (!notice && selection.runId && selected.data?.conversation?.answer?.run_id !== selection.runId) throw new Error("NOTEBOOK_CONTEXT_INVALID");
      if (selection.studioJobId) {
        // 본인/Notebook/현재 자료 검증이 끝난 context 이후 기존 공유 GET 계약을 그대로 사용한다.
        const job = await getSelectedJob(session.workspace_id, selection.studioJobId, { notebookId, signal });
        if (!active()) return;
        const messages = { failed:"선택한 Studio 작업이 실패했습니다.", unavailable:"선택한 Studio 작업을 현재 처리할 수 없습니다.",
          queued:"선택한 Studio 작업이 대기 중입니다.", leased:"선택한 Studio 작업을 준비 중입니다.",
          generating:"선택한 Studio 작업을 생성 중입니다.", completed:"선택한 Studio 작업이 완료됐습니다." };
        if (job?.job_id !== selection.studioJobId || !Object.hasOwn(messages, job.status)) throw new Error("NOTEBOOK_CONTEXT_INVALID");
        notice = messages[job.status];
      }
      if (selection.sourceVersionId) {
        const bound = selected.data?.sources?.find(item=>item.source_version_id===selection.sourceVersionId);
        if (!bound) throw new Error("NOTEBOOK_NOT_FOUND");
        const sources = await getTargetSources(session.workspace_id, {notebookId,signal});
        if (!active()) return;
        const targets = Array.isArray(sources) ? sources.filter(item=>item.source_version_id===selection.sourceVersionId && item.source_id===bound.source_id) : [];
        if (targets.length!==1) throw new Error("NOTEBOOK_NOT_FOUND");
      }
      if (selection.outputVersionId) {
        if (!selected.data?.output_version_ids?.includes(selection.outputVersionId)) throw new Error("NOTEBOOK_NOT_FOUND");
        const result = await getTargetOutputs(session.workspace_id, {notebookId,signal});
        if (!active()) return;
        if (!Array.isArray(result?.outputs)) throw new Error("NOTEBOOK_CONTEXT_INVALID");
        for (const output of result.outputs) {
          if (!selected.data.studio_output_ids.includes(output.studio_output_id)) continue;
          const versions = await getTargetVersions(session.workspace_id,output.studio_output_id,{notebookId,signal});
          if (!active()) return;
          const matches = versions.filter(item=>item.output_version_id===selection.outputVersionId);
          if (matches.length>1) throw new Error("NOTEBOOK_CONTEXT_INVALID");
          if (matches.length===1) {
            initialStudioOutput = {...output,...matches[0],versions};
            break;
          }
        }
        if (!initialStudioOutput) throw new Error("NOTEBOOK_NOT_FOUND");
      }
      const currentSession = await getSession({ signal });
      if (!active()) return;
      if (!sameSession(session, currentSession) || currentSession.password_change_required) throw new Error("NOTEBOOK_UNAVAILABLE");
      if (active()) validatedView.current = { notebookId, search,
        session: Object.fromEntries(SESSION_FIELDS.map(key => [key, session[key]])) };
      if (active()) setView({
        state: "ready", workspaceId: session.workspace_id, notebook: notebook.data, context: selected.data,
        error: null, notice, initialSourceVersionId:selection.sourceVersionId ?? null, initialStudioOutput,
      });
      if (active()) reveal();
    } catch (error) {
      if (!active()) return;
      if (error?.message === "AUTHENTICATION_REQUIRED") {
        navigate("/");
        return;
      }
      setView({
        state: "error", workspaceId: null, notebook: null, context: null,
        error: SAFE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_UNAVAILABLE",
      });
      reveal();
    }
  }, [conceal, getSelectedContext, getSelectedNotebook, getSelectedJob, getLocationSearch, getTargetSources,
    getTargetOutputs, getTargetVersions, getSession, navigate, notebookId, reveal]);

  // 일반 창 복귀는 현재 접근권한만 재검사한다. 기존 context/adapter/본문은
  // hidden·inert 아래 유지하여 진행 중인 질문과 사용자 입력을 unmount하지 않는다.
  const revalidateCurrent = useCallback(async (signal) => {
    const prior = validatedView.current;
    if (!prior || prior.notebookId !== notebookId) {
      return load(signal);
    }
    const epoch = ++requestEpoch.current;
    const active = () => !signal?.aborted && requestEpoch.current === epoch;
    conceal();
    try {
      if (prior.search !== getLocationSearch()) return load(signal);
      const session = await getSession({ signal });
      if (!active()) return;
      if (session.password_change_required) {
        validatedView.current = null;
        setView({ state: "error", workspaceId: null, notebook: null, context: null, error: "NOTEBOOK_UNAVAILABLE" });
        navigate("/password-change");
        return;
      }
      if (!sameSession(prior.session, session)) throw new Error("NOTEBOOK_UNAVAILABLE");
      await getSelectedNotebook(session.workspace_id, notebookId, { signal });
      if (!active()) return;
      const currentSession = await getSession({ signal });
      if (!active()) return;
      if (!sameSession(prior.session, currentSession) || !sameSession(session, currentSession)
          || currentSession.password_change_required) throw new Error("NOTEBOOK_UNAVAILABLE");
      reveal();
    } catch (error) {
      if (!active()) return;
      validatedView.current = null;
      setView({ state: "error", workspaceId: null, notebook: null, context: null,
        error: SAFE_ERRORS.has(error?.message) ? error.message : "NOTEBOOK_UNAVAILABLE" });
      if (error?.message === "AUTHENTICATION_REQUIRED") { navigate("/"); return; }
      reveal();
    }
  }, [conceal, getLocationSearch, getSelectedNotebook, getSession, load, navigate, notebookId, reveal]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => { requestEpoch.current += 1; controller.abort(); };
  }, [load]);

  useEffect(() => {
    let controller = null;
    const request = (retain) => {
      conceal();
      controller?.abort();
      controller = new AbortController();
      void (retain ? revalidateCurrent(controller.signal) : load(controller.signal));
    };
    const concealCurrent = () => { requestEpoch.current += 1; controller?.abort(); conceal(); };
    const onPageShow = (event) => { if (event.persisted) request(false); };
    const onPageHide = () => { validatedView.current = null; concealCurrent(); };
    const onFocus = () => request(true);
    const onPopState = () => request(false);
    const onVisibility = () => { if (document.visibilityState === "hidden") concealCurrent(); else request(true); };
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("popstate", onPopState);
    window.addEventListener("blur", concealCurrent);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      controller?.abort();
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("blur", concealCurrent);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [conceal, load, revalidateCurrent]);

  const adapter = useMemo(() => view.state === "ready"
    ? createNotebookContextWorkspaceAdapter(
      createWorkspaceAdapter(view.workspaceId, view.context.notebook_id), view.context,
    )
    : null, [view, createWorkspaceAdapter]);

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
    {view.notice && <p className="notebook-route-state" role="status">{view.notice}</p>}
    <ActualWorkspace workspaceId={view.workspaceId} notebookId={view.context.notebook_id} notebookTitle={view.notebook?.title}
      initialSourceVersionId={view.initialSourceVersionId} initialStudioOutput={view.initialStudioOutput}
      adapter={adapter} onLogout={() => void handleLogout()} />
  </div>;
}
