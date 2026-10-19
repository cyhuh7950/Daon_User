"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { recoveryApi, parseSystemOperationsStatus } from "../../lib/recovery-api.js";
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

function systemSession(response) {
  const d=response?.payload?.data;
  if(!safeId(d?.user_id)||!safeId(d?.session_id)||d.is_system_admin!==true
    ||![null,undefined,""].includes(d.workspace_id)
    ||[d.tenant_id,d.device_id].some(v=>v!=null&&v!==""&&!safeId(v)))return null;
  return [d.user_id,d.session_id,d.tenant_id??null,d.device_id??null];
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
  || session.operations.includes(kind === "execute" ? "cloud_restore_execute" : kind === "preview" ? "cloud_restore_preview" : "cloud_restore_cancel"));
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
  if (Object.hasOwn(preview,"kind")) {
    const keys=["kind","version","created_at","expires_at","destination","source_manifest_digest","closure_fingerprint","included_root_count","excluded_root_count","exclusion_counts"];
    const summary=destinationSummary(destination);
    const counts=preview.exclusion_counts;
    if (Object.keys(preview).length!==keys.length || !keys.every(k=>Object.hasOwn(preview,k)) || preview.kind!=="selected_source_lineage" || !summary
      || !serverTimestamp(preview.expires_at) || Date.parse(preview.expires_at)<=Date.parse(preview.created_at) || Date.parse(preview.expires_at)>Date.parse(summary.expires_at)
      || ![preview.source_manifest_digest,preview.closure_fingerprint].every(v=>typeof v==="string"&&/^[a-f0-9]{64}$/u.test(v))
      || ![preview.included_root_count,preview.excluded_root_count].every(v=>Number.isSafeInteger(v)&&v>=0&&v<=100)
      || preview.included_root_count<1 || preview.included_root_count+preview.excluded_root_count>100
      || !counts || Object.keys(counts).length!==3 || !Object.keys(EXCLUSION_NAMES).every(k=>Number.isSafeInteger(counts[k])&&counts[k]>=0&&counts[k]<=100)
      || Object.values(counts).reduce((a,b)=>a+b,0)!==preview.excluded_root_count) return null;
    const projection={version:preview.version,kind:preview.kind,includedCount:preview.included_root_count,excludedCount:preview.excluded_root_count,
      counts:{purged:counts.purged,held:counts.held,tombstoned:counts.tombstoned},destination:summary,expiresAt:preview.expires_at};
    previewComparisons.set(projection,JSON.stringify([preview.kind,preview.version,preview.created_at,preview.expires_at,summary,
      preview.source_manifest_digest,preview.closure_fingerprint,preview.included_root_count,preview.excluded_root_count,projection.counts]));
    return projection;
  }
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
  return {...item,etag:response.etag===`"backup:${id}:${data.version}"`?response.etag:null};
}

function destinationSummary(item) {
  if (!item || typeof item!=="object" || Array.isArray(item) || Object.keys(item).length!==4
    || !["destination_id","version","display_name","expires_at"].every(k=>Object.hasOwn(item,k))
    || !safeId(item.destination_id) || !Number.isSafeInteger(item.version) || item.version<1 || item.version>2147483647
    || typeof item.display_name!=="string" || !item.display_name.trim() || item.display_name.length>80 || /[<>:/\\\u0000-\u001f\u007f]/u.test(item.display_name)
    || !serverTimestamp(item.expires_at)) return null;
  return {destination_id:item.destination_id,version:item.version,display_name:item.display_name,expires_at:item.expires_at};
}

function destinationSummaries(response) {
  const rows=response?.payload?.data;
  if (!Array.isArray(rows)||rows.length>1000)throw new Error("INVALID_DESTINATIONS");
  const values=rows.map(destinationSummary);
  if (values.some(v=>!v)||new Set(values.map(v=>v.destination_id)).size!==values.length)throw new Error("INVALID_DESTINATIONS");
  return values;
}

function lineageSummary(response,roots) {
  const value=response?.payload?.data;
  const fields=["root_source_version_ids","dependency_source_version_ids","dependency_source_ids","source_version_count","source_count","unique_object_count","total_bytes","metadata_bytes","closure_fingerprint"];
  const canonId=v=>typeof v==="string"&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(v)&&!/[\s\u0000-\u001f\u007f]/u.test(v);
  if (!value || Object.keys(value).length!==9 || !fields.every(k=>Object.hasOwn(value,k)))throw new Error("INVALID_LINEAGE");
  for (const key of fields.slice(0,3))if(!Array.isArray(value[key])||value[key].length>100||!value[key].every(canonId)||new Set(value[key]).size!==value[key].length)throw new Error("INVALID_LINEAGE");
  if (JSON.stringify([...value.root_source_version_ids].sort())!==JSON.stringify(roots)||value.root_source_version_ids.some(v=>value.dependency_source_version_ids.includes(v))
    || !["source_version_count","source_count","unique_object_count","total_bytes","metadata_bytes"].every(k=>Number.isSafeInteger(value[k])&&value[k]>=0)
    || value.source_version_count!==roots.length+value.dependency_source_version_ids.length || value.source_version_count>100
    || value.source_count<1||value.source_count>100||value.unique_object_count<1||value.unique_object_count>100||value.total_bytes>536870912||value.metadata_bytes<1||value.metadata_bytes>33554432
    || typeof value.closure_fingerprint!=="string"||!/^[a-f0-9]{64}$/u.test(value.closure_fingerprint))throw new Error("INVALID_LINEAGE");
  return {fingerprint:value.closure_fingerprint,dependencies:value.dependency_source_version_ids.length,versions:value.source_version_count,
    objects:value.unique_object_count,bytes:value.total_bytes};
}

