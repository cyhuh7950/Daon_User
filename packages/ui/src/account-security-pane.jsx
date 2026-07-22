"use client";

import { useEffect, useId, useMemo, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import "./workspace.css";
import {
  DETAILED_PERMISSIONS,
  MEMBERSHIP_ROLES,
  REALM_MOVE_STEPS,
  SENSITIVE_ACTIONS,
  createAccountSecurityViewState,
  projectAccountSecurity,
  projectAccountSecurityRoute,
  transitionAccountSecurityState
} from "./account-security-model.js";

const ROLE_LABELS = {
  personal_owner: "개인 소유자", organization_admin: "조직 관리자", workspace_admin: "Workspace 관리자",
  editor: "편집자", reviewer: "검토자", approver: "승인자", viewer: "조회자"
};
const PERMISSION_LABELS = {
  external_llm: "외부 LLM 전송", internet_search: "인터넷 검색", local_internal_llm: "로컬·사내 LLM",
  daon_approved_knowledge: "Daon 승인 지식", file_download_share: "파일 다운로드·공유",
  knowledge_registration: "생산 지식 등록", data_realm_move: "영역 이동", final_approval_delivery: "최종 승인·외부 전달"
};
const SENSITIVE_LABELS = {
  approve_external_egress: "외부 전송 승인", move_data_realm: "Local-private→Cloud-sync 영역 이동",
  external_share_download: "조직 외부 공유·보호 파일 다운로드", final_approval_registration: "최종 승인·생산 지식 등록",
  change_organization_policy: "조직 정책·Credential 변경", revoke_device: "장치·Session·Sync Key 철회", purge_restore: "영구 삭제·Restore·Rollback"
};
const REALM_LABELS = {
  target_scope: "1 대상·범위", authorization_sensitive_check: "2 권한·민감정보",
  explicit_approval: "3 명시 승인", transfer_preview: "4 전송 Preview", version_audit: "5 버전·Audit"
};

function Info({ label }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  return <span className="security-info"><button type="button" aria-label={`${label} 설명`} aria-expanded={open} aria-controls={tooltipId} aria-describedby={open ? tooltipId : undefined} onClick={() => setOpen((value) => !value)} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setOpen(false); } }}>i</button>{open && <span id={tooltipId} role="tooltip">{label}. Prototype Fixture의 판정 기준이며 실제 Backend 처리는 후속 단계입니다.</span>}</span>;
}

function Badge({ children, tone = "neutral" }) {
  return <span className={`security-badge security-${tone}`}>{children}</span>;
}

function SecurityHeader({ state, projection, routeMetadata, onNavigate }) {
  const go = (screen) => {
    const nextRoute = projectAccountSecurityRoute(screen);
    window.history.pushState({ screen }, "", nextRoute.path);
    onNavigate(screen);
  };
  return <>
    <header className="security-header">
      <div><p className="eyebrow">계정·조직·보안</p><h1>{routeMetadata.title}</h1></div>
      <div className="header-status"><Badge tone="prototype">Production-bound Prototype</Badge><Badge>{projection.layoutMode}</Badge><Badge tone="warning">실제 API 미실행</Badge></div>
    </header>
    <nav className="security-route-tabs" aria-label="계정과 조직 설정">
      <button type="button" aria-current={state.screen === "account" ? "page" : undefined} onClick={() => go("account")}>계정 설정</button>
      <button type="button" aria-current={state.screen === "organization" ? "page" : undefined} onClick={() => go("organization")}>조직 설정</button>
      <a href="/workspaces/workspace-release-one">Workspace로 돌아가기</a>
    </nav>
  </>;
}

function StepUpPanel({ state, dispatch }) {
  const current = state.stepUpAuthorizations.at(-1);
  return <section className="security-card" aria-labelledby="step-up-title">
    <div className="security-title"><h2 id="step-up-title">StepUpAuthorization</h2><Info label="민감 작업 단기 추가 인증" /></div>
    <p>Actor + Action + Target + Policy Version 결합 · 장기 ApprovalRequest와 분리</p>
    <div className="security-actions">
      <button type="button" onClick={() => dispatch({ type: "perform-sensitive-action", action: "revoke_device", target: "device-windows-001" })}>추가 인증 없이 철회 시도</button>
      <button type="button" onClick={() => dispatch({ type: "issue-step-up", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion })}>장치 철회용 발급 Preview</button>
      <button type="button" onClick={() => dispatch({ type: "perform-sensitive-action", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion })}>장치 철회 1회 사용</button>
      <button type="button" onClick={() => dispatch({ type: "perform-sensitive-action", actor: state.actorId, action: "revoke_device", target: "device-other", policyVersion: state.policyVersion })}>다른 Target 사용 시도</button>
      <button type="button" onClick={() => dispatch({ type: "issue-step-up", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion, expiresAt: "2026-07-22T10:00:00+09:00" })}>만료 Authorization 발급</button>
      <button type="button" onClick={() => dispatch({ type: "perform-sensitive-action", actor: state.actorId, action: "revoke_device", target: "device-windows-001", policyVersion: state.policyVersion, now: "2026-07-22T10:01:00+09:00" })}>만료 사용 시도</button>
    </div>
    {current ? <dl className="security-dl"><div><dt>ID</dt><dd>{current.id}</dd></div><div><dt>상태</dt><dd>{current.status}</dd></div><div><dt>Action·Target</dt><dd>{current.action} · {current.target}</dd></div><div><dt>Policy</dt><dd>{current.policyVersion}</dd></div></dl> : <p className="secondary">발급된 단기 Authorization 없음</p>}
  </section>;
}

