"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { providerSettingsApi } from "../lib/provider-settings-api.js";

const PROVIDERS = Object.freeze([
  "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
  "OPENROUTER", "ANTHROPIC", "OLLAMA", "OMNIROUTE", "EOUL_GATEWAY", "MEDIA_BRIDGE", "SENTENCE_TRANSFORMERS"
]);
const ACTIVE_CAPABILITIES = Object.freeze([
  "text_generation", "image_understanding", "document_parsing"
]);
const CAPABILITIES = Object.freeze([
  ...ACTIVE_CAPABILITIES, "embedding", "reranking", "audio_understanding",
  "speech_to_text", "video_understanding", "image_generation", "text_to_speech",
  "audio_generation", "video_generation"
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
  return `${prefix}-${crypto.randomUUID()}`;
}

function emptyConnectionDraft() {
  return {
    connection_id: "",
    provider_code: "OLLAMA",
    display_name: "",
    base_url: "",
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
    base_url: "",
    logical_model_ids: connection.models.map((model) => model.model_id).join("\n"),
    enabled: connection.enabled,
    version: connection.version
  };
}

export function formatModelChoice(connection, model) {
  return `${connection.display_name} · ${model.model_id}`;
}

export function projectProviderConnection(connection) {
  if (!connection?.enabled) {
    return { label: connection?.configured ? "비활성 · Credential 설정됨" : "비활성 · Credential 없음", verified: false };
  }
  const verified = connection.verification_status === "verified";
  const credential = connection.configured ? "Credential 설정됨" : "Credential 없음";
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
  return `${prefix}을 저장하지 못했습니다. 다시 시도해 주세요.`;
}

function normalizedLogicalModels(value) {
  return [...new Set(value.split(/[\n,]/u).map((item) => item.trim()).filter(Boolean))];
}

function modelKey(connectionId, modelId) {
  return JSON.stringify([connectionId, modelId]);
}

export function ProviderSettingsWorkspace({ workspaceId, embedded = false }) {
  const [resolvedWorkspaceId, setResolvedWorkspaceId] = useState(workspaceId ?? null);
  const [isSystemAdmin, setIsSystemAdmin] = useState(null);
  const [connections, setConnections] = useState([]);
  const [userCredentials, setUserCredentials] = useState({});
  const [modelDefaults, setModelDefaults] = useState({ available_models: [], defaults: [] });
  const [modelDefaultsEtag, setModelDefaultsEtag] = useState(null);
  const [defaultDrafts, setDefaultDrafts] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(emptyConnectionDraft);
  const [credential, setCredential] = useState("");
  const [administratorPassword, setAdministratorPassword] = useState("");
  const [capabilityDrafts, setCapabilityDrafts] = useState({});
  const [status, setStatus] = useState({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });

  const selectConnection = useCallback((connection) => {
    setSelectedId(connection.connection_id);
    setDraft(draftFromConnection(connection));
    setCredential("");
  }, []);

  const applyConnections = useCallback((items, preferredId = null) => {
    const projectedItems = withDefaultProviders(items);
    setConnections(projectedItems);
    setCapabilityDrafts(Object.fromEntries(projectedItems.flatMap((connection) => connection.models.map((model) => [
      modelKey(connection.connection_id, model.model_id), [...model.effective_capabilities]
    ]))));
    const selected = projectedItems.find((item) => item.connection_id === preferredId) ?? projectedItems[0];
    if (selected) selectConnection(selected);
    else {
      setSelectedId(null);
      setDraft(emptyConnectionDraft());
    }
  }, [selectConnection]);

  const applyModelDefaults = useCallback((snapshot, etag) => {
    const safeSnapshot = snapshot && typeof snapshot === "object"
      ? snapshot
      : { available_models: [], defaults: [] };
    setModelDefaults(safeSnapshot);
    setModelDefaultsEtag(etag);
    setDefaultDrafts(Object.fromEntries((safeSnapshot.defaults ?? []).map((item) => [
      item.capability, modelKey(item.connection_id, item.model_id)
    ])));
  }, []);

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
      const [result, defaults, personal] = await Promise.all([
        providerSettingsApi.listConnections(),
        providerSettingsApi.getModelDefaults(activeWorkspaceId),
        providerSettingsApi.listUserCredentials()
      ]);
      applyConnections(Array.isArray(result.payload?.data) ? result.payload.data : []);
      applyModelDefaults(defaults.payload?.data, defaults.etag);
      applyUserCredentials(personal.payload?.data);
      setStatus({ kind: "ready", message: systemAdmin ? "시스템 연결과 Workspace 기본 모델을 조회했습니다." : "공유 Provider 연결과 개인 키 설정을 조회했습니다." });
    } catch {
      setStatus({ kind: "error", message: "Provider 설정을 불러오지 못했습니다. 다시 시도해 주세요." });
    }
  }, [applyConnections, applyModelDefaults, applyUserCredentials, workspaceId]);

  useEffect(() => { load(); }, [load]);

  const selectedConnection = connections.find((item) => item.connection_id === selectedId) ?? null;
  const adminAvailableModels = useMemo(() => connections.flatMap((connection) => (
    !connection.enabled || connection.verification_status !== "verified" || connection.catalog_status !== "ready"
      ? []
      : connection.models.filter((model) => model.catalog_status === "ready").map((model) => ({ connection, model }))
  )), [connections]);
  const availableModels = useMemo(
    () => Array.isArray(modelDefaults.available_models) ? modelDefaults.available_models : [],
    [modelDefaults]
  );

  async function stepUp(targetId) {
    const result = await providerSettingsApi.issueStepUp(
      targetId, administratorPassword, operationKey("provider-step-up")
    );
    const authorization = result.payload?.data?.step_up_authorization;
    if (typeof authorization !== "string" || !authorization) throw new Error("STEP_UP_RESPONSE_INVALID");
    return authorization;
  }

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
    setStatus({ kind: "saving", message: "Provider 연결을 검증하고 저장하는 중입니다." });
    try {
      const authorization = await stepUp(`provider-connection:${connectionId}`);
      let result;
      if (includeCredential && draft.version > 0) {
        result = await providerSettingsApi.replaceCredential(connectionId, {
          credential,
          expected_version: draft.version,
          step_up_authorization_id: authorization
        }, operationKey("provider-credential-replace"));
      } else {
        const body = {
          display_name: draft.display_name.trim(),
          base_url: draft.base_url.trim(),
          logical_model_ids: normalizedLogicalModels(draft.logical_model_ids),
          enabled: draft.enabled,
          expected_version: draft.version,
          step_up_authorization_id: authorization,
          ...(includeCredential ? { credential } : {})
        };
        result = draft.version === 0
          ? await providerSettingsApi.createConnection({ connection_id: connectionId, provider_code: draft.provider_code, ...body }, operationKey("provider-create"))
          : await providerSettingsApi.updateConnection(connectionId, body, operationKey("provider-update"));
      }
      const item = result.payload.data;
      applyConnections([...connections.filter((connection) => connection.connection_id !== item.connection_id), item], item.connection_id);
      setCredential("");
      setAdministratorPassword("");
      setStatus({ kind: "ready", message: includeCredential ? "키를 저장하고 연결을 확인했습니다." : "Provider 연결을 저장했습니다." });
    } catch (error) {
      setCredential("");
      setAdministratorPassword("");
      setStatus({ kind: "error", message: safeProviderErrorMessage(action, error) });
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

  async function saveModelDefault(capability) {
    const selected = defaultDrafts[capability];
    if (!selected || !modelDefaultsEtag || !resolvedWorkspaceId) return;
    let parsed;
    try { parsed = JSON.parse(selected); } catch { return; }
    if (!Array.isArray(parsed) || parsed.length !== 2 || parsed.some((item) => typeof item !== "string" || !item)) return;
    const [connectionId, modelId] = parsed;
    const current = (modelDefaults.defaults ?? []).find((item) => item.capability === capability);
    setStatus({ kind: "saving", message: `${CAPABILITY_LABELS[capability]} 기본 모델을 저장하는 중입니다.` });
    try {
      const result = await providerSettingsApi.saveModelDefault(
        resolvedWorkspaceId,
        { capability, connection_id: connectionId, model_id: modelId, expected_version: current?.version ?? 0 },
        modelDefaultsEtag,
        operationKey("workspace-model-default")
      );
      applyModelDefaults(result.payload?.data, result.etag);
      setStatus({ kind: "ready", message: `${CAPABILITY_LABELS[capability]} 기본 모델을 저장했습니다.` });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("default", error) });
    }
  }

  async function deleteCredential() {
    if (!selectedConnection) return;
    setStatus({ kind: "saving", message: "Credential을 삭제하는 중입니다." });
    try {
      const authorization = await stepUp(`provider-connection:${selectedConnection.connection_id}`);
      await providerSettingsApi.deleteCredential(selectedConnection.connection_id, {
        expected_version: selectedConnection.version,
        step_up_authorization_id: authorization
      }, operationKey("provider-credential-delete"));
      setCredential("");
      setAdministratorPassword("");
      await load();
      setStatus({ kind: "ready", message: "Credential을 삭제했습니다. 연결과 카탈로그는 유지됩니다." });
    } catch (error) {
      setAdministratorPassword("");
      setStatus({ kind: "error", message: safeProviderErrorMessage("credential", error) });
    }
  }

  async function refreshCatalog() {
    if (!selectedConnection) return;
    setStatus({ kind: "saving", message: "모델 카탈로그를 새로고침하는 중입니다." });
    try {
      const authorization = await stepUp(`provider-connection:${selectedConnection.connection_id}`);
      const result = await providerSettingsApi.refreshCatalog(selectedConnection.connection_id, {
        expected_version: selectedConnection.version,
        step_up_authorization_id: authorization
      }, operationKey("provider-catalog-refresh"));
      const item = result.payload.data;
      applyConnections([...connections.filter((connection) => connection.connection_id !== item.connection_id), item], item.connection_id);
      setAdministratorPassword("");
      setStatus({ kind: "ready", message: "모델 카탈로그를 새로고침했습니다." });
    } catch (error) {
      setAdministratorPassword("");
      setStatus({ kind: "error", message: safeProviderErrorMessage("catalog", error) });
    }
  }

  function toggleCapability(connection, model, capability) {
    const key = modelKey(connection.connection_id, model.model_id);
    setCapabilityDrafts((current) => {
      const selected = current[key] ?? model.effective_capabilities;
      const next = selected.includes(capability)
        ? selected.filter((item) => item !== capability)
        : [...selected, capability];
      return next.length ? { ...current, [key]: next } : current;
    });
  }

  async function saveCapabilities(connection, model) {
    setStatus({ kind: "saving", message: "모델 기능 보정을 저장하는 중입니다." });
    try {
      const authorization = await stepUp(`provider-model:${connection.connection_id}:${model.model_id}`);
      const result = await providerSettingsApi.correctCapabilities(
        connection.connection_id,
        model.model_id,
        {
          effective_capabilities: capabilityDrafts[modelKey(connection.connection_id, model.model_id)] ?? model.effective_capabilities,
          expected_version: model.catalog_version,
          step_up_authorization_id: authorization
        },
        operationKey("provider-capability")
      );
      const updatedModel = result.payload.data;
      const nextConnections = connections.map((item) => item.connection_id === connection.connection_id
        ? { ...item, models: item.models.map((candidate) => candidate.model_id === model.model_id ? updatedModel : candidate) }
        : item);
      applyConnections(nextConnections, connection.connection_id);
      setAdministratorPassword("");
      setStatus({ kind: "ready", message: "모델 기능 보정을 저장했습니다." });
    } catch (error) {
      setAdministratorPassword("");
      setStatus({ kind: "error", message: safeProviderErrorMessage("capability", error) });
    }
  }

  const busy = status.kind === "saving" || status.kind === "loading";
  const canMutate = isSystemAdmin === true && !busy && Boolean(administratorPassword) && Boolean(draft.connection_id.trim()) && Boolean(draft.display_name.trim()) && Boolean(draft.base_url.trim());
  const canUsePersonalCredential = isSystemAdmin || Number(selectedConnection?.version ?? 0) > 0;
  const canReplaceCredential = !busy && canUsePersonalCredential && Boolean(credential) && Boolean(selectedConnection)
    && (isSystemAdmin !== true || Boolean(administratorPassword));
  const Root = embedded ? "div" : "main";

  return (
    <Root className={`provider-settings-shell ${embedded ? "is-embedded" : ""}`}>
      <header className="provider-settings-header">
        <div><span className="section-kicker">MODEL CONNECTIONS</span><h1>{embedded ? "Provider 연결" : "모델·Provider 설정"}</h1><p>{resolvedWorkspaceId ? "현재 Workspace 설정" : "Workspace 확인 중"}</p></div>
        <button className="secondary-button" type="button" onClick={load} disabled={busy}>새로고침</button>
      </header>
      <div className={`provider-status ${status.kind}`} role="status"><span className="status-dot" aria-hidden="true" />{status.message}</div>

      {isSystemAdmin !== null ? <section className="provider-admin-panel" aria-labelledby="provider-admin-title">
        <div className="studio-section-heading"><div><span className="section-kicker">{isSystemAdmin ? "SYSTEM ADMIN" : "SHARED CONNECTIONS"}</span><h2 id="provider-admin-title">시스템 Provider 연결</h2></div>{isSystemAdmin ? <button className="secondary-button" type="button" onClick={() => { setSelectedId(null); setDraft(emptyConnectionDraft()); setCredential(""); }}>연결 추가</button> : <small>Endpoint·모델 설정은 시스템 관리자만 변경할 수 있습니다.</small>}</div>
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
                <label>Provider<select value={draft.provider_code} disabled={draft.version > 0} onChange={(event) => setDraft((current) => ({ ...current, provider_code: event.target.value }))}>{PROVIDERS.map((provider) => <option value={provider} key={provider}>{provider}</option>)}</select></label>
                <label>연결 이름<input value={draft.display_name} autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))} /></label>
                <label>Endpoint<input value={draft.base_url} autoComplete="off" placeholder={draft.version ? "보안을 위해 저장된 주소는 표시하지 않습니다" : "서버에서 검증할 Endpoint"} onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value }))} /></label>
                <label className="provider-field-wide">Logical model IDs<textarea value={draft.logical_model_ids} placeholder="Gateway 모델 ID를 줄바꿈으로 구분" onChange={(event) => setDraft((current) => ({ ...current, logical_model_ids: event.target.value }))} /></label>
              </> : <p className="provider-field-wide">공유 연결의 Endpoint와 모델 목록은 숨겨져 있습니다. 아래에서 이 연결의 API Key만 교체할 수 있습니다.</p>}
              <label>API Key 또는 Client Key<input type="password" value={credential} autoComplete="new-password" disabled={!canUsePersonalCredential} placeholder={!isSystemAdmin && !canUsePersonalCredential ? "관리자가 먼저 Provider 연결을 등록해야 합니다" : "키를 입력하세요"} onChange={(event) => setCredential(event.target.value)} /></label>
              {isSystemAdmin ? <label>현재 비밀번호<input type="password" value={administratorPassword} autoComplete="current-password" onChange={(event) => setAdministratorPassword(event.target.value)} /></label> : null}
            </div>
            {isSystemAdmin ? <label className="styled-check"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} /><span>연결 활성</span></label> : null}
            <div className="provider-detail-actions">
              {isSystemAdmin ? <button className="secondary-button" type="button" onClick={() => saveConnection(false)} disabled={!canMutate}>연결 저장</button> : null}
              <button className="primary-button" type="button" onClick={() => saveConnection(true)} disabled={!canReplaceCredential}>{isSystemAdmin ? "시스템 키 저장 및 연결 확인" : "내 계정 키 저장"}</button>
              {!isSystemAdmin ? <button className="secondary-button danger-button" type="button" onClick={deleteUserCredential} disabled={busy || !canUsePersonalCredential || !userCredentials[selectedConnection?.connection_id]}>내 계정 키 삭제</button> : null}
              {isSystemAdmin ? <><button className="secondary-button danger-button" type="button" onClick={deleteCredential} disabled={busy || !selectedConnection?.configured || !administratorPassword}>키 삭제</button><button className="secondary-button" type="button" onClick={refreshCatalog} disabled={busy || !selectedConnection || !administratorPassword}>카탈로그 새로고침</button></> : null}
            </div>
          </div>
        </div>
      </section> : null}

      <section className="workspace-model-defaults" aria-labelledby="workspace-default-title">
        <div className="studio-section-heading"><div><span className="section-kicker">WORKSPACE</span><h2 id="workspace-default-title">Workspace 기본 모델</h2></div></div>
        <div className="capability-default-grid">
          {ACTIVE_CAPABILITIES.map((capability) => {
            const choices = availableModels.filter((model) => model.effective_capabilities.includes(capability));
            return <article className="capability-default-card" key={capability}><strong>{CAPABILITY_LABELS[capability]}</strong><select aria-label={`${CAPABILITY_LABELS[capability]} 기본 모델`} value={defaultDrafts[capability] ?? ""} disabled={busy || !choices.length} onChange={(event) => setDefaultDrafts((current) => ({ ...current, [capability]: event.target.value }))}><option value="">선택 안 함</option>{choices.map((model) => <option value={modelKey(model.connection_id, model.model_id)} key={modelKey(model.connection_id, model.model_id)}>{model.display_name} · {model.model_id}</option>)}</select><button className="secondary-button" type="button" disabled={busy || !defaultDrafts[capability] || !modelDefaultsEtag} onClick={() => saveModelDefault(capability)}>기본 모델 저장</button><small>{choices.length ? `${choices.length}개 모델 사용 가능` : "선택 가능한 모델 없음"}</small></article>;
          })}
        </div>
        <div className="provider-model-grid">
          {adminAvailableModels.map(({ connection, model }) => {
            const correction = capabilityDrafts[modelKey(connection.connection_id, model.model_id)] ?? model.effective_capabilities;
            return <article className="provider-model-card" key={modelKey(connection.connection_id, model.model_id)}><header><div><strong>{formatModelChoice(connection, model)}</strong><small>Catalog v{model.catalog_version}</small></div><span className="connection-badge is-ready">{model.override_applied ? "관리자 보정" : "자동 판별"}</span></header><div className="model-capabilities">{model.effective_capabilities.map((capability) => ACTIVE_CAPABILITIES.includes(capability) ? <span className="capability-chip is-active" key={capability}>{CAPABILITY_LABELS[capability]}</span> : <button className="capability-chip is-pending" type="button" disabled title="실행 Adapter가 연결되지 않았습니다." key={capability}>{CAPABILITY_LABELS[capability]} · 준비 중</button>)}</div>{isSystemAdmin ? <details className="capability-correction"><summary>모델 기능 보정</summary><fieldset><legend className="sr-only">{formatModelChoice(connection, model)} 기능</legend><div className="role-chip-grid">{CAPABILITIES.map((capability) => { const ready = ACTIVE_CAPABILITIES.includes(capability); return <label className="role-chip" key={capability} title={ready ? undefined : "실행 Adapter가 연결되지 않았습니다."}><input type="checkbox" disabled={!ready} checked={correction.includes(capability)} onChange={() => toggleCapability(connection, model, capability)} /><span>{CAPABILITY_LABELS[capability]}{ready ? "" : " · 준비 중"}</span></label>; })}</div></fieldset><button className="secondary-button" type="button" onClick={() => saveCapabilities(connection, model)} disabled={busy || !administratorPassword}>기능 보정 저장</button></details> : null}</article>;
          })}
          {isSystemAdmin === true && !adminAvailableModels.length ? <div className="provider-empty"><strong>사용 가능한 모델이 없습니다.</strong><small>연결을 확인하고 카탈로그를 새로고침하세요.</small></div> : null}
        </div>
      </section>
    </Root>
  );
}