const COMPONENT_NAMES = { provider: "Provider 설정", api: "API", storage: "DB·Object Storage", sync: "동기화", queue: "작업 Queue" };
const STATUS_NAMES = { ready: "준비", warning: "주의", error: "오류" };
const OBSERVATION_NAMES = {
  document_pending: "문서 처리 대기", document_retry_wait: "문서 재시도 대기", document_leased: "문서 lease", document_dead_letter: "문서 처리 실패 대기",
  object_pending: "Object 대기", object_retry_wait: "Object 재시도 대기", object_leased: "Object lease", object_dead_letter: "Object 실패 대기",
  object_oldest_pending_seconds: "Object 가장 오래된 대기", source_waiting_model: "모델 대기 Source", index_records_current: "현재 결속 Index 기록",
  source_storage_bytes: "접근 가능한 원본 Object 저장량", reported_prompt_tokens: "보고된 Prompt Token", reported_completion_tokens: "보고된 Completion Token",
  reported_total_tokens: "보고된 Total Token", attempts_without_reported_usage: "usage 누락/불완전 attempt",
};
const OBSERVATION_STATES = { measured: "기록 확인", partial: "부분 기록", not_collected: "미수집", unavailable: "조회 불가" };
const OBSERVATION_UNITS = { count: "건", bytes: "bytes", tokens: "tokens", seconds: "초" };
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

function selectionNotebooks(rows) {
  if (!Array.isArray(rows) || rows.length > 500 || rows.some(item => !safeId(item?.id)
    || typeof item.title !== "string" || item.title.length < 1 || item.title.length > 120)
    || new Set(rows.map(item => item.id)).size !== rows.length) throw new Error("INVALID_SELECTION");
  return rows.map(item => ({ id: item.id, title: item.title }));
}

function selectionSources(value, notebookId) {
  if (value?.notebookId !== notebookId || typeof value.bindingEtag !== "string"
    || !/^"notebook-binding:[1-9][0-9]*"$/u.test(value.bindingEtag)
    || !Array.isArray(value.sources) || value.sources.length > 1000
    || value.sources.some(item => !safeId(item?.sourceId) || !safeId(item?.versionId)
      || (item.filename !== undefined && (typeof item.filename !== "string" || !/^[^/\\\u0000-\u001f]{1,255}$/u.test(item.filename))))
    || new Set(value.sources.map(item => item.versionId)).size !== value.sources.length) throw new Error("INVALID_SELECTION");
  return { notebookId, bindingEtag: value.bindingEtag, sources: value.sources.map(item => ({ sourceId: item.sourceId, versionId: item.versionId,
    ...(item.filename !== undefined ? { filename: item.filename } : {}) })) };
}