function AccountView({ state, dispatch }) {
  return <div className="security-grid">
    <section className="security-card">
      <div className="security-title"><h2>현재 계정·Membership</h2><Info label="NavigationPersona와 MembershipRole 분리" /></div>
      <dl className="security-dl"><div><dt>Actor</dt><dd>{state.actorId}</dd></div><div><dt>NavigationPersona</dt><dd>{state.navigationPersona}</dd></div><div><dt>MembershipRole</dt><dd>{state.membership.role}</dd></div><div><dt>Tenant·Workspace</dt><dd>{state.tenantId} · {state.workspaceId}</dd></div></dl>
    </section>
    <section className="security-card security-span-2">
      <div className="security-title"><h2>등록 장치·신뢰 상태</h2><Info label="Session·Sync Key 안전 Metadata" /></div>
      <div className="device-grid">{state.devices.map((device) => <article key={device.id} className="device-card"><div><strong>{device.type}</strong><Badge tone={device.status === "trusted" ? "success" : device.status === "revoked" ? "danger" : "warning"}>{device.status}</Badge></div><p>{device.id}</p><small>{device.trustReason} · Session {device.sessionCount} · Sync Key {device.syncKeyState}</small></article>)}</div>
      {state.deviceRevocationPreview && <p className="visible-warning">철회 Preview: {state.deviceRevocationPreview.deviceId} · 실제 Session/Sync Key 조작 {state.deviceRevocationPreview.actualRevocations}건 · 재등록은 새 Device Registration Preview</p>}
    </section>
    <StepUpPanel state={state} dispatch={dispatch} />
  </div>;
}

function RoleMatrix({ state, dispatch }) {
  return <section className="security-card security-span-3">
    <div className="security-title"><h2>MembershipRole 7 × 세부 권한 8 Matrix</h2><Info label="UI 숨김이 아닌 순수 Authorization 판정" /></div>
    <label>검증 역할<select value={state.selectedRole} onChange={(event) => dispatch({ type: "select-role", role: event.target.value })}>{MEMBERSHIP_ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}</select></label>
    <div className="security-table-wrap"><table className="security-matrix"><thead><tr><th>MembershipRole</th>{DETAILED_PERMISSIONS.map((permission) => <th key={permission}>{PERMISSION_LABELS[permission]}</th>)}</tr></thead><tbody>{MEMBERSHIP_ROLES.map((role) => <tr key={role} data-selected={role === state.selectedRole}><th>{ROLE_LABELS[role]}</th>{DETAILED_PERMISSIONS.map((permission) => <td key={permission}>{state.membershipGrants[role][permission] ? "허용" : "거부"}</td>)}</tr>)}</tbody></table></div>
    <div className="security-actions"><button type="button" onClick={() => dispatch({ type: "revoke-permission", role: state.selectedRole, permission: "internet_search" })}>선택 역할 인터넷 검색만 회수</button><button type="button" onClick={() => dispatch({ type: "attempt-role-escalation", requestedRole: "organization_admin" })}>역할 상승 시도</button><button type="button" onClick={() => dispatch({ type: "attempt-tenant-access", tenantId: "tenant-other" })}>다른 Tenant 직접 접근</button></div>
  </section>;
}

