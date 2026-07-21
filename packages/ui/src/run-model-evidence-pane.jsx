"use client";

import { useState } from "react";
import { BRANCH_STATES, EVIDENCE_STATES, RUN_STAGES } from "./run-model-evidence-model.js";

const MODE_OPTIONS = [
  ["auto", "자동"],
  ["local_only.device_only", "로컬만 · device_only"],
  ["local_only.private_org_allowed", "로컬만 · private_org_allowed"],
  ["pinned", "직접 선택 · pinned"]
];
const SCENARIOS = [
  ["normal", "정상"], ["fallback", "Fallback"], ["pinned_wait", "waiting_user"], ["approval", "waiting_approval"],
  ["cost_limit", "비용 차단 · COST_LIMIT_EXCEEDED"], ["auth_error", "인증 실패"], ["important_conflict", "중요 충돌"]
];
const EVIDENCE_LABELS = { sufficient: "근거 충분", partial: "근거 부분", insufficient: "근거 부족" };
const NEXT_ACTION_LABELS = { retry: "재시도 · 새 Run", choose_allowed_model: "허용 모델 변경", request_policy_approval: "정책 승인 요청", create_new_run_after_authorized_change: "승인 변경 후 새 Run" };

function Help({ id, children }) {
  const [open, setOpen] = useState(false);
  const tooltipId = `run-tooltip-${id}`;
  return <span className="info-control"><button className="icon-button" type="button" aria-label={`${children} 설명`} aria-describedby={open ? tooltipId : undefined} aria-expanded={open} onClick={() => setOpen((value) => !value)} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>i</button>{open && <span id={tooltipId} role="tooltip" className="info-tooltip">{children}</span>}</span>;
}

function statusMark(status) {
  if (status === "completed") return "✓";
  if (BRANCH_STATES.includes(status)) return "!";
  return "→";
}

