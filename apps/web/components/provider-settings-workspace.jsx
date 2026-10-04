"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { providerSettingsApi } from "../lib/provider-settings-api.js";
import { summarizeConnectionUsage } from "./provider-settings-usage.js";

const MANAGED_MODEL_PROVIDERS = new Set();
const CREDENTIAL_REQUIRED_PROVIDERS = new Set([
  "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
  "OPENROUTER", "ANTHROPIC", "OMNIROUTE", "EOUL_GATEWAY", "SENTENCE_TRANSFORMERS", "CUSTOM"
]);
const COMPATIBLE_APIS = Object.freeze({
  openai_compatible: { label: "OpenAI 호환", apiType: "Chat Completions" },
  anthropic_compatible: { label: "Anthropic 호환", apiType: "Messages" },
});
const FIXED_AUTO_MODELS = Object.freeze({ OMNIROUTE: "auto", OPENROUTER: "openrouter/auto" });
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
    connection_id: operationKey("provider"),
    provider_code: "CUSTOM",
    display_name: "",
    provider_name: "",
    base_url: defaultEndpoint,
    logical_model_ids: "",
    auto_model_id: "",
    short_code: "",
    access_mode: "public",
    credential_requirement: "required",
    adapter_type: "openai_compatible",
    enabled: true,
    version: 0
  };
}

function withDefaultProviders(items) {
  return items;
}

function draftFromConnection(connection) {
  return {
    connection_id: connection.connection_id,
    provider_code: connection.provider_code,
    display_name: connection.display_name,
    provider_name: connection.provider_name ?? connection.provider_code,
    base_url: connection.base_url || "",
    logical_model_ids: (connection.allowed_model_ids ?? []).join("\n"),
    auto_model_id: connection.auto_model_id ?? FIXED_AUTO_MODELS[connection.provider_code] ?? "",
    short_code: connection.short_code ?? "",
    access_mode: connection.access_mode ?? "public",
    credential_requirement: connection.credential_requirement ?? "required",
    adapter_type: connection.adapter_type ?? connection.provider_code,
    enabled: Number(connection.version ?? 0) === 0 ? true : connection.enabled,
    version: connection.version
  };
}

export function formatModelChoice(connection, model) {
  return `${connection.display_name} · ${model.model_id}`;
}

export function projectProviderConnection(connection, userCredential = null) {
  const routeReady = connection?.provider_code !== "OMNIROUTE" || (connection.allowed_model_ids ?? []).some((modelId) =>
    (connection.models ?? []).some((model) => model.model_id === modelId && model.catalog_status === "ready"
      && (model.effective_capabilities ?? []).includes("text_generation")));
  if (connection?.access_mode === "personal") {
    const verified = connection.enabled === true && routeReady && userCredential?.verification_status === "verified";
    return { label: `비공용 · 개인 Key 필요 · ${verified ? "사용 가능" : "사용 대기"}`, verified };
  }
  if (connection?.access_mode === "public" && connection?.credential_requirement === "none") {
    const verified = connection.enabled === true && routeReady && connection.verification_status === "verified";
    return { label: `공용 · Key 불필요 · ${verified ? "사용 가능" : "확인 필요"}`, verified };
  }
  if (connection?.access_mode === "public") {
    const verified = connection.enabled === true && routeReady && connection.verification_status === "verified";
    return { label: `공용 · 관리자 Key 필요 · ${verified ? "사용 가능" : "확인 필요"}`, verified };
  }
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
    && !MANAGED_MODEL_PROVIDERS.has(connection?.provider_code)
    && (connection?.provider_code !== "OMNIROUTE" || connection?.configured === true);
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
    && !(connection?.provider_code === "CUSTOM" && connection?.access_mode === "personal" && !(connection.allowed_model_ids ?? []).length)
    && (draft?.provider_code !== "OMNIROUTE" || !isNewConnection || omniRouteSelectedModelCount(draft.logical_model_ids) <= 4)
    && (isNewConnection
      ? Boolean(draft?.display_name?.trim()) && Boolean(draft?.base_url?.trim())
      : Boolean(connection));
}

