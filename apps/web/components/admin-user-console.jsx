"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";
import {
  approveAdminUser, changeAdminUserState, createAdminUser, deleteAdminUser,
  listAdminUsers, requestAdminUserPasswordReset, updateAdminUser, createAdminUserIdempotencyKey,
  listAdminMembershipTenants, getAdminEffectiveMemberships, changeAdminMembershipRole,
  ADMIN_MEMBERSHIP_REASONS,
} from "../lib/admin-users-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["ADMIN_USERS_UNAVAILABLE", "ADMIN_USERS_RESPONSE_INVALID", "FORBIDDEN"]);
const replaceLocation = (path) => window.location.replace(path);
const MUTABLE_STATES = new Set(["active", "suspended"]);
const canChangeState = (user) => !user.protected && MUTABLE_STATES.has(user.state);
const canApprove = (user) => !user.protected && user.state === "pending_approval";
const stateLabel = { active: "활성", suspended: "중지", pending_email: "이메일 인증 대기", pending_approval: "승인 대기" };
const displayNameFor = (user) => user.login_id ?? user.email ?? "이름 없음";
const MEMBERSHIP_ROLE_UNAVAILABLE = "역할 미조회";
const workspaceRoleLabels = {
  workspace_admin: "작업공간 관리자", editor: "편집자", reviewer: "검토자", approver: "승인자", viewer: "열람자",
};
const safeRoleError = (error) => new Set([
  "FORBIDDEN", "RESOURCE_UNAVAILABLE", "VERSION_CONFLICT", "IDEMPOTENCY_KEY_REUSED",
  "ADMIN_MEMBERSHIP_INPUT_INVALID", "ADMIN_MEMBERSHIP_RESPONSE_INVALID",
]).has(error?.message) ? error.message : "ADMIN_MEMBERSHIP_UNAVAILABLE";

