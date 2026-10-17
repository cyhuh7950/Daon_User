"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as organizationApi from "../lib/organization-admin-api.js";
import { secureUuid } from "../../../packages/ui/src/secure-uuid.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";
import {
  changeMemberRole, changeMemberState, createInvitation, decideCreationRequest, decideJoinRequest,
  getOrganizationSession, listCreationRequests, listDirectory, listJoinRequests, listMembers, revokeInvitation,
} from "../lib/organization-admin-api.js";

const styles = { display: "grid", gap: 10 };
function failure(error) { return error?.status === 401 ? "로그인이 필요합니다." : error?.status === 403 ? "이 콘솔에 접근할 권한이 없습니다." : `요청을 처리하지 못했습니다. (${error?.message || "UNKNOWN"})`; }
function State({ children }) { return <p className="org-console-state" role="status">{children}</p>; }
function Action({ children, ...props }) { return <button type="button" className="org-console-button" {...props}>{children}</button>; }
function DecisionButtons({ onDecision, disabled }) { return <span className="org-console-actions"><Action disabled={disabled} onClick={() => onDecision(true)}>승인</Action><Action disabled={disabled} onClick={() => onDecision(false)}>거부</Action></span>; }

function SystemConsole({ creationRequests, directory, reload }) {
  const [busy, setBusy] = useState("");
  const decide = async (item, approved) => { setBusy(item.request_id); try { await decideCreationRequest(item.request_id, { approved, expected_version: item.version }); await reload(); } catch (error) { window.alert(failure(error)); } finally { setBusy(""); } };
  return <section className="org-console-card" aria-labelledby="creation-title"><h2 id="creation-title">조직 생성 신청</h2>{creationRequests.length === 0 ? <State>처리할 조직 생성 신청이 없습니다.</State> : <div style={styles}>{creationRequests.map((item) => <article className="org-console-row" key={item.request_id}><div><strong>{item.requested_org_name}</strong><small>{item.requested_org_identifier} · 신청자 {item.applicant_user_id} · {item.state}</small></div>{item.state === "pending" && <DecisionButtons disabled={busy === item.request_id} onDecision={(approved) => decide(item, approved)} />}</article>)}</div>}<div className="org-console-subcard"><strong>전체 조직·사용자</strong><small>조직 {directory.organizations.length}개 · 사용자 {directory.users.length}명</small>{directory.organizations.map((org) => <article className="org-console-row" key={`${org.tenant_id}-${org.state}`}><div><strong>{org.name}</strong><small>{org.tenant_id} · {org.state} · 신청자 {org.applicant_user_id}</small></div></article>)}{directory.users.map((user) => <article className="org-console-row" key={user.user_id}><div><strong>{user.login_id || user.user_id}</strong><small>{user.email || "이메일 없음"} · {user.state}</small></div></article>)}</div></section>;
}

function LegacySystemAdminConsole({ mode = "system" }) {
  const [session, setSession] = useState(null); const [tenantId, setTenantId] = useState(""); const [data, setData] = useState({ creation: [], directory: { organizations: [], users: [] }, joins: [], members: [] }); const [status, setStatus] = useState("loading"); const [error, setError] = useState(null);
  const load = useCallback(async (tenant = tenantId) => { setStatus("loading"); setError(null); try { const result = mode === "system" ? { creation: await listCreationRequests(), directory: await listDirectory() } : { joins: await listJoinRequests(tenant), members: await listMembers(tenant) }; setData((previous) => ({ ...previous, ...result })); setStatus("ready"); } catch (reason) { setError(reason); setStatus("error"); } }, [mode, tenantId]);
  useEffect(() => { let mounted = true; getOrganizationSession().then((value) => { if (!mounted) return; setSession(value); const queryTenant = new URLSearchParams(window.location.search).get("tenant_id"); const selected = queryTenant || value?.tenant_id || ""; setTenantId(selected); if (mode === "system") return load(selected, value); if (selected) return load(selected, value); setStatus("empty"); }).catch((reason) => { if (mounted) { setError(reason); setStatus("error"); } }); return () => { mounted = false; }; }, [mode, load]);
  if (status === "loading") return <main className="org-console-page"><State>관리 콘솔을 불러오는 중입니다.</State></main>;
  if (status === "error") return <main className="org-console-page"><State>{failure(error)}</State><Action onClick={() => load()}>다시 시도</Action></main>;
  if (status === "empty") return <main className="org-console-page"><State>조직 컨텍스트가 없어 관리 콘솔을 열 수 없습니다.</State></main>;
  return <main className="org-console-page"><header className="org-console-header"><div><p>DAON ADMIN</p><h1>전체 관리자</h1><small>조직 생성 신청과 전체 조회</small></div><nav><a href="/notebooks">Notebook</a></nav></header><SystemConsole creationRequests={data.creation} directory={data.directory} reload={() => load()} /></main>;
}

