"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { recoveryApi } from "../../lib/recovery-api.js";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const BACKUP_STATES = new Set(["queued", "capturing", "verifying", "ready", "failed", "expired"]);
const RESTORE_STATES = new Set(["requested", "preview_ready", "authorized", "restoring", "verifying", "completed", "cancelled", "failed", "blocked"]);
const MESSAGES = {
  loading: "현재 세션과 복구 대상을 확인하고 있습니다.",
  invalid: "올바른 복구 대상 주소가 아닙니다.",
  unauthenticated: "로그인을 다시 확인해 주세요.",
  forbidden: "현재 권한으로 이 복구 대상에 접근할 수 없습니다.",
  notFound: "복구 대상을 찾을 수 없습니다.",
  unavailable: "복구 정보를 확인할 수 없습니다. 잠시 후 다시 확인해 주세요.",
};
const safeId = value => typeof value === "string" && !/[\s\u0000-\u001f\u007f]/u.test(value) && SAFE_ID.test(value);

function selection(search, hash) {
  if (hash) return false;
  if (!search) return null;
  const entries = [...new URLSearchParams(search)];
  if (entries.length !== 1) return false;
  const [key, id] = entries[0];
  if (!["backup_id", "restore_request_id"].includes(key) || !safeId(id)) return false;
  // 허용 ID의 원값 또는 표준 인코딩만 받고 임의 인코딩 별칭은 거부한다.
  if (search !== `?${key}=${id}` && search !== `?${key}=${encodeURIComponent(id)}`) return false;
  return { key, id };
}

function sessionCore(response) {
  const data = response?.payload?.data;
  const core = [data?.user_id, data?.tenant_id, data?.workspace_id, data?.session_id];
  return core.every(safeId) ? core : null;
}

function projectedTarget(response, target, tenantId) {
  const data = response?.payload?.data;
  const states = target.key === "backup_id" ? BACKUP_STATES : RESTORE_STATES;
  const id = target.key === "backup_id" ? data?.backup_id : data?.request_id;
  if (id !== target.id || data?.tenant_id !== tenantId || !safeId(data?.workspace_id)
    || !states.has(data?.state) || !Number.isSafeInteger(data?.version) || data.version < 1) return null;
  // 목적지·원문·manifest·추가 응답 필드는 표시 상태에 보존하지 않는다.
  return { id, tenantId, workspaceId: data.workspace_id, state: data.state, version: data.version };
}

const COMPONENT_NAMES = { provider: "Provider 설정", api: "API", storage: "DB·Object Storage", sync: "동기화", queue: "작업 Queue" };
const STATUS_NAMES = { ready: "준비", warning: "주의", error: "오류" };
const errorState = error => error?.status === 401 ? "unauthenticated" : error?.status === 403 ? "forbidden"
  : error?.status === 404 ? "notFound" : "unavailable";

function projectedBackups(response, core) {
  const rows = response?.payload?.data;
  if (!Array.isArray(rows)) return null;
  const seen = new Set(), items = [];
  for (const row of rows) {
    if (!safeId(row?.backup_id) || seen.has(row.backup_id) || row.workspace_id !== core[2]) return null;
    const item = projectedTarget({ payload: { data: row } }, { key: "backup_id", id: row.backup_id }, core[1]);
    if (!item) return null;
    seen.add(item.id); items.push(item);
  }
  return items;
}

