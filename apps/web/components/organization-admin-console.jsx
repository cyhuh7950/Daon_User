"use client";

import { useCallback, useEffect, useState } from "react";
import {
  changeMemberRole, changeMemberState, createInvitation, decideCreationRequest, decideJoinRequest,
  getOrganizationSession, listCreationRequests, listJoinRequests, listMembers, revokeInvitation,
} from "../lib/organization-admin-api.js";

const styles = { display: "grid", gap: 10 };
function failure(error) { return error?.status === 401 ? "로그인이 필요합니다." : error?.status === 403 ? "이 콘솔에 접근할 권한이 없습니다." : `요청을 처리하지 못했습니다. (${error?.message || "UNKNOWN"})`; }
function State({ children }) { return <p className="org-console-state" role="status">{children}</p>; }
function Action({ children, ...props }) { return <button type="button" className="org-console-button" {...props}>{children}</button>; }
function DecisionButtons({ onDecision, disabled }) { return <span className="org-console-actions"><Action disabled={disabled} onClick={() => onDecision(true)}>승인</Action><Action disabled={disabled} onClick={() => onDecision(false)}>거부</Action></span>; }

function SystemConsole({ creationRequests, reload }) {
  const [busy, setBusy] = useState("");
  const decide = async (item, approved) => { setBusy(item.request_id); try { await decideCreationRequest(item.request_id, { approved, expected_version: item.version }); await reload(); } catch (error) { window.alert(failure(error)); } finally { setBusy(""); } };
  return <section className="org-console-card" aria-labelledby="creation-title"><h2 id="creation-title">조직 생성 신청</h2>{creationRequests.length === 0 ? <State>처리할 조직 생성 신청이 없습니다.</State> : <div style={styles}>{creationRequests.map((item) => <article className="org-console-row" key={item.request_id}><div><strong>{item.requested_org_name}</strong><small>{item.requested_org_identifier} · 신청자 {item.applicant_user_id} · {item.state}</small></div>{item.state === "pending" && <DecisionButtons disabled={busy === item.request_id} onDecision={(approved) => decide(item, approved)} />}</article>)}</div>}<div className="org-console-subcard"><strong>조직·사용자 조회</strong><State>조회 API 계약이 연결되면 이 영역에서 전체 조직과 사용자를 확인할 수 있습니다.</State></div></section>;
}

function OrganizationConsole({ tenantId, joinRequests, members, reload }) {
  const [busy, setBusy] = useState(""); const [code, setCode] = useState(""); const [invite, setInvite] = useState(null);
  const decide = async (item, approved) => { setBusy(item.request_id); try { await decideJoinRequest(item.request_id, { approved, expected_version: item.version }); await reload(); } catch (error) { window.alert(failure(error)); } finally { setBusy(""); } };
  const toggle = async (member, active) => { setBusy(member.user_id); try { await changeMemberState(tenantId, member.user_id, active, { expected_version: member.version }); await reload(); } catch (error) { window.alert(failure(error)); } finally { setBusy(""); } };
  const role = async (member, value) => { setBusy(member.user_id); try { await changeMemberRole(tenantId, member.user_id, { role: value, expected_version: member.version }); await reload(); } catch (error) { window.alert(failure(error)); } finally { setBusy(""); } };
  const makeInvite = async (event) => { event.preventDefault(); setBusy("invite"); try { const result = await createInvitation(tenantId, { code, expires_at: new Date(Date.now() + 7 * 86400000).toISOString(), max_uses: 10 }); setInvite(result); setCode(""); } catch (error) { window.alert(failure(error)); } finally { setBusy(""); } };
  return <div style={styles}>
    <section className="org-console-card"><h2>가입 신청</h2>{joinRequests.length === 0 ? <State>처리할 가입 신청이 없습니다.</State> : <div style={styles}>{joinRequests.map((item) => <article className="org-console-row" key={item.request_id}><div><strong>{item.user_id}</strong><small>{item.invitation_id ? "초대코드 사용" : "조직 선택"} · {item.state}</small></div>{item.state === "pending" && <DecisionButtons disabled={busy === item.request_id} onDecision={(approved) => decide(item, approved)} />}</article>)}</div>}</section>
    <section className="org-console-card"><h2>조직 사용자</h2>{members.length === 0 ? <State>등록된 사용자가 없습니다.</State> : <div style={styles}>{members.map((member) => <article className="org-console-row" key={member.user_id}><div><strong>{member.user_id}</strong><small>{member.state} · 버전 {member.version}</small></div><label className="org-console-select">역할<select value={member.role} onChange={(event) => role(member, event.target.value)} disabled={busy === member.user_id}><option value="member">member</option><option value="organization_admin">organization_admin</option><option value="personal_owner">personal_owner</option></select></label><Action disabled={busy === member.user_id} onClick={() => toggle(member, member.state !== "active")}>{member.state === "active" ? "중지" : "활성화"}</Action></article>)}</div>}</section>
    <section className="org-console-card"><h2>초대코드</h2><form className="org-console-form" onSubmit={makeInvite}><input aria-label="초대코드" value={code} onChange={(event) => setCode(event.target.value)} placeholder="초대코드 입력" required /><Action disabled={busy === "invite"}>생성</Action></form>{invite && <article className="org-console-row"><div><strong>{invite.invitation_id}</strong><small>상태 {invite.state} · 만료 {invite.expires_at}</small></div><Action onClick={async () => { try { await revokeInvitation(tenantId, invite.invitation_id); setInvite(null); } catch (error) { window.alert(failure(error)); } }}>폐기</Action></article>}<State>초대코드 원문은 서버에 저장하지 않으며, 생성 직후만 전달됩니다.</State></section>
  </div>;
}

