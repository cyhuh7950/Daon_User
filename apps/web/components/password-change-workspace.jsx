"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { changeCurrentPassword } from "../lib/auth-api.js";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";

const SAFE_ERRORS = new Set(["AUTHENTICATION_REQUIRED", "PASSWORD_POLICY_FAILED", "PASSWORD_CHANGE_FAILED", "PASSWORD_CHANGE_RESPONSE_INVALID"]);
const replaceLocation = (path) => window.location.replace(path);

export function PasswordChangeWorkspace({
  getSession = getCurrentNotebookSession,
  changePassword = changeCurrentPassword,
  navigate = replaceLocation,
}) {
  const pendingRef = useRef(false);
  const [validated, setValidated] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  const validateSession = useCallback(async (signal) => {
    setValidated(false); setError(null);
    try {
      const session = await getSession({ signal });
      if (!session.password_change_required) { navigate("/notebooks"); return; }
      if (!signal?.aborted) setValidated(true);
    } catch (caught) {
      if (signal?.aborted) return;
      if (caught?.message === "AUTHENTICATION_REQUIRED") { navigate("/"); return; }
      setError("SESSION_UNAVAILABLE"); setValidated(true);
    }
  }, [getSession, navigate]);

  useEffect(() => { const controller = new AbortController(); void validateSession(controller.signal); return () => controller.abort(); }, [validateSession]);
  useEffect(() => {
    let controller = null;
    const revalidate = () => { controller?.abort(); controller = new AbortController(); void validateSession(controller.signal); };
    const onPageShow = (event) => { if (event.persisted) revalidate(); };
    const onPageHide = () => setValidated(false);
    window.addEventListener("pageshow", onPageShow); window.addEventListener("pagehide", onPageHide); window.addEventListener("popstate", revalidate);
    return () => { controller?.abort(); window.removeEventListener("pageshow", onPageShow); window.removeEventListener("pagehide", onPageHide); window.removeEventListener("popstate", revalidate); };
  }, [validateSession]);

  const submit = async (event) => {
    event.preventDefault();
    if (pendingRef.current || newPassword.length < 12 || newPassword.length > 256 || newPassword !== confirmation) return;
    pendingRef.current = true; setPending(true); setError(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword(""); setNewPassword(""); setConfirmation(""); navigate("/");
    } catch (caught) {
      setError(SAFE_ERRORS.has(caught?.message) ? caught.message : "PASSWORD_CHANGE_FAILED");
    } finally { pendingRef.current = false; setPending(false); }
  };

  if (!validated) return <main className="password-change-page" role="status" aria-busy="true">인증 상태를 확인하는 중입니다.</main>;
  return <main className="password-change-page"><section className="password-change-card" aria-labelledby="password-change-title">
    <p>SECURITY REQUIRED</p><h1 id="password-change-title">새 비밀번호 설정</h1><small>다른 기능을 사용하기 전에 비밀번호를 변경해야 합니다.</small>
    <form onSubmit={submit}>
      <label>현재 비밀번호<input autoFocus type="password" autoComplete="current-password" maxLength={256} required disabled={pending} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
      <label>새 비밀번호<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={pending} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /><span>12자 이상으로 입력하세요.</span></label>
      <label>새 비밀번호 확인<input type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={pending} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
      {confirmation && confirmation !== newPassword && <p className="password-change-error" role="alert">새 비밀번호가 일치하지 않습니다.</p>}
      {error && <p className="password-change-error" role="alert">{error}</p>}
      <button type="submit" disabled={pending || newPassword.length < 12 || newPassword !== confirmation}>{pending ? "변경 중…" : "비밀번호 변경"}</button>
    </form>
  </section></main>;
}
