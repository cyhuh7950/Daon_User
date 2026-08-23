"use client";

import { useState } from "react";
import { getOrganizationSession, submitJoinRequest } from "../lib/organization-admin-api.js";

export function OrganizationJoinForm() {
  const [invitationCode, setInvitationCode] = useState("");
  const [state, setState] = useState("idle");
  const [message, setMessage] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    const code = invitationCode.trim();
    if (!code) {
      setState("error");
      setMessage("초대코드를 입력해 주세요.");
      return;
    }
    setState("loading");
    setMessage("");
    try {
      await getOrganizationSession();
      const result = await submitJoinRequest(code);
      setState("success");
      setMessage(`가입 신청이 접수되었습니다. 조직 관리자 승인 후 사용할 수 있습니다. (요청: ${result.request_id})`);
      setInvitationCode("");
    } catch (error) {
      setState("error");
      const messages = {
        AUTHENTICATION_REQUIRED: "로그인 후 조직 가입을 신청할 수 있습니다.",
        INVITATION_INVALID: "유효하지 않거나 만료된 초대코드입니다.",
        PERSISTENCE_CONFLICT: "이미 처리 중인 조직 가입 신청이 있습니다.",
      };
      setMessage(messages[error?.message] || "조직 가입 신청을 처리하지 못했습니다. 다시 시도해 주세요.");
    }
  };

  return <main className="organization-join-page">
    <section className="organization-join-card" aria-labelledby="organization-join-title">
      <p className="eyebrow">DAON WORKSPACE</p>
      <h1 id="organization-join-title">조직 가입</h1>
      <p className="organization-join-description">조직 관리자에게 받은 초대코드를 입력하면 가입 신청을 보낼 수 있습니다.</p>
      <form onSubmit={submit}>
        <label htmlFor="organization-invitation-code">초대코드</label>
        <input
          id="organization-invitation-code"
          value={invitationCode}
          onChange={(event) => setInvitationCode(event.target.value)}
          placeholder="예: AAAAA-TEST-2026"
          autoComplete="off"
          disabled={state === "loading"}
        />
        <button type="submit" disabled={state === "loading"}>
          {state === "loading" ? "신청 중…" : "가입 신청"}
        </button>
      </form>
      {message && <p role={state === "error" ? "alert" : "status"} className={`organization-join-message ${state}`}>{message}</p>}
    </section>
  </main>;
}
