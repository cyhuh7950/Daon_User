"use client";

import { useMemo, useState } from "react";
import {
  AUTHORITY_ORDER,
  PROCESSING_PATHS,
  createSourcePrototypeSeed,
  getFinalizationLocks,
  projectSourceState,
  selectEvidenceSnapshot
} from "./source-knowledge-model.js";
import { Help, WeightControl } from "./source-knowledge-controls.js";

const TAB_LABELS = Object.freeze({ overview: "Source", processing: "처리", authority: "권위", conflicts: "충돌" });
const TYPE_LABELS = Object.freeze({ user_material: "파일·직접 입력", internet: "인터넷", llm_knowledge: "LLM 일반지식", daon_approved: "Daon 승인 지식", produced_knowledge: "생산 지식" });
const STATUS_LABELS = Object.freeze({ ready: "사용 가능", waiting_model: "모델 대기", partial_understanding: "부분 이해", needs_review: "검토 필요", failed: "실패", expired: "만료", disabled: "사용 중지" });
const STEP_LABELS = Object.freeze({ vision_llm_understanding: "Vision/LLM 의미 이해", parser_ocr_validation: "Parser/OCR 검증·보완", evidence_reconciliation: "근거 위치 조정", indexing: "색인", audio_llm_understanding: "Audio LLM 직접 의미 이해", transcript_timecode_validation: "전사·시간 구간 검증", speech_to_text: "ASR 음성 인식", llm_semantic_understanding: "LLM 의미 이해" });

function SourceStatus({ status }) {
  return <span className={`source-status source-status-${status}`}><span aria-hidden="true">●</span>{STATUS_LABELS[status] ?? status}</span>;
}

function RegistrationEntry({ workspaceId, onUploadPdf, onClose }) {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState({ kind: "idle", message: "PDF 파일 한 개를 선택해 등록하세요." });
  const entries = ["직접 입력", "인터넷 검색", "LLM 일반지식", "Daon 승인 지식", "사용자 생산 지식"];
  const upload = async (event) => {
    event.preventDefault();
    if (!file || !onUploadPdf) return;
    setStatus({ kind: "uploading", message: "PDF를 안전하게 등록하는 중입니다." });
    try {
      const result = await onUploadPdf(file);
      setStatus({ kind: "success", message: `등록 완료 · ${result.source_id}` });
    } catch (error) {
      setStatus({ kind: "error", message: `등록 실패 · ${error instanceof Error ? error.message : "PDF_UPLOAD_FAILED"}` });
    }
  };
  return (
    <section className="registration-entry" aria-labelledby="registration-title">
      <div className="card-row"><h3 id="registration-title">Source 등록 진입</h3><button type="button" onClick={onClose}>닫기</button></div>
      <form className="pdf-upload-form" onSubmit={upload}>
        <label htmlFor="source-pdf-file">사용자 PDF</label>
        <input id="source-pdf-file" type="file" accept="application/pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        <button type="submit" disabled={!workspaceId || !onUploadPdf || !file || status.kind === "uploading"}>PDF 등록</button>
      </form>
      <p className={`upload-status upload-status-${status.kind}`} role="status">{onUploadPdf ? status.message : "실제 PDF 등록 연결 unavailable"}</p>
      <p className="secondary">PDF 등록은 실제 API·Object Storage·DB에 연결됩니다. 의미 이해·색인·대화 실행은 다음 연결 단계까지 unavailable입니다.</p>
      <div className="registration-grid">{entries.map((entry) => <button type="button" key={entry} disabled title="M6에서 실제 연결">{entry}<span>unavailable</span></button>)}</div>
      <p className="visible-notice">사용자 생산 지식은 명시적으로 등록해야 하며 Daon 승인 지식으로 자동 승격되지 않습니다.</p>
    </section>
  );
}

