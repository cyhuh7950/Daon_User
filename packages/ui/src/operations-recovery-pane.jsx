"use client";

import { useEffect, useId, useMemo, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import "./workspace.css";
import {
  createOperationsRecoveryViewState,
  projectOperationsRecovery,
  projectOperationsRecoveryRoute,
  transitionOperationsRecovery
} from "./operations-recovery-model.js";

function Info({ label }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  return <span className="operations-info"><button type="button" aria-label={`${label} 설명`} aria-expanded={open} aria-controls={tooltipId} aria-describedby={open ? tooltipId : undefined} onClick={() => setOpen((value) => !value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setOpen(false); } }}>i</button>{open && <span id={tooltipId} role="tooltip">{label}. 현재 화면은 Production-bound Prototype이며 실제 Adapter 연결은 후속 Work Order가 소유합니다.</span>}</span>;
}

function Badge({ children, tone = "neutral" }) {
  return <span className={`operations-badge operations-${tone}`}>{children}</span>;
}

function Header({ state, projection, dispatch }) {
  const navigate = (screen) => {
    const route = projectOperationsRecoveryRoute(screen);
    window.history.pushState({ screen }, "", route.path);
    dispatch({ type: "navigate", screen });
  };
  return <>
    <header className="operations-header">
      <div><p className="eyebrow">운영·알림·복구</p><h1>{projection.route.title}</h1></div>
      <div className="operations-header-status"><Badge tone="prototype">Production-bound Prototype</Badge><Badge>{projection.layoutMode}</Badge><Badge tone="warning">실제 API 미실행</Badge></div>
    </header>
    <nav className="operations-route-tabs" aria-label="운영과 알림 화면">
      <button type="button" aria-current={state.screen === "operations" ? "page" : undefined} onClick={() => navigate("operations")}>운영 상태</button>
      <button type="button" aria-current={state.screen === "notifications" ? "page" : undefined} onClick={() => navigate("notifications")}>알림</button>
      <a href="/settings/organization">조직 설정</a>
    </nav>
  </>;
}

function StatusCard({ title, help, children, span = false }) {
  return <section className={`operations-card${span ? " operations-span-2" : ""}`}><div className="operations-title"><h2>{title}</h2><Info label={help} /></div>{children}</section>;
}

