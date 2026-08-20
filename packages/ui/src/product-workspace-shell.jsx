"use client";

import { useEffect, useRef, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import { canCreateGroundedReport, createProductWorkspaceState, normalizeProductWorkspaceState, projectQuestionFailureState } from "./product-workspace-model.js";
import { createProductStudioState } from "./product-studio-model.js";
import { ProductStudioPane } from "./product-studio-pane.jsx";
import { isGeneralConversationIntent } from "./conversation-intent.js";
import "./workspace.css";

const STATE_LABELS = Object.freeze({
  loading: "Workspace를 불러오는 중입니다.",
  empty: "표시할 Workspace 자료가 없습니다.",
  ready: "Workspace가 준비되었습니다.",
  error: "Workspace 처리 중 안전 오류가 발생했습니다.",
  forbidden: "현재 권한으로 Workspace를 열 수 없습니다.",
  unavailable: "실제 Workspace 연결이 아직 준비되지 않았습니다."
});

const PANE_ICONS = Object.freeze({
  "product-pane-sources": "source", "product-pane-conversation": "conversation", "product-pane-studio": "studio",
});

function readableManualLine(line) {
  return line
    .replace(/!\[([^\]]+)\]\([^)]*\)/gu, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/gu, "$1")
    .replace(/\*\*([^*]+)\*\*/gu, "$1")
    .replace(/`([^`]+)`/gu, "$1");
}

function ManualMarkdown({ text }) {
  return <div className="manual-reader-body">
    {text.split("\n").map((line, index) => {
      const value = readableManualLine(line.trim());
      if (!value) return <span className="manual-reader-space" aria-hidden="true" key={index} />;
      if (/^!\[/u.test(line.trim())) return <p className="manual-reader-caption" key={index}>{value}</p>;
      const heading = /^(#{1,3})\s+(.+)$/u.exec(value);
      if (heading) return heading[1].length === 1 ? <h3 key={index}>{heading[2]}</h3> : <h4 key={index}>{heading[2]}</h4>;
      const ordered = /^\d+\.\s+(.+)$/u.exec(value);
      if (ordered) return <p className="manual-reader-list" key={index}><span>{value.slice(0, value.indexOf(" "))}</span>{ordered[1]}</p>;
      const bullet = /^-\s+(.+)$/u.exec(value);
      if (bullet) return <p className="manual-reader-list" key={index}><span>•</span>{bullet[1]}</p>;
      if (value.startsWith("> ")) return <aside key={index}>{value.slice(2)}</aside>;
      return <p key={index}>{value}</p>;
    })}
  </div>;
}

const SOURCE_STATE_LABELS = Object.freeze({
  registered: "등록됨",
  security_check: "보안 확인 중",
  processing: "처리 중",
  indexing: "색인 중",
  ready: "사용 가능",
  waiting_model: "모델 대기",
  partial_understanding: "부분 이해",
  needs_review: "검토 필요",
  failed: "처리 실패",
  expired: "만료됨",
  disabled: "사용 중지",
  deleting: "삭제 중",
  deleted: "삭제됨",
});

const KNOWLEDGE_PRODUCER_LABELS = Object.freeze({
  daon2: "Daon 2",
  daon2_5: "Daon 2.5",
  daon3: "Daon 3",
});

const OPERATIONS_LABELS = Object.freeze({
  provider: "Provider", api: "API", storage: "Storage", sync: "Sync", queue: "Queue",
});
const OPERATIONS_MESSAGES = Object.freeze({
  PROVIDER_READY: "LLM Provider와 선택 모델이 준비되었습니다.",
  PROVIDER_CONFIGURATION_REQUIRED: "LLM Provider 또는 선택 모델 설정이 필요합니다.",
  API_READY: "Workspace API가 정상입니다.", API_UNAVAILABLE: "Workspace API 상태를 확인할 수 없습니다.",
  STORAGE_READY: "Database와 Object Storage가 정상입니다.", STORAGE_UNAVAILABLE: "저장소 상태 확인이 필요합니다.",
  SYNC_READY: "대기 중인 동기화가 없습니다.", SYNC_PENDING: "승인 또는 전송을 기다리는 동기화가 있습니다.",
  QUEUE_READY: "처리 Queue가 정상입니다.", QUEUE_ATTENTION_REQUIRED: "대기 또는 실패한 처리 작업이 있습니다.",
});
const OUTPUT_SETTING_LABELS = Object.freeze({
  evidence_report: "근거 기반 보고서",
  compliance_checklist: "제약·준수 점검표",
  comparison_table: "비교·데이터 표",
  knowledge_graph: "지식 구조도",
  business_draft: "업무 문서 초안",
});
const OUTPUT_SETTING_FORMATS = Object.freeze({
  evidence_report: Object.freeze(["pdf", "docx"]),
  compliance_checklist: Object.freeze(["xlsx", "csv", "pdf"]),
  comparison_table: Object.freeze(["xlsx", "csv", "pdf"]),
  knowledge_graph: Object.freeze(["json", "svg", "png"]),
  business_draft: Object.freeze(["docx", "pdf"]),
});
const EGRESS_MODE_LABELS = Object.freeze({ deny_external: "외부 전송 차단", allow_approved_external: "승인된 외부 전송" });
const EGRESS_APPROVER_LABELS = Object.freeze({ workspace_manager: "Workspace 관리자", organization_admin: "조직 관리자" });
const LICENSE_RESOURCE_LABELS = Object.freeze({
  users: "사용자", notebooks: "Notebook", storage_bytes: "저장공간",
  generation_runs: "생성 실행", source_versions: "Source Version", studio_outputs: "Studio 산출물",
});
const LICENSE_STATUS_LABELS = Object.freeze({
  not_configured: "미적용", active: "정상", expiring_soon: "30일 이내 만료",
  expired: "만료", limit_reached: "한도 도달",
});

function SafePane({ id, title, description, children }) {
  return (
    <div className="pane-slot">
      <section id={id} className="workspace-pane" aria-labelledby={`${id}-title`}>
        <div className="pane-heading">
          <h2 id={`${id}-title`}><span className={`pane-icon icon-${PANE_ICONS[id]}`} aria-hidden="true" />{title}</h2>
          <button className="info-button" type="button" title={description} aria-label={`${title} 설명`}>i</button>
        </div>
        {children}
      </section>
    </div>
  );
}

function trapDialogFocus(event, close) {
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...event.currentTarget.querySelectorAll("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")];
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function safeErrorCode(error, fallback) {
  return typeof error?.message === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(error.message)
    ? error.message
    : fallback;
}

const SAFE_DTO_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const PROCESSING_STATUS_KEYS = Object.freeze([
  "processing_run_id", "source_id", "source_version_id", "processing_state",
  "source_state", "job_state", "safe_error_code"
]);
const PENDING_SOURCE_STATES = new Set(["registered", "security_check", "processing", "indexing", "ready"]);
const PENDING_PROCESSING_STATES = new Set([
  "accepted", "queued", "processing", "vision_llm_understanding", "audio_llm_understanding",
  "speech_to_text", "llm_semantic_understanding", "parser_ocr_validation",
  "transcript_timecode_validation", "evidence_reconciliation", "completed"
]);
const PENDING_JOB_STATES = new Set(["pending", "queued", "leased", "processing", "retry_wait", "completed"]);
const TERMINAL_SOURCE_FAILURES = new Set(["waiting_model", "partial_understanding", "needs_review", "failed", "disabled", "expired", "deleting", "deleted"]);
const TERMINAL_PROCESSING_FAILURES = new Set(["failed", "policy_blocked"]);
const TERMINAL_JOB_FAILURES = new Set(["dead_letter"]);
const DEFAULT_PROCESSING_POLL_OPTIONS = Object.freeze({
  deadlineMs: 150_000,
  intervalMs: 1_000,
  statusRequestTimeoutMs: 10_000
});

function isSafeDtoId(value) {
  return typeof value === "string" && SAFE_DTO_ID.test(value);
}

function hasExactKeys(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function abortError() {
  return Object.assign(new Error("PROCESSING_POLL_ABORTED"), { name: "AbortError" });
}

function waitForProcessingPoll(intervalMs, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timeoutId);
      reject(abortError());
    };
    const timeoutId = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, intervalMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function monotonicNow() {
  return globalThis.performance?.now?.() ?? Date.now();
}

function linkedAbortSignal(signals) {
  const controller = new AbortController();
  const listeners = [];
  const abort = (signal) => {
    if (!controller.signal.aborted) controller.abort(signal.reason ?? abortError());
  };
  for (const signal of signals) {
    if (signal.aborted) {
      abort(signal);
      break;
    }
    const listener = () => abort(signal);
    signal.addEventListener("abort", listener, { once: true });
    listeners.push([signal, listener]);
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      for (const [signal, listener] of listeners) signal.removeEventListener("abort", listener);
    }
  };
}

function awaitAbortable(promise, signal) {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abortError());
    signal.addEventListener("abort", onAbort, { once: true });
    Promise.resolve(promise).then(
      (value) => { signal.removeEventListener("abort", onAbort); resolve(value); },
      (error) => { signal.removeEventListener("abort", onAbort); reject(error); }
    );
  });
}

function normalizeProcessingPollOptions(options) {
  return {
    deadlineMs: Number.isSafeInteger(options?.deadlineMs) && options.deadlineMs >= 1 && options.deadlineMs <= 150_000
      ? options.deadlineMs
      : DEFAULT_PROCESSING_POLL_OPTIONS.deadlineMs,
    intervalMs: Number.isSafeInteger(options?.intervalMs) && options.intervalMs >= 0 && options.intervalMs <= 60_000
      ? options.intervalMs
      : DEFAULT_PROCESSING_POLL_OPTIONS.intervalMs,
    statusRequestTimeoutMs: Number.isSafeInteger(options?.statusRequestTimeoutMs)
      && options.statusRequestTimeoutMs >= 1
      && options.statusRequestTimeoutMs <= 10_000
      ? options.statusRequestTimeoutMs
      : DEFAULT_PROCESSING_POLL_OPTIONS.statusRequestTimeoutMs,
    now: typeof options?.now === "function" ? options.now : monotonicNow,
    wait: typeof options?.wait === "function" ? options.wait : waitForProcessingPoll
  };
}

function processingDisposition(status, submission) {
  const validShape = hasExactKeys(status, PROCESSING_STATUS_KEYS)
    && isSafeDtoId(status.processing_run_id)
    && isSafeDtoId(status.source_id)
    && isSafeDtoId(status.source_version_id)
    && typeof status.processing_state === "string"
    && typeof status.source_state === "string"
    && (status.job_state === null || typeof status.job_state === "string")
    && (status.safe_error_code === null || (
      typeof status.safe_error_code === "string" && /^[A-Z][A-Z0-9_]{2,63}$/u.test(status.safe_error_code)
    ));
  if (!validShape) throw new Error("PROCESSING_STATUS_INVALID");
  if (
    status.processing_run_id !== submission.processing_run_id
    || status.source_id !== submission.source_id
    || status.source_version_id !== submission.source_version_id
  ) throw new Error("PROCESSING_LINEAGE_MISMATCH");
  if (
    status.source_state === "ready"
    && status.processing_state === "completed"
    && status.job_state === "completed"
    && status.safe_error_code === null
  ) return "ready";
  if (
    status.safe_error_code
    || TERMINAL_SOURCE_FAILURES.has(status.source_state)
    || TERMINAL_PROCESSING_FAILURES.has(status.processing_state)
    || TERMINAL_JOB_FAILURES.has(status.job_state)
  ) throw new Error(status.safe_error_code ?? "PROCESSING_STATUS_UNAVAILABLE");
  if (
    !PENDING_SOURCE_STATES.has(status.source_state)
    || !PENDING_PROCESSING_STATES.has(status.processing_state)
    || (status.job_state !== null && !PENDING_JOB_STATES.has(status.job_state))
  ) throw new Error("PROCESSING_STATUS_INVALID");
  return "pending";
}

export function projectSafeQuestionAnswer(answer, workspaceId, citationUrl, selectedSource, selectedKnowledgeId = null) {
  const validAnswer = hasExactKeys(answer, ["run_id", "run_result_id", "answer", "insufficient", "citations"])
    && isSafeDtoId(answer.run_id)
    && isSafeDtoId(answer.run_result_id)
    && typeof answer.answer === "string"
    && answer.answer.length >= 1
    && answer.answer.length <= 8_000
    && typeof answer.insufficient === "boolean"
    && Array.isArray(answer.citations)
    && answer.citations.length <= 10;
  if (!validAnswer) throw new Error("QUESTION_RESPONSE_INVALID");
  const citations = answer.citations.map((citation) => {
    const validLocator = hasExactKeys(citation?.locator, ["kind", "value"])
      && typeof citation.locator.kind === "string"
      && /^[a-z][a-z0-9_]{0,31}$/u.test(citation.locator.kind)
      && typeof citation.locator.value === "string"
      && citation.locator.value.length >= 1
      && citation.locator.value.length <= 255
      && !/[\u0000-\u001f\u007f]/u.test(citation.locator.value);
    const validCitation = hasExactKeys(citation, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
      && isSafeDtoId(citation.citation_id)
      && isSafeDtoId(citation.source_id)
      && isSafeDtoId(citation.source_version_id)
      && isSafeDtoId(citation.evidence_span_id)
      && Number.isSafeInteger(citation.page)
      && citation.page >= 1
      && validLocator
      && (citation.locator.kind !== "page" || citation.locator.value === String(citation.page))
      && (
        citation.origin === "raw_source"
          ? citation.source_id === selectedSource?.sourceId
            && citation.source_version_id === selectedSource?.sourceVersionId
            && citation.context_item_id === selectedSource?.sourceId
          : citation.origin === "daon_knowledge"
            ? citation.context_item_id === selectedKnowledgeId
          : false
      );
    if (!validCitation) throw new Error("QUESTION_RESPONSE_INVALID");
    let contentUrl;
    try {
      contentUrl = citationUrl(citation);
    } catch {
      throw new Error("QUESTION_RESPONSE_INVALID");
    }
    const expectedBase = `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/citations/${encodeURIComponent(citation.citation_id)}/content`;
    const expectedUrl = citation.locator.kind === "page" ? `${expectedBase}#page=${citation.page}` : expectedBase;
    const expectedNativeUrl = `#/citations/${encodeURIComponent(citation.citation_id)}?page=${citation.page}`;
    if (contentUrl !== expectedUrl && contentUrl !== expectedNativeUrl) throw new Error("QUESTION_RESPONSE_INVALID");
    return { ...citation, content_url: contentUrl };
  });
  return { ...answer, citations };
}

