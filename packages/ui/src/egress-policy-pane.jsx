"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { createEgressPolicyDraft, egressPolicyReducer } from "./egress-policy-model.js";

export function EgressPolicyPane(props) {
  const identity = props.organizationId && props.workspaceId
    ? JSON.stringify([props.organizationId, props.workspaceId]) : "session-resolved";
  return <EgressPolicyPaneInner key={identity} {...props} />;
}

export function EgressPolicyPaneInner({ organizationId, workspaceId, adapter }) {
  const [state, dispatch] = useReducer(
    egressPolicyReducer, undefined, () => egressPolicyReducer(undefined, { type: "init" }),
  );
  const passwordRef = useRef(null);
  const [context, setContext] = useState({ organizationId, workspaceId });
  const [activeScope, setActiveScope] = useState("organization");
  const [passwordPresent, setPasswordPresent] = useState(false);
  const mountedRef = useRef(false);
  const epochRef = useRef(0);
  const abortRef = useRef(null);
  const contextRef = useRef({ organizationId, workspaceId });
  const scopeRef = useRef("organization");
  function beginOperation() {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    return { epoch: ++epochRef.current, signal: abortRef.current.signal };
  }
  function isCurrent(snapshot) {
    return mountedRef.current && epochRef.current === snapshot.epoch
      && scopeRef.current === snapshot.scope
      && contextRef.current.organizationId === snapshot.context.organizationId
      && contextRef.current.workspaceId === snapshot.context.workspaceId;
  }
  async function loadPolicy(initialContext, scope, operation) {
    dispatch({ type: "loading" });
    try {
      let active = initialContext;
      if (!active.organizationId || !active.workspaceId) {
        const resolved = await adapter.loadContext({ signal: operation.signal });
        if (!mountedRef.current || epochRef.current !== operation.epoch || scopeRef.current !== scope) return;
        active = {
          organizationId: resolved.data.organization_id,
          workspaceId: resolved.data.workspace_id,
        };
        contextRef.current = active;
        setContext(active);
      }
      const snapshot = { epoch: operation.epoch, context: active, scope };
      const view = await adapter.load({ workspaceId: active.workspaceId, signal: operation.signal });
      if (!isCurrent(snapshot)) return;
      dispatch({ type: "loaded", data: { ...view.data, editable_scope: scope, etag: view.etag } });
    } catch (error) {
      const snapshot = { epoch: operation.epoch, context: contextRef.current, scope };
      if (error?.name !== "AbortError" && isCurrent(snapshot)) {
        dispatch({ type: "failed", code: error.message });
      }
    }
  }
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      epochRef.current += 1;
      abortRef.current?.abort();
    };
  }, []);
  useEffect(() => {
    const nextContext = { organizationId, workspaceId };
    contextRef.current = nextContext;
    scopeRef.current = "organization";
    setContext(nextContext);
    setActiveScope("organization");
    dispatch({ type: "context_loading" });
    const operation = beginOperation();
    void loadPolicy(nextContext, "organization", operation);
    return () => operation.signal.aborted || abortRef.current?.abort();
  }, [organizationId, workspaceId, adapter]);
  function refresh() {
    const operation = beginOperation();
    void loadPolicy(contextRef.current, scopeRef.current, operation);
  }
  function selectScope(scope) {
    if (state.status === "saving") return;
    if (passwordRef.current) passwordRef.current.value = "";
    setPasswordPresent(false);
    beginOperation();
    scopeRef.current = scope;
    setActiveScope(scope);
    dispatch({ type: "loaded", data: { ...state.effective, editable_scope: scope } });
  }
  async function save(event) {
    event.preventDefault();
    const passwordElement = passwordRef.current;
    const currentPassword = passwordElement?.value || "";
    if (!state.canSave || state.status === "saving" || !currentPassword) return;
    const sensitive = { currentPassword, stepUpAuthorization: null };
    const operation = beginOperation();
    const snapshot = { epoch: operation.epoch, context: { ...contextRef.current }, scope: scopeRef.current };
    dispatch({ type: "saving" });
    try {
      const savePolicy = snapshot.scope === "organization"
        ? adapter.saveOrganization : adapter.saveWorkspace;
      await savePolicy({
        organizationId: snapshot.context.organizationId, workspaceId: snapshot.context.workspaceId,
        etag: snapshot.scope === "organization"
          ? state.effective.organization_etag : state.effective.workspace_etag,
        idempotencyKey: crypto.randomUUID(), draft: state.draft, sensitive,
        signal: operation.signal,
      });
      if (!isCurrent(snapshot)) return;
      const view = await adapter.load({ workspaceId: snapshot.context.workspaceId, signal: operation.signal });
      if (!isCurrent(snapshot)) return;
      dispatch({ type: "loaded", data: { ...view.data, editable_scope: snapshot.scope, etag: view.etag } });
    } catch (error) {
      if (error?.name !== "AbortError" && isCurrent(snapshot)) {
        dispatch({ type: "failed", code: error.message });
      }
    } finally {
      if (passwordElement) passwordElement.value = "";
      setPasswordPresent(false);
    }
  }
  if (!state.effective && state.errorCode) return <section className="egress-policy-pane"><p role="alert">정책을 불러오지 못했습니다. ({state.errorCode})</p><button type="button" onClick={refresh}>다시 시도</button></section>;
  if (!state.effective) return <p role="status">Egress 정책을 불러오는 중입니다.</p>;
  return <section className="egress-policy-pane" aria-labelledby="egress-policy-title">
    <header><h1 id="egress-policy-title">외부 전송 정책</h1><button type="button" className="info-button" title="조직 정책은 Workspace보다 우선하며 완화할 수 없습니다." aria-label="외부 전송 정책 설명">i</button></header>
    <p role="status">최종 effective 정책: {state.effective.mode === "deny_external" ? "외부 전송 차단" : "승인된 외부 전송 허용"}</p>
    {state.effective.parent_locked ? <p className="policy-lock" role="note">조직 차단 정책이 적용되어 Workspace에서 완화할 수 없습니다.</p> : null}
    <nav aria-label="정책 적용 단계">
      <button type="button" disabled={state.status === "saving"} aria-pressed={activeScope === "organization"} onClick={() => selectScope("organization")}>1. 조직 정책</button>
      <button type="button" disabled={state.status === "saving"} aria-pressed={activeScope === "workspace"} onClick={() => selectScope("workspace")}>2. Workspace 정책</button>
    </nav>
    <p>{activeScope === "organization" ? "조직 정책을 별도로 저장합니다." : "Workspace 정책을 별도로 저장합니다."}</p>
    <form key={JSON.stringify([context.organizationId, context.workspaceId, activeScope])} onSubmit={save}>
      <label>정책 모드<select value={state.draft.mode} onChange={(event) => dispatch({ type: "drafted", draft: createEgressPolicyDraft({ ...state.draft, mode: event.target.value }) })}>
        <option value="deny_external">외부 전송 차단</option><option value="allow_approved_external" disabled={state.effective.parent_locked && state.effective.editable_scope !== "organization"}>승인된 외부 전송 허용</option>
      </select></label>
      <label>최대 전송 bytes<input type="number" min="0" max="104857600" value={state.draft.max_bytes} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, max_bytes: Number(event.target.value) } })} /></label>
      <label>허용 Provider 종류<input value={state.draft.allowed_provider_kinds.join(",")} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, allowed_provider_kinds: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) } })} placeholder="external_api,server_internal,local_runtime" /></label>
      <label>허용 목적지<input value={state.draft.allowed_destinations.join(",")} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, allowed_destinations: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) } })} /></label>
      <label>분류<select value={state.draft.classification} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, classification: event.target.value } })}><option value="public">public</option><option value="internal">internal</option><option value="confidential">confidential</option><option value="restricted">restricted</option></select></label>
      <label><input type="checkbox" checked={state.draft.masking_required} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, masking_required: event.target.checked } })} />마스킹 필수</label>
      <label><input type="checkbox" checked={state.draft.redaction_required} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, redaction_required: event.target.checked } })} />삭제 처리 필수</label>
      <label>필수 승인자<select value={state.draft.required_approver} onChange={(event) => dispatch({ type: "drafted", draft: { ...state.draft, required_approver: event.target.value } })}><option value="workspace_manager">Workspace 관리자</option><option value="organization_admin">조직 관리자</option></select></label>
      <label>현재 비밀번호<input ref={passwordRef} type="password" autoComplete="current-password" required onInput={(event) => setPasswordPresent(Boolean(event.currentTarget.value))} /></label>
      <button type="submit" disabled={!state.canSave || !passwordPresent || state.status === "saving"}>정책 저장</button>
    </form>
    {state.errorCode ? <p role="alert">정책을 저장하지 못했습니다. ({state.errorCode})</p> : null}
  </section>;
}
