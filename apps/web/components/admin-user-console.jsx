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
const MEMBERSHIP_ROLE_UNAVAILABLE = "조직·Workspace 선택 필요";
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
  const editInFlight = useRef(false);
  const modalOpener = useRef(null);
  const modalCancel = useRef(null);
  const deleteInFlight = useRef(false);
  const roleRequestId = useRef(0);
  const roleInFlight = useRef(false);
  const unconfirmedUsers = useRef(new Map());
  const uncertaintySession = useRef(null);
  const [sessionValidated, setSessionValidated] = useState(false);
  const [sessionUserId, setSessionUserId] = useState(null);
  const [state, setState] = useState("loading");
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [modalPending, setModalPending] = useState(false);
  const [deletePending, setDeletePending] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({ login_id: "", email: "", initial_password: "", state: "active" });
  const [rolePanel, setRolePanel] = useState(null);
  const [roleSummary, setRoleSummary] = useState(null);

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
    unconfirmedUsers.current.clear(); uncertaintySession.current = null;
    conceal(); setUsers([]); setSelected(new Set()); setRolePanel(null); setRoleSummary(null); setModal(null);
    setSessionUserId(null); setNotice(null); setError(null);
    navigate("/");
    return true;
  };

  const load = useCallback(async (signal) => {
    const requestId = ++roleRequestId.current; setRolePanel(null); setRoleSummary(null); setModal(null);
    conceal(); setState("loading"); setError(null); setNotice(null); setUsers([]); setSelected(new Set());
    try {
      const session = await getSession({ signal });
      if (signal?.aborted || roleRequestId.current !== requestId) return;
      const identity = JSON.stringify([session.user_id, session.tenant_id, session.workspace_id, session.session_id, session.device_id]);
      if (uncertaintySession.current !== identity) unconfirmedUsers.current.clear();
      uncertaintySession.current = identity;
      if (session.password_change_required) { navigate("/password-change"); return; }
      if (!session.is_system_admin) { navigate("/notebooks"); return; }
      setSessionUserId(session.user_id ?? null);
      const settledSaves = new Map([...unconfirmedUsers.current]
        .filter(([, pending]) => pending.save && !pending.save.inFlight).map(([id, pending]) => [id, pending.save]));
      const result = await getUsers({ signal });
      if (signal?.aborted || roleRequestId.current !== requestId) return;
      for (const [userId, savedRequest] of settledSaves) {
        const pending = unconfirmedUsers.current.get(userId);
        if (pending?.save !== savedRequest) continue;
        if (pending.reset) unconfirmedUsers.current.set(userId, { reset: pending.reset });
        else unconfirmedUsers.current.delete(userId);
      }
      setUsers(result); setState("ready"); reveal();
    } catch (caught) {
      if (signal?.aborted || roleRequestId.current !== requestId) return;
      if (caught?.message === "AUTHENTICATION_REQUIRED") { unconfirmedUsers.current.clear(); uncertaintySession.current = null; navigate("/"); return; }
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
  const deletionTargets = visibleUsers.filter((user) => selected.has(user.user_id) && !user.protected);
  useEffect(() => {
    setSelected((current) => new Set(visibleUsers.filter((user) => !user.protected && current.has(user.user_id)).map((user) => user.user_id)));
  }, [visibleUsers]);
  useEffect(() => {
    if (!modal) return;
    const opener = modalOpener.current;
    modalCancel.current?.focus();
    return () => {
      if (!protectedRoot.current?.hidden && document.body.contains(opener)) opener?.focus();
    };
  }, [modal?.kind, modal?.user?.user_id]);
  const closeModal = () => {
    if (editInFlight.current || roleInFlight.current) return;
    roleRequestId.current += 1; setRolePanel(null); setModal(null);
    setForm({ login_id: "", email: "", initial_password: "", state: "active" });
  };
  const modalKeys = (event) => {
    if (event.key === "Escape") { event.preventDefault(); closeModal(); return; }
    if (event.key !== "Tab") return;
    const controls = [];
    const collect = (node) => {
      for (const child of node.childNodes ?? []) {
        if (child.hidden || child.inert) continue;
        if (["BUTTON", "INPUT", "SELECT"].includes(child.tagName) && !child.disabled) controls.push(child);
        collect(child);
      }
    };
    collect(event.currentTarget);
    const first = controls[0], last = controls.at(-1);
    if (!first) { event.preventDefault(); event.currentTarget.focus(); }
    else if (event.shiftKey && (document.activeElement === first || !event.currentTarget.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || !event.currentTarget.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
  };

  const operationKey = createAdminUserIdempotencyKey;
  const saveUser = async (event) => {
    event.preventDefault();
    if (!modal || modal.uncertain || unconfirmedUsers.current.get(modal.user?.user_id)?.save || editInFlight.current || deleteInFlight.current || modal.user?.protected) return;
    const epoch = roleRequestId.current;
    const current = () => roleRequestId.current === epoch;
    const completed = [...(modal.completed ?? [])];
    let stage = "등록";
    const selectedRole = rolePanel?.membership?.workspaces.find((item) => item.workspace_id === rolePanel.workspaceId);
    const roleChanged = modal.kind === "edit" && selectedRole && rolePanel.role !== selectedRole.role;
    if (modal.kind === "edit") {
      if (form.email !== (modal.user.email ?? "") && (!form.email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(form.email))) { setError("유효한 이메일을 입력하세요."); return; }
      if (form.state !== modal.user.state && (!canChangeState(modal.user) || !MUTABLE_STATES.has(form.state)
          || modal.user.user_id === sessionUserId)) { setError("변경할 수 없는 계정 상태입니다."); return; }
      if (roleChanged && (!rolePanel.membershipVerified || rolePanel.error || selectedRole.state !== "active"
          || !Object.hasOwn(workspaceRoleLabels, rolePanel.role) || !ADMIN_MEMBERSHIP_REASONS.some(({ code }) => code === rolePanel.reason)
          || modal.user.user_id === sessionUserId)) { setError("역할·사유와 현재 권한을 다시 조회·확인하세요."); return; }
      if (roleChanged && !window.confirm(`${displayNameFor(modal.user)}의 ${rolePanel.workspaceId} 역할을 변경하시겠습니까?`)) return;
    }
    const requestSession = uncertaintySession.current;
    const targetId = modal.kind === "edit" ? modal.user.user_id : null;
    const saveRequest = { inFlight: true, completed };
    if (targetId) unconfirmedUsers.current.set(targetId, { ...unconfirmedUsers.current.get(targetId), save: saveRequest });
    editInFlight.current = true; setModalPending(true);
    setError(null);
    const acceptUser = (updated, label) => {
      if (updated?.user_id !== modal.user.user_id) throw new Error("ADMIN_USERS_RESPONSE_INVALID");
      completed.push(label);
      setUsers((items) => items.map((item) => item.user_id === updated.user_id ? updated : item));
      setModal((value) => value && { ...value, user: updated, completed: [...completed] });
    };
    try {
      if (modal?.kind === "create") {
        const created = await registerUser({ login_id: form.login_id, email: form.email, initial_password: form.initial_password }, { idempotencyKey: operationKey("admin-create") });
        if (!current()) return;
        setUsers((items) => [...items, created]);
      } else if (modal?.kind === "edit") {
        if (form.email !== (modal.user.email ?? "")) {
          stage = "이메일";
          const updated = await editUser(modal.user.user_id, { email: form.email }, { idempotencyKey: operationKey("admin-update") });
          if (!current()) return;
          acceptUser(updated, stage);
        }
        if (roleChanged) {
          stage = "역할";
          if (!(await saveRole())) throw new Error("ADMIN_MEMBERSHIP_UNAVAILABLE");
          if (!current()) return;
          completed.push(stage);
        }
        if (form.state !== modal.user.state && canChangeState(modal.user)) {
          stage = "상태";
          const updated = await setUserState(modal.user.user_id, form.state, { idempotencyKey: operationKey("admin-state") });
          if (!current()) return;
          acceptUser(updated, stage);
        }
      }
      if (current()) {
        const pending = unconfirmedUsers.current.get(targetId);
        if (pending?.save === saveRequest) {
          if (pending.reset) unconfirmedUsers.current.set(targetId, { reset: pending.reset });
          else unconfirmedUsers.current.delete(targetId);
        }
        roleRequestId.current += 1; setRolePanel(null); setModal(null); setForm({ login_id: "", email: "", initial_password: "", state: "active" });
      }
    } catch (caught) {
      if (!current() || handleRoleAuthentication(caught)) return;
      roleRequestId.current += 1;
      setRoleSummary(null);
      setRolePanel((panel) => panel && { ...panel, membershipVerified: false, loading: false });
      if (modal.kind === "edit") unconfirmedUsers.current.set(modal.user.user_id, {
        ...unconfirmedUsers.current.get(modal.user.user_id), save: { inFlight: false, completed: [...completed] },
      });
      setModal((value) => value && { ...value, uncertain: true, completed: [...completed] });
      setError(`완료 항목: ${completed.join(", ") || "없음"}. ${stage} 실패 또는 결과 미확정 — 다시 조회·확인하세요. ${safeRoleError(caught)}`);
    } finally {
      if (uncertaintySession.current === requestSession && unconfirmedUsers.current.get(targetId)?.save === saveRequest) saveRequest.inFlight = false;
      editInFlight.current = false; setModalPending(false);
    }
  };
  const recheckEdit = async () => {
    if (!modal?.uncertain || editInFlight.current) return;
    if (modal.kind === "create") { await load(); return; }
    const epoch = roleRequestId.current;
    editInFlight.current = true; setModalPending(true); setRoleSummary(null);
    try {
      const refreshed = await getUsers();
      const user = refreshed.find((item) => item.user_id === modal.user.user_id);
      if (!user) throw new Error("RESOURCE_UNAVAILABLE");
      const membership = rolePanel?.tenantId ? await getMemberships(rolePanel.tenantId, user.user_id) : null;
      if (epoch !== roleRequestId.current) return;
      const workspace = membership?.workspaces.find((item) => item.workspace_id === rolePanel.workspaceId);
      const pendingReset = unconfirmedUsers.current.get(user.user_id)?.reset;
      const resetUncertain = Boolean(pendingReset);
      if (resetUncertain) unconfirmedUsers.current.set(user.user_id, { reset: pendingReset });
      else unconfirmedUsers.current.delete(user.user_id);
      setUsers(refreshed);
      setModal({ kind: "edit", user, completed: [], resetUncertain });
      setForm({ login_id: user.login_id ?? "", email: user.email ?? "", initial_password: "", state: user.state });
      setRolePanel((panel) => panel && { ...panel, user, membership, membershipVerified: Boolean(membership),
        workspaceId: workspace?.workspace_id ?? "", role: workspace?.role ?? "", reason: "", latest: null, error: null });
      if (workspace) rememberRole(rolePanel, workspace);
      setError(null); setNotice("서버 상태를 다시 조회했습니다. 미저장 입력은 버렸습니다. 필요한 변경만 다시 선택하세요.");
    } catch (caught) {
      if (epoch === roleRequestId.current && !handleRoleAuthentication(caught)) setError(`재조회 실패 — 저장 결과를 확인할 수 없습니다. ${safeRoleError(caught)}`);
    } finally { editInFlight.current = false; setModalPending(false); }
  };
  const approve = async (user) => {
    if (!canApprove(user) || user.protected || modal?.uncertain || unconfirmedUsers.current.get(user.user_id)?.save || editInFlight.current) return;
    const epoch = roleRequestId.current;
    editInFlight.current = true; setModalPending(true);
    try {
      const updated = await approveUser(user.user_id, { idempotencyKey: operationKey("admin-approve") });
      if (epoch !== roleRequestId.current) return;
      setUsers((items) => items.map((item) => item.user_id === updated.user_id ? updated : item));
      setModal((value) => value && { ...value, user: updated }); setForm((value) => ({ ...value, state: updated.state }));
    } catch (caught) { if (epoch === roleRequestId.current && !handleRoleAuthentication(caught)) setError("ADMIN_USER_APPROVAL_FAILED"); }
    finally { editInFlight.current = false; setModalPending(false); }
  };
  const deleteConfirmedUser = async (user) => {
    if (user.protected) return false;
    try {
      await removeUser(user.user_id, { idempotencyKey: operationKey("admin-delete") });
      setUsers((current) => current.filter((item) => item.user_id !== user.user_id));
      return true;
    } catch (caught) { if (!handleRoleAuthentication(caught)) setError(caught?.message || "ADMIN_USER_DELETE_FAILED"); return false; }
  };
  const withDeleteLock = async (action) => {
    if (deleteInFlight.current) return;
    deleteInFlight.current = true; setDeletePending(true);
    try { await action(); }
    finally { deleteInFlight.current = false; setDeletePending(false); }
  };
  const reset = async (user) => {
    if (user.protected || !user.has_email || modal?.uncertain || unconfirmedUsers.current.get(user.user_id)?.save
        || unconfirmedUsers.current.get(user.user_id)?.reset || editInFlight.current || form.email !== (user.email ?? "")
        || !window.confirm(`${user.login_id ?? user.user_id} 계정에 비밀번호 초기화 메일을 발송하시겠습니까?`)) return;
    const epoch = roleRequestId.current;
    const requestSession = uncertaintySession.current;
    const resetRequest = { inFlight: true };
    unconfirmedUsers.current.set(user.user_id, { ...unconfirmedUsers.current.get(user.user_id), reset: resetRequest });
    editInFlight.current = true; setModalPending(true);
    setNotice(null);
    try {
      await resetPassword(user.user_id, { idempotencyKey: operationKey("admin-password-reset") });
      if (epoch !== roleRequestId.current) return;
      const pending = unconfirmedUsers.current.get(user.user_id);
      if (pending?.reset === resetRequest) {
        if (pending.save) unconfirmedUsers.current.set(user.user_id, { save: pending.save });
        else unconfirmedUsers.current.delete(user.user_id);
      }
      setNotice(`${user.login_id ?? user.user_id} 계정의 비밀번호 초기화 메일을 발송했습니다.`);
      setError(null);
    } catch (caught) {
      if (epoch !== roleRequestId.current || handleRoleAuthentication(caught)) return;
      setModal((value) => value && { ...value, resetUncertain: true });
      setError("ADMIN_USER_PASSWORD_RESET_FAILED");
    }
    finally {
      if (uncertaintySession.current === requestSession && unconfirmedUsers.current.get(user.user_id)?.reset === resetRequest) resetRequest.inFlight = false;
      editInFlight.current = false; setModalPending(false);
    }
  };

  const toggleSelected = (userId, checked) => setSelected((current) => {
    if (!visibleUsers.some((user) => user.user_id === userId && !user.protected)) return current;
    const next = new Set(current); if (checked) next.add(userId); else next.delete(userId); return next;
  });
  const toggleAll = (checked) => setSelected(checked ? new Set(visibleUsers.filter((user) => !user.protected).map((user) => user.user_id)) : new Set());
  const removeSelected = async () => {
    const targets = visibleUsers.filter((user) => selected.has(user.user_id) && !user.protected);
    if (modal || editInFlight.current || deleteInFlight.current || !targets.length || !window.confirm(`${targets.length}명의 계정을 삭제하시겠습니까?`)) return;
    const epoch = roleRequestId.current;
    await withDeleteLock(async () => {
      for (const user of targets) {
        if (!(await deleteConfirmedUser(user))) break;
        if (epoch !== roleRequestId.current) break;
        setSelected((current) => { const next = new Set(current); next.delete(user.user_id); return next; });
      }
    });
  };
  const openEdit = (user, opener) => {
    if (editInFlight.current || deleteInFlight.current) return;
    modalOpener.current = opener;
    setForm({ login_id: user.login_id ?? "", email: user.email ?? "", initial_password: "", state: user.state });
    const unconfirmed = unconfirmedUsers.current.get(user.user_id);
    setModal({ kind: "edit", user, completed: unconfirmed?.save?.completed ?? [], uncertain: Boolean(unconfirmed?.save), resetUncertain: Boolean(unconfirmed?.reset) }); setError(null); setNotice(null);
    if (user.protected === true) {
      roleRequestId.current += 1; setRolePanel(null); setRoleSummary(null);
    } else void openRole(user);
  };
  const openCreate = (event) => {
    if (editInFlight.current || deleteInFlight.current) return;
    modalOpener.current = event.currentTarget;
    roleRequestId.current += 1; setRolePanel(null); setError(null); setNotice(null);
    setForm({ login_id: "", email: "", initial_password: "", state: "active" });
    setModal({ kind: "create" });
  };

  const openRole = async (user) => {
    if (roleInFlight.current) return;
    const requestId = ++roleRequestId.current;
    setRoleSummary(null);
    setRolePanel({ user, tenants: [], tenantId: "", membership: null, membershipVerified: false, workspaceId: "", role: "",
      reason: "", latest: null, error: null, notice: null, loading: true, saving: false });
    try {
      const tenants = await getTenants();
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, tenants, loading: false });
    } catch (caught) {
      if (roleRequestId.current !== requestId || handleRoleAuthentication(caught)) return;
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, error: safeRoleError(caught), loading: false });
    }
  };
  const selectTenant = async (tenantId) => {
    if (editInFlight.current || roleInFlight.current) return;
    const requestId = ++roleRequestId.current;
    setRoleSummary(null);
    const userId = rolePanel?.user.user_id;
    setRolePanel((current) => current && { ...current, tenantId, membership: null, membershipVerified: false, workspaceId: "",
      role: "", reason: "", latest: null, error: null, notice: null, loading: Boolean(tenantId) });
    if (!tenantId || !userId) return;
    try {
      const membership = await getMemberships(tenantId, userId);
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, membership, membershipVerified: true, loading: false,
      });
    } catch (caught) {
      if (roleRequestId.current !== requestId || handleRoleAuthentication(caught)) return;
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, error: safeRoleError(caught), loading: false,
      });
    }
  };
  const rememberRole = (panel, workspace) => setRoleSummary({ userId: panel.user.user_id, tenantId: panel.tenantId,
    tenantName: panel.tenants.find((tenant) => tenant.tenant_id === panel.tenantId)?.name ?? panel.tenantId,
    workspaceId: workspace.workspace_id, role: workspace.role, version: workspace.version });
  const selectWorkspace = (workspaceId) => {
    if (!rolePanel || editInFlight.current || roleInFlight.current) return;
    const workspace = rolePanel.membership?.workspaces.find((item) => item.workspace_id === workspaceId);
    setRoleSummary(null);
    if (workspace && rolePanel.membershipVerified && !rolePanel.error) rememberRole(rolePanel, workspace);
    setRolePanel((current) => current && { ...current, workspaceId: workspace ? workspaceId : "", role: workspace?.role ?? "",
      reason: "", latest: null, error: null, notice: null });
  };
  const saveRole = async () => {
    const panel = rolePanel;
    const currentRole = panel?.membership?.workspaces.find((item) => item.workspace_id === panel.workspaceId);
    if (!panel?.tenantId || !currentRole || !panel.role || !panel.reason || roleInFlight.current
        || panel.user.protected || panel.user.user_id === sessionUserId) return;
    const requestId = roleRequestId.current;
    roleInFlight.current = true;
    setRoleSummary(null);
    setRolePanel((current) => current && { ...current, saving: true, membershipVerified: false, error: null, notice: null });
    try {
      const result = await setMembershipRole(panel.tenantId, panel.workspaceId, panel.user.user_id, {
        role: panel.role, expected_version: currentRole.version, reason: panel.reason,
      }, { idempotencyKey: createAdminUserIdempotencyKey("admin-role") });
      const membership = await getMemberships(panel.tenantId, panel.user.user_id);
      const confirmed = membership.workspaces.find((item) => item.workspace_id === panel.workspaceId);
      if (!confirmed || confirmed.role !== panel.role || confirmed.version < result.version) throw new Error("ADMIN_MEMBERSHIP_RESPONSE_INVALID");
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, membership, membershipVerified: true, latest: null, reason: "", notice: "역할 변경 완료", error: null,
      });
      if (roleRequestId.current === requestId) rememberRole(panel, confirmed);
      return true;
    } catch (caught) {
      if (roleRequestId.current !== requestId || handleRoleAuthentication(caught)) return false;
      let latest = null;
      try { latest = await getMemberships(panel.tenantId, panel.user.user_id); }
      catch (refreshError) {
        if (handleRoleAuthentication(refreshError)) return;
        /* Keep the entered choice if refresh fails. */
      }
      if (roleRequestId.current === requestId) setRolePanel((current) => current && {
        ...current, latest, error: safeRoleError(caught), notice: null,
      });
      return false;
    } finally {
      roleInFlight.current = false;
      if (roleRequestId.current === requestId) setRolePanel((current) => current && { ...current, saving: false });
    }
  };
  const selectedRoleView = rolePanel?.membership?.workspaces.find((item) => item.workspace_id === rolePanel.workspaceId);
  const latestRoleView = rolePanel?.latest?.workspaces.find((item) => item.workspace_id === rolePanel.workspaceId);

  return <main className="admin-user-page">
    <header className="admin-user-header" inert={Boolean(modal)}><div><p>계정 및 권한 설정</p><h1>사용자·계정 관리</h1><small>일반 사용자를 등록하고 승인·수정·중지·삭제합니다.</small></div><div className="admin-user-header-actions"><button type="button" onClick={openCreate}>사용자 등록</button><a href="/notebooks">Notebook으로</a></div></header>
    {!sessionValidated && <section className="admin-user-state" role="status" aria-busy="true">사용자 목록을 불러오는 중입니다.</section>}
    <div ref={protectedRoot} hidden={!sessionValidated} inert={!sessionValidated}
      aria-hidden={!sessionValidated ? "true" : undefined} data-session-validated={sessionValidated ? "true" : "false"}>
    {error && <section className="admin-user-state admin-user-error" role="alert"><span>{error}</span><button type="button" onClick={() => void load()}>다시 시도</button></section>}
    {notice && <section className="admin-user-state admin-user-notice" role="status">{notice}</section>}
    {state === "ready" && <section className="admin-user-card" inert={Boolean(modal)} aria-hidden={modal ? "true" : undefined} aria-labelledby="admin-user-list-title">
      <div className="admin-user-toolbar"><div className="admin-user-tools"><label>사용자 ID 또는 이메일 검색<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="로그인 ID, 사용자 ID 또는 이메일" /></label><label>전체 상태<select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">전체 상태</option><option value="active">활성</option><option value="suspended">중지</option><option value="pending_email">이메일 인증 대기</option><option value="pending_approval">승인 대기</option></select></label><button type="button" onClick={() => setSearch(search.trim())}>조회</button></div><div className="admin-user-bulk-actions"><span aria-live="polite">선택 {deletionTargets.length}건</span><button type="button" disabled={!deletionTargets.length || deletePending || Boolean(modal)} onClick={() => void removeSelected()}>선택 삭제</button><button type="button" onClick={() => void load()}>새로고침</button></div></div>
      <p className="admin-user-result-count">{visibleUsers.length}명의 계정을 조회했습니다.</p>
      {visibleUsers.length === 0 ? <p className="admin-user-empty">조건에 맞는 사용자가 없습니다.</p> : <div className="admin-user-table-wrap"><table className="admin-user-table"><thead><tr><th scope="col"><input type="checkbox" aria-label="전체 사용자 선택" checked={visibleUsers.some((user) => !user.protected) && visibleUsers.filter((user) => !user.protected).every((user) => selected.has(user.user_id))} onChange={(event) => toggleAll(event.target.checked)} /></th><th scope="col">사용자</th><th scope="col">이메일</th><th scope="col">상태</th><th scope="col">역할</th></tr></thead><tbody>{visibleUsers.map((user) => <tr key={user.user_id} tabIndex={0} aria-label={`${displayNameFor(user)} 사용자 수정`}
        onDoubleClick={(event) => { if (!["INPUT", "BUTTON", "SELECT", "LABEL", "A"].includes(event.target.tagName)) openEdit(user, event.currentTarget); }}
        onKeyDown={(event) => { if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) { event.preventDefault(); openEdit(user, event.currentTarget); } }}>
        <td><input type="checkbox" aria-label={`${displayNameFor(user)} 선택`} disabled={user.protected} checked={selected.has(user.user_id)} onChange={(event) => toggleSelected(user.user_id, event.target.checked)} /></td>
        <td><strong>{displayNameFor(user)}</strong>{user.protected && <small>보호된 시스템 관리자</small>}</td>
        <td>{user.email ?? "이메일 없음"}</td><td><span className={`admin-user-status status-${user.state}`}>{stateLabel[user.state] ?? user.state}</span></td>
        <td><div className="admin-user-role-list">{user.protected === true ? <span>시스템 관리자</span> : roleSummary?.userId === user.user_id
          ? <span>마지막 조회 · 조직 {roleSummary.tenantName} ({roleSummary.tenantId}) · Workspace {roleSummary.workspaceId} · {workspaceRoleLabels[roleSummary.role]} · version {roleSummary.version}</span>
          : <span>{rolePanel?.user.user_id === user.user_id
            ? modal?.uncertain ? "저장 결과 미확정 · 역할 재확인 필요"
              : rolePanel.loading ? "선택 범위 역할 조회 중…"
                : rolePanel.error ? "선택 범위 역할 조회 실패"
                  : rolePanel.membershipVerified ? rolePanel.membership.workspaces.length === 0
                    ? "선택한 조직 범위에 Workspace 역할 없음" : "Workspace 선택 필요" : MEMBERSHIP_ROLE_UNAVAILABLE
            : MEMBERSHIP_ROLE_UNAVAILABLE}</span>}</div></td>
      </tr>)}</tbody></table></div>}
    </section>}
    {modal && <div className="admin-user-modal-backdrop admin-user-role-backdrop" role="presentation">
      <form className="admin-user-modal admin-user-role-modal" role="dialog" tabIndex={-1} aria-modal="true" aria-busy={modalPending} aria-labelledby="admin-user-modal-title" onKeyDown={modalKeys} onSubmit={saveUser}>
        <h2 id="admin-user-modal-title">{modal.kind === "create" ? "사용자 등록" : "사용자 수정"}</h2>
        {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
        {modalPending && <p role="status">처리 중…</p>}
        {modal.uncertain && <p role="alert">저장 결과 미확정 — 실제 서버 상태를 다시 조회하기 전에는 수정·승인·초기화를 요청할 수 없습니다.</p>}
        {modal.resetUncertain && <p role="alert">초기화 메일 송신 결과 미확정 — 중복 발송을 막기 위해 이 세션에서는 새 초기화 요청을 차단합니다. 사용자 목록 조회로 메일 송신 여부를 확인할 수는 없습니다.</p>}
        {modal.user?.protected && <p>보호된 시스템 관리자 — 이 폼은 읽기 전용입니다. 시스템 관리자 권한은 조직·Workspace 멤버십 역할과 별도입니다.</p>}
        {modal.completed?.length > 0 && <p role="status">완료 항목: {modal.completed.join(", ")}</p>}
        {modal.kind === "edit" && <p>사용자 ID: {form.login_id} · 저장된 이메일: {modal.user.email ?? "없음"}</p>}
        {modal.kind === "edit" && !modal.user.protected && <p>변경 항목은 이메일 → 역할 → 상태 순으로 각각 저장하며, 일부 항목만 저장될 수 있습니다.</p>}
        {modal.kind === "create" && <label>사용자 ID<input required disabled={modalPending} value={form.login_id} onChange={(event) => setForm({ ...form, login_id: event.target.value })} /></label>}
        <label>이메일<input required={modal.kind === "create" || form.email !== (modal.user.email ?? "")} type="email" disabled={modalPending || Boolean(modal.user?.protected)} value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>
        {modal.kind === "edit" && <label>상태<select aria-label="계정 상태" disabled={modalPending || !canChangeState(modal.user) || modal.user.user_id === sessionUserId} value={form.state} onChange={(event) => setForm({ ...form, state: event.target.value })}>
          {Object.entries(stateLabel).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select></label>}
        {modal.kind === "create" && <label>초기 비밀번호<input required type="password" minLength={12} disabled={modalPending} value={form.initial_password} onChange={(event) => setForm({ ...form, initial_password: event.target.value })} /><span>12자 이상으로 입력하세요.</span></label>}
        {modal.kind === "edit" && <>
          <button type="button" disabled={modalPending || modal.uncertain || modal.resetUncertain || modal.user.protected || !modal.user.has_email || form.email !== (modal.user.email ?? "")} onClick={() => void reset(modal.user)}>비밀번호 초기화</button>
          {form.email !== (modal.user.email ?? "") && <p>이메일 변경은 먼저 수정으로 저장하세요. 초기화 메일은 저장된 이메일로만 발송됩니다.</p>}
          {canApprove(modal.user) && !modal.user.protected && <button type="button" disabled={modalPending || modal.uncertain} onClick={() => void approve(modal.user)}>승인</button>}
          {rolePanel && <section aria-label="유효 멤버십 역할">
      <p>시스템 관리자 여부 (별도 권한): {rolePanel.tenantId && rolePanel.membership && rolePanel.membershipVerified && !rolePanel.error
        && typeof rolePanel.membership.target_is_system_admin === "boolean"
        ? (rolePanel.membership.target_is_system_admin ? "예" : "아니요") : "미조회"}</p>
      {rolePanel.error && <p role="alert">{rolePanel.error}</p>}
      {rolePanel.notice && <p role="status">{rolePanel.notice}</p>}
      <label>조직 선택<select aria-label="조직 선택" value={rolePanel.tenantId}
        onChange={(event) => void selectTenant(event.target.value)} disabled={modalPending || rolePanel.saving}>
        <option value="">조직을 선택하세요</option>
        {rolePanel.tenants.map((tenant) => <option key={tenant.tenant_id} value={tenant.tenant_id}>{tenant.name}</option>)}
      </select></label>
      {rolePanel.loading && <p role="status">유효 역할 조회 중…</p>}
      {!rolePanel.loading && !rolePanel.membership && <p>{rolePanel.error ? "선택 범위의 역할 조회 실패" : "조직을 선택하세요"}</p>}
      {rolePanel.membership && <>
        <p>조직 역할 (읽기 전용): {rolePanel.membership.tenant
          ? `${rolePanel.membership.tenant.role} · ${rolePanel.membership.tenant.state} · version ${rolePanel.membership.tenant.version}`
          : rolePanel.membershipVerified ? "조직 역할 없음" : "조직 역할 재확인 필요"}</p>
        <label>작업공간 선택<select aria-label="작업공간 선택" value={rolePanel.workspaceId}
          onChange={(event) => selectWorkspace(event.target.value)} disabled={modalPending || rolePanel.saving}>
          <option value="">작업공간을 선택하세요</option>
          {rolePanel.membership.workspaces.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.workspace_id}</option>)}
        </select></label>
        {selectedRoleView ? <>
          <p>{rolePanel.membershipVerified && !rolePanel.error ? "현재 역할" : "이전 조회 역할 (현재값 재확인 필요)"}: {selectedRoleView.role} ({workspaceRoleLabels[selectedRoleView.role]}) · {selectedRoleView.state} · version {selectedRoleView.version}</p>
          <label>변경할 역할<select aria-label="변경할 역할" value={rolePanel.role}
            disabled={modalPending || rolePanel.user.protected || rolePanel.user.user_id === sessionUserId} onChange={(event) => setRolePanel((current) => ({ ...current, role: event.target.value, notice: null }))}>
            {Object.entries(workspaceRoleLabels).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select></label>
          <label>변경 사유<select aria-label="변경 사유" value={rolePanel.reason}
            disabled={modalPending || rolePanel.user.protected || rolePanel.user.user_id === sessionUserId} onChange={(event) => setRolePanel((current) => ({ ...current, reason: event.target.value, notice: null }))}>
            <option value="">사유를 선택하세요</option>
            {ADMIN_MEMBERSHIP_REASONS.map(({ code, label }) => <option key={code} value={code}>{label}</option>)}
          </select></label>
          {latestRoleView && <p>최신 역할: {latestRoleView.role} · {latestRoleView.state} · version {latestRoleView.version}</p>}

        </> : <p>{rolePanel.membershipVerified && !rolePanel.error && rolePanel.membership.workspaces.length === 0
          ? "선택한 조직 범위에 Workspace 역할 없음" : "Workspace를 선택하세요"}</p>}
      </>}

          </section>}
        </>}
        {modal.uncertain && <button type="button" disabled={modalPending} onClick={() => void recheckEdit()}>서버 상태 다시 조회</button>}
        <div><button ref={modalCancel} type="button" disabled={modalPending} onClick={closeModal}>취소</button>
          <button type="submit" disabled={modalPending || modal.uncertain || Boolean(modal.user?.protected)}>{modal.kind === "create" ? "등록" : "수정"}</button></div>
      </form>
    </div>}
    </div>
  </main>;
}
