"use client";

import { useState } from "react";
import {
  OUTPUT_TYPES, canSubmitGeneration, confirmGenerationSettings, createStudioGenerationInput,
  mergeStudioVersion, selectOutputType, updateGenerationSettings,
} from "./product-studio-model.js";

const FUTURE_OUTPUT_TYPES = Object.freeze([
  Object.freeze({ id: "slides", label: "슬라이드", phase: "Daon 2.5" }),
  Object.freeze({ id: "infographic", label: "인포그래픽", phase: "Daon 2.5" }),
  Object.freeze({ id: "flashcards", label: "플래시카드", phase: "Daon 2.5" }),
  Object.freeze({ id: "quiz", label: "퀴즈", phase: "Daon 2.5" }),
  Object.freeze({ id: "audio", label: "AI 오디오", phase: "Daon 3" }),
  Object.freeze({ id: "video", label: "동영상", phase: "Daon 3" }),
]);

const TYPE_ICONS = Object.freeze({
  evidence_report: "document", compliance_checklist: "check", comparison_table: "table",
  knowledge_map: "map", business_draft: "draft", slides: "slides", infographic: "image",
  flashcards: "cards", quiz: "quiz", audio: "audio", video: "video",
});

function outputStatusLabel(status) {
  return ({
    draft: "초안", in_review: "검토 중", approved: "승인됨", delivered: "전달됨",
    revision_requested: "수정 필요", generating: "생성 중", failed: "생성 실패",
  })[status] ?? "저장됨";
}