export function buildQuestionKnowledgeContext(selectedSource, selectedKnowledgeId) {
  if (!selectedSource && !selectedKnowledgeId) throw new Error("QUESTION_CONTEXT_INVALID");
  return {
    mode: selectedKnowledgeId && selectedSource
      ? "mixed"
      : selectedKnowledgeId ? "daon_priority" : "raw_only",
    resources: [
      ...(selectedKnowledgeId ? [{
        resourceKind: "knowledge_package", resourceId: selectedKnowledgeId,
      }] : []),
      ...(selectedSource ? [{
        resourceKind: "source", resourceId: selectedSource.sourceId,
        versionId: selectedSource.sourceVersionId,
      }] : []),
    ],
  };
}

export async function submitGroundedReport({ adapter, state, title, purpose, idempotencyKey, signal }) {
  const reportTitle = typeof title === "string" ? title.trim() : "";
  const reportPurpose = typeof purpose === "string" ? purpose.trim() : "";
  if (
    !adapter || typeof adapter.createReport !== "function" || typeof adapter.listStudioOutputs !== "function"
    || !canCreateGroundedReport(state) || !reportTitle || !reportPurpose
  ) return { submitted: false, outputs: state?.studioOutputs ?? [] };
  await adapter.createReport({
    source_id: state.selectedSource.sourceId,
    source_version_id: state.selectedSource.sourceVersionId,
    run_id: state.answer.run_id,
    run_result_id: state.answer.run_result_id,
    title: reportTitle,
    purpose: reportPurpose
  }, { idempotencyKey, signal });
  return { submitted: true, outputs: await adapter.listStudioOutputs({ signal }) };
}

export async function submitGroundedReportForm(event, {
  adapter, state, title, purpose, idempotencyRef, uuid = () => crypto.randomUUID(), signal,
}) {
  event?.preventDefault();
  const normalized = {
    source_id: state?.selectedSource?.sourceId,
    source_version_id: state?.selectedSource?.sourceVersionId,
    run_id: state?.answer?.run_id,
    run_result_id: state?.answer?.run_result_id,
    title: typeof title === "string" ? title.trim() : "",
    purpose: typeof purpose === "string" ? purpose.trim() : ""
  };
  if (!canCreateGroundedReport(state) || !normalized.title || !normalized.purpose) {
    return { submitted: false, outputs: state?.studioOutputs ?? [] };
  }
  const fingerprint = JSON.stringify(normalized);
  let key = idempotencyRef?.current?.fingerprint === fingerprint
    ? idempotencyRef.current.key
    : `report-${uuid()}`;
  if (typeof key !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/u.test(key)) {
    throw new Error("STUDIO_INPUT_INVALID");
  }
  if (idempotencyRef) idempotencyRef.current = { fingerprint, key };
  return submitGroundedReport({ adapter, state, title: normalized.title, purpose: normalized.purpose, idempotencyKey: key, signal });
}

