"use client";

import { useEffect, useRef, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import { canCreateGroundedReport, createProductWorkspaceState, normalizeProductWorkspaceState } from "./product-workspace-model.js";
import "./workspace.css";

const STATE_LABELS = Object.freeze({
  loading: "Workspace를 불러오는 중입니다.",
  empty: "표시할 Workspace 자료가 없습니다.",
  ready: "Workspace가 준비되었습니다.",
  error: "Workspace 처리 중 안전 오류가 발생했습니다.",
  forbidden: "현재 권한으로 Workspace를 열 수 없습니다.",
  unavailable: "실제 Workspace 연결이 아직 준비되지 않았습니다."
});

function SafePane({ id, title, description, children }) {
  return (
    <div className="pane-slot">
      <section id={id} className="workspace-pane" aria-labelledby={`${id}-title`}>
        <div className="pane-heading">
          <h2 id={`${id}-title`}>{title}</h2>
        </div>
        <p className="visible-state">{description}</p>
        {children}
      </section>
    </div>
  );
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
    && typeof status.job_state === "string"
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
    || !PENDING_JOB_STATES.has(status.job_state)
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

export async function submitGroundedReport({ adapter, state, title, purpose, idempotencyKey }) {
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
  }, { idempotencyKey });
  return { submitted: true, outputs: await adapter.listStudioOutputs() };
}

export async function submitGroundedReportForm(event, {
  adapter, state, title, purpose, idempotencyRef, uuid = () => crypto.randomUUID(),
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
  return submitGroundedReport({ adapter, state, title: normalized.title, purpose: normalized.purpose, idempotencyKey: key });
}

export function ProductWorkspaceShell({ workspaceId, state = createProductWorkspaceState(), adapter = null, processingPollOptions = null }) {
  const [viewState, setViewState] = useState(() => normalizeProductWorkspaceState(state));
  const [processing, setProcessing] = useState(null);
  const [question, setQuestion] = useState("");
  const [reportTitle, setReportTitle] = useState("");
  const [reportPurpose, setReportPurpose] = useState("");
  const [reportPending, setReportPending] = useState(false);
  const pollControllerRef = useRef(null);
  const reportIdempotencyRef = useRef(null);

  useEffect(() => {
    if (!adapter || typeof adapter.listSources !== "function" || !workspaceId) return undefined;
    const controller = new AbortController();
    Promise.all([
      adapter.listSources({ signal: controller.signal }),
      typeof adapter.listStudioOutputs === "function"
        ? adapter.listStudioOutputs({ signal: controller.signal })
        : Promise.resolve([])
    ]).then(([sources, studioOutputs]) => {
      if (controller.signal.aborted) return;
      const projected = sources.map((source) => ({
        sourceId: source.source_id,
        sourceVersionId: source.source_version_id,
        filename: source.filename,
        ready: source.source_state === "ready"
          && source.processing_state === "completed"
          && source.job_state === "completed"
      }));
      const selectedSource = projected.find((source) => source.ready) ?? null;
      setViewState({
        ...createProductWorkspaceState({ status: projected.length ? "ready" : "empty" }),
        sources: projected, selectedSource, studioOutputs
      });
    }).catch((error) => {
      if (!controller.signal.aborted) {
        setViewState(createProductWorkspaceState({ status: "error", safeError: safeErrorCode(error, "SOURCE_LIST_FAILED") }));
      }
    });
    return () => controller.abort();
  }, [adapter, workspaceId]);

  useEffect(() => () => {
    pollControllerRef.current?.abort();
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
      const answer = await adapter.askQuestion({ ...viewState.selectedSource, question: question.trim() });
      const safeAnswer = projectSafeQuestionAnswer(answer, workspaceId, adapter.citationUrl, viewState.selectedSource);
      setViewState((current) => ({ ...current, answer: safeAnswer }));
      reportIdempotencyRef.current = null;
    } catch (error) {
      setViewState(createProductWorkspaceState({ status: "error", safeError: safeErrorCode(error, "QUESTION_FAILED") }));
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
        idempotencyRef: reportIdempotencyRef,
      });
      if (result.submitted) setViewState((current) => ({ ...current, studioOutputs: result.outputs }));
    } catch (error) {
      setViewState((current) => ({ ...current, safeError: safeErrorCode(error, "STUDIO_CREATE_FAILED") }));
    } finally {
      setReportPending(false);
    }
  };

  const reportReady = canCreateGroundedReport(viewState);

  return (
    <main className="adaptive-workspace" data-product-workspace-state={viewState.status} data-workspace-id={workspaceId ?? ""}>
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Daon 사용자 Workspace</p>
          <h1>Workspace</h1>
        </div>
        <span className={`workspace-status status-${viewState.status}`} role="status">{STATE_LABELS[viewState.status]}</span>
      </header>
      <div className="workspace-panes" aria-label="Workspace 3면">
        <SafePane id="product-pane-sources" title="Source·지식·권위" description="PDF 등록과 처리 상태는 실제 same-origin 연결만 사용합니다.">
          <label>PDF Source<input type="file" accept="application/pdf" onChange={uploadPdf} disabled={!adapter} /></label>
          {processing ? <p role="status">처리 상태: {processing.job_state ?? "PROCESSING_STATUS_AVAILABLE"}</p> : null}
          <ul aria-label="Source 목록">
            {viewState.sources.map((source) => (
              <li key={`${source.sourceId}:${source.sourceVersionId}`}>
                <button type="button" onClick={() => selectSource(source)} disabled={!source.ready}>
                  {source.filename ?? source.sourceId}
                </button>
              </li>
            ))}
          </ul>
        </SafePane>
        <SafePane id="product-pane-conversation" title="대화·실행" description="ready Source 선택 후 실제 질문과 Citation을 사용합니다.">
          <form onSubmit={askQuestion}>
            <label>질문<input value={question} onChange={(event) => setQuestion(event.currentTarget.value)} disabled={!viewState.selectedSource} /></label>
            <button type="submit" disabled={!viewState.selectedSource || !question.trim()}>질문 실행</button>
          </form>
          {viewState.answer ? <p>{viewState.answer.answer}</p> : null}
          {viewState.answer?.citations?.map((citation) => (
            <a key={citation.citation_id} href={citation.content_url}>Citation page {citation.page}</a>
          ))}
        </SafePane>
        <SafePane id="product-pane-studio" title="업무 Studio" description="근거가 확인된 답변으로 보고서를 생성하고 저장 결과를 확인합니다.">
          <form onSubmit={createReport}>
            <label>보고서 제목<input value={reportTitle} maxLength={200} onChange={(event) => setReportTitle(event.currentTarget.value)} /></label>
            <label>결과 목적<input value={reportPurpose} maxLength={500} onChange={(event) => setReportPurpose(event.currentTarget.value)} /></label>
            <button type="submit" disabled={!reportReady || !reportTitle.trim() || !reportPurpose.trim() || reportPending}>보고서 생성</button>
          </form>
          <ul aria-label="저장된 보고서">
            {viewState.studioOutputs.map((output) => (
              <li key={output.studio_output_id}>
                <strong>{output.title}</strong>
                <span>{output.status}</span>
                <ul aria-label={`${output.title} Citation 계보`}>
                  {output.citations.map((citation) => (
                    <li key={citation.citation_id}>Citation page {citation.page}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </SafePane>
      </div>
      {viewState.safeError ? <p className="safe-error" role="alert">{viewState.safeError}</p> : null}
    </main>
  );
}
