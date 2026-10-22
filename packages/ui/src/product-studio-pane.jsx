"use client";

import { useEffect, useRef, useState } from "react";
import { parseSheetCell } from "./product-workspace-model.js";
import { parseDocxBlock } from "./shared-docx-block.js";
import { pptxBlockLabel } from "./shared-pptx-block.js";
import { parseAudioSegment } from "./shared-audio-segment.js";
import { secureUuid } from "./secure-uuid.js";
import { StudioOutputIcon } from "./studio-output-icon.jsx";
import {
  OUTPUT_TYPES, canConfirmGenerationSettings, canSubmitGeneration, confirmGenerationSettings, createStudioGenerationInput,
  mergeStudioVersion, selectOutputType, updateGenerationSettings,
} from "./product-studio-model.js";

const TYPE_ICONS = Object.freeze({
  evidence_report: "document", compliance_checklist: "check", comparison_table: "table",
  knowledge_map: "map", business_draft: "draft", slides: "slides", infographic: "image",
  flashcards: "cards", quiz: "quiz", audio: "audio", video: "video",
});

function outputStatusLabel(status) {
  return ({
    draft: "초안", in_review: "검토 중", approved: "승인됨", delivered: "전달 기록됨",
    revision_requested: "수정 필요", generating: "생성 중", failed: "생성 실패", unavailable: "사용 불가",
  })[status] ?? "저장됨";
}

function outputTypeLabel(outputType) {
  return OUTPUT_TYPES.find((item) => item.id === outputType)?.label ?? "산출물";
}

function canRequestStudioAction(action, output) {
  if (!output || output.canonicalPending || output.lifecyclePending) return false;
  const closed = ["expired", "withdrawn"].includes(output.approval_request_state)
    || (typeof output.approval_expires_at === "string" && Date.parse(output.approval_expires_at) <= Date.now());
  if (action === "reviews") return output.status === "draft" && !output.review_request_id;
  if (action === "approval-requests") return ["in_review", "review_requested"].includes(output.status)
    && Boolean(output.review_request_id) && (!output.approval_request_id || closed);
  if (action === "approvals") return ["in_review", "review_requested"].includes(output.status)
    && Boolean(output.approval_request_id) && !output.approval_id && !closed;
  if (action === "withdraw") return Boolean(output.approval_request_id) && output.approval_request_state === "pending" && !closed;
  if (action === "deliveries") return output.status === "approved" && Boolean(output.approval_id) && !output.delivery_id;
  if (action === "knowledge-registrations") return output.status === "approved" && !output.knowledge_registration_id;
  return false;
}

function validStudioActionReceipt(result, request) {
  const action = { reviews: "review", "approval-requests": "approval_request", approvals: "approval", deliveries: "delivery", "knowledge-registrations": "knowledge_registration", withdraw: "withdraw" }[request.action];
  const keys = ["record_id", "action", "status", "output_version_id"];
  if (action === "knowledge_registration") keys.push("registered_source_version_id", "searchable");
  const safeId = (value) => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value);
  return result !== null && typeof result === "object" && !Array.isArray(result)
    && Object.keys(result).length === keys.length && keys.every((key) => Object.hasOwn(result, key))
    && safeId(result.record_id) && result.action === action && result.output_version_id === request.versionId
    && (action === "knowledge_registration"
      ? result.status === "registered" && safeId(result.registered_source_version_id) && result.searchable === true
      : action === "withdraw" ? result.status === "withdrawn" && result.record_id === request.payload.approval_request_id : result.status === "accepted");
}

function safeStudioErrorMessage(code) {
  if (!code) return null;
  return ({
    NOTEBOOK_MODEL_SELECTION_REQUIRED:"모델 설정에서 사용할 허용 모델을 직접 선택해 주세요.",
    NOTEBOOK_MODEL_SETTINGS_CHANGED:"모델 설정이 변경되었습니다. 다시 조회하고 모델을 확인해 주세요.",
    STUDIO_GENERATION_UNAVAILABLE:"생성 연결을 사용할 수 없습니다. 현재 모델과 접근 권한을 확인해 주세요.",
    STUDIO_GENERATION_UNCERTAIN:"생성 처리 여부를 확인해야 합니다. 같은 작업을 새로 실행하지 마세요.",
    STUDIO_EVIDENCE_INSUFFICIENT:"선택 자료만으로 이 산출물을 만들기 어렵습니다. 필요한 자료를 확인해 주세요.",
    STUDIO_INPUT_CHANGED:"자료 또는 모델 설정이 변경되었습니다. 현재 자료와 선택을 다시 확인해 주세요.",
    STUDIO_CREATE_FAILED: "산출물을 생성하지 못했습니다. 설정과 연결 상태를 확인해 주세요.",
    STUDIO_LIST_FAILED: "저장된 산출물을 불러오지 못했습니다. 운영상태에서 연결을 확인해 주세요.",
    STUDIO_VERSION_FAILED: "새 버전을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    STUDIO_ACTION_FAILED: "요청을 처리하지 못했습니다. 현재 상태를 확인해 주세요.",
    STUDIO_EXPORT_FAILED: "파일을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    STUDIO_OUTPUT_UNAVAILABLE: "현재 자료나 실행 조건으로 산출물을 만들 수 없습니다. 필요한 자료와 설정을 확인해 주세요.",
    STUDIO_CREATE_TIMEOUT: "산출물 생성이 지연되고 있습니다. Library에서 상태를 다시 확인해 주세요.",
    STEP_UP_REQUIRED: "추가 인증을 완료하지 못했습니다. 비밀번호를 확인한 뒤 다시 시도해 주세요.",
    STUDIO_STEP_UP_UNAVAILABLE: "추가 인증 연결을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    APPROVAL_REQUEST_EXPIRED: "승인 기한이 지났습니다. 현재 상태를 확인하고 새 요청을 만들어 주세요.",
    APPROVAL_REQUEST_PENDING: "진행 중인 승인 요청이 있습니다. 현재 요청을 확인해 주세요.",
    PRECONDITION_FAILED: "저장 상태가 변경되었습니다. 현재 값을 새로 확인해 주세요.",
  })[code] ?? "요청을 안전하게 완료하지 못했습니다. 운영상태에서 연결 상태를 확인해 주세요.";
}

function studioContentText(content) {
  if (content == null) return "";
  if (typeof content === "string" || typeof content === "number" || typeof content === "boolean") return String(content);
  if (Array.isArray(content)) return content.map(studioContentText).filter(Boolean).join("\n");
  return Object.values(content).map(studioContentText).filter(Boolean).join("\n");
}

function complianceJudgementLabel(value) {
  return ({ compliant: "준수", non_compliant: "미준수", needs_review: "검토 필요" })[value] ?? "확인 필요";
}

function comparisonStateLabel(value) {
  return ({ same: "동일", changed: "변경", missing: "누락", conflict: "충돌" })[value] ?? "확인 필요";
}