function SourceOverview({ source, versionId, onVersionChange, onOpenEvidence }) {
  const version = source.versions.find((item) => item.id === versionId) ?? source.versions.at(-1);
  return (
    <div className="source-detail-section">
      <dl className="source-metadata">
        <div><dt>ID</dt><dd>{source.id}</dd></div><div><dt>유형</dt><dd>{TYPE_LABELS[source.type]}</dd></div>
        <div><dt>소유·Workspace</dt><dd>{source.owner}</dd></div><div><dt>데이터 영역</dt><dd>{source.region}</dd></div>
        <div><dt>민감도</dt><dd>{source.sensitivity}</dd></div><div><dt>출처</dt><dd>{source.origin}</dd></div>
        <div><dt>조회 시각</dt><dd>{source.checkedAt}</dd></div><div><dt>활성화</dt><dd>{source.active ? "활성" : "검색·생성 제외"}</dd></div>
      </dl>
      <label htmlFor="source-version">불변 Source Version</label>
      <select id="source-version" value={version.id} onChange={(event) => onVersionChange(event.target.value)}>{source.versions.map((item) => <option key={item.id} value={item.id}>v{item.version} · {item.capturedAt}</option>)}</select>
      <dl className="version-snapshot"><div><dt>Digest</dt><dd>{version.digest}</dd></div><div><dt>Captured At</dt><dd>{version.capturedAt}</dd></div><div><dt>이전 버전</dt><dd>{version.previousVersionId ?? "없음"}</dd></div><div><dt>Evidence 위치</dt><dd>{version.evidence.position}</dd></div><div><dt>불변</dt><dd>{version.immutable ? "예" : "아니오"}</dd></div></dl>
      {source.registration === "explicit_required" && <div className="visible-state warning-state"><strong>명시적 등록 필요</strong><span>자동 순환·Daon 승인 지식 자동 승격 없음</span></div>}
      <button id="evidence-trigger-knowledge" className="secondary-action" type="button" onClick={(event) => onOpenEvidence(selectEvidenceSnapshot(source, version.id), event)}>Evidence 위치 열기</button>
    </div>
  );
}

function ProcessingFlow({ source, onDomainAction }) {
  const path = PROCESSING_PATHS[source.modality];
  const completed = new Set(source.processingRun.completedSteps);
  return (
    <div className="source-detail-section">
      <div className="flow-contract"><strong>{path?.label ?? "처리 분기"}</strong><span>{path?.evidence}</span></div>
      {source.modality === "document" && <p className="secondary"><strong>Vision/LLM-first</strong> · Parser/OCR는 검증·보완 전용이며 Parser/OCR-only 성공은 없습니다.</p>}
      {source.modality === "audio_asr_llm" && <p className="secondary"><strong>ASR + LLM</strong> · ASR-only ready 금지</p>}
      <ol className="processing-steps">{(path?.steps ?? []).map((step) => <li key={step} className={completed.has(step) ? "step-completed" : step === source.processingRun.currentStep ? "step-current" : "step-pending"}><span aria-hidden="true">{completed.has(step) ? "✓" : step === source.processingRun.currentStep ? "▶" : "○"}</span><strong>{STEP_LABELS[step]}</strong><small>{completed.has(step) ? "완료" : step === source.processingRun.currentStep ? "현재 단계" : "대기"}</small></li>)}</ol>
      <dl className="run-snapshot"><div><dt>ProcessingRun</dt><dd>{source.processingRun.status}</dd></div><div><dt>종료 Code</dt><dd>{source.processingRun.code ?? "없음"}</dd></div><div><dt>Evidence</dt><dd>{source.processingRun.evidence}</dd></div><div><dt>Ready Gate</dt><dd>{source.processingRun.readyGate}</dd></div></dl>
      {source.status === "partial_understanding" && <div className="visible-state warning-state"><strong>부분 이해 범위</strong><span>{source.processingRun.evidence}</span><span>Source 전체 기본 검색·생성 제외</span></div>}
      {source.retryAction && <button type="button" disabled title={source.retryAction.reason}>{source.retryAction.label} · unavailable</button>}
      {source.status === "disabled" && <div className="visible-state"><strong>사용 중지</strong><span>검색·생성에서 제외되며 새 실행으로 성공 상태를 만들지 않습니다.</span></div>}
      {source.recoveryOptions && source.status !== "disabled" && <div className="recovery-options">
        <button type="button" disabled title="M2-07에서 실제 Run 연결">재처리 요청 · unavailable</button>
        <button type="button" onClick={() => onDomainAction({ type: "request-review", sourceId: source.id })}>검토 요청</button>
        <button type="button" onClick={() => onDomainAction({ type: "disable-source", sourceId: source.id })}>사용 중지</button>
      </div>}
      {source.audit?.length > 0 && <div className="visible-state"><strong>Audit Preview</strong><span>{source.audit.at(-1).action} · {source.audit.at(-1).result}</span></div>}
    </div>
  );
}

