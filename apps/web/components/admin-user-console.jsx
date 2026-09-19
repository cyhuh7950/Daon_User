"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";
import { changeAdminUserState, listAdminUsers } from "../lib/admin-users-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["ADMIN_USERS_UNAVAILABLE", "ADMIN_USERS_RESPONSE_INVALID", "FORBIDDEN"]);
const replaceLocation = (path) => window.location.replace(path);
const MUTABLE_STATES = new Set(["active", "suspended"]);
const canChangeState = (user) => !user.protected && MUTABLE_STATES.has(user.state);

export function AdminUserConsole({
  getSession = getCurrentNotebookSession,
  getUsers = listAdminUsers,
  setUserState = changeAdminUserState,
  navigate = replaceLocation,
}) {
  const protectedRoot = useRef(null);
  const pendingIds = useRef(new Set());
  const [sessionValidated, setSessionValidated] = useState(false);
  const [state, setState] = useState("loading");
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [pending, setPending] = useState(new Set());
  const [error, setError] = useState(null);

  const conceal = useCallback(() => {
    concealProtectedRoute(protectedRoot.current);
    setSessionValidated(false);
  }, []);
  const reveal = useCallback(() => {
    revealProtectedRoute(protectedRoot.current);
    setSessionValidated(true);
  }, []);

  const load = useCallback(async (signal) => {
    conceal(); setState("loading"); setError(null); setUsers([]);
    try {
      const session = await getSession({ signal });
      if (session.password_change_required) { navigate("/password-change"); return; }
      if (!session.is_system_admin) { navigate("/notebooks"); return; }
      const result = await getUsers({ signal });
      if (signal?.aborted) return;
    setUsers(result); setSelectedIds(new Set()); setState("ready"); reveal();
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
      && (!query || `${user.login_id ?? ""} ${user.user_id} ${user.email ?? ""}`.toLocaleLowerCase("ko-KR").includes(query)));
  }, [search, stateFilter, users]);

  const allVisibleSelected = visibleUsers.length > 0 && visibleUsers.every((user) => selectedIds.has(user.user_id));
  const toggleSelected = (userId) => setSelectedIds((current) => {
    const next = new Set(current);
    if (next.has(userId)) next.delete(userId); else next.add(userId);
    return next;
  });
  const toggleAllVisible = () => setSelectedIds((current) => {
    const next = new Set(current);
    if (allVisibleSelected) visibleUsers.forEach((user) => next.delete(user.user_id));
    else visibleUsers.forEach((user) => next.add(user.user_id));
    return next;
  });

  const changeState = async (user) => {
    if (!canChangeState(user) || pendingIds.current.has(user.user_id)) return;
    pendingIds.current.add(user.user_id); setPending(new Set(pendingIds.current)); setError(null);
    const nextState = user.state === "active" ? "suspended" : "active";
    try {
      const changed = await setUserState(user.user_id, nextState, { idempotencyKey: `admin-state-${crypto.randomUUID()}` });
      setUsers((current) => current.map((item) => item.user_id === changed.user_id ? changed : item));
    } catch (caught) {
      setError(new Set(["PROTECTED_ADMIN_ACCOUNT", "IDEMPOTENCY_KEY_REUSED", "FORBIDDEN"]).has(caught?.message)
        ? caught.message : "ADMIN_USER_STATE_FAILED");
    } finally {
      pendingIds.current.delete(user.user_id); setPending(new Set(pendingIds.current));
    }
  };

  return <main className="admin-user-page">
    <header className="admin-user-header"><div><p>계정 및 권한 설정</p><h1>사용자·계정 관리</h1></div><div className="admin-user-actions"><span className="admin-user-selection">선택 {selectedIds.size}건</span><button type="button" disabled={!selectedIds.size} onClick={() => setError("선택 삭제 기능은 아직 제공되지 않습니다.")}>선택 삭제</button><button type="button" onClick={() => void load()}>새로고침</button><button type="button" onClick={() => setError("사용자 등록 기능은 아직 제공되지 않습니다.")}>사용자 등록</button></div></header>
    {!sessionValidated && <section className="admin-user-state" role="status" aria-busy="true">사용자 목록을 불러오는 중입니다.</section>}
    <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
      aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
    {error && <section className="admin-user-state admin-user-error" role="alert"><span>{error}</span><button type="button" onClick={() => void load()}>다시 시도</button></section>}
    {state === "ready" && <section className="admin-user-card" aria-labelledby="admin-user-list-title">
      <div className="admin-user-toolbar"><div className="admin-user-search"><input type="search" value={searchDraft} onChange={(event) => { setSearchDraft(event.target.value); setSearch(event.target.value); }} placeholder="로그인 ID, 사용자 ID 또는 이메일" /><select aria-label="전체 상태" value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">전체 상태</option><option value="active">활성</option><option value="suspended">중지</option></select><button type="button" onClick={() => setSearch(searchDraft)}>조회</button></div></div>
      <p className="admin-user-count">{visibleUsers.length}명의 계정을 조회했습니다</p>
      {visibleUsers.length === 0 ? <p className="admin-user-empty">조건에 맞는 사용자가 없습니다.</p> : <div className="admin-user-table-wrap"><table className="admin-user-table"><thead><tr><th scope="col"><input type="checkbox" aria-label="표시된 사용자 전체 선택" checked={allVisibleSelected} onChange={toggleAllVisible} /></th><th scope="col">사용자</th><th scope="col">이메일</th><th scope="col">상태</th><th scope="col">역할</th><th scope="col">관리</th></tr></thead><tbody>
        {visibleUsers.map((user) => <tr key={user.user_id}>
          <td><input type="checkbox" aria-label={`${user.login_id ?? user.user_id} 선택`} checked={selectedIds.has(user.user_id)} onChange={() => toggleSelected(user.user_id)} /></td>
          <td><strong>{user.login_id ?? "로그인 ID 없음"}</strong><small>{user.user_id}</small>{user.protected ? <small>보호된 시스템 관리자</small> : null}</td>
          <td>{user.email ?? "이메일 없음"}</td>
          <td><span className={`admin-user-status ${user.state}`}>{user.protected ? "보호됨" : user.state === "pending_email" ? "이메일 인증 대기" : user.state === "active" ? "활성" : "중지"}</span></td>
          <td><span className="admin-user-role">{user.protected ? "관리자" : "사용자"}</span></td>
          <td><div className="admin-user-row-actions"><button type="button" disabled={user.protected || !canChangeState(user) || pending.has(user.user_id)} aria-label={`${user.login_id ?? user.user_id} 계정 상태 변경`} onClick={() => void changeState(user)}>{user.protected ? "보호됨" : pending.has(user.user_id) ? "처리 중…" : user.state === "active" ? "중지" : user.state === "pending_email" ? "인증 대기" : "재활성화"}</button><button type="button" onClick={() => setError("사용자 수정 기능은 아직 제공되지 않습니다.")}>수정</button></div></td>
        </tr>)}
      </tbody></table></div>}
    </section>}
    </div>
  </main>;
}
