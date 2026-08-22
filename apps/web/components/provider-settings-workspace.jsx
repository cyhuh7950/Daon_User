"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { providerSettingsApi } from "../lib/provider-settings-api.js";

const PROVIDERS = Object.freeze([
  "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
  "OPENROUTER", "ANTHROPIC", "OLLAMA"
]);
const ROLES = Object.freeze([
  "text", "vision", "document_parser", "audio_understanding", "speech_to_text", "embedding", "reranker"
]);
const ROLE_LABELS = Object.freeze({
  text: "텍스트 생성", vision: "이미지 이해", document_parser: "문서 분석",
  audio_understanding: "오디오 이해", speech_to_text: "음성 변환", embedding: "임베딩", reranker: "재정렬",
});

export function projectProviderConnection(profile) {
  if (!profile?.active) return { label: profile?.credential_configured ? "비활성 · Credential 설정됨" : "미설정", verified: false };
  if (!profile.credential_configured) return { label: "활성 · Credential 미설정", verified: false };
  return { label: "활성 · Credential 설정됨 · 연결 미확인", verified: false };
}

export function projectProviderEndpoint(baseUrl) {
  return typeof baseUrl === "string" && baseUrl.trim() ? "Endpoint 설정됨" : "Endpoint 미설정";
}

export function safeProviderErrorMessage(action, error) {
  const prefix = ({ provider: "Provider 설정", model: "모델", bindings: "역할 매핑" })[action] ?? "설정";
  if (error?.code === "VERSION_CONFLICT" || error?.code === "PRECONDITION_FAILED") return `${prefix}이 다른 변경과 충돌했습니다. 새로고침 후 다시 시도해 주세요.`;
  if (error?.code === "FORBIDDEN") return `${prefix}을 변경할 권한이 없습니다.`;
  return `${prefix}을 저장하지 못했습니다. 다시 시도해 주세요.`;
}

function operationKey(prefix) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function initialDraft(providerCode) {
  return {
    provider_code: providerCode,
    base_url: "",
    active: false,
    credential_configured: false,
    profile_version: 0
  };
}

function initialDeploymentDraft(providerCode) {
  return {
    provider_code: providerCode,
    deployment_id: `deployment-${providerCode.toLowerCase()}-${crypto.randomUUID()}`,
    model_id: "",
    roles: ["text"],
    active: false,
    selected: false,
    version: 0
  };
}

