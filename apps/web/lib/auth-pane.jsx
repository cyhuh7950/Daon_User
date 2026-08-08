"use client";

import { useState } from "react";
import { authApi } from "./auth-api.js";

const initial = { login_id: "", email: "", password: "", token: "" };

export function AuthPane() {
  const [form, setForm] = useState(initial);
  const [message, setMessage] = useState("");
  const update = (field) => (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  const run = async (path, payload, success) => {
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
      if (path === "login" && result?.data?.workspace_id) {
        window.location.assign(`/workspaces/${encodeURIComponent(result.data.workspace_id)}`);
      }
    }
    catch (error) { setMessage(`처리 실패: ${error.message}`); }
  };
  return (
    <section className="daon-auth-pane" aria-labelledby="daon-auth-title">
      <div className="daon-auth-heading"><h2 id="daon-auth-title">사용자 가입·로그인</h2><span title="가입 후 이메일 인증을 완료해야 로그인할 수 있습니다." aria-label="도움말">ⓘ</span></div>
      <div className="daon-auth-grid">
        <label>사용자 ID<input value={form.login_id} onChange={update("login_id")} autoComplete="username" /></label>
        <label>메일 주소<input type="email" value={form.email} onChange={update("email")} autoComplete="email" /></label>
        <label>비밀번호<input type="password" minLength={12} value={form.password} onChange={update("password")} autoComplete="new-password" /></label>
        <label>인증·재설정 토큰<input value={form.token} onChange={update("token")} /></label>
      </div>
      <div className="daon-auth-actions">
        <button type="button" onClick={() => run("signup", { login_id: form.login_id, email: form.email, password: form.password }, "인증 메일을 요청했습니다.")}>가입</button>
        <button type="button" onClick={() => run("login", { login_id: form.login_id, password: form.password }, "로그인했습니다.")}>로그인</button>
        <button type="button" onClick={() => run("verify-email", { token: form.token }, "이메일 인증이 완료되었습니다.")}>이메일 인증</button>
        <button type="button" onClick={() => run("resend-verification", { identifier: form.login_id || form.email }, "인증 메일을 재요청했습니다.")}>인증 재전송</button>
        <button type="button" onClick={() => run("password-reset/request", { identifier: form.login_id || form.email }, "비밀번호 재설정 메일을 요청했습니다.")}>재설정 메일</button>
        <button type="button" onClick={() => run("password-reset/confirm", { token: form.token, new_password: form.password }, "비밀번호가 변경되었습니다.")}>비밀번호 변경</button>
      </div>
      {message && <p className="daon-auth-status" role="status">{message}</p>}
    </section>
  );
}
