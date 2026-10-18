"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { recoveryApi } from "../../lib/recovery-api.js";
import { secureUuid } from "@daon-user/ui/secure-uuid";

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;
const BACKUP_STATES = new Set(["queued", "capturing", "verifying", "ready", "failed", "expired"]);
const RESTORE_STATES = new Set(["requested", "preview_ready", "authorized", "restoring", "verifying", "completed", "cancelled", "failed", "blocked"]);
const CANCEL_STATES = new Set(["requested", "preview_ready", "authorized"]);
const EXCLUSION_NAMES = { purged: "영구 삭제 대상 제외", held: "보존 대상 제외", tombstoned: "삭제 표시 대상 제외" };
const MESSAGES = {
  loading: "현재 세션과 복구 대상을 확인하고 있습니다.",
  invalid: "올바른 복구 대상 주소가 아닙니다.",
  unauthenticated: "로그인을 다시 확인해 주세요.",
  forbidden: "현재 권한으로 이 복구 대상에 접근할 수 없습니다.",
  notFound: "복구 대상을 찾을 수 없습니다.",
  unavailable: "복구 정보를 확인할 수 없습니다. 잠시 후 다시 확인해 주세요.",
  recheck: "대상 또는 처리 결과를 확정할 수 없습니다. 다시 조회로 정확한 요청 상태를 확인해 주세요. 자동으로 재전송하지 않습니다.",
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

function actionSession(response) {
  const core = sessionCore(response), data = response?.payload?.data;
  if (!core || !safeId(data.device_id) || !Array.isArray(data.recovery_operations)
    || !data.recovery_operations.every(safeId)) return null;
  return { identity: [...core, data.device_id], operations: [...data.recovery_operations] };
}
const sameSession = (left, right) => left && right && left.identity.every((v, i) => v === right.identity[i]);
// primary Workspace의 projection으로 다른 Workspace의 서버 GET 허용을 덮어쓰지 않는다.
const permits = (session, item, kind) => !!session && (session.identity[2] !== item.workspaceId
  || session.operations.includes(kind === "execute" ? "cloud_restore_execute" : "cloud_restore_cancel"));
// 비교 자료는 DOM·표시 state와 분리하며 projection의 수명 밖에는 남기지 않는다.
const previewComparisons = new WeakMap();

function serverTimestamp(value) {
  if (typeof value !== "string" || /\s/u.test(value)) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/u.exec(value);
  if (!match) return false;
  const [year, month, day, hour, minute, second, offsetHour, offsetMinute] = match.slice(1).map(part => Number(part ?? 0));
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]
    && hour < 24 && minute < 60 && second < 60 && offsetHour < 24 && offsetMinute < 60
    && Number.isFinite(Date.parse(value));
}

function projectedPreview(preview) {
  if (!preview || !Number.isSafeInteger(preview.version) || preview.version < 1) return null;
  const destination = preview.destination;
  if (!destination || typeof destination !== "object" || Array.isArray(destination) || !serverTimestamp(preview.created_at)) return null;
  const destinationIds = [destination.tenant_id, destination.workspace_id, destination.database_id, destination.bucket_id];
  if (!destinationIds.every(safeId)) return null;
  const included = preview.included_object_ids, excluded = preview.excluded_object_ids, reasons = preview.exclusion_reasons;
  if (![included, excluded, reasons].every(Array.isArray) || !included.every(safeId) || !excluded.every(safeId)) return null;
  const all = [...included, ...excluded], seen = new Set(), counts = { purged: 0, held: 0, tombstoned: 0 };
  if (new Set(all).size !== all.length || reasons.length !== excluded.length) return null;
  const excludedSet = new Set(excluded);
  for (const pair of reasons) {
    if (!Array.isArray(pair) || pair.length !== 2 || !excludedSet.has(pair[0]) || seen.has(pair[0])
      || typeof pair[1] !== "string" || !Object.hasOwn(counts, pair[1])) return null;
    seen.add(pair[0]); ++counts[pair[1]];
  }
  const projection = { version: preview.version, includedCount: included.length, excludedCount: excluded.length, counts };
  // 알려진 DTO 값만 비교하며 미정의 추가 필드는 읽거나 복사하지 않는다.
  previewComparisons.set(projection, JSON.stringify([preview.version, included, excluded, reasons, ...destinationIds, preview.created_at]));
  return projection;
}

