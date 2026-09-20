"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { providerSettingsApi } from "../lib/provider-settings-api.js";
import { summarizeConnectionUsage } from "./provider-settings-usage.js";

const PROVIDERS = Object.freeze([
  "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
  "OPENROUTER", "ANTHROPIC", "OLLAMA", "OMNIROUTE", "EOUL_GATEWAY", "MEDIA_BRIDGE", "SENTENCE_TRANSFORMERS"
]);
const MANAGED_MODEL_PROVIDERS = new Set(["MEDIA_BRIDGE", "OMNIROUTE"]);
const CREDENTIAL_REQUIRED_PROVIDERS = new Set([
  "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
  "OPENROUTER", "ANTHROPIC", "OMNIROUTE", "EOUL_GATEWAY", "SENTENCE_TRANSFORMERS"
]);
const CAPABILITY_LABELS = Object.freeze({
  text_generation: "텍스트 생성",
  image_understanding: "이미지 이해",
  document_parsing: "문서 분석",
  embedding: "임베딩",
  reranking: "재정렬",
  audio_understanding: "오디오 이해",
  speech_to_text: "음성 인식(STT)",
  video_understanding: "영상 이해",
  image_generation: "이미지 생성",
  text_to_speech: "음성 생성(TTS)",
  audio_generation: "오디오 생성",
  video_generation: "영상 생성"
});