function safeStudioErrorMessage(code) {
  if (!code) return null;
  return ({
    STUDIO_CREATE_FAILED: "산출물을 생성하지 못했습니다. 설정과 연결 상태를 확인해 주세요.",
    STUDIO_LIST_FAILED: "저장된 산출물을 불러오지 못했습니다. 운영상태에서 연결을 확인해 주세요.",
    STUDIO_VERSION_FAILED: "새 버전을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    STUDIO_ACTION_FAILED: "요청을 처리하지 못했습니다. 현재 상태를 확인해 주세요.",
    STUDIO_EXPORT_FAILED: "파일을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  })[code] ?? "요청을 안전하게 완료하지 못했습니다. 운영상태에서 연결 상태를 확인해 주세요.";
}

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
  const generationState = view.pending ? "pending" : view.safeError ? "failed" : selectedOutput ? "completed" : "idle";
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
      {view.safeError && !view.selectedOutputType ? <p className="inline-alert" role="alert">{safeStudioErrorMessage(view.safeError)}</p> : null}
      {!view.selectedOutputType ? <div className="studio-home" data-studio-view="home">
        <div className="studio-type-tiles" data-columns="3" aria-label="산출물 유형">
          {OUTPUT_TYPES.map((type) => <button className={`studio-type-tile tile-${TYPE_ICONS[type.id]}`} key={type.id} type="button" onClick={() => setView(selectOutputType(view, type.id))}>{type.label}</button>)}
          {FUTURE_OUTPUT_TYPES.map((type) => <button className={`studio-type-tile studio-type-future tile-${TYPE_ICONS[type.id]}`} key={type.id} type="button" disabled aria-label={`${type.label}, ${type.phase} 준비 중`}><span>{type.label}</span><small>{type.phase} · 준비 중</small></button>)}
        </div>
        <section className="studio-library" aria-labelledby="stored-studio-outputs"><div className="studio-section-heading"><div><span className="section-kicker">LIBRARY</span><h3 id="stored-studio-outputs">저장된 산출물</h3></div><span className="library-count">{view.outputs.length}</span></div>
          {view.outputs.length ? <ul>{view.outputs.map((output) => <li className="studio-library-row" key={output.studio_output_id}><span className="library-type-icon" aria-hidden="true" /><span className="library-row-copy"><button type="button" onClick={() => setView((current) => ({ ...current, selectedOutputId: output.studio_output_id }))}>{output.title}</button><small>Source {output.source_count ?? output.source_version_ids?.length ?? 1} · Version {output.output_version_id ?? "-"}</small></span><span className={`output-status status-${output.status ?? "saved"}`}>{outputStatusLabel(output.status)}</span></li>)}</ul> : <div className="studio-empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>아직 저장된 산출물이 없습니다.</strong><small>위 유형을 선택하면 근거가 결속된 산출물을 만들 수 있습니다.</small></div>}
        </section>
      </div> : <section className="studio-config-view" data-studio-view="config" aria-labelledby="studio-config-title">
        <header className="studio-config-header"><button className="studio-back" type="button" onClick={() => setView((current) => ({ ...current, selectedOutputType: null, settingsConfirmed: false, settingsSnapshot: null }))}>뒤로</button><div><span className="section-kicker">CREATE</span><h3 id="studio-config-title">{selected?.label} 설정</h3></div></header>
        <div className={`generation-state generation-${generationState}`} data-generation-state={generationState} role={generationState === "failed" ? "alert" : "status"}>
          {generationState === "pending" ? <><span className="state-spinner" aria-hidden="true" /><span><strong>산출물을 생성하고 있습니다.</strong><small>현재 설정과 근거 Snapshot을 안전하게 결속하는 중입니다.</small></span></> : null}
          {generationState === "failed" ? <span><strong>생성을 완료하지 못했습니다.</strong><small>{safeStudioErrorMessage(view.safeError)}</small></span> : null}
          {generationState === "completed" ? <span><strong>산출물이 저장되었습니다.</strong><small>Library에서 상세와 Version 계보를 확인할 수 있습니다.</small></span> : null}
          {generationState === "idle" ? <span><strong>설정을 확인해 주세요.</strong><small>생성 전 목적과 검토 조건을 한 번 더 확인합니다.</small></span> : null}
        </div>
        <form className="studio-config-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <div className="studio-form-grid">
            <label>목적<input placeholder="이 산출물로 해결할 업무" value={view.settings.purpose ?? ""} onChange={(event) => setField("purpose", event.currentTarget.value)} /></label>
            <label>독자<input placeholder="결과를 검토하거나 사용할 사람" value={view.settings.audience ?? ""} onChange={(event) => setField("audience", event.currentTarget.value)} /></label>
            <label>분량<select value={view.settings.length ?? ""} onChange={(event) => setField("length", event.currentTarget.value)}><option value="">선택</option><option value="short">짧게</option><option value="standard">표준</option><option value="long">상세</option></select></label>
            <label>구성<select value={view.settings.structure ?? ""} onChange={(event) => setField("structure", event.currentTarget.value)}><option value="">선택</option><option value="summary-body-conclusion">요약 · 본문 · 결론</option><option value="executive-summary">핵심 요약 중심</option><option value="letter">업무 문서형</option></select></label>
            <label>출력 형식<select value={view.settings.outputFormat ?? ""} onChange={(event) => setField("outputFormat", event.currentTarget.value)}><option value="">선택</option>{selected?.formats.map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></label>
            <label>검토 조건<select value={view.settings.reviewCondition ?? ""} onChange={(event) => setField("reviewCondition", event.currentTarget.value)}><option value="">선택</option><option value="review_required">검토 필수</option><option value="approval_required">승인 필수</option></select></label>
          </div>
          <div className="studio-setting-summary"><div><span>현재 모델</span><strong>{view.modelLabel ?? "Workspace 기본 모델"}</strong><small>{adapter?.createGeneration ? "생성 시 현재 선택을 검증합니다." : "모델 연결 상태를 확인해 주세요."}</small></div><div><span>정책 요약</span><strong>{view.locks.length ? `조직 정책 ${view.locks.length}개 적용` : "Workspace 정책 적용"}</strong><small>정책은 생성 시점에 다시 확인되며 이 화면에서 해제할 수 없습니다.</small></div></div>
          <div className="studio-config-actions"><button className="secondary-button" type="button" onClick={() => setView(confirmGenerationSettings(view))} disabled={!view.selectedOutputType}>설정 확인</button><button className="primary-button" type="submit" disabled={!canSubmitGeneration(view)}>생성</button></div>
        </form>
        <div className="studio-config-library"><span className="section-kicker">LIBRARY</span><h3>저장된 산출물</h3><small>{view.outputs.length ? `${view.outputs.length}개의 산출물이 저장되어 있습니다.` : "생성 완료 후 여기에 저장됩니다."}</small></div>
      </section>}
      {selectedOutput ? <section className="studio-output-detail" aria-label="선택 산출물 상세">
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
    </div>
  );
}