function StatusDashboard({ state }) {
  return <>
    <StatusCard title="Service 상태" help="API·Worker·DB·Object Storage 안전 상태">
      <div className="operations-status-grid">{state.services.map((item) => <article key={item.id}><strong>{item.label}</strong><Badge tone={item.status === "healthy" ? "success" : "warning"}>{item.status}</Badge><small>{item.safeCode}</small></article>)}</div>
    </StatusCard>
    <StatusCard title="Queue 상태" help="처리·Index·실패·자동·수동 재처리 Queue">
      <label>Queue Filter<select value={state.queueFilter} onChange={(event) => state.dispatch({ type: "set-queue-filter", queueFilter: event.target.value })}><option value="all">전체</option>{state.queues.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <div className="operations-status-grid">{state.queues.filter((item) => state.queueFilter === "all" || item.id === state.queueFilter).map((item) => <article key={item.id}><strong>{item.label}</strong><Badge tone={item.status === "warning" ? "warning" : "success"}>{item.status}</Badge><small>{item.count}건</small></article>)}</div>
    </StatusCard>
    <StatusCard title="Model·Runtime Node" help="불투명 Deployment·역할·Health·Capacity">
      <div className="operations-list">{state.modelDeployments.map((item) => <p key={item.id}><strong>{item.kind} · {item.role}</strong><span>{item.health} · {item.capacity} · {item.id}</span></p>)}{state.runtimeNodes.map((item) => <p key={item.id}><strong>Runtime Node</strong><span>{item.status} · {item.capacity} · {item.id}</span></p>)}</div>
    </StatusCard>
    <StatusCard title="Connector·비용" help="Daon·인터넷과 사용자·조직 비용 한도">
      <div className="operations-list">{state.connectors.map((item) => <p key={item.id}><strong>{item.kind}</strong><span>{item.status} · {item.safeCode}</span></p>)}{state.costs.map((item) => <p key={item.scope}><strong>{item.scope}</strong><span>{item.storage} · {item.tokens} · {item.amount} · {item.code}</span></p>)}</div>
    </StatusCard>
    <StatusCard title="Backup·Restore·Update·Rollback" help="복구 목표·훈련 결과와 요청 Preview" span>
      <dl className="operations-dl"><div><dt>Backup</dt><dd>{state.backup.status} · 마지막 성공 {state.backup.lastSuccessfulAt}</dd></div><div><dt>복구 목표</dt><dd>RPO {state.backup.rpo} · RTO {state.backup.rto}</dd></div><div><dt>Restore Drill</dt><dd>{state.backup.restoreDrill} · 실제 Restore 0건</dd></div><div><dt>Update</dt><dd>{state.update.channel} · {state.update.status} · Rollback {String(state.update.rollbackAvailable)}</dd></div></dl>
      <p className="operations-visible-warning">`prototype_fixture` 요청 Preview와 `deferred_actual`을 분리합니다. G9-DRILL/G9-DEPLOY 승인 없는 실제 Backup·Restore·Update·Rollback은 0건입니다.</p>
    </StatusCard>
  </>;
}

function RecoveryApiPanel({ adapter, workspaceId }) {
  const [backups, setBackups] = useState([]);
  const [selected, setSelected] = useState(null);
  const [restore, setRestore] = useState(null);
  const [restoreEtag, setRestoreEtag] = useState(null);
  const [previewStepUp, setPreviewStepUp] = useState("");
  const [executeStepUp, setExecuteStepUp] = useState("");
  const [status, setStatus] = useState(adapter ? "loading" : "unavailable");
  const [safeError, setSafeError] = useState(null);
  const run = async (operation) => {
    setStatus("working"); setSafeError(null);
    try { await operation(); setStatus("ready"); }
    catch (error) { setSafeError({ code: error.code ?? "RESOURCE_UNAVAILABLE", traceId: error.traceId ?? "unavailable" }); setStatus("failed"); }
  };
  const refresh = () => run(async () => {
    const result = await adapter.listBackups(workspaceId);
    setBackups(Array.isArray(result.payload.data) ? result.payload.data : []);
  });
  useEffect(() => { if (adapter) void refresh(); }, [adapter, workspaceId]);
  if (!adapter) return <StatusCard title="Backup·Restore 실제 API" help="Web same-origin Recovery API 연결 상태" span><p className="operations-visible-warning">이 Client에는 Recovery API Adapter가 연결되지 않았습니다.</p></StatusCard>;
  const requestBackup = () => run(async () => {
    await adapter.createBackup({
      workspace_id: workspaceId, trigger: "manual", schema_revision: "0006",
      retention_watermark: "current-retention",
      objects: [{ object_id: "fixture-ui-object", checksum_sha256: "a".repeat(64), byte_size: 1 }]
    }, `backup-ui-${Date.now()}`);
    const result = await adapter.listBackups(workspaceId);
    setBackups(result.payload.data);
  });
  const preview = () => run(async () => {
    const result = await adapter.previewRestore(selected.backup_id, {
      destination: {
        tenant_id: "fixture-ui-tenant", workspace_id: "fixture-ui-workspace",
        database_id: "fixture-ui-database", bucket_id: "fixture-ui-bucket"
      },
      step_up_authorization_id: previewStepUp
    }, `restore-preview-ui-${Date.now()}`);
    setRestore(result.payload.data); setRestoreEtag(result.etag);
  });
  const execute = () => run(async () => {
    const result = await adapter.executeRestore(restore.request_id, {
      preview_version: restore.preview.version,
      step_up_authorization_id: executeStepUp
    }, restoreEtag, `restore-execute-ui-${Date.now()}`);
    setRestore(result.payload.data); setRestoreEtag(result.etag);
  });
  return <StatusCard title="Backup·Restore 실제 API" help="same-origin 7개 Cloud Recovery Route의 요청·목록·Preview·진행·결과" span>
    <div className="operations-actions"><button type="button" onClick={refresh}>목록 새로고침</button><button type="button" onClick={requestBackup}>전용 Fixture Backup 요청</button></div>
    <p role="status" aria-live="polite">{status}{safeError ? ` · ${safeError.code} · Trace ${safeError.traceId}` : ""}</p>
    <div className="operations-list">{backups.map((item) => <p key={item.backup_id}><button type="button" onClick={() => setSelected(item)}>{item.backup_id}</button><span>{item.state} · Schema {item.schema_revision} · Object {item.object_count}</span></p>)}</div>
    {selected && <div className="operations-lineage"><strong>Restore Preview</strong><span>대상 {selected.backup_id} · 격리 Fixture 목적지</span><label>Preview Step-up ID<input value={previewStepUp} onChange={(event) => setPreviewStepUp(event.target.value)} /></label><button type="button" disabled={!previewStepUp} onClick={preview}>현재 권한·Retention 재검증 Preview</button></div>}
    {restore && <div className="operations-lineage"><strong>{restore.state}</strong><span>포함 {restore.preview.included_object_ids.length} · 제외 {restore.preview.excluded_object_ids.length}</span><span>{restore.preview.exclusion_reasons.map(([id, reason]) => `${id}:${reason}`).join(" · ") || "제외 없음"}</span><label>Execute용 새 Step-up ID<input value={executeStepUp} onChange={(event) => setExecuteStepUp(event.target.value)} /></label><button type="button" disabled={restore.state !== "preview_ready" || !executeStepUp} onClick={execute}>격리 Restore 실행</button></div>}
    <p className="operations-visible-warning">Preview와 Execute는 서로 다른 Step-up을 요구합니다. 운영 대상·제자리 덮어쓰기는 API에서 Fail-close됩니다.</p>
  </StatusCard>;
}

function RetryPanel({ state, dispatch }) {
  const latest = state.processingRuns.at(-1);
  const manual = () => dispatch({ type: "manual-retry", idempotencyKey: `manual-preview-${state.processingRuns.length}`, role: state.membership?.role, capability: "processing.retry", tenantId: state.tenantId, workspaceId: state.workspaceId, sourceId: state.waitingSource.sourceId });
  const readiness = () => dispatch({ type: "readiness-event", eventId: "readiness-demo-001", deploymentState: "ready", nodeState: "healthy", providerState: "healthy", requiredRole: state.waitingSource.requiredRole });
  return <StatusCard title="waiting_model 재처리" help="Readiness Event 제한 자동 Queue와 현재 권한 수동 새 Run" span>
    <dl className="operations-dl"><div><dt>SourceVersion</dt><dd>{state.waitingSource.sourceVersionId}</dd></div><div><dt>필수 역할</dt><dd>{state.waitingSource.requiredRole}</dd></div><div><dt>이전 ProcessingRun</dt><dd>{state.processingRuns[0].id} · 불변 {String(state.processingRuns[0].immutable)}</dd></div><div><dt>Mode·Backoff</dt><dd>{state.waitingSource.selectionMode} · {state.waitingSource.backoffUntil}</dd></div></dl>
    <div className="operations-actions" aria-label="재처리 Mode">
      {['auto', 'local_only', 'pinned', 'direct'].map((mode) => <button type="button" key={mode} aria-pressed={state.waitingSource.selectionMode === mode} onClick={() => dispatch({ type: "set-selection-mode", mode })}>{mode}</button>)}
    </div>
    <div className="operations-actions">
      <button type="button" onClick={readiness}>healthy Readiness Event 자동 Queue</button>
      <button type="button" onClick={manual}>현재 권한 수동 재처리 Preview</button>
      <button type="button" onClick={readiness}>같은 Event 중복 억제</button>
    </div>
    <div className="operations-actions">
      <button type="button" disabled={!latest?.retryOfProcessingRunId} onClick={() => dispatch({ type: "complete-retry-preview", runId: latest.id, outcome: "ready" })}>이해·검증→indexing→ready</button>
      <button type="button" disabled={!latest?.retryOfProcessingRunId} onClick={() => dispatch({ type: "complete-retry-preview", runId: latest.id, outcome: "policy_blocked" })}>정책 후보 0</button>
      <button type="button" disabled={!latest?.retryOfProcessingRunId} onClick={() => dispatch({ type: "complete-retry-preview", runId: latest.id, outcome: "runtime_exhausted" })}>Runtime 재소진</button>
    </div>
    {latest?.retryOfProcessingRunId && <div className="operations-lineage"><strong>새 Run 계보</strong><span>{latest.id}</span><span>retry_of_processing_run_id: {latest.retryOfProcessingRunId}</span><span>trigger_type: {latest.triggerType}</span><span>trigger_event_id: {latest.triggerEventId ?? "manual"}</span><span>현재 ACL·영역·RoutingPolicyVersion·비용·외부 전송 Snapshot</span><span>실제 ProcessingRun 생성 {String(latest.actualRunCreated)}</span></div>}
    <p className="operations-visible-warning">pinned·직접 선택은 Readiness Event만으로 자동 실행하지 않습니다. 활성 Run·Event ID·Idempotency Key·Backoff 중복은 새 Run 0건으로 억제합니다.</p>
  </StatusCard>;
}

const FAILURE_LABELS = {
  daon: "Daon Connector", external_llm: "External LLM", local_llm: "Local LLM",
  internet: "인터넷 Connector", index: "Index", evidence_store: "Evidence Store"
};

function FailureRecovery({ state, dispatch }) {
  const incident = state.incidents.find(({ id }) => id === state.selectedIncidentId) ?? state.incidents[0];
  const nextStatus = { detected: "warning", warning: "restricted", restricted: "recovering", recovering: "recovered" }[incident.status];
  return <>
    <StatusCard title="장애별 축소 운영" help="각 장애를 독립 주입하고 다른 Service를 바꾸지 않는 Prototype Fixture" span>
      <div className="operations-actions">{Object.entries(FAILURE_LABELS).map(([failure, label]) => <button type="button" key={failure} onClick={() => dispatch({ type: "inject-failure", failure })}>{label} 장애</button>)}</div>
      {state.degradation && <div className="operations-visible-warning" aria-live="polite"><strong>{FAILURE_LABELS[state.degradation.failure]} · {state.degradation.state}</strong><span>{state.degradation.message}</span><span>{state.degradation.invariant} · 금지된 우회 {state.degradation.unauthorizedFallbackCount}건 · 실제 Service 변경 {state.degradation.actualServiceChanges}건</span></div>}
    </StatusCard>
    <StatusCard title="Incident·복구" help="detected→warning→restricted→recovering→recovered 명시 상태">
      <label>Incident<select value={incident.id} onChange={(event) => dispatch({ type: "select-incident", incidentId: event.target.value })}>{state.incidents.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.status}</option>)}</select></label>
      <ol className="incident-steps">{["detected", "warning", "restricted", "recovering", "recovered"].map((status) => <li key={status} data-current={incident.status === status}>{status}</li>)}</ol>
      <button type="button" disabled={!nextStatus} onClick={() => dispatch({ type: "advance-incident", incidentId: incident.id, status: nextStatus })}>다음 복구 단계: {nextStatus ?? "완료"}</button>
    </StatusCard>
    <StatusCard title="보안 운영 신호" help="비용·Step-up·과거 AccessDecision 집계">
      <div className="operations-list"><p><strong>비용</strong><span>{state.securitySignals.cost.code} · Frozen Context 자동 재시도 {String(state.securitySignals.cost.retryableInFrozenContext)}</span></p><p><strong>Step-up</strong><span>{state.securitySignals.stepUp.states.join(" · ")} · 실제 Write 0건</span></p><p><strong>AccessDecision</strong><span>{state.securitySignals.accessDecision.states.join(" · ")} · 원본 OutputVersion 변경 0건</span></p></div>
      <div className="operations-actions"><button type="button" onClick={() => dispatch({ type: "preview-recovery", action: "restore" })}>Step-up 없이 Restore</button><button type="button" onClick={() => dispatch({ type: "preview-recovery", action: "restore", stepUpAuthorizationId: "stepup-restore-001" })}>승인 없는 Restore Preview</button><button type="button" onClick={() => dispatch({ type: "preview-recovery", action: "update", stepUpAuthorizationId: "stepup-update-001" })}>승인 없는 Update Preview</button></div>
    </StatusCard>
  </>;
}

