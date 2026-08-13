"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { createEgressPolicyDraft, egressPolicyReducer } from "./egress-policy-model.js";

export function EgressPolicyPane({ organizationId, workspaceId, adapter }) {
  const [state, dispatch] = useReducer(
    egressPolicyReducer, undefined, () => egressPolicyReducer(undefined, { type: "init" }),
  );
  const passwordRef = useRef(null);
  const [context, setContext] = useState({ organizationId, workspaceId });
  async function refresh() {
    dispatch({ type: "loading" });
    try {
      let active = context;
      if (!active.organizationId || !active.workspaceId) {
        const resolved = await adapter.loadContext();
        active = {
          organizationId: resolved.data.organization_id,
          workspaceId: resolved.data.workspace_id,
        };
        setContext(active);
      }
      const view = await adapter.load({ workspaceId: active.workspaceId });
      dispatch({ type: "loaded", data: { ...view.data, editable_scope: "organization", etag: view.etag } });
    } catch (error) { dispatch({ type: "failed", code: error.message }); }
  }
  useEffect(() => { void refresh(); }, [workspaceId]);
  async function save(event) {
    event.preventDefault();
    const sensitive = { currentPassword: passwordRef.current?.value || "", stepUpAuthorization: null };
    dispatch({ type: "saving" });
    try {
      await adapter.save({ organizationId: context.organizationId, workspaceId: context.workspaceId, etag: state.effective.organization_etag,
        idempotencyKey: crypto.randomUUID(), draft: state.draft, sensitive });
      if (passwordRef.current) passwordRef.current.value = "";
      await refresh();
    } catch (error) {
      if (passwordRef.current) passwordRef.current.value = "";
      dispatch({ type: "failed", code: error.message });
    }
  }
  if (!state.effective && state.errorCode) return <section className="egress-policy-pane"><p role="alert">정책을 불러오지 못했습니다. ({state.errorCode})</p><button type="button" onClick={refresh}>다시 시도</button></section>;
  if (!state.effective) return <p role="status">Egress 정책을 불러오는 중입니다.</p>;
  return <section className="egress-policy-pane" aria-labelledby="egress-policy-title">
    <header><h1 id="egress-policy-title">외부 전송 정책</h1><button type="button" className="info-button" title="조직 정책은 Workspace보다 우선하며 완화할 수 없습니다." aria-label="외부 전송 정책 설명">i</button></header>
    <p role="status">현재 정책: {state.effective.mode === "deny_external" ? "외부 전송 차단" : "승인된 외부 전송 허용"}</p>
    {state.effective.parent_locked ? <p className="policy-lock" role="note">조직 차단 정책이 적용되어 Workspace에서 완화할 수 없습니다.</p> : null}
    <form onSubmit={save}>
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
      <label>현재 비밀번호<input ref={passwordRef} type="password" autoComplete="current-password" required /></label>
      <button type="submit" disabled={!state.canSave || state.status === "saving"}>정책 저장</button>
    </form>
    {state.errorCode ? <p role="alert">정책을 저장하지 못했습니다. ({state.errorCode})</p> : null}
  </section>;
}