function projectedTarget(response, target, tenantId) {
  const data = response?.payload?.data;
  const states = target.key === "backup_id" ? BACKUP_STATES : RESTORE_STATES;
  const id = target.key === "backup_id" ? data?.backup_id : data?.request_id;
  if (id !== target.id || data?.tenant_id !== tenantId || !safeId(data?.workspace_id)
    || !states.has(data?.state) || !Number.isSafeInteger(data?.version) || data.version < 1) return null;
  const item = { id, tenantId, workspaceId: data.workspace_id, state: data.state, version: data.version };
  if (target.key === "restore_request_id") {
    const preview = projectedPreview(data.preview);
    if (!preview || (data.verification_digest != null && (typeof data.verification_digest !== "string"
      || !/^[a-f0-9]{64}$/iu.test(data.verification_digest)))) return null;
    return { ...item, preview, verified: !!data.verification_digest,
      etag: response.etag === `"restore:${id}:${data.version}"` ? response.etag : null };
  }
  // 목적지·원문·manifest·추가 응답 필드는 표시 상태에 보존하지 않는다.
  return item;
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
  const passwordRef = useRef(null);
  const bindPassword = useCallback(node => {
    // API 교체·unmount 시 passive cleanup보다 먼저 분리되는 입력 노드도 비운다.
    if (passwordRef.current && passwordRef.current !== node) passwordRef.current.value = "";
    passwordRef.current = node;
  }, []);
  const actionRef = useRef(null);
  const [confirmation, setConfirmation] = useState(null);
  const [busy, setBusy] = useState(false);
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
      if (passwordRef.current) passwordRef.current.value = "";
      if (actionRef.current) { actionRef.current.password = ""; actionRef.current.token = ""; actionRef.current.controller.abort(); }
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
        const beforeResponse = await api.getSession();
        const before = sessionCore(beforeResponse);
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
        const afterResponse = await api.getSession();
        const after = sessionCore(afterResponse);
        if (!current()) return;
        if (!after || !before.every((value, index) => value === after[index])) { publish("forbidden"); return; }
        const auth = actionSession(afterResponse);
        // 기기 변경 역시 쓰기 권한을 남기지 않는다. 기존 GET의 core 계약은 유지한다.
        item.actionSession = sameSession(actionSession(beforeResponse), auth) ? auth : null;
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
    if (!ready && passwordRef.current) passwordRef.current.value = "";
    if (targetRef.current) { targetRef.current.hidden = !ready; targetRef.current.inert = !ready; }
  }, [ready, loaded]);

  const canAct = kind => ready && state === "ready" && loaded.target?.etag
    && permits(loaded.target.actionSession, loaded.target, kind)
    && (kind === "execute" ? loaded.target.state === "preview_ready" : CANCEL_STATES.has(loaded.target.state));

  const mutate = async kind => {
    if (actionRef.current || confirmation?.owner !== loaded || confirmation.kind !== kind || !canAct(kind)) return;
    let password = passwordRef.current?.value ?? "";
    if (passwordRef.current) passwordRef.current.value = "";
    if (kind === "execute" && (password.length < 12 || password.length > 1024)) {
      setConfirmation(null); setLoaded({ ...loaded, state: "recheck", target: null }); return;
    }
    const operation = { controller: new AbortController(), password, token: "" };
    password = "";
    actionRef.current = operation; setBusy(true);
    const original = loaded.target, auth = original.actionSession;
    const options = { signal: operation.controller.signal };
    const current = () => scope.active && scopeRef.current === scope && generation.current === loaded.epoch
      && !paused.current && !options.signal.aborted && window.location.search + window.location.hash === loaded.address;
    const requireCurrent = () => { if (!current()) throw new Error("STALE_RECOVERY_ACTION"); };
    const requireSession = response => {
      requireCurrent();
      const next = actionSession(response);
      if (!sameSession(auth, next) || !permits(next, original, kind)) throw new Error("RECOVERY_SESSION_CHANGED");
    };
    const exactRead = async unchanged => {
      requireSession(await api.getSession(options));
      const response = await api.getRestore(original.id, options);
      requireCurrent();
      const item = projectedTarget(response, { key: "restore_request_id", id: original.id }, original.tenantId);
      if (!item || item.workspaceId !== original.workspaceId || !item.etag || item.version < original.version) throw new Error("RECOVERY_TARGET_CHANGED");
      if (unchanged && (item.version !== original.version || item.etag !== original.etag || item.state !== original.state
        || previewComparisons.get(item.preview) !== previewComparisons.get(original.preview))) throw new Error("RECOVERY_TARGET_CHANGED");
      const sessionResponse = await api.getSession(options);
      requireSession(sessionResponse);
      item.actionSession = actionSession(sessionResponse);
      return item;
    };
    try {
      await exactRead(true);
      requireCurrent();
      if (kind === "execute") {
        const issuance = api.issueRestoreStepUp(original.id, operation.password, { ...options, idempotencyKey: secureUuid() });
        operation.password = "";
        operation.token = await issuance;
        requireCurrent();
        if (typeof operation.token !== "string" || !operation.token.trim() || operation.token.length > 4096
          || /[\u0000-\u0020\u007f]/u.test(operation.token)) throw new Error("INVALID_STEP_UP");
        // 인증 도중 세션·대상·Preview 변경도 거부하고, POST body 구성 직전에 current를 확인한다.
        await exactRead(true);
        requireCurrent();
        await api.executeRestore(original.id, { preview_version: original.preview.version, step_up_authorization_id: operation.token },
          original.etag, secureUuid(), options);
        operation.token = "";
      } else {
        await api.cancelRestore(original.id, original.etag, secureUuid(), options);
      }
      requireCurrent();
      // POST 응답은 완료 증거가 아니다. 정확 GET과 전후 동일 세션으로만 결과를 게시한다.
      const item = await exactRead(false);
      requireCurrent();
      setLoaded({ ...loaded, target: item });
    } catch (error) {
      if (current()) setLoaded({ ...loaded, state: [401, 403].includes(error?.status) ? errorState(error) : "recheck", target: null });
    } finally {
      operation.password = ""; operation.token = "";
      if (actionRef.current === operation) { actionRef.current = null; setBusy(false); }
      setConfirmation(value => value?.owner === loaded ? null : value);
    }
  };

  return (
    <main className="operations-shell" aria-labelledby="operations-safe-title">
      <h1 id="operations-safe-title">운영 상태·복구</h1>
      {!ready && <p className="operations-visible-warning" role="status">{MESSAGES[state] ?? MESSAGES.loading}</p>}
      {state !== "invalid" && <button type="button" disabled={state === "loading" || state === "none"}
        onClick={() => refreshRef.current?.()}>다시 조회</button>}
      <section ref={targetRef} className="operations-card" hidden={!ready} inert={!ready}
        aria-label={state === "home" ? "현재 운영 상태와 백업 목록" : "선택한 복구 대상"}>
        {ready && state === "home" && <>
          <p>백업 생성·새 Preview 생성은 아직 연결되지 않았습니다. 기존 저장 요청은 정확 ID 상세에서 제한형 Execute·Cancel을 사용할 수 있습니다. 독립 전체 DB 복원은 지원하지 않습니다.</p>
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
        {ready && state === "ready" && <><dl>
          <dt>대상 ID</dt><dd>{loaded.target.id}</dd>
          <dt>Tenant</dt><dd>{loaded.target.tenantId}</dd>
          <dt>Workspace</dt><dd>{loaded.target.workspaceId}</dd>
          <dt>상태</dt><dd>{loaded.target.state}</dd>
          <dt>버전</dt><dd>{loaded.target.version}</dd>
        </dl>
          {loaded.target.preview && <>
            <h2>기존 안전 Preview 결과</h2>
            <p>Preview 버전 {loaded.target.preview.version} · 포함 {loaded.target.preview.includedCount}건 · 제외 {loaded.target.preview.excludedCount}건</p>
            <ul>{Object.entries(EXCLUSION_NAMES).map(([reason, label]) => <li key={reason}>{label}: {loaded.target.preview.counts[reason]}건</li>)}</ul>
            <p>서버 검증 기록: {loaded.target.verified ? "있음" : "없음"}</p>
            <p>completed는 서버 요청 처리 상태이며 전체 DB 복원 성공을 의미하지 않습니다. 독립 전체 DB 복원은 지원하지 않습니다.</p>
          </>}
          {canAct("execute") && <button type="button" disabled={busy} onClick={() => {
            if (passwordRef.current) passwordRef.current.value = "";
            setConfirmation({ owner: loaded, kind: "execute" });
          }}>복구 실행 준비</button>}
          {canAct("cancel") && <button type="button" disabled={busy} onClick={() => {
            if (passwordRef.current) passwordRef.current.value = "";
            setConfirmation({ owner: loaded, kind: "cancel" });
          }}>복구 요청 취소</button>}
          {confirmation?.owner === loaded && confirmation.kind === "execute" && canAct("execute") && <form onSubmit={event => {
            event.preventDefault(); void mutate("execute");
          }}>
            <p>기존 Preview를 확인했습니다. 새 비밀번호 인증 후 이 요청을 별도로 실행합니다.</p>
            <label>현재 비밀번호<input ref={bindPassword} type="password" autoComplete="current-password" minLength={12} maxLength={1024} required disabled={busy} /></label>
            <button type="submit" disabled={busy}>인증 후 실행 확정</button>
          </form>}
          {confirmation?.owner === loaded && confirmation.kind === "cancel" && canAct("cancel") && <div>
            <p>이 복구 요청을 취소하시겠습니까?</p>
            <button type="button" disabled={busy} onClick={() => { void mutate("cancel"); }}>취소 확정</button>
          </div>}
          {confirmation?.owner === loaded && <button type="button" disabled={busy} onClick={() => {
            if (passwordRef.current) passwordRef.current.value = "";
            setConfirmation(null);
          }}>확인 닫기</button>}
          {busy && <p role="status">요청을 확인하고 있습니다. 중복 전송하지 마세요.</p>}
        </>}
      </section>
    </main>
  );
}