const emptyScope = () => ({ workspaces: [], joins: [], members: [], invitations: [] });
const sameSession = (a, b) => Boolean(a && b && ["user_id", "tenant_id", "session_id", "device_id", "workspace_id"].every(key => a[key] === b[key]));
const roleNames = { viewer: "조회자", editor: "편집자", reviewer: "검토자", approver: "승인자" };
const safeFailure = error => error?.status === 401 ? "로그인이 필요합니다." : error?.status === 403 ? "이 조직을 관리할 권한이 없습니다." : error?.status === 404 ? "관리 조직을 찾을 수 없습니다." : "요청을 처리하지 못했습니다. 다시 조회하세요.";

function OrganizationManagementConsole({ api: suppliedApi }) {
  const api = useRef(suppliedApi).current;
  const root = useRef(null), codeInput = useRef(null), epoch = useRef(0), controller = useRef(null);
  const currentSession = useRef(null), currentTenant = useRef(""), operation = useRef(null), booting = useRef(null), mounted = useRef(false);
  const [session, setSession] = useState(null), [tenants, setTenants] = useState([]), [tenantId, setTenantId] = useState("");
  const [data, setData] = useState(emptyScope), [status, setStatus] = useState("loading"), [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false), [unavailable, setUnavailable] = useState(false);
  const [workspaceId, setWorkspaceId] = useState(""), [code, setCode] = useState(""), [days, setDays] = useState(1), [uses, setUses] = useState(1);
  const [roles, setRoles] = useState({}), [reason, setReason] = useState("");
  const clearInputs = () => { if (codeInput.current) codeInput.current.value = ""; setCode(""); setRoles({}); setReason(""); setWorkspaceId(""); setDays(1); setUses(1); };
  const invalidate = () => {
    booting.current = null;
    epoch.current++; controller.current?.abort(); concealProtectedRoute(root.current); setData(emptyScope()); clearInputs();
    const pending = operation.current;
    if (pending && (pending.phase === "sent" || pending.unconfirmed)) { pending.unconfirmed = true; pending.status = "uncertain"; pending.controller?.abort(); }
    else if (pending?.phase === "checking") { operation.current = null; pending.controller?.abort(); }
  };
  const failRead = (error, generation) => {
    if (!mounted.current || generation !== epoch.current) return;
    invalidate();
    const denied = error?.status === 401 || error?.status === 403;
    if (denied) { setSession(null); setTenants([]); }
    setMessage(safeFailure(error)); setStatus(denied ? "denied" : "error");
  };
  const loadScope = async (tenant, selectedSession) => {
    invalidate(); currentTenant.current = tenant; setTenantId(tenant); setMessage(""); setStatus(tenant ? "loading" : "unselected");
    if (!tenant) return false;
    const generation = epoch.current;
    const abort = new AbortController(); controller.current = abort;
    try {
      const options = { signal: abort.signal };
      const [workspaces, joins, members, invitations] = await Promise.all([
        api.listManagedWorkspaces(tenant, options), api.listJoinRequests(tenant, options), api.listMembers(tenant, options), api.listInvitations(tenant, options),
      ]);
      if (!mounted.current || generation !== epoch.current || !sameSession(selectedSession, currentSession.current)) return false;
      if ([joins, members, invitations].some(values => values.some(value => value.tenant_id !== tenant))) throw new Error("ORGANIZATION_RESPONSE_INVALID");
      setData({ workspaces, joins, members, invitations }); setStatus("ready"); return true;
    } catch (error) { failRead(error, generation); return false; }
  };
  const boot = async () => {
    if (booting.current) return;
    invalidate(); const attempt = {}; booting.current = attempt; setSession(null); setTenants([]); setStatus("loading");
    const generation = epoch.current, abort = new AbortController(); controller.current = abort;
    try {
      const fresh = await api.getOrganizationSession({ signal: abort.signal });
      if (!mounted.current || generation !== epoch.current) return;
      if (!fresh?.session_id || !fresh?.user_id) { const error = new Error(); error.status = 401; throw error; }
      const unchanged = sameSession(fresh, currentSession.current);
      if (!unchanged) { if (operation.current) operation.current.args = null; operation.current = null; currentTenant.current = ""; setTenants([]); setBusy(false); setUnavailable(false); }
      currentSession.current = fresh; setSession(fresh);
      if (fresh.is_system_admin) {
        const values = await api.listManagedOrganizations({ signal: abort.signal });
        if (!mounted.current || generation !== epoch.current) return;
        setTenants(values);
        const tenant = unchanged && values.some(value => value.tenant_id === currentTenant.current) ? currentTenant.current : "";
        await loadScope(tenant, fresh);
      } else await loadScope(fresh.tenant_id, fresh);
    } catch (error) { failRead(error, generation); }
    finally { if (booting.current === attempt) booting.current = null; }
  };
  useEffect(() => {
    mounted.current = true; void boot();
    const hide = () => { invalidate(); setStatus("hidden"); };
    const visibility = () => { if (document.visibilityState === "hidden") hide(); else void boot(); };
    const resume = () => { void boot(); };
    window.addEventListener("pagehide", hide); window.addEventListener("pageshow", resume); window.addEventListener("focus", resume); window.addEventListener("popstate", resume); document.addEventListener("visibilitychange", visibility);
    return () => { mounted.current = false; epoch.current++; controller.current?.abort(); operation.current?.controller?.abort(); window.removeEventListener("pagehide", hide); window.removeEventListener("pageshow", resume); window.removeEventListener("focus", resume); window.removeEventListener("popstate", resume); document.removeEventListener("visibilitychange", visibility); };
  }, []);
  useEffect(() => { if (status !== "hidden") revealProtectedRoute(root.current); }, [status]);

  const send = async request => {
    if (operation.current !== request || request.status === "sending") return;
    request.status = "sending"; request.phase = "checking"; request.controller = new AbortController(); setBusy(true); setMessage("");
    const generation = epoch.current;
    try {
      const fresh = await api.getOrganizationSession({ signal: request.controller.signal });
      if (operation.current !== request) return;
      if (!fresh?.session_id || !fresh?.user_id) { const error = new Error("ORGANIZATION_SESSION_INVALID"); error.status = 401; throw error; }
      if (!sameSession(fresh, request.session)) { operation.current = null; request.args = null; await boot(); return; }
      if (!mounted.current || generation !== epoch.current || request.tenant !== currentTenant.current || operation.current !== request) return;
      request.phase = "sent";
      await api[request.name](...request.args, { ...request.options, idempotencyKey: request.key, expectedSessionId: request.session.session_id, signal: request.controller.signal });
      if (operation.current !== request || !sameSession(request.session, currentSession.current)) return;
      operation.current = null; request.args = null; request.phase = "done";
      if (mounted.current && generation === epoch.current && currentTenant.current === request.tenant) {
        const reloaded = await loadScope(request.tenant, request.session);
        if (mounted.current && currentTenant.current === request.tenant && sameSession(request.session, currentSession.current)) {
          setMessage(previous => reloaded ? "저장했습니다. 현재 조직을 다시 조회했습니다." : `저장은 확인했습니다. ${previous || "현재 조직 재조회에 실패했습니다. 다시 조회하세요."}`);
        }
      }
    } catch (error) {
      if (operation.current !== request || !sameSession(request.session, currentSession.current)) return;
      // 재확인의 현재 단계/거부 응답은 이전 전송의 미확정을 해소하지 않는다.
      if (request.unconfirmed) { request.status = "uncertain"; }
      else if (request.phase !== "sent") { operation.current = null; }
      else if (error?.status === 503 && error?.message === "ORGANIZATION_WORKFLOW_UNAVAILABLE") { operation.current = null; setUnavailable(true); setMessage("조직 관리 쓰기 기능이 준비되지 않았습니다."); }
      else if (!error?.status || error.status >= 500 || error.status === 408) { request.unconfirmed = true; request.status = "uncertain"; }
      else { operation.current = null; request.args = null; }
      if (!mounted.current || generation !== epoch.current) return;
      if (error?.status === 401 || error?.status === 403) { failRead(error, generation); return; }
      if (operation.current?.status === "uncertain") setMessage("결과 미확정 — 새 요청을 제출하지 말고 동일 요청 결과를 확인하세요.");
      else if (!(error?.status === 503 && error?.message === "ORGANIZATION_WORKFLOW_UNAVAILABLE")) setMessage(safeFailure(error));
    } finally { if (mounted.current && sameSession(request.session, currentSession.current)) setBusy(false); }
  };
  const mutate = (name, args, options = {}) => {
    if (operation.current || unavailable || status !== "ready") return;
    try {
      const request = { name, args: Object.freeze(args.map(value => value && typeof value === "object" ? Object.freeze({ ...value }) : value)), options: Object.freeze({ ...options }),
        tenant: currentTenant.current, session: Object.freeze({ ...currentSession.current }), key: `org-${secureUuid()}`, status: "new", phase: "checking" };
      operation.current = request; void send(request);
    } catch { setMessage("안전한 요청 키를 만들 수 없습니다. 이 환경에서는 저장하지 않습니다."); }
  };
  const pending = operation.current;
  const locked = busy || Boolean(pending) || unavailable;
  const create = event => {
    event.preventDefault();
    if (!workspaceId || !code.trim() || !Number.isInteger(days) || days < 1 || days > 7 || !Number.isInteger(uses) || uses < 1 || uses > 10) return;
    const body = { workspace_id: workspaceId, code: code.trim(), expires_at: new Date(Date.now()+days*86400000).toISOString(), max_uses: uses };
    mutate("createInvitation", [tenantId, body]); if (codeInput.current) codeInput.current.value = ""; setCode("");
  };
  return <main className="org-console-page" ref={root}>
    <header className="org-console-header"><div><p>DAON ADMIN</p><h1>조직 관리자</h1><small>선택한 조직의 가입·사용자·초대 관리</small></div><nav><a href="/notebooks">Notebook</a><a href="/admin">전체 관리자</a></nav></header>
    {session?.is_system_admin && <section className="org-console-card"><label className="org-console-select">관리 조직<select aria-label="관리 조직" value={tenantId} onChange={event => { void loadScope(event.target.value, currentSession.current); }}><option value="">관리할 조직을 선택하세요</option>{tenants.map(value => <option key={value.tenant_id} value={value.tenant_id}>{value.display_name} · {value.tenant_id}</option>)}</select></label></section>}
    {message && <State>{message}</State>}
    {status === "ready" && pending?.status === "uncertain" && <section className="org-console-card"><State>결과 미확정 — 같은 세션의 원래 조직에서만 결과를 확인할 수 있습니다.</State><Action disabled={busy || pending.tenant !== tenantId} onClick={() => { void send(pending); }}>동일 요청 결과 확인</Action></section>}
    {status === "loading" && <State>관리 콘솔을 불러오는 중입니다.</State>}
    {status === "unselected" && <State>관리할 조직을 선택하세요</State>}
    {(status === "error" || status === "denied") && <Action onClick={() => { void boot(); }}>다시 조회</Action>}
    {status === "ready" && <div style={styles}>
      <section className="org-console-card"><label className="org-console-select">대상 Workspace<select aria-label="대상 Workspace" value={workspaceId} onChange={event => setWorkspaceId(event.target.value)} disabled={locked}><option value="">Workspace를 선택하세요</option>{data.workspaces.map(value => <option key={value.workspace_id} value={value.workspace_id}>{value.display_name} · {value.workspace_id}</option>)}</select></label><Action disabled={busy} onClick={() => { void loadScope(tenantId, currentSession.current); }}>현재 조직 재조회</Action></section>
      <section className="org-console-card"><h2>가입 신청</h2>{data.joins.length === 0 ? <State>처리할 가입 신청이 없습니다.</State> : data.joins.map(item => <article className="org-console-row" key={item.request_id}><div><strong>{item.user_id}</strong><small>{item.state} · 버전 {item.version}</small></div>{item.state === "pending" && <DecisionButtons disabled={locked} onDecision={approved => mutate("decideJoinRequest", [item.request_id, { approved, role: "member", expected_version: item.version }])} />}</article>)}</section>
      <section className="org-console-card"><h2>조직 사용자</h2><label className="org-console-select">변경 사유<input aria-label="변경 사유" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} disabled={locked} /></label>{data.members.length === 0 ? <State>등록된 사용자가 없습니다.</State> : data.members.map(member => <article className="org-console-row" key={member.user_id}><div><strong>{member.user_id}</strong><small>{member.state} · 버전 {member.version}</small>{member.join_workspace_id ? <small>{member.join_workspace_id} · 실제 역할 {roleNames[member.access_role] || member.access_role || "권한 없음"} · {member.access_state || "연결된 권한 없음"} · auth 버전 {member.access_version ?? "없음"}</small> : <small>기존 기록—Workspace 미연결 · 표기 역할 {member.role} · 실제 실행 권한과 별개</small>}</div>{member.join_workspace_id && <label className="org-console-select">실제 Workspace 역할<select aria-label={`${member.user_id} 실제 역할`} value={roles[member.user_id] || member.access_role || "viewer"} disabled={locked || member.state !== "active" || member.access_state !== "active"} onChange={event => setRoles(previous => ({ ...previous, [member.user_id]: event.target.value }))}>{Object.entries(roleNames).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select><Action disabled={locked || !reason.trim() || member.state !== "active" || member.access_state !== "active"} onClick={() => mutate("changeMemberRole", [tenantId, member.user_id, { workspace_id: member.join_workspace_id, role: roles[member.user_id] || member.access_role, expected_version: member.version, expected_auth_version: member.access_version, reason: reason.trim() }])}>역할 저장</Action></label>}<Action disabled={locked} onClick={() => mutate("changeMemberState", [tenantId, member.user_id, member.state !== "active", { expected_version: member.version, reason: reason.trim() || null }])}>{member.state === "active" ? "중지" : "활성화"}</Action></article>)}</section>
      <section className="org-console-card"><h2>초대코드</h2><form className="org-console-form" onSubmit={create}><input ref={codeInput} aria-label="초대코드" value={code} onChange={event => setCode(event.target.value)} placeholder="안전한 별도 경로로 전달할 코드" required disabled={locked} /><label>유효기간(일)<input aria-label="유효기간" type="number" min={1} max={7} value={days} onChange={event => setDays(Number(event.target.value))} disabled={locked} /></label><label>최대 사용 횟수<input aria-label="최대 사용 횟수" type="number" min={1} max={10} value={uses} onChange={event => setUses(Number(event.target.value))} disabled={locked} /></label><Action type="submit" disabled={locked || !workspaceId || !code.trim()}>생성</Action><span title="코드는 제출 후 입력에서 지웁니다. 별도 안전한 경로로 직접 전달하며 메일을 보내지 않습니다." aria-label="초대코드 전달 안내">ⓘ</span></form>{data.invitations.length === 0 ? <State>초대가 없습니다.</State> : data.invitations.map(item => <article className="org-console-row" key={item.invitation_id}><div><strong>{item.invitation_id}</strong><small>{item.workspace_id || "기존 기록—Workspace 미연결"} · {item.state} · 사용 {item.used_count}/{item.max_uses} · 만료 {item.expires_at} · 버전 {item.version}</small></div><Action disabled={locked || item.state !== "active"} onClick={() => mutate("revokeInvitation", [tenantId, item.invitation_id], { expectedVersion: item.version })}>폐기</Action></article>)}</section>
    </div>}
  </main>;
}

export default function OrganizationAdminConsole({ mode = "organization", api = organizationApi }) {
  return mode === "system" ? <LegacySystemAdminConsole /> : <OrganizationManagementConsole api={api} />;
}