function AuthorityPanel({ source, rulesets, overrideValue, onDomainAction }) {
  return (
    <div className="source-detail-section">
      <ol className="authority-order" aria-label="권위 우선순위">{AUTHORITY_ORDER.map((authority, index) => <li key={authority} className={authority === source.authority ? "authority-current" : ""}><span>{index + 1}</span>{authority}</li>)}</ol>
      <div className="authority-rule"><strong>권위 우선 병합</strong><span>가중치는 같은 Tier 안에서만 작동하며 권위를 뒤집지 못합니다.</span></div>
      <WeightControl source={source} overrideValue={overrideValue} onSetOverride={(value) => onDomainAction({ type: "set-weight-override", sourceId: source.id, value })} onClearOverride={() => onDomainAction({ type: "clear-weight-override", sourceId: source.id })} />
      <p className="secondary">Source 제외는 가중치 0이 아니라 별도 활성화 상태로 관리합니다.</p>
      <h3>RuleSet · Source와 별도 정책</h3>
      <div className="ruleset-list">{rulesets.map((ruleset) => <article key={ruleset.id} className="ruleset-card"><div className="card-row"><strong>{ruleset.name}</strong><span>{ruleset.locked ? "🔒 강제" : "선택"}</span></div><p>{ruleset.condition} · {ruleset.version}</p><p className="secondary">실패 방식 {ruleset.failureMode}</p><button type="button" aria-label={`${ruleset.name} ${ruleset.enabled ? "끄기" : "켜기"}`} disabled={ruleset.locked} onClick={() => onDomainAction({ type: "toggle-ruleset", rulesetId: ruleset.id, enabled: !ruleset.enabled })}>{ruleset.enabled ? "적용 중" : "미적용"}</button>{ruleset.locked && <Help id={ruleset.id} label={`${ruleset.name} 잠금 이유`}>조직 강제 Binding은 사용자가 해제할 수 없습니다.</Help>}</article>)}</div>
    </div>
  );
}

function ConflictPanel({ conflicts, onResolve, onRaise }) {
  const locks = getFinalizationLocks(conflicts);
  return (
    <div className="source-detail-section">
      <div className={locks.length ? "conflict-block" : "conflict-cleared"} role="status"><strong>{locks.length ? "review_required=true · 최종화 차단" : "중요 충돌 검토 완료"}</strong><span>{locks.length ? "승인·외부 전달·생산 지식 등록이 비활성화됩니다." : "Prototype 검토 상태 전이만 완료했습니다."}</span></div>
      <div className="conflict-list">{conflicts.map((conflict) => {
        const latestAudit = conflict.audit?.at(-1);
        return <article key={conflict.id} className={`conflict-card conflict-${conflict.severity}`}><div className="card-row"><strong>{conflict.claim}</strong><span>{conflict.severity}</span></div><p>{conflict.reason}</p><dl><div><dt>판정</dt><dd>{conflict.policyVersion}</dd></div><div><dt>관련 Source·Version</dt><dd>{conflict.sources.join(" · ")}</dd></div><div><dt>적용</dt><dd>{conflict.applied}</dd></div><div><dt>배제·대안</dt><dd>{conflict.excluded}</dd></div></dl><div className="conflict-review-actions">{conflict.severity === "informational" && <button type="button" onClick={() => onRaise(conflict.id, "material")}>material로 상향</button>}{conflict.severity !== "critical" && <button type="button" onClick={() => onRaise(conflict.id, "critical")}>critical로 상향</button>}{conflict.reviewRequired && conflict.resolution.status !== "resolved" && <button type="button" onClick={() => onResolve(conflict.id)}>검토 완료 처리</button>}</div><dl className="conflict-audit-preview" aria-label={`${conflict.claim} Audit Preview`}><div><dt>Audit Preview</dt><dd>{latestAudit ? `${latestAudit.action} · ${latestAudit.reviewer}` : "검토 행동 없음"}</dd></div><div><dt>해결 상태</dt><dd>{conflict.resolution.status}</dd></div><div><dt>해결 행동</dt><dd>{conflict.resolution.action ?? "없음"}</dd></div></dl><span className="secondary">검토자는 심각도를 올릴 수 있으며 정책 잠금 중요도를 낮출 수 없습니다.</span></article>;
      })}</div>
      <div className="finalization-actions">{[["approval", "승인"], ["external_delivery", "외부 전달"], ["knowledge_registration", "생산 지식 등록"]].map(([id, label]) => <button type="button" key={id} disabled title="M2-05 이후 연결">{label}{locks.includes(id) ? " · 차단 · Prototype · unavailable" : " · Prototype · unavailable"}</button>)}</div>
      <p className="secondary">Audit Preview · ConflictPolicyVersion과 검토자 행동을 보존합니다.</p>
    </div>
  );
}