function operationKey(prefix) {
  const webCrypto = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") {
    return `${prefix}-${webCrypto.randomUUID()}`;
  }
  if (typeof webCrypto?.getRandomValues === "function") {
    const values = webCrypto.getRandomValues(new Uint32Array(4));
    return `${prefix}-${Array.from(values, (value) => value.toString(16).padStart(8, "0")).join("")}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function emptyConnectionDraft(defaultEndpoint = "") {
  return {
    connection_id: "",
    provider_code: "OLLAMA",
    display_name: "",
    base_url: defaultEndpoint,
    logical_model_ids: "",
    enabled: true,
    version: 0
  };
}

function withDefaultProviders(items) {
  const registered = new Set(items.map((item) => item.provider_code));
  const defaults = PROVIDERS.filter((provider) => !registered.has(provider)).map((provider) => ({
    connection_id: `provider-${provider.toLowerCase()}`,
    provider_code: provider,
    display_name: provider,
    base_url: "",
    enabled: false,
    configured: false,
    credential_version: 0,
    verification_status: "unverified",
    catalog_status: "stale",
    catalog_version: 0,
    version: 0,
    models: [],
  }));
  return [...items, ...defaults];
}

function draftFromConnection(connection) {
  return {
    connection_id: connection.connection_id,
    provider_code: connection.provider_code,
    display_name: connection.display_name,
    base_url: connection.base_url || "",
    logical_model_ids: connection.models.map((model) => model.model_id).join("\n"),
    enabled: Number(connection.version ?? 0) === 0 ? true : connection.enabled,
    version: connection.version
  };
}

export function formatModelChoice(connection, model) {
  return `${connection.display_name} · ${model.model_id}`;
}

export function projectProviderConnection(connection) {
  const credential = connection?.configured ? "Credential 설정됨" : "Credential 없음";
  if (!connection?.enabled) {
    return { label: `비활성 · ${credential}`, verified: false };
  }
  const verified = connection.verification_status === "verified";
  return { label: `활성 · ${credential} · ${verified ? "확인됨" : "확인 필요"}`, verified };
}

export function safeProviderErrorMessage(action, error) {
  const prefix = ({
    provider: "Provider 연결",
    credential: "Credential",
    catalog: "모델 카탈로그",
    capability: "모델 기능"
  })[action] ?? "Provider 설정";
  if (error?.code === "VERSION_CONFLICT" || error?.code === "PRECONDITION_FAILED" || error?.code === "PROVIDER_CREDENTIAL_VERSION_CONFLICT") {
    return `${prefix}이 다른 변경과 충돌했습니다. 새로고침 후 다시 시도해 주세요.`;
  }
  if (error?.code === "FORBIDDEN" || error?.code === "ACTION_DENIED") return `${prefix}을 변경할 권한이 없습니다.`;
  if (action === "catalog" && (error?.code === "PROVIDER_CONNECTION_NOT_FOUND" || error?.code === "PROVIDER_CREDENTIAL_NOT_CONFIGURED")) {
    return "저장된 API Key가 없습니다. 먼저 API Key를 저장하세요.";
  }
  if (action === "catalog" && (error?.code === "PROVIDER_AUTHENTICATION_FAILED" || error?.code === "PROVIDER_VERIFICATION_FAILED")) {
    return "API Key가 유효하지 않거나 Provider에 연결할 수 없습니다. Endpoint와 API Key를 확인하세요.";
  }
  return `${prefix}을 저장하지 못했습니다. 다시 시도해 주세요.`;
}

export function canRefreshCatalog(connection, busy) {
  return !busy && Number(connection?.version ?? 0) > 0
    && !MANAGED_MODEL_PROVIDERS.has(connection?.provider_code);
}

export function providerRequiresCredential(providerCode, baseUrl = "") {
  if (providerCode === "OLLAMA") return false;
  if (providerCode === "EOUL_GATEWAY" || providerCode === "MEDIA_BRIDGE") {
    try {
      const hostname = new URL(baseUrl).hostname;
      return !new Set(["localhost", "127.0.0.1", "[::1]", "::1"]).has(hostname);
    } catch {
      return false;
    }
  }
  return CREDENTIAL_REQUIRED_PROVIDERS.has(providerCode);
}

export function canSaveCredential(connection, draft, credential, busy) {
  const isNewConnection = Number(draft?.version ?? 0) === 0;
  return !busy && Boolean(credential?.trim()) && Boolean(draft?.connection_id?.trim())
    && (isNewConnection
      ? Boolean(draft?.display_name?.trim()) && Boolean(draft?.base_url?.trim())
      : Boolean(connection));
}

function normalizedLogicalModels(value) {
  return [...new Set(value.split(/[\n,]/u).map((item) => item.trim()).filter(Boolean))];
}

function updateLogicalModelSelection(current, modelId, checked) {
  const selected = normalizedLogicalModels(current);
  const next = checked ? [...selected, modelId] : selected.filter((item) => item !== modelId);
  return [...new Set(next)].join("\n");
}

function modelKey(connectionId, modelId) {
  return JSON.stringify([connectionId, modelId]);
}

export function ProviderSettingsWorkspace({ workspaceId, embedded = false }) {
  const [resolvedWorkspaceId, setResolvedWorkspaceId] = useState(workspaceId ?? null);
  const [isSystemAdmin, setIsSystemAdmin] = useState(null);
  const [connections, setConnections] = useState([]);
  const [userCredentials, setUserCredentials] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(() => emptyConnectionDraft());
  const [credential, setCredential] = useState("");
  const [healthSettings, setHealthSettings] = useState({ interval_minutes: 60, version: 0 });
  const [healthIntervalDraft, setHealthIntervalDraft] = useState(60);
  const [status, setStatus] = useState({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });

  const selectConnection = useCallback((connection) => {
    setSelectedId(connection.connection_id);
    setDraft(draftFromConnection(connection));
    setCredential("");
  }, []);

  const applyConnections = useCallback((items, preferredId = null) => {
    const projectedItems = withDefaultProviders(items);
    setConnections(projectedItems);
    const selected = projectedItems.find((item) => item.connection_id === preferredId) ?? projectedItems[0];
    if (selected) selectConnection(selected);
    else {
      setSelectedId(null);
      setDraft(emptyConnectionDraft());
    }
  }, [selectConnection]);

  const applyUserCredentials = useCallback((snapshot) => {
    const items = Array.isArray(snapshot?.credentials) ? snapshot.credentials : [];
    setUserCredentials(Object.fromEntries(items.map((item) => [item.connection_id, item])));
  }, []);

  const load = useCallback(async () => {
    setStatus({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });
    try {
      const session = await providerSettingsApi.getSession();
      const activeWorkspaceId = workspaceId ?? session.payload?.data?.workspace_id;
      if (typeof activeWorkspaceId !== "string" || !activeWorkspaceId.trim()) throw new Error("RESOURCE_UNAVAILABLE");
      setResolvedWorkspaceId(activeWorkspaceId);
      const systemAdmin = session.payload?.data?.is_system_admin === true;
      setIsSystemAdmin(systemAdmin);
      const [result, personal, health] = await Promise.all([
        providerSettingsApi.listConnections(),
        providerSettingsApi.listUserCredentials(),
        systemAdmin ? providerSettingsApi.getHealthSettings() : Promise.resolve(null)
      ]);
      applyConnections(Array.isArray(result.payload?.data) ? result.payload.data : []);
      applyUserCredentials(personal.payload?.data);
      if (health?.payload?.data) {
        setHealthSettings(health.payload.data);
        setHealthIntervalDraft(health.payload.data.interval_minutes);
      }
      setStatus({ kind: "ready", message: systemAdmin ? "시스템 연결과 모델 목록을 조회했습니다." : "공유 Provider 연결과 개인 키 설정을 조회했습니다." });
    } catch {
      setStatus({ kind: "error", message: "Provider 설정을 불러오지 못했습니다. 다시 시도해 주세요." });
    }
  }, [applyConnections, applyUserCredentials, workspaceId]);

  useEffect(() => { load(); }, [load]);

  const selectedConnection = connections.find((item) => item.connection_id === selectedId) ?? null;
  const connectionUsage = summarizeConnectionUsage(connections);
  const adminAvailableModels = useMemo(() => connections.flatMap((connection) => (
    !connection.enabled || connection.verification_status !== "verified" || connection.catalog_status !== "ready"
      ? []
      : connection.models.filter((model) => model.catalog_status === "ready").map((model) => ({ connection, model }))
  )), [connections]);
  const availableModels = useMemo(() => (
    (selectedConnection?.models ?? [])
      .filter((model) => model.catalog_status === "ready")
      .map((model) => model.model_id)
  ), [selectedConnection]);
  const selectedModelIds = useMemo(() => normalizedLogicalModels(draft.logical_model_ids), [draft.logical_model_ids]);

  async function saveConnection(includeCredential) {
    const connectionId = draft.connection_id.trim();
    if (!isSystemAdmin) {
      if (!includeCredential || !connectionId || !credential) return;
      setStatus({ kind: "saving", message: "개인 Provider 키를 저장하는 중입니다." });
      try {
        const current = userCredentials[connectionId];
        const result = await providerSettingsApi.replaceUserCredential(connectionId, {
          credential, expected_version: current?.credential_version ?? 0
        });
        setUserCredentials((items) => ({ ...items, [connectionId]: result.payload.data }));
        setCredential("");
        setStatus({ kind: "ready", message: "이 계정에서만 사용하는 개인 키를 저장했습니다." });
      } catch (error) {
        setCredential("");
        setStatus({ kind: "error", message: safeProviderErrorMessage("credential", error) });
      }
      return;
    }
    const action = includeCredential ? "credential" : "provider";
    setStatus({ kind: "saving", message: "Provider 연결 설정을 저장하는 중입니다." });
    try {
      let result;
      if (includeCredential && draft.version > 0) {
        result = await providerSettingsApi.replaceCredential(connectionId, {
          credential,
          expected_version: draft.version
        }, operationKey("provider-credential-replace"));
      } else {
        const body = {
          display_name: draft.display_name.trim(),
          base_url: draft.base_url.trim(),
          logical_model_ids: normalizedLogicalModels(draft.logical_model_ids),
          enabled: draft.enabled,
          expected_version: draft.version,
          ...(includeCredential ? { credential } : {})
        };
        result = draft.version === 0
          ? await providerSettingsApi.createConnection({ connection_id: connectionId, provider_code: draft.provider_code, ...body }, operationKey("provider-create"))
          : await providerSettingsApi.updateConnection(connectionId, body, operationKey("provider-update"));
      }
      const item = result.payload.data;
      applyConnections([...connections.filter((connection) => connection.connection_id !== item.connection_id), item], item.connection_id);
      setCredential("");
      setStatus({ kind: "ready", message: includeCredential ? "API Key를 저장했습니다." : "Provider 연결을 저장했습니다." });
    } catch (error) {
      setCredential("");
      setStatus({ kind: "error", message: safeProviderErrorMessage(action, error) });
    }
  }

  async function saveHealthSettings() {
    if (!isSystemAdmin || busy) return;
    setStatus({ kind: "saving", message: "연결 상태 확인 주기를 저장하는 중입니다." });
    try {
      const result = await providerSettingsApi.saveHealthSettings({
        interval_minutes: Number(healthIntervalDraft),
        expected_version: healthSettings.version,
      });
      setHealthSettings(result.payload.data);
      setHealthIntervalDraft(result.payload.data.interval_minutes);
      setStatus({ kind: "ready", message: "연결 상태 확인 주기를 저장했습니다." });
    } catch {
      setStatus({ kind: "error", message: "연결 상태 확인 주기를 저장하지 못했습니다. 새로고침 후 다시 시도해 주세요." });
    }
  }

  async function deleteUserCredential() {
    if (!selectedConnection || !userCredentials[selectedConnection.connection_id]) return;
    setStatus({ kind: "saving", message: "개인 Provider 키를 삭제하는 중입니다." });
    try {
      const current = userCredentials[selectedConnection.connection_id];
      await providerSettingsApi.deleteUserCredential(selectedConnection.connection_id, {
        expected_version: current.credential_version
      });
      setUserCredentials((items) => {
        const next = { ...items };
        delete next[selectedConnection.connection_id];
        return next;
      });
      setCredential("");
      setStatus({ kind: "ready", message: "이 계정의 개인 키를 삭제했습니다." });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("credential", error) });
    }
  }

  async function deleteCredential() {
    if (!selectedConnection) return;
    setStatus({ kind: "saving", message: "Credential을 삭제하는 중입니다." });
    try {
      await providerSettingsApi.deleteCredential(selectedConnection.connection_id, {
        expected_version: selectedConnection.version
      }, operationKey("provider-credential-delete"));
      setCredential("");
      await load();
      setStatus({ kind: "ready", message: "Credential을 삭제했습니다. 연결과 카탈로그는 유지됩니다." });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("credential", error) });
    }
  }

  async function refreshCatalog() {
    if (!selectedConnection) return;
    setStatus({ kind: "saving", message: "모델 카탈로그를 새로고침하는 중입니다." });
    try {
      const result = await providerSettingsApi.refreshCatalog(selectedConnection.connection_id, {
        expected_version: selectedConnection.version
      }, operationKey("provider-catalog-refresh"));
      const item = result.payload.data;
      applyConnections([...connections.filter((connection) => connection.connection_id !== item.connection_id), item], item.connection_id);
      setStatus({ kind: "ready", message: "모델 카탈로그를 새로고침했습니다." });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("catalog", error) });
    }
  }

  const busy = status.kind === "saving" || status.kind === "loading";
  const managedModels = MANAGED_MODEL_PROVIDERS.has(selectedConnection?.provider_code);
  const canMutate = isSystemAdmin === true && !busy && Boolean(draft.connection_id.trim()) && Boolean(draft.display_name.trim()) && Boolean(draft.base_url.trim());
  const canUsePersonalCredential = isSystemAdmin || Number(selectedConnection?.version ?? 0) > 0;
  const Root = embedded ? "div" : "main";

  return (
    <Root className={`provider-settings-shell ${embedded ? "is-embedded" : ""}`}>
      <header className="provider-settings-header">
        <div><span className="section-kicker">MODEL CONNECTIONS</span><h1>{embedded ? "Provider 연결" : "모델·Provider 설정"}</h1><p>{resolvedWorkspaceId ? "현재 Workspace 설정" : "Workspace 확인 중"}</p></div>
        <button className="secondary-button" type="button" onClick={load} disabled={busy}>새로고침</button>
      </header>
      <div className={`provider-status ${status.kind}`} role="status"><span className="status-dot" aria-hidden="true" />{status.message}</div>

      {isSystemAdmin !== null ? <section className="provider-admin-panel" aria-labelledby="provider-admin-title">
        <div className="studio-section-heading"><div><span className="section-kicker">{isSystemAdmin ? "SYSTEM ADMIN" : "SHARED CONNECTIONS"}</span><h2 id="provider-admin-title">시스템 Provider 연결</h2><small>{connectionUsage.label} · {connectionUsage.description}</small></div>{isSystemAdmin ? <div className="provider-heading-actions"><label>상태 확인 주기<select aria-label="상태 확인 주기" value={healthIntervalDraft} onChange={(event) => setHealthIntervalDraft(Number(event.target.value))}><option value={60}>60분</option><option value={120}>120분</option><option value={360}>360분</option><option value={720}>720분</option><option value={1440}>1440분</option></select></label><button className="secondary-button" type="button" onClick={saveHealthSettings} disabled={busy || Number(healthIntervalDraft) === Number(healthSettings.interval_minutes)}>주기 저장</button><button className="secondary-button" type="button" onClick={() => { setSelectedId(null); setDraft(emptyConnectionDraft()); setCredential(""); }}>연결 추가</button></div> : <small>Endpoint·모델 설정은 시스템 관리자만 변경할 수 있습니다.</small>}</div>
        <div className="provider-settings-layout">
          <div className="provider-connection-list" aria-label="Provider 연결 목록">
            {connections.map((connection) => { const projected = projectProviderConnection(connection); return <button className="provider-card" type="button" aria-pressed={selectedId === connection.connection_id} onClick={() => selectConnection(connection)} key={connection.connection_id}><span className="provider-monogram" aria-hidden="true">{connection.provider_code.slice(0, 1)}</span><span><strong>{connection.display_name}</strong><small>{connection.provider_code} · {projected.label}</small></span><span className={`provider-state-dot ${projected.verified ? "is-ready" : ""}`} aria-hidden="true" /></button>; })}
            {!connections.length ? <div className="provider-empty"><strong>등록된 연결이 없습니다.</strong><small>{isSystemAdmin ? "연결 이름과 Endpoint를 입력해 첫 연결을 추가하세요." : "시스템 관리자에게 Provider 연결 등록을 요청하세요."}</small></div> : null}
          </div>

          <div className="provider-detail">
            <header><div><span className="section-kicker">{draft.version ? "SELECTED CONNECTION" : "NEW CONNECTION"}</span><h2>{draft.display_name || "새 Provider 연결"}</h2></div>{selectedConnection ? <span className={`connection-badge ${projectProviderConnection(selectedConnection).verified ? "is-ready" : ""}`}>{projectProviderConnection(selectedConnection).label}</span> : null}</header>
            <div className="provider-detail-grid">
              {isSystemAdmin ? <>
                <label>Connection ID<input value={draft.connection_id} disabled={draft.version > 0} autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, connection_id: event.target.value }))} /></label>
                <label>Provider<select value={draft.provider_code} disabled={draft.version > 0} onChange={(event) => setDraft((current) => ({ ...current, provider_code: event.target.value, base_url: connections.find((connection) => connection.provider_code === event.target.value)?.base_url ?? current.base_url }))}>{PROVIDERS.map((provider) => <option value={provider} key={provider}>{provider}</option>)}</select></label>
                <label>연결 이름<input value={draft.display_name} autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))} /></label>
                <label>Endpoint<input value={draft.base_url} autoComplete="off" placeholder={draft.version ? "보안을 위해 저장된 주소는 표시하지 않습니다" : "서버에서 검증할 Endpoint"} onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value }))} /></label>
                <fieldset className="provider-field-wide provider-model-picker"><legend>연결할 모델 (선택)</legend><p>모델을 선택하지 않으면 Provider 기준 모델을 사용합니다.</p>{managedModels ? <small>이 Provider가 모델을 직접 관리하므로 Daon에서 모델을 선택하지 않습니다.</small> : availableModels.length ? <div className="provider-model-options">{availableModels.map((modelId) => <label key={modelId}><input type="checkbox" checked={selectedModelIds.includes(modelId)} onChange={(event) => setDraft((current) => ({ ...current, logical_model_ids: updateLogicalModelSelection(current.logical_model_ids, modelId, event.target.checked) }))} /><span>{modelId}</span></label>)}</div> : <small>등록된 모델이 없습니다. 모델 연결 없이 저장할 수 있습니다.</small>}{selectedModelIds.length ? <button type="button" className="provider-model-clear" onClick={() => setDraft((current) => ({ ...current, logical_model_ids: "" }))}>Provider 기준 모델 사용으로 변경</button> : null}</fieldset><p className="provider-field-wide provider-form-note">API Key 저장과 모델 조회는 선택 사항입니다. 모델을 지정하지 않아도 Provider 연결은 저장됩니다.</p>
              </> : <p className="provider-field-wide">공유 연결의 Endpoint와 모델 목록은 숨겨져 있습니다. 아래에서 이 연결의 API Key만 교체할 수 있습니다.</p>}
              <label>API Key 또는 Client Key {providerRequiresCredential(draft.provider_code, draft.base_url) ? "(연결 저장 선택 · 사용 시 필수)" : "(선택)"}{isSystemAdmin && selectedConnection?.configured ? <small className="provider-field-status is-saved">저장됨 · 새 키를 입력하면 교체됩니다.</small> : null}<input type="password" value={credential} autoComplete="new-password" disabled={!canUsePersonalCredential} placeholder={!isSystemAdmin && !canUsePersonalCredential ? "관리자가 먼저 Provider 연결을 등록해야 합니다" : providerRequiresCredential(draft.provider_code, draft.base_url) ? "연결 저장은 가능하지만 사용하려면 API Key가 필요합니다" : "연결 저장 후 Provider 기본 인증으로 사용할 수 있습니다"} onChange={(event) => setCredential(event.target.value)} /></label>
            </div>
            {isSystemAdmin ? <label className="styled-check"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} /><span>사용 후보에 포함</span></label> : null}
            <div className="provider-detail-actions">
              {isSystemAdmin ? <button className="secondary-button" type="button" onClick={() => saveConnection(false)} disabled={!canMutate}>연결 저장</button> : null}
              <button className="primary-button" type="button" onClick={() => saveConnection(true)} disabled={!canSaveCredential(selectedConnection, draft, credential, busy) || !canUsePersonalCredential}>{isSystemAdmin ? "시스템 키 저장" : "내 계정 키 저장"}</button>
              {!isSystemAdmin ? <button className="secondary-button danger-button" type="button" onClick={deleteUserCredential} disabled={busy || !canUsePersonalCredential || !userCredentials[selectedConnection?.connection_id]}>내 계정 키 삭제</button> : null}
              {isSystemAdmin ? <button className="secondary-button danger-button" type="button" onClick={deleteCredential} disabled={busy || !selectedConnection?.configured}>키 삭제</button> : null}
              {isSystemAdmin && !managedModels ? <button className="secondary-button" type="button" onClick={refreshCatalog} disabled={!canRefreshCatalog(selectedConnection, busy)} title={selectedConnection?.configured ? "저장된 API Key로 모델 목록을 수동 조회합니다." : "먼저 API Key를 저장하세요."}>모델 조회</button> : null}
            </div>
          </div>
        </div>
      </section> : null}

      <section className="workspace-model-defaults" aria-labelledby="provider-models-title">
        <div className="studio-section-heading"><div><span className="section-kicker">MODEL CATALOG</span><h2 id="provider-models-title">조회된 모델</h2><small>모델 지정과 `모델 조회`는 선택 사항입니다. 지정하지 않으면 Provider 기준 모델을 사용합니다.</small></div></div>
        <div className="provider-model-grid">
          {adminAvailableModels.map(({ connection, model }) => (
            <article className="provider-model-card" key={modelKey(connection.connection_id, model.model_id)}><header><div><strong>{formatModelChoice(connection, model)}</strong><small>Catalog v{model.catalog_version}</small></div><span className="connection-badge is-ready">사용 가능</span></header><div className="model-capabilities">{model.effective_capabilities.map((capability) => <span className="capability-chip is-active" key={capability}>{CAPABILITY_LABELS[capability] ?? capability}</span>)}</div></article>
          ))}
          {isSystemAdmin === true && !adminAvailableModels.length ? <div className="provider-empty"><strong>저장된 모델 카탈로그가 없습니다.</strong><small>필요한 경우 API Key를 저장한 뒤 `모델 조회`로 확인할 수 있습니다. 모델을 지정하지 않아도 Provider 기준 모델로 사용할 수 있습니다.</small></div> : null}
        </div>
      </section>
    </Root>
  );
}