function PolicyPanel({ state, dispatch }) {
  const selected = state.organizationPolicy[state.selectedPolicy];
  return <section className="security-card security-span-2">
    <div className="security-title"><h2>조직 정책·잠금</h2><Info label="요청값·유효값·잠금 사유·Policy Version" /></div>
    <label>정책 항목<select value={state.selectedPolicy} onChange={(event) => dispatch({ type: "select-policy", policy: event.target.value })}>{Object.keys(state.organizationPolicy).map((key) => <option key={key}>{key}</option>)}</select></label>
    <dl className="security-dl"><div><dt>요청값</dt><dd>{selected.requestedValue}</dd></div><div><dt>유효값</dt><dd>{selected.effectiveValue}</dd></div><div><dt>잠금 이유</dt><dd>{selected.lockReason}</dd></div><div><dt>Policy Version</dt><dd>{state.policyVersion}</dd></div></dl>
    <div className="security-actions"><button type="button" onClick={() => dispatch({ type: "request-policy-relaxation", field: state.selectedPolicy, requestedValue: "unrestricted" })}>완화 저장 시도</button><button type="button" onClick={() => dispatch({ type: "preview-policy-change", role: state.selectedRole, field: state.selectedPolicy, value: "requested-change" })}>선택 역할 정책 변경 Preview</button></div>
    {state.policyPreview && <p className="visible-warning">요청 {state.policyPreview.requestedValue} → 유효 {state.policyPreview.effectiveValue} · {state.policyPreview.lockReason ?? "권한 판정"} · 실제 Write {state.policyPreview.actualApiWrites}건</p>}
    {state.safety?.code === "AUTHORIZATION_DENIED" && <p className="visible-warning"><strong>{state.safety.code}</strong> · HTTP 403 계약 Preview · 실제 API 미실행 · 요청값 {selected.requestedValue} · 유효값 {selected.effectiveValue} · Policy Version {state.policyVersion} · 실제 Write 0건</p>}
  </section>;
}

function AccessPanel({ state, dispatch }) {
  const latest = state.accessDecisions.at(-1);
  const partial = state.accessDecisions.findLast((decision) => decision.state === "partially_redacted");
  const blocked = state.accessDecisions.findLast((decision) => decision.state === "access_blocked");
  return <section className="security-card security-span-2">
    <div className="security-title"><h2>과거 결과 현재 권한 재검증</h2><Info label="OutputVersion 불변·요청별 AccessDecision" /></div>
    <p>승인 Badge 대상 <strong>{state.historicalOutputVersion.id}</strong> · ApprovalRequest {state.historicalOutputVersion.approvalRequestId}</p>
    <div className="security-actions">{["available", "partially_redacted", "access_blocked"].map((accessState) => <button key={accessState} type="button" aria-pressed={state.accessState === accessState} onClick={() => dispatch({ type: "set-current-access", accessState })}>{accessState}</button>)}</div>
    <div className="security-actions">{["read", "citation", "export", "delivery", "knowledge_registration", "rerun"].map((operation) => <button key={operation} type="button" onClick={() => dispatch({ type: "evaluate-past-access", operation })}>{operation} 재판정</button>)}</div>
    {latest && <dl className="security-dl"><div><dt>AccessDecision</dt><dd>{latest.id} · {latest.state}</dd></div><div><dt>Code</dt><dd>{latest.code}</dd></div><div><dt>Masking</dt><dd>{latest.maskedReferences.join(", ") || "0건"}</dd></div><div><dt>판정 근거</dt><dd>{latest.reason}</dd></div></dl>}
    <p className="visible-warning">AccessDecision {state.accessDecisions.length}건 · 여섯 작업 판정 {state.accessDecisions.length >= 6 ? "6건 완료" : `${state.accessDecisions.length}/6`} · partially_redacted Masking {partial?.maskedReferences.join(", ") || "0건"} · access_blocked {blocked?.code || (state.accessState === "access_blocked" ? "CURRENT_ACCESS_DENIED" : "미판정")}</p>
    {state.rerunPreviews.at(-1) && <p className="visible-success">새 Run Preview {state.rerunPreviews.at(-1).id} · previous {state.rerunPreviews.at(-1).previousRunId} · 현재 정책 {state.rerunPreviews.at(-1).policyVersion} · 실제 Run 0건</p>}
    <p className="secondary">이미 외부 Export된 사본은 회수 성공을 주장하지 않으며 시점·대상·후속 권한 변경을 Audit Preview로 경고합니다.</p>
  </section>;
}

function RealmMove({ state, dispatch }) {
  const authorization = state.stepUpAuthorizations.find((item) => item.id === state.realmMove.stepUpAuthorizationId);
  return <section className="security-card">
    <div className="security-title"><h2>Local-private → Cloud-sync</h2><Info label="건너뛸 수 없는 5단계 영역 이동" /></div>
    <ol className="realm-steps">{REALM_MOVE_STEPS.map((step) => <li key={step} data-complete={state.realmMove.completedSteps.includes(step)}>{REALM_LABELS[step]}</li>)}</ol>
    <div className="security-actions"><button type="button" onClick={() => dispatch({ type: "issue-step-up", actor: state.actorId, action: "move_data_realm", target: state.realmMove.sourceId, policyVersion: state.policyVersion })}>영역 이동 Step-up</button>{REALM_MOVE_STEPS.map((step) => <button key={step} type="button" onClick={() => dispatch({ type: "advance-realm-move", step })}>{REALM_LABELS[step]}</button>)}</div>
    <p className="visible-success">영역 이동 {state.realmMove.completedSteps.length}/5 완료 · Step-up {authorization ? `${authorization.id}/${authorization.status}` : "미사용"} · Approval Preview {state.realmMove.approvalPreview ? "완료" : "대기"}</p>
    <p>실제 전송 {state.actualTransfers}건 · 대상 SourceVersion {state.actualSourceVersionsCreated}건 · 재색인 {state.actualReindexes}건</p>
  </section>;
}