export async function openNativeCitation(event, { adapter, citation, signal, registerObjectUrl, unregisterObjectUrl, openWindow = globalThis.open, objectUrl = globalThis.URL, BlobType = globalThis.Blob, schedule = globalThis.setTimeout }) {
  if (typeof adapter?.citationContent !== "function") return false;
  event?.preventDefault();
  const result = await adapter.citationContent(citation, { signal });
  if (signal?.aborted) return false;
  const bytes = result?.bytes;
  if (
    !hasExactKeys(result, ["content_type", "page", "bytes"])
    || result.content_type !== "application/pdf"
    || result.page !== citation?.page
    || !Array.isArray(bytes) || bytes.length < 5 || bytes.length > 25 * 1024 * 1024
    || bytes.slice(0, 5).some((byte, index) => byte !== [0x25, 0x50, 0x44, 0x46, 0x2d][index])
    || typeof objectUrl?.createObjectURL !== "function" || typeof objectUrl?.revokeObjectURL !== "function"
    || typeof BlobType !== "function" || typeof openWindow !== "function"
  ) throw new Error("CITATION_RESPONSE_INVALID");
  const url = objectUrl.createObjectURL(new BlobType([Uint8Array.from(bytes)], { type: "application/pdf" }));
  registerObjectUrl?.(url);
  if (signal?.aborted) {
    objectUrl.revokeObjectURL(url);
    unregisterObjectUrl?.(url);
    return false;
  }
  try {
    openWindow(`${url}#page=${result.page}`, "_blank", "noopener,noreferrer");
  } finally {
    schedule(() => {
      objectUrl.revokeObjectURL(url);
      unregisterObjectUrl?.(url);
    }, 60_000);
  }
  return true;
}

