"use client";

import { useEffect, useRef, useState } from "react";
import * as organizationApi from "../lib/organization-admin-api.js";
import { secureUuid } from "@daon-user/ui/secure-uuid";
import { concealProtectedRoute, revealProtectedRoute } from "../lib/protected-route-guard.js";

const sameSession = (a, b) => Boolean(a && b && ["user_id", "session_id", "tenant_id", "workspace_id", "device_id"].every(key => a[key] === b[key]));
const validSession = value => Boolean(value?.user_id && value?.session_id);
const labels = { pending: "승인 대기", approved: "승인됨", rejected: "거부됨" };

export function OrganizationJoinForm({ api: suppliedApi = organizationApi }) {
  const api = useRef(suppliedApi).current;
  const root = useRef(null), input = useRef(null), epoch = useRef(0), mounted = useRef(false);
  const currentSession = useRef(null), reading = useRef(null), controller = useRef(null), operation = useRef(null);
  const [invitationCode, setInvitationCode] = useState("");
  const [state, setState] = useState("loading"), [rows, setRows] = useState([]), [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const clearInput = () => { if (input.current) input.current.value = ""; setInvitationCode(""); };
  const discard = () => { if (operation.current) operation.current.code = null; operation.current = null; };
  const invalidate = () => {
    epoch.current++; reading.current = null; controller.current?.abort(); concealProtectedRoute(root.current); setRows([]); clearInput();
    const pending = operation.current;
    if (pending && (pending.phase === "sent" || pending.unconfirmed)) { pending.unconfirmed = true; pending.status = "uncertain"; pending.controller?.abort(); }
    else if (pending) { pending.controller?.abort(); discard(); }
  };
  const failRead = (error, generation) => {
    if (!mounted.current || generation !== epoch.current) return;
    invalidate(); setState(error?.status === 401 || error?.status === 403 ? "denied" : "error");
    setMessage(error?.status === 401 ? "로그인 후 조직 가입을 신청할 수 있습니다." : error?.status === 403 ? "본인 신청을 조회할 권한이 없습니다." : "본인 신청 조회에 실패했습니다. 상태를 다시 조회하세요.");
  };
  const refresh = async () => {
    if (reading.current) return false;
    invalidate(); const attempt = {}; reading.current = attempt; const generation = epoch.current;
    const abort = new AbortController(); controller.current = abort; setState("loading"); setMessage("");
    try {
      const fresh = await api.getOrganizationSession({ signal: abort.signal });
      if (!mounted.current || generation !== epoch.current) return false;
      if (!validSession(fresh)) { const error = new Error(); error.status = 401; throw error; }
      if (!sameSession(fresh, currentSession.current)) { discard(); setBusy(false); }
      currentSession.current = fresh;
      const values = await api.listOwnJoinRequests({ signal: abort.signal });
      if (!mounted.current || generation !== epoch.current) return false;
      if (!Array.isArray(values) || values.some(value => value.user_id !== fresh.user_id || !labels[value.state])) throw new Error("ORGANIZATION_RESPONSE_INVALID");
      setRows(values); setState("ready"); return true;
    } catch (error) { failRead(error, generation); return false; }
    finally { if (reading.current === attempt) reading.current = null; }
  };
  useEffect(() => {
    mounted.current = true; void refresh();
    const hide = () => { invalidate(); setState("hidden"); };
    const resume = () => { void refresh(); };
    const visibility = () => { if (document.visibilityState === "hidden") hide(); else resume(); };
    window.addEventListener("pagehide", hide); window.addEventListener("pageshow", resume); window.addEventListener("focus", resume); window.addEventListener("popstate", resume); document.addEventListener("visibilitychange", visibility);
    return () => { mounted.current = false; epoch.current++; controller.current?.abort(); operation.current?.controller?.abort(); window.removeEventListener("pagehide", hide); window.removeEventListener("pageshow", resume); window.removeEventListener("focus", resume); window.removeEventListener("popstate", resume); document.removeEventListener("visibilitychange", visibility); };
  }, []);
  useEffect(() => { if (state !== "hidden") revealProtectedRoute(root.current); }, [state]);
  const send = async request => {
    if (operation.current !== request || request.status === "sending") return;
    request.status = "sending"; request.phase = "checking"; request.controller = new AbortController();
    const generation = epoch.current; setBusy(true); setMessage("");
    try {
      const fresh = await api.getOrganizationSession({ signal: request.controller.signal });
      if (operation.current !== request) return;
      if (!validSession(fresh)) { const error = new Error(); error.status = 401; throw error; }
      if (!sameSession(fresh, request.session)) { discard(); await refresh(); return; }
      if (!mounted.current || generation !== epoch.current) return;
      request.phase = "sent";
      const result = await api.submitJoinRequest(request.code, { expectedSessionId: request.session.session_id, idempotencyKey: request.key, signal: request.controller.signal });
      if (operation.current !== request || !sameSession(request.session, currentSession.current)) return;
      if (result?.user_id !== request.session.user_id || !labels[result?.state]) throw new Error("ORGANIZATION_RESPONSE_INVALID");
      discard(); request.phase = "done";
      if (mounted.current && generation === epoch.current) {
        const loaded = await refresh();
        if (mounted.current && sameSession(request.session, currentSession.current)) setMessage(loaded ? "가입 신청 접수를 확인했습니다. 현재 본인 상태를 조회했습니다." : "접수는 확인했지만 상태 조회에 실패했습니다. 다시 조회하세요.");
      }
    } catch (error) {
      if (operation.current !== request || !sameSession(request.session, currentSession.current)) return;
      if (request.unconfirmed) request.status = "uncertain";
      else if (request.phase !== "sent") discard();
      else if (!error?.status || error.status >= 500 && error.message !== "ORGANIZATION_WORKFLOW_UNAVAILABLE" || error.status === 408) { request.unconfirmed = true; request.status = "uncertain"; }
      else discard();
      if (!mounted.current || generation !== epoch.current) return;
      if (error?.status === 401 || error?.status === 403) { failRead(error, generation); return; }
      const messages = { INVITATION_INVALID: "유효하지 않거나 만료된 초대코드입니다.", JOIN_REQUEST_ALREADY_PENDING: "이미 승인 대기 중인 신청이 있습니다. 상태를 조회하세요.", ORGANIZATION_WORKFLOW_UNAVAILABLE: "조직 가입 신청 기능이 아직 준비되지 않았습니다." };
      setMessage(operation.current?.unconfirmed ? "결과 미확정 — 새 신청 대신 동일 신청 결과를 확인하세요." : messages[error?.message] || "조직 가입 신청을 처리하지 못했습니다. 상태를 다시 조회하세요.");
    } finally { if (mounted.current && sameSession(request.session, currentSession.current)) setBusy(false); }
  };
  const submit = event => {
    event.preventDefault();
    if (operation.current || state !== "ready") return;
    const code = invitationCode.trim();
    if (!code) { setMessage("초대코드를 입력해 주세요."); return; }
    try {
      const request = { code, key: `org-${secureUuid()}`, session: Object.freeze({ ...currentSession.current }), status: "new", phase: "checking" };
      operation.current = request; clearInput(); void send(request);
    } catch { setMessage("안전한 요청 키를 만들 수 없습니다. 이 환경에서는 신청하지 않습니다."); }
  };
  const pending = operation.current, locked = busy || Boolean(pending);

  return <main className="organization-join-page" ref={root}>
    <section className="organization-join-card" aria-labelledby="organization-join-title">
      <p className="eyebrow">DAON WORKSPACE</p><h1 id="organization-join-title">조직 가입</h1>
      <button type="button" disabled={busy} onClick={() => { void refresh(); }}>상태 새로고침</button>
      {state === "loading" && <p role="status">본인 신청을 조회하는 중입니다.</p>}
      {message && <p role={state === "error" || state === "denied" ? "alert" : "status"}>{message}</p>}
      {state === "ready" && <>
        {pending?.unconfirmed && <div><p role="status">결과 미확정 — 원 세션·원 코드·원 키로만 확인합니다.</p><button type="button" disabled={busy} onClick={() => { void send(pending); }}>동일 신청 결과 확인</button></div>}
        <form onSubmit={submit}>
          <label htmlFor="organization-invitation-code">초대코드</label>
          <input ref={input} id="organization-invitation-code" value={invitationCode} onChange={event => setInvitationCode(event.target.value)} maxLength={160} autoComplete="off" disabled={locked} />
          <button type="submit" disabled={locked || !invitationCode.trim()}>가입 신청</button>
          <span title="신청만으로 접근 권한이 생기지 않습니다. 거부 후에는 유효한 초대코드로 직접 새 신청을 제출하세요. 코드는 저장하지 않습니다." aria-label="가입 신청 안내">ⓘ</span>
        </form>
        <h2>본인 가입 신청</h2>
        {rows.length === 0 ? <p role="status">본인 가입 신청이 없습니다.</p> : <ul>{rows.map(value => <li key={value.request_id}><strong>{labels[value.state]}</strong> · {value.tenant_id} · {value.request_id}{value.decision_reason && <small> · {value.decision_reason}</small>}</li>)}</ul>}
      </>}
    </section>
  </main>;
}