function OrganizationView({ state, dispatch }) {
  return <div className="security-grid">
    <RoleMatrix state={state} dispatch={dispatch} />
    <PolicyPanel state={state} dispatch={dispatch} />
    <section className="security-card"><div className="security-title"><h2>Provider 안전 표시</h2><Info label="불투명 Profile·Deployment와 정책만 표시" /></div>{state.providerProfiles.map((profile) => <dl className="security-dl" key={profile.profileId}><div><dt>Profile</dt><dd>{profile.profileId}</dd></div><div><dt>Deployment</dt><dd>{profile.deploymentId}</dd></div><div><dt>영역</dt><dd>{profile.dataRealm}</dd></div><div><dt>정책</dt><dd>{profile.policyState}</dd></div></dl>)}</section>
    <section className="security-card"><div className="security-title"><h2>민감 작업 최소 7종</h2><Info label="조직이 제거할 수 없는 Step-up 정본" /></div><ol className="sensitive-list">{SENSITIVE_ACTIONS.map((action) => <li key={action}><strong>{SENSITIVE_LABELS[action]}</strong><small>{action}</small></li>)}</ol></section>
    <StepUpPanel state={state} dispatch={dispatch} />
    <AccessPanel state={state} dispatch={dispatch} />
    <RealmMove state={state} dispatch={dispatch} />
  </div>;
}

export function AccountSecurityWorkspace({ initialScreen = "account" }) {
  const [state, setState] = useState(() => createAccountSecurityViewState({ screen: initialScreen }));
  const [width, setWidth] = useState(1920);
  useEffect(() => {
    const resize = () => setWidth(window.innerWidth);
    const pop = () => setState((current) => transitionAccountSecurityState(current, { type: "navigate", screen: window.location.pathname.endsWith("organization") ? "organization" : "account" }));
    resize(); window.addEventListener("resize", resize); window.addEventListener("popstate", pop);
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("popstate", pop); };
  }, []);
  const projection = useMemo(() => projectAccountSecurity(state, width), [state, width]);
  const routeMetadata = useMemo(() => projectAccountSecurityRoute(state.screen), [state.screen]);
  const dispatch = (action) => setState((current) => transitionAccountSecurityState(current, action));
  return <main className={`account-security security-${projection.layoutMode}`} data-route-id={routeMetadata.routeId} data-screen-id={routeMetadata.screenId} data-client-type={state.clientType} data-selected-role={state.selectedRole} data-policy-version={state.policyVersion} data-output-version-id={state.selectedOutputVersionId}>
    <SecurityHeader state={state} projection={projection} routeMetadata={routeMetadata} onNavigate={(screen) => dispatch({ type: "navigate", screen })} />
    {state.screen === "account" ? <AccountView state={state} dispatch={dispatch} /> : <OrganizationView state={state} dispatch={dispatch} />}
    {state.safety && <section className="safe-error" role="alert"><strong>{state.safety.code}</strong><p>{state.safety.message}</p><p>{state.safety.userAction} · Trace {state.safety.traceId}</p></section>}
    <section className="security-audit"><div className="security-title"><h2>Append-only Audit Preview</h2><button type="button" onClick={() => dispatch({ type: "mutate-audit", operation: "delete", eventId: state.auditEvents[0]?.id })}>기존 Event 삭제 시도</button></div><p>Event {state.auditEvents.length}건 · Actor·Action·Target·Policy Version·Trace ID·판정·안전 Code</p>{state.auditEvents.slice(-4).map((event) => <p key={event.id}><strong>{event.action}</strong> · {event.target} · {event.decision}/{event.code} · {event.traceId}</p>)}</section>
    <p className="prototype-unavailable">prototype_fixture · 실제 OIDC/PKCE·Cookie·CSRF·RLS·DB·API 401/403·Session/Key 철회·MFA·Egress·Connector 0건 · deferred_actual M3/M4/M5/M6/M8 · HTTP 403 계약 Preview · 실제 API 미실행</p>
  </main>;
}
