"use client";

import { useState } from "react";
import {
  OUTPUT_TYPES, canSubmitGeneration, confirmGenerationSettings, createStudioGenerationInput,
  mergeStudioVersion, selectOutputType, updateGenerationSettings,
} from "./product-studio-model.js";

function studioContentText(content) {
  if (content == null) return "";
  if (typeof content === "string" || typeof content === "number" || typeof content === "boolean") return String(content);
  if (Array.isArray(content)) return content.map(studioContentText).filter(Boolean).join("\n");
  return Object.values(content).map(studioContentText).filter(Boolean).join("\n");
}

export function ProductStudioPane({ state, adapter }) {
  const [view, setView] = useState(state);
  const [revisionContent, setRevisionContent] = useState("");
  const [changeReason, setChangeReason] = useState("");
  const [stepUpPassword, setStepUpPassword] = useState("");
  const selected = OUTPUT_TYPES.find((item) => item.id === view.selectedOutputType);
  const selectedOutput = view.outputs.find((output) => output.studio_output_id === view.selectedOutputId) ?? null;
  const setField = (field, value) => setView((current) => updateGenerationSettings(current, { [field]: value }));
  const submit = async () => {
    if (!adapter?.createGeneration || !canSubmitGeneration(view)) return;
    setView((current) => ({ ...current, pending: true, safeError: null }));
    try {
      const output = await adapter.createGeneration(createStudioGenerationInput(view));
      setView((current) => ({ ...current, pending: false, outputs: [output, ...current.outputs], selectedOutputId: output.studio_output_id }));
    } catch (error) {
      setView((current) => ({ ...current, pending: false, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_CREATE_FAILED" }));
    }
  };
  const updateSelectedOutput = (patch) => setView((current) => ({
    ...current,
    outputs: current.outputs.map((output) => output.studio_output_id === current.selectedOutputId ? { ...output, ...patch } : output),
  }));
  const revise = async (revisionType = "user_edit") => {
    if (!selectedOutput || !adapter?.createStudioVersion || !revisionContent.trim() || !changeReason.trim()) return;
    setView((current) => ({ ...current, pending: true, safeError: null }));
    try {
      const version = await adapter.createStudioVersion(selectedOutput.studio_output_id, {
        previous_version_id: selectedOutput.output_version_id, revision_type: revisionType,
        change_reason: changeReason, content: revisionContent,
        ...(revisionType === "settings_change" ? { settings: {
          purpose: view.settings.purpose, audience: view.settings.audience,
          source_version_ids: view.settings.sourceVersionIds, ruleset_version_id: view.settings.rulesetVersionId ?? null,
          length: view.settings.length, structure: view.settings.structure, output_format: view.settings.outputFormat,
          review_condition: view.settings.reviewCondition,
        } } : {}),
      });
      setView((current) => ({ ...current, outputs: current.outputs.map((output) => output.studio_output_id === current.selectedOutputId ? mergeStudioVersion(output, { ...version, approval_required: true }) : output) }));
      setRevisionContent(""); setChangeReason("");
      setView((current) => ({ ...current, pending: false }));
    } catch (error) {
      setView((current) => ({ ...current, pending: false, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_VERSION_FAILED" }));
    }
  };
  const act = async (action, payload = {}) => {
    if (!selectedOutput || !adapter?.createStudioAction) return;
    setView((current) => ({ ...current, pending: true, safeError: null }));
    try {
      const stepUpGroup = { approvals: "final_approval_or_knowledge_registration", deliveries: "external_transfer", "knowledge-registrations": "final_approval_or_knowledge_registration" }[action];
      const currentPassword = stepUpPassword;
      if (stepUpGroup) setStepUpPassword("");
      const step_up_authorization = stepUpGroup
        ? await adapter.issueStudioStepUp(stepUpGroup, selectedOutput.output_version_id, currentPassword)
        : undefined;
      const result = await adapter.createStudioAction(action, { output_version_id: selectedOutput.output_version_id, ...payload, ...(step_up_authorization ? { step_up_authorization } : {}) });
      const linkField = { reviews: "review_request_id", "approval-requests": "approval_request_id", approvals: "approval_id" }[action];
      const lifecycleStatus = {
        reviews: "in_review", "approval-requests": "in_review",
        approvals: payload.decision === "rejected" ? "revision_requested" : "approved",
        deliveries: "delivered",
      }[action];
      updateSelectedOutput({ ...(linkField ? { [linkField]: result.record_id } : {}), status: lifecycleStatus ?? selectedOutput.status });
      setView((current) => ({ ...current, pending: false }));
    } catch (error) {
      setView((current) => ({ ...current, pending: false, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_ACTION_FAILED" }));
    }
  };
  const download = async () => {
    if (!selectedOutput || !adapter?.downloadStudioExport) return;
    try {
      const format = selectedOutput.version?.output_format ?? selectedOutput.output_format ?? "pdf";
      const result = await adapter.downloadStudioExport(selectedOutput.studio_output_id, selectedOutput.output_version_id, format);
      const url = URL.createObjectURL(new Blob([Uint8Array.from(result.bytes)], { type: result.contentType ?? "application/octet-stream" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `studio-${selectedOutput.output_version_id}.${format}`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      setView((current) => ({ ...current, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_EXPORT_FAILED" }));
    }
  };
  return (
    <div className="product-studio" data-studio-state={view.status}>
      <div className="studio-type-tiles" aria-label="산출물 유형">
        {OUTPUT_TYPES.map((type) => <button key={type.id} type="button" onClick={() => setView(selectOutputType(view, type.id))}>{type.label}</button>)}
      </div>
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <label>목적<input value={view.settings.purpose ?? ""} onChange={(event) => setField("purpose", event.currentTarget.value)} /></label>
        <label>독자<input value={view.settings.audience ?? ""} onChange={(event) => setField("audience", event.currentTarget.value)} /></label>
        <label>분량<input value={view.settings.length ?? ""} onChange={(event) => setField("length", event.currentTarget.value)} /></label>
        <label>구성<input value={view.settings.structure ?? ""} onChange={(event) => setField("structure", event.currentTarget.value)} /></label>
        <label>출력 형식<select value={view.settings.outputFormat ?? ""} onChange={(event) => setField("outputFormat", event.currentTarget.value)}>
          <option value="">선택</option>{selected?.formats.map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}
        </select></label>
        <label>검토 조건<input value={view.settings.reviewCondition ?? ""} onChange={(event) => setField("reviewCondition", event.currentTarget.value)} /></label>
        {view.locks.map((lock) => <p key={lock.field} className="studio-lock" role="status">잠금: {lock.field} · {lock.reason}</p>)}
        <button type="button" onClick={() => setView(confirmGenerationSettings(view))} disabled={!view.selectedOutputType}>설정 확인</button>
        <button type="submit" disabled={!canSubmitGeneration(view)}>명시 생성</button>
      </form>
      <section aria-labelledby="stored-studio-outputs"><h3 id="stored-studio-outputs">저장된 산출물</h3>
        <ul>{view.outputs.map((output) => <li key={output.studio_output_id}><button type="button" onClick={() => setView((current) => ({ ...current, selectedOutputId: output.studio_output_id }))}>{output.title}</button><span>{output.status}</span></li>)}</ul>
      </section>
      {selectedOutput ? <section aria-label="선택 산출물 상세">
        <h3>{selectedOutput.title}</h3>
        <p className="studio-output-content">{studioContentText(selectedOutput.content ?? selectedOutput.version?.content)}</p>
        <p>Version {selectedOutput.output_version_id} · 근거 {selectedOutput.citations ?? 0} · 설정 {selectedOutput.settings_snapshot_id ?? "-"}</p>
        <label>변경 사유<input value={changeReason} onChange={(event) => setChangeReason(event.currentTarget.value)} /></label>
        <label>편집 내용<textarea value={revisionContent} onChange={(event) => setRevisionContent(event.currentTarget.value)} /></label>
        <button type="button" disabled={view.pending || !changeReason.trim() || !revisionContent.trim()} onClick={() => void revise("user_edit")}>편집 새 Version</button>
        <button type="button" disabled={view.pending || !changeReason.trim() || !revisionContent.trim()} onClick={() => void revise("ai_regeneration")}>AI 재생성 새 Version</button>
        <button type="button" disabled={view.pending || !changeReason.trim() || !revisionContent.trim()} onClick={() => void revise("settings_change")}>설정 변경 새 Version</button>
        <label>추가 인증 비밀번호<input type="password" autoComplete="current-password" value={stepUpPassword} onChange={(event) => setStepUpPassword(event.currentTarget.value)} /></label>
        <button type="button" disabled={view.pending} onClick={() => void act("reviews")}>검토 요청</button>
        <button type="button" disabled={view.pending || !selectedOutput.review_request_id} onClick={() => void act("approval-requests", { review_request_id: selectedOutput.review_request_id })}>승인 요청</button>
        <button type="button" disabled={view.pending || !selectedOutput.approval_request_id || !stepUpPassword} onClick={() => void act("approvals", { approval_request_id: selectedOutput.approval_request_id, decision: "approved" })}>승인</button>
        <button type="button" disabled={view.pending || !selectedOutput.approval_request_id || !stepUpPassword} onClick={() => void act("approvals", { approval_request_id: selectedOutput.approval_request_id, decision: "rejected" })}>수정 요청</button>
        <button type="button" disabled={view.pending || selectedOutput.status !== "approved"} onClick={() => void download()}>내보내기</button>
        <button type="button" disabled={view.pending || selectedOutput.status !== "approved" || !selectedOutput.approval_id || !stepUpPassword} onClick={() => void act("deliveries", { approval_id: selectedOutput.approval_id, recipient: "workspace_recipient" })}>전달</button>
        <button type="button" disabled={view.pending || selectedOutput.status !== "approved" || !stepUpPassword} onClick={() => void act("knowledge-registrations", { explicit: true })}>생산 지식 등록</button>
      </section> : null}
      {view.safeError ? <p role="alert">{view.safeError}</p> : null}
    </div>
  );
}