function AlertAudit({ state, dispatch }) {
  const signal = () => dispatch({ type: "signal-alert", tenantId: state.tenantId, workspaceId: state.workspaceId, resource: "model-deployment-vision-001", safeCode: "MODEL_UNAVAILABLE", policyVersion: state.policyVersion });
  return <StatusCard title="Alert·Audit Preview" help="안정 Alert Key 중복 억제와 Append-only Audit" span>
    <div className="operations-actions"><button type="button" onClick={signal}>운영 경고 Signal</button><button type="button" onClick={signal}>같은 Signal Count 갱신</button></div>
    <div className="operations-alerts">{state.alerts.map((item) => <article key={`${item.incidentId}-${item.generation}`}><strong>{item.safeCode} · 세대 {item.generation}</strong><span>Count {item.count} · {item.status} · {item.scope}</span><span>재시도 {String(item.retryable)} · {item.userAction} · {item.traceId}</span></article>)}</div>
    <div className="operations-audit">{state.auditEvents.slice(-5).map((item) => <p key={item.id}><strong>{item.action}</strong> · {item.target} · {item.decision} · {item.code} · {item.traceId}</p>)}</div>
  </StatusCard>;
}

function OperationsView({ state, dispatch, recoveryAdapter }) {
  const boundState = useMemo(() => ({ ...state, dispatch }), [state, dispatch]);
  return <main className="operations-grid"><StatusDashboard state={boundState} /><RecoveryApiPanel adapter={recoveryAdapter} workspaceId={state.workspaceId} /><RetryPanel state={state} dispatch={dispatch} /><FailureRecovery state={state} dispatch={dispatch} /><AlertAudit state={state} dispatch={dispatch} /></main>;
}