export function RecoveryOperationsWorkspace({ api = recoveryApi } = {}) {
  const targetRef = useRef(null);
  const generation = useRef(0);
  const paused = useRef(false);
  const refreshRef = useRef(null);
  const scopeRef = useRef({ api, active: false });
  if (scopeRef.current.api !== api) scopeRef.current = { api, active: false };
  const scope = scopeRef.current;
  const [loaded, setLoaded] = useState({ ownerApi: api, state: "none", target: null });
  const address = typeof window === "undefined" ? "" : window.location.search + window.location.hash;
  const own = loaded.ownerApi === api && loaded.scope === scope && loaded.address === address;
  const ready = own && ["ready", "home"].includes(loaded.state) && loaded.epoch === generation.current && !paused.current;
  const state = own ? loaded.state : loaded.state === "none" ? "none" : "loading";

  useEffect(() => {
    scope.active = true;
    const hide = () => {
      if (targetRef.current) { targetRef.current.hidden = true; targetRef.current.inert = true; }
    };
    const conceal = () => {
      paused.current = true;
      ++generation.current;
      hide();
      setLoaded({ ownerApi: api, scope, state: "loading", target: null });
    };
    const refresh = async () => {
      if (document.visibilityState === "hidden") { conceal(); return; }
      paused.current = false;
      const epoch = ++generation.current;
      hide();
      const search = window.location.search, hash = window.location.hash;
      const currentAddress = search + hash;
      const current = () => scope.active && scopeRef.current === scope && generation.current === epoch
        && !paused.current && window.location.search + window.location.hash === currentAddress;
      const publish = (nextState, target = null, home = null) => {
        if (current()) setLoaded({ ownerApi: api, scope, address: currentAddress, epoch, state: nextState, target, home });
      };
      const target = selection(search, hash);
      if (target === false) { publish("invalid"); return; }
      publish("loading");
      try {
        const before = sessionCore(await api.getSession());
        if (!current()) return;
        if (!before) { publish("unavailable"); return; }
        // 실제 search/hash가 모두 없는 홈만 조회한다. 초기 state로 주소를 추정하지 않는다.
        if (target === null) {
          const [status, backups] = await Promise.allSettled([
            Promise.resolve().then(() => api.getOperationsStatus(before[2])),
            Promise.resolve().then(() => api.listBackups(before[2])),
          ]);
          if (!current()) return;
          const after = sessionCore(await api.getSession());
          if (!current()) return;
          if (!after || !before.every((value, index) => value === after[index])) { publish("forbidden"); return; }
          // 인증 거부는 한 영역만의 장애로 취급해 다른 본문을 남기지 않는다.
          if ([status, backups].some(result => result.status === "rejected" && result.reason?.status === 401)) {
            publish("unauthenticated"); return;
          }
          const data = status.status === "fulfilled" && status.value?.workspace_id === before[2] ? status.value : null;
          const items = backups.status === "fulfilled" ? projectedBackups(backups.value, before) : null;
          publish("home", null, {
            status: data, statusError: data ? null : errorState(status.reason),
            items, backupsError: items ? null : errorState(backups.reason),
          });
          return;
        }
        // 대상 Workspace의 현재 POLICY_MANAGE 판정은 기존 서버 GET에 맡긴다.
        const response = await (target.key === "backup_id" ? api.getBackup(target.id) : api.getRestore(target.id));
        if (!current()) return;
        const item = projectedTarget(response, target, before[1]);
        if (!item) { publish("unavailable"); return; }
        const after = sessionCore(await api.getSession());
        if (!current()) return;
        if (!after || !before.every((value, index) => value === after[index])) { publish("forbidden"); return; }
        publish("ready", item);
      } catch (error) {
        publish(errorState(error));
      }
    };
    const onRefresh = () => { void refresh(); };
    refreshRef.current = onRefresh;
    const visibility = () => { if (document.visibilityState === "hidden") conceal(); else onRefresh(); };
    window.addEventListener("popstate", onRefresh);
    window.addEventListener("pagehide", conceal);
    window.addEventListener("pageshow", onRefresh);
    window.addEventListener("focus", onRefresh);
    document.addEventListener("visibilitychange", visibility);
    onRefresh();
    return () => {
      scope.active = false;
      if (refreshRef.current === onRefresh) refreshRef.current = null;
      ++generation.current;
      hide();
      window.removeEventListener("popstate", onRefresh);
      window.removeEventListener("pagehide", conceal);
      window.removeEventListener("pageshow", onRefresh);
      window.removeEventListener("focus", onRefresh);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [api, scope]);

  useLayoutEffect(() => {
    if (targetRef.current) { targetRef.current.hidden = !ready; targetRef.current.inert = !ready; }
  }, [ready, loaded]);

  return (
    <main className="operations-shell" aria-labelledby="operations-safe-title">
      <h1 id="operations-safe-title">운영 상태·복구</h1>
      {!ready && <p className="operations-visible-warning" role="status">{MESSAGES[state] ?? MESSAGES.loading}</p>}
      {state !== "invalid" && <button type="button" disabled={state === "loading" || state === "none"}
        onClick={() => refreshRef.current?.()}>다시 조회</button>}
      <section ref={targetRef} className="operations-card" hidden={!ready} inert={!ready}
        aria-label={state === "home" ? "현재 운영 상태와 백업 목록" : "선택한 복구 대상"}>
        {ready && state === "home" && <>
          <p>현재 화면은 상태·백업 조회를 지원합니다. 백업 생성·복구 실행은 아직 연결되지 않았습니다.</p>
          <h2>현재 Workspace 운영 상태</h2>
          {loaded.home.status ? <>
            <p>전체 상태: {STATUS_NAMES[loaded.home.status.overall_status]} · 확인 시각: {loaded.home.status.checked_at}</p>
            <ul>{loaded.home.status.components.map(item => <li key={item.component_id}>
              {COMPONENT_NAMES[item.component_id]} · {STATUS_NAMES[item.status]} · {item.safe_code} · 대기 {item.pending_count}건
            </li>)}</ul>
            <p>Provider는 설정 상태이며 모델 실제 호출 결과가 아닙니다. 대기 건수는 실패 건수가 아닙니다.</p>
          </> : <p role="status">운영 상태: {MESSAGES[loaded.home.statusError]}</p>}
          <h2>백업 목록</h2>
          {loaded.home.items === null ? <p role="status">백업 목록: {MESSAGES[loaded.home.backupsError]}</p>
            : loaded.home.items.length === 0 ? <p role="status">조회된 백업이 없습니다.</p>
              : <ul>{loaded.home.items.map(item => <li key={item.id}>
                <a href={`?backup_id=${encodeURIComponent(item.id)}`}>{item.id}</a> · {item.state} · 버전 {item.version}
              </li>)}</ul>}
        </>}
        {ready && state === "ready" && <dl>
          <dt>대상 ID</dt><dd>{loaded.target.id}</dd>
          <dt>Tenant</dt><dd>{loaded.target.tenantId}</dd>
          <dt>Workspace</dt><dd>{loaded.target.workspaceId}</dd>
          <dt>상태</dt><dd>{loaded.target.state}</dd>
          <dt>버전</dt><dd>{loaded.target.version}</dd>
        </dl>}
      </section>
    </main>
  );
}