function normalizedLogicalModels(value) {
  return [...new Set(value.split(/[\n,]/u).map((item) => item.trim()).filter(Boolean))];
}

function omniRouteSelectedModelCount(value) {
  return normalizedLogicalModels(value).length;
}

function unchangedRouteSelection(draft, connection) {
  if (!Object.hasOwn(FIXED_AUTO_MODELS, draft.provider_code) || !draft.version || connection?.connection_id !== draft.connection_id) return false;
  const selected = normalizedLogicalModels(draft.logical_model_ids);
  const allowed = connection.allowed_model_ids ?? [];
  return allowed.length > 0 && selected.length === allowed.length && selected.every((modelId) => allowed.includes(modelId));
}

function updateLogicalModelSelection(current, modelId, checked) {
  const selected = normalizedLogicalModels(current);
  const next = checked ? [...selected, modelId] : selected.filter((item) => item !== modelId);
  return [...new Set(next)].join("\n");
}

function modelKey(connectionId, modelId) {
  return JSON.stringify([connectionId, modelId]);
}

export function ProviderSettingsWorkspace({ workspaceId, embedded = false, showNotebookLink = false }) {
  const [resolvedWorkspaceId, setResolvedWorkspaceId] = useState(workspaceId ?? null);
  const [isSystemAdmin, setIsSystemAdmin] = useState(null);
  const [connections, setConnections] = useState([]);
  const [userCredentials, setUserCredentials] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(() => emptyConnectionDraft());
  const [credential, setCredential] = useState("");
  const [previewModelIds, setPreviewModelIds] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [healthSettings, setHealthSettings] = useState({ interval_minutes: 60, version: 0 });
  const [healthIntervalDraft, setHealthIntervalDraft] = useState(60);
  const [status, setStatus] = useState({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });

  const selectConnection = useCallback((connection) => {
    setSelectedId(connection.connection_id);
    setDraft(draftFromConnection(connection));
    setCredential("");
    setPreviewModelIds([]);
    setConfirmDelete(false);
  }, []);

  const applyConnections = useCallback((items, preferredId = null) => {
    const projectedItems = withDefaultProviders(items);
    setConnections(projectedItems);
    const selected = projectedItems.find((item) => item.connection_id === preferredId) ?? projectedItems[0];
    if (selected) selectConnection(selected);
    else {
      setSelectedId(null);
      setDraft(emptyConnectionDraft());
      setPreviewModelIds([]);
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
    !connection.enabled || (connection.access_mode === "personal"
      ? userCredentials[connection.connection_id]?.verification_status !== "verified"
      : connection.verification_status !== "verified") || connection.catalog_status !== "ready"
      ? []
      : connection.models.filter((model) => model.catalog_status === "ready" && (connection.allowed_model_ids ?? []).includes(model.model_id)).map((model) => ({ connection, model }))
  )), [connections, userCredentials]);
  const availableModels = useMemo(() => (
    [...new Set([
      ...(draft.auto_model_id.trim() ? [draft.auto_model_id.trim()] : []),
      ...(selectedConnection?.models ?? [])
        .filter((model) => model.catalog_status === "ready")
        .map((model) => model.model_id),
      ...previewModelIds,
    ])]
  ), [draft.auto_model_id, selectedConnection, previewModelIds]);
  const selectedModelIds = useMemo(() => normalizedLogicalModels(draft.logical_model_ids), [draft.logical_model_ids]);
  const customProbeCount = selectedModelIds.length + (draft.auto_model_id.trim() && !selectedModelIds.includes(draft.auto_model_id.trim()) ? 1 : 0);

  async function saveConnection(includeCredential) {
    const connectionId = draft.connection_id.trim();
    const compatible = draft.provider_code === "CUSTOM" && Object.hasOwn(COMPATIBLE_APIS, draft.adapter_type);
    const route = draft.provider_code === "OMNIROUTE" || draft.provider_code === "OPENROUTER";
    const routeModelsUnchanged = unchangedRouteSelection(draft, selectedConnection);
    if (route && !(includeCredential && draft.version > 0) && !routeModelsUnchanged && omniRouteSelectedModelCount(draft.logical_model_ids) > 4) return;
    if (isSystemAdmin && route && draft.access_mode === "personal" && includeCredential) return;
    if (!isSystemAdmin) {
      if (!includeCredential || !connectionId || !credential || selectedConnection?.access_mode !== "personal" || !canUsePersonalCredential) return;
      setStatus({ kind: "saving", message: "개인 Provider 키를 저장하는 중입니다." });
      try {
        const current = userCredentials[connectionId];
        const result = await providerSettingsApi.replaceUserCredential(connectionId, {
          credential, expected_version: current?.credential_version ?? 0
        });
        setUserCredentials((items) => ({ ...items, [connectionId]: result.payload.data }));
        setCredential("");
        setStatus({ kind: "ready", message: result.payload.data.verification_status === "verified" ? "개인 Key 연결 시험에 성공했습니다. 이제 이 계정에서 사용할 수 있습니다." : "개인 Key 연결 시험이 확인되지 않았습니다." });
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
        const chosenModels = normalizedLogicalModels(draft.logical_model_ids);
        const modelIds = route && routeModelsUnchanged ? [...selectedConnection.allowed_model_ids] : chosenModels;
        const body = {
          display_name: draft.display_name.trim(),
          provider_name: draft.provider_name.trim(),
          base_url: draft.base_url.trim(),
          logical_model_ids: modelIds,
          allowed_model_ids: modelIds,
          auto_model_id: draft.auto_model_id.trim() || null,
          short_code: draft.short_code.trim().toUpperCase(),
          access_mode: draft.credential_requirement === "none" ? "public" : draft.access_mode,
          credential_requirement: draft.credential_requirement,
          adapter_type: draft.adapter_type,
          enabled: draft.enabled,
          expected_version: draft.version,
          ...(compatible && credential.trim()
            ? draft.access_mode === "personal" ? { test_credential: credential } : { credential }
            : (includeCredential || (route && draft.access_mode === "public" && credential.trim())) ? { credential } : {})
        };
        result = draft.version === 0
          ? await providerSettingsApi.createConnection({ connection_id: connectionId, provider_code: draft.provider_code, ...body }, operationKey("provider-create"))
          : await providerSettingsApi.updateConnection(connectionId, body, operationKey("provider-update"));
      }
      const item = result.payload.data;
      applyConnections([...connections.filter((connection) => connection.connection_id !== item.connection_id), item], item.connection_id);
      setCredential("");
      setStatus({ kind: "ready", message: item.verification_status === "verified" ? "연결 시험에 성공했고 Provider 연결을 저장했습니다." : "Provider 연결을 사용 대기 상태로 저장했습니다." });
    } catch (error) {
      if (!compatible) setCredential("");
      setStatus({ kind: "error", message: safeProviderErrorMessage(action, error) });
    }
  }

  async function previewModels() {
    if (!isSystemAdmin || draft.provider_code !== "CUSTOM" || !Object.hasOwn(COMPATIBLE_APIS, draft.adapter_type) || (draft.credential_requirement === "required" && !credential.trim())) return;
    setStatus({ kind: "saving", message: "모델 목록을 조회하는 중입니다." });
    try {
      const result = await providerSettingsApi.previewModels({
        connection_id: draft.connection_id.trim(), provider_code: "CUSTOM",
        adapter_type: draft.adapter_type, base_url: draft.base_url.trim(),
        ...(credential.trim() ? { credential } : {}),
      });
      const ids = Array.isArray(result.payload?.data?.model_ids)
        ? result.payload.data.model_ids.filter((item) => typeof item === "string" && item.trim())
        : [];
      setPreviewModelIds(ids);
      setStatus({ kind: "ready", message: ids.length
        ? `${ids.length}개 모델을 조회했습니다. 사용할 모델을 선택하거나 모델 ID를 직접 입력하세요.`
        : "조회된 모델이 없습니다. 모델 ID를 직접 입력해 시험할 수 있습니다." });
    } catch {
      setPreviewModelIds([]);
      setStatus({ kind: "error", message: "모델 목록을 조회하지 못했습니다. 모델 ID를 직접 입력해 시험할 수 있습니다." });
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
      setStatus({ kind: "ready", message: result.payload.data.interval_minutes === 0 ? "정기 확인을 중지했습니다." : "연결 상태 확인 주기를 저장했습니다." });
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
      setStatus({ kind: "ready", message: "시스템 Key를 삭제했습니다. 연결과 모델 설정은 유지됩니다." });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("credential", error) });
    }
  }

  async function deleteConnection() {
    if (!isSystemAdmin || !selectedConnection || !confirmDelete) return;
    setStatus({ kind: "saving", message: "Provider 연결을 삭제하는 중입니다." });
    try {
      await providerSettingsApi.deleteConnection(selectedConnection.connection_id, {
        expected_version: selectedConnection.version
      }, operationKey("provider-connection-delete"));
      applyConnections(connections.filter((item) => item.connection_id !== selectedConnection.connection_id));
      setConfirmDelete(false);
      setStatus({ kind: "ready", message: "Provider 연결을 삭제했습니다." });
    } catch (error) {
      setConfirmDelete(false);
      setStatus({ kind: "error", message: safeProviderErrorMessage("provider", error) });
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
  const compatible = draft.provider_code === "CUSTOM" && Object.hasOwn(COMPATIBLE_APIS, draft.adapter_type);
  const pendingCompatible = compatible && draft.version === 0 && draft.access_mode === "personal"
    && !credential.trim() && selectedModelIds.length === 0 && !draft.auto_model_id.trim();
  const personalCustomProbeNeeded = compatible && draft.version > 0 && draft.access_mode === "personal"
    && (draft.base_url.trim() !== selectedConnection?.base_url
      || draft.auto_model_id.trim() !== (selectedConnection?.auto_model_id ?? "")
      || draft.access_mode !== selectedConnection?.access_mode
      || (draft.enabled && !selectedConnection?.enabled)
      || selectedModelIds.length !== (selectedConnection?.allowed_model_ids ?? []).length
      || selectedModelIds.some((modelId) => !(selectedConnection?.allowed_model_ids ?? []).includes(modelId)));
  const routeModelsUnchanged = unchangedRouteSelection(draft, selectedConnection);
  const managedModels = MANAGED_MODEL_PROVIDERS.has(selectedConnection?.provider_code);
  const canMutate = isSystemAdmin === true && !busy && Boolean(draft.connection_id.trim()) && Boolean(draft.provider_name.trim()) && Boolean(draft.display_name.trim()) && Boolean(draft.base_url.trim())
    && (!draft.auto_model_id || (draft.auto_model_id.length <= 256 && /^\S+$/u.test(draft.auto_model_id)))
    && (!Object.hasOwn(FIXED_AUTO_MODELS, draft.provider_code) || routeModelsUnchanged || omniRouteSelectedModelCount(draft.logical_model_ids) <= 4)
    && (!personalCustomProbeNeeded || Boolean(credential.trim()))
    && (!compatible || pendingCompatible || ((selectedModelIds.length >= 1 || draft.auto_model_id.trim()) && selectedModelIds.length <= 4 && (draft.version > 0 || draft.credential_requirement === "none" || Boolean(credential.trim()))));
  const canPreview = isSystemAdmin === true && compatible && !busy
    && Boolean(draft.connection_id.trim()) && Boolean(draft.base_url.trim()) && (draft.credential_requirement === "none" || Boolean(credential.trim()));
  const personalCustomWithoutModels = !isSystemAdmin && selectedConnection?.provider_code === "CUSTOM"
    && selectedConnection?.access_mode === "personal" && !(selectedConnection.allowed_model_ids ?? []).length;
  const canUsePersonalCredential = !personalCustomWithoutModels
    && (isSystemAdmin || (Number(selectedConnection?.version ?? 0) > 0 && selectedConnection?.access_mode === "personal"));
  const adminSystemKeyAllowed = draft.credential_requirement === "required"
    && !(draft.provider_code === "OMNIROUTE" && draft.access_mode === "personal");
  const Root = embedded ? "div" : "main";

  return (
    <Root className={`provider-settings-shell ${embedded ? "is-embedded" : ""}`}>
      <header className="provider-settings-header">
        <div><span className="section-kicker">MODEL CONNECTIONS</span><h1>{embedded ? "Provider 연결" : "모델·Provider 설정"}</h1><p>{resolvedWorkspaceId ? "현재 Workspace 설정" : "Workspace 확인 중"}</p></div>
        <div className="provider-settings-navigation">{showNotebookLink ? <a className="secondary-button" href="/notebooks">Notebook으로</a> : null}<button className="secondary-button" type="button" onClick={load} disabled={busy}>새로고침</button></div>
      </header>
      <div className={`provider-status ${status.kind}`} role="status"><span className="status-dot" aria-hidden="true" />{status.message}</div>

      {isSystemAdmin !== null ? <section className="provider-admin-panel" aria-labelledby="provider-admin-title">
        <div className="studio-section-heading"><div><span className="section-kicker">{isSystemAdmin ? "SYSTEM ADMIN" : "SHARED CONNECTIONS"}</span><h2 id="provider-admin-title">시스템 Provider 연결</h2><small>{connectionUsage.label} · {connectionUsage.description}</small></div>{isSystemAdmin ? <div className="provider-heading-actions"><label>상태 확인 주기<select aria-label="상태 확인 주기" value={healthIntervalDraft} onChange={(event) => setHealthIntervalDraft(Number(event.target.value))}><option value={0}>사용 안 함 (0분)</option><option value={60}>60분</option><option value={120}>120분</option><option value={360}>360분</option><option value={720}>720분</option><option value={1440}>1440분</option></select></label><button className="secondary-button" type="button" onClick={saveHealthSettings} disabled={busy || Number(healthIntervalDraft) === Number(healthSettings.interval_minutes)}>주기 저장</button><button className="secondary-button" type="button" onClick={() => { setSelectedId(null); setDraft(emptyConnectionDraft()); setCredential(""); setPreviewModelIds([]); }}>연결 추가</button></div> : <small>Endpoint·모델 설정은 시스템 관리자만 변경할 수 있습니다.</small>}</div>
        <div className="provider-settings-layout">
          <div className="provider-connection-list" aria-label="Provider 연결 목록">
            {connections.map((connection) => { const projected = projectProviderConnection(connection, userCredentials[connection.connection_id]); return <button className="provider-card" type="button" aria-pressed={selectedId === connection.connection_id} onClick={() => selectConnection(connection)} key={connection.connection_id}><span className="provider-monogram" aria-hidden="true">{connection.short_code ?? "??"}</span><span><strong>{connection.display_name}</strong><small>{connection.provider_name ?? connection.provider_code} · {projected.label}</small></span><span className={`provider-state-dot ${projected.verified ? "is-ready" : ""}`} aria-hidden="true" /></button>; })}
            {!connections.length ? <div className="provider-empty"><strong>등록된 연결이 없습니다.</strong><small>{isSystemAdmin ? "연결 이름과 Endpoint를 입력해 첫 연결을 추가하세요." : "시스템 관리자에게 Provider 연결 등록을 요청하세요."}</small></div> : null}
          </div>

          <div className="provider-detail">
            <header><div><span className="section-kicker">{draft.version ? "SELECTED CONNECTION" : "NEW CONNECTION"}</span><h2>{draft.display_name || "새 Provider 연결"}</h2></div>{selectedConnection ? <span className={`connection-badge ${projectProviderConnection(selectedConnection, userCredentials[selectedConnection.connection_id]).verified ? "is-ready" : ""}`}>{projectProviderConnection(selectedConnection, userCredentials[selectedConnection.connection_id]).label}</span> : null}</header>
            <div className="provider-detail-grid">
              {isSystemAdmin ? <>
                {draft.version > 0
                  ? <label>호환 방식<input readOnly value={compatible ? COMPATIBLE_APIS[draft.adapter_type].label : draft.provider_code === "CUSTOM" ? "기존 CUSTOM (OpenAI 호환)" : "기존 Provider 방식"} /></label>
                  : <label>호환 방식<select value={draft.adapter_type} onChange={(event) => {
                    const protocol = event.target.value;
                    setCredential(""); setPreviewModelIds([]);
                    setDraft((current) => ({ ...current, provider_code: "CUSTOM",
                      adapter_type: protocol, provider_name: "",
                      base_url: "", logical_model_ids: "", auto_model_id: "", credential_requirement: "required",
                      access_mode: "public" }));
                  }}><option value="openai_compatible">OpenAI 호환</option><option value="anthropic_compatible">Anthropic 호환</option></select></label>}
                {compatible
                  ? <label>API 유형<input readOnly value={COMPATIBLE_APIS[draft.adapter_type]?.apiType ?? "Chat Completions"} /></label>
                  : draft.provider_code === "CUSTOM"
                    ? <label>Provider 방식<input readOnly value="CUSTOM" /></label>
                  : <label>Provider 방식<input readOnly value={draft.provider_code} /></label>}
                <label>Provider 표시 이름<input value={draft.provider_name} maxLength={256} autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, provider_name: event.target.value }))} /></label>
                <label>연결 이름<input value={draft.display_name} autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))} /></label>
                <label>두 글자 약어<input aria-label="두 글자 약어" value={draft.short_code} maxLength={2} pattern="[A-Z]{2}" autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, short_code: event.target.value.toUpperCase() }))} /></label>
                <label>Endpoint<input value={draft.base_url} autoComplete="off" placeholder={draft.version ? "보안을 위해 저장된 주소는 표시하지 않습니다" : "서버에서 검증할 Endpoint"} onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value }))} /></label>
                <label>인증 유형{draft.version > 0 && compatible ? <input readOnly value={draft.credential_requirement === "none" ? "Key 불필요" : "Key 필요"} /> : <select value={draft.credential_requirement} onChange={(event) => { if (event.target.value === "none") setCredential(""); setDraft((current) => ({ ...current, credential_requirement: event.target.value, access_mode: event.target.value === "none" ? "public" : current.access_mode })); }}><option value="required">Key 필요</option><option value="none">Key 불필요</option></select>}</label>
                {Object.hasOwn(FIXED_AUTO_MODELS, draft.provider_code) ? <label>Auto 모델 ID<input readOnly value={draft.auto_model_id} /></label> : null}
                {compatible && draft.adapter_type === "openai_compatible" ? <label className="styled-check"><input type="checkbox" checked={Boolean(draft.auto_model_id)} onChange={(event) => setDraft((current) => ({ ...current, auto_model_id: event.target.checked ? "auto" : "" }))} /><span>라우터 Auto 사용</span></label> : null}
                {compatible && draft.adapter_type === "openai_compatible" && draft.auto_model_id ? <label>Auto 모델 ID<input value={draft.auto_model_id} maxLength={256} autoComplete="off" onChange={(event) => setDraft((current) => ({ ...current, auto_model_id: event.target.value }))} /></label> : null}
                <fieldset className="provider-field-wide provider-model-picker">
                  <legend>사용 허용 모델</legend>
                  <p>체크한 모델만 사용 허용 목록에 저장합니다. 모델을 지정하지 않는 호출은 Provider의 기준 모델을 사용합니다.</p>
                  {managedModels ? <small>이 Provider가 모델을 직접 관리합니다.</small> : availableModels.length
                    ? <div className="provider-model-options">{availableModels.map((modelId) => {
                      const model = selectedConnection?.models?.find((item) => item.model_id === modelId);
                      const isAuto = modelId === draft.auto_model_id;
                      return <label key={modelId}><input type="checkbox" checked={selectedModelIds.includes(modelId)} onChange={(event) => setDraft((current) => ({ ...current, logical_model_ids: updateLogicalModelSelection(current.logical_model_ids, modelId, event.target.checked) }))} /><span>{isAuto ? `Auto · ${modelId}` : modelId}{model?.catalog_origin === "logical" ? " · 논리 모델" : ""}</span></label>;
                    })}</div>
                    : <small>{compatible ? "모델 조회는 선택 사항입니다. 모델 ID를 직접 입력해 시험할 수 있습니다." : "조회된 모델이 없습니다. 모델 조회 후 허용할 모델을 선택하세요."}</small>}
                  {compatible || draft.provider_code === "OMNIROUTE" ? <><label className="provider-manual-models">모델 ID 직접 입력<textarea value={draft.logical_model_ids} rows={3} onChange={(event) => setDraft((current) => ({ ...current, logical_model_ids: event.target.value }))} /></label><small>최대 4개 모델을 허용할 수 있습니다.{draft.adapter_type === "anthropic_compatible" ? " Anthropic 모델 목록은 첫 페이지만 표시될 수 있습니다." : ""}</small></> : null}
                  {selectedModelIds.length ? <button type="button" className="provider-model-clear" onClick={() => setDraft((current) => ({ ...current, logical_model_ids: "" }))}>허용 목록 비우기</button> : null}
                </fieldset>
              </> : <div className="provider-field-wide"><p>연결 이름과 허용 모델은 읽기 전용입니다.</p><p>Endpoint: {selectedConnection?.base_url ?? ""}</p><p>사용 허용 모델: {(selectedConnection?.allowed_model_ids ?? []).join(", ") || "없음"}</p></div>}
              {(isSystemAdmin ? adminSystemKeyAllowed : selectedConnection?.access_mode === "personal") ? <label>API Key 또는 Client Key{isSystemAdmin && selectedConnection?.configured ? <small className="provider-field-status is-saved">저장됨 · 새 키를 입력하면 교체됩니다.</small> : null}{isSystemAdmin && compatible && draft.access_mode === "personal" ? <small>개인 연결의 시험 Key는 저장하지 않습니다.</small> : null}{personalCustomWithoutModels ? <small>관리자가 사용할 모델을 허용한 뒤 Key를 시험할 수 있습니다.</small> : null}<input type="password" value={credential} autoComplete="new-password" disabled={!canUsePersonalCredential} onChange={(event) => setCredential(event.target.value)} /></label> : null}
            </div>
            {isSystemAdmin ? <><label className="styled-check"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} /><span>사용 후보에 포함</span></label><label className="styled-check"><input type="checkbox" checked={draft.access_mode === "public"} disabled={draft.credential_requirement === "none"} onChange={(event) => setDraft((current) => ({ ...current, access_mode: event.target.checked ? "public" : "personal" }))} /><span>공용 사용{draft.credential_requirement === "none" ? " (Key 불필요 연결은 항상 공용)" : ""}</span></label></> : null}
            <div className="provider-detail-actions">
              {isSystemAdmin && compatible ? <span className="provider-test-cost">{pendingCompatible ? "시험 없이 사용 대기 연결을 등록합니다. Provider 호출과 사용료가 없습니다." : `시험 대상 ${customProbeCount}개 모델 · 실제 시험 시 사용료가 발생할 수 있습니다.`}</span> : null}
              {draft.provider_code === "OMNIROUTE" ? <span className="provider-test-cost">시험 대상 {omniRouteSelectedModelCount(draft.logical_model_ids) || 1}개 모델 · {selectedModelIds.length ? "선택한 모델을 각각 시험합니다." : "모델 미지정 기준 모델을 시험합니다."} 공용 연결은 정기 점검에서도 각각 호출되어 사용료가 발생할 수 있습니다.{selectedConnection?.provider_code === "OMNIROUTE" && (selectedConnection.allowed_model_ids ?? []).length > omniRouteSelectedModelCount(draft.logical_model_ids) ? ` 기존 허용 목록은 ${selectedConnection.allowed_model_ids.length}개 모델을 Key 재시험·정기 점검에서 각각 호출합니다.` : ""}</span> : null}
              {isSystemAdmin ? <button className="secondary-button" type="button" onClick={() => saveConnection(false)} disabled={!canMutate || !/^[A-Z]{2}$/u.test(draft.short_code)}>{pendingCompatible ? "사용 대기 연결 등록" : "연결 시험 및 저장"}</button> : null}
              {(isSystemAdmin ? adminSystemKeyAllowed && !compatible : selectedConnection?.access_mode === "personal") ? <button className="primary-button" type="button" onClick={() => saveConnection(true)} disabled={!canSaveCredential(selectedConnection, draft, credential, busy) || !canUsePersonalCredential}>{isSystemAdmin ? "시스템 키 시험 및 저장" : "내 계정 키 시험 및 저장"}</button> : null}
              {!isSystemAdmin && selectedConnection?.access_mode === "personal" ? <button className="secondary-button danger-button" type="button" onClick={deleteUserCredential} disabled={busy || !canUsePersonalCredential || !userCredentials[selectedConnection?.connection_id]}>내 계정 키 삭제</button> : null}
              {isSystemAdmin ? <button className="secondary-button danger-button" type="button" onClick={deleteCredential} disabled={busy || !selectedConnection?.configured}>키 삭제</button> : null}
              {isSystemAdmin && compatible ? <button className="secondary-button" type="button" onClick={previewModels} disabled={!canPreview} title={draft.credential_requirement === "none" ? "API Key 없이 모델 목록을 조회합니다." : "입력한 Key로 모델 목록을 조회합니다. Provider에 따라 사용료가 발생할 수 있습니다."}>모델 목록 조회</button> : null}
              {isSystemAdmin && !managedModels && (!compatible || (selectedConnection?.version > 0 && selectedConnection?.access_mode === "public" && (selectedConnection?.configured || selectedConnection?.credential_requirement === "none"))) ? <button className="secondary-button" type="button" onClick={refreshCatalog} disabled={!canRefreshCatalog(selectedConnection, busy)} title={selectedConnection?.credential_requirement === "none" ? "API Key 없이 모델 목록을 조회합니다." : selectedConnection?.configured ? "저장된 API Key로 모델 목록을 수동 조회합니다." : "먼저 API Key를 저장하세요."}>모델 조회</button> : null}
              {isSystemAdmin && selectedConnection?.version > 0 ? <button className="secondary-button danger-button" type="button" onClick={() => setConfirmDelete(true)} disabled={busy}>연결 삭제</button> : null}
              {isSystemAdmin && confirmDelete ? <div className="provider-delete-confirm" role="group" aria-label="연결 삭제 확인"><span>이 연결을 실제 삭제합니다. 참조가 있으면 삭제되지 않습니다.</span><button type="button" className="danger-button" onClick={deleteConnection}>삭제 확인</button><button type="button" className="secondary-button" onClick={() => setConfirmDelete(false)}>취소</button></div> : null}
            </div>
          </div>
        </div>
      </section> : null}

      <section className="workspace-model-defaults" aria-labelledby="provider-models-title">
        <div className="studio-section-heading"><div><span className="section-kicker">MODEL CATALOG</span><h2 id="provider-models-title">사용 허용 모델</h2><small>{isSystemAdmin ? "조회된 카탈로그와 사용 허용 목록을 구분해 관리합니다." : "시스템 관리자가 허용한 모델만 표시합니다."}</small></div></div>
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
