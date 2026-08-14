"use client";

import { useEffect, useRef, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import { canCreateGroundedReport, createProductWorkspaceState, normalizeProductWorkspaceState, projectQuestionFailureState } from "./product-workspace-model.js";
import { createProductStudioState } from "./product-studio-model.js";
import { ProductStudioPane } from "./product-studio-pane.jsx";
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

export function projectSafeQuestionAnswer(answer, workspaceId, citationUrl, selectedSource) {
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
    const validCitation = hasExactKeys(citation, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page"])
      && isSafeDtoId(citation.citation_id)
      && isSafeDtoId(citation.source_id)
      && isSafeDtoId(citation.source_version_id)
      && isSafeDtoId(citation.evidence_span_id)
      && Number.isSafeInteger(citation.page)
      && citation.page >= 1
      && citation.source_id === selectedSource?.sourceId
      && citation.source_version_id === selectedSource?.sourceVersionId;
    if (!validCitation) throw new Error("QUESTION_RESPONSE_INVALID");
    let contentUrl;
    try {
      contentUrl = citationUrl(citation);
    } catch {
      throw new Error("QUESTION_RESPONSE_INVALID");
    }
    const expectedUrl = `/bff/api/workspaces/${encodeURIComponent(workspaceId)}/citations/${encodeURIComponent(citation.citation_id)}/content#page=${citation.page}`;
    if (contentUrl !== expectedUrl) throw new Error("QUESTION_RESPONSE_INVALID");
    return { ...citation, content_url: contentUrl };
  });
  return { ...answer, citations };
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

export function ProductWorkspaceShell({ workspaceId, state = createProductWorkspaceState(), adapter = null, processingPollOptions = null, desktopOfflineStudio = null, providerSettings = null }) {
  const [viewState, setViewState] = useState(() => normalizeProductWorkspaceState(state));
  const [processing, setProcessing] = useState(null);
  const [question, setQuestion] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const [reportPurpose, setReportPurpose] = useState("");
  const [reportPending, setReportPending] = useState(false);
  const [settingsMenuOpen, setSettingsMenuOpen] = useState(false);
  const [modalView, setModalView] = useState(null);
  const pollControllerRef = useRef(null);
  const reportIdempotencyRef = useRef(null);
  const lifetimeControllerRef = useRef(null);
  const citationUrlsRef = useRef(new Set());
  const questionPasswordRef = useRef(null);
  const modalRef = useRef(null);
  const modalOpenerRef = useRef(null);
  const settingsButtonRef = useRef(null);

  useEffect(() => {
    if (!modalView) return undefined;
    const first = modalRef.current?.querySelector?.("button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])");
    first?.focus();
    return undefined;
  }, [modalView]);

  useEffect(() => {
    if (!adapter || typeof adapter.listSources !== "function" || !workspaceId) return undefined;
    const controller = new AbortController();
    Promise.allSettled([
      adapter.listSources({ signal: controller.signal }),
      (typeof adapter.listStudioOutputs === "function"
        ? (typeof adapter.listProductStudioOutputs === "function"
          ? adapter.listProductStudioOutputs({ signal: controller.signal })
          : adapter.listStudioOutputs({ signal: controller.signal }))
        : Promise.resolve([]))
    ]).then(([sourceResult, studioResult]) => {
      if (controller.signal.aborted) return;
      if (sourceResult.status === "rejected") throw sourceResult.reason;
      const sources = sourceResult.value;
      const projected = sources.map((source) => ({
        sourceId: source.source_id,
        sourceVersionId: source.source_version_id,
        filename: source.filename,
        ready: source.source_state === "ready"
          && source.processing_state === "completed"
          && source.job_state === "completed"
      }));
      const selectedSource = projected.find((source) => source.ready) ?? null;
      const studioValue = studioResult.status === "fulfilled" ? studioResult.value : [];
      const studioOutputs = Array.isArray(studioValue) ? studioValue : studioValue?.outputs ?? [];
      const studioLocks = Array.isArray(studioValue?.studioLocks) ? studioValue.studioLocks : [];
      setViewState({
        ...createProductWorkspaceState({ status: projected.length ? "ready" : "empty" }),
        sources: projected, selectedSource, studioOutputs, studioLocks,
        studioStatus: studioResult.status === "fulfilled" ? "ready" : "unavailable",
        studioSafeError: studioResult.status === "fulfilled"
          ? null
          : safeErrorCode(studioResult.reason, "STUDIO_LIST_FAILED"),
      });
    }).catch((error) => {
      if (!controller.signal.aborted) {
        setViewState(createProductWorkspaceState({ status: "error", safeError: safeErrorCode(error, "SOURCE_LIST_FAILED") }));
      }
    });
    return () => controller.abort();
  }, [adapter, workspaceId]);

  useEffect(() => {
    const controller = new AbortController();
    lifetimeControllerRef.current = controller;
    return () => {
      pollControllerRef.current?.abort();
      controller.abort();
      if (lifetimeControllerRef.current === controller) lifetimeControllerRef.current = null;
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
    if (!adapter || !viewState.selectedSource || !question.trim()) return;
    try {
      const signal = lifetimeControllerRef.current?.signal;
      if (!signal || signal.aborted) return;
      const idempotencyKey = crypto.randomUUID();
      let stepUpAuthorizationId = null;
      const password = questionPasswordRef.current?.value || "";
      if (password && adapter.authorizeQuestion) {
        const authorization = await adapter.authorizeQuestion(
          { ...viewState.selectedSource, question: question.trim(), password },
          { signal, idempotencyKey },
        );
        stepUpAuthorizationId = authorization.step_up_authorization_id;
      }
      if (questionPasswordRef.current) questionPasswordRef.current.value = "";
      const answer = await adapter.askQuestion(
        { ...viewState.selectedSource, question: question.trim(), stepUpAuthorizationId },
        { signal, idempotencyKey },
      );
      if (signal.aborted) return;
      const safeAnswer = projectSafeQuestionAnswer(answer, workspaceId, adapter.citationUrl, viewState.selectedSource);
      setViewState((current) => ({ ...current, answer: safeAnswer }));
      reportIdempotencyRef.current = null;
    } catch (error) {
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
    reportIdempotencyRef.current = null;
    setViewState((current) => ({ ...current, selectedSource: source, answer: null }));
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
  const desktopEditor = typeof desktopOfflineStudio?.editor === "function"
    ? desktopOfflineStudio.editor(viewState)
    : desktopOfflineStudio?.editor;
  const desktopStudio = typeof desktopOfflineStudio?.studio === "function"
    ? desktopOfflineStudio.studio(viewState)
    : desktopOfflineStudio?.studio;
  const openModal = (view, opener) => {
    modalOpenerRef.current = opener ?? document.activeElement;
    setSettingsMenuOpen(false);
    setModalView(view);
  };
  const closeModal = () => {
    setModalView(null);
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
              <button type="button" role="menuitem" disabled><span className="menu-icon icon-output" aria-hidden="true" /><span><strong>출력·버전</strong><small>준비 중</small></span></button>
              <button type="button" role="menuitem" disabled><span className="menu-icon icon-sync" aria-hidden="true" /><span><strong>동기화·승인</strong><small>준비 중</small></span></button>
              <button type="button" role="menuitem" disabled><span className="menu-icon icon-policy" aria-hidden="true" /><span><strong>조직 정책</strong><small>읽기 전용</small></span></button>
            </div> : null}
          </div>
        </div>
      </header>
      <div className="workspace-panes" data-layout="source-conversation-studio" aria-label="Workspace 3면">
        <SafePane id="product-pane-sources" title="Source·지식·권위" description="PDF 등록과 처리 상태는 실제 same-origin 연결만 사용합니다.">
          <label className="source-add-button"><span aria-hidden="true">＋</span>Source 추가<input type="file" accept="application/pdf" onChange={uploadPdf} disabled={!adapter} /></label>
          {processing ? <p className="source-inline-state" role="status">처리 중 · 잠시만 기다려 주세요.</p> : null}
          <ul className="source-list" aria-label="Source 목록">
            {viewState.sources.map((source) => (
              <li className="source-list-row" key={`${source.sourceId}:${source.sourceVersionId}`}>
                <button type="button" onClick={() => selectSource(source)} disabled={!source.ready}>
                  <span className="source-file-icon" aria-hidden="true">PDF</span><span className="source-row-copy"><strong>{source.filename ?? source.sourceId}</strong><small>Version {source.sourceVersionId} · {source.ready ? "사용 가능" : "처리 중"}</small></span><span className={`source-ready-dot ${source.ready ? "is-ready" : ""}`} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          {!viewState.sources.length ? <div className="pane-empty"><span aria-hidden="true">◇</span><strong>Source를 추가해 주세요.</strong><small>PDF를 등록하면 질문과 Studio 생성에 사용할 수 있습니다.</small></div> : null}
          {viewState.safeError ? <p className="safe-error inline-alert" role="alert">Source를 불러오지 못했습니다. 운영상태에서 연결을 확인해 주세요.</p> : null}
        </SafePane>
        <SafePane id="product-pane-conversation" title="대화·실행" description="ready Source 선택 후 실제 질문과 Citation을 사용합니다.">
          {desktopEditor ? desktopEditor : <div className="conversation-workspace"><div className="conversation-transcript" aria-live="polite">
          {viewState.answer ? <article className="assistant-message"><div className="assistant-avatar" aria-hidden="true">D</div><div><span className="message-author">Daon</span><p>{viewState.answer.answer}</p></div></article> : <div className="conversation-empty"><span className="conversation-orbit" aria-hidden="true">✦</span><h3>Source에 대해 무엇이든 물어보세요.</h3><p>선택한 Source의 근거와 Citation을 사용해 답합니다.</p></div>}
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
            }}>Citation · {citation.page}쪽</a>
          ))}</div></div><form className="conversation-composer" onSubmit={askQuestion}>
            <label><span className="sr-only">질문</span><textarea rows="2" placeholder={viewState.selectedSource ? "Source에 대해 질문하세요" : "먼저 Source를 선택하세요"} value={question} onChange={(event) => setQuestion(event.currentTarget.value)} disabled={!viewState.selectedSource} /></label>
            <details className="composer-auth"><summary title="외부 Provider 정책이 요구할 때만 사용합니다.">추가 인증</summary><label>현재 비밀번호<input ref={questionPasswordRef} type="password" autoComplete="current-password" /></label></details>
            <button className="composer-submit" type="submit" aria-label="질문 실행" disabled={!viewState.selectedSource || !question.trim()}>↑</button>
          </form></div>}
        </SafePane>
        <SafePane id="product-pane-studio" title="업무 Studio" description="근거가 확인된 답변으로 보고서를 생성하고 저장 결과를 확인합니다.">
          {desktopStudio ? desktopStudio : <><ProductStudioPane
            key={`${viewState.answer?.run_id ?? "no-run"}:${viewState.selectedSource?.sourceVersionId ?? "no-source"}`}
            adapter={adapter}
            state={createProductStudioState({
              workspaceId,
              grounded: canCreateGroundedReport(viewState) ? {
                sourceId: viewState.selectedSource.sourceId,
                sourceVersionId: viewState.selectedSource.sourceVersionId,
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
          <header className="workspace-modal-header"><div><span className="section-kicker">WORKSPACE SETTINGS</span><h2 id="workspace-modal-title">{modalView === "llm" ? "LLM 설정" : "운영상태"}</h2></div><button className="modal-close" type="button" onClick={closeModal} aria-label="닫기">×</button></header>
          {modalView === "llm" ? <div className="workspace-modal-content">{providerSettings ?? <div className="modal-unavailable" role="status"><strong>Provider 설정 연결이 필요합니다.</strong><p>현재 Workspace에서는 설정 상태를 불러올 수 없습니다.</p><button type="button" disabled>연결 기능 준비 중</button></div>}</div> : <div className="operations-status-grid"><article><span className="status-dot" aria-hidden="true" /><div><strong>Web Workspace</strong><small>{STATE_LABELS[viewState.status]}</small></div></article><article><span className="status-dot" aria-hidden="true" /><div><strong>same-origin 연결</strong><small>브라우저 BFF 경계를 사용합니다.</small></div></article><article><span className="status-dot" aria-hidden="true" /><div><strong>저장된 산출물</strong><small>{viewState.studioOutputs.length}개</small></div></article></div>}
        </section>
      </div> : null}
    </main>
  );
}
