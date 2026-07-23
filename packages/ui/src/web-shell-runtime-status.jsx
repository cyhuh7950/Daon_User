"use client";

import { useCallback, useEffect, useState } from "react";
import "./web-shell-runtime.css";
import { createWebShellRuntimeState, transitionWebShellRuntime } from "./web-shell-runtime-model.js";

const STATUS_LABELS = { starting: "Shell 시작 중", ready: "Web Shell 준비", recovering: "Shell 복구 확인 중", unavailable: "Shell 상태 확인 불가" };

export function WebShellRuntimeStatus() {
  const [state, setState] = useState(createWebShellRuntimeState);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const loadRuntime = useCallback(async (signal) => {
    setState((current) => transitionWebShellRuntime(current, { type: "request-started" }));
    try {
      const response = await fetch("/bff/shell/runtime", { method: "GET", headers: { Accept: "application/json" }, cache: "no-store", signal });
      if (!response.ok) throw new Error("RUNTIME_UNAVAILABLE");
      const descriptor = await response.json();
      setState((current) => transitionWebShellRuntime(current, { type: "request-succeeded", descriptor }));
    } catch (error) {
      if (error?.name === "AbortError") return;
      setState((current) => transitionWebShellRuntime(current, { type: "request-failed", code: "RUNTIME_UNAVAILABLE" }));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadRuntime(controller.signal);
    return () => controller.abort();
  }, [loadRuntime]);

  const lastSuccess = state.last_success;
  return (
    <aside className={`web-shell-runtime web-shell-${state.status}`} data-shell-status={state.status} data-downstream-state={state.downstream_state} aria-live="polite">
      <span className="web-shell-runtime-dot" aria-hidden="true">●</span>
      <span className="web-shell-runtime-label">{STATUS_LABELS[state.status]}</span>
      <button className="web-shell-runtime-info" type="button" aria-label="Web Shell 상태 설명" aria-expanded={detailsOpen} aria-controls="web-shell-runtime-details" onClick={() => setDetailsOpen((open) => !open)} onKeyDown={(event) => { if (event.key === "Escape") setDetailsOpen(false); }}>i</button>
      {state.retryable && <button className="web-shell-runtime-retry" type="button" onClick={() => loadRuntime()}>재시도</button>}
      {detailsOpen && <div id="web-shell-runtime-details" className="web-shell-runtime-popover" role="status">
        <strong>{state.status === "ready" ? "Next Process와 BFF 경계가 응답합니다." : "현재 조회는 성공하지 않았습니다."}</strong>
        <span>Downstream: deferred_actual</span>
        {lastSuccess && <span>마지막 성공: {lastSuccess.observed_at}</span>}
        {lastSuccess && <span>Build: {lastSuccess.build_id}</span>}
      </div>}
    </aside>
  );
}