function NotificationsView({ state, dispatch }) {
  return <main className="operations-grid"><StatusCard title="경고·진행·권한·복구 알림" help="시간·Severity·상태·대상·안전 Code·Trace ID와 Incident Deep Link" span>
    <div className="notification-list">{state.notifications.map((item) => {
      const alert = state.alerts.find(({ incidentId }) => incidentId === item.incidentId);
      const incident = state.incidents.find(({ id }) => id === item.incidentId);
      return <article key={item.id} data-read={item.read}><div><Badge tone={item.severity === "info" ? "success" : "warning"}>{item.severity}</Badge><strong>{item.kind} · {item.status}</strong></div><p>{item.message}</p><small>{item.occurredAt} · {item.target} · {item.safeCode} · {item.traceId}</small>{alert && <small>Alert Count {alert.count} · 세대 {alert.generation}</small>}{incident && <small>Incident 상태 {incident.status} · Deep Link {item.deepLink}</small>}<div className="operations-actions"><button type="button" onClick={() => dispatch({ type: "mark-notification-read", notificationId: item.id })}>{item.read ? "읽음" : "읽음 처리 Preview"}</button>{item.incidentId && <button type="button" onClick={() => { window.history.pushState({ screen: "operations" }, "", item.deepLink); dispatch({ type: "select-incident", incidentId: item.incidentId }); dispatch({ type: "navigate", screen: "operations" }); }}>Operations Incident 열기</button>}</div></article>;
    })}</div>
    <p className="operations-visible-warning">알림 읽음은 ViewState Prototype 전이입니다. 실제 서버 Notification Write 0건입니다.</p>
  </StatusCard></main>;
}