function BackupSelectionPane({ api, auth, scope, scopeRef, generation, paused, owner, actionRef, invalidate, onNavigate }) {
  const mounted = useRef(true);
  const [pane, setPane] = useState({ phase: "closed", notebooks: [], selection: null, ids: [] });
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; if (actionRef.current?.selectionOwner === owner) actionRef.current.controller.abort(); };
  }, [actionRef, owner]);
  const allowed = auth?.operations.includes("cloud_backup_create");
  const current = operation => mounted.current && scope.active && scopeRef.current === scope
    && generation.current === owner.epoch && !paused.current && !operation.controller.signal.aborted
    && actionRef.current === operation && window.location.search + window.location.hash === owner.address;
  const begin = post => {
    if (!allowed || (actionRef.current?.selectionPost && !actionRef.current.controller.signal.aborted)) return null;
    actionRef.current?.controller.abort();
    const operation = { controller: new AbortController(), selectionOwner: owner, selectionPost: post, password: "", token: "" };
    actionRef.current = operation;
    return operation;
  };
  const check = operation => { if (!current(operation)) throw new Error("STALE_SELECTION"); };
  const checkSession = async operation => {
    check(operation);
    const next = actionSession(await api.getSession({ signal: operation.controller.signal }));
    check(operation);
    if (!sameSession(auth, next) || !next.operations.includes("cloud_backup_create")) {
      invalidate("forbidden"); throw new Error("SELECTION_SESSION_CHANGED");
    }
  };
  const fail = (operation, error) => {
    if (!current(operation)) return;
    if ([401, 403].includes(error?.status)) { invalidate(errorState(error)); return; }
    setPane({ phase: "error", notebooks: [], selection: null, ids: [], message: operation.selectionPost
      ? error?.status === 413 ? "선택 자료의 정본 합계는 512MiB 이하여야 합니다. 자료 다시 조회 후 선택해 주세요."
        : "백업 처리 결과를 확정할 수 없습니다. 독립 보존 준비와 요청 상태를 다시 확인해 주세요. 자동으로 재전송하지 않습니다."
      : "선택 자료를 확인할 수 없습니다. 자료 다시 조회 후 선택해 주세요." });
  };
  const finish = operation => { if (actionRef.current === operation) actionRef.current = null; };
  const load = async notebookId => {
    const operation = begin(false); if (!operation) return;
    setPane({ phase: "loading", notebooks: [], selection: null, ids: [] });
    try {
      await checkSession(operation);
      const notebooks = selectionNotebooks(await api.listSelectionNotebooks(auth.identity[2], { signal: operation.controller.signal }));
      check(operation);
      if (notebookId !== undefined && !notebooks.some(item => item.id === notebookId)) throw new Error("NOTEBOOK_CHANGED");
      const selected = notebookId === undefined ? null : selectionSources(await api.getSelectionSources(auth.identity[2], notebookId,
        { signal: operation.controller.signal }), notebookId);
      await checkSession(operation);
      setPane({ phase: "ready", notebooks, selection: selected, ids: [] });
    } catch (error) { fail(operation, error); } finally { finish(operation); }
  };
  const submit = async () => {
    if (pane.phase !== "ready" || !pane.selection || pane.ids.length < 1 || pane.ids.length > 100) return;
    const operation = begin(true); if (!operation) return;
    const selected = pane.selection, ids = [...pane.ids].sort();
    setPane(value => ({ ...value, phase: "posting" }));
    try {
      await checkSession(operation);
      const notebooks = selectionNotebooks(await api.listSelectionNotebooks(auth.identity[2], { signal: operation.controller.signal }));
      check(operation);
      if (!notebooks.some(item => item.id === selected.notebookId)) throw new Error("NOTEBOOK_CHANGED");
      const fresh = selectionSources(await api.getSelectionSources(auth.identity[2], selected.notebookId,
        { signal: operation.controller.signal }), selected.notebookId);
      check(operation);
      if (fresh.bindingEtag !== selected.bindingEtag || ids.some(id => {
        const original = selected.sources.find(item => item.versionId === id);
        return !original || !fresh.sources.some(item => item.versionId === id && item.sourceId === original.sourceId);
      })) throw new Error("SELECTION_CHANGED");
      await checkSession(operation);
      check(operation);
      await api.createBackupSelection({ workspace_id: auth.identity[2], notebook_id: selected.notebookId,
        trigger: "manual", source_version_ids: ids }, secureUuid(), { signal: operation.controller.signal });
      await checkSession(operation);
      // 201/ready receipt만으로 보존 성공을 만들거나 새 목록 행을 합성하지 않는다.
      setPane({ phase: "error", notebooks: [], selection: null, ids: [], message:
        "요청 응답을 받았지만 독립 보존 완료는 확인되지 않았습니다. 기존 백업 목록에서 다시 확인해 주세요." });
    } catch (error) { fail(operation, error); } finally { finish(operation); }
  };
  const lineage = async create => {
    if (pane.phase!=="ready"||!pane.selection||!pane.ids.length||pane.ids.length>100||(create&&(!pane.lineage||!pane.consent)))return;
    const operation=begin(create);if(!operation)return;
    const selected=pane.selection,ids=[...pane.ids].sort(),prior=pane.lineage;
    setPane(v=>({...v,phase:create?"posting":"loading",consent:false}));
    try{
      await checkSession(operation);
      const fresh=selectionSources(await api.getSelectionSources(auth.identity[2],selected.notebookId,{signal:operation.controller.signal}),selected.notebookId);
      check(operation);
      if(fresh.bindingEtag!==selected.bindingEtag||ids.some(id=>!fresh.sources.some(s=>s.versionId===id&&selected.sources.some(old=>old.versionId===id&&old.sourceId===s.sourceId))))throw new Error("SELECTION_CHANGED");
      const input={workspace_id:auth.identity[2],notebook_id:selected.notebookId,source_version_ids:ids,lineage_mode:"exact"};
      const summary=lineageSummary(await api.previewBackupSelection(input,{signal:operation.controller.signal}),ids);
      await checkSession(operation);check(operation);
      if(!create){setPane(v=>({...v,phase:"ready",lineage:summary,consent:false}));return;}
      if(summary.fingerprint!==prior.fingerprint)throw new Error("LINEAGE_CHANGED");
      const result=await api.createLineageBackup({...input,trigger:"manual",closure_consent:{include_prior_versions:true,closure_fingerprint:prior.fingerprint}},secureUuid(),{signal:operation.controller.signal});
      await checkSession(operation);check(operation);
      const id=result?.payload?.data?.backup_id;if(!safeId(id))throw new Error("BACKUP_UNCERTAIN");
      const detail=await api.getBackup(id,{signal:operation.controller.signal});await checkSession(operation);check(operation);
      const actual=projectedTarget(detail,{key:"backup_id",id},auth.identity[1]);
      if(!actual||actual.workspaceId!==auth.identity[2]||actual.state!=="ready")throw new Error("BACKUP_UNCERTAIN");
      onNavigate(`?backup_id=${encodeURIComponent(id)}`);
    }catch(error){fail(operation,error);}finally{finish(operation);}
  };
  if (!allowed) return null;
  if (pane.phase === "closed") return <button type="button" onClick={() => { void load(); }}>백업 자료 선택</button>;
  return <div aria-label="백업 자료 선택">
    <p>실제 Notebook과 현재 연결된 SourceVersion 1~100개만 선택합니다. 최대 정본 합계는 512MiB이며 서버가 해석합니다. 독립 보존 준비 전에는 생성되지 않습니다.</p>
    {pane.message && <p role="status">{pane.message}</p>}
    {["loading", "posting"].includes(pane.phase) && <p role="status">현재 세션과 선택 자료를 확인하고 있습니다.</p>}
    {pane.phase !== "posting" && <button type="button" onClick={() => { void load(); }}>자료 다시 조회</button>}
    {pane.phase === "ready" && pane.notebooks.length === 0 && <p>선택할 Notebook이 없습니다.</p>}
    {pane.notebooks.map(item => <button type="button" key={item.id} disabled={pane.phase !== "ready"}
      onClick={() => { void load(item.id); }}><span>{[...item.title].slice(0, 60).join("")}{[...item.title].length > 60 ? "…" : ""}</span><small> · {item.id}</small></button>)}
    {pane.selection && <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <p>Notebook {pane.selection.notebookId} · 선택 {pane.ids.length}건</p>
      <p>파일명이 없는 자료는 SourceVersion ID로 구분합니다.</p>
      {pane.selection.sources.length === 0 && <p>현재 연결된 SourceVersion이 없습니다.</p>}
      {pane.selection.sources.map(item => <button type="button" key={item.versionId} aria-pressed={pane.ids.includes(item.versionId)}
        disabled={pane.phase !== "ready"} onClick={() => setPane(value => ({ ...value,
          ids: value.ids.includes(item.versionId) ? value.ids.filter(id => id !== item.versionId) : [...value.ids, item.versionId],lineage:null,consent:false }))}>
        {item.filename ? <><span>{[...item.filename].slice(0, 60).join("")}{[...item.filename].length > 60 ? "…" : ""}</span><small> · SourceVersion {item.versionId}</small></>
          : <>SourceVersion {item.versionId}</>}</button>)}
      <button type="submit" disabled={pane.phase !== "ready" || pane.ids.length < 1 || pane.ids.length > 100}>선택 자료 백업 요청</button>
      {typeof api.previewBackupSelection==="function"&&<button type="button" disabled={pane.phase!=="ready"||!pane.ids.length||pane.ids.length>100} onClick={()=>{void lineage(false);}}>계보 사전확인</button>}
      {pane.lineage&&<>
        <p>선행 Version {pane.lineage.dependencies}개 · 전체 Version {pane.lineage.versions}개 · 고유 Object {pane.lineage.objects}개 · {pane.lineage.bytes} bytes</p>
        <button type="button" disabled={pane.phase!=="ready"} aria-pressed={!!pane.consent} onClick={()=>setPane(v=>({...v,consent:!v.consent}))}>선행 버전 포함에 동의</button>
        <button type="button" disabled={pane.phase!=="ready"||!pane.consent} onClick={()=>{void lineage(true);}}>계보 포함 백업 요청</button>
      </>}
    </form>}
  </div>;
}

