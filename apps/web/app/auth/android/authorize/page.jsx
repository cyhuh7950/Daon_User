"use client";

import { useEffect, useState } from "react";
import { AuthPane } from "../../../../lib/auth-pane.jsx";
import { getCurrentNotebookSession } from "../../../../lib/notebook-api.js";

const TRANSACTION_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

function transactionFromSearch(search) {
  const entries = [...new URLSearchParams(search)];
  return entries.length === 1 && entries[0][0] === "transaction_id"
    && TRANSACTION_ID.test(entries[0][1]) ? entries[0][1] : null;
}

export default function AndroidAuthorizePage() {
  const [transactionId, setTransactionId] = useState(null);
  const [status, setStatus] = useState("checking");

  const checkSession = async (signal) => {
    setStatus("checking");
    try {
      const session = await getCurrentNotebookSession({ signal });
      if (!signal?.aborted) setStatus(session.password_change_required ? "password-change" : "ready");
    } catch (error) {
      if (signal?.aborted) return;
      setStatus(error?.message === "AUTHENTICATION_REQUIRED" ? "login"
        : error?.message === "PASSWORD_CHANGE_REQUIRED" ? "password-change" : "unavailable");
    }
  };

  useEffect(() => {
    const selected = transactionFromSearch(window.location.search);
    if (!selected) { setStatus("invalid"); return; }
    setTransactionId(selected);
    const controller = new AbortController();
    void checkSession(controller.signal);
    return () => controller.abort();
  }, []);

  if (status === "invalid") return <main className="daon-auth-pane" role="alert">인증 거래를 확인할 수 없습니다.</main>;
  if (status === "checking") return <main className="daon-auth-pane" role="status" aria-busy="true">인증 상태를 확인하는 중입니다.</main>;
  if (status === "login") return <main><AuthPane onLoginComplete={() => checkSession()} /></main>;
  if (status === "password-change") return <main className="daon-auth-pane" role="alert">비밀번호를 변경한 뒤 이 화면으로 돌아오세요. <a href="/password-change">비밀번호 변경</a></main>;
  if (status === "unavailable") return <main className="daon-auth-pane" role="alert">인증 요청을 완료하지 못했습니다. 새 거래로 다시 시작하세요.</main>;
  return <main className="daon-auth-pane"><h1>Android 로그인 승인</h1><p>이 기기에서 로그인을 계속하시겠습니까?</p>
    <form action="/bff/api/session/android/authorize" method="post">
      <input type="hidden" name="transaction_id" value={transactionId} />
      <button type="submit">계속</button>
    </form>
  </main>;
}