export function OperationsRecoveryWorkspace({ initialScreen = "operations", clientType = "web", recoveryAdapter = null }) {
  const [state, setState] = useState(() => createOperationsRecoveryViewState({ screen: initialScreen, clientType }));
  const [width, setWidth] = useState(1920);
  const dispatch = (action) => setState((current) => transitionOperationsRecovery(current, action));
  useEffect(() => {
    const resize = () => setWidth(window.innerWidth);
    const pop = () => dispatch({ type: "navigate", screen: window.location.pathname === "/notifications" ? "notifications" : "operations" });
    resize(); window.addEventListener("resize", resize); window.addEventListener("popstate", pop);
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("popstate", pop); };
  }, []);
  const projection = projectOperationsRecovery(state, width);
  if (projection.availability === "unavailable") return <main className="operations-shell"><h1>운영 상태</h1><p className="operations-visible-warning">unavailable · {projection.continueOn}</p></main>;
  return <div className={`operations-shell operations-${projection.layoutMode}`} data-route-id={projection.route.routeId} data-screen-id={projection.route.screenId}>
    <Header state={state} projection={projection} dispatch={dispatch} />
    <div className="operations-live" role="status" aria-live="polite">{state.safety ? <><strong>{state.safety.code}</strong> · {state.safety.message} · Trace {state.safety.traceId}</> : <>ready · Prototype Adapter 실제 외부 효과 {Object.values(state.actualEffects).reduce((sum, value) => sum + value, 0)}건</>}</div>
    {state.screen === "notifications" ? <NotificationsView state={state} dispatch={dispatch} /> : <OperationsView state={state} dispatch={dispatch} recoveryAdapter={recoveryAdapter} />}
  </div>;
}
