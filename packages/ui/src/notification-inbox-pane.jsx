"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import "@daon-user/design-tokens/tokens.css";
import "./workspace.css";

const VIEW_STATES = Object.freeze(["loading", "empty", "forbidden", "unavailable", "error", "ready"]);
const NOTIFICATION_FIELDS = "id workspace_id recipient_id kind severity title summary source_event_id resource_type resource_id deep_link audit_event_id trace_id delivery_state read_state created_at delivered_at read_at version".split(" ");
const INBOX_FIELDS = "request_id request_kind status workspace_id actor_id due_at resource_type resource_id deep_link".split(" ");
const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);
const exactFields = (value, fields) => isRecord(value) && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key));
const isId = value => typeof value === "string" && value.length >= 1 && value.length <= 256;
const isText = (value, maximum) => typeof value === "string" && value.length > 0 && value.length <= maximum && !/[<>\u0000-\u001f]/u.test(value);
const isTime = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.test(value) && Number.isFinite(Date.parse(value));

function validPage(payload, mode) {
  if (!exactFields(payload, ["data", "meta"]) || !isRecord(payload.meta)) return false;
  const page = payload.data;
  if (!exactFields(page, mode === "notifications" ? ["items", "next_cursor", "unread_count"] : ["items", "next_cursor"]) || !Array.isArray(page.items)) return false;
  if (page.next_cursor !== null && (typeof page.next_cursor !== "string" || page.next_cursor.length < 1 || page.next_cursor.length > 512)) return false;
  if (mode === "notifications" && (!Number.isSafeInteger(page.unread_count) || page.unread_count < 0)) return false;
  return page.items.every(item => {
    if (!exactFields(item, mode === "notifications" ? NOTIFICATION_FIELDS : INBOX_FIELDS)) return false;
    if (![item.workspace_id, item.resource_type, item.resource_id].every(isId) || typeof item.deep_link !== "string" || item.deep_link.length < 1 || item.deep_link.length > 512) return false;
    if (mode !== "notifications") return isId(item.request_id) && isId(item.actor_id) &&
      ["review", "approval", "delivery"].includes(item.request_kind) &&
      ["pending", "in_review", "approved", "rejected", "expired", "withdrawn", "delivered", "failed", "cancelled"].includes(item.status) && (item.due_at === null || isTime(item.due_at));
    return [item.id, item.recipient_id, item.source_event_id, item.audit_event_id, item.trace_id].every(isId) &&
      ["authentication", "device", "membership", "policy", "run_state", "run_failed", "review", "approval", "delivery", "warning", "recovery"].includes(item.kind) &&
      ["info", "warning", "critical"].includes(item.severity) && isText(item.title, 160) && isText(item.summary, 500) &&
      ["pending", "delivered", "failed", "suppressed"].includes(item.delivery_state) && ["unread", "read"].includes(item.read_state) &&
      Number.isSafeInteger(item.version) && item.version >= 1 && isTime(item.created_at) &&
      (item.delivered_at === null || isTime(item.delivered_at)) && (item.read_at === null || isTime(item.read_at));
  });
}

function safeStatus(status) {
  if (status === 401 || status === 403 || status === 404) return "forbidden";
  if (status === 502 || status === 503 || status === 504) return "unavailable";
  return "error";
}

