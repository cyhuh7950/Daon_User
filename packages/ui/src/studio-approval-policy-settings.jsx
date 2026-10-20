import { useEffect, useRef, useState } from "react";
import { secureUuid } from "./secure-uuid.js";

export function StudioApprovalPolicySettings({ adapter, workspaceId }) {
  const [policy, setPolicy] = useState(null);
  const [days, setDays] = useState("7");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const requestRef = useRef(null);
  const scopeRef = useRef(null);
  const changeButtonRef = useRef(null);
  const returnFocusRef = useRef(false);
  scopeRef.current = { adapter, workspaceId };
  const current = request => requestRef.current === request && !request.controller.signal.aborted
    && scopeRef.current.adapter === request.adapter && scopeRef.current.workspaceId === request.workspaceId;
  useEffect(() => {
    const controller = new AbortController();
    const scope = { adapter, workspaceId };
    returnFocusRef.current = false;
    setPolicy(null); setDays("7"); setPassword(""); setChallenge(false); setPending(false); setError(null);
    const owns = () => !controller.signal.aborted && scopeRef.current.adapter === scope.adapter && scopeRef.current.workspaceId === scope.workspaceId;
    if (adapter?.getStudioApprovalPolicy) {
      void adapter.getStudioApprovalPolicy({ signal: controller.signal }).then(value => {
        if (owns()) { setPolicy(value); setDays(String(value.ttl_days)); }
      }).catch(() => { if (owns()) setError("STUDIO_APPROVAL_POLICY_UNAVAILABLE"); });
    } else setError("STUDIO_APPROVAL_POLICY_UNAVAILABLE");
    return () => { controller.abort(); requestRef.current?.controller.abort(); requestRef.current = null; };
  }, [adapter, workspaceId]);
  useEffect(() => {
    if (!challenge && returnFocusRef.current) {
      returnFocusRef.current = false;
      changeButtonRef.current?.focus();
    }
  }, [challenge]);
  const cancel = () => {
    requestRef.current?.controller.abort(); requestRef.current = null;
    returnFocusRef.current = true;
    setPassword(""); setChallenge(false); setPending(false);
  };
  const begin = () => {
    if (requestRef.current || !policy?.can_manage || !adapter?.saveStudioApprovalPolicy
      || !adapter?.issueStudioApprovalPolicyStepUp || !/^(?:[1-9]|[12][0-9]|30)$/u.test(days)) return;
    requestRef.current = { adapter, workspaceId, controller: new AbortController(), issuing: false,
      ttl: Number(days), expected: policy.policy_version, target: policy.step_up_target_id, idempotencyKey: secureUuid() };
    setPassword(""); setError(null); setChallenge(true);
  };
  const confirm = async () => {
    const request = requestRef.current;
    if (!request || !current(request) || request.issuing || password.length < 12) return;
    const currentPassword = password;
    request.issuing = true; setPassword(""); setPending(true); setError(null);
    try {
      const token = await request.adapter.issueStudioApprovalPolicyStepUp(request.target, currentPassword, { signal: request.controller.signal });
      if (!current(request)) return;
      if (typeof token !== "string" || !token) throw new Error("STEP_UP_REQUIRED");
      const value = await request.adapter.saveStudioApprovalPolicy({ ttl_days: request.ttl,
        expected_policy_version: request.expected, step_up_authorization: token },
      { signal: request.controller.signal, idempotencyKey: request.idempotencyKey });
      if (current(request)) { setPolicy(value); setDays(String(value.ttl_days)); returnFocusRef.current = true; setChallenge(false); requestRef.current = null; }
    } catch (failure) {
      if (current(request)) {
        request.issuing = false;
        setError(/^[A-Z][A-Z0-9_]+$/u.test(failure?.message) ? failure.message : "STUDIO_APPROVAL_POLICY_UNAVAILABLE");
      }
    } finally {
      if (scopeRef.current.adapter === request.adapter && scopeRef.current.workspaceId === request.workspaceId && !request.controller.signal.aborted) setPending(false);
    }
  };
  return <div className="workspace-modal-content" aria-label="승인 정책">
    {error ? <p role="alert">승인 정책을 처리하지 못했습니다. 현재 접근과 연결을 확인해 주세요. ({error})</p> : null}
    {!policy && !error ? <p role="status">승인 정책을 불러오고 있습니다.</p> : null}
    {policy ? <>
      <p>조직 승인 기한 {policy.ttl_days}일</p>
      <label>승인 기한 일수<input type="number" min="1" max="30" value={days} disabled={challenge || pending || !policy.can_manage}
        onChange={event => { if (!requestRef.current) setDays(event.currentTarget.value); }} /></label>
      <button ref={changeButtonRef} type="button" disabled={challenge || pending || !policy.can_manage || !/^(?:[1-9]|[12][0-9]|30)$/u.test(days)} onClick={begin}>승인 기한 변경</button>
      <small>{policy.can_manage ? "재인증 후 이후 요청에만 적용됩니다." : "조직 정책으로 잠김 · 현재 정책 변경 권한이 없습니다."}</small>
    </> : null}
    {challenge ? <section aria-label="승인 정책 추가 인증">
      <p>조직 승인 정책 변경을 위해 추가 인증이 필요합니다.</p>
      <label>추가 인증 비밀번호<input type="password" autoComplete="current-password" maxLength={1024} value={password} disabled={pending}
        onChange={event => setPassword(event.currentTarget.value)} /></label>
      <button type="button" disabled={pending || password.length < 12} onClick={() => void confirm()}>추가 인증 확인</button>
      <button type="button" onClick={cancel}>추가 인증 취소</button>
    </section> : null}
  </div>;
}
