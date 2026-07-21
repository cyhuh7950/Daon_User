"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import accessibilityContract from "../accessibility-contract.json";
import "@daon-user/design-tokens/tokens.css";
import "./workspace.css";
import { createWorkspaceViewState, PANE_IDS, projectWorkspace, transitionWorkspace } from "./workspace-model.js";
import { focusInitialModalControl, setBackgroundInert, transitionHelp, trapModalTab } from "./workspace-interaction.js";
import { SourceKnowledgePane } from "./source-knowledge-pane.jsx";
import { RunModelEvidencePane } from "./run-model-evidence-pane.jsx";

const PANE_LABELS = { knowledge: "자료·지식", conversation: "대화·실행", studio: "업무 Studio" };

function Status({ tone, children }) {
  return <span className={`workspace-status status-${tone}`}><span aria-hidden="true">●</span>{children}</span>;
}

function InfoButton({ id, label }) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef(null);
  const tooltipId = `tooltip-${id}`;
  const change = (action) => setOpen((current) => transitionHelp(current, action));
  return (
    <span className="info-control" ref={wrapperRef} onPointerEnter={() => change("pointer-enter")} onPointerLeave={() => change("pointer-leave")} onBlur={(event) => { if (!wrapperRef.current?.contains(event.relatedTarget)) change("blur"); }}>
      <button id={`info-${id}`} className="icon-button" type="button" aria-label={label} aria-describedby={open ? tooltipId : undefined} aria-expanded={open} aria-controls={tooltipId} onFocus={() => change("focus")} onClick={() => change("open")} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); change("escape"); } }}>i</button>
      {open && <span id={tooltipId} className="info-tooltip" role="tooltip">{label}. 선택 면의 현재 상태와 사용할 수 있는 동작을 설명합니다.</span>}
    </span>
  );
}

function Pane({ pane, state, onOpenEvidence, onSetCursor, onSelectSource, onSourceKnowledgeAction, onRunModelAction }) {
  if (pane === "knowledge") return <SourceKnowledgePane selectedSourceId={state.selected_source_id} domainState={state.source_knowledge} onDomainAction={onSourceKnowledgeAction} onSelectSource={onSelectSource} onOpenEvidence={onOpenEvidence} />;
  if (pane === "conversation") return <RunModelEvidencePane domainState={state.run_model} onDomainAction={onRunModelAction} onOpenEvidence={onOpenEvidence} />;
  return (
    <section className="workspace-pane" id="pane-studio" aria-labelledby="pane-studio-title">
      <div className="pane-heading"><div><p className="eyebrow">열린 산출물</p><h2 id="pane-studio-title">업무 Studio</h2></div><InfoButton id="studio" label="업무 Studio 면 설명" /></div>
      <article className="artifact-card"><div className="card-row"><strong>근거 기반 보고서 초안</strong><Status tone="ready">ready</Status></div><p className="secondary">{state.artifact_id}</p><label htmlFor="artifact-cursor">편집 위치</label><select id="artifact-cursor" value={state.artifact_cursor} onChange={(event) => onSetCursor(event.target.value)}><option value="section-2:paragraph-3">2절 · 3문단</option><option value="section-3:table-1">3절 · 표 1</option></select></article>
      <button className="secondary-action" type="button" disabled title="M2-05에서 연결">생성 설정 · unavailable</button>
    </section>
  );
}