function safeDeepLink(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return null;
  const notebook = /^\/notebooks\/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\?(?:output_version_id|source_version_id|run_id|studio_job_id)=[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.exec(value);
  // $가 마지막 개행 앞에서 일치해도 전체 입력과 다르면 허용하지 않는다. URL 정규화도 하지 않는다.
  if (value === "/settings/account" || (notebook && notebook[0] === value)) return value;
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
  const rootRef = useRef(null);
  const scopeRef = useRef({ api, mode, active: false, cursor: null });
  if (scopeRef.current.api !== api || scopeRef.current.mode !== mode) scopeRef.current = { api, mode, active: false, cursor: null };
  const scope = scopeRef.current;
  const generation = useRef(0);
  const readyGeneration = useRef(0);
  const listRequest = useRef(null);
  const readRequest = useRef(null);
  const concealed = useRef(false);
  const [snapshot, setSnapshot] = useState({ api, mode, state: "loading", items: [], count: 0 });
  const [readingState, setReading] = useState(null);
  const reading = readingState?.scope === scope && readingState.value;
  const ownSnapshot = snapshot.api === api && snapshot.mode === mode && (!snapshot.scope || snapshot.scope === scope);
  const viewState = ownSnapshot ? snapshot.state : "loading";
  const items = ownSnapshot ? snapshot.items : [];
  const unreadCount = ownSnapshot ? snapshot.count : 0;
  const readUncertain = ownSnapshot && snapshot.uncertain;
  const uncertain = () => readRequest.current?.scope === scope && readRequest.current.phase !== "pending";
  const empty = useCallback((state) => {
    readyGeneration.current = 0;
    setSnapshot({ api, mode, scope, state, items: [], count: 0,
      epoch: generation.current, cursor: scope.cursor, nextCursor: null,
      uncertain: readRequest.current?.scope === scope && readRequest.current.phase !== "pending" });
  }, [api, mode, scope]);

  const load = useCallback(async () => {
    const epoch = ++generation.current;
    const cursor = scope.cursor;
    listRequest.current?.abort();
    const controller = new AbortController();
    listRequest.current = controller;
    const crossedMutation = readRequest.current?.scope === scope && readRequest.current.phase === "pending";
    const current = () => generation.current === epoch && !concealed.current && scope.active && scopeRef.current === scope;
    if (rootRef.current) { rootRef.current.hidden = true; rootRef.current.inert = true; }
    empty("loading");
    try {
      const response = await api.list(mode, { signal: controller.signal, ...(mode === "inbox" ? { cursor } : {}) });
      if (!current()) return;
      // 저장 이전에 시작한 조회는 저장 결과의 확정 근거가 될 수 없다.
      if (crossedMutation) return;
      if (!response.ok) {
        empty(safeStatus(response.status)); return;
      }
      const payload = response.payload;
      if (!validPage(payload, mode)) { empty("error"); return; }
      const nextItems = payload.data.items;
      const pendingRead = readRequest.current;
      if (pendingRead?.scope === scope && pendingRead.phase !== "pending" && nextItems.some(item =>
        item.id === pendingRead.id && item.read_state === "read" && item.version > pendingRead.version)) readRequest.current = null;
      readyGeneration.current = epoch;
      setSnapshot({ api, mode, scope, epoch, items: nextItems, uncertain: uncertain(),
        cursor, nextCursor: mode === "inbox" ? payload.data.next_cursor : null,
        count: mode === "notifications" ? payload.data.unread_count : 0,
        state: nextItems.length ? "ready" : "empty" });
    } catch {
      if (current()) empty("unavailable");
    }
  }, [api, mode, scope, empty]);

  useEffect(() => {
    scope.active = true;
    const conceal = () => {
      concealed.current = true;
      ++generation.current;
      listRequest.current?.abort();
      if (readRequest.current?.scope === scope) readRequest.current.controller.abort();
      // 동결 전에 React flush와 무관하게 이전 본문 조작을 막는다.
      if (rootRef.current) { rootRef.current.hidden = true; rootRef.current.inert = true; }
      empty("loading");
    };
    const refresh = () => {
      concealed.current = false;
      void load();
    };
    const visibility = () => { if (document.visibilityState === "hidden") conceal(); else refresh(); };
    window.addEventListener("pagehide", conceal);
    window.addEventListener("pageshow", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visibility);
    refresh();
    return () => {
      scope.active = false;
      ++generation.current;
      listRequest.current?.abort();
      if (readRequest.current?.scope === scope) readRequest.current.controller.abort();
      window.removeEventListener("pagehide", conceal);
      window.removeEventListener("pageshow", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [load, empty, scope]);

  useEffect(() => {
    // 이전 항목을 제거한 렌더가 commit된 뒤에만 복귀 화면을 연다.
    if (!concealed.current && ownSnapshot && rootRef.current) {
      rootRef.current.hidden = false;
      rootRef.current.inert = false;
    }
  }, [snapshot, ownSnapshot]);

  const openInboxPage = (cursor) => {
    if (mode !== "inbox" || !ownSnapshot || !scope.active || scopeRef.current !== scope || concealed.current ||
        snapshot.epoch !== generation.current || viewState === "loading" || viewState === "forbidden") return;
    if (cursor !== null && (viewState !== "ready" || cursor !== snapshot.nextCursor)) return;
    if (cursor === scope.cursor) return;
    // 이벤트에서 동기 고정해 중복 클릭을 막고 focus/재시도도 같은 페이지를 조회한다.
    scope.cursor = cursor;
    void load();
  };

  const markRead = async (item) => {
    if (item.read_state === "read" || readRequest.current?.scope === scope || concealed.current || mode !== "notifications" || viewState !== "ready" || readyGeneration.current !== generation.current || snapshot.epoch !== generation.current) return;
    const request = { controller: new AbortController(), scope, id: item.id, version: item.version, phase: "pending" };
    readRequest.current = request;
    setReading({ scope, value: true });
    const sameScope = () => scope.active && scopeRef.current === scope;
    const fail = state => {
      request.phase = "uncertain";
      ++generation.current;
      listRequest.current?.abort();
      empty(state);
    };
    try {
      const response = await api.markRead(item, { signal: request.controller.signal });
      if (!sameScope()) return;
      if (!response.ok) { fail(safeStatus(response.status)); return; }
      request.phase = "confirming";
      if (!concealed.current) await load();
    } catch {
      if (sameScope()) fail("unavailable");
    } finally {
      if (sameScope()) setReading({ scope, value: false });
    }
  };

  return <div ref={rootRef} className="operations-shell operations-wide-dashboard" data-view-state={viewState}>
    <Header mode={mode} unreadCount={unreadCount} />
    {readUncertain && <p className="operations-visible-warning" role="status">읽음 처리 결과 미확정 · 다시 조회해 확인해 주세요. 새 읽음 요청은 보내지 않습니다.</p>}
    <div className="operations-live" role="status" aria-live="polite">{viewState} · 실제 same-origin API</div>
    {viewState !== "ready" ? <main className="operations-grid"><StatePanel state={viewState} retry={load} /></main> :
      <main className="operations-grid"><section className="operations-card operations-span-2">
        <div className="operations-title"><h2>{mode === "notifications" ? "현재 권한 알림" : "실행 가능한 요청"}</h2><Info label="목록과 Deep Link는 요청마다 현재 권한으로 다시 판정됩니다." /></div>
        <div className="notification-list">{items.map((item) => {
          const link = safeDeepLink(item.deep_link);
          const id = item.id ?? item.request_id;
          return <article key={id} data-read={item.read_state === "read"}>
            <div><span className="operations-badge">{item.severity ?? (item.request_kind === "delivery" ? "전달 기록" : item.request_kind)}</span><strong>{item.title ?? `${item.request_kind === "delivery" ? "전달 기록" : item.request_kind} · ${item.status}`}</strong></div>
            <p>{item.summary ?? `${item.resource_type} · ${item.resource_id}`}</p>
            {mode === "inbox" && item.request_kind === "delivery" && <small>실제 외부 송신 없음</small>}
            <small>{item.created_at ?? item.due_at ?? "기한 없음"} · Trace {item.trace_id ?? "소유 Domain Audit 연결"}</small>
            <div className="operations-actions">
              {mode === "notifications" && <button type="button" disabled={reading || readUncertain || item.read_state === "read"} onClick={() => void markRead(item)}>{item.read_state === "read" ? "읽음" : "읽음 처리"}</button>}
              {link ? <a href={link}>원 요청 열기</a> : <span className="operations-visible-warning">CURRENT_ACCESS_DENIED</span>}
            </div>
          </article>;
        })}</div>
      </section></main>}
    {mode === "inbox" && <nav className="operations-actions" aria-label="전달함 페이지">
      <button type="button" disabled={!ownSnapshot || !snapshot.cursor || viewState === "loading" || viewState === "forbidden"}
        onClick={() => openInboxPage(null)}>첫 페이지</button>
      <button type="button" disabled={viewState !== "ready" || !snapshot.nextCursor}
        onClick={() => openInboxPage(snapshot.nextCursor)}>다음 페이지</button>
    </nav>}
    <span hidden>{VIEW_STATES.join(" ")}</span>
  </div>;
}
