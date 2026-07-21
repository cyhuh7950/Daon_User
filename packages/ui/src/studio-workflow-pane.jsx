"use client";

import { useState } from "react";
import { MOBILE_STUDIO_ACTIONS, OUTPUT_TYPES, evaluateMobileAction, evaluateRoleAction } from "./studio-workflow-model.js";

function Help({ id, children }) {
  const [open, setOpen] = useState(false);
  const tooltipId = `studio-tooltip-${id}`;
  return <span className="info-control"><button className="icon-button" type="button" aria-label={`${id} 설명`} aria-expanded={open} aria-describedby={open ? tooltipId : undefined} onClick={() => setOpen((value) => !value)} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>i</button>{open && <span className="info-tooltip" id={tooltipId} role="tooltip">{children}</span>}</span>;
}

function Badge({ children, tone = "neutral" }) {
  return <span className={`studio-badge studio-badge-${tone}`}>{children}</span>;
}

function Step({ state, label }) {
  return <li data-state={state}><span aria-hidden="true">{state === "complete" ? "✓" : state === "current" ? "→" : "○"}</span>{label}</li>;
}

export function StudioWorkflowPane({ domainState, onStudioAction, onOpenEvidence }) {
  const state = domainState;
  const contract = OUTPUT_TYPES[state.selectedOutputType];
  const currentVersion = state.versions.at(-1);
  const approval = state.approvalRequests.at(-1);
  const requestRank = { configuring: 0, confirmed: 1, submitted: 2 }[state.request.status] ?? 0;
  const roles = ["editor", "reviewer", "approver", "viewer"];

  return (
    <section className="workspace-pane studio-pane" id="pane-studio" aria-labelledby="pane-studio-title">
      <div className="pane-heading"><div><p className="eyebrow">Production-bound Prototype</p><h2 id="pane-studio-title">업무 Studio</h2></div><Help id="Studio 업무 흐름">생성 설정부터 Version·검토·승인·전달·생산 지식 등록까지의 결정론적 Fixture입니다.</Help></div>

      <nav className="studio-tiles" aria-label="산출물 유형">
        {Object.entries(OUTPUT_TYPES).map(([id, item]) => <button key={id} type="button" aria-pressed={state.selectedOutputType === id} onClick={() => onStudioAction({ type: "select-output", outputType: id })}><strong>{item.label}</strong><small>{id}</small><span>{item.formats.join(" · ")}</span></button>)}
      </nav>

      <section className="studio-section" aria-labelledby="studio-settings-title">
        <div className="card-row"><h3 id="studio-settings-title">생성 설정</h3><Badge tone={state.request.status}>{state.request.status}</Badge></div>
        <ol className="studio-request-steps"><Step state={requestRank > 0 ? "complete" : "current"} label="configuring" /><Step state={requestRank > 1 ? "complete" : requestRank === 1 ? "current" : "pending"} label="confirmed" /><Step state={requestRank === 2 ? "current" : "pending"} label="submitted" /></ol>
        <div className="studio-settings-grid">
          <label>결과 목적<input value={state.settings.purpose} onChange={(event) => onStudioAction({ type: "update-setting", field: "purpose", value: event.target.value, reason: "목적 변경" })} /></label>
          <label>대상 독자<input value={state.settings.audience} onChange={(event) => onStudioAction({ type: "update-setting", field: "audience", value: event.target.value, reason: "독자 변경" })} /></label>
          <div><span>Source · SourceVersion · KnowledgeScope</span><strong>{state.settings.sourceSelection.sourceVersionIds.join(" · ")}</strong><small>{state.settings.sourceSelection.knowledgeScopeId}</small></div>
          <div><span>분량 · Section · 표 · 도식 · Template</span><strong>{state.settings.structure.length} · {state.settings.structure.sections.join(" / ")}</strong><small>{state.settings.structure.template} · 출처 {state.settings.structure.defaultSource}</small></div>
          <label>출력 형식<select value={state.settings.outputFormat} onChange={(event) => onStudioAction({ type: "update-setting", field: "outputFormat", value: event.target.value, reason: "출력 형식 변경" })}>{contract.formats.map((format) => <option key={format}>{format}</option>)}</select></label>
          <div><span>전문가 검토 조건</span><strong>사용자 선택 {state.settings.expertReview.requested ? "사용" : "미사용"} · 조직 필수</strong><small>🔒 {state.settings.expertReview.reason}</small></div>
        </div>
        <div className="studio-locks" aria-label="완화 불가 정책 잠금">
          <strong>🔒 정책 잠금 · 완화 불가</strong>
          <span>강제 RuleSet {state.settings.ruleSetBindings[0].versionId} · {state.settings.ruleSetBindings[0].reason}</span>
          <span>권위 {state.settings.authorityPriority}</span>
          <span>가중치 Clamp {state.settings.effectiveWeights[0].requested} → {state.settings.effectiveWeights[0].effective} · {state.settings.effectiveWeights[0].clampReason}</span>
          <span>데이터 영역 {state.settings.dataRegion} · Egress {state.settings.egressPolicy}</span>
          <button type="button" onClick={() => onStudioAction({ type: "update-setting", field: "egressPolicy", value: "unrestricted" })}>잠금 완화 시도</button>
        </div>
        <div className="studio-actions"><button type="button" onClick={() => onStudioAction({ type: "confirm" })}>설정 확정</button><button type="button" disabled={state.request.status !== "confirmed"} onClick={() => onStudioAction({ type: "submit" })}>명시 생성 제출</button></div>
        {state.snapshot && <details className="studio-snapshot" open><summary>GenerationSettingsSnapshot Preview · 불변</summary><dl className="version-snapshot"><div><dt>ID</dt><dd>{state.snapshot.id}</dd></div><div><dt>Actor · Workspace</dt><dd>{state.snapshot.actorId} · {state.snapshot.workspaceId}</dd></div><div><dt>SourceVersion</dt><dd>{state.snapshot.sourceVersionIds.join(" · ")}</dd></div><div><dt>RuleSetVersion</dt><dd>{state.snapshot.ruleSetVersionIds.join(" · ")}</dd></div><div><dt>정책 Version</dt><dd>{Object.values(state.snapshot.policyVersions).join(" · ")}</dd></div><div><dt>확정 시각</dt><dd>{state.snapshot.confirmedAt}</dd></div></dl><p>Run {state.request.runId ?? "제출 전"} · StudioOutput {state.request.studioOutputId ?? "제출 전"} · 최초 OutputVersion {currentVersion?.id ?? "생성 전"}</p></details>}
      </section>

      <section className="studio-section" aria-labelledby="studio-version-title">
        <div className="card-row"><h3 id="studio-version-title">편집 · Version · 근거</h3><Badge tone={currentVersion?.status ?? "empty"}>{currentVersion?.status ?? "Version 0건"}</Badge></div>
        {!currentVersion && <button type="button" onClick={() => onStudioAction({ type: "load-draft-fixture" })}>생성 결과 Fixture 열기</button>}
        {currentVersion && <>
          <div className="version-lineage"><strong>{currentVersion.id}</strong><span>← {currentVersion.previousVersionId ?? "최초"}</span><span>{currentVersion.revisionType} · {currentVersion.changeReason}</span></div>
          <p>{currentVersion.content.sections.join(" · ")} · 근거 {currentVersion.evidenceReferences[0].evidenceId} · 신뢰 {currentVersion.confidenceState}</p>
          <button id="evidence-trigger-studio" type="button" onClick={(event) => onOpenEvidence({ id: currentVersion.evidenceReferences[0].evidenceId, sourceId: "source-daon-guidance", sourceVersionId: currentVersion.evidenceReferences[0].sourceVersionId, name: "승인 운영 지침.pdf", position: currentVersion.evidenceReferences[0].position, excerpt: "승인된 기준선과 검증 증거를 함께 보존합니다.", kind: "document-region" }, event)}>Citation · M2-03 Evidence Viewer</button>
          <label>편집 Cursor<select value={state.cursor} onChange={(event) => onStudioAction({ type: "set-cursor", cursor: event.target.value })}><option value="section-2:paragraph-3">2절 · 3문단</option><option value="section-3:table-1">3절 · 표 1</option></select></label>
          <div className="studio-actions"><button type="button" onClick={() => onStudioAction({ type: "user-edit", reason: "사용자 문구 편집" })}>사용자 편집 · 새 Version</button><button type="button" onClick={() => onStudioAction({ type: "partial-regenerate", section: contract.sections[1], reason: "부분 근거 보강" })}>부분 AI 재생성</button><button type="button" onClick={() => onStudioAction({ type: "update-setting", field: "purpose", value: `${state.settings.purpose} · 변경`, reason: "제출 후 목적 변경" })}>제출 후 설정 변경</button></div>
          <details><summary>Version 비교</summary><p>변경 내용 · 변경 사유 · Revision 유형 · 근거 변경 여부를 비교합니다.</p>{state.versions.slice(-2).map((version) => <div className="version-compare" key={version.id}><strong>{version.id}</strong><span>{version.revisionType}</span><span>{version.changeReason}</span><span>Evidence {version.evidenceReferences.length}건</span></div>)}</details>
        </>}
      </section>

      {currentVersion && <section className="studio-section" aria-labelledby="studio-review-title">
        <div className="card-row"><h3 id="studio-review-title">검토 · ApprovalRequest · 재승인</h3><Badge tone={approval?.status ?? "neutral"}>{approval?.status ?? "요청 없음"}</Badge></div>
        <p>{currentVersion.status} · Review {currentVersion.reviewState} · Approval {currentVersion.approvalState} · 재승인 {currentVersion.requiresReapproval ? "필요" : "아님"}</p>
        <div className="studio-actions"><button type="button" onClick={() => onStudioAction({ type: "request-review" })}>검토 요청</button><button type="button" onClick={() => onStudioAction({ type: "start-review" })}>검토 시작</button><button type="button" onClick={() => onStudioAction({ type: "request-revision", reason: "검토 근거 보강" })}>수정 요청</button><button type="button" onClick={() => onStudioAction({ type: "request-approval", expiresInDays: 7 })}>승인 요청 · 7일</button><button type="button" onClick={() => onStudioAction({ type: "approve" })}>승인</button><button type="button" onClick={() => onStudioAction({ type: "reject" })}>반려</button><button type="button" onClick={() => onStudioAction({ type: "expire-approval" })}>만료 Fixture</button><button type="button" onClick={() => onStudioAction({ type: "withdraw-approval" })}>판정 전 회수</button><button type="button" onClick={() => onStudioAction({ type: "post-approval-change", reason: "승인 후 내용 변경" })}>승인 후 변경</button></div>
        {approval && <p className="secondary">{approval.id} · 1~30일 범위 {approval.expiresInDays}일 · 만료 24시간 전 알림 · 자동 승인 {approval.automaticApproval ? "있음" : "0건"} · OutputVersion/Audit 보존</p>}
      </section>}

      <section className="studio-section" aria-labelledby="studio-access-title">
        <div className="card-row"><h3 id="studio-access-title">Export · Delivery · KnowledgeRegistration</h3><Help id="현재 접근 판정">각 요청마다 현재 Membership·Workspace ACL·SourceVersion 권한·조직 정책을 다시 판정합니다.</Help></div>
        <div className="access-switch"><button type="button" aria-pressed={state.access.state === "available"} onClick={() => onStudioAction({ type: "set-access", accessState: "available" })}>available</button><button type="button" aria-pressed={state.access.state === "partially_redacted"} onClick={() => onStudioAction({ type: "set-access", accessState: "partially_redacted" })}>partially_redacted</button><button type="button" aria-pressed={state.access.state === "access_blocked"} onClick={() => onStudioAction({ type: "set-access", accessState: "access_blocked" })}>access_blocked</button></div>
        <p>AccessDecision {state.access.state} · Version {state.access.decisionVersion} · 허용 {state.access.allowedReferences.length} · Mask {state.access.maskedReferences.length}</p>
        <div className="studio-actions"><button type="button" onClick={() => onStudioAction({ type: "preview-export" })}>Export Preview</button><button type="button" onClick={() => onStudioAction({ type: "deliver" })}>Delivery Preview</button><button type="button" onClick={() => onStudioAction({ type: "request-registration" })}>명시 KnowledgeRegistration</button><button type="button" disabled={state.knowledgeRegistration?.status !== "requested"} onClick={() => onStudioAction({ type: "register-knowledge" })}>등록 완료 Fixture</button><button type="button" disabled={state.knowledgeRegistration?.status !== "requested"} onClick={() => onStudioAction({ type: "reject-registration" })}>등록 거부 Fixture</button><button type="button" onClick={() => onStudioAction({ type: "load-approved-fixture" })}>승인 Version Fixture</button><button type="button" onClick={() => onStudioAction({ type: "set-important-conflict" })}>중요 충돌 Fixture</button></div>
        {state.exportPreview && <div className="preview-card"><strong>Export Preview · {state.exportPreview.runtime}</strong><span>{state.exportPreview.outputVersionId} · {state.exportPreview.knowledgeScope}</span><span>허용 부록 {state.exportPreview.allowedEvidenceAppendix.join(" · ")} · Mask {state.exportPreview.maskedReferences.join(" · ") || "0건"}</span><span>실제 파일 생성 {state.exportPreview.fileCreated ? "완료" : "0건"}</span></div>}
        {state.delivery.status === "preview_only" && <p>Delivery {state.delivery.runtime} · 실제 전달 {state.delivery.delivered ? "완료" : "0건"}</p>}
        {state.knowledgeRegistration && <p>{state.knowledgeRegistration.id} · {state.knowledgeRegistration.status} · 고정 Version {state.knowledgeRegistration.outputVersionId} · 이력 {state.knowledgeRegistrations.length}건 · 자동 등록 {state.knowledgeRegistration.automatic ? "있음" : "0건"} · 실제 Index 쓰기 0건 · 순환 감지 {state.knowledgeRegistration.cycleDetection} · Daon 쓰기 {state.daonWrites}건</p>}
        <table className="role-matrix"><caption>역할별 Prototype 권한 Matrix</caption><thead><tr><th>역할</th><th>편집</th><th>승인</th><th>Download</th><th>전달</th><th>등록</th></tr></thead><tbody>{roles.map((role) => <tr key={role}><th>{role}</th>{["edit", "approve", "download", "deliver", "register"].map((action) => <td key={action}>{evaluateRoleAction(role, action).allowed ? "✓" : "—"}</td>)}</tr>)}</tbody></table>
      </section>

      <section className="studio-section" aria-labelledby="studio-mobile-title">
        <div className="card-row"><h3 id="studio-mobile-title">모바일 화이트리스트</h3><Badge>Gateway 계약</Badge></div>
        <div className="mobile-matrix">{MOBILE_STUDIO_ACTIONS.map((action) => { const decision = evaluateMobileAction(action); return <button key={action} type="button" data-allowed={decision.allowed} onClick={() => onStudioAction({ type: "mobile-action", action })}><strong>{decision.allowed ? "허용" : "차단"}</strong><span>{action}</span><small>{decision.stateDomain} · Content Revision {decision.createsContentRevision ? "생성" : "0건"}</small><small>{decision.code} · {decision.continueOn}</small></button>; })}</div>
        {state.mobileDecision && <p className={state.mobileDecision.allowed ? "success-state" : "warning-state"}>{state.mobileDecision.code} · Content Revision {state.mobileDecision.createsContentRevision ? "생성" : "0건"} · {state.mobileDecision.continueOn ?? state.mobileDecision.stateDomain}</p>}
      </section>

      {state.safety && <section className="safe-error" role="alert"><strong>{state.safety.code}</strong><p>{state.safety.message}</p><p>{state.safety.userAction} · Trace {state.safety.traceId}</p></section>}
      <p className="prototype-unavailable">실제 API·DB·LLM·파일 Export·전달·지식 Index 0건 · Prototype · unavailable · M4/M5/M6/M8 후속 책임</p>
    </section>
  );
}