export function ProductWorkspaceShell({ workspaceId, state = createProductWorkspaceState(), adapter = null, processingPollOptions = null, desktopOfflineStudio = null, providerSettings = null, onLogout = null }) {
  const [viewState, setViewState] = useState(() => normalizeProductWorkspaceState(state));
  const [processing, setProcessing] = useState(null);
  const [question, setQuestion] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const [reportPurpose, setReportPurpose] = useState("");
  const [reportPending, setReportPending] = useState(false);
  const [settingsMenuOpen, setSettingsMenuOpen] = useState(false);
  const [modalView, setModalView] = useState(null);
  const [loadRevision, setLoadRevision] = useState(0);
  const [knowledgePackages, setKnowledgePackages] = useState([]);
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState(null);
  const [knowledgeSafeError, setKnowledgeSafeError] = useState(null);
  const [operationsStatus, setOperationsStatus] = useState(null);
  const [operationsPending, setOperationsPending] = useState(false);
  const [operationsSafeError, setOperationsSafeError] = useState(null);
  const [outputSettings, setOutputSettings] = useState(null);
  const [outputDraft, setOutputDraft] = useState(null);
  const [outputSettingsPending, setOutputSettingsPending] = useState(false);
  const [outputSettingsSafeError, setOutputSettingsSafeError] = useState(null);
  const [outputSettingsDirty, setOutputSettingsDirty] = useState(false);
  const [outputCloseGuard, setOutputCloseGuard] = useState(false);
  const [syncOperations, setSyncOperations] = useState([]);
  const [syncPending, setSyncPending] = useState(false);
  const [syncSafeError, setSyncSafeError] = useState(null);
  const [syncSelections, setSyncSelections] = useState({});
  const [egressPolicy, setEgressPolicy] = useState(null);
  const [egressPolicyPending, setEgressPolicyPending] = useState(false);
  const [egressPolicySafeError, setEgressPolicySafeError] = useState(null);
  const [licenseView, setLicenseView] = useState(null);
  const [licensePending, setLicensePending] = useState(false);
  const [licenseSafeError, setLicenseSafeError] = useState(null);
  const [manualManifest, setManualManifest] = useState(null);
  const [manualDocument, setManualDocument] = useState(null);
  const [manualSearch, setManualSearch] = useState("");
  const [manualPending, setManualPending] = useState(false);
  const [manualSafeError, setManualSafeError] = useState(null);
  const pollControllerRef = useRef(null);
  const reportIdempotencyRef = useRef(null);
  const lifetimeControllerRef = useRef(null);
  const workspaceMountedRef = useRef(false);
  const citationUrlsRef = useRef(new Set());
  const questionPasswordRef = useRef(null);
  const questionEpochRef = useRef(0);
  const modalRef = useRef(null);
  const modalOpenerRef = useRef(null);
  const settingsButtonRef = useRef(null);
  const syncPasswordRef = useRef(null);
  const licenseFileRef = useRef(null);
  const licensePasswordRef = useRef(null);

  useEffect(() => {
    if (!modalView) return undefined;
    const first = modalRef.current?.querySelector?.("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])");
    first?.focus();
    return undefined;
  }, [modalView]);

  useEffect(() => {
    if (!adapter || typeof adapter.listSources !== "function" || !workspaceId) return undefined;
    questionEpochRef.current += 1;
    const controller = new AbortController();
    Promise.allSettled([
      adapter.listSources({ signal: controller.signal }),
      (typeof adapter.listKnowledgePackages === "function"
        ? adapter.listKnowledgePackages({ signal: controller.signal })
        : Promise.resolve([])),
      (typeof adapter.listStudioOutputs === "function"
        ? (typeof adapter.listProductStudioOutputs === "function"
          ? adapter.listProductStudioOutputs({ signal: controller.signal })
          : adapter.listStudioOutputs({ signal: controller.signal }))
        : Promise.resolve([])),
      (typeof adapter.loadNotebookConversation === "function"
        ? adapter.loadNotebookConversation({ signal: controller.signal })
        : Promise.resolve(null)),
    ]).then(([sourceResult, knowledgeResult, studioResult, conversationResult]) => {
      if (controller.signal.aborted) return;
      const sources = sourceResult.status === "fulfilled" ? sourceResult.value : [];
      const projected = sources.map((source) => ({
        sourceId: source.source_id,
        sourceVersionId: source.source_version_id,
        filename: source.filename,
        sourceState: source.source_state,
        processingState: source.processing_state,
        jobState: source.job_state,
        ready: source.source_state === "ready"
          && source.processing_state === "completed"
          && source.job_state === "completed"
      }));
      const selectedSource = projected.find((source) => source.ready) ?? null;
      const projectedKnowledge = knowledgeResult.status === "fulfilled"
        ? knowledgeResult.value.map((item) => ({
          packageId: item.package_id,
          producerLabel: KNOWLEDGE_PRODUCER_LABELS[item.producer] ?? "Daon",
          producerVersion: item.producer_version,
          authorityLabel: item.authority === "approved" ? "승인" : "검증 필요",
          registrationLabel: item.registration_state === "registered" ? "등록됨" : "등록 확인 필요",
        }))
        : [];
      setKnowledgePackages(projectedKnowledge);
      setSelectedKnowledgeId((current) => projectedKnowledge.some((item) => item.packageId === current) ? current : null);
      setKnowledgeSafeError(knowledgeResult.status === "fulfilled"
        ? null
        : safeErrorCode(knowledgeResult.reason, "KNOWLEDGE_PACKAGE_LIST_FAILED"));
      const studioValue = studioResult.status === "fulfilled" ? studioResult.value : [];
      const studioOutputs = Array.isArray(studioValue) ? studioValue : studioValue?.outputs ?? [];
      const studioLocks = Array.isArray(studioValue?.studioLocks) ? studioValue.studioLocks : [];
      setViewState({
        ...createProductWorkspaceState({
          status: sourceResult.status === "rejected"
            ? "error" : projected.length || projectedKnowledge.length ? "ready" : "empty",
          safeError: sourceResult.status === "fulfilled"
            ? null : safeErrorCode(sourceResult.reason, "SOURCE_LIST_FAILED"),
        }),
        sources: projected, selectedSource, studioOutputs, studioLocks,
        answer: conversationResult.status === "fulfilled" ? conversationResult.value : null,
        conversationSafeError: conversationResult.status === "fulfilled"
          ? null : safeErrorCode(conversationResult.reason, "CONVERSATION_LIST_FAILED"),
        studioStatus: studioResult.status === "fulfilled" ? "ready" : "unavailable",
        studioSafeError: studioResult.status === "fulfilled"
          ? null
          : safeErrorCode(studioResult.reason, "STUDIO_LIST_FAILED"),
      });
    }).catch((error) => {
      if (!controller.signal.aborted) {
        setKnowledgePackages([]);
        setSelectedKnowledgeId(null);
        setViewState(createProductWorkspaceState({ status: "error", safeError: safeErrorCode(error, "SOURCE_LIST_FAILED") }));
      }
    });
    return () => controller.abort();
  }, [adapter, workspaceId, loadRevision]);

  useEffect(() => {
    const controller = new AbortController();
    workspaceMountedRef.current = true;
    lifetimeControllerRef.current = controller;
    return () => {
      questionEpochRef.current += 1;
      pollControllerRef.current?.abort();
      controller.abort();
      if (lifetimeControllerRef.current === controller) {
        lifetimeControllerRef.current = null;
        workspaceMountedRef.current = false;
      }
      for (const url of citationUrlsRef.current) globalThis.URL?.revokeObjectURL?.(url);
      citationUrlsRef.current.clear();
    };
  }, []);

  const uploadPdf = async (event) => {
    const file = event.currentTarget.files?.[0];
    if (!adapter || !file) return;
    pollControllerRef.current?.abort();
    const controller = new AbortController();
    pollControllerRef.current = controller;
    const pollOptions = normalizeProcessingPollOptions(processingPollOptions);
    const deadlineAt = pollOptions.now() + pollOptions.deadlineMs;
    let deadlineExpired = false;
    const deadlineTimer = setTimeout(() => {
      deadlineExpired = true;
      controller.abort(abortError());
    }, pollOptions.deadlineMs);
    const remainingMs = () => deadlineAt - pollOptions.now();
    setViewState(createProductWorkspaceState({ status: "loading" }));
    setProcessing(null);
    try {
      const submission = await awaitAbortable(
        adapter.uploadPdf(file, { signal: controller.signal }),
        controller.signal
      );
      if (!isSafeDtoId(submission?.processing_run_id) || !isSafeDtoId(submission?.source_id) || !isSafeDtoId(submission?.source_version_id)) {
        throw new Error("PDF_UPLOAD_RESPONSE_INVALID");
      }
      while (!controller.signal.aborted) {
        const remainingBeforeRequest = remainingMs();
        if (remainingBeforeRequest <= 0) {
          deadlineExpired = true;
          controller.abort(abortError());
          throw new Error("PROCESSING_TIMEOUT");
        }
        const requestController = new AbortController();
        const requestTimeout = setTimeout(
          () => requestController.abort(abortError()),
          Math.min(pollOptions.statusRequestTimeoutMs, remainingBeforeRequest)
        );
        const linkedRequest = linkedAbortSignal([controller.signal, requestController.signal]);
        let processingState;
        try {
          processingState = await awaitAbortable(
            adapter.getProcessingStatus(submission.processing_run_id, { signal: linkedRequest.signal }),
            linkedRequest.signal
          );
        } catch (error) {
          if (controller.signal.aborted) throw error;
          if (!requestController.signal.aborted) throw error;
          const remainingAfterTimeout = remainingMs();
          if (remainingAfterTimeout <= 0) {
            deadlineExpired = true;
            controller.abort(abortError());
            throw new Error("PROCESSING_TIMEOUT");
          }
          await pollOptions.wait(Math.min(pollOptions.intervalMs, remainingAfterTimeout), controller.signal);
          continue;
        } finally {
          clearTimeout(requestTimeout);
          linkedRequest.cleanup();
        }
        if (controller.signal.aborted) return;
        setProcessing(processingState);
        if (processingDisposition(processingState, submission) === "ready") {
          const selectedSource = { sourceId: processingState.source_id, sourceVersionId: processingState.source_version_id };
          setViewState({ ...createProductWorkspaceState({ status: "ready" }), sources: [selectedSource], selectedSource });
          return;
        }
        setViewState(createProductWorkspaceState({ status: "loading" }));
        const remainingBeforeWait = remainingMs();
        if (remainingBeforeWait <= 0) {
          deadlineExpired = true;
          controller.abort(abortError());
          throw new Error("PROCESSING_TIMEOUT");
        }
        await pollOptions.wait(Math.min(pollOptions.intervalMs, remainingBeforeWait), controller.signal);
      }
    } catch (error) {
      if (controller.signal.aborted && !deadlineExpired) return;
      setViewState(createProductWorkspaceState({
        status: "error",
        safeError: deadlineExpired ? "PROCESSING_TIMEOUT" : safeErrorCode(error, "PDF_UPLOAD_FAILED")
      }));
    } finally {
      clearTimeout(deadlineTimer);
      if (pollControllerRef.current === controller) pollControllerRef.current = null;
    }
  };

  const askQuestion = async (event) => {
    event.preventDefault();
    const generalConversation = isGeneralConversationIntent(question);
    if (!adapter || (!viewState.selectedSource && !selectedKnowledgeId && !generalConversation) || !question.trim()) return;
    const questionEpoch = ++questionEpochRef.current;
    try {
      if (!workspaceMountedRef.current) return;
      if (!lifetimeControllerRef.current || lifetimeControllerRef.current.signal.aborted) {
        lifetimeControllerRef.current = new AbortController();
      }
      const signal = lifetimeControllerRef.current.signal;
      if (!signal || signal.aborted) return;
      const idempotencyKey = crypto.randomUUID();
      const knowledgeContext = viewState.selectedSource || selectedKnowledgeId
        ? buildQuestionKnowledgeContext(viewState.selectedSource, selectedKnowledgeId)
        : null;
      let stepUpAuthorizationId = null;
      const password = questionPasswordRef.current?.value || "";
      if (password && adapter.authorizeQuestion) {
        const authorization = await adapter.authorizeQuestion(
          { knowledgeContext, question: question.trim(), password },
          { signal, idempotencyKey },
        );
        stepUpAuthorizationId = authorization.step_up_authorization_id;
      }
      if (questionEpoch !== questionEpochRef.current || signal.aborted) return;
      if (questionPasswordRef.current) questionPasswordRef.current.value = "";
      const answer = await adapter.askQuestion(
        { knowledgeContext, question: question.trim(), stepUpAuthorizationId },
        { signal, idempotencyKey },
      );
      if (signal.aborted || questionEpoch !== questionEpochRef.current) return;
      const safeAnswer = projectSafeQuestionAnswer(
        answer, workspaceId, adapter.citationUrl, viewState.selectedSource, selectedKnowledgeId,
      );
      setViewState((current) => ({
        ...current, answer: safeAnswer,
        answerIntent: generalConversation ? "general_ungrounded" : "grounded",
      }));
      reportIdempotencyRef.current = null;
    } catch (error) {
      if (questionEpoch !== questionEpochRef.current) return;
      setViewState((current) => projectQuestionFailureState(
        current,
        new Error(safeErrorCode(error, "QUESTION_FAILED")),
      ));
    } finally {
      if (questionPasswordRef.current) questionPasswordRef.current.value = "";
    }
  };

  const selectSource = (source) => {
    if (!source.ready) return;
    questionEpochRef.current += 1;
    reportIdempotencyRef.current = null;
    setViewState((current) => ({
      ...current, selectedSource: source, answer: null, answerIntent: null,
    }));
  };

  const createReport = async (event) => {
    if (
      !adapter || typeof adapter.createReport !== "function" || reportPending
      || !canCreateGroundedReport(viewState) || !reportTitle.trim() || !reportPurpose.trim()
    ) {
      event.preventDefault();
      return;
    }
    setReportPending(true);
    try {
      const result = await submitGroundedReportForm(event, {
        adapter, state: viewState, title: reportTitle, purpose: reportPurpose,
        idempotencyRef: reportIdempotencyRef, signal: lifetimeControllerRef.current?.signal,
      });
      if (result.submitted) setViewState((current) => ({ ...current, studioOutputs: result.outputs }));
    } catch (error) {
      setViewState((current) => ({ ...current, safeError: safeErrorCode(error, "STUDIO_CREATE_FAILED") }));
    } finally {
      setReportPending(false);
    }
  };

  const reportReady = canCreateGroundedReport(viewState);
  const generalConversationReady = isGeneralConversationIntent(question);
  const desktopEditor = typeof desktopOfflineStudio?.editor === "function"
    ? desktopOfflineStudio.editor(viewState)
    : desktopOfflineStudio?.editor;
  const desktopStudio = typeof desktopOfflineStudio?.studio === "function"
    ? desktopOfflineStudio.studio(viewState)
    : desktopOfflineStudio?.studio;
  const loadOperationsStatus = async () => {
    if (typeof adapter?.getOperationsStatus !== "function") {
      setOperationsStatus(null);
      setOperationsSafeError("OPERATIONS_STATUS_UNAVAILABLE");
      return;
    }
    setOperationsPending(true);
    setOperationsSafeError(null);
    try {
      const result = await adapter.getOperationsStatus({ signal: lifetimeControllerRef.current?.signal });
      setOperationsStatus(result);
    } catch (error) {
      setOperationsStatus(null);
      setOperationsSafeError(safeErrorCode(error, "OPERATIONS_STATUS_UNAVAILABLE"));
    } finally {
      setOperationsPending(false);
    }
  };
  const loadOutputVersionSettings = async () => {
    if (typeof adapter?.getOutputVersionSettings !== "function") {
      setOutputSettingsSafeError("OUTPUT_VERSION_SETTINGS_UNAVAILABLE");
      return;
    }
    setOutputSettingsPending(true);
    setOutputSettingsSafeError(null);
    try {
      const result = await adapter.getOutputVersionSettings({ signal: lifetimeControllerRef.current?.signal });
      setOutputSettings(result);
      setOutputDraft({ ...result.default_formats });
      setOutputSettingsDirty(false);
      setOutputCloseGuard(false);
    } catch (error) {
      setOutputSettingsSafeError(safeErrorCode(error, "OUTPUT_VERSION_SETTINGS_UNAVAILABLE"));
    } finally {
      setOutputSettingsPending(false);
    }
  };
  const saveOutputVersionSettings = async ({ closeAfter = false } = {}) => {
    if (!outputSettings || !outputDraft || typeof adapter?.saveOutputVersionSettings !== "function") return;
    setOutputSettingsPending(true);
    setOutputSettingsSafeError(null);
    try {
      const saved = await adapter.saveOutputVersionSettings({
        default_formats: outputDraft, version: outputSettings.version, etag: outputSettings.etag,
      }, { signal: lifetimeControllerRef.current?.signal });
      setOutputSettings(saved);
      setOutputDraft({ ...saved.default_formats });
      setOutputSettingsDirty(false);
      setOutputCloseGuard(false);
      if (closeAfter) {
        setModalView(null);
        queueMicrotask(() => modalOpenerRef.current?.focus?.());
      }
    } catch (error) {
      setOutputSettingsSafeError(safeErrorCode(error, "OUTPUT_VERSION_SETTINGS_UNAVAILABLE"));
    } finally {
      setOutputSettingsPending(false);
    }
  };
  const loadSyncOperations = async () => {
    if (typeof adapter?.listSyncOperations !== "function") {
      setSyncOperations([]);
      setSyncSafeError("SYNC_SETTINGS_UNAVAILABLE");
      return;
    }
    setSyncPending(true);
    setSyncSafeError(null);
    try {
      const result = await adapter.listSyncOperations({ signal: lifetimeControllerRef.current?.signal });
      setSyncOperations(result);
      setSyncSelections(Object.fromEntries(result.map((operation) => [
        operation.operation_id, operation.state === "awaiting_approval" ? [...operation.item_ids] : [...operation.approved_item_ids],
      ])));
    } catch (error) {
      setSyncOperations([]);
      setSyncSafeError(safeErrorCode(error, "SYNC_SETTINGS_UNAVAILABLE"));
    } finally {
      setSyncPending(false);
    }
  };
  const approveSyncOperation = async (operation) => {
    if (syncPending || typeof adapter?.approveSyncOperation !== "function" || typeof adapter?.issueStudioStepUp !== "function") return;
    const password = syncPasswordRef.current?.value ?? "";
    setSyncPending(true);
    setSyncSafeError(null);
    try {
      const idempotencyKey = crypto.randomUUID();
      const stepUpAuthorizationId = await adapter.issueStudioStepUp(
        "data_area_move", operation.operation_id, password,
        { idempotencyKey, signal: lifetimeControllerRef.current?.signal },
      );
      const saved = await adapter.approveSyncOperation(
        operation,
        { approvedItemIds: syncSelections[operation.operation_id] ?? [], stepUpAuthorizationId },
        { idempotencyKey, signal: lifetimeControllerRef.current?.signal },
      );
      setSyncOperations((current) => current.map((item) => item.operation_id === saved.operation_id ? saved : item));
    } catch (error) {
      setSyncSafeError(safeErrorCode(error, "SYNC_SETTINGS_APPROVAL_FAILED"));
    } finally {
      if (syncPasswordRef.current) syncPasswordRef.current.value = "";
      setSyncPending(false);
    }
  };
  const loadEgressPolicy = async () => {
    if (typeof adapter?.getEgressPolicy !== "function") {
      setEgressPolicy(null);
      setEgressPolicySafeError("EGRESS_POLICY_UNAVAILABLE");
      return;
    }
    setEgressPolicyPending(true);
    setEgressPolicySafeError(null);
    try {
      setEgressPolicy(await adapter.getEgressPolicy({ signal: lifetimeControllerRef.current?.signal }));
    } catch (error) {
      setEgressPolicy(null);
      setEgressPolicySafeError(safeErrorCode(error, "EGRESS_POLICY_UNAVAILABLE"));
    } finally {
      setEgressPolicyPending(false);
    }
  };
  const loadLicense = async () => {
    if (typeof adapter?.getLicense !== "function") {
      setLicenseView(null);
      setLicenseSafeError("LICENSE_UNAVAILABLE");
      return;
    }
    setLicensePending(true);
    setLicenseSafeError(null);
    try {
      setLicenseView(await adapter.getLicense({ signal: lifetimeControllerRef.current?.signal }));
    } catch (error) {
      setLicenseView(null);
      setLicenseSafeError(safeErrorCode(error, "LICENSE_UNAVAILABLE"));
    } finally {
      setLicensePending(false);
    }
  };
  const applyLicense = async () => {
    const file = licenseFileRef.current?.files?.[0];
    const password = licensePasswordRef.current?.value ?? "";
    if (licensePending || typeof adapter?.applyLicense !== "function" || !file || file.size > 65_536) {
      setLicenseSafeError("LICENSE_DOCUMENT_INVALID");
      return;
    }
    setLicensePending(true);
    setLicenseSafeError(null);
    try {
      const document = JSON.parse(await file.text());
      const saved = await adapter.applyLicense(document, password, {
        idempotencyKey: crypto.randomUUID(), signal: lifetimeControllerRef.current?.signal,
      });
      setLicenseView(saved);
    } catch (error) {
      setLicenseSafeError(safeErrorCode(error, "LICENSE_APPLY_FAILED"));
    } finally {
      if (licenseFileRef.current) licenseFileRef.current.value = "";
      if (licensePasswordRef.current) licensePasswordRef.current.value = "";
      setLicensePending(false);
    }
  };
  const loadManualHub = async () => {
    if (typeof adapter?.getManualManifest !== "function") {
      setManualManifest(null);
      setManualSafeError("MANUAL_HUB_UNAVAILABLE");
      return;
    }
    setManualPending(true);
    setManualSafeError(null);
    try {
      const result = await adapter.getManualManifest({ signal: lifetimeControllerRef.current?.signal });
      setManualManifest(result);
      setManualDocument(null);
      setManualSearch("");
    } catch (error) {
      setManualManifest(null);
      setManualSafeError(safeErrorCode(error, "MANUAL_HUB_UNAVAILABLE"));
    } finally {
      setManualPending(false);
    }
  };
  const readManual = async (documentId) => {
    if (manualPending || typeof adapter?.readManual !== "function") return;
    setManualPending(true);
    setManualSafeError(null);
    try {
      setManualDocument(await adapter.readManual(documentId, manualManifest, { signal: lifetimeControllerRef.current?.signal }));
    } catch (error) {
      setManualSafeError(safeErrorCode(error, "MANUAL_CONTENT_UNAVAILABLE"));
    } finally {
      setManualPending(false);
    }
  };
  const downloadManual = async (documentId, format) => {
    if (manualPending || typeof adapter?.downloadManual !== "function") return;
    setManualPending(true);
    setManualSafeError(null);
    try {
      const result = await adapter.downloadManual(documentId, format, manualManifest, { signal: lifetimeControllerRef.current?.signal });
      const url = globalThis.URL?.createObjectURL?.(result.blob);
      if (!url) throw new Error("MANUAL_DOWNLOAD_UNAVAILABLE");
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      globalThis.URL.revokeObjectURL(url);
    } catch (error) {
      setManualSafeError(safeErrorCode(error, "MANUAL_DOWNLOAD_UNAVAILABLE"));
    } finally {
      setManualPending(false);
    }
  };
  const openModal = (view, opener) => {
    modalOpenerRef.current = opener ?? document.activeElement;
    setSettingsMenuOpen(false);
    setModalView(view);
    if (view === "operations") void loadOperationsStatus();
    if (view === "output-version") void loadOutputVersionSettings();
    if (view === "sync") void loadSyncOperations();
    if (view === "organization-policy") void loadEgressPolicy();
    if (view === "license") void loadLicense();
    if (view === "manual") void loadManualHub();
  };
  const closeModal = (force = false) => {
    if (!force && modalView === "output-version" && outputSettingsDirty) {
      setOutputCloseGuard(true);
      return;
    }
    setModalView(null);
    setOutputCloseGuard(false);
    queueMicrotask(() => modalOpenerRef.current?.focus?.());
  };
  const compactState = viewState.status === "ready" ? "준비" : viewState.status === "loading" ? "확인 중" : viewState.status === "error" ? "주의" : "연결 필요";

  return (
    <main className="adaptive-workspace" data-product-workspace-state={viewState.status} data-workspace-id={workspaceId ?? ""}>
      <div className="workspace-surface" inert={modalView ? true : undefined} aria-hidden={modalView ? "true" : undefined}>
      <header className="workspace-header workspace-app-bar">
        <div className="workspace-brand"><span className="daon-mark" aria-hidden="true">D</span><div><p className="eyebrow">DAON WORKSPACE</p><h1>Workspace</h1></div></div>
        <div className="workspace-app-actions">
          <span className={`workspace-status status-${viewState.status}`} role="status"><span className="status-dot" aria-hidden="true" />{compactState}<span className="status-connection"> · Cloud</span></span>
          <button type="button" onClick={(event) => openModal("operations", event.currentTarget)}>운영상태</button>
          <div className="settings-menu-anchor">
            <button ref={settingsButtonRef} type="button" aria-haspopup="menu" aria-expanded={settingsMenuOpen} onClick={() => setSettingsMenuOpen((open) => !open)}>설정</button>
            {settingsMenuOpen ? <div className="settings-menu" role="menu" aria-label="Workspace 설정">
              <button type="button" role="menuitem" onClick={() => openModal("llm", settingsButtonRef.current)}><span className="menu-icon icon-model" aria-hidden="true" /><span><strong>LLM 설정</strong><small>Provider와 모델 연결</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("output-version", settingsButtonRef.current)}><span className="menu-icon icon-output" aria-hidden="true" /><span><strong>출력·버전</strong><small>기본 형식과 저장 정책</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("sync", settingsButtonRef.current)}><span className="menu-icon icon-sync" aria-hidden="true" /><span><strong>동기화·승인</strong><small>Preview 확인과 명시 승인</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("organization-policy", settingsButtonRef.current)}><span className="menu-icon icon-policy" aria-hidden="true" /><span><strong>조직 정책</strong><small>읽기 전용</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("license", settingsButtonRef.current)}><span className="menu-icon icon-license" aria-hidden="true" /><span><strong>라이선스</strong><small>Edition과 사용 한도</small></span></button>
              <button type="button" role="menuitem" onClick={() => openModal("manual", settingsButtonRef.current)}><span className="menu-icon icon-manual" aria-hidden="true" /><span><strong>사용자 설명서</strong><small>가이드 검색·읽기·다운로드</small></span></button>
              <button type="button" role="menuitem" onClick={() => { setSettingsMenuOpen(false); onLogout?.(); }}><span className="menu-icon" aria-hidden="true" /><span><strong>로그아웃</strong><small>현재 세션만 종료</small></span></button>
            </div> : null}
          </div>
        </div>
      </header>
      <div className="workspace-panes" data-layout="source-conversation-studio" aria-label="Workspace 3면">
        <SafePane id="product-pane-sources" title="Source·지식·권위" description="PDF 등록과 처리 상태는 실제 same-origin 연결만 사용합니다.">
          <label className="source-add-button"><span aria-hidden="true">＋</span>Source 추가<input type="file" accept="application/pdf" onChange={uploadPdf} disabled={!adapter} /></label>
          {processing ? <p className="source-inline-state" role="status">처리 중 · 잠시만 기다려 주세요.</p> : null}
          <section className="source-group" aria-labelledby="approved-knowledge-title">
            <div className="source-group-heading"><h3 id="approved-knowledge-title">Daon 승인 지식</h3><span>{knowledgePackages.length}</span></div>
            <ul className="source-list knowledge-list" aria-label="Daon 승인 지식 목록">
              {knowledgePackages.map((item) => (
                <li className="source-list-row knowledge-list-row" key={item.packageId}>
                  <button type="button" aria-pressed={selectedKnowledgeId === item.packageId} onClick={() => { questionEpochRef.current += 1; reportIdempotencyRef.current = null; setViewState((current) => ({ ...current, answer: null, answerIntent: null })); setSelectedKnowledgeId((current) => current === item.packageId ? null : item.packageId); }}>
                    <span className="source-file-icon knowledge-icon" aria-hidden="true">K</span><span className="source-row-copy"><strong>{item.producerLabel} · {item.producerVersion}</strong><small>{item.authorityLabel} · {item.registrationLabel}</small></span><span className={`source-ready-dot ${selectedKnowledgeId === item.packageId ? "is-ready" : ""}`} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
            {knowledgeSafeError ? <div className="inline-alert compact" role="alert">승인 지식을 불러오지 못했습니다.</div> : null}
          </section>
          <section className="source-group" aria-labelledby="raw-source-title">
          <div className="source-group-heading"><h3 id="raw-source-title">Raw Source</h3><span>{viewState.sources.length}</span></div>
          <ul className="source-list" aria-label="Raw Source 목록">
            {viewState.sources.map((source) => (
              <li className="source-list-row" key={`${source.sourceId}:${source.sourceVersionId}`}>
                <button type="button" aria-pressed={viewState.selectedSource?.sourceVersionId === source.sourceVersionId} onClick={() => selectSource(source)} disabled={!source.ready}>
                  <span className="source-file-icon" aria-hidden="true">PDF</span><span className="source-row-copy"><strong>{source.filename ?? source.sourceId}</strong><small>Version {source.sourceVersionId} · {SOURCE_STATE_LABELS[source.sourceState] ?? "상태 확인 필요"}</small></span><span className={`source-ready-dot ${source.ready ? "is-ready" : ""}`} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          </section>
          {viewState.status === "loading" ? <div className="pane-empty" role="status"><span className="state-spinner" aria-hidden="true" /><strong>Source를 불러오고 있습니다.</strong><small>현재 Workspace의 안전한 목록을 확인하는 중입니다.</small></div> : null}
          {viewState.status === "empty" ? <div className="pane-empty"><span aria-hidden="true">◇</span><strong>Source를 추가해 주세요.</strong><small>PDF를 등록하면 질문과 Studio 생성에 사용할 수 있습니다.</small></div> : null}
          {viewState.status === "error" && !viewState.sources.length ? <div className="inline-alert" role="alert"><span>Source를 불러오지 못했습니다. 운영상태에서 연결을 확인해 주세요.</span><button type="button" onClick={() => { setViewState(createProductWorkspaceState({ status: "loading" })); setLoadRevision((current) => current + 1); }}>다시 시도</button></div> : null}
        </SafePane>
        <SafePane id="product-pane-conversation" title="대화·실행" description="ready Source 선택 후 실제 질문과 Citation을 사용합니다.">
          {desktopEditor ? desktopEditor : <div className="conversation-workspace"><div className="conversation-transcript" aria-live="polite">
          <div className="context-selection-status" role="status">질문 컨텍스트 · {selectedKnowledgeId ? "Daon 승인 지식" : ""}{selectedKnowledgeId && viewState.selectedSource ? " + " : ""}{viewState.selectedSource ? "Raw Source" : ""}</div>
          {viewState.answer ? <article className="assistant-message"><div className="assistant-avatar" aria-hidden="true">D</div><div><span className="message-author">Daon</span><p>{viewState.answer.answer}</p></div></article> : <div className="conversation-empty"><span className="conversation-orbit" aria-hidden="true">✦</span><h3>Source에 대해 무엇이든 물어보세요.</h3><p>선택한 Source의 근거와 Citation을 사용해 답합니다.</p></div>}
          {viewState.answerIntent === "general_ungrounded"
            ? <div className="context-selection-status" role="status">일반 대화 · 근거 미사용</div> : null}
          {viewState.conversationSafeError ? <div className="inline-alert compact" role="alert">대화를 불러오지 못했습니다. 다시 시도해 주세요.</div> : null}
          <div className="citation-row">{viewState.answer?.citations?.map((citation) => (
            <a key={citation.citation_id} href={citation.content_url} onClick={(event) => {
              void openNativeCitation(event, {
                adapter,
                citation,
                signal: lifetimeControllerRef.current?.signal,
                registerObjectUrl: (url) => citationUrlsRef.current.add(url),
                unregisterObjectUrl: (url) => citationUrlsRef.current.delete(url)
              }).catch((error) => {
                setViewState((current) => ({ ...current, status: "error", safeError: safeErrorCode(error, "CITATION_RESPONSE_INVALID") }));
              });
            }}>Citation · {citation.locator.kind === "page" ? `${citation.page}쪽` : "지식 구간"}</a>
          ))}</div></div><form className="conversation-composer" onSubmit={askQuestion}>
            <label><span className="sr-only">질문</span><textarea rows="2" placeholder={viewState.selectedSource || selectedKnowledgeId ? "선택한 지식에 대해 질문하세요" : "인사하거나 Daon 사용법을 물어보세요"} value={question} onChange={(event) => setQuestion(event.currentTarget.value)} /></label>
            <details className="composer-auth"><summary title="외부 Provider 정책이 요구할 때만 사용합니다.">추가 인증</summary><label>현재 비밀번호<input ref={questionPasswordRef} type="password" autoComplete="current-password" /></label></details>
            <button className="composer-submit" type="submit" aria-label="질문 실행" disabled={!question.trim() || (!viewState.selectedSource && !selectedKnowledgeId && !generalConversationReady)}>↑</button>
          </form></div>}
        </SafePane>
        <SafePane id="product-pane-studio" title="업무 Studio" description="근거가 확인된 답변으로 보고서를 생성하고 저장 결과를 확인합니다.">
          {desktopStudio ? desktopStudio : <><ProductStudioPane
            key={`${viewState.answer?.run_id ?? "no-run"}:${viewState.selectedSource?.sourceVersionId ?? "no-source"}`}
            adapter={adapter}
            state={createProductStudioState({
              workspaceId,
              grounded: canCreateGroundedReport(viewState) ? {
                sourceId: viewState.selectedSource?.sourceId ?? viewState.answer.citations[0].source_id,
                sourceVersionId: viewState.selectedSource?.sourceVersionId ?? viewState.answer.citations[0].source_version_id,
                sourceVersionIds: [...new Set(viewState.answer.citations.map((citation) => citation.source_version_id))],
                runId: viewState.answer.run_id,
                runResultId: viewState.answer.run_result_id,
              } : null,
              locks: Array.isArray(viewState.studioLocks) ? viewState.studioLocks : [],
              outputs: viewState.studioOutputs,
              status: viewState.studioStatus === "unavailable"
                ? "unavailable"
                : reportReady ? "ready" : "unavailable",
              safeError: viewState.studioSafeError ?? null,
            })}
          />
          {reportReady ? <details className="grounded-report-legacy"><summary>빠른 근거 보고서</summary><form onSubmit={createReport}>
            <label>보고서 제목<input value={reportTitle} maxLength={200} onChange={(event) => setReportTitle(event.currentTarget.value)} /></label>
            <label>결과 목적<input value={reportPurpose} maxLength={500} onChange={(event) => setReportPurpose(event.currentTarget.value)} /></label>
            <button type="submit" disabled={!reportReady || !reportTitle.trim() || !reportPurpose.trim() || reportPending}>보고서 생성</button>
          </form></details> : null}
          <ul aria-label="저장된 보고서">
            {viewState.studioOutputs.map((output) => (
              <li key={output.studio_output_id}>
                <strong>{output.title}</strong>
                <span>{output.status}</span>
                <ul aria-label={`${output.title} Citation 계보`}>
                  {(Array.isArray(output.citations) ? output.citations : []).map((citation) => (
                    <li key={citation.citation_id}>Citation page {citation.page}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul></>}
        </SafePane>
      </div>
      </div>
      {modalView ? <div className="workspace-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeModal(); }}>
        <section ref={modalRef} className={`workspace-modal workspace-modal-${modalView}`} role="dialog" aria-modal="true" aria-labelledby="workspace-modal-title" onKeyDown={(event) => trapDialogFocus(event, closeModal)}>
          <header className="workspace-modal-header"><div><span className="section-kicker">WORKSPACE SETTINGS</span><h2 id="workspace-modal-title">{modalView === "llm" ? "LLM 설정" : modalView === "output-version" ? "출력·버전" : modalView === "sync" ? "동기화·승인" : modalView === "organization-policy" ? "조직 정책" : modalView === "license" ? "라이선스" : modalView === "manual" ? "사용자 설명서" : "운영상태"}</h2></div><button className="modal-close" type="button" onClick={() => closeModal()} aria-label="닫기">×</button></header>
          {modalView === "llm" ? <div className="workspace-modal-content">{providerSettings ?? <div className="modal-unavailable" role="status"><strong>Provider 설정 연결이 필요합니다.</strong><p>현재 Workspace에서는 설정 상태를 불러올 수 없습니다.</p><button type="button" disabled>연결 기능 준비 중</button></div>}</div> : modalView === "output-version" ? <div className="workspace-modal-content output-version-settings">
            {outputSettingsPending && !outputSettings ? <div className="modal-unavailable" role="status">출력 설정을 불러오고 있습니다.</div> : null}
            {outputSettingsSafeError ? <div className="modal-unavailable" role="alert"><strong>출력 설정을 처리하지 못했습니다.</strong><p>변경 내용은 저장되지 않았습니다.</p><button type="button" onClick={() => void loadOutputVersionSettings()}>다시 불러오기</button></div> : null}
            {outputSettings && outputDraft ? <form onSubmit={(event) => { event.preventDefault(); void saveOutputVersionSettings(); }}>
              <div className="output-format-grid">
                {Object.entries(OUTPUT_SETTING_LABELS).map(([type, label]) => <label key={type}><span>{label}</span><select value={outputDraft[type]} disabled={outputSettingsPending} onChange={(event) => { setOutputDraft((current) => ({ ...current, [type]: event.target.value })); setOutputSettingsDirty(true); }}>
                  {OUTPUT_SETTING_FORMATS[type].map((format) => <option key={format} value={format}>{format.toUpperCase()}</option>)}
                </select></label>)}
              </div>
              <div className="version-policy-row"><div><strong>Version 저장 방식</strong><small>기존 Version은 보존하고 새 Version을 추가합니다.</small></div><span>Append-only</span></div>
              <div className="modal-form-actions"><button type="button" disabled={!outputSettingsDirty || outputSettingsPending} onClick={() => { setOutputDraft({ ...outputSettings.default_formats }); setOutputSettingsDirty(false); }}>변경 취소</button><button type="submit" disabled={!outputSettingsDirty || outputSettingsPending} onClick={(event) => { event.preventDefault(); void saveOutputVersionSettings(); }}>{outputSettingsPending ? "저장 중" : "저장"}</button></div>
            </form> : null}
            {outputCloseGuard ? <div className="unsaved-settings-guard" role="alertdialog" aria-label="저장하지 않은 변경"><strong>저장하지 않은 변경이 있습니다.</strong><p>저장하거나 변경을 버린 뒤 닫아 주세요.</p><div><button type="button" onClick={() => void saveOutputVersionSettings({ closeAfter: true })}>저장</button><button type="button" onClick={() => { setOutputSettingsDirty(false); closeModal(true); }}>버리기</button><button type="button" onClick={() => setOutputCloseGuard(false)}>계속 편집</button></div></div> : null}
          </div> : modalView === "sync" ? <div className="workspace-modal-content sync-approval-settings" aria-live="polite">
            <div className="sync-settings-toolbar"><div><strong>동기화 Preview</strong><small>자동 전송하지 않습니다. 승인할 항목을 확인해 명시적으로 선택하세요.</small></div><button type="button" disabled={syncPending} onClick={() => void loadSyncOperations()}>새로고침</button></div>
            {syncPending && !syncOperations.length ? <div className="modal-unavailable" role="status">동기화 상태를 불러오고 있습니다.</div> : null}
            {syncSafeError ? <div className="modal-unavailable" role="alert"><strong>동기화 설정을 처리하지 못했습니다.</strong><p>승인 또는 전송은 실행되지 않았습니다.</p><button type="button" onClick={() => void loadSyncOperations()}>다시 불러오기</button></div> : null}
            {!syncPending && !syncSafeError && !syncOperations.length ? <div className="sync-empty-state"><strong>승인을 기다리는 동기화가 없습니다.</strong><small>Windows 앱에서 Preview를 만들면 이 화면에서 확인할 수 있습니다.</small></div> : null}
            <div className="sync-operation-list">
              {syncOperations.map((operation) => <article key={operation.operation_id} data-sync-state={operation.state}>
                <header><div><strong>{operation.state === "awaiting_approval" ? "승인 대기" : operation.state === "approved" ? "승인됨" : operation.state === "conflict" ? "충돌 확인 필요" : "처리 상태"}</strong><small>항목 {operation.item_ids.length}개 · Version {operation.version}</small></div><span>{operation.state}</span></header>
                <fieldset disabled={operation.state !== "awaiting_approval" || syncPending}><legend>승인 항목</legend>{operation.item_ids.map((itemId) => <label key={itemId}><input type="checkbox" checked={(syncSelections[operation.operation_id] ?? []).includes(itemId)} onChange={(event) => setSyncSelections((current) => ({ ...current, [operation.operation_id]: event.target.checked ? [...new Set([...(current[operation.operation_id] ?? []), itemId])] : (current[operation.operation_id] ?? []).filter((id) => id !== itemId) }))} /><span>{itemId}</span></label>)}</fieldset>
                {operation.state === "awaiting_approval" ? <button type="button" disabled={syncPending || !(syncSelections[operation.operation_id] ?? []).length} onClick={() => void approveSyncOperation(operation)}>선택 항목 승인</button> : null}
              </article>)}
            </div>
            {syncOperations.some((operation) => operation.state === "awaiting_approval") ? <label className="sync-step-up-password"><span>현재 비밀번호</span><input ref={syncPasswordRef} type="password" autoComplete="current-password" disabled={syncPending} /><small>승인 시 Step-up 인증에만 사용하며 저장하지 않습니다.</small></label> : null}
          </div> : modalView === "organization-policy" ? <div className="workspace-modal-content organization-policy-settings" aria-live="polite">
            {egressPolicyPending ? <div className="modal-unavailable" role="status">조직 정책을 불러오고 있습니다.</div> : null}
            {!egressPolicyPending && egressPolicySafeError ? <div className="modal-unavailable" role="alert"><strong>조직 정책을 불러오지 못했습니다.</strong><p>정책은 변경되지 않았습니다.</p><button type="button" onClick={() => void loadEgressPolicy()}>다시 불러오기</button></div> : null}
            {!egressPolicyPending && egressPolicy ? <>
              <div className="policy-lock-banner"><span aria-hidden="true">⌾</span><div><strong>조직 강제 정책</strong><small>이 Workspace에서는 읽기 전용이며 조직 정책을 완화할 수 없습니다.</small></div></div>
              <dl className="organization-policy-grid">
                <div><dt>정책 모드</dt><dd>{EGRESS_MODE_LABELS[egressPolicy.organization_policy.mode]}</dd></div>
                <div><dt>허용 Provider 종류</dt><dd>{egressPolicy.organization_policy.allowed_provider_kinds.join(", ") || "허용 없음"}</dd></div>
                <div><dt>허용 목적지</dt><dd>{egressPolicy.organization_policy.allowed_destinations.join(", ") || "허용 없음"}</dd></div>
                <div><dt>분류</dt><dd>{egressPolicy.organization_policy.classification}</dd></div>
                <div><dt>최대 전송량</dt><dd>{egressPolicy.organization_policy.max_bytes.toLocaleString("ko-KR")} bytes</dd></div>
                <div><dt>마스킹</dt><dd>{egressPolicy.organization_policy.masking_required ? "마스킹 필수" : "마스킹 선택"}</dd></div>
                <div><dt>삭제 처리</dt><dd>{egressPolicy.organization_policy.redaction_required ? "삭제 처리 필수" : "삭제 처리 선택"}</dd></div>
                <div><dt>필수 승인자</dt><dd>{EGRESS_APPROVER_LABELS[egressPolicy.organization_policy.required_approver]}</dd></div>
              </dl>
              <div className="workspace-policy-effective"><div><strong>Workspace 적용 결과</strong><small>{egressPolicy.parent_locked ? "조직 차단이 우선 적용됩니다." : "조직과 Workspace 정책의 교집합입니다."}</small></div><span>{EGRESS_MODE_LABELS[egressPolicy.mode]}</span></div>
            </> : null}
          </div> : modalView === "license" ? <div className="workspace-modal-content license-settings" aria-live="polite">
            {licensePending && !licenseView ? <div className="modal-unavailable" role="status">라이선스 정보를 확인하고 있습니다.</div> : null}
            {licenseSafeError ? <div className="modal-unavailable" role="alert"><strong>라이선스 요청을 처리하지 못했습니다.</strong><p>{licenseSafeError}</p><button type="button" disabled={licensePending} onClick={() => void loadLicense()}>다시 불러오기</button></div> : null}
            {licenseView ? <>
              <section className="license-summary" aria-label="라이선스 요약">
                <div><span>제품</span><strong>{licenseView.product}</strong></div>
                <div><span>Edition</span><strong>{licenseView.edition ?? "미적용"}</strong></div>
                <div><span>License ID</span><strong>{licenseView.license_id_hint ?? "-"}</strong></div>
                <div><span>상태</span><strong data-license-status={licenseView.status}>{LICENSE_STATUS_LABELS[licenseView.status] ?? licenseView.status}</strong></div>
                <div><span>발급</span><strong>{licenseView.issued_at ? new Date(licenseView.issued_at).toLocaleDateString("ko-KR") : "-"}</strong></div>
                <div><span>만료</span><strong>{licenseView.expires_at ? new Date(licenseView.expires_at).toLocaleDateString("ko-KR") : "-"}</strong></div>
              </section>
              {licenseView.warning ? <div className="license-warning" role="status"><strong>{LICENSE_STATUS_LABELS[licenseView.status]}</strong><span>{licenseView.warning.action}</span></div> : null}
              <section className="license-features" aria-labelledby="license-feature-title"><h3 id="license-feature-title">허용 기능</h3><div>{licenseView.features.map((feature) => <span key={feature}>{feature}</span>)}</div></section>
              <section className="license-resources" aria-labelledby="license-resource-title"><h3 id="license-resource-title">사용 한도</h3>{licenseView.resources.map((resource) => <article key={resource.resource} data-license-resource-status={resource.status}>
                <div><strong>{LICENSE_RESOURCE_LABELS[resource.resource] ?? resource.resource}</strong><span>{resource.used.toLocaleString("ko-KR")} / {resource.limit.toLocaleString("ko-KR")}</span></div>
                <progress max={resource.limit} value={Math.min(resource.used, resource.limit)} aria-label={`${LICENSE_RESOURCE_LABELS[resource.resource] ?? resource.resource} 사용량`} />
                <small>잔여 {resource.remaining.toLocaleString("ko-KR")}</small>
              </article>)}</section>
              {!licenseView.can_apply ? <p className="license-readonly-note">일반 사용자는 라이선스 정보를 읽기 전용으로 확인합니다.</p> : <form className="license-apply-form" onSubmit={(event) => { event.preventDefault(); void applyLicense(); }}>
                <header><strong>서명 라이선스 적용</strong><button className="info-button" type="button" title="승인된 Public key로 서명·제품·조직·기간·Schema를 검증합니다. 원문은 저장하지 않습니다." aria-label="라이선스 적용 설명">i</button></header>
                <label><span>License document</span><input ref={licenseFileRef} type="file" accept="application/json,.json" disabled={licensePending} /></label>
                <label><span>현재 비밀번호</span><input ref={licensePasswordRef} type="password" minLength={12} maxLength={1024} autoComplete="current-password" disabled={licensePending} /></label>
                <button type="submit" disabled={licensePending}>{licensePending ? "검증·적용 중" : "Step-up 후 검증·적용"}</button>
              </form>}
            </> : null}
          </div> : modalView === "manual" ? <div className="workspace-modal-content manual-hub" aria-live="polite">
            <div className="manual-hub-toolbar">
              <div><strong>Daon 문서 Hub</strong><small>{manualManifest ? `Release ${manualManifest.release_version} · ${manualManifest.language}` : "Release 정보를 확인하고 있습니다."}</small></div>
              <label><span className="sr-only">설명서 검색</span><input type="search" value={manualSearch} onChange={(event) => setManualSearch(event.target.value)} placeholder="문서 제목 또는 설명 검색" disabled={!manualManifest || manualPending} /></label>
            </div>
            {manualPending && !manualManifest ? <div className="modal-unavailable" role="status">사용자 설명서를 불러오고 있습니다.</div> : null}
            {manualSafeError ? <div className="modal-unavailable" role="alert"><strong>사용자 설명서를 처리하지 못했습니다.</strong><p>허용된 Release 문서를 다시 확인해 주세요.</p><button type="button" disabled={manualPending} onClick={() => void loadManualHub()}>다시 불러오기</button></div> : null}
            <div className="manual-hub-layout">
              <nav className="manual-document-list" aria-label="Daon 설명서 목록">
                {manualManifest?.documents.filter((item) => `${item.title} ${item.summary}`.toLocaleLowerCase("ko-KR").includes(manualSearch.trim().toLocaleLowerCase("ko-KR"))).map((item) => <article key={item.document_id} data-manual-scope={item.auth_scope}>
                  <button type="button" aria-pressed={manualDocument?.document_id === item.document_id} disabled={manualPending} onClick={() => void readManual(item.document_id)}><strong>{item.title}</strong><small>{item.summary}</small><span>Web에서 읽기</span></button>
                  <div><button type="button" disabled={manualPending} onClick={() => void downloadManual(item.document_id, "docx")}>DOCX</button><button type="button" disabled={manualPending} onClick={() => void downloadManual(item.document_id, "pdf")}>PDF</button></div>
                </article>)}
              </nav>
              <article className="manual-reader" aria-label="선택한 설명서 본문">
                {manualDocument ? <><header><div><strong>{manualDocument.title}</strong><small>검증된 Release Markdown 정본</small></div><button type="button" onClick={() => setManualDocument(null)}>목록 보기</button></header><ManualMarkdown text={manualDocument.text} /></> : <div className="manual-reader-empty"><span aria-hidden="true">▤</span><strong>읽을 문서를 선택해 주세요.</strong><small>문서별 공개 범위와 로그인 후 조직 전용 절차를 구분해 제공합니다.</small></div>}
              </article>
            </div>
          </div> : <div className="operations-status-grid" aria-live="polite">
            {operationsPending ? <div className="modal-unavailable" role="status">운영상태를 확인하고 있습니다.</div> : null}
            {!operationsPending && operationsSafeError ? <div className="modal-unavailable" role="alert"><strong>운영상태를 불러오지 못했습니다.</strong><p>잠시 후 안전하게 다시 확인해 주세요.</p><button type="button" onClick={() => void loadOperationsStatus()}>상태 새로고침</button></div> : null}
            {!operationsPending && operationsStatus?.components?.map((item) => <article key={item.component_id} data-operation-status={item.status}>
              <span className={`status-dot status-${item.status}`} aria-hidden="true" />
              <div><strong>{OPERATIONS_LABELS[item.component_id]}</strong><small>{OPERATIONS_MESSAGES[item.safe_code] ?? "상태를 확인할 수 없습니다."}{item.pending_count ? ` · ${item.pending_count}건` : ""}</small></div>
              {item.recovery_action === "open_llm_settings" ? <button type="button" onClick={() => setModalView("llm")}>LLM 설정 열기</button> : null}
              {item.recovery_action === "open_sync_settings" ? <button type="button" onClick={() => { closeModal(); setSettingsMenuOpen(true); }}>동기화 설정 열기</button> : null}
              {item.recovery_action === "refresh_status" ? <button type="button" onClick={() => void loadOperationsStatus()}>상태 새로고침</button> : null}
            </article>)}
          </div>}
        </section>
      </div> : null}
    </main>
  );
}
