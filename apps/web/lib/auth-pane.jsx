"use client";

import { useEffect, useRef, useState } from "react";
import { authApi } from "./auth-api.js";

const initial = { login_id: "", email: "", password: "", token: "" };

export function AuthPane() {
  const [screen, setScreen] = useState("login");
  const [signupStep, setSignupStep] = useState("signup");
  const [resetStep, setResetStep] = useState("request");
  const [form, setForm] = useState(initial);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const firstInputRef = useRef(null);

  useEffect(() => { firstInputRef.current?.focus(); }, [screen, signupStep, resetStep]);

  const update = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  const clearSensitive = () => setForm((current) => ({ ...current, password: "", token: "" }));
  const changeScreen = (nextScreen) => {
    if (busy) return;
    clearSensitive();
    setMessage("");
    if (nextScreen === "signup") setSignupStep("signup");
    if (nextScreen === "password-reset") setResetStep("request");
    setScreen(nextScreen);
  };
  const run = async (path, payload, success, afterSuccess) => {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const operations = {
        signup: () => authApi.signup(payload),
        login: () => authApi.login(payload),
        "verify-email": () => authApi.verifyEmail(payload.token),
        "resend-verification": () => authApi.resendVerification(payload.identifier),
        "password-reset/request": () => authApi.requestPasswordReset(payload.identifier),
        "password-reset/confirm": () => authApi.confirmPasswordReset(payload.token, payload.new_password),
      };
      const result = await operations[path]();
      setMessage(success);
      afterSuccess?.();
      if (path === "login" && result?.data?.workspace_id) {
        window.location.assign(`/workspaces/${encodeURIComponent(result.data.workspace_id)}`);
      } else if (path === "login") {
        setMessage("WORKSPACE_REQUIRED");
      }
    }
    catch { setMessage("처리 실패: 요청을 완료하지 못했습니다."); }
    finally {
      clearSensitive();
      setBusy(false);
    }
  };

  const title = screen === "login" ? "로그인" : screen === "signup" ? "가입" : "비밀번호 재설정";

  return (
    <section className="daon-auth-pane" aria-labelledby="daon-auth-title">
      <div className="daon-auth-heading"><h2 id="daon-auth-title">{title}</h2><span title="가입 후 이메일 인증을 완료해야 로그인할 수 있습니다." aria-label="도움말">ⓘ</span></div>

      {screen === "login" && (
        <form onSubmit={(event) => { event.preventDefault(); run("login", { login_id: form.login_id, password: form.password }, "로그인했습니다."); }}>
          <div className="daon-auth-grid">
            <label>사용자 ID<input ref={firstInputRef} name="login-id" value={form.login_id} onInput={update("login_id")} autoComplete="username" /></label>
            <label>비밀번호<input name="password" type="password" minLength={12} value={form.password} onInput={update("password")} autoComplete="current-password" /></label>
          </div>
          <div className="daon-auth-actions">
            <button type="button" disabled={busy} onClick={() => run("login", { login_id: form.login_id, password: form.password }, "로그인했습니다.")}>로그인</button>
            <button className="daon-auth-link" type="button" disabled={busy} onClick={() => changeScreen("signup")}>가입하기</button>
            <button className="daon-auth-link" type="button" disabled={busy} onClick={() => changeScreen("password-reset")}>비밀번호를 잊으셨나요?</button>
          </div>
        </form>
      )}

      {screen === "signup" && signupStep === "signup" && (
        <form onSubmit={(event) => { event.preventDefault(); run("signup", { login_id: form.login_id, email: form.email, password: form.password }, "인증 메일을 요청했습니다.", () => setSignupStep("verify")); }}>
          <div className="daon-auth-grid">
            <label>사용자 ID<input ref={firstInputRef} name="signup-login-id" value={form.login_id} onInput={update("login_id")} autoComplete="username" /></label>
            <label>메일 주소<input name="email" type="email" value={form.email} onInput={update("email")} autoComplete="email" /></label>
            <label>비밀번호<input name="signup-password" type="password" minLength={12} value={form.password} onInput={update("password")} autoComplete="new-password" /></label>
          </div>
          <div className="daon-auth-actions">
            <button type="button" disabled={busy} onClick={() => run("signup", { login_id: form.login_id, email: form.email, password: form.password }, "인증 메일을 요청했습니다.", () => setSignupStep("verify"))}>가입</button>
            <button className="daon-auth-link" type="button" disabled={busy} onClick={() => changeScreen("login")}>로그인으로 돌아가기</button>
          </div>
        </form>
      )}

      {screen === "signup" && signupStep === "verify" && (
        <form onSubmit={(event) => { event.preventDefault(); run("verify-email", { token: form.token }, "이메일 인증이 완료되었습니다."); }}>
          <div className="daon-auth-grid">
            <label>이메일 인증 토큰<input ref={firstInputRef} name="verification-token" value={form.token} onInput={update("token")} autoComplete="off" /></label>
          </div>
          <div className="daon-auth-actions">
            <button type="button" disabled={busy} onClick={() => run("verify-email", { token: form.token }, "이메일 인증이 완료되었습니다.")}>이메일 인증</button>
            <button type="button" disabled={busy} onClick={() => run("resend-verification", { identifier: form.login_id || form.email }, "인증 메일을 재요청했습니다.")}>인증 재전송</button>
            <button className="daon-auth-link" type="button" disabled={busy} onClick={() => changeScreen("login")}>로그인으로 돌아가기</button>
          </div>
        </form>
      )}

      {screen === "password-reset" && resetStep === "request" && (
        <form onSubmit={(event) => { event.preventDefault(); run("password-reset/request", { identifier: form.login_id || form.email }, "비밀번호 재설정 메일을 요청했습니다.", () => setResetStep("confirm")); }}>
          <div className="daon-auth-grid">
            <label>사용자 ID 또는 메일 주소<input ref={firstInputRef} name="reset-identifier" value={form.login_id || form.email} onInput={(event) => setForm((current) => ({ ...current, login_id: event.target.value, email: "" }))} autoComplete="username" /></label>
          </div>
          <div className="daon-auth-actions">
            <button type="button" disabled={busy} onClick={() => run("password-reset/request", { identifier: form.login_id || form.email }, "비밀번호 재설정 메일을 요청했습니다.", () => setResetStep("confirm"))}>재설정 메일 요청</button>
            <button className="daon-auth-link" type="button" disabled={busy} onClick={() => changeScreen("login")}>로그인으로 돌아가기</button>
          </div>
        </form>
      )}

      {screen === "password-reset" && resetStep === "confirm" && (
        <form onSubmit={(event) => { event.preventDefault(); run("password-reset/confirm", { token: form.token, new_password: form.password }, "비밀번호가 재설정되었습니다."); }}>
          <div className="daon-auth-grid">
            <label>재설정 토큰<input ref={firstInputRef} name="reset-token" value={form.token} onInput={update("token")} autoComplete="off" /></label>
            <label>새 비밀번호<input name="reset-password" type="password" minLength={12} value={form.password} onInput={update("password")} autoComplete="new-password" /></label>
          </div>
          <div className="daon-auth-actions">
            <button type="button" disabled={busy} onClick={() => run("password-reset/confirm", { token: form.token, new_password: form.password }, "비밀번호가 재설정되었습니다.")}>비밀번호 재설정</button>
            <button className="daon-auth-link" type="button" disabled={busy} onClick={() => changeScreen("login")}>로그인으로 돌아가기</button>
          </div>
        </form>
      )}
      {message && <p className="daon-auth-status" role="status">{message}</p>}
    </section>
  );
}