export function RunModelEvidencePane({ domainState, onDomainAction, onOpenEvidence }) {
  const { run, settings, selectedScenario } = domainState;
  const selectedMode = settings.mode === "local_only" ? `${settings.mode}.${settings.localScope}` : settings.mode;
  const citation = run.citations[0];
  const stageIndex = Math.max(0, RUN_STAGES.indexOf(run.status));
  const setMode = (value) => {
    if (value.startsWith("local_only.")) onDomainAction({ type: "set-mode", mode: "local_only", localScope: value.split(".")[1] });
    else onDomainAction({ type: "set-mode", mode: value, pinnedDeploymentId: value === "pinned" ? "dep-org-text" : null });
  };

  return (
    <section className="workspace-pane run-pane" id="pane-conversation" aria-labelledby="pane-conversation-title">
      <div className="pane-heading"><div><p className="eyebrow">Production-bound Prototype</p><h2 id="pane-conversation-title">대화·실행</h2></div><Help id="overview">Fixture와 순수 Reducer만 실행하며 실제 API·DB·LLM·Provider Network는 0건입니다.</Help></div>

      <div className="run-control-grid">
        <label htmlFor="run-mode">모델 선택 Mode</label>
        <select id="run-mode" value={selectedMode} onChange={(event) => setMode(event.target.value)}>{MODE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
        {settings.mode === "pinned" && <label htmlFor="pinned-model">허용 Deployment<select id="pinned-model" value={settings.pinnedDeploymentId ?? "dep-org-text"} onChange={(event) => onDomainAction({ type: "set-mode", mode: "pinned", pinnedDeploymentId: event.target.value })}><option value="dep-org-text">사내 Text 모델 · dep-org-text</option></select></label>}
        <p className="secondary">현재 설정 변경은 실행 중 Frozen Snapshot을 바꾸지 않고 다음 Run에만 적용됩니다.</p>
        <button className="primary-action" type="button" onClick={() => onDomainAction({ type: "start" })}>새 Prototype Run 시작</button>
      </div>

      <details className="run-snapshot" open><summary>Frozen RoutingContext Preview</summary><dl className="version-snapshot"><div><dt>Mode · Local Scope · Pinned</dt><dd>{run.snapshot.routing.mode} · {run.snapshot.routing.localScope ?? "해당 없음"} · {run.snapshot.routing.pinnedDeploymentId ?? "해당 없음"}</dd></div><div><dt>Actor · Tenant · Workspace</dt><dd>{run.snapshot.actorId} · {run.snapshot.tenantId} · {run.snapshot.workspaceId}</dd></div><div><dt>역할 · 분류 · 데이터 영역 · Egress</dt><dd>{run.snapshot.role} · {run.snapshot.classification} · {run.snapshot.dataRegion} · {run.snapshot.egressPolicy}</dd></div><div><dt>Routing · Workspace 정책</dt><dd>{run.snapshot.routing.policyVersion} · {run.snapshot.workspacePolicyVersion}</dd></div><div><dt>정책 기한</dt><dd>{run.snapshot.deadline}</dd></div><div><dt>비용 한도</dt><dd>{run.snapshot.cost.limit} {run.snapshot.cost.currency} · {run.snapshot.cost.scope}</dd></div><div><dt>허용 후보</dt><dd>{run.snapshot.routing.allowedCandidateIds.join(" → ") || "허용 후보 없음"}</dd></div><div><dt>정렬 순서</dt><dd>{run.snapshot.routing.sortingOrder.join(" → ")}</dd></div><div><dt>Fallback 계획</dt><dd>{run.snapshot.routing.fallbackPlan.join(" → ") || "자동 Fallback 없음"}</dd></div><div><dt>SourceVersion · 권위</dt><dd>{run.snapshot.sourceVersions.join(" · ")} · {run.snapshot.authorityTiers.join(" → ")}</dd></div><div><dt>가중치 계층 · 요청 · 유효 · Clamp</dt><dd>{run.snapshot.weightApplications.map((item) => `${item.layer} · ${item.requested} · ${item.effective} · ${item.clamped ? "Clamp" : "원값"}`).join(" / ")}</dd></div><div><dt>RuleSet</dt><dd>{run.snapshot.ruleSetSnapshotIds.join(" · ")}</dd></div><div><dt>Prompt · Tool</dt><dd>{run.snapshot.promptContractVersion} · {run.snapshot.toolContractVersion}</dd></div></dl></details>

      <div className="run-scenarios" aria-label="결정론적 실행 Fixture">{SCENARIOS.map(([value, label]) => <button key={value} type="button" aria-pressed={selectedScenario === value} onClick={() => onDomainAction({ type: "select-scenario", scenario: value })}>{label}</button>)}</div>

      <section className="run-progress" aria-live="polite"><div className="card-row"><strong>실행 단계</strong><span className={`run-state state-${run.status}`}>{statusMark(run.status)} {run.status}</span></div><ol className="run-steps">{RUN_STAGES.map((stage, index) => <li key={stage} data-state={index < stageIndex ? "completed" : index === stageIndex ? "current" : "pending"}><span aria-hidden="true">{index < stageIndex ? "✓" : index === stageIndex ? "→" : "○"}</span>{stage}</li>)}</ol><p className="secondary">Trace {run.traceId} · 시작 {run.startedAt} · 취소 {run.cancellable ? "가능" : "불가"}</p><div className="run-actions"><button type="button" onClick={() => onDomainAction({ type: "advance" })} disabled={run.status === "completed" || BRANCH_STATES.includes(run.status)}>다음 단계</button><button type="button" onClick={() => onDomainAction({ type: "cancel" })} disabled={!run.cancellable}>취소</button></div></section>

      {run.error && <section className="safe-error" role="alert"><strong>{run.error.code}</strong><p>{run.error.message}</p><dl className="version-snapshot"><div><dt>실패 단계</dt><dd>{run.error.failedStage}</dd></div><div><dt>영향</dt><dd>{run.error.impact}</dd></div><div><dt>재시도</dt><dd>{run.error.retryable ? "가능" : "불가"}</dd></div><div><dt>조치</dt><dd>{run.error.userAction}</dd></div><div><dt>Trace</dt><dd>{run.error.traceId}</dd></div></dl></section>}
      {run.nextActions.length > 0 && <div className="run-actions" aria-label="다음 행동">{run.nextActions.map((action) => <button key={action} type="button" onClick={() => onDomainAction(action === "retry" ? { type: "start" } : action === "choose_allowed_model" ? { type: "set-mode", mode: "auto" } : { type: "select-scenario", scenario: selectedScenario })}>{NEXT_ACTION_LABELS[action] ?? action}</button>)}</div>}
      {run.status === "policy_blocked" && <section className="warning-state" aria-label="비용 차단 원장"><strong>비용 사전 차단</strong><p>{run.ledger.accumulatedCost} + 예상 {run.ledger.estimatedCost} / {run.ledger.costLimit} {run.ledger.currency} · 차단 시점 {run.ledger.costBlockedAt}</p><p>Attempt {run.attempts.length}건 · 동일 Frozen Context 자동 재시도 0건 · 미완성 결과 0건. 권한 있는 변경 후 새 Run만 생성할 수 있습니다.</p></section>}

      <section className="run-routing-summary" aria-label="모델 선택과 계보"><div className="card-row"><strong>모델 선택·계보</strong><span>{run.decision.selectedDeploymentId ?? "선택 없음"}</span></div><p>선택 이유 {run.decision.selectionReason}</p><p>Provider Profile {run.ledger.providerProfiles.join(" · ") || "없음"} · Deployment {run.ledger.deployments.join(" · ") || "없음"}</p><p>ModelArtifact {run.ledger.modelArtifacts.join(" · ") || "없음"} · Digest {run.ledger.artifactDigests.join(" · ") || "없음"}</p><p>역할별 최종 모델 {Object.entries(run.ledger.roleFinalModels).map(([role, value]) => `${role}:${value}`).join(" · ")} · Understanding {run.ledger.understandingModels.text}:{run.ledger.understandingModels.deploymentId}</p><p>ModelAttempt {run.attempts.map((item) => `${item.id}:${item.deploymentId}:${item.result}`).join(" · ") || "호출 전 차단 (0건)"}</p></section>

      <section className="evidence-summary"><div className="card-row"><strong>근거 상태</strong><span className={`evidence-state evidence-${run.evidenceState}`}>{run.evidenceState === "sufficient" ? "✓" : "!"} {EVIDENCE_LABELS[run.evidenceState]}</span></div><p className="secondary">표시 계약: {EVIDENCE_STATES.join(" | ")}</p>{run.finalization.blocked && <p className="warning-state">IMPORTANT_KNOWLEDGE_CONFLICT · 최종 결과 확정 차단 · 검토 진입 필요</p>}{citation && <button id="evidence-trigger-run-citation" className="secondary-action" type="button" onClick={(event) => onOpenEvidence(citation, event)}>Citation · {citation.position}</button>}</section>

      <details className="decision-ledger"><summary>실행 결정 원장</summary><dl className="version-snapshot"><div><dt>Mode · Policy</dt><dd>{run.ledger.mode} · {run.ledger.routingPolicyVersion}</dd></div><div><dt>정책 제외</dt><dd>{run.ledger.policyExclusions.map((item) => `${item.deploymentId}:${item.code}`).join(" · ") || "0건"}</dd></div><div><dt>Runtime 제외</dt><dd>{run.ledger.runtimeExclusions.map((item) => `${item.deploymentId}:${item.code}`).join(" · ") || "0건"}</dd></div><div><dt>선택 이유</dt><dd>{run.ledger.selectionReason}</dd></div><div><dt>ModelAttempt</dt><dd>{run.attempts.map((item) => `${item.id}:${item.deploymentId}:${item.result}`).join(" · ") || "호출 전 차단"}</dd></div><div><dt>모델 계보</dt><dd>{run.ledger.modelArtifacts.join(" · ")} · {run.ledger.artifactDigests.join(" · ")}</dd></div><div><dt>보조 도구</dt><dd>{run.ledger.auxiliaryTools.map((item) => `${item.name} ${item.version}`).join(" · ")} · {run.ledger.auxiliaryToolReasons.join(" · ")}</dd></div><div><dt>비용</dt><dd>{run.ledger.accumulatedCost} + 예상 {run.ledger.estimatedCost} / {run.ledger.costLimit} {run.ledger.currency}</dd></div><div><dt>비용 차단 시점</dt><dd>{run.ledger.costBlockedAt ?? "차단 없음"}</dd></div><div><dt>사용량</dt><dd>Token {run.ledger.tokenUsage.input + run.ledger.tokenUsage.output} · Byte {run.ledger.byteUsage} · {run.ledger.latencyMs}ms</dd></div><div><dt>IDs</dt><dd>{run.ledger.nodeId} · {run.ledger.requestId} · {run.ledger.runId}</dd></div></dl></details>

      <p className="prototype-unavailable">실제 Retrieval·Provider·Citation 조정 · Prototype · unavailable</p>
    </section>
  );
}
