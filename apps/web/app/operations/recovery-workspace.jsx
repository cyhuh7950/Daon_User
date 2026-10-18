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
  if (!search) return null;
  if (hash) return false;
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

export function RecoveryOperationsWorkspace({ api = recoveryApi } = {}) {
  const targetRef = useRef(null);
  const generation = useRef(0);
  const paused = useRef(false);
  const scopeRef = useRef({ api, active: false });
  if (scopeRef.current.api !== api) scopeRef.current = { api, active: false };
  const scope = scopeRef.current;
  const [loaded, setLoaded] = useState({ ownerApi: api, state: "none", target: null });
  const address = typeof window === "undefined" ? "" : window.location.search + window.location.hash;
  const own = loaded.ownerApi === api && loaded.scope === scope && loaded.address === address;
  const ready = own && loaded.state === "ready" && loaded.epoch === generation.current && !paused.current;
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
      const publish = (nextState, target = null) => {
        if (current()) setLoaded({ ownerApi: api, scope, address: currentAddress, epoch, state: nextState, target });
      };
      const target = selection(search, hash);
      if (!target) { publish(target === null ? "none" : "invalid"); return; }
      publish("loading");
      try {
        const before = sessionCore(await api.getSession());
        if (!current()) return;
        if (!before) { publish("unavailable"); return; }
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
        const next = error?.status === 401 ? "unauthenticated" : error?.status === 403 ? "forbidden"
          : error?.status === 404 ? "notFound" : "unavailable";
        publish(next);
      }
    };
    const onRefresh = () => { void refresh(); };
    const visibility = () => { if (document.visibilityState === "hidden") conceal(); else onRefresh(); };
    window.addEventListener("popstate", onRefresh);
    window.addEventListener("pagehide", conceal);
    window.addEventListener("pageshow", onRefresh);
    window.addEventListener("focus", onRefresh);
    document.addEventListener("visibilitychange", visibility);
    onRefresh();
    return () => {
      scope.active = false;
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
      {state === "none" ? <>
        <p className="operations-visible-warning" role="status">RESOURCE_UNAVAILABLE</p>
        <p>실제 관리자 운영 기능은 후속 Stage C 권한·연결 Gate 완료 후 제공됩니다.</p>
      </> : state !== "ready" && <p className="operations-visible-warning" role="status">{MESSAGES[state] ?? MESSAGES.loading}</p>}
      <section ref={targetRef} className="operations-card" hidden={!ready} inert={!ready} aria-label="선택한 복구 대상">
        {ready && <dl>
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
