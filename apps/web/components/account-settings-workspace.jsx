"use client";

import { useEffect, useRef, useState } from "react";
import { changeCurrentPassword, logoutCurrentSession } from "../lib/auth-api.js";
import { getCurrentNotebookSession } from "../lib/notebook-api.js";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";
import { getAccountProfile, saveAccountProfile, listAccountDevices, listAccountSessions,
  requestAccountStepUp, trustAccountDevice, revokeAccountDevice, revokeAccountSession } from "../lib/account-settings-api.js";

const replaceLocation = (path) => window.location.replace(path);
const sameSession = (left, right) => ["user_id", "tenant_id", "workspace_id", "session_id", "device_id"]
  .every((key) => left?.[key] === right?.[key]);
const requestKey = () => {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") return `account-${webCrypto.randomUUID()}`;
  if (typeof webCrypto?.getRandomValues !== "function") throw new Error("ACCOUNT_CRYPTO_UNAVAILABLE");
  const bytes = webCrypto.getRandomValues(new Uint8Array(16));
  return `account-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
};
const loadSettings = async (_session, options = {}) => {
  const [profile, devices, sessions] = await Promise.all([getAccountProfile(options),
    listAccountDevices(options.deviceCursor ? { cursor: options.deviceCursor } : {}, options),
    listAccountSessions(options.sessionCursor ? { cursor: options.sessionCursor } : {}, options)]);
  const session = await getCurrentNotebookSession(options);
  return { session, profile, devices, sessions };
};

function AccountProfileDevices({ session, getSettings, saveProfile, stepUp, trustDevice, revokeDevice, revokeSession,
  runOperation, validateSession, isActive, passwordInputs, additionalClear, pending }) {
  const [bundle, setBundle] = useState(null);
  const [name, setName] = useState("");
  const [loadState, setLoadState] = useState("loading");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState(null);
  const [password, setPassword] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const read = useRef(null);
  const profileRequest = useRef(null);
  const sensitiveRequest = useRef(null);
  const trustRequest = useRef(null);
  const nameInput = useRef(null);
  const mounted = useRef(true);
  const load = async (options = {}) => {
    read.current?.abort();
    const controller = new AbortController(); read.current = controller;
    setLoadState("loading");
    try {
      const value = await getSettings(session, { ...options, signal: controller.signal });
      if (!mounted.current || !isActive() || controller.signal.aborted || read.current !== controller) return;
      if (!validateSession(value.session)) return;
      setBundle((old) => ({ ...value,
        devices: options.deviceCursor && old ? { ...value.devices, items: [...old.devices.items, ...value.devices.items.filter((item) => !old.devices.items.some((prior) => prior.device_id === item.device_id))] } : value.devices,
        sessions: options.sessionCursor && old ? { ...value.sessions, items: [...old.sessions.items, ...value.sessions.items.filter((item) => !old.sessions.items.some((prior) => prior.session_id === item.session_id))] } : value.sessions,
      }));
      setName(value.profile.display_name ?? ""); setLoadState("ready");
    } catch (caught) {
      if (!mounted.current || !isActive() || controller.signal.aborted) return;
      if (caught?.message === "AUTHENTICATION_REQUIRED") validateSession(null);
      else { setBundle(null); setName(""); setLoadState("failed"); }
    }
  };
  useEffect(() => {
    mounted.current = true; additionalClear.current = () => setPassword(""); void load();
    return () => { mounted.current = false; additionalClear.current = null; read.current?.abort(); if (nameInput.current) nameInput.current.value = ""; };
  }, [session, getSettings]);
  const clearReauthentication = () => { if (passwordInputs.current[3]) passwordInputs.current[3].value = ""; setPassword(""); };
  const failed = (caught) => {
    if (caught?.message === "ACCOUNT_VERSION_CONFLICT") {
      profileRequest.current = null; setUncertain(false); setNotice("다른 화면에서 프로필이 변경되었습니다. 다시 조회한 이름을 확인하고 저장하세요."); void load();
    } else {
      const unknown = ["ACCOUNT_RESULT_UNKNOWN", "ACCOUNT_RESPONSE_INVALID"].includes(caught?.message);
      setUncertain(unknown);
      setNotice(unknown ? "처리 결과를 확인하지 못했습니다. 자동 재제출하지 않습니다. 같은 요청 확인 또는 다시 조회를 선택하세요."
        : caught?.message === "ACCOUNT_FORBIDDEN" ? "추가 인증 또는 작업 권한을 확인하세요. 비밀번호를 다시 입력하세요." : "계정 작업을 완료하지 못했습니다. 입력과 현재 상태를 확인하세요.");
    }
  };
  const prepareRequest = (create) => {
    try { return create(); }
    catch {
      setNotice("보안 난수 생성 기능을 사용할 수 없어 요청을 보내지 않았습니다. 브라우저 보안 환경을 확인하세요.");
      return null;
    }
  };
  const submitProfile = (event) => {
    event.preventDefault();
    const normalized = name.trim();
    if (!bundle || [...normalized].length < 1 || [...normalized].length > 80 || /\p{Cc}/u.test(normalized)) return;
    const payload = Object.freeze({ display_name: normalized, expected_version: bundle.profile.version });
    const prior = profileRequest.current;
    if (!prior || JSON.stringify(prior.payload) !== JSON.stringify(payload)) profileRequest.current = prepareRequest(() => Object.freeze({ payload, key: requestKey(), expectedSessionId: session.session_id }));
    const active = profileRequest.current;
    if (!active) return;
    void runOperation("profile", (signal) => saveProfile(active.payload, { signal, idempotencyKey: active.key, expectedSessionId: active.expectedSessionId }), {
      success: async (result) => {
        if (result?.display_name !== normalized || !Number.isSafeInteger(result.version) || result.version <= 0) throw new Error("ACCOUNT_RESPONSE_INVALID");
        setUncertain(false); profileRequest.current = null; setNotice("표시 이름을 저장했습니다. 로그인 ID와 비밀번호는 변경되지 않았습니다."); await load();
      }, failure: failed,
    });
  };
  const choose = (kind, row) => {
    clearReauthentication(); setUncertain(false); setNotice("");
    setSelected({ kind, id: kind === "device" ? row.device_id : row.session_id, is_current: row.is_current }); sensitiveRequest.current = null;
  };
  const submitSensitive = (event) => {
    event.preventDefault();
    if (!selected || !password || password.length > 256) return;
    const secret = password, target = selected;
    clearReauthentication();
    if (!sensitiveRequest.current) sensitiveRequest.current = prepareRequest(() => ({ stepKey: requestKey(), mutationKey: requestKey() }));
    const keys = sensitiveRequest.current;
    if (!keys) return;
    void runOperation("revoke", async (signal) => {
      const grant = await stepUp(target.id, secret, { signal, idempotencyKey: keys.stepKey });
      if (signal.aborted) throw new Error("ACCOUNT_REQUEST_ABORTED");
      return (target.kind === "device" ? revokeDevice : revokeSession)(target.id, grant.step_up_authorization, { signal, idempotencyKey: keys.mutationKey });
    }, { success: async (result) => {
      if (result?.state !== "revoked" || result[target.kind === "device" ? "device_id" : "session_id"] !== target.id) throw new Error("ACCOUNT_RESPONSE_INVALID");
      setUncertain(false); setSelected(null); sensitiveRequest.current = null;
      setNotice(target.kind === "device" ? "장치의 로그인 세션을 철회했습니다. 동기화 키 철회는 별도 후속 확인이 필요하며 완료로 표시하지 않습니다." : "선택한 세션을 철회했습니다.");
      if (target.is_current) validateSession(null); else await load();
    }, failure: failed });
  };
  const trust = (row) => {
    if (!trustRequest.current || trustRequest.current.id !== row.device_id) trustRequest.current = prepareRequest(() => ({ id: row.device_id, key: requestKey() }));
    if (!trustRequest.current) return;
    const key = trustRequest.current.key;
    void runOperation("trust", (signal) => trustDevice(row.device_id, { signal, idempotencyKey: key }), {
      success: async (result) => { if (result?.state !== "trusted") throw new Error("ACCOUNT_RESPONSE_INVALID"); trustRequest.current = null; setUncertain(false); setNotice("현재 장치의 신뢰 등록을 확인했습니다."); await load(); }, failure: failed,
    });
  };
  return <section className="settings-card" aria-labelledby="account-profile-title">
    <h2 id="account-profile-title">본인 프로필 · 등록 장치와 세션</h2>
    {loadState === "loading" && <p role="status" aria-busy="true">프로필과 본인 목록 조회 중…</p>}
    {loadState === "failed" && <p role="alert">프로필·장치 조회 실패. 기존 값을 추정해 표시하지 않습니다.</p>}
    <button type="button" disabled={Boolean(pending) || loadState === "loading"} onClick={() => { profileRequest.current = null; setUncertain(false); void load(); }}>프로필·장치 다시 조회</button>
    {bundle && loadState === "ready" && <>
      <form aria-label="표시 이름 저장" onSubmit={submitProfile}>
        <label>표시 이름 (1~80자)<input ref={nameInput} type="text" autoComplete="nickname" value={name} disabled={Boolean(pending) || uncertain}
          onChange={(event) => { setName(event.target.value); profileRequest.current = null; }} /></label>
        <p>로그인 ID·이메일·역할은 변경하지 않습니다. 저장 version: {bundle.profile.version}</p>
        <button type="submit" disabled={Boolean(pending) || !name.trim()}>{uncertain && profileRequest.current ? "같은 프로필 요청 확인" : "표시 이름 저장"}</button>
      </form>
      <table><caption>현재 소속의 본인 등록 장치</caption><thead><tr><th>장치 ID</th><th>종류</th><th>상태</th><th>최근 사용 (UTC)</th><th>작업</th></tr></thead><tbody>
        {bundle.devices.items.map((row) => <tr key={row.device_id}><td>{row.device_id}{row.is_current ? " (현재 장치)" : ""}</td><td>{row.platform}</td><td>{({ registered: "등록됨", trusted: "신뢰됨", revoked: "철회됨" })[row.state]}</td><td><time dateTime={row.last_seen_at}>{row.last_seen_at}</time></td><td>
          {row.is_current && row.state === "registered" && <button type="button" disabled={Boolean(pending)} onClick={() => trust(row)}>현재 장치 신뢰 등록</button>}
          {row.state !== "revoked" && <button type="button" aria-label={`장치 철회 ${row.device_id}`} disabled={Boolean(pending)} onClick={() => choose("device", row)}>장치 철회</button>}
        </td></tr>)}
      </tbody></table>
      {bundle.devices.next_cursor && <button type="button" disabled={Boolean(pending)} onClick={() => void load({ deviceCursor: bundle.devices.next_cursor })}>장치 더 보기</button>}
      <table><caption>현재 소속의 본인 세션</caption><thead><tr><th>세션 ID</th><th>장치 ID</th><th>접속</th><th>상태</th><th>만료 (UTC)</th><th>작업</th></tr></thead><tbody>
        {bundle.sessions.items.map((row) => <tr key={row.session_id}><td>{row.session_id}{row.is_current ? " (현재 세션)" : ""}</td><td>{row.device_id}</td><td>{row.client_kind}</td><td>{row.state === "active" ? "사용 중" : "철회됨"}</td><td><time dateTime={row.expires_at}>{row.expires_at}</time></td><td>
          {row.state === "active" && <button type="button" aria-label={`세션 철회 ${row.session_id}`} disabled={Boolean(pending)} onClick={() => choose("session", row)}>세션 철회</button>}
        </td></tr>)}
      </tbody></table>
      {bundle.sessions.next_cursor && <button type="button" disabled={Boolean(pending)} onClick={() => void load({ sessionCursor: bundle.sessions.next_cursor })}>세션 더 보기</button>}
      {selected && <form aria-label="철회 추가 인증" onSubmit={submitSensitive}>
        <p>{selected.kind === "device" ? "장치" : "세션"} {selected.id} 철회. {selected.is_current ? "현재 접속이 종료됩니다." : "다른 본인 접속은 유지됩니다."}</p>
        <label>철회 추가 인증 비밀번호<input ref={(node) => { passwordInputs.current[3] = node; }} type="password" autoComplete="current-password" maxLength={256} value={password} disabled={Boolean(pending)} onChange={(event) => setPassword(event.target.value)} /></label>
        <button type="submit" disabled={Boolean(pending) || !password}>추가 인증 후 철회</button>
        <button type="button" disabled={Boolean(pending)} onClick={() => { clearReauthentication(); setSelected(null); sensitiveRequest.current = null; }}>철회 취소</button>
      </form>}
    </>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}

export function AccountSettingsWorkspace({
  getSession = getCurrentNotebookSession, changePassword = changeCurrentPassword,
  logout = logoutCurrentSession, navigate = replaceLocation,
  getSettings = loadSettings, saveProfile = saveAccountProfile, stepUp = requestAccountStepUp,
  trustDevice = trustAccountDevice, revokeDevice = revokeAccountDevice, revokeSession = revokeAccountSession,
}) {
  const epoch = useRef(0);
  const operation = useRef(null);
  const protectedRoot = useRef(null);
  const passwordInputs = useRef([]);
  const additionalClear = useRef(null);
  const validatedSession = useRef(null);
  const [session, setSession] = useState(null);
  const [state, setState] = useState("loading");
  const [retry, setRetry] = useState(0);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");

  const clearPasswords = () => {
    for (const input of passwordInputs.current) { if (input) input.value = ""; }
    setCurrentPassword(""); setNewPassword(""); setConfirmation("");
    additionalClear.current?.();
  };
  const discardDisplay = () => {
    concealProtectedRoute(protectedRoot.current); validatedSession.current = null;
    clearPasswords(); setSession(null); setState("loading");
  };
  const acceptSession = (value) => {
    if (value.password_change_required) { discardDisplay(); navigate("/password-change"); return; }
    validatedSession.current = value; setSession(value); setState("ready");
  };

  useEffect(() => {
    let controller;
    const invalidate = () => {
      ++epoch.current; controller?.abort(); operation.current?.controller.abort(); operation.current = null;
    };
    const hide = () => {
      invalidate(); discardDisplay(); setPending(""); setError("");
    };
    const load = async () => {
      hide(); controller = new AbortController();
      const current = epoch.current;
      try {
        const value = await getSession({ signal: controller.signal });
        if (current !== epoch.current || controller.signal.aborted) return;
        acceptSession(value);
      } catch (caught) {
        if (current !== epoch.current || controller.signal.aborted) return;
        if (caught?.message === "AUTHENTICATION_REQUIRED") { navigate("/"); return; }
        setState("failed");
      }
    };
    const show = () => { void load(); };
    const pop = () => { void load(); };
    const visible = () => { if (document.visibilityState === "visible") void load(); };
    void load();
    window.addEventListener("pagehide", hide); window.addEventListener("pageshow", show); window.addEventListener("popstate", pop);
    window.addEventListener("focus", pop); document.addEventListener("visibilitychange", visible);
    return () => {
      invalidate(); concealProtectedRoute(protectedRoot.current); validatedSession.current = null;
      for (const input of passwordInputs.current) { if (input) input.value = ""; }
      window.removeEventListener("pagehide", hide); window.removeEventListener("pageshow", show); window.removeEventListener("popstate", pop);
      window.removeEventListener("focus", pop); document.removeEventListener("visibilitychange", visible);
    };
  }, [getSession, changePassword, logout, navigate, retry]);

  useEffect(() => {
    if (state === "ready" && session === validatedSession.current) revealProtectedRoute(protectedRoot.current);
  }, [session, state]);

  const runOperation = async (kind, invoke, callbacks) => {
    if (operation.current || state !== "ready" || !validatedSession.current) return;
    const expected = validatedSession.current;
    const active = { epoch: epoch.current, controller: new AbortController() };
    const current = () => active.epoch === epoch.current && !active.controller.signal.aborted;
    const failedValidation = (caught) => {
      discardDisplay();
      if (caught?.message === "AUTHENTICATION_REQUIRED") navigate("/");
      else setState("failed");
    };
    const matches = (value) => {
      if (value.password_change_required || !sameSession(expected, value)) {
        discardDisplay(); acceptSession(value); setError("로그인 세션이 변경되었습니다. 새 계정 정보를 확인하고 다시 입력하세요."); return false;
      }
      return true;
    };
    operation.current = active;
    setPending(kind); setError(""); clearPasswords();
    let verified = false;
    try {
      const latest = await getSession({ signal: active.controller.signal });
      if (!current() || !matches(latest)) return;
      verified = true;
      const result = await invoke(active.controller.signal);
      if (!current()) return;
      if (callbacks) { await callbacks.success(result); return; }
      if (result?.status !== (kind === "password" ? "password_changed" : "logged_out")) throw new Error("RESPONSE_INVALID");
      discardDisplay(); setState(kind === "password" ? "password_changed" : "signed_out");
      if (kind === "logout") navigate("/");
    } catch (caught) {
      if (!current()) return;
      if (!verified) { failedValidation(caught); return; }
      if (kind === "profile" && caught?.message === "ACCOUNT_SESSION_CHANGED") {
        discardDisplay(); setRetry((value) => value + 1); return;
      }
      if (caught?.message === "AUTHENTICATION_REQUIRED") {
        try {
          const latest = await getSession({ signal: active.controller.signal });
          if (!current() || !matches(latest)) return;
          if (callbacks) { callbacks.failure(caught); return; }
          setError(kind === "password" ? "현재 비밀번호를 확인하고 다시 시도하세요." : "로그아웃하지 못했습니다. 다시 시도하세요.");
        } catch (validationError) { if (current()) failedValidation(validationError); }
        return;
      }
      if (callbacks) { callbacks.failure(caught); return; }
      if (kind === "logout") setError("로그아웃하지 못했습니다. 현재 세션 상태를 확인한 뒤 다시 시도하세요.");
      else if (caught?.message === "PASSWORD_POLICY_FAILED") setError("새 비밀번호가 서버의 비밀번호 정책에 맞지 않습니다. 다시 입력하세요.");
      else setError("비밀번호 변경을 확인하지 못했습니다. 자동으로 재시도하지 않습니다. 로그인 상태를 확인하세요.");
    } finally {
      if (operation.current === active) { operation.current = null; setPending(""); }
    }
  };
  const passwordValid = currentPassword.length > 0 && currentPassword.length <= 256
    && newPassword.length >= 12 && newPassword.length <= 256 && newPassword === confirmation
    && !/[\u0000-\u001f]/u.test(currentPassword + newPassword);
  const submit = (event) => {
    event.preventDefault();
    if (!passwordValid) return;
    void runOperation("password", (signal) => changePassword(currentPassword, newPassword, { signal }));
  };

  return <main className="common-settings-page" aria-labelledby="account-settings-title">
    <header className="common-settings-header"><a href="/notebooks">Notebook으로</a><h1 id="account-settings-title">계정 설정</h1></header>
    {state === "loading" && <p className="common-settings-state" role="status" aria-busy="true">계정 정보를 불러오는 중입니다.</p>}
    {state === "failed" && <div className="common-settings-error" role="alert">계정 정보를 불러오지 못했습니다. <button type="button" onClick={() => setRetry((value) => value + 1)}>다시 조회</button></div>}
    {(state === "password_changed" || state === "signed_out") && <section className="common-settings-state" role="status">
      <p>{state === "password_changed" ? "비밀번호가 변경되었습니다. 모든 세션이 종료됩니다. 새 비밀번호로 다시 로그인하세요." : "현재 세션에서 로그아웃했습니다."}</p>
      <button type="button" onClick={() => navigate("/")}>로그인으로</button>
    </section>}
    <div ref={protectedRoot} hidden={state !== "ready"} inert={state !== "ready"}
      aria-hidden={state !== "ready" ? "true" : undefined} data-session-validated={state === "ready" ? "true" : "false"}>
    {state === "ready" && session && <div className="license-settings-grid">
      <section className="settings-card" aria-labelledby="account-session-title">
        <h2 id="account-session-title">본인 계정 · 현재 세션</h2>
        <dl style={{ overflowWrap: "anywhere" }}>
          <dt>로그인 ID</dt><dd>{session.login_id || "제공되지 않음"}</dd>
          <dt>사용자 ID</dt><dd>{session.user_id}</dd>
          <dt>현재 소속 ID</dt><dd>{session.tenant_id}</dd>
          <dt>현재 Workspace ID</dt><dd>{session.workspace_id}</dd>
          <dt>현재 세션 ID</dt><dd>{session.session_id}</dd>
          <dt>현재 세션의 장치 ID</dt><dd>{session.device_id}</dd>
          <dt>접속 방식</dt><dd>Web · 동일 출처 보안 쿠키</dd>
          <dt>세션 만료 시각 (UTC)</dt><dd><time dateTime={session.expires_at}>{session.expires_at}</time></dd>
        </dl>
        <button type="button" disabled={Boolean(pending)} onClick={() => void runOperation("logout", (signal) => logout({ signal }))}>{pending === "logout" ? "로그아웃 중…" : "현재 세션 로그아웃"}</button>
      </section>
      <section className="settings-card" aria-labelledby="account-password-title">
        <h2 id="account-password-title">비밀번호 변경</h2>
        <p className="settings-warning" id="account-password-warning">제출하면 실제 계정 비밀번호가 변경되고, 현재 세션을 포함한 모든 세션이 종료됩니다. 새 비밀번호로 다시 로그인해야 합니다.</p>
        <form className="license-apply" onSubmit={submit} aria-describedby="account-password-warning">
          <label>현재 비밀번호<input ref={(input) => { passwordInputs.current[0] = input; }} type="password" autoComplete="current-password" maxLength={256} required disabled={Boolean(pending)} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
          <label>새 비밀번호 (12~256자)<input ref={(input) => { passwordInputs.current[1] = input; }} type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={Boolean(pending)} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label>
          <label>새 비밀번호 확인<input ref={(input) => { passwordInputs.current[2] = input; }} type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={Boolean(pending)} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
          {confirmation && confirmation !== newPassword && <p role="alert">새 비밀번호가 일치하지 않습니다.</p>}
          <button type="submit" disabled={Boolean(pending) || !passwordValid}>{pending === "password" ? "변경 중…" : "비밀번호 변경"}</button>
        </form>
      </section>
      <AccountProfileDevices key={[session.user_id, session.tenant_id, session.workspace_id, session.session_id, session.device_id].join("|")}
        session={session} getSettings={getSettings} saveProfile={saveProfile} stepUp={stepUp} trustDevice={trustDevice} revokeDevice={revokeDevice} revokeSession={revokeSession}
        runOperation={runOperation} isActive={() => validatedSession.current === session} passwordInputs={passwordInputs} additionalClear={additionalClear} pending={pending} validateSession={(value) => {
          if (!value) { discardDisplay(); navigate("/"); return false; }
          if (!sameSession(session, value) || value.password_change_required) { discardDisplay(); acceptSession(value); return false; }
          return true;
        }} />
      {error && <p className="settings-card" role="alert">{error}</p>}
    </div>}
    </div>
  </main>;
}
