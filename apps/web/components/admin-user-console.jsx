"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";
import { changeAdminUserState, listAdminUsers } from "../lib/admin-users-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const SAFE_ERRORS = new Set(["ADMIN_USERS_UNAVAILABLE", "ADMIN_USERS_RESPONSE_INVALID", "FORBIDDEN"]);
const replaceLocation = (path) => window.location.replace(path);

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
  const [stateFilter, setStateFilter] = useState("all");
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
      && (!query || `${user.login_id ?? ""} ${user.user_id}`.toLocaleLowerCase("ko-KR").includes(query)));
  }, [search, stateFilter, users]);

  const changeState = async (user) => {
    if (user.protected || pendingIds.current.has(user.user_id)) return;
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
    <header className="admin-user-header"><div><p>DAON ADMIN</p><h1>사용자 관리</h1><small>계정 상태를 조회하고 일반 사용자를 활성화하거나 중지합니다.</small></div><a href="/notebooks">Notebook으로</a></header>
    {!sessionValidated && <section className="admin-user-state" role="status" aria-busy="true">사용자 목록을 불러오는 중입니다.</section>}
    <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
      aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
    {error && <section className="admin-user-state admin-user-error" role="alert"><span>{error}</span><button type="button" onClick={() => void load()}>다시 시도</button></section>}
    {state === "ready" && <section className="admin-user-card" aria-labelledby="admin-user-list-title">
      <h2 id="admin-user-list-title">사용자 목록</h2>
      <div className="admin-user-tools"><label>사용자 검색<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="로그인 ID 또는 사용자 ID" /></label><label>상태 필터<select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">전체</option><option value="active">활성</option><option value="suspended">중지</option></select></label></div>
      {visibleUsers.length === 0 ? <p className="admin-user-empty">조건에 맞는 사용자가 없습니다.</p> : <div className="admin-user-list">
        {visibleUsers.map((user) => <article className="admin-user-row" key={user.user_id}>
          <div><strong>{user.login_id ?? "로그인 ID 없음"}</strong><span>{user.user_id}</span><small>{user.has_email ? "이메일 등록" : "이메일 없음"} · {user.protected ? "보호된 시스템 관리자" : user.state === "active" ? "활성 계정" : "중지된 계정"}</small></div>
          <button type="button" disabled={user.protected || pending.has(user.user_id)} aria-label={`${user.login_id ?? user.user_id} 계정 상태 변경`} onClick={() => void changeState(user)}>{user.protected ? "보호됨" : pending.has(user.user_id) ? "처리 중…" : user.state === "active" ? "중지" : "재활성화"}</button>
        </article>)}
      </div>}
    </section>}
    </div>
  </main>;
}