export default function OrganizationAdminConsole({ mode = "organization" }) {
  const [session, setSession] = useState(null); const [tenantId, setTenantId] = useState(""); const [data, setData] = useState({ creation: [], joins: [], members: [] }); const [status, setStatus] = useState("loading"); const [error, setError] = useState(null);
  const load = useCallback(async (tenant = tenantId) => { setStatus("loading"); setError(null); try { const result = mode === "system" ? { creation: await listCreationRequests() } : { joins: await listJoinRequests(tenant), members: await listMembers(tenant) }; setData((previous) => ({ ...previous, ...result })); setStatus("ready"); } catch (reason) { setError(reason); setStatus("error"); } }, [mode, tenantId]);
  useEffect(() => { let mounted = true; getOrganizationSession().then((value) => { if (!mounted) return; setSession(value); const queryTenant = new URLSearchParams(window.location.search).get("tenant_id"); const selected = queryTenant || value?.tenant_id || ""; setTenantId(selected); if (mode === "system") return load(selected, value); if (selected) return load(selected, value); setStatus("empty"); }).catch((reason) => { if (mounted) { setError(reason); setStatus("error"); } }); return () => { mounted = false; }; }, [mode, load]);
  if (status === "loading") return <main className="org-console-page"><State>관리 콘솔을 불러오는 중입니다.</State></main>;
  if (status === "error") return <main className="org-console-page"><State>{failure(error)}</State><Action onClick={() => load()}>다시 시도</Action></main>;
  if (status === "empty") return <main className="org-console-page"><State>조직 컨텍스트가 없어 관리 콘솔을 열 수 없습니다.</State></main>;
  return <main className="org-console-page"><header className="org-console-header"><div><p>DAON ADMIN</p><h1>{mode === "system" ? "전체 관리자" : "조직 관리자"}</h1><small>{mode === "system" ? "조직 생성 신청과 전체 조회" : `조직 ${tenantId} 관리`}</small></div><nav><a href="/notebooks">Notebook</a>{mode === "system" ? <a href="/organization-admin">조직 관리자</a> : <a href="/admin">전체 관리자</a>}</nav></header>{mode === "system" ? <SystemConsole creationRequests={data.creation} reload={() => load()} /> : <OrganizationConsole tenantId={tenantId} joinRequests={data.joins} members={data.members} reload={() => load()} />}</main>;
}