function WorkspaceTelemetry({status}){
  const kinds={document:"문서",studio:"Studio",object:"Object"};
  const states={fresh:"최근 수신",stale:"지난 관측",measured:"집계 확인",partial:"부분 집계",not_collected:"미집계",not_reported:"미보고",unavailable:"조회 불가"};
  const metrics={cpu_quota_millicores:"CPU 할당",memory_limit_bytes:"메모리 한도",memory_current_bytes:"메모리 사용",runtime_available_bytes:"영속 저장 여유"};
  const events={manual_reprocess_pending:"수동 재처리 대기",readiness_reprocess_pending:"Readiness 재처리 대기",retry_wait:"재시도 대기",backoff_wait:"Backoff 대기",
    step_up_denied_24h:"추가 인증 거부",step_up_expired_24h:"만료 토큰 사용",access_blocked_24h:"접근 차단",access_masked_24h:"부분 마스킹",egress_denied_24h:"외부 송신 거부"};
  const value=r=>r&&["fresh","stale","measured","partial"].includes(r.state)&&Number.isSafeInteger(r.value)&&r.value>=0?r.value:null;
  const cost=status.cost_summary,deployment=status.deployment_summary;
  return <>
    <h3>Worker·자원 관측</h3>
    <ul>{(status.worker_samples??[]).map(r=><li key={r.worker_kind}>
      {kinds[r.worker_kind]} · {states[r.state]??"조회 불가"} · {{alive:"프로세스 관측",processing:"처리 중",poll_stale:"poll 지연",stopped:"종료 관측",unknown:"실행 미확인"}[r.process_state]}
      {serverTimestamp(r.observed_at)?` · ${r.observed_at}`:""}
    </li>)}</ul>
    <dl>{(status.resource_samples??[]).map(r=><div key={`${r.worker_kind}:${r.metric_id}`}><dt>{kinds[r.worker_kind]} · {metrics[r.metric_id]}</dt>
      <dd>{states[r.state]??"조회 불가"} · {value(r)===null?"값 미확인":`${r.value} ${r.unit}`}</dd></div>)}</dl>
    <h3>차단·재처리 관측 <span title="현재 접근 가능한 기록만 집계합니다. 뒤 5종은 같은 DB 시각의 직전 24시간입니다. 귀속 미확인·미구현·조회 실패는 0건으로 계산하지 않습니다.">ⓘ</span></h3>
    <dl>{Object.entries(events).map(([id,label])=>{const r=status.event_counts?.find(row=>row.metric_id===id);return <div key={id}><dt>{label}</dt>
      <dd>{states[r?.state]??"미집계"} · {value(r)===null?"값 미확인":`${r.value}건`}{serverTimestamp(r?.window_end)?` · ${r.window_start} ~ ${r.window_end}`:""}</dd></div>;})}</dl>
    <h3>비용·배포 기록</h3>
    <p>비용: {states[cost?.state]??"미집계"} · {(cost?.known_amounts??[]).map(r=>`${r.currency} ${r.amount}`).join(" / ")||"확정 금액 없음"}
      {Number.isSafeInteger(cost?.unknown_attempt_count)?` · 비용 미확인 attempt ${cost.unknown_attempt_count}건`:""}
      {Number.isSafeInteger(cost?.unconfirmed_run_count)?` · 수집 미확정 Run ${cost.unconfirmed_run_count}건`:""}</p>
    <p>배포·Rollback: {deployment?.result?`${deployment.operation} · ${deployment.result} · ${deployment.code_revision??"코드 미확인"}`:"확인된 기록 미수집"}</p>
    {(status.unimplemented??[]).length>0&&<p>미구현 수집: {status.unimplemented.map(r=>({waiting_role_producer:"역할 Readiness",readiness_event_producer:"Readiness 자동 재처리",
      retry_suppression_receipt:"중복 억제 기록",node_capacity_producer:"Node 용량",provider_capacity_reporter:"Provider 용량",deployment_receipt_writer:"배포 기록",cost_receipt_writer:"비용 기록"}[r.producer_id])).join(", ")}</p>}
  </>;
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
  const [destinations,setDestinations]=useState(null);
  const scopeRef = useRef({ api, active: false });
  if (scopeRef.current.api !== api) scopeRef.current = { api, active: false };
  const scope = scopeRef.current;
  const [metricsScope,setMetricsScope]=useState("self");
  const metricsScopeRef=useRef(metricsScope);metricsScopeRef.current=metricsScope;
  const [loaded, setLoaded] = useState({ ownerApi: api, state: "none", target: null });
  const address = typeof window === "undefined" ? "" : window.location.search + window.location.hash;
  const own = loaded.ownerApi === api && loaded.scope === scope && loaded.address === address && loaded.metricsScope===metricsScope;
  const ready = own && ["ready", "home", "system"].includes(loaded.state) && loaded.epoch === generation.current && !paused.current;
  const state = own ? loaded.state : loaded.state === "none" ? "none" : "loading";

  useEffect(() => {
    scope.active = true;
    let systemController=null;
    const hide = () => {
      systemController?.abort();systemController=null;
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
        && !paused.current && window.location.search + window.location.hash === currentAddress && metricsScopeRef.current===metricsScope;
      const publish = (nextState, target = null, home = null) => {
        if (current()) setLoaded({ ownerApi: api, scope, metricsScope, address: currentAddress, epoch, state: nextState, target, home });
      };
      const target = selection(search, hash);
      if (target === false) { publish("invalid"); return; }
      publish("loading");
      try {
        const beforeResponse = await api.getSession();
        const before = sessionCore(beforeResponse);
        if (!current()) return;
        const systemBefore=target===null&&!before?systemSession(beforeResponse):null;
        if(systemBefore){
          const controller=new AbortController();systemController=controller;
          const data=parseSystemOperationsStatus(await api.getSystemOperationsStatus({signal:controller.signal}));
          if(!current()||controller.signal.aborted)return;
          const systemAfter=systemSession(await api.getSession({signal:controller.signal}));
          if(!current()||controller.signal.aborted)return;
          if(!systemAfter||!systemBefore.every((v,i)=>v===systemAfter[i])){publish("forbidden");return;}
          publish("system",null,{status:data});return;
        }
        if (!before) { publish("unavailable"); return; }
        // 실제 search/hash가 모두 없는 홈만 조회한다. 초기 state로 주소를 추정하지 않는다.
        if (target === null) {
          if(metricsScope==="workspace_visible"&&!actionSession(beforeResponse)?.operations.includes("cloud_backup_list")){
            publish("forbidden");return;
          }
          const [status, backups] = await Promise.allSettled([
            Promise.resolve().then(() => api.getOperationsStatus(before[2],{scope:metricsScope})),
            Promise.resolve().then(() => api.listBackups(before[2])),
          ]);
          if (!current()) return;
          const afterResponse = await api.getSession();
          const after = sessionCore(afterResponse);
          if (!current()) return;
          if (!after || !before.every((value, index) => value === after[index])) { publish("forbidden"); return; }
          // 인증 거부는 한 영역만의 장애로 취급해 다른 본문을 남기지 않는다.
          if ([status, backups].some(result => result.status === "rejected" && result.reason?.status === 401)) {
            publish("unauthenticated"); return;
          }
          const data = status.status === "fulfilled" && status.value?.workspace_id === before[2] ? status.value : null;
          const items = backups.status === "fulfilled" ? projectedBackups(backups.value, before) : null;
          publish("home", null, {
            actionSession: sameSession(actionSession(beforeResponse), actionSession(afterResponse)) ? actionSession(afterResponse) : null,
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
  }, [api, scope,metricsScope]);

  useLayoutEffect(() => {
    if (!ready && passwordRef.current) passwordRef.current.value = "";
    if (targetRef.current) { targetRef.current.hidden = !ready; targetRef.current.inert = !ready; }
  }, [ready, loaded]);

  const canBrowseDestinations=()=>ready&&state==="ready"&&loaded.target?.state==="ready"&&!loaded.target.preview&&loaded.target.etag
    && permits(loaded.target.actionSession,loaded.target,"preview")&&typeof api.getRestoreDestinations==="function";
  const canAct = kind => ready && state === "ready" && loaded.target?.etag
    && permits(loaded.target.actionSession, loaded.target, kind)
    && (kind === "preview" ? canBrowseDestinations()&&destinations?.owner===loaded&&destinations.phase==="ready"&&!!destinations.selected
      : kind === "execute" ? loaded.target.state === "preview_ready"&&!!loaded.target.preview
        && (!loaded.target.preview.expiresAt||Date.parse(loaded.target.preview.expiresAt)>Date.now()) : !!loaded.target.preview&&CANCEL_STATES.has(loaded.target.state));
  const navigate=address=>{window.history.replaceState(null,"",address);refreshRef.current?.();};
  const loadDestinations=async()=>{
    if(actionRef.current||!canBrowseDestinations())return;
    const original=loaded.target,auth=original.actionSession;
    const operation={controller:new AbortController(),password:"",token:""};actionRef.current=operation;setBusy(true);
    const options={signal:operation.controller.signal};
    const current=()=>scope.active&&scopeRef.current===scope&&generation.current===loaded.epoch&&!paused.current&&!options.signal.aborted
      &&window.location.search+window.location.hash===loaded.address;
    const check=()=>{if(!current())throw new Error("STALE_DESTINATION_READ");};
    const read=async()=>{
      const before=actionSession(await api.getSession(options));check();if(!sameSession(auth,before)||!permits(before,original,"preview"))throw new Error("SESSION_CHANGED");
      const item=projectedTarget(await api.getBackup(original.id,options),{key:"backup_id",id:original.id},original.tenantId);check();
      if(!item||item.workspaceId!==original.workspaceId||item.state!==original.state||item.version!==original.version||item.etag!==original.etag)throw new Error("BACKUP_CHANGED");
      const after=actionSession(await api.getSession(options));check();if(!sameSession(auth,after)||!permits(after,original,"preview"))throw new Error("SESSION_CHANGED");
    };
    setDestinations({owner:loaded,phase:"loading",items:[],selected:null});
    try{
      await read();const items=destinationSummaries(await api.getRestoreDestinations(original.id,options));check();await read();
      setDestinations({owner:loaded,phase:"ready",items,selected:null});
    }catch(error){if(current()){
      setDestinations({owner:loaded,phase:"error",items:[],selected:null});
      if([401,403].includes(error?.status))setLoaded({...loaded,state:errorState(error),target:null});
    }}finally{operation.password="";operation.token="";if(actionRef.current===operation){actionRef.current=null;setBusy(false);}}
  };

  const mutate = async kind => {
    if (actionRef.current || confirmation?.owner !== loaded || confirmation.kind !== kind || !canAct(kind)) return;
    let password = passwordRef.current?.value ?? "";
    if (passwordRef.current) passwordRef.current.value = "";
    if ((kind === "execute"||kind === "preview") && (password.length < 12 || password.length > 1024)) {
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
      const response = await (kind==="preview"?api.getBackup(original.id,options):api.getRestore(original.id, options));
      requireCurrent();
      const item = projectedTarget(response, { key: kind==="preview"?"backup_id":"restore_request_id", id: original.id }, original.tenantId);
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
      if (kind === "execute"||kind === "preview") {
        const issuance = api.issueRestoreStepUp(original.id, operation.password, { ...options, idempotencyKey: secureUuid() });
        operation.password = "";
        operation.token = await issuance;
        requireCurrent();
        if (typeof operation.token !== "string" || !operation.token.trim() || operation.token.length > (kind==="preview"?512:4096)
          || /[\u0000-\u0020\u007f]/u.test(operation.token)) throw new Error("INVALID_STEP_UP");
        // 인증 도중 세션·대상·Preview 변경도 거부하고, POST body 구성 직전에 current를 확인한다.
        await exactRead(true);
        requireCurrent();
        if(kind==="preview"){
          const destination=confirmation.destination;
          const items=destinationSummaries(await api.getRestoreDestinations(original.id,options));requireCurrent();
          if(!destination||Date.parse(destination.expires_at)<=Date.now()||!items.some(item=>JSON.stringify(item)===JSON.stringify(destination)))throw new Error("DESTINATION_CHANGED");
          await exactRead(true);requireCurrent();
          const result=await api.previewRegisteredRestore(original.id,{workspace_id:original.workspaceId,destination_id:destination.destination_id,
            destination_version:destination.version,step_up_authorization_id:operation.token},secureUuid(),options);
          operation.token="";requireCurrent();
          const id=result?.payload?.data?.request_id;if(!safeId(id))throw new Error("PREVIEW_UNCERTAIN");
          requireSession(await api.getSession(options));
          const detail=await api.getRestore(id,options);requireCurrent();
          const item=projectedTarget(detail,{key:"restore_request_id",id},original.tenantId);
          if(!item||item.workspaceId!==original.workspaceId||detail.payload.data.backup_id!==original.id||item.preview.kind!=="selected_source_lineage"||!item.etag)throw new Error("PREVIEW_UNCERTAIN");
          requireSession(await api.getSession(options));requireCurrent();navigate(`?restore_request_id=${encodeURIComponent(id)}`);return;
        }
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
        aria-label={state === "system" ? "시스템 운영 상태" : state === "home" ? "현재 운영 상태와 백업 목록" : "선택한 복구 대상"}>
        {ready && state === "system" && <>
          <h2>시스템 운영 상태</h2>
          <p>Workspace 자료 집계가 아닌 시스템 관측입니다. 미수집은 0이나 준비 완료가 아닙니다.</p>
          <ul>{loaded.home.status.health.map(r=><li key={r.component_id}>
            {{api:"API",database:"DB",object_storage:"Object Storage"}[r.component_id]} · {r.state==="ready"?"준비":"조회 불가"} · {r.checked_at}
          </li>)}</ul>
          <h3>Worker 관측</h3>
          <ul>{loaded.home.status.worker_samples.map(r=><li key={r.worker_kind}>
            {{document:"문서",studio:"Studio",object:"Object"}[r.worker_kind]} · {r.state==="fresh"?"최근 관측":r.state==="stale"?"지난 관측":r.state==="unavailable"?"조회 불가":"미수집"}
            {r.process_state==="processing"?" · 처리 중":r.process_state==="stopped"?" · 종료 관측":""}{r.observed_at?` · ${r.observed_at}`:""}
          </li>)}</ul>
          <h3>프로세스 자원 관측 12종</h3>
          <ul>{loaded.home.status.resource_samples.map(r=><li key={`${r.worker_kind}:${r.metric_id}`}>
            {r.worker_kind} · {{cpu_quota_millicores:"CPU 할당",memory_limit_bytes:"메모리 한도",memory_current_bytes:"메모리 사용",runtime_available_bytes:"영속 저장 여유"}[r.metric_id]}
            {r.value===null?` · ${r.state==="unavailable"?"조회 불가":"미수집"}`:` · ${r.value} ${r.unit} · ${r.state==="stale"?"지난 관측":"최근 관측"}`}
          </li>)}</ul>
          <h3>배포·Rollback 기록</h3>
          {loaded.home.status.deployment_summary.result===null?<p>확인된 배포 기록 미수집</p>:<p>{loaded.home.status.deployment_summary.operation} · {loaded.home.status.deployment_summary.result} · {loaded.home.status.deployment_summary.code_revision}</p>}
          {loaded.home.status.unimplemented.length>0&&<p>미구현: {loaded.home.status.unimplemented.map(r=>({worker_observation_producer:"Worker 수집",resource_observation_producer:"자원 수집",deployment_receipt_writer:"배포 기록 수신"}[r.producer_id])).join(", ")}</p>}
        </>}
        {ready && state === "home" && <>
          <p>백업은 자료 선택·계보 사전확인·명시 동의 후 독립 보존 준비가 된 서버에서만 생성됩니다. v3 백업의 등록 목적지 Preview와 별도 인증 Execute·Cancel은 정확 ID 상세에서 사용합니다. 독립 전체 DB 복원은 지원하지 않습니다.</p>
          <BackupSelectionPane api={api} auth={loaded.home.actionSession} scope={scope} scopeRef={scopeRef}
            generation={generation} paused={paused} owner={loaded} actionRef={actionRef} onNavigate={navigate} invalidate={nextState => {
              ++generation.current;
              if (targetRef.current) { targetRef.current.hidden = true; targetRef.current.inert = true; }
              setLoaded({ ...loaded, state: nextState, target: null, home: null });
            }} />
          <h2>현재 Workspace 운영 상태</h2>
          <fieldset><legend>관측 범위</legend>
            <button type="button" aria-pressed={metricsScope==="self"} onClick={()=>setMetricsScope("self")}>본인 기록</button>
            <button type="button" aria-pressed={metricsScope==="workspace_visible"}
              disabled={!loaded.home.actionSession?.operations.includes("cloud_backup_list")}
              onClick={()=>setMetricsScope("workspace_visible")}>현재 열람 가능한 Workspace 기록</button>
          </fieldset>
          {loaded.home.status ? <>
            <p>기존 5종 상태: {STATUS_NAMES[loaded.home.status.overall_status]} · 확인 시각: {loaded.home.status.checked_at}</p>
            <ul>{loaded.home.status.components.map(item => <li key={item.component_id}>
              {COMPONENT_NAMES[item.component_id]} · {STATUS_NAMES[item.status]} · {item.safe_code} · 대기 {item.pending_count}건
            </li>)}</ul>
            <p>Provider는 설정 상태이며 모델 실제 호출 결과가 아닙니다. 대기 건수는 실패 건수가 아닙니다.</p>
            <h3>기존 기록 운영 관측 16종</h3>
            <dl aria-label="기존 기록 운영 관측 16종">{Object.entries(OBSERVATION_NAMES).map(([id, label]) => {
              const row = loaded.home.status.observations?.find(item => item.metric_id === id);
              const state = row && Object.hasOwn(OBSERVATION_STATES, row.state) ? row.state : "unavailable";
              const measured = ["measured", "partial"].includes(state) && Number.isSafeInteger(row?.value) && row.value >= 0;
              return <div key={id} data-metric-id={id}><dt>{label}</dt><dd>{OBSERVATION_STATES[state]} · {measured ? `${row.value} ${OBSERVATION_UNITS[row.unit] ?? ""}` : "값 미확인"}
                {measured && serverTimestamp(row.observed_at) ? ` · ${row.observed_at}` : ""}</dd></div>;
            })}</dl>
            <p>자료·Token은 본인의 현재 접근 가능한 기록만 집계합니다. Queue는 기존 Workspace 운영 집계입니다. Token 누락은 0으로 계산하지 않으며 기간 전체 사용량이 아닙니다.</p>
            <WorkspaceTelemetry status={loaded.home.status}/>
            <p>lease와 Index 기록 수는 실제 생존이나 Build 진행률이 아닙니다. 미집계·미보고는 정상 0건이 아닙니다.</p>
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
          {canBrowseDestinations()&&<button type="button" disabled={busy} onClick={()=>{void loadDestinations();}}>등록 복원 목적지 조회</button>}
          {destinations?.owner===loaded&&<>
            {destinations.phase==="loading"&&<p role="status">등록 목적지와 현재 권한을 확인하고 있습니다.</p>}
            {destinations.phase==="error"&&<p role="status">등록 목적지를 확인할 수 없습니다. 다시 조회해 주세요.</p>}
            {destinations.phase==="ready"&&destinations.items.length===0&&<p>사용 가능한 등록 목적지가 없습니다.</p>}
            {destinations.items.map(item=><button key={item.destination_id} type="button" disabled={busy||destinations.phase!=="ready"}
              aria-pressed={destinations.selected?.destination_id===item.destination_id} onClick={()=>setDestinations(v=>({...v,selected:item}))}>{item.display_name} · {item.destination_id}</button>)}
            {canAct("preview")&&<button type="button" disabled={busy} onClick={()=>{
              if(passwordRef.current)passwordRef.current.value="";
              setConfirmation({owner:loaded,kind:"preview",destination:{...destinations.selected}});
            }}>등록 목적지 Preview 준비</button>}
          </>}
          {loaded.target.preview && <>
            <h2>기존 안전 Preview 결과</h2>
            <p>Preview 버전 {loaded.target.preview.version} · 포함 {loaded.target.preview.includedCount}건 · 제외 {loaded.target.preview.excludedCount}건</p>
            <ul>{Object.entries(EXCLUSION_NAMES).map(([reason, label]) => <li key={reason}>{label}: {loaded.target.preview.counts[reason]}건</li>)}</ul>
            <p>서버 검증 기록: {loaded.target.verified ? "있음" : "없음"}</p>
            {loaded.target.preview.kind==="selected_source_lineage"&&<p>등록 목적지 {loaded.target.preview.destination.display_name} · 버전 {loaded.target.preview.destination.version} · 만료 {loaded.target.preview.expiresAt}. 포함/제외 수는 root 기준이며 필요한 선행은 함께 검증합니다.</p>}
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
          {confirmation?.owner === loaded && ["execute","preview"].includes(confirmation.kind) && canAct(confirmation.kind) && <form onSubmit={event => {
            event.preventDefault(); void mutate(confirmation.kind);
          }}>
            <p>{confirmation.kind==="preview"?"등록 목적지에 대한 안전 Preview를 새 비밀번호 인증 후 요청합니다. 아직 복원 실행이 아닙니다.":"기존 Preview를 확인했습니다. 새 비밀번호 인증 후 이 요청을 별도로 실행합니다."}</p>
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
