"use client";

import { useCallback, useEffect, useId, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import "./workspace.css";

const VIEW_STATES = Object.freeze(["loading", "empty", "forbidden", "unavailable", "error", "ready"]);

function safeStatus(status) {
  if (status === 401 || status === 403 || status === 404) return "forbidden";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  return "error";
}

function safeDeepLink(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return null;
  const first = value.split(/[/?#]/).filter(Boolean)[0];
  return new Set(["operations", "inbox", "notifications", "workspaces"]).has(first) ? value : null;
}

function Info({ label }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <span className="operations-info">
    <button type="button" aria-label={`${label} 설명`} aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>i</button>
    {open && <span id={id} role="tooltip">{label}</span>}
  </span>;
}

function StatePanel({ state, retry }) {
  const labels = {
    loading: "알림 정보를 불러오는 중입니다.", empty: "표시할 항목이 없습니다.",
    forbidden: "현재 권한으로 이 항목을 볼 수 없습니다.", unavailable: "알림 서비스를 사용할 수 없습니다.",
    error: "요청을 안전하게 처리하지 못했습니다.", ready: "ready",
  };
  return <section className="operations-card operations-span-2" data-view-state={state}>
    <p className="operations-visible-warning" role="status">{labels[state]}</p>
    {new Set(["unavailable", "error"]).has(state) && <button type="button" onClick={retry}>다시 시도</button>}
  </section>;
}

function Header({ mode, unreadCount }) {
  return <>
    <header className="operations-header">
      <div><p className="eyebrow">업무 알림</p><h1>{mode === "notifications" ? "알림" : "전달함"}</h1></div>
      <div className="operations-header-status"><span className="operations-badge">미읽음 {unreadCount}</span></div>
    </header>
    <nav className="operations-route-tabs" aria-label="알림과 전달함">
      <a aria-current={mode === "notifications" ? "page" : undefined} href="/notifications">알림</a>
      <a aria-current={mode === "inbox" ? "page" : undefined} href="/inbox">전달함</a>
      <a href="/operations">운영 상태</a>
    </nav>
  </>;
}

export function NotificationInboxWorkspace({ api, mode = "notifications" }) {
  const [viewState, setViewState] = useState("loading");
  const [items, setItems] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);

  const load = useCallback(async () => {
    setViewState("loading");
    try {
      const response = await api.list(mode);
      if (!response.ok) {
        setItems([]); setUnreadCount(0); setViewState(safeStatus(response.status)); return;
      }
      const payload = response.payload;
      const nextItems = Array.isArray(payload?.data?.items) ? payload.data.items : [];
      setItems(nextItems);
      setUnreadCount(Number.isInteger(payload?.data?.unread_count) ? payload.data.unread_count : 0);
      setViewState(nextItems.length ? "ready" : "empty");
    } catch {
      setItems([]); setUnreadCount(0); setViewState("unavailable");
    }
  }, [api, mode]);

  useEffect(() => { void load(); }, [load]);

  const markRead = async (item) => {
    if (item.read_state === "read") return;
    try {
      const response = await api.markRead(item);
      if (!response.ok) { setViewState(safeStatus(response.status)); return; }
      await load();
    } catch {
      setViewState("unavailable");
    }
  };

  return <div className="operations-shell operations-wide-dashboard" data-view-state={viewState}>
    <Header mode={mode} unreadCount={unreadCount} />
    <div className="operations-live" role="status" aria-live="polite">{viewState} · 실제 same-origin API</div>
    {viewState !== "ready" ? <main className="operations-grid"><StatePanel state={viewState} retry={load} /></main> :
      <main className="operations-grid"><section className="operations-card operations-span-2">
        <div className="operations-title"><h2>{mode === "notifications" ? "현재 권한 알림" : "실행 가능한 요청"}</h2><Info label="목록과 Deep Link는 요청마다 현재 권한으로 다시 판정됩니다." /></div>
        <div className="notification-list">{items.map((item) => {
          const link = safeDeepLink(item.deep_link);
          const id = item.id ?? item.request_id;
          return <article key={id} data-read={item.read_state === "read"}>
            <div><span className="operations-badge">{item.severity ?? item.request_kind}</span><strong>{item.title ?? `${item.request_kind} · ${item.status}`}</strong></div>
            <p>{item.summary ?? `${item.resource_type} · ${item.resource_id}`}</p>
            <small>{item.created_at ?? item.due_at ?? "기한 없음"} · Trace {item.trace_id ?? "소유 Domain Audit 연결"}</small>
            <div className="operations-actions">
              {mode === "notifications" && <button type="button" disabled={item.read_state === "read"} onClick={() => void markRead(item)}>{item.read_state === "read" ? "읽음" : "읽음 처리"}</button>}
              {link ? <a href={link}>원 요청 열기</a> : <span className="operations-visible-warning">CURRENT_ACCESS_DENIED</span>}
            </div>
          </article>;
        })}</div>
      </section></main>}
    <span hidden>{VIEW_STATES.join(" ")}</span>
  </div>;
}