export function SourceKnowledgePane({ workspaceId, onUploadPdf, selectedSourceId, domainState, onDomainAction, onSelectSource, onOpenEvidence }) {
  const seed = useMemo(() => createSourcePrototypeSeed(), []);
  const projectedSources = seed.sources.map((source) => projectSourceState(source, domainState.sourceStateById[source.id]));
  const selectedSource = projectedSources.find((source) => source.id === selectedSourceId) ?? projectedSources[0];
  const versionId = domainState.versionBySource[selectedSource.id] ?? selectedSource.versions.at(-1).id;
  return (
    <section className="workspace-pane source-knowledge-pane" id="pane-knowledge" aria-labelledby="pane-knowledge-title">
      <div className="pane-heading"><div><p className="eyebrow">자료·지식 · 프로토타입 데이터</p><h2 id="pane-knowledge-title">Source·권위 흐름</h2></div><Help id="knowledge-domain" label="자료·지식 흐름 설명">실제 Upload·API·DB·LLM 실행은 연결되지 않았으며 unavailable로 표시합니다.</Help></div>
      <button type="button" className="primary-action" onClick={() => onDomainAction({ type: "set-registration-open", open: !domainState.registrationOpen })} aria-expanded={domainState.registrationOpen} aria-controls="source-registration-entry">Source 등록 진입</button>
      {domainState.registrationOpen && <div id="source-registration-entry"><RegistrationEntry workspaceId={workspaceId} onUploadPdf={onUploadPdf} onClose={() => onDomainAction({ type: "set-registration-open", open: false })} /></div>}
      <div className="source-workbench">
        <nav className="source-list" aria-label="Source 목록">{projectedSources.map((source) => <button type="button" key={source.id} className={source.id === selectedSource.id ? "source-list-item selected" : "source-list-item"} aria-current={source.id === selectedSource.id ? "true" : undefined} onClick={() => onSelectSource(source.id)}><span><strong>{source.name}</strong><small>{TYPE_LABELS[source.type]} · {source.group}</small></span><SourceStatus status={source.status} /></button>)}</nav>
        <article className="source-detail" aria-live="polite">
          <header><div><p className="eyebrow">선택 Source</p><h3>{selectedSource.name}</h3></div><SourceStatus status={selectedSource.status} /></header>
          <nav className="source-tabs" aria-label="Source Detail 탭">{Object.entries(TAB_LABELS).map(([id, label]) => <button type="button" key={id} aria-pressed={domainState.activeTab === id} onClick={() => onDomainAction({ type: "set-tab", tab: id })}>{label}</button>)}</nav>
          {domainState.activeTab === "overview" && <SourceOverview source={selectedSource} versionId={versionId} onVersionChange={(nextVersionId) => onDomainAction({ type: "select-version", sourceId: selectedSource.id, versionId: nextVersionId })} onOpenEvidence={onOpenEvidence} />}
          {domainState.activeTab === "processing" && <ProcessingFlow source={selectedSource} onDomainAction={onDomainAction} />}
          {domainState.activeTab === "authority" && <AuthorityPanel source={selectedSource} rulesets={domainState.rulesets} overrideValue={domainState.weightOverrides[selectedSource.id]} onDomainAction={onDomainAction} />}
          {domainState.activeTab === "conflicts" && <ConflictPanel conflicts={domainState.conflicts} onRaise={(conflictId, severity) => onDomainAction({ type: "raise-conflict-severity", conflictId, severity, reviewer: "reviewer-prototype" })} onResolve={(conflictId) => onDomainAction({ type: "resolve-conflict", conflictId, reviewer: "reviewer-prototype" })} />}
        </article>
      </div>
    </section>
  );
}