export function AdminUserConsole({
  getSession = getCurrentNotebookSession,
  getUsers = listAdminUsers,
  setUserState = changeAdminUserState,
  registerUser = createAdminUser,
  editUser = updateAdminUser,
  approveUser = approveAdminUser,
  removeUser = deleteAdminUser,
  resetPassword = requestAdminUserPasswordReset,
  getTenants = listAdminMembershipTenants,
  getMemberships = getAdminEffectiveMemberships,
  setMembershipRole = changeAdminMembershipRole,
  navigate = replaceLocation,
}) {
  const protectedRoot = useRef(null);
  const pendingIds = useRef(new Set());
  const deleteInFlight = useRef(false);
  const roleRequestId = useRef(0);
  const roleInFlight = useRef(false);
  const [sessionValidated, setSessionValidated] = useState(false);
  const [sessionUserId, setSessionUserId] = useState(null);
  const [state, setState] = useState("loading");
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [pending, setPending] = useState(new Set());
  const [deletePending, setDeletePending] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({ login_id: "", email: "", initial_password: "", state: "active" });
  const [rolePanel, setRolePanel] = useState(null);

  const conceal = useCallback(() => {
    concealProtectedRoute(protectedRoot.current);
    setSessionValidated(false);
  }, []);
  const reveal = useCallback(() => {
    revealProtectedRoute(protectedRoot.current);
    setSessionValidated(true);
  }, []);
  const handleRoleAuthentication = (caught) => {
    if (caught?.message !== "AUTHENTICATION_REQUIRED") return false;
    roleRequestId.current += 1;
    conceal(); setUsers([]); setSelected(new Set()); setRolePanel(null); setModal(null);
    setSessionUserId(null); setNotice(null); setError(null);
    navigate("/");
    return true;
  };

  const load = useCallback(async (signal) => {
    roleRequestId.current += 1; setRolePanel(null);
    conceal(); setState("loading"); setError(null); setNotice(null); setUsers([]); setSelected(new Set());
    try {
      const session = await getSession({ signal });
      if (session.password_change_required) { navigate("/password-change"); return; }
      if (!session.is_system_admin) { navigate("/notebooks"); return; }
      setSessionUserId(session.user_id ?? null);
      const result = await getUsers({ signal });
      if (signal?.aborted) return;
      setUsers(result); setState("ready"); reveal();
    } catch (caught) {
      if (signal?.aborted) return;
      if (caught?.message === "AUTHENTICATION_REQUIRED") { navigate("/"); return; }
      setError(SAFE_ERRORS.has(caught?.message) ? caught.message : "ADMIN_USERS_UNAVAILABLE");
      setState("error"); reveal();
    }
  }, [conceal, getSession, getUsers, navigate, reveal]);

  useEffect(() => {
    const controller = new AbortController(); void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    let controller = null;
    const revalidate = () => { conceal(); controller?.abort(); controller = new AbortController(); void load(controller.signal); };
    const onPageShow = (event) => { if (event.persisted) revalidate(); };
    const onPageHide = () => concealProtectedRoute(protectedRoot.current);
    window.addEventListener("pageshow", onPageShow); window.addEventListener("pagehide", onPageHide); window.addEventListener("popstate", revalidate);
    return () => { controller?.abort(); window.removeEventListener("pageshow", onPageShow); window.removeEventListener("pagehide", onPageHide); window.removeEventListener("popstate", revalidate); };
  }, [conceal, load]);

  const visibleUsers = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("ko-KR");
    return users.filter((user) => (stateFilter === "all" || user.state === stateFilter)
      && (!query || `${user.login_id ?? ""} ${user.email ?? ""}`.toLocaleLowerCase("ko-KR").includes(query)));
  }, [search, stateFilter, users]);

  const changeState = async (user) => {
    if (!canChangeState(user) || pendingIds.current.has(user.user_id)) return;
    pendingIds.current.add(user.user_id); setPending(new Set(pendingIds.current)); setError(null);
    const nextState = user.state === "active" ? "suspended" : "active";
    try {
      const changed = await setUserState(user.user_id, nextState, { idempotencyKey: createAdminUserIdempotencyKey("admin-state") });
      setUsers((current) => current.map((item) => item.user_id === changed.user_id ? changed : item));
    } catch (caught) {
      setError(new Set(["PROTECTED_ADMIN_ACCOUNT", "IDEMPOTENCY_KEY_REUSED", "FORBIDDEN"]).has(caught?.message)
        ? caught.message : "ADMIN_USER_STATE_FAILED");
    } finally {
      pendingIds.current.delete(user.user_id); setPending(new Set(pendingIds.current));
    }
  };

  const operationKey = createAdminUserIdempotencyKey;
  const saveUser = async (event) => {
    event.preventDefault();
    setError(null);
    try {
      if (modal?.kind === "create") {
        const created = await registerUser(form, { idempotencyKey: operationKey("admin-create") });
        setUsers((current) => [...current, created]);
      } else if (modal?.kind === "edit") {
        const updated = await editUser(modal.user.user_id, { email: form.email }, { idempotencyKey: operationKey("admin-update") });
        let next = updated;
        if (form.state !== modal.user.state && canChangeState(modal.user)) {
          next = await setUserState(updated.user_id, form.state, { idempotencyKey: operationKey("admin-state") });
        }
        setUsers((current) => current.map((item) => item.user_id === next.user_id ? next : item));
      }
      setModal(null); setForm({ login_id: "", email: "", initial_password: "", state: "active" });
    } catch (caught) {
      setError(caught?.message || "ADMIN_USER_MUTATION_FAILED");
    }
  };
  const approve = async (user) => {
    if (!canApprove(user)) return;
    try {
      const updated = await approveUser(user.user_id, { idempotencyKey: operationKey("admin-approve") });
      setUsers((current) => current.map((item) => item.user_id === updated.user_id ? updated : item));
    } catch (caught) { setError(caught?.message || "ADMIN_USER_APPROVAL_FAILED"); }
  };
  const deleteConfirmedUser = async (user) => {
    if (user.protected) return false;
    try {
      await removeUser(user.user_id, { idempotencyKey: operationKey("admin-delete") });
      setUsers((current) => current.filter((item) => item.user_id !== user.user_id));
      return true;
    } catch (caught) { setError(caught?.message || "ADMIN_USER_DELETE_FAILED"); return false; }
  };
  const withDeleteLock = async (action) => {
    if (deleteInFlight.current) return;
    deleteInFlight.current = true; setDeletePending(true);
    try { await action(); }
    finally { deleteInFlight.current = false; setDeletePending(false); }
  };
  const remove = async (user) => {
    if (user.protected || deleteInFlight.current || !window.confirm(`${user.login_id ?? user.user_id} 계정을 삭제하시겠습니까?`)) return;
    await withDeleteLock(() => deleteConfirmedUser(user));
  };

  const reset = async (user) => {
    if (user.protected || !user.has_email || !window.confirm(`${user.login_id ?? user.user_id} 계정에 비밀번호 초기화 메일을 발송하시겠습니까?`)) return;
    try {
      await resetPassword(user.user_id, { idempotencyKey: operationKey("admin-password-reset") });
      setNotice(`${user.login_id ?? user.user_id} 계정의 비밀번호 초기화 메일을 발송했습니다.`);
      setError(null);
    } catch (caught) { setError(caught?.message || "ADMIN_USER_PASSWORD_RESET_FAILED"); }
  };

  const toggleSelected = (userId, checked) => setSelected((current) => {
    const next = new Set(current); if (checked) next.add(userId); else next.delete(userId); return next;
  });
  const toggleAll = (checked) => setSelected(checked ? new Set(visibleUsers.filter((user) => !user.protected).map((user) => user.user_id)) : new Set());
  const removeSelected = async () => {
    const targets = visibleUsers.filter((user) => selected.has(user.user_id) && !user.protected);
    if (deleteInFlight.current || !targets.length || !window.confirm(`${targets.length}명의 계정을 삭제하시겠습니까?`)) return;
    await withDeleteLock(async () => {
      for (const user of targets) {
        if (!(await deleteConfirmedUser(user))) break;
        setSelected((current) => { const next = new Set(current); next.delete(user.user_id); return next; });
      }
    });
  };
  const openEdit = (user) => {
    setForm({ login_id: user.login_id ?? "", email: user.email ?? "", initial_password: "", state: user.state });
    setModal({ kind: "edit", user });
  };
  const openCreate = () => {
    setForm({ login_id: "", email: "", initial_password: "", state: "active" });
    setModal({ kind: "create" });
  };

  const openRole = async (user) => {
    if (roleInFlight.current) return;
    const requestId = ++roleRequestId.current;
    setRolePanel({ user, tenants: [], tenantId: "", membership: null, workspaceId: "", role: "",
      reason: "", latest: null, error: null, notice: null, loading: true, saving: false });
    try {
      const tenants = await getTenants();
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, tenants, loading: false });
    } catch (caught) {
      if (handleRoleAuthentication(caught)) return;
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, error: safeRoleError(caught), loading: false });
    }
  };
  const selectTenant = async (tenantId) => {
    const requestId = ++roleRequestId.current;
    const userId = rolePanel?.user.user_id;
    setRolePanel((current) => current && { ...current, tenantId, membership: null, workspaceId: "",
      role: "", reason: "", latest: null, error: null, notice: null, loading: Boolean(tenantId) });
    if (!tenantId || !userId) return;
    try {
      const membership = await getMemberships(tenantId, userId);
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, membership, loading: false });
    } catch (caught) {
      if (handleRoleAuthentication(caught)) return;
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, error: caught?.message === "RESOURCE_UNAVAILABLE" ? null : safeRoleError(caught), loading: false,
      });
    }
  };
  const selectWorkspace = (workspaceId) => setRolePanel((current) => {
    if (!current) return current;
    const selectedWorkspace = current.membership?.workspaces.find((item) => item.workspace_id === workspaceId);
    return { ...current, workspaceId: selectedWorkspace ? workspaceId : "", role: selectedWorkspace?.role ?? "",
      reason: "", latest: null, error: null, notice: null };
  });
  const saveRole = async () => {
    const panel = rolePanel;
    const currentRole = panel?.membership?.workspaces.find((item) => item.workspace_id === panel.workspaceId);
    const latestRole = panel?.latest?.workspaces.find((item) => item.workspace_id === panel.workspaceId);
    if (!panel?.tenantId || !currentRole || !panel.role || !panel.reason || roleInFlight.current
        || panel.user.protected || panel.user.user_id === sessionUserId) return;
    if (!window.confirm(`${displayNameFor(panel.user)}의 ${panel.workspaceId} 역할을 변경하시겠습니까?`)) return;
    const requestId = roleRequestId.current;
    roleInFlight.current = true;
    setRolePanel((current) => current && { ...current, saving: true, error: null, notice: null });
    try {
      const result = await setMembershipRole(panel.tenantId, panel.workspaceId, panel.user.user_id, {
        role: panel.role, expected_version: (latestRole ?? currentRole).version, reason: panel.reason,
      }, { idempotencyKey: createAdminUserIdempotencyKey("admin-role") });
      const membership = await getMemberships(panel.tenantId, panel.user.user_id);
      const confirmed = membership.workspaces.find((item) => item.workspace_id === panel.workspaceId);
      if (!confirmed || confirmed.role !== panel.role || confirmed.version < result.version) throw new Error("ADMIN_MEMBERSHIP_RESPONSE_INVALID");
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, membership, latest: null, reason: "", notice: "역할 변경 완료", error: null,
      });
    } catch (caught) {
      if (handleRoleAuthentication(caught)) return;
      let latest = null;
      try { latest = await getMemberships(panel.tenantId, panel.user.user_id); }
      catch (refreshError) {
        if (handleRoleAuthentication(refreshError)) return;
        /* Keep the entered choice if refresh fails. */
      }
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, latest, error: safeRoleError(caught), notice: null,
      });
    } finally {
      roleInFlight.current = false;
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, saving: false });
    }
  };
  const selectedRoleView = rolePanel?.membership?.workspaces.find((item) => item.workspace_id === rolePanel.workspaceId);
  const latestRoleView = rolePanel?.latest?.workspaces.find((item) => item.workspace_id === rolePanel.workspaceId);

  return <main className="admin-user-page">
    <header className="admin-user-header"><div><p>계정 및 권한 설정</p><h1>사용자·계정 관리</h1><small>일반 사용자를 등록하고 승인·수정·중지·삭제합니다.</small></div><div className="admin-user-header-actions"><button type="button" onClick={openCreate}>사용자 등록</button><a href="/notebooks">Notebook으로</a></div></header>
    {!sessionValidated && <section className="admin-user-state" role="status" aria-busy="true">사용자 목록을 불러오는 중입니다.</section>}
    <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
      aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
    {error && <section className="admin-user-state admin-user-error" role="alert"><span>{error}</span><button type="button" onClick={() => void load()}>다시 시도</button></section>}
    {notice && <section className="admin-user-state admin-user-notice" role="status">{notice}</section>}
    {state === "ready" && <section className="admin-user-card" aria-labelledby="admin-user-list-title">
      <div className="admin-user-toolbar"><div className="admin-user-tools"><label>사용자 ID 또는 이메일 검색<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="로그인 ID, 사용자 ID 또는 이메일" /></label><label>전체 상태<select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">전체 상태</option><option value="active">활성</option><option value="suspended">중지</option><option value="pending_email">이메일 인증 대기</option><option value="pending_approval">승인 대기</option></select></label><button type="button" onClick={() => setSearch(search.trim())}>조회</button></div><div className="admin-user-bulk-actions"><span aria-live="polite">선택 {selected.size}건</span><button type="button" disabled={!selected.size || deletePending} onClick={() => void removeSelected()}>선택 삭제</button><button type="button" onClick={() => void load()}>새로고침</button></div></div>
      <p className="admin-user-result-count">{visibleUsers.length}명의 계정을 조회했습니다.</p>
      {visibleUsers.length === 0 ? <p className="admin-user-empty">조건에 맞는 사용자가 없습니다.</p> : <div className="admin-user-table-wrap"><table className="admin-user-table"><thead><tr><th scope="col"><input type="checkbox" aria-label="전체 사용자 선택" checked={visibleUsers.some((user) => !user.protected) && visibleUsers.filter((user) => !user.protected).every((user) => selected.has(user.user_id))} onChange={(event) => toggleAll(event.target.checked)} /></th><th scope="col">사용자</th><th scope="col">이메일</th><th scope="col">상태</th><th scope="col">역할</th><th scope="col">관리</th></tr></thead><tbody>{visibleUsers.map((user) => <tr key={user.user_id}><td><input type="checkbox" aria-label={`${displayNameFor(user)} 선택`} disabled={user.protected} checked={selected.has(user.user_id)} onChange={(event) => toggleSelected(user.user_id, event.target.checked)} /></td><td><strong>{displayNameFor(user)}</strong>{user.protected && <small>보호된 시스템 관리자</small>}</td><td>{user.email ?? "이메일 없음"}</td><td><span className={`admin-user-status status-${user.state}`}>{stateLabel[user.state] ?? user.state}</span></td><td><div className="admin-user-role-list"><span>{MEMBERSHIP_ROLE_UNAVAILABLE}</span></div></td><td><div className="admin-user-row-actions"><button type="button" aria-label={`${displayNameFor(user)} 역할 조회`} onClick={() => void openRole(user)}>역할 조회</button><button type="button" disabled={!canChangeState(user) || pending.has(user.user_id)} aria-label={`${displayNameFor(user)} 계정 상태 변경`} onClick={() => void changeState(user)}>{user.protected ? "보호됨" : pending.has(user.user_id) ? "처리 중…" : user.state === "active" ? "중지" : user.state === "suspended" ? "재활성화" : user.state === "pending_email" ? "인증 대기" : stateLabel[user.state]}</button>{canApprove(user) && <button type="button" onClick={() => void approve(user)}>승인</button>}{!user.protected && <><button type="button" onClick={() => openEdit(user)}>수정</button><button type="button" onClick={() => void reset(user)} disabled={!user.has_email}>비밀번호 초기화</button><button type="button" onClick={() => void remove(user)} disabled={deletePending}>삭제</button></>}</div></td></tr>)}</tbody></table></div>}
    </section>}
    {rolePanel && <section className="admin-user-card" aria-label="유효 멤버십 역할">
      <h2>{displayNameFor(rolePanel.user)} · 유효 멤버십 역할</h2>
      <button type="button" disabled={rolePanel.saving} onClick={() => {
        if (roleInFlight.current) return;
        roleRequestId.current += 1; setRolePanel(null);
      }}>닫기</button>
      <p>시스템 관리자 여부 (별도 권한): {rolePanel.tenantId && rolePanel.membership && !rolePanel.error
        && typeof rolePanel.membership.target_is_system_admin === "boolean"
        ? (rolePanel.membership.target_is_system_admin ? "예" : "아니요") : "미조회"}</p>
      {rolePanel.error && <p role="alert">{rolePanel.error}</p>}
      {rolePanel.notice && <p role="status">{rolePanel.notice}</p>}
      <label>조직 선택<select aria-label="조직 선택" value={rolePanel.tenantId}
        onChange={(event) => void selectTenant(event.target.value)} disabled={rolePanel.saving}>
        <option value="">조직을 선택하세요</option>
        {rolePanel.tenants.map((tenant) => <option key={tenant.tenant_id} value={tenant.tenant_id}>{tenant.name}</option>)}
      </select></label>
      {rolePanel.loading && <p role="status">유효 역할 조회 중…</p>}
      {!rolePanel.loading && (!rolePanel.tenantId || !rolePanel.membership) && <p>역할 미조회</p>}
      {rolePanel.membership && <>
        <p>조직 역할 (읽기 전용): {rolePanel.membership.tenant
          ? `${rolePanel.membership.tenant.role} · ${rolePanel.membership.tenant.state} · version ${rolePanel.membership.tenant.version}`
          : "역할 미조회"}</p>
        <label>작업공간 선택<select aria-label="작업공간 선택" value={rolePanel.workspaceId}
          onChange={(event) => selectWorkspace(event.target.value)} disabled={rolePanel.saving}>
          <option value="">작업공간을 선택하세요</option>
          {rolePanel.membership.workspaces.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.workspace_id}</option>)}
        </select></label>
        {selectedRoleView ? <>
          <p>현재 역할: {selectedRoleView.role} ({workspaceRoleLabels[selectedRoleView.role]}) · {selectedRoleView.state} · version {selectedRoleView.version}</p>
          <label>변경할 역할<select aria-label="변경할 역할" value={rolePanel.role}
            onChange={(event) => setRolePanel((current) => ({ ...current, role: event.target.value, notice: null }))}>
            {Object.entries(workspaceRoleLabels).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select></label>
          <label>변경 사유<select aria-label="변경 사유" value={rolePanel.reason}
            onChange={(event) => setRolePanel((current) => ({ ...current, reason: event.target.value, notice: null }))}>
            <option value="">사유를 선택하세요</option>
            {ADMIN_MEMBERSHIP_REASONS.map(({ code, label }) => <option key={code} value={code}>{label}</option>)}
          </select></label>
          {latestRoleView && <p>최신 역할: {latestRoleView.role} · {latestRoleView.state} · version {latestRoleView.version}</p>}
          <button type="button" disabled={!rolePanel.reason || rolePanel.saving || rolePanel.user.protected
            || rolePanel.user.user_id === sessionUserId || (rolePanel.latest && !latestRoleView)}
            onClick={() => void saveRole()}>{rolePanel.saving ? "처리 중…" : "역할 변경"}</button>
        </> : <p>역할 미조회</p>}
      </>}
    </section>}
    </div>
    {modal && <div className="admin-user-modal-backdrop" role="presentation"><form className="admin-user-modal" aria-labelledby="admin-user-modal-title" onSubmit={saveUser}><h2 id="admin-user-modal-title">{modal.kind === "create" ? "사용자 등록" : "사용자 수정"}</h2>{modal.kind === "edit" && <p>{form.login_id} · {form.email}</p>}{modal.kind === "create" && <label>사용자 ID<input required value={form.login_id} onChange={(event) => setForm({ ...form, login_id: event.target.value })} /></label>}<label>이메일<input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>{modal.kind === "edit" && <label>상태<select value={form.state} onChange={(event) => setForm({ ...form, state: event.target.value })}><option value="active">활성</option><option value="suspended">중지</option><option value="pending_email">이메일 인증 대기</option><option value="pending_approval">승인 대기</option></select></label>}{modal.kind === "edit" && <p>{MEMBERSHIP_ROLE_UNAVAILABLE}</p>}{modal.kind === "create" && <label>초기 비밀번호<input required type="password" minLength={12} value={form.initial_password} onChange={(event) => setForm({ ...form, initial_password: event.target.value })} /><span>12자 이상으로 입력하세요.</span></label>}<div><button type="button" onClick={() => setModal(null)}>취소</button><button type="submit">수정</button></div></form></div>}
  </main>;
}
