"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import {
  createProductionBoundEvidenceState,
  projectProductionBoundEvidence,
  transitionProductionBoundEvidence
} from "./evidence-hub-model.js";
import "./evidence-hub.css";

const STORAGE_KEY = "daon-m2-production-bound-evidence-state";

function InfoTip({ id, open, onToggle, children }) {
  return (
    <span className="evidence-info">
      <button type="button" aria-label="검증 수준 설명" aria-expanded={open} aria-describedby={open ? id : undefined} onClick={onToggle}>i</button>
      {open ? <span id={id} role="tooltip">{children}</span> : null}
    </span>
  );
}

export function EvidenceHubApp({ route, screen }) {
  const [state, dispatch] = useReducer(transitionProductionBoundEvidence, undefined, createProductionBoundEvidenceState);
  const [sessionRestored, setSessionRestored] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const projection = useMemo(() => projectProductionBoundEvidence(state, { viewport_width: typeof window === "undefined" ? 1920 : window.innerWidth }), [state]);
  const selectedClient = projection.clients.find((item) => item.client_type === projection.selected_client_type);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY));
      if (saved) {
        dispatch({ type: "select-client", client_type: saved.selected_client_type });
        dispatch({ type: "select-status", status: saved.selected_status });
        const checkedJourneyIds = Array.isArray(saved.checked_journey_ids) ? [...new Set(saved.checked_journey_ids)] : [];
        for (const journeyId of checkedJourneyIds) dispatch({ type: "toggle-journey-check", journey_id: journeyId });
      }
    } catch {
      // 손상된 Browser Session은 기존 Model의 기본 상태로 닫는다.
    } finally {
      setSessionRestored(true);
    }
  }, []);

  useEffect(() => {
    if (!sessionRestored) return;
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      selected_client_type: state.selected_client_type,
      selected_status: state.selected_status,
      checked_journey_ids: state.checked_journey_ids
    }));
  }, [sessionRestored, state]);

  useEffect(() => {
    const close = (event) => { if (event.key === "Escape") setHelpOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  return (
    <main className="evidence-hub" data-route-id={route.route_id} data-screen-id={screen.screen_id} data-client-type={projection.selected_client_type} data-state={projection.selected_status}>
      <p className="evidence-local-only" role="status">개발·검증 전용 · 사용자 제품 아님 · 외부 API와 상태 변경 없음</p>
      <header className="evidence-header">
        <div>
          <p className="evidence-eyebrow">M2 Exit · Production-bound Prototype Evidence Pack</p>
          <h1>Daon 사용자 프로그램 Evidence Hub</h1>
          <p>8개 사용자 여정과 Web·Windows·Android·iOS의 증명 수준을 한 흐름에서 추적합니다.</p>
        </div>
        <div className="evidence-header-status">
          <span className="evidence-badge evidence-observation">verified_with_observations</span>
          <span className="evidence-badge">prototype_fixture</span>
          <span className="evidence-badge">deferred_actual</span>
          <InfoTip id="evidence-level-help" open={helpOpen} onToggle={() => setHelpOpen((value) => !value)}>
            M2는 Web Prototype과 플랫폼 계약 Projection을 증명합니다. 실제 API·DB·LLM·Native Runtime 성공은 후속 M3~M9 책임입니다.
          </InfoTip>
        </div>
      </header>

      <section className="evidence-controls" aria-label="Evidence 표시 설정">
        <div>
          <strong>플랫폼</strong>
          <div className="evidence-segmented">
            {projection.clients.map((client) => (
              <button key={client.client_type} type="button" aria-pressed={projection.selected_client_type === client.client_type} onClick={() => dispatch({ type: "select-client", client_type: client.client_type })}>{client.label}</button>
            ))}
          </div>
        </div>
        <label>
          <strong>화면 상태</strong>
          <select value={projection.selected_status} onChange={(event) => dispatch({ type: "select-status", status: event.target.value })}>
            {['loading', 'empty', 'ready', 'warning', 'error', 'forbidden', 'unavailable'].map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <div className="evidence-current-proof" aria-live="polite">
          <strong>{selectedClient.label} · {projection.selected_status}</strong>
          <span>{selectedClient.proof}</span>
          <small>실제 Native Runtime 실행: {selectedClient.native_runtime_executed ? "완료" : "미실행"}</small>
        </div>
      </section>

      <nav className="evidence-route-strip" aria-label="실제 Prototype Route">
        <a href="/workspaces/workspace-release-one">Workspace</a>
        <a href="/settings/account">Account</a>
        <a href="/settings/organization">Organization</a>
        <a href="/operations">Operations</a>
        <a href="/notifications">Notifications</a>
      </nav>

      <section className="evidence-layout">
        <div className="evidence-journeys">
          <div className="evidence-section-title">
            <div><h2>필수 사용자 여정 8종</h2><p>확인 상태는 이 Browser Session에만 보존되며 서버 저장 성공이 아닙니다.</p></div>
            <span>{projection.checked_journey_ids.length}/8 확인</span>
          </div>
          <div className="evidence-journey-grid">
            {projection.journeys.map((journey) => {
              const checked = projection.checked_journey_ids.includes(journey.id);
              const matrix = projection.platform_journey_matrix.find((item) => item.client_type === projection.selected_client_type && item.journey_id === journey.id);
              return (
                <article key={journey.id} className="evidence-journey" data-checked={checked}>
                  <header><span>{journey.number}</span><div><h3>{journey.title}</h3><small>{matrix.verification_level} · {matrix.m3_owner}</small></div></header>
                  <p>{journey.summary}</p>
                  <dl><div><dt>Mock Adapter</dt><dd>{journey.mock_adapter}</dd></div><div><dt>Actual</dt><dd>deferred_actual</dd></div></dl>
                  <div className="evidence-journey-actions">
                    <button type="button" aria-pressed={checked} onClick={() => dispatch({ type: "toggle-journey-check", journey_id: journey.id })}>{checked ? "확인됨" : "확인 표시"}</button>
                    <a href={journey.routes[0].href}>화면 열기</a>
                  </div>
                </article>
              );
            })}
          </div>
        </div>

        <aside className="evidence-side" aria-label="플랫폼·오류 증거">
          <section className="evidence-card">
            <h2>플랫폼 정직성</h2>
            <dl className="evidence-platform-dl">
              <div><dt>client_type</dt><dd>{selectedClient.client_type}</dd></div>
              <div><dt>M3 Owner</dt><dd>{selectedClient.m3_owners.join(" · ")}</dd></div>
              <div><dt>DOM UI Import</dt><dd>{selectedClient.dom_ui_imported ? "Web 공용 UI" : "없음 · Contract Projection"}</dd></div>
              <div><dt>IPC/Local Service</dt><dd>{selectedClient.ipc_or_local_service_verified ? "검증" : "미실행"}</dd></div>
            </dl>
          </section>

          <section className="evidence-card">
            <h2>오류·권한·축소 운영</h2>
            <div className="evidence-state-links">
              {projection.negative_state_links.map((item) => <a key={item.code} href={item.href}><strong>{item.code}</strong><span>{item.label}</span></a>)}
            </div>
          </section>

          <section className="evidence-card evidence-observation-card">
            <h2>선행 Evidence 정합</h2>
            <p><strong>DIRECT 82 · SUCCESSOR 4 · LEGACY DRIFT 4 · UNEXPLAINED 0</strong></p>
            <p>Legacy Drift 4건은 TP-1 Observation이며 선행 Hash 완전성 PASS로 계산하지 않습니다.</p>
            <code>predecessor-evidence-reconciliation.json</code>
          </section>
        </aside>
      </section>
    </main>
  );
}