export function AdaptiveWorkspace({ routeId = "workspace_detail", screenId = "workspace_detail" }) {
  const [viewState, setViewState] = useState(() => createWorkspaceViewState());
  const [viewportWidth, setViewportWidth] = useState(1440);
  const overlayTriggerIds = useRef([]);
  const pendingFocusId = useRef("");
  const dragRef = useRef(null);
  const workspaceSurfaceRef = useRef(null);
  const modalRef = useRef(null);
  const projection = useMemo(() => projectWorkspace(viewState, viewportWidth), [viewState, viewportWidth]);

  useEffect(() => {
    const update = () => setViewportWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);

  useEffect(() => {
    const modal = modalRef.current;
    const background = workspaceSurfaceRef.current;
    setBackgroundInert(background, Boolean(modal));
    const focusTimer = setTimeout(() => {
      const pendingTarget = document.getElementById(pendingFocusId.current);
      if (pendingTarget) {
        pendingTarget.focus();
        pendingFocusId.current = "";
      } else if (modal) {
        focusInitialModalControl(modal);
      }
    }, 0);
    if (!modal) return () => clearTimeout(focusTimer);
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeOverlay();
        return;
      }
      trapModalTab(modal, event);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKeyDown);
      setBackgroundInert(background, false);
    };
  }, [viewState.open_drawer]);

  const updateState = (action) => setViewState((current) => transitionWorkspace(current, action));
  const activatePane = (pane) => updateState({ type: "activate-pane", pane });
  const openPaneDrawer = (pane, event) => { overlayTriggerIds.current.push(event.currentTarget.id); updateState({ type: "open-drawer", pane }); };
  const openEvidence = (evidenceOrEvent, eventMaybe) => {
    const event = eventMaybe ?? evidenceOrEvent;
    const evidence = eventMaybe ? evidenceOrEvent : undefined;
    overlayTriggerIds.current.push(event.currentTarget.id);
    updateState({ type: "open-evidence", evidence });
  };
  const closeOverlay = () => {
    const triggerId = overlayTriggerIds.current.pop();
    pendingFocusId.current = triggerId ?? "";
    setViewState((current) => transitionWorkspace(current, { type: "close-overlay" }));
  };
  const resizePane = (pane, delta) => updateState({ type: "resize-pane", pane, delta });
  const onResizeKeyDown = (pane, event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    resizePane(pane, event.key === 'ArrowLeft' ? -2 : 2);
  };
  const onResizePointerDown = (pane, event) => {
    dragRef.current = { pane, x: event.clientX };
    const onMove = (moveEvent) => {
      if (!dragRef.current) return;
      const delta = ((moveEvent.clientX - dragRef.current.x) / Math.max(window.innerWidth, 1)) * 100;
      dragRef.current.x = moveEvent.clientX;
      resizePane(pane, delta);
    };
    const onUp = () => { dragRef.current = null; document.removeEventListener("pointermove", onMove); document.removeEventListener("pointerup", onUp); };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
  };
  const accessibilityStandard = accessibilityContract.standard;

  return (
    <main className={`adaptive-workspace mode-${projection.layoutMode}`} data-layout-mode={projection.layoutMode} data-route-id={routeId} data-screen-id={screenId} data-accessibility-standard={accessibilityStandard} data-selected-source={viewState.selected_source_id} data-conversation-id={viewState.conversation_id} data-run-id={viewState.run_id} data-run-status={viewState.run_status} data-artifact-id={viewState.artifact_id} data-artifact-cursor={viewState.artifact_cursor} data-evidence-id={viewState.evidence_id} data-evidence-position={viewState.evidence_position} data-evidence-source={viewState.evidence_source_id} data-evidence-source-version={viewState.evidence_source_version_id}>
      <div className="workspace-surface" ref={workspaceSurfaceRef}>
      <header id="workspace-header" className="workspace-header"><div><p className="eyebrow">Workspace</p><h1>Release 1 운영 준비</h1></div><div className="header-status"><Status tone="unavailable">실행 unavailable</Status><span className="layout-badge">{projection.layoutMode}</span><span className="prototype-badge">프로토타입 데이터</span></div></header>

      {projection.layoutMode !== "three-pane" && <nav className="pane-switcher" aria-label="작업 면 전환">{PANE_IDS.map((pane) => <button type="button" key={pane} aria-pressed={viewState.active_pane === pane} onClick={() => activatePane(pane)}>{PANE_LABELS[pane]}</button>)}</nav>}

      <div className="workspace-panes">
        {projection.visiblePanes.map((pane, index) => <div className="pane-slot" key={pane} style={projection.layoutMode === "three-pane" ? { flexBasis: `${viewState.pane_sizes[pane]}%` } : undefined}><Pane pane={pane} state={viewState} onOpenEvidence={openEvidence} onRunModelAction={(domainAction) => updateState({ type: "run-model", domainAction })} onSourceKnowledgeAction={(domainAction) => updateState({ type: "source-knowledge", domainAction })} onSelectSource={(sourceId) => updateState({ type: "select-source", sourceId })} onSetCursor={(cursor) => updateState({ type: "set-artifact-cursor", cursor })} />{projection.layoutMode === "three-pane" && index < projection.visiblePanes.length - 1 && <div className="resize-handle" id={`resize-handle-${pane}`} role="separator" aria-label={`${PANE_LABELS[pane]} 너비 조절`} aria-orientation="vertical" aria-valuenow={Math.round(viewState.pane_sizes[pane])} tabIndex={0} onKeyDown={(event) => onResizeKeyDown(pane, event)} onPointerDown={(event) => onResizePointerDown(pane, event)} />}</div>)}
      </div>

      {projection.layoutMode === "two-pane" && projection.hiddenPanes.map((pane) => <button id={`drawer-trigger-${pane}`} className="drawer-launcher" type="button" key={pane} onClick={(event) => openPaneDrawer(pane, event)}>{PANE_LABELS[pane]} Drawer 열기</button>)}
      {projection.layoutMode === "single-pane" && <div className="drawer-launchers">{projection.hiddenPanes.map((pane) => <button id={`drawer-trigger-${pane}`} type="button" key={pane} onClick={(event) => openPaneDrawer(pane, event)}>{PANE_LABELS[pane]} 보조 Drawer</button>)}</div>}

      {projection.layoutMode === "bottom-tabs" && <nav id="bottom-tabs" className="bottom-tabs" aria-label="모바일 작업 면">{PANE_IDS.map((pane) => <button type="button" key={pane} aria-current={viewState.active_pane === pane ? "page" : undefined} onClick={() => activatePane(pane)}>{PANE_LABELS[pane]}</button>)}</nav>}
      </div>

      {viewState.open_drawer && viewState.open_drawer !== "evidence" && <div className="overlay-backdrop"><aside ref={modalRef} id="workspace-drawer" className="workspace-drawer" role="dialog" aria-modal="true" aria-label={`${PANE_LABELS[viewState.open_drawer]} Drawer`}><button autoFocus data-modal-initial-focus className="close-button" type="button" aria-label="Drawer 닫기" title="Drawer 닫기" onClick={closeOverlay}>×</button><Pane pane={viewState.open_drawer} state={viewState} onOpenEvidence={openEvidence} onRunModelAction={(domainAction) => updateState({ type: "run-model", domainAction })} onSourceKnowledgeAction={(domainAction) => updateState({ type: "source-knowledge", domainAction })} onSelectSource={(sourceId) => updateState({ type: "select-source", sourceId })} onSetCursor={(cursor) => updateState({ type: "set-artifact-cursor", cursor })} /></aside></div>}

      {viewState.open_drawer === "evidence" && <div className="overlay-backdrop"><aside ref={modalRef} id="evidence-viewer" className={`evidence-viewer ${projection.evidencePresentation}`} role="dialog" aria-modal="true" aria-labelledby="evidence-title"><div className="viewer-header"><div><p className="eyebrow">Source 원문</p><h2 id="evidence-title">근거 Viewer</h2></div><button autoFocus data-modal-initial-focus className="close-button" type="button" aria-label="근거 Viewer 닫기" title="근거 Viewer 닫기" onClick={closeOverlay}>×</button></div><p><strong>{viewState.evidence_name}</strong></p><dl className="version-snapshot"><div><dt>Source</dt><dd>{viewState.evidence_source_id}</dd></div><div><dt>Source Version</dt><dd>{viewState.evidence_source_version_id}</dd></div><div><dt>Evidence</dt><dd>{viewState.evidence_id}</dd></div><div><dt>종류</dt><dd>{viewState.evidence_kind}</dd></div></dl><p className="evidence-position">{viewState.evidence_position}</p><blockquote>{viewState.evidence_excerpt}</blockquote></aside></div>}
    </main>
  );
}
