import { useRef, useState } from "react";
import { logoutNativeSession, submitNativeLogin } from "./native-session-bridge.js";

export function NativeAuthPanel({ sessionBridge, sessionStatus, onSessionChange }) {
  const loginIdInput = useRef(null);
  const passwordInput = useRef(null);
  const [operationState, setOperationState] = useState("idle");

  const login = async (event) => {
    event.preventDefault();
    setOperationState("working");
    try {
      await submitNativeLogin({ sessionBridge, loginId: loginIdInput.current?.value ?? "", passwordInput: passwordInput.current, onSessionChange });
      setOperationState("ready");
    } catch {
      setOperationState("AUTHENTICATION_REQUIRED");
    }
  };

  const logout = async () => {
    setOperationState("working");
    try {
      await logoutNativeSession({ sessionBridge, onSessionChange });
      setOperationState("idle");
    } catch {
      setOperationState("AUTHENTICATION_REQUIRED");
    }
  };

  if (sessionStatus.authenticated) {
    return <section aria-label="Windows Native 인증" className="desktop-native-auth">
      <span role="status">인증됨 · {sessionStatus.userId} · {sessionStatus.workspaceId}</span>
      <button type="button" onClick={logout}>로그아웃</button>
      {operationState === "AUTHENTICATION_REQUIRED" ? <span>AUTHENTICATION_REQUIRED</span> : null}
    </section>;
  }

  return <form aria-label="Windows Native 로그인" className="desktop-native-auth" onSubmit={login}>
    <label>Login ID<input ref={loginIdInput} name="login-id" autoComplete="username" defaultValue="" /></label>
    <label>Password<input ref={passwordInput} name="password" type="password" autoComplete="current-password" defaultValue="" /></label>
    <button type="submit" disabled={operationState === "working"}>로그인</button>
    <span role="status">{operationState === "AUTHENTICATION_REQUIRED" ? "AUTHENTICATION_REQUIRED" : "로그인이 필요합니다."}</span>
  </form>;
}
