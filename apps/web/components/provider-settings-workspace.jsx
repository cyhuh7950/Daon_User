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

export function ProviderSettingsWorkspace({ workspaceId }) {
  const [resolvedWorkspaceId, setResolvedWorkspaceId] = useState(workspaceId ?? null);
  const [drafts, setDrafts] = useState(() => Object.fromEntries(PROVIDERS.map((code) => [code, initialDraft(code)])));
  const [deploymentDrafts, setDeploymentDrafts] = useState([]);
  const [bindings, setBindings] = useState({});
  const [bindingVersion, setBindingVersion] = useState(0);
  const [bindingEtag, setBindingEtag] = useState(null);
  const [status, setStatus] = useState({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });

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
      setDrafts(Object.fromEntries(profilesResult.payload.data.map((profile) => {
        return [profile.provider_code, {
          ...initialDraft(profile.provider_code),
          base_url: profile.base_url,
          active: profile.active,
          credential_configured: profile.credential_configured,
          profile_version: profile.version
        }];
      })));
      setDeploymentDrafts(deploymentsResult.payload.data.map((item) => ({ ...item })));
      setBindings(policyResult.payload.data.bindings);
      setBindingVersion(policyResult.payload.data.version);
      setBindingEtag(policyResult.etag ?? `"model-policy:${activeWorkspaceId}:${policyResult.payload.data.version}"`);
      setStatus({ kind: "ready", message: "Provider 설정을 조회했습니다." });
    } catch (error) {
      setStatus({ kind: "error", message: `설정을 불러오지 못했습니다. ${error.code ?? "RESOURCE_UNAVAILABLE"}` });
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
    setStatus({ kind: "saving", message: `${providerCode} 설정을 저장하는 중입니다.` });
    try {
      const activeWorkspaceId = workspaceIdForWrite();
      const profileResult = await providerSettingsApi.saveProfile({
        workspace_id: activeWorkspaceId,
        provider_code: providerCode,
        base_url: draft.base_url,
        active: draft.active,
        expected_version: draft.profile_version
      }, operationKey(`provider-${providerCode}`));
      setDrafts((current) => ({
        ...current,
        [providerCode]: {
          ...current[providerCode],
          profile_version: profileResult.payload.data.version,
          credential_configured: profileResult.payload.data.credential_configured
        }
      }));
      setStatus({ kind: "ready", message: `${providerCode} 설정을 저장했습니다.` });
    } catch (error) {
      setStatus({ kind: "error", message: `저장하지 못했습니다. ${error.code ?? "RESOURCE_UNAVAILABLE"}` });
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
      setStatus({ kind: "error", message: `모델을 저장하지 못했습니다. ${error.code ?? "RESOURCE_UNAVAILABLE"}` });
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
      setStatus({ kind: "error", message: `역할 매핑을 저장하지 못했습니다. ${error.code ?? "RESOURCE_UNAVAILABLE"}` });
    }
  }

  return (
    <main className="provider-settings-shell">
      <header>
        <div><h1>모델·Provider 설정</h1><p>Workspace {resolvedWorkspaceId ?? "확인 중"}</p></div>
        <button type="button" onClick={load}>새로고침</button>
      </header>
      <div className={`provider-status ${status.kind}`} role="status">{status.message}</div>
      <section aria-labelledby="provider-list-title">
        <h2 id="provider-list-title">Provider Profile · ModelDeployment</h2>
        <div className="provider-grid">
          {PROVIDERS.map((providerCode) => {
            const draft = drafts[providerCode];
            return (
              <article className="provider-card" key={providerCode}>
                <div className="provider-card-title">
                  <h3>{providerCode}</h3>
                  <span>{draft.credential_configured ? "Credential 설정됨" : "Credential 미설정"}</span>
                </div>
                <label>Base URL<input value={draft.base_url} onChange={(event) => updateDraft(providerCode, "base_url", event.target.value)} /></label>
                <div className="provider-switches">
                  <label><input type="checkbox" checked={draft.active} onChange={(event) => updateDraft(providerCode, "active", event.target.checked)} />Provider 활성</label>
                </div>
                <button type="button" onClick={() => saveProvider(providerCode)}>Provider 저장</button>
                <div className="deployment-list">
                  {deploymentDrafts.filter((item) => item.provider_code === providerCode).map((deployment) => (
                    <section className="deployment-card" key={deployment.deployment_id}>
                      <label>Deployment ID<input value={deployment.deployment_id} disabled={deployment.version > 0} onChange={(event) => updateDeployment(deployment.deployment_id, "deployment_id", event.target.value)} /></label>
                      <label>모델 ID<input value={deployment.model_id} onChange={(event) => updateDeployment(deployment.deployment_id, "model_id", event.target.value)} /></label>
                      <fieldset><legend>역할</legend>{ROLES.map((role) => <label key={role}><input type="checkbox" checked={deployment.roles.includes(role)} onChange={() => toggleRole(deployment.deployment_id, role)} />{role}</label>)}</fieldset>
                      <div className="provider-switches">
                        <label><input type="checkbox" checked={deployment.active} onChange={(event) => updateDeployment(deployment.deployment_id, "active", event.target.checked)} />모델 활성</label>
                        <label><input type="checkbox" checked={deployment.selected} onChange={(event) => updateDeployment(deployment.deployment_id, "selected", event.target.checked)} />선택</label>
                      </div>
                      <button type="button" onClick={() => saveDeployment(deployment.deployment_id)}>모델 저장</button>
                    </section>
                  ))}
                  <button type="button" onClick={() => addDeployment(providerCode)}>모델 추가</button>
                </div>
              </article>
            );
          })}
        </div>
      </section>
      <section className="role-bindings" aria-labelledby="role-binding-title">
        <h2 id="role-binding-title">역할 매핑</h2>
        <div className="role-grid">
          {ROLES.map((role) => <label key={role}>{role}<select value={bindings[role] ?? ""} onChange={(event) => setBindings((current) => ({ ...current, [role]: event.target.value }))}><option value="">선택 안 함</option>{deployments.filter((item) => item.roles.includes(role)).map((item) => <option value={item.deployment_id} key={item.deployment_id}>{item.provider_code} · {item.model_id}</option>)}</select></label>)}
        </div>
        <button type="button" onClick={saveBindings}>역할 매핑 저장</button>
      </section>
    </main>
  );
}
