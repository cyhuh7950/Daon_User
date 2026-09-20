"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";
import {
  approveAdminUser, changeAdminUserState, createAdminUser, deleteAdminUser,
  listAdminUsers, requestAdminUserPasswordReset, updateAdminUser, createAdminUserIdempotencyKey,
} from "../lib/admin-users-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["ADMIN_USERS_UNAVAILABLE", "ADMIN_USERS_RESPONSE_INVALID", "FORBIDDEN"]);
const replaceLocation = (path) => window.location.replace(path);
const MUTABLE_STATES = new Set(["active", "suspended"]);
const canChangeState = (user) => !user.protected && MUTABLE_STATES.has(user.state);
const canApprove = (user) => !user.protected && user.state === "pending_approval";
const stateLabel = { active: "활성", suspended: "중지", pending_email: "이메일 인증 대기", pending_approval: "승인 대기" };
const rolesFor = (user) => user.protected ? ["관리자"] : ["일반 사용자"];
const displayNameFor = (user) => user.login_id ?? user.email ?? "이름 없음";

export function AdminUserConsole({
  getSession = getCurrentNotebookSession,
  getUsers = listAdminUsers,
  setUserState = changeAdminUserState,
  registerUser = createAdminUser,
  editUser = updateAdminUser,
  approveUser = approveAdminUser,
  removeUser = deleteAdminUser,
  resetPassword = requestAdminUserPasswordReset,
  navigate = replaceLocation,
}) {
  const protectedRoot = useRef(null);
  const pendingIds = useRef(new Set());
  const [sessionValidated, setSessionValidated] = useState(false);
  const [state, setState] = useState("loading");
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [pending, setPending] = useState(new Set());
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({ login_id: "", email: "", initial_password: "", state: "active", roles: ["전문가"] });

  const conceal = useCallback(() => {
    concealProtectedRoute(protectedRoot.current);
    setSessionValidated(false);
  }, []);
  const reveal = useCallback(() => {
    revealProtectedRoute(protectedRoot.current);
    setSessionValidated(true);
  }, []);

  const load = useCallback(async (signal) => {
    conceal(); setState("loading"); setError(null); setNotice(null); setUsers([]); setSelected(new Set());
    try {
      const session = await getSession({ signal });
      if (session.password_change_required) { navigate("/password-change"); return; }
      if (!session.is_system_admin) { navigate("/notebooks"); return; }
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
      setModal(null); setForm({ login_id: "", email: "", initial_password: "", state: "active", roles: ["전문가"] });
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
  const remove = async (user) => {
    if (user.protected || !window.confirm(`${user.login_id ?? user.user_id} 계정을 삭제하시겠습니까?`)) return;
    try {
      await removeUser(user.user_id, { idempotencyKey: operationKey("admin-delete") });
      setUsers((current) => current.filter((item) => item.user_id !== user.user_id));
    } catch (caught) { setError(caught?.message || "ADMIN_USER_DELETE_FAILED"); }
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
    if (!targets.length || !window.confirm(`${targets.length}명의 계정을 삭제하시겠습니까?`)) return;
    for (const user of targets) await remove(user);
    setSelected(new Set());
  };
  const openEdit = (user) => {
    setForm({ login_id: user.login_id ?? "", email: user.email ?? "", initial_password: "", state: user.state, roles: rolesFor(user) });
    setModal({ kind: "edit", user });
  };
  const openCreate = () => {
    setForm({ login_id: "", email: "", initial_password: "", state: "active", roles: ["일반 사용자"] });
    setModal({ kind: "create" });
  };

  return <main className="admin-user-page">
    <header className="admin-user-header"><div><p>계정 및 권한 설정</p><h1>사용자·계정 관리</h1><small>일반 사용자를 등록하고 승인·수정·중지·삭제합니다.</small></div><div className="admin-user-header-actions"><button type="button" onClick={openCreate}>사용자 등록</button><a href="/notebooks">Notebook으로</a></div></header>
    {!sessionValidated && <section className="admin-user-state" role="status" aria-busy="true">사용자 목록을 불러오는 중입니다.</section>}
    <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
      aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
    {error && <section className="admin-user-state admin-user-error" role="alert"><span>{error}</span><button type="button" onClick={() => void load()}>다시 시도</button></section>}
    {notice && <section className="admin-user-state admin-user-notice" role="status">{notice}</section>}
    {state === "ready" && <section className="admin-user-card" aria-labelledby="admin-user-list-title">
      <div className="admin-user-toolbar"><div className="admin-user-tools"><label>사용자 ID 또는 이메일 검색<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="로그인 ID, 사용자 ID 또는 이메일" /></label><label>전체 상태<select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">전체 상태</option><option value="active">활성</option><option value="suspended">중지</option><option value="pending_email">이메일 인증 대기</option><option value="pending_approval">승인 대기</option></select></label><button type="button" onClick={() => setSearch(search.trim())}>조회</button></div><div className="admin-user-bulk-actions"><span aria-live="polite">선택 {selected.size}건</span><button type="button" disabled={!selected.size} onClick={() => void removeSelected()}>선택 삭제</button><button type="button" onClick={() => void load()}>새로고침</button></div></div>
      <p className="admin-user-result-count">{visibleUsers.length}명의 계정을 조회했습니다.</p>
      {visibleUsers.length === 0 ? <p className="admin-user-empty">조건에 맞는 사용자가 없습니다.</p> : <div className="admin-user-table-wrap"><table className="admin-user-table"><thead><tr><th scope="col"><input type="checkbox" aria-label="전체 사용자 선택" checked={visibleUsers.some((user) => !user.protected) && visibleUsers.filter((user) => !user.protected).every((user) => selected.has(user.user_id))} onChange={(event) => toggleAll(event.target.checked)} /></th><th scope="col">사용자</th><th scope="col">이메일</th><th scope="col">상태</th><th scope="col">역할</th><th scope="col">관리</th></tr></thead><tbody>{visibleUsers.map((user) => <tr key={user.user_id}><td><input type="checkbox" aria-label={`${displayNameFor(user)} 선택`} disabled={user.protected} checked={selected.has(user.user_id)} onChange={(event) => toggleSelected(user.user_id, event.target.checked)} /></td><td><strong>{displayNameFor(user)}</strong>{user.protected && <small>보호된 시스템 관리자</small>}</td><td>{user.email ?? "이메일 없음"}</td><td><span className={`admin-user-status status-${user.state}`}>{stateLabel[user.state] ?? user.state}</span></td><td><div className="admin-user-role-list">{rolesFor(user).map((role) => <span key={role}>{role}</span>)}</div></td><td><div className="admin-user-row-actions"><button type="button" disabled={!canChangeState(user) || pending.has(user.user_id)} aria-label={`${displayNameFor(user)} 계정 상태 변경`} onClick={() => void changeState(user)}>{user.protected ? "보호됨" : pending.has(user.user_id) ? "처리 중…" : user.state === "active" ? "중지" : user.state === "suspended" ? "재활성화" : user.state === "pending_email" ? "인증 대기" : stateLabel[user.state]}</button>{canApprove(user) && <button type="button" onClick={() => void approve(user)}>승인</button>}{!user.protected && <><button type="button" onClick={() => openEdit(user)}>수정</button><button type="button" onClick={() => void reset(user)} disabled={!user.has_email}>비밀번호 초기화</button><button type="button" onClick={() => void remove(user)}>삭제</button></>}</div></td></tr>)}</tbody></table></div>}
    </section>}
    </div>
    {modal && <div className="admin-user-modal-backdrop" role="presentation"><form className="admin-user-modal" aria-labelledby="admin-user-modal-title" onSubmit={saveUser}><h2 id="admin-user-modal-title">{modal.kind === "create" ? "사용자 등록" : "사용자 수정"}</h2>{modal.kind === "edit" && <p>{form.login_id} · {form.email}</p>}{modal.kind === "create" && <label>사용자 ID<input required value={form.login_id} onChange={(event) => setForm({ ...form, login_id: event.target.value })} /></label>}<label>이메일<input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>{modal.kind === "edit" && <label>상태<select value={form.state} onChange={(event) => setForm({ ...form, state: event.target.value })}><option value="active">활성</option><option value="suspended">중지</option><option value="pending_email">이메일 인증 대기</option><option value="pending_approval">승인 대기</option></select></label>}{modal.kind === "edit" && <fieldset className="admin-user-role-fields"><legend>권한 역할</legend><label><input type="checkbox" checked={form.roles.includes("관리자")} disabled={modal.user.protected} readOnly />관리자</label><label><input type="checkbox" checked={form.roles.includes("전문가")} disabled readOnly />전문가</label><small>역할 정책은 현재 계정 권한 계약으로 표시됩니다.</small></fieldset>}{modal.kind === "create" && <label>초기 비밀번호<input required type="password" minLength={12} value={form.initial_password} onChange={(event) => setForm({ ...form, initial_password: event.target.value })} /><span>12자 이상으로 입력하세요.</span></label>}<div><button type="button" onClick={() => setModal(null)}>취소</button><button type="submit">수정</button></div></form></div>}
  </main>;
}