export function ProviderSettingsWorkspace({ workspaceId, embedded = false }) {
  const [resolvedWorkspaceId, setResolvedWorkspaceId] = useState(workspaceId ?? null);
  const [drafts, setDrafts] = useState(() => Object.fromEntries(PROVIDERS.map((code) => [code, initialDraft(code)])));
  const [deploymentDrafts, setDeploymentDrafts] = useState([]);
  const [bindings, setBindings] = useState({});
  const [bindingVersion, setBindingVersion] = useState(0);
  const [bindingEtag, setBindingEtag] = useState(null);
  const [status, setStatus] = useState({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });
  const [selectedProvider, setSelectedProvider] = useState("UPSTAGE");
  const [endpointEdits, setEndpointEdits] = useState({});
  const [connectionChecks, setConnectionChecks] = useState({});

  const load = useCallback(async () => {
    setStatus({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });
    try {
      const sessionResult = workspaceId ? null : await providerSettingsApi.getSession();
      const activeWorkspaceId = workspaceId ?? sessionResult?.payload?.data?.workspace_id;
      if (typeof activeWorkspaceId !== "string" || !activeWorkspaceId.trim()) {
        const error = new Error("RESOURCE_UNAVAILABLE");
        error.code = "RESOURCE_UNAVAILABLE";
        throw error;
      }
      setResolvedWorkspaceId(activeWorkspaceId);
      const [profilesResult, deploymentsResult, policyResult] = await Promise.all([
        providerSettingsApi.listProfiles(activeWorkspaceId),
        providerSettingsApi.listDeployments(activeWorkspaceId),
        providerSettingsApi.getModelPolicy(activeWorkspaceId)
      ]);
      setDrafts({ ...Object.fromEntries(PROVIDERS.map((code) => [code, initialDraft(code)])), ...Object.fromEntries(profilesResult.payload.data.map((profile) => {
        return [profile.provider_code, {
          ...initialDraft(profile.provider_code),
          base_url: profile.base_url,
          active: profile.active,
          credential_configured: profile.credential_configured,
          profile_version: profile.version
        }];
      })) });
      setDeploymentDrafts(deploymentsResult.payload.data.map((item) => ({ ...item })));
      setBindings(policyResult.payload.data.bindings);
      setBindingVersion(policyResult.payload.data.version);
      setBindingEtag(policyResult.etag ?? `"model-policy:${activeWorkspaceId}:${policyResult.payload.data.version}"`);
      setStatus({ kind: "ready", message: "Provider 설정을 조회했습니다." });
    } catch (error) {
      setStatus({ kind: "error", message: "Provider 설정을 불러오지 못했습니다. 연결 상태를 확인해 주세요." });
    }
  }, [workspaceId]);

  useEffect(() => { load(); }, [load]);

  function workspaceIdForWrite() {
    if (resolvedWorkspaceId) return resolvedWorkspaceId;
    const error = new Error("RESOURCE_UNAVAILABLE");
    error.code = "RESOURCE_UNAVAILABLE";
    throw error;
  }

  const deployments = useMemo(
    () => deploymentDrafts.filter((item) => item.model_id && item.active),
    [deploymentDrafts]
  );

  function updateDraft(providerCode, field, value) {
    setDrafts((current) => ({
      ...current,
      [providerCode]: { ...current[providerCode], [field]: value }
    }));
  }

  function updateDeployment(deploymentId, field, value) {
    setDeploymentDrafts((current) => current.map((item) => (
      item.deployment_id === deploymentId ? { ...item, [field]: value } : item
    )));
  }

  function toggleRole(deploymentId, role) {
    const current = deploymentDrafts.find((item) => item.deployment_id === deploymentId);
    if (!current) return;
    const roles = current.roles.includes(role)
      ? current.roles.filter((item) => item !== role)
      : [...current.roles, role];
    updateDeployment(deploymentId, "roles", roles.length ? roles : [role]);
  }

  function addDeployment(providerCode) {
    setDeploymentDrafts((current) => [...current, initialDeploymentDraft(providerCode)]);
  }

  async function saveProvider(providerCode) {
    const draft = drafts[providerCode];
    const nextBaseUrl = Object.hasOwn(endpointEdits, providerCode) ? endpointEdits[providerCode] : draft.base_url;
    setStatus({ kind: "saving", message: `${providerCode} 설정을 저장하는 중입니다.` });
    try {
      const activeWorkspaceId = workspaceIdForWrite();
      const profileResult = await providerSettingsApi.saveProfile({
        workspace_id: activeWorkspaceId,
        provider_code: providerCode,
        base_url: nextBaseUrl,
        active: draft.active,
        expected_version: draft.profile_version
      }, operationKey(`provider-${providerCode}`));
      setDrafts((current) => ({
        ...current,
        [providerCode]: {
          ...current[providerCode],
          base_url: nextBaseUrl,
          profile_version: profileResult.payload.data.version,
          credential_configured: profileResult.payload.data.credential_configured
        }
      }));
      setEndpointEdits((current) => { const next = { ...current }; delete next[providerCode]; return next; });
      setStatus({ kind: "ready", message: `${providerCode} 설정을 저장했습니다.` });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("provider", error) });
    }
  }

  async function checkConnection(providerCode) {
    setStatus({ kind: "checking", message: `${providerCode} 연결을 확인하는 중입니다.` });
    try {
      const result = await providerSettingsApi.checkConnection(workspaceIdForWrite(), providerCode);
      setConnectionChecks((current) => ({ ...current, [providerCode]: result }));
      setStatus({
        kind: result.status === "ready" ? "ready" : "warning",
        message: result.status === "ready"
          ? `${providerCode} 연결을 확인했습니다.`
          : `${providerCode} 연결을 사용할 수 없습니다. Credential과 Provider 상태를 확인해 주세요.`,
      });
    } catch (error) {
      setConnectionChecks((current) => ({ ...current, [providerCode]: { providerCode, status: "unavailable", checkedAt: null } }));
      setStatus({ kind: "error", message: "Provider 연결을 확인하지 못했습니다. 다시 시도해 주세요." });
    }
  }

  async function saveDeployment(deploymentId) {
    const draft = deploymentDrafts.find((item) => item.deployment_id === deploymentId);
    if (!draft) return;
    setStatus({ kind: "saving", message: `${draft.provider_code} 모델을 저장하는 중입니다.` });
    try {
      const activeWorkspaceId = workspaceIdForWrite();
      const result = await providerSettingsApi.saveDeployment({
        workspace_id: activeWorkspaceId,
        deployment_id: draft.deployment_id,
        provider_code: draft.provider_code,
        model_id: draft.model_id,
        roles: draft.roles,
        active: draft.active,
        selected: draft.selected,
        expected_version: draft.version
      }, operationKey(`deployment-${draft.provider_code}`));
      setDeploymentDrafts((current) => current.map((item) => (
        item.deployment_id === deploymentId ? { ...result.payload.data } : item
      )));
      setStatus({ kind: "ready", message: `${draft.provider_code} 모델을 저장했습니다.` });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("model", error) });
    }
  }

  async function saveBindings() {
    setStatus({ kind: "saving", message: "역할 매핑을 저장하는 중입니다." });
    try {
      const activeWorkspaceId = workspaceIdForWrite();
      const result = await providerSettingsApi.saveModelPolicy(
        activeWorkspaceId,
        { bindings, expected_version: bindingVersion },
        bindingEtag,
        operationKey("model-policy")
      );
      setBindingVersion(result.payload.data.version);
      setBindingEtag(result.etag);
      setStatus({ kind: "ready", message: "역할 매핑을 저장했습니다." });
    } catch (error) {
      setStatus({ kind: "error", message: safeProviderErrorMessage("bindings", error) });
    }
  }

  const selectedDraft = drafts[selectedProvider] ?? initialDraft(selectedProvider);
  const selectedDeployments = deploymentDrafts.filter((item) => item.provider_code === selectedProvider);
  const Root = embedded ? "div" : "main";
  return (
    <Root className={`provider-settings-shell ${embedded ? "is-embedded" : ""}`}>
      <header className="provider-settings-header"><div><span className="section-kicker">MODEL CONNECTIONS</span><h1>{embedded ? "Provider 연결" : "모델·Provider 설정"}</h1><p>{resolvedWorkspaceId ? "현재 Workspace 설정" : "Workspace 확인 중"}</p></div><button className="secondary-button" type="button" onClick={load}>새로고침</button></header>
      <div className={`provider-status ${status.kind}`} role="status"><span className="status-dot" aria-hidden="true" />{status.message}</div>
      <div className="provider-settings-layout">
        <section aria-labelledby="provider-list-title"><h2 id="provider-list-title">Provider</h2><div className="provider-grid">
          {PROVIDERS.map((providerCode) => { const draft = drafts[providerCode] ?? initialDraft(providerCode); const connection = projectProviderConnection(draft); return <button className="provider-card" type="button" aria-pressed={selectedProvider === providerCode} onClick={() => setSelectedProvider(providerCode)} key={providerCode}><span className="provider-monogram" aria-hidden="true">{providerCode.slice(0, 1)}</span><span><strong>{providerCode}</strong><small>{connection.label}</small></span><span className="provider-state-dot" aria-hidden="true" /></button>; })}
        </div></section>
        <section className="provider-detail" aria-labelledby="provider-detail-title"><header><div><span className="section-kicker">SELECTED PROVIDER</span><h2 id="provider-detail-title">{selectedProvider}</h2></div><span className="connection-badge">{connectionChecks[selectedProvider]?.status === "ready" ? "연결 확인됨" : projectProviderConnection(selectedDraft).label}</span></header>
          <div className="provider-detail-grid"><div className="credential-summary"><span>Endpoint</span><strong>{projectProviderEndpoint(selectedDraft.base_url)}</strong><small>내부 주소 원문은 화면에 표시하지 않습니다.</small></div><label>Endpoint 변경<input value={endpointEdits[selectedProvider] ?? ""} autoComplete="off" placeholder="새 Endpoint를 입력하면 기존 값을 교체합니다" onChange={(event) => setEndpointEdits((current) => ({ ...current, [selectedProvider]: event.target.value }))} /></label><div className="credential-summary"><span>Credential</span><strong>{selectedDraft.credential_configured ? "설정됨" : "미설정"}</strong><small>Credential 원문은 화면에 표시하지 않습니다.</small></div></div>
          <label className="styled-check"><input type="checkbox" checked={selectedDraft.active} onChange={(event) => updateDraft(selectedProvider, "active", event.target.checked)} /><span>Provider 활성</span></label>
          <div className="provider-detail-actions"><button className="secondary-button" type="button" onClick={() => checkConnection(selectedProvider)} disabled={status.kind === "checking" || !selectedDraft.active || !selectedDraft.credential_configured}>연결 시험</button><button className="primary-button" type="button" onClick={() => saveProvider(selectedProvider)} disabled={status.kind === "saving"}>Provider 저장</button></div>
          <div className="deployment-list"><div className="studio-section-heading"><h3>모델</h3><button className="secondary-button" type="button" onClick={() => addDeployment(selectedProvider)}>모델 추가</button></div>
            {selectedDeployments.length ? selectedDeployments.map((deployment) => <section className="deployment-card" key={deployment.deployment_id}><div className="provider-detail-grid"><label>Deployment ID<input value={deployment.deployment_id} disabled={deployment.version > 0} onChange={(event) => updateDeployment(deployment.deployment_id, "deployment_id", event.target.value)} /></label><label>모델 ID<input value={deployment.model_id} onChange={(event) => updateDeployment(deployment.deployment_id, "model_id", event.target.value)} /></label></div><fieldset><legend>역할</legend><div className="role-chip-grid">{ROLES.map((role) => <label className="role-chip" key={role}><input type="checkbox" checked={deployment.roles.includes(role)} onChange={() => toggleRole(deployment.deployment_id, role)} /><span>{ROLE_LABELS[role]}</span></label>)}</div></fieldset><div className="provider-switches"><label className="styled-check"><input type="checkbox" checked={deployment.active} onChange={(event) => updateDeployment(deployment.deployment_id, "active", event.target.checked)} /><span>활성</span></label><label className="styled-check"><input type="checkbox" checked={deployment.selected} onChange={(event) => updateDeployment(deployment.deployment_id, "selected", event.target.checked)} /><span>기본 선택</span></label></div><button className="secondary-button" type="button" onClick={() => saveDeployment(deployment.deployment_id)}>모델 저장</button></section>) : <div className="provider-empty"><strong>등록된 모델이 없습니다.</strong><small>실제 모델 연결 전에는 생성 Action이 활성화되지 않습니다.</small></div>}
          </div>
        </section>
      </div>
      <details className="role-bindings"><summary>역할 매핑</summary><div className="role-grid">{ROLES.map((role) => <label key={role}>{ROLE_LABELS[role]}<select value={bindings[role] ?? ""} onChange={(event) => setBindings((current) => ({ ...current, [role]: event.target.value }))}><option value="">선택 안 함</option>{deployments.filter((item) => item.roles.includes(role)).map((item) => <option value={item.deployment_id} key={item.deployment_id}>{item.provider_code} · {item.model_id}</option>)}</select></label>)}</div><button className="primary-button" type="button" onClick={saveBindings}>역할 매핑 저장</button></details>
    </Root>
  );
}