function comparisonCellText(value) {
  if (value == null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function knowledgeConfidenceLabel(value) {
  return ({ verified: "검증됨", unverified: "미검증", needs_review: "검토 필요" })[value] ?? "확인 필요";
}

function draftReviewLabel(value) {
  return ({ draft: "초안", in_review: "검토 중", revision_requested: "수정 필요", approved: "승인됨" })[value] ?? "확인 필요";
}

function StudioOutputContent({ output }) {
  const content = output.content ?? output.version?.content;
  if (output.output_type === "evidence_report" && content && typeof content === "object" && !Array.isArray(content)) {
    return <section className="studio-document-result" aria-label="근거 보고서 결과">
      <h4>근거 보고서 결과</h4>
      <div className="document-section-list">{[["요약", content.summary], ["본문", content.body], ["결론", content.conclusion]].map(([label, text], index) =>
        text ? <article key={label}><span className="document-section-number">{String(index + 1).padStart(2, "0")}</span><div><h5>{label}</h5><p>{studioContentText(text)}</p></div></article> : null
      )}</div>
      {Array.isArray(content.warnings) && content.warnings.length ? <p className="document-warning">확인 필요 · {content.warnings.join(", ")}</p> : null}
    </section>;
  }
  if (output.output_type === "compliance_checklist" && Array.isArray(content?.items)) {
    return <section className="studio-compliance-result" aria-label="제약·준수 점검 결과">
      <h4>제약·준수 점검 결과</h4>
      <div className="studio-table-scroll"><table><thead><tr><th>항목</th><th>점검 기준</th><th>확인 기록</th><th>판정</th><th>근거</th><th>조치</th></tr></thead>
        <tbody>{content.items.map((item, index) => <tr key={item?.item_id ?? `check-${index + 1}`}>
          <th scope="row">{item?.item_id ?? `check-${index + 1}`}</th>
          <td>{item?.criterion??"—"}</td><td>{item?.observation??"—"}</td>
          <td><span className={`compliance-judgement judgement-${item?.judgement ?? "unknown"}`}>{complianceJudgementLabel(item?.judgement)}</span></td>
          <td>{item?.evidence || "근거 없음"}</td><td>{item?.action || "검토"}</td>
        </tr>)}</tbody>
      </table></div>
      {Array.isArray(content.warnings) && content.warnings.length ? <p className="compliance-warning">확인 필요 · {content.warnings.join(", ")}</p> : null}
    </section>;
  }
  if (output.output_type === "comparison_table" && Array.isArray(content?.rows)) {
    return <section className="studio-comparison-result" aria-label="비교·데이터 결과">
      <h4>비교·데이터 결과</h4>
      <div className="studio-table-scroll"><table><thead><tr><th>항목</th><th>기준</th><th>현재</th><th>상태</th><th>근거</th></tr></thead>
        <tbody>{content.rows.map((row, index) => <tr key={row?.key ?? `row-${index + 1}`}>
          <th scope="row">{row?.key ?? `항목 ${index + 1}`}</th>
          <td>{comparisonCellText(row?.baseline)}</td><td>{comparisonCellText(row?.current)}</td>
          <td><span className={`comparison-state state-${row?.state ?? "unknown"}`}>{comparisonStateLabel(row?.state)}</span></td>
          <td>{Array.isArray(row?.evidence) ? row.evidence.filter(Boolean).join(" · ") || "근거 없음" : comparisonCellText(row?.evidence)}</td>
        </tr>)}</tbody>
      </table></div>
    </section>;
  }
  if (output.output_type === "knowledge_map" && Array.isArray(content?.nodes) && Array.isArray(content?.edges)) {
    const labels = Object.fromEntries(content.nodes.map((node) => [node?.id, node?.label ?? node?.id]));
    return <section className="studio-knowledge-result" aria-label="지식 구조 결과">
      <h4>지식 구조 결과</h4>
      {content.relation_mode==="quoted_context"?<p>인용 맥락 연결 · 방향 미확정: 원문에 함께 나타난 항목이며 인과나 전달 방향을 확정하지 않습니다.</p>:null}
      <div className="knowledge-node-grid">{content.nodes.map((node, index) => <article key={node?.id ?? `node-${index + 1}`} className="knowledge-node-card">
        <span className="knowledge-node-mark">{index + 1}</span><strong>{node?.label ?? node?.id ?? `지식 ${index + 1}`}</strong>
        <span className={`knowledge-confidence confidence-${node?.confidence ?? "unknown"}`}>{content.relation_mode==="quoted_context"&&node?.confidence==="verified"?"원문 확인":knowledgeConfidenceLabel(node?.confidence)}</span>
        <small>{node?.evidence || "근거 없음"}</small>
      </article>)}</div>
      {content.edges.length ? <ul className="knowledge-edge-list" aria-label="지식 관계">{content.edges.map((edge, index) => <li key={edge?.id ?? `edge-${index + 1}`}>
        <strong>{labels[edge?.source] ?? edge?.source} {content.relation_mode==="quoted_context"?"—":"→"} {labels[edge?.target] ?? edge?.target}</strong>
        <span>{edge?.relation ?? edge?.condition ?? "관계"}</span>
      </li>)}</ul> : <p className="secondary">표시할 관계가 없습니다.</p>}
    </section>;
  }
  if (output.output_type === "business_draft" && Array.isArray(content?.sections)) {
    return <section className="studio-document-result" aria-label="업무 문서 결과">
      <header><h4>업무 문서 결과</h4><span className="document-review-state">{draftReviewLabel(content.review_state)}</span></header>
      <div className="document-section-list">{content.sections.map((section, index) => <article key={`${section?.title ?? "section"}-${index + 1}`}>
        <span className="document-section-number">{String(index + 1).padStart(2, "0")}</span>
        <div><h5>{section?.title || `Section ${index + 1}`}</h5><p>{section?.body || "내용 없음"}</p>
          <ul aria-label={`${section?.title || `Section ${index + 1}`} 근거`}>{Array.isArray(section?.evidence) && section.evidence.length
            ? section.evidence.map((evidence) => <li key={evidence}>{evidence}</li>)
            : <li>근거 없음</li>}</ul>
        </div>
      </article>)}</div>
      {Array.isArray(content.warnings) && content.warnings.length ? <p className="document-warning">확인 필요 · {content.warnings.join(", ")}</p> : null}
    </section>;
  }
  if (output.output_type === "slides" && Array.isArray(content?.slides)) {
    return <section className="studio-document-result" aria-label="슬라이드 결과">
      <header><h4>슬라이드 결과</h4><span className="document-review-state">{outputStatusLabel(output.status)}</span></header>
      <div className="document-section-list">{content.slides.map((slide, index) => <article key={slide?.slide_number ?? `slide-${index + 1}`}>
        <span className="document-section-number">{String(slide?.slide_number ?? index + 1).padStart(2, "0")}</span>
        <div><h5>{slide?.title || `슬라이드 ${index + 1}`}</h5><p>{slide?.body || "내용 없음"}</p>
          <ul aria-label={`${slide?.title || `슬라이드 ${index + 1}`} 근거`}>{Array.isArray(slide?.evidence) && slide.evidence.length
            ? slide.evidence.map((evidence) => <li key={evidence}>{evidence}</li>) : <li>근거 없음</li>}</ul>
        </div>
      </article>)}</div>
    </section>;
  }
  if (output.output_type === "infographic" && Array.isArray(content?.metrics)) {
    return <section className="studio-document-result" aria-label="인포그래픽 결과">
      <header><h4>{content.title || "인포그래픽 결과"}</h4><span className="document-review-state">{outputStatusLabel(output.status)}</span></header>
      <p>{content.summary || "요약 없음"}</p>
      <div className="knowledge-node-grid">{content.metrics.map((metric, index) => <article key={`${metric?.label ?? "metric"}-${index + 1}`} className="knowledge-node-card">
        <strong>{metric?.label || `지표 ${index + 1}`}</strong><span>{metric?.value ?? "—"}</span><small>{metric?.evidence || "근거 없음"}</small>
      </article>)}</div>
    </section>;
  }
  if (output.output_type === "flashcards" && Array.isArray(content?.cards)) {
    return <section className="studio-document-result" aria-label="플래시카드 결과">
      <header><h4>{content.title || "플래시카드 결과"}</h4><span className="document-review-state">{outputStatusLabel(output.status)}</span></header>
      <div className="document-section-list">{content.cards.map((card, index) => <article key={card?.id ?? `card-${index + 1}`}>
        <span className="document-section-number">{String(index + 1).padStart(2, "0")}</span><div><h5>{card?.question || `카드 ${index + 1}`}</h5><p>{card?.answer || "답변 없음"}</p><small>{card?.evidence || "근거 없음"}</small></div>
      </article>)}</div>
    </section>;
  }
  if (output.output_type === "quiz" && Array.isArray(content?.questions)) {
    return <section className="studio-document-result" aria-label="퀴즈 결과">
      <header><h4>{content.title || "퀴즈 결과"}</h4><span className="document-review-state">{outputStatusLabel(output.status)}</span></header>
      <div className="document-section-list">{content.questions.map((question, index) => <article key={question?.id ?? `quiz-${index + 1}`}>
        <span className="document-section-number">{String(index + 1).padStart(2, "0")}</span><div><h5>{question?.question || `문제 ${index + 1}`}</h5><ol>{(Array.isArray(question?.options) ? question.options : []).map((option, optionIndex) => <li key={`${question?.id ?? index}-${optionIndex}`}>{option}</li>)}</ol>{Number.isInteger(question?.answer_index)?<p>정답: {question.answer_index+1}번</p>:null}<p>{question?.explanation || "해설 없음"}</p></div>
      </article>)}</div>
    </section>;
  }
  return <p className="studio-output-content">{studioContentText(content)}</p>;
}

function initialOutputState(state, output) {
  return output ? {...state, selectedOutputId:output.studio_output_id,
    outputs:[output,...state.outputs.filter(item=>item.studio_output_id!==output.studio_output_id)]} : state;
}

export function ProductStudioPane({ state, adapter, onKnowledgeRegistered, initialOutput = null }) {
  const [view, setView] = useState(()=>initialOutputState(state, initialOutput));
  const [revisionContent, setRevisionContent] = useState("");
  const [settingsRevisionTarget,setSettingsRevisionTarget]=useState(null);
  const [changeReason, setChangeReason] = useState("");
  const [knowledgeNotice, setKnowledgeNotice] = useState(null);
  const [savedRevision, setSavedRevision] = useState(null);
  const [typedChallenge,setTypedChallenge]=useState(null);
  const [typedPassword,setTypedPassword]=useState("");
  const [typedStatus,setTypedStatus]=useState(null);
  const [stepUp, setStepUp] = useState(null);
  const [password, setPassword] = useState("");
  const [approvalInfo, setApprovalInfo] = useState({ items: [], unread_count: 0, error: false });
  const [approvalRefresh, setApprovalRefresh] = useState(0);
  const generationRequestRef = useRef(null);
  const generationScopeRef = useRef(null);
  const detailRequestRef = useRef(null);
  const revisionRequestRef = useRef(null);
  const actionRequestRef = useRef(null);
  const detailSelectionRef = useRef(null);
  const currentModelVersion=adapter?.getStudioModelSettingsVersion?.()??null;
  const currentInputKey=JSON.stringify({selected:state.settings?.sourceVersionIds??[],grounded:state.grounded??null,
    sources:(state.sources??[]).map(source=>[source.sourceId,source.sourceVersionId,source.ready]),locks:state.locks??[]});
  const inputScopeRef=useRef({key:currentInputKey,modelVersion:currentModelVersion,epoch:0});
  if(inputScopeRef.current.key!==currentInputKey||inputScopeRef.current.modelVersion!==currentModelVersion){
    inputScopeRef.current={key:currentInputKey,modelVersion:currentModelVersion,epoch:inputScopeRef.current.epoch+1};
  }
  generationScopeRef.current = { adapter, workspaceId: state.workspaceId };
  useEffect(() => {
    const controller = new AbortController();
    const scope = { adapter, workspaceId: state.workspaceId };
    setApprovalInfo({ items: [], unread_count: 0, error: false });
    if (!adapter?.listStudioApprovalNotices) return () => controller.abort();
    const current = () => !controller.signal.aborted && generationScopeRef.current.adapter === scope.adapter && generationScopeRef.current.workspaceId === scope.workspaceId;
    void adapter.listStudioApprovalNotices({ signal: controller.signal })
      .then(notices => { if (current()) setApprovalInfo({ ...notices, error: false }); })
      .catch(() => { if (current()) setApprovalInfo(value => ({ ...value, error: true })); });
    return () => controller.abort();
  }, [adapter, state.workspaceId, approvalRefresh]);
  useEffect(() => {
    setTypedChallenge(null);setTypedPassword("");setTypedStatus(null);setSettingsRevisionTarget(null);
    setView(initialOutputState(state, initialOutput));
    setRevisionContent(""); setChangeReason(""); setSavedRevision(null); setKnowledgeNotice(null);
    return () => {
      generationRequestRef.current?.controller.abort();
      generationRequestRef.current = null;
      detailRequestRef.current?.controller.abort();
      revisionRequestRef.current?.controller.abort();
      actionRequestRef.current?.controller.abort();
      detailRequestRef.current = null;
      revisionRequestRef.current = null;
      actionRequestRef.current = null;
    };
  }, [adapter, state.workspaceId]);
  const incomingLibraryErrorRef=useRef(state.safeError??null);
  useEffect(()=>{
    // Parent list results remain authoritative after Source/Run refresh. Updating
    // the library must not discard an admitted request or its status receipt.
    const previous=incomingLibraryErrorRef.current,next=state.safeError??null;
    incomingLibraryErrorRef.current=next;
    setView(current=>({...current,status:state.status,outputs:state.outputs,
      selectedOutputId:state.outputs.some(output=>output.studio_output_id===current.selectedOutputId)?current.selectedOutputId:null,
      safeError:next??(current.safeError===previous?null:current.safeError)}));
  },[state.outputs,state.status,state.safeError]);
  const stepUpRef = useRef(null);
  const cancelStepUp = () => {
    if (actionRequestRef.current?.phase === "challenge" || actionRequestRef.current?.kind === "export") {
      actionRequestRef.current.controller.abort();
      actionRequestRef.current = null;
    }
    stepUpRef.current = null; setStepUp(null); setPassword("");
    setView((current) => ({ ...current, pending: false }));
  };
  useEffect(() => {
    stepUpRef.current = null; setStepUp(null); setPassword("");
    return () => {
      if (stepUpRef.current) {
        stepUpRef.current = null;
        setView((current) => ({ ...current, pending: false }));
      }
    };
  }, [adapter, state.workspaceId]);
  const selected = OUTPUT_TYPES.find((item) => item.id === view.selectedOutputType);
  const selectedOutput = view.outputs.find((output) => output.studio_output_id === view.selectedOutputId) ?? null;
  detailSelectionRef.current = { outputId: selectedOutput?.studio_output_id, versionId: selectedOutput?.output_version_id };
  const revisionSaved = savedRevision?.outputId === selectedOutput?.studio_output_id
    && savedRevision?.versionId === selectedOutput?.output_version_id && Boolean(savedRevision);
  const generationState = typedStatus&&!typedStatus.terminal ? "uncertain" : view.pending ? "pending" : revisionSaved ? "completed" : view.safeError ? "failed" : selectedOutput ? "completed" : "idle";
  const configurationBusy = Boolean(generationRequestRef.current) || view.pending || Boolean(revisionRequestRef.current) || Boolean(actionRequestRef.current);
  const fieldDisabled = (field) => configurationBusy || view.locks.some((lock) => lock.field === field);
  const sameDetailScope = (request) => !request.controller.signal.aborted
    && generationScopeRef.current.adapter === request.adapter
    && generationScopeRef.current.workspaceId === request.workspaceId;
  const ownsDetail = (request) => detailRequestRef.current === request && sameDetailScope(request);
  const actionDisabled = (action) => configurationBusy || Boolean(generationRequestRef.current) || !canRequestStudioAction(action, selectedOutput);
  const setField = (field, value) => {
    if (generationRequestRef.current || revisionRequestRef.current || actionRequestRef.current) return;
    setView((current) => current.pending || !current.selectedOutputType || current.locks.some((lock) => lock.field === field)
      ? current : updateGenerationSettings(current, { [field]: value }));
  };
  const openOutput = async (output) => {
    const previous = detailRequestRef.current;
    if (previous?.kind === "read" && previous.pending && previous.outputId === output.studio_output_id && ownsDetail(previous)) return;
    if (previous?.kind === "read") previous.controller.abort();
    const request = { kind: "read", pending: true, outputId: output.studio_output_id,
      adapter, workspaceId: state.workspaceId, controller: new AbortController() };
    detailRequestRef.current = request;
    cancelStepUp();
    setRevisionContent(""); setChangeReason(""); setSavedRevision(null);
    setView((current) => ({ ...current, selectedOutputId: output.studio_output_id, pending: Boolean(adapter?.listStudioVersions), safeError: null }));
    try {
      if (!adapter?.listStudioVersions) return;
      const versions = await adapter.listStudioVersions(output.studio_output_id, { signal: request.controller.signal });
      if (!ownsDetail(request)) return;
      const latest = versions[0] ?? null;
      setView((current) => ({
        ...current, pending: false,
        outputs: current.outputs.map((item) => item.studio_output_id === output.studio_output_id
          ? { ...item, ...(latest ?? {}), versions, ...(latest ? { canonicalPending: false, lifecyclePending: false,
            actionReceipt: item.actionReceipt?.output_version_id === latest.output_version_id ? item.actionReceipt : null } : {}) }
          : item),
      }));
    } catch (error) {
      if (ownsDetail(request)) setView((current) => ({ ...current, pending: false, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_LIST_FAILED" }));
    } finally {
      request.pending = false;
    }
  };
  const typedOwns=request=>generationRequestRef.current===request&&sameDetailScope(request);
  const typedCurrent=request=>typedOwns(request)&&!request.invalidated&&request.inputEpoch===inputScopeRef.current.epoch
    &&request.input.model_settings_version===request.adapter.getStudioModelSettingsVersion?.();
  const invalidateTyped=request=>{
    if(!typedOwns(request)||request.invalidated)return;
    const dispatched=["write","read","uncertain"].includes(request.phase)||Boolean(request.jobId);
    request.controller.abort();setTypedChallenge(null);setTypedPassword("");
    if(dispatched){
      request.controller=new AbortController();request.invalidated=true;request.phase="uncertain";
      setTypedStatus({jobId:request.jobId??null,actions:[]});
      setView(current=>({...current,pending:false,safeError:null}));
    }else{
      generationRequestRef.current=null;setTypedStatus(null);
      setView(current=>({...current,pending:false,safeError:"STUDIO_INPUT_CHANGED"}));
    }
  };
  const appliedInputScopeRef=useRef(inputScopeRef.current.epoch);
  useEffect(()=>{
    if(appliedInputScopeRef.current===inputScopeRef.current.epoch)return;
    appliedInputScopeRef.current=inputScopeRef.current.epoch;
    const request=generationRequestRef.current;
    if(request&&!typedCurrent(request))invalidateTyped(request);
    setView(current=>({...current,sources:state.sources,grounded:state.grounded,locks:state.locks,
      settings:{...current.settings,sourceVersionIds:state.settings?.sourceVersionIds??[]},settingsConfirmed:false,settingsSnapshot:null}));
  },[currentInputKey,currentModelVersion]);
  const finishTyped=request=>{
    if(!typedCurrent(request))return;
    generationRequestRef.current=null;setTypedChallenge(null);setTypedPassword("");setTypedStatus(null);setSettingsRevisionTarget(null);
    setView(current=>({...current,pending:false}));
  };
  const readTypedJob=async(request,manual=false)=>{
    request.phase="read";
    setView(current=>({...current,pending:true,safeError:null}));
    try{
      for(let attempt=0;attempt<(manual?1:120);attempt++){
        if(!manual&&attempt>0)await new Promise(resolve=>setTimeout(resolve,500));
        if(!typedOwns(request))return;
        const job=await request.adapter.getGenerationJob(request.jobId,{signal:request.controller.signal});
        if(!typedOwns(request))return;
        if(!typedCurrent(request)){
          invalidateTyped(request);
          if(!manual){setTypedStatus({jobId:request.jobId,actions:[]});return;}
          if(!["queued","leased","generating"].includes(job.status)){
            generationRequestRef.current=null;
            setTypedStatus({terminal:true,actions:["설정이 변경되기 전 접수된 작업입니다. 저장된 산출물에서 현재 상태를 확인해 주세요."]});
            setView(current=>({...current,pending:false}));return;
          }
        }
        if(["queued","leased","generating"].includes(job.status))continue;
        if(job.status!=="completed"||!job.output){
          setView(current=>({...current,safeError:job.error_code??"STUDIO_OUTPUT_UNAVAILABLE"}));
          setTypedStatus({terminal:true,actions:job.next_actions??[]});
          generationRequestRef.current=null;
          setView(current=>({...current,pending:false}));return;
        }
        const output={...job.output,output_type:job.output_type,title:job.title,
          studio_output_id:job.studio_output_id,output_version_id:job.output_version_id,
          source_version_ids:job.output.source_version_ids?.length?job.output.source_version_ids:job.lineage?.source_version_ids?.length?job.lineage.source_version_ids:[...new Set((job.output.citations??[]).map(item=>item.source_version_id).filter(Boolean))]};
        setView(current=>({...current,outputs:[output,...current.outputs.filter(item=>item.studio_output_id!==output.studio_output_id)],selectedOutputId:request.kind==="regenerate"&&current.selectedOutputId!==request.outputId?current.selectedOutputId:output.studio_output_id,safeError:null}));
        setRevisionContent("");setChangeReason("");finishTyped(request);return;
      }
      request.phase="uncertain";setTypedStatus({jobId:request.jobId,actions:[]});
    }catch{
      if(typedOwns(request)){request.phase="uncertain";setTypedStatus({jobId:request.jobId,actions:[]});}
    }finally{if(typedOwns(request))setView(current=>({...current,pending:false}));}
  };
  const dispatchTyped=async(request,passwordValue)=>{
    if(!typedCurrent(request)){invalidateTyped(request);return;}
    if(request.authorizing)return;
    request.authorizing=true;
    setView(current=>({...current,pending:true,safeError:null}));
    try{
      request.phase="authorize";
      const authorization=await request.adapter.authorizeGeneration({...request.auth,...(passwordValue?{password:passwordValue}:{})},
        {signal:request.controller.signal,idempotencyKey:request.idempotencyKey});
      if(!typedCurrent(request)){invalidateTyped(request);return;}
      setTypedChallenge(null);setTypedPassword("");request.phase="write";
      const input={...request.input,...(authorization.step_up_authorization_id?{step_up_authorization_id:authorization.step_up_authorization_id}:{})};
      const job=request.kind==="generate"
        ?await request.adapter.createGeneration(input,{signal:request.controller.signal,idempotencyKey:request.idempotencyKey})
        :await request.adapter.createStudioVersion(request.outputId,input,{signal:request.controller.signal,idempotencyKey:request.idempotencyKey});
      request.jobId=job.job_id;
      if(!typedCurrent(request)){invalidateTyped(request);if(typedOwns(request))setTypedStatus({jobId:request.jobId,actions:[]});return;}
      if(!request.jobId||typeof request.adapter.getGenerationJob!=="function")throw new Error("STUDIO_JOB_FAILED");
      await readTypedJob(request);
    }catch(error){
      if(!typedCurrent(request))return;
      if(request.phase==="authorize"&&error?.message==="STEP_UP_REQUIRED"){
        setTypedChallenge(request);setTypedPassword("");setView(current=>({...current,pending:false}));return;
      }
      if(request.phase==="write"&&!(error.httpStatus>=400&&error.httpStatus<500)){
        request.phase="uncertain";setTypedStatus({jobId:request.jobId??null,actions:[]});
        setView(current=>({...current,pending:false,safeError:null}));return;
      }
      setView(current=>({...current,safeError:/^[A-Z][A-Z0-9_]+$/u.test(error?.message)?error.message:"STUDIO_CREATE_FAILED"}));
      finishTyped(request);
    }finally{request.authorizing=false;}
  };
  const beginTyped=async(kind,input,outputId=null)=>{
    if(generationRequestRef.current||revisionRequestRef.current||actionRequestRef.current||view.pending)return;
    try{
      if(!adapter?.authorizeGeneration)throw new Error("STUDIO_GENERATION_UNAVAILABLE");
      const version=adapter.getStudioModelSettingsVersion?.();
      if(!Number.isInteger(version)||version<1)throw new Error("NOTEBOOK_MODEL_SELECTION_REQUIRED");
      const frozen={...input,model_settings_version:version};
      const auth=kind==="generate"?{...frozen,operation:"generate"}:{workspace_id:state.workspaceId,operation:"regenerate",output_id:outputId,...frozen};
      const request={kind,input:frozen,auth,outputId,adapter,workspaceId:state.workspaceId,
        controller:new AbortController(),idempotencyKey:secureUuid(),phase:"authorize",inputEpoch:inputScopeRef.current.epoch};
      generationRequestRef.current=request;setTypedStatus(null);await dispatchTyped(request);
    }catch(error){setView(current=>({...current,pending:false,safeError:error.message==="NOTEBOOK_MODEL_SELECTION_REQUIRED"?error.message:"STUDIO_GENERATION_UNAVAILABLE"}));}
  };
  const submit=async()=>{if(canSubmitGeneration(view))await beginTyped("generate",createStudioGenerationInput(view));};
  const prepareSettingsRevision=()=>{
    if(configurationBusy||!selectedOutput||!OUTPUT_TYPES.some(type=>type.id===selectedOutput.output_type&&!type.generationUnavailable))return;
    setSettingsRevisionTarget({outputId:selectedOutput.studio_output_id,versionId:selectedOutput.output_version_id});
    setView(current=>({...selectOutputType({...current,grounded:null,settings:{sourceVersionIds:state.settings?.sourceVersionIds??[]}},selectedOutput.output_type),selectedOutputId:selectedOutput.studio_output_id}));
    setRevisionContent("");setChangeReason("");
  };
  const revise = async (revisionType = "user_edit") => {
    if(revisionType==="settings_change"&&(!settingsRevisionTarget||settingsRevisionTarget.outputId!==selectedOutput?.studio_output_id
      ||settingsRevisionTarget.versionId!==selectedOutput?.output_version_id||!canSubmitGeneration(view)))return;
    if(revisionType!=="user_edit"){
      if(!selectedOutput||!changeReason.trim())return;
      const input={previous_version_id:selectedOutput.output_version_id,revision_type:revisionType,change_reason:changeReason,
        ...(revisionType==="settings_change"?{settings:{purpose:view.settings.purpose,audience:view.settings.audience,language:view.settings.language??"ko",
          source_version_ids:view.settings.sourceVersionIds,ruleset_version_id:view.settings.rulesetVersionId??null,length:view.settings.length,
          structure:view.settings.structure,output_format:view.settings.outputFormat,review_condition:view.settings.reviewCondition}}:{})};
      await beginTyped("regenerate",input,selectedOutput.studio_output_id);return;
    }
    if (revisionRequestRef.current || actionRequestRef.current || view.pending || !selectedOutput || !adapter?.createStudioVersion || !revisionContent.trim() || !changeReason.trim()) return;
    const outputId = selectedOutput.studio_output_id;
    if (detailRequestRef.current?.kind === "read") detailRequestRef.current.controller.abort();
    const request = { kind: "write", outputId, adapter, workspaceId: state.workspaceId, controller: new AbortController() };
    revisionRequestRef.current = request;
    detailRequestRef.current = request;
    cancelStepUp();
    setSavedRevision(null);
    setView((current) => ({ ...current, pending: true, safeError: null }));
    try {
      let version;
      try {
        version = await adapter.createStudioVersion(outputId, {
          previous_version_id: selectedOutput.output_version_id, revision_type: revisionType,
          change_reason: changeReason, content: revisionContent,
          ...(revisionType === "settings_change" ? { settings: {
            purpose: view.settings.purpose, audience: view.settings.audience, language: view.settings.language ?? "ko",
            source_version_ids: view.settings.sourceVersionIds, ruleset_version_id: view.settings.rulesetVersionId ?? null,
            length: view.settings.length, structure: view.settings.structure, output_format: view.settings.outputFormat,
            review_condition: view.settings.reviewCondition,
          } } : {}),
        }, { signal: request.controller.signal });
      } catch (error) {
        if (ownsDetail(request)) setView((current) => ({ ...current, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_VERSION_FAILED" }));
        return;
      }
      if (!sameDetailScope(request)) return;
      // A read opened for this target while its write was pending must not
      // replace the successful write receipt with the previous version.
      if (detailRequestRef.current?.kind === "read" && detailRequestRef.current.outputId === outputId) {
        detailRequestRef.current.controller.abort();
        detailRequestRef.current = request;
      }
      // The write succeeded. Keep its receipt even if the canonical read fails;
      // an old ordinal/history must not describe this new version.
      setView((current) => ({ ...current, outputs: current.outputs.map((output) => {
        if (output.studio_output_id !== outputId) return output;
        const outputMetadata = Object.fromEntries(["studio_output_id", "output_type", "title", "source_id", "source_version_ids", "source_count"]
          .filter((field) => Object.hasOwn(output, field)).map((field) => [field, output[field]]));
        return { ...mergeStudioVersion(outputMetadata, { ...version, approval_required: true }), canonicalPending: true };
      }) }));
      if (ownsDetail(request)) {
        setSavedRevision({ outputId, versionId: version.output_version_id });
        setRevisionContent(""); setChangeReason("");
      }
      if (!adapter.listStudioVersions) return;
      try {
        const versions = await adapter.listStudioVersions(outputId, { signal: request.controller.signal });
        if (!sameDetailScope(request)) return;
        const canonical = versions.find((item) => item.output_version_id === version.output_version_id);
        if (!canonical) throw new Error("STUDIO_LIST_FAILED");
        setView((current) => ({ ...current, pending: ownsDetail(request) ? false : current.pending, outputs: current.outputs.map((output) =>
          output.studio_output_id === outputId && output.output_version_id === version.output_version_id
            ? { ...mergeStudioVersion(output, canonical), version: canonical, versions, canonicalPending: false }
            : output) }));
      } catch {
        if (ownsDetail(request)) setView((current) => ({ ...current, safeError: "STUDIO_LIST_FAILED" }));
      }
    } finally {
      if (revisionRequestRef.current === request) {
        revisionRequestRef.current = null;
        if (sameDetailScope(request)) setView((current) => ({ ...current, pending: ownsDetail(request) ? false : current.pending }));
      }
    }
  };
  const actionOwnsSelection = (request) => ownsDetail(request)
    && detailSelectionRef.current.outputId === request.outputId && detailSelectionRef.current.versionId === request.versionId;
  const markActionUnverified = (request, receipt) => setView((current) => ({ ...current, outputs: current.outputs.map((output) => {
    if (output.studio_output_id !== request.outputId || output.output_version_id !== request.versionId) return output;
    const unknown = { ...output, lifecyclePending: true, actionReceipt: receipt };
    for (const field of ["status", "review_request_id", "approval_request_id", "approval_request_state", "approval_expires_at", "approval_id", "delivery_id", "knowledge_registration_id", "version", "versions"]) delete unknown[field];
    return unknown;
  }) }));
  const performAction = async (request, token) => {
    request.phase = "write";
    if (detailRequestRef.current?.kind === "read") detailRequestRef.current.controller.abort();
    detailRequestRef.current = request;
    setView((current) => ({ ...current, pending: true, safeError: null }));
    try {
      const result = request.action === "withdraw"
        ? await request.adapter.withdrawStudioApprovalRequest(request.payload.approval_request_id, request.versionId, { signal: request.controller.signal, idempotencyKey: request.idempotencyKey })
        : await request.adapter.createStudioAction(request.action, {
        output_version_id: request.versionId, ...request.payload,
        ...(token ? { step_up_authorization: token } : {}),
      }, { signal: request.controller.signal });
      if (!sameDetailScope(request)) return;
      if (!validStudioActionReceipt(result, request)) {
        markActionUnverified(request, null);
        throw new Error("STUDIO_RESPONSE_INVALID");
      }
      if (detailRequestRef.current?.kind === "read" && detailRequestRef.current.outputId === request.outputId
        && detailSelectionRef.current.versionId === request.versionId) {
        detailRequestRef.current.controller.abort();
        detailRequestRef.current = request;
      }
      // A committed receipt is not the authoritative lifecycle state.
      markActionUnverified(request, result);
      try {
        if (!request.adapter.listStudioVersions) throw new Error("STUDIO_LIST_FAILED");
        const versions = await request.adapter.listStudioVersions(request.outputId, { signal: request.controller.signal });
        if (!sameDetailScope(request)) return;
        const canonical = versions.find((version) => version.output_version_id === request.versionId);
        if (!canonical) throw new Error("STUDIO_LIST_FAILED");
        setView((current) => ({ ...current, outputs: current.outputs.map((output) =>
          output.studio_output_id === request.outputId && output.output_version_id === request.versionId
            ? { ...output, ...canonical, version: canonical, versions, lifecyclePending: false }
            : output) }));
        if (actionOwnsSelection(request)) setApprovalRefresh(value => value + 1);
        if (request.action === "knowledge-registrations" && actionOwnsSelection(request)) {
          setKnowledgeNotice("생산 지식이 등록되었습니다. 자료 목록을 새로고침합니다.");
          onKnowledgeRegistered?.();
        }
      } catch {
        if (actionOwnsSelection(request)) setView((current) => ({ ...current, safeError: "STUDIO_LIST_FAILED" }));
      }
    } catch (error) {
      if (actionOwnsSelection(request)) setView((current) => ({ ...current, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_ACTION_FAILED" }));
    } finally {
      if (actionRequestRef.current === request) {
        actionRequestRef.current = null;
        if (sameDetailScope(request)) setView((current) => ({ ...current, pending: actionOwnsSelection(request) ? false : current.pending }));
      }
    }
  };
  const act = async (action, payload = {}) => {
    if (actionRequestRef.current || generationRequestRef.current || revisionRequestRef.current || view.pending
      || !(action === "withdraw" ? adapter?.withdrawStudioApprovalRequest : adapter?.createStudioAction) || !canRequestStudioAction(action, selectedOutput)) return;
    const request = { kind: "action", action, payload: { ...payload }, target: { ...selectedOutput }, phase: "challenge",
      outputId: selectedOutput.studio_output_id, versionId: selectedOutput.output_version_id,
      adapter, workspaceId: state.workspaceId, controller: new AbortController(), idempotencyKey: secureUuid() };
    actionRequestRef.current = request;
    if (["approvals", "deliveries", "knowledge-registrations"].includes(action)) {
      stepUpRef.current = request; setStepUp(request); setPassword("");
      setView((current) => ({ ...current, safeError: null }));
      return;
    }
    await performAction(request);
  };
  const currentLifecycle = request => actionRequestRef.current === request && ownsDetail(request)
    && detailSelectionRef.current.outputId === request.outputId && detailSelectionRef.current.versionId === request.versionId;
  const lifecycleRequest = kind => ({ kind, phase: "challenge", outputId: selectedOutput?.studio_output_id,
    versionId: selectedOutput?.output_version_id, adapter, workspaceId: state.workspaceId,
    controller: new AbortController(), idempotencyKey: secureUuid() });
  const readApprovalNotice = async notice => {
    if (configurationBusy || actionRequestRef.current || generationRequestRef.current || notice.read_at || !adapter?.markStudioApprovalNoticeRead) return;
    const request = lifecycleRequest("notice"); request.phase = "write";
    actionRequestRef.current = request; detailRequestRef.current?.controller.abort(); detailRequestRef.current = request;
    setView(current => ({ ...current, pending: true, safeError: null }));
    try {
      const receipt = await request.adapter.markStudioApprovalNoticeRead(notice.notification_id, notice.version,
        { signal: request.controller.signal, idempotencyKey: request.idempotencyKey });
      if (!currentLifecycle(request)) return;
      setApprovalInfo(current => {
        const items = current.items.map(item => item.notification_id === receipt.notification_id ? { ...item, ...receipt } : item);
        return { ...current, items, unread_count: items.filter(item => item.read_at === null).length };
      });
      const notices = await request.adapter.listStudioApprovalNotices({ signal: request.controller.signal });
      if (currentLifecycle(request)) setApprovalInfo(current => ({ ...current, ...notices, error: false }));
    } catch {
      if (currentLifecycle(request)) setApprovalInfo(current => ({ ...current, error: true }));
    } finally {
      const owns = currentLifecycle(request);
      if (actionRequestRef.current === request) actionRequestRef.current = null;
      if (owns) setView(current => ({ ...current, pending: false }));
    }
  };
  const confirmStepUp = async () => {
    const challenge = stepUpRef.current;
    if (!challenge || challenge.issuing || password.length < 12) return;
    const currentPassword = password;
    setPassword(""); challenge.issuing = true;
    setView((current) => ({ ...current, pending: true, safeError: null }));
    try {
      const issue = challenge.kind === "export" ? challenge.adapter?.issueStudioExportStepUp : challenge.adapter?.issueStudioStepUp;
      if (!issue) throw new Error("STUDIO_STEP_UP_UNAVAILABLE");
      const token = challenge.kind === "export"
        ? await issue(challenge.outputId, challenge.versionId, challenge.format, currentPassword, { signal: challenge.controller.signal })
        : await issue("final_approval_or_knowledge_registration", challenge.versionId, currentPassword, { signal: challenge.controller.signal });
      if (stepUpRef.current !== challenge || !sameDetailScope(challenge)
        || detailSelectionRef.current.outputId !== challenge.outputId || detailSelectionRef.current.versionId !== challenge.versionId) return;
      if (typeof token !== "string" || !token) throw new Error("STEP_UP_REQUIRED");
      stepUpRef.current = null; setStepUp(null);
      if (challenge.kind === "export") await performDownload(challenge, token);
      else await performAction(challenge, token);
    } catch (error) {
      if (stepUpRef.current !== challenge) return;
      challenge.issuing = false;
      setView((current) => ({ ...current, pending: false, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STEP_UP_REQUIRED" }));
    }
  };
  const performDownload = async (request, token) => {
    request.phase = "write";
    if (detailRequestRef.current?.kind === "read") detailRequestRef.current.controller.abort();
    detailRequestRef.current = request;
    try {
      const result = await request.adapter.downloadStudioExport(request.outputId, request.versionId, request.format,
        { stepUpAuthorization: token, idempotencyKey: request.idempotencyKey, signal: request.controller.signal });
      if (!actionOwnsSelection(request) || actionRequestRef.current !== request) return;
      const url = URL.createObjectURL(new Blob([Uint8Array.from(result.bytes)], { type: result.contentType ?? "application/octet-stream" }));
      try {
        const anchor = document.createElement("a"); anchor.href = url; anchor.download = `studio-${request.versionId}.${request.format}`; anchor.click();
      } finally { setTimeout(() => URL.revokeObjectURL(url), 60_000); }
    } catch (error) {
      if (actionOwnsSelection(request)) setView((current) => ({ ...current, safeError: /^[A-Z][A-Z0-9_]+$/u.test(error?.message) ? error.message : "STUDIO_EXPORT_FAILED" }));
    } finally {
      if (actionRequestRef.current === request) {
        actionRequestRef.current = null;
        if (actionOwnsSelection(request)) setView((current) => ({ ...current, pending: false }));
      }
    }
  };
  const download = () => {
    if (actionRequestRef.current || generationRequestRef.current || revisionRequestRef.current || view.pending
      || !selectedOutput || selectedOutput.status !== "approved" || selectedOutput.canonicalPending
      || selectedOutput.lifecyclePending || !adapter?.downloadStudioExport) return;
    try {
      const request = { kind: "export", phase: "challenge", outputId: selectedOutput.studio_output_id,
        versionId: selectedOutput.output_version_id, format: selectedOutput.version?.output_format ?? selectedOutput.output_format ?? "pdf",
        adapter, workspaceId: state.workspaceId, controller: new AbortController(), idempotencyKey: secureUuid() };
      actionRequestRef.current = request; stepUpRef.current = request;
      setStepUp(request); setPassword(""); setView(current => ({ ...current, safeError: null }));
    } catch {
      setView(current => ({ ...current, safeError: "STUDIO_EXPORT_FAILED" }));
    }
  };
  return (
    <div className="product-studio" data-studio-state={view.status}>
      {typedChallenge?<section role="dialog" aria-label="Studio 생성 추가 인증"><p>이 생성 요청에 결속된 추가 인증이 필요합니다.</p>
        <label>생성 인증 비밀번호<input type="password" autoComplete="current-password" maxLength={1024} value={typedPassword} disabled={view.pending} onChange={event=>setTypedPassword(event.currentTarget.value)}/></label>
        <button type="button" disabled={view.pending||typedPassword.length<12} onClick={()=>{const value=typedPassword;setTypedPassword("");void dispatchTyped(typedChallenge,value);}}>생성 인증 확인</button>
        <button type="button" disabled={view.pending} onClick={()=>{typedChallenge.controller.abort();generationRequestRef.current=null;setTypedChallenge(null);setTypedPassword("");setView(current=>({...current,pending:false}));}}>생성 인증 취소</button>
      </section>:null}
      {typedStatus?<section role="status"><p>{typedStatus.terminal?"요청을 완료하지 못했습니다. 필요한 자료와 현재 상태를 확인해 주세요.":"생성 처리 여부를 확인해야 합니다. 새 생성 요청은 잠겨 있습니다."}</p>
        {typedStatus.actions?.length?<ul>{typedStatus.actions.map(action=><li key={action}>{action}</li>)}</ul>:null}
        {typedStatus.jobId?<button type="button" disabled={view.pending} onClick={()=>{const request=generationRequestRef.current;if(request?.jobId)void readTypedJob(request,true);}}>처리 상태 확인</button>:typedStatus.terminal?null:<p>접수 식별자를 받지 못했습니다. 요청을 다시 실행하지 말고 저장된 산출물과 운영상태를 확인해 주세요.</p>}
      </section>:null}
      {knowledgeNotice ? <p role="status">{knowledgeNotice}</p> : null}
      {approvalInfo.error ? <p role="alert">승인 알림을 불러오지 못했습니다. 현재 접근과 연결을 확인해 주세요.</p> : null}
      {adapter?.listStudioApprovalNotices ? <section aria-label="승인 알림">
        <p>승인 알림 · 안 읽음 {approvalInfo.unread_count}</p>
        <ul>{approvalInfo.items.map(notice => <li key={notice.notification_id}>
          {({ due_24h: "승인 기한 임박", expired: "승인 요청 만료", withdrawn: "승인 요청 회수" })[notice.event_kind]} · {notice.scheduled_at}
          <button type="button" disabled={configurationBusy || notice.read_at !== null} onClick={() => void readApprovalNotice(notice)}>{notice.read_at ? "읽음" : "읽음 표시"}</button>
        </li>)}</ul>
      </section> : null}
      {stepUp ? <section role="dialog" aria-label="Studio 추가 인증">
        <p>선택한 Version의 민감 작업을 위해 추가 인증이 필요합니다.</p>
        {view.safeError ? <p role="alert">{safeStudioErrorMessage(view.safeError)}</p> : null}
        <label>추가 인증 비밀번호<input type="password" autoComplete="current-password" maxLength={1024} value={password} disabled={view.pending} onChange={(event) => setPassword(event.currentTarget.value)} /></label>
        <button type="button" disabled={view.pending || password.length < 12} onClick={() => void confirmStepUp()}>추가 인증 확인</button>
        <button type="button" onClick={cancelStepUp}>추가 인증 취소</button>
      </section> : null}
      {view.safeError && (!view.selectedOutputType || selectedOutput) ? <p className="inline-alert" role="alert">{safeStudioErrorMessage(view.safeError)}</p> : null}
      {!view.selectedOutputType ? <div className="studio-home" data-studio-view="home">
        <div className="studio-type-tiles" data-columns="3" aria-label="산출물 유형">
          {OUTPUT_TYPES.map((type) => <button className={`studio-type-tile tile-${TYPE_ICONS[type.id]}${type.generationUnavailable ? " studio-type-future" : ""}`} key={type.id} type="button"
            disabled={type.generationUnavailable === true || configurationBusy}
            onClick={() => { if (!type.generationUnavailable && !generationRequestRef.current && !revisionRequestRef.current && !actionRequestRef.current) setView((current) => current.pending ? current : selectOutputType(current, type.id)); }}>
            <StudioOutputIcon outputType={type.id} />
            <span className="studio-type-label">{type.label}{type.generationUnavailable ? <small>현재 파일 생성 미지원</small> : null}</span>
          </button>)}
        </div>
        <section className="studio-library" aria-labelledby="stored-studio-outputs"><div className="studio-section-heading"><div><span className="section-kicker">LIBRARY</span><h3 id="stored-studio-outputs">저장된 산출물</h3></div><span className="library-count">{view.outputs.length}</span></div>
          {view.outputs.length ? <ul>{view.outputs.map((output) => <li className="studio-library-row" key={output.studio_output_id}><StudioOutputIcon outputType={output.output_type} className="library-type-icon" /><span className="library-row-copy"><small className="library-output-type">{outputTypeLabel(output.output_type)}</small><button type="button" onClick={() => void openOutput(output)}>{output.title}</button><small>Source {output.source_count ?? output.source_version_ids?.length ?? 1} · Version {output.content_version ?? output.output_version_id ?? "-"}</small></span><span className={`output-status status-${output.status ?? "saved"}`}>{outputStatusLabel(output.status)}</span></li>)}</ul> : <div className="studio-empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>아직 저장된 산출물이 없습니다.</strong><small>위 유형을 선택하면 근거가 결속된 산출물을 만들 수 있습니다.</small></div>}
        </section>
      </div> : <section className="studio-config-view" data-studio-view="config" aria-labelledby="studio-config-title">
        <header className="studio-config-header"><button className="studio-back" type="button" disabled={configurationBusy} onClick={() => { if (!generationRequestRef.current && !revisionRequestRef.current && !actionRequestRef.current) {setSettingsRevisionTarget(null);setView((current) => current.pending ? current : ({ ...current, selectedOutputType: null, settingsConfirmed: false, settingsSnapshot: null }));} }}>뒤로</button><div><span className="section-kicker">CREATE</span><h3 id="studio-config-title">{selected?.label} 설정</h3></div></header>
        <div className={`generation-state generation-${generationState}`} data-generation-state={generationState} role={generationState === "failed" ? "alert" : "status"}>
          {generationState === "pending" ? <><span className="state-spinner" aria-hidden="true" /><span><strong>산출물을 생성하고 있습니다.</strong><small>현재 설정과 근거 Snapshot을 안전하게 결속하는 중입니다.</small></span></> : null}
          {generationState === "failed" ? <span><strong>생성을 완료하지 못했습니다.</strong><small>{safeStudioErrorMessage(view.safeError)}</small></span> : null}
          {generationState === "completed" ? <span><strong>산출물이 저장되었습니다.</strong><small>Library에서 상세와 Version 계보를 확인할 수 있습니다.</small></span> : null}
          {generationState === "uncertain" ? <span><strong>생성 처리 상태를 확인해야 합니다.</strong><small>새 요청을 보내지 않고 접수된 작업의 상태를 확인해 주세요.</small></span> : null}
          {generationState === "idle" ? <span><strong>설정을 확인해 주세요.</strong><small>생성 전 목적과 검토 조건을 한 번 더 확인합니다.</small></span> : null}
        </div>
        <form className="studio-config-form" onSubmit={(event) => { event.preventDefault(); void(settingsRevisionTarget?revise("settings_change"):submit()); }}>
          <fieldset className="studio-source-selection">
            <legend>사용 Source</legend>
            {view.sources?.length ? view.sources.map((source) => <label key={`${source.sourceId}:${source.sourceVersionId}`}>
              <input
                type="checkbox"
                checked={view.settings.sourceVersionIds?.includes(source.sourceVersionId) ?? false}
                disabled={!source.ready || fieldDisabled("sourceVersionIds")}
                onChange={(event) => {
                  const current = new Set(view.settings.sourceVersionIds ?? []);
                  if (event.currentTarget.checked) current.add(source.sourceVersionId);
                  else current.delete(source.sourceVersionId);
                  setField("sourceVersionIds", [...current]);
                }}
              />
              <span>{source.filename ?? source.sourceId}</span><small>Version {source.sourceVersionId}</small>
            </label>) : <p className="inline-alert compact">사용 가능한 Source가 없습니다.</p>}
          </fieldset>
          <div className="studio-form-grid">
            <label>목적<input disabled={fieldDisabled("purpose")} placeholder="이 산출물로 해결할 업무" value={view.settings.purpose ?? ""} onChange={(event) => setField("purpose", event.currentTarget.value)} /></label>
            <label>독자<input disabled={fieldDisabled("audience")} placeholder="결과를 검토하거나 사용할 사람" value={view.settings.audience ?? ""} onChange={(event) => setField("audience", event.currentTarget.value)} /></label>
            <label>출력 언어<select disabled={fieldDisabled("language")} value={view.settings.language ?? "ko"} onChange={(event) => setField("language", event.currentTarget.value)}><option value="ko">한국어</option><option value="en">English</option></select></label>
            <label>분량<select disabled={fieldDisabled("length")} value={view.settings.length ?? ""} onChange={(event) => setField("length", event.currentTarget.value)}><option value="">선택</option><option value="short">짧게</option><option value="standard">표준</option><option value="long">상세</option></select></label>
            <label>구성<select disabled={fieldDisabled("structure")} value={view.settings.structure ?? ""} onChange={(event) => setField("structure", event.currentTarget.value)}><option value="">선택</option><option value="summary-body-conclusion">요약 · 본문 · 결론</option><option value="executive-summary">핵심 요약 중심</option><option value="letter">업무 문서형</option></select></label>
            <label>출력 형식<select disabled={fieldDisabled("outputFormat")} value={view.settings.outputFormat ?? ""} onChange={(event) => setField("outputFormat", event.currentTarget.value)}><option value="">선택</option>{selected?.formats.map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></label>
            <label>검토 조건<select disabled={fieldDisabled("reviewCondition")} value={view.settings.reviewCondition ?? ""} onChange={(event) => setField("reviewCondition", event.currentTarget.value)}><option value="">선택</option><option value="review_required">검토 필수</option><option value="approval_required">승인 필수</option></select></label>
          </div>
          <div className="studio-setting-summary"><div><span>생성 방식</span><strong>명시 선택한 모델로 자료 기반 생성</strong><small>선택 Source만 근거로 사용하며 모델 호출이 발생합니다.</small></div><div><span>정책 요약</span><strong>{view.locks.length ? `조직 정책 ${view.locks.length}개 적용` : "Workspace 정책 적용"}</strong><small>정책은 생성 시점에 다시 확인되며 이 화면에서 해제할 수 없습니다.</small>
            {view.locks.length ? <ul>{view.locks.map((lock) => <li key={lock.field}>{({ reviewCondition: "검토 조건", rulesetVersionId: "RuleSet", dataArea: "데이터 영역", authorityPolicy: "권위 정책", weightProfile: "가중치", egressPolicy: "외부 전송 정책" })[lock.field] ?? lock.field}: {lock.value == null ? "미결속" : String(lock.value)} · {lock.reason}</li>)}</ul> : null}
          </div></div>
          <div className="studio-config-actions"><button className="secondary-button" type="button" onClick={() => { if (!generationRequestRef.current && !revisionRequestRef.current && !actionRequestRef.current) setView((current) => canConfirmGenerationSettings(current) ? confirmGenerationSettings(current) : current); }} disabled={configurationBusy || !canConfirmGenerationSettings(view)}>설정 확인</button><button className="primary-button" type="submit" disabled={configurationBusy || !canSubmitGeneration(view)}>{settingsRevisionTarget?"설정 변경 생성":"생성"}</button></div>
        </form>
        <div className="studio-config-library"><span className="section-kicker">LIBRARY</span><h3>저장된 산출물</h3><small>{view.outputs.length ? `${view.outputs.length}개의 산출물이 저장되어 있습니다.` : "생성 완료 후 여기에 저장됩니다."}</small></div>
      </section>}
      {selectedOutput ? <section className="studio-output-detail" aria-label="선택 산출물 상세">
        {revisionSaved ? <p role="status">새 Version이 저장되었습니다. ({savedRevision.versionId})</p> : null}
        {selectedOutput.actionReceipt?.output_version_id === selectedOutput.output_version_id ? <p role="status">요청이 접수되었습니다. ({selectedOutput.actionReceipt.record_id})</p> : null}
        {selectedOutput.lifecyclePending ? <p role="status">현재 Version의 처리 상태를 확인하지 못했습니다. 상세를 다시 열어 확인해 주세요.</p> : null}
        {selectedOutput.canonicalPending ? <p role="status">저장은 완료되었으나 새 Version의 정본 조회가 아직 확인되지 않았습니다.</p> : null}
        <header className="studio-output-detail-header"><div><span className="section-kicker">{outputTypeLabel(selectedOutput.output_type)}</span><h3>{selectedOutput.title}</h3></div><span className={`output-status status-${selectedOutput.status ?? "saved"}`}>{selectedOutput.lifecyclePending ? "상태 미확인" : outputStatusLabel(selectedOutput.status)}</span></header>
        <StudioOutputContent output={selectedOutput} />
        {selectedOutput.approval_request_id ? <p>승인 요청 {selectedOutput.approval_request_id} · {({ pending: "대기 중", approved: "승인됨", rejected: "반려됨", withdrawn: "회수됨", expired: "만료됨" })[selectedOutput.approval_request_state] ?? "상태 미확인"} · 기한 {selectedOutput.approval_expires_at ?? "미확인"}</p> : null}
        <p>Version {selectedOutput.content_version ?? selectedOutput.output_version_id} · 근거 {Array.isArray(selectedOutput.citations) ? selectedOutput.citations.length : selectedOutput.citations ?? (selectedOutput.canonicalPending ? "미확인" : 0)} · 설정 {selectedOutput.settings_snapshot_id ?? (selectedOutput.canonicalPending ? "미확인" : "-")}</p>
        {selectedOutput.versions?.length ? <section className="studio-version-history" aria-label="Version 이력"><h4>Version 이력</h4><ol>{selectedOutput.versions.map((version) => <li key={version.output_version_id}><strong>Version {version.content_version}</strong><span>{outputStatusLabel(version.status)} · {version.change_reason}</span></li>)}</ol></section> : null}
        {Array.isArray(selectedOutput.citations) && selectedOutput.citations.length ? <section className="studio-citation-list" aria-label="근거 Citation"><h4>근거 Citation</h4><ul>{selectedOutput.citations.map((citation) => {
          const cell = citation.locator?.kind === "sheet_cell" ? parseSheetCell(citation.locator.value) : null;
          const block = citation.locator?.kind === "docx_block" ? parseDocxBlock(citation.locator.value) : null;
          const audio=citation.locator?.kind === "audio_segment" ? parseAudioSegment(citation.locator.value) : null;
          return <li key={citation.citation_id}>{citation.origin === "daon_knowledge" ? "Daon 생성 지식" : "원본 지식"} · {audio?`음성 ${audio.start_ms}–${audio.end_ms}ms`:citation.locator?.kind === "pptx_block" ? pptxBlockLabel(citation.locator.value) : block ? `문단·표 ${block.block} · ${block.start}–${block.end}` : cell ? `${cell.sheet} · ${cell.range}` : citation.locator?.kind === "page" ? `${citation.locator.value}쪽` : citation.locator?.value}</li>;
        })}</ul></section> : null}
        <label>변경 사유<input disabled={configurationBusy} value={changeReason} onChange={(event) => { if (!view.pending && !revisionRequestRef.current && !actionRequestRef.current) setChangeReason(event.currentTarget.value); }} /></label>
        <label>편집 내용<textarea disabled={configurationBusy} value={revisionContent} onChange={(event) => { if (!view.pending && !revisionRequestRef.current && !actionRequestRef.current) setRevisionContent(event.currentTarget.value); }} /></label>
        <button type="button" disabled={configurationBusy || !changeReason.trim() || !revisionContent.trim()} onClick={() => void revise("user_edit")}>편집 새 Version</button>
        <button type="button" disabled={configurationBusy || !changeReason.trim()} onClick={() => void revise("ai_regeneration")}>AI 재생성 새 Version</button>
        <button type="button" disabled={configurationBusy} onClick={prepareSettingsRevision}>설정 변경 준비</button>
        <button type="button" disabled={configurationBusy || !changeReason.trim() || !settingsRevisionTarget || !canSubmitGeneration(view)} onClick={() => void revise("settings_change")}>설정 변경 새 Version</button>
        <button type="button" disabled={actionDisabled("reviews")} onClick={() => void act("reviews")}>검토 요청</button>
        <button type="button" disabled={actionDisabled("approval-requests")} onClick={() => void act("approval-requests", { review_request_id: selectedOutput.review_request_id })}>승인 요청</button>
        <button type="button" disabled={actionDisabled("approvals")} onClick={() => void act("approvals", { approval_request_id: selectedOutput.approval_request_id, decision: "approved" })}>승인</button>
        <button type="button" disabled={actionDisabled("approvals")} onClick={() => void act("approvals", { approval_request_id: selectedOutput.approval_request_id, decision: "rejected" })}>수정 요청</button>
        <button type="button" disabled={actionDisabled("withdraw") || !adapter?.withdrawStudioApprovalRequest} onClick={() => void act("withdraw", { approval_request_id: selectedOutput.approval_request_id })}>승인 요청 회수</button>
        <button type="button" disabled={configurationBusy || selectedOutput.status !== "approved" || selectedOutput.canonicalPending || selectedOutput.lifecyclePending} onClick={() => download()}>내보내기</button>
        <button type="button" disabled={actionDisabled("deliveries")} onClick={() => void act("deliveries", { approval_id: selectedOutput.approval_id, recipient: "workspace_recipient" })}>전달 기록 등록</button>
        <small>전달 기록만 등록하며 실제 외부 송신은 없습니다.{selectedOutput.delivery_id ? " 전달 기록이 이미 등록되었습니다." : ""}</small>
        <button type="button" disabled={actionDisabled("knowledge-registrations")} onClick={() => void act("knowledge-registrations", { explicit: true })}>생산 지식 등록</button>
      </section> : null}
    </div>
  );
}
