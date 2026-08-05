"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { providerSettingsApi } from "../lib/provider-settings-api.js";

const PROVIDERS = Object.freeze([
  "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
  "OPENROUTER", "ANTHROPIC", "OLLAMA"
]);
const ROLES = Object.freeze([
  "text", "vision", "audio_understanding", "speech_to_text", "embedding", "reranker"
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
    profile_version: 0,
    deployment_id: `deployment-${providerCode.toLowerCase()}`,
    model_id: "",
    roles: ["text"],
    deployment_active: false,
    selected: false,
    deployment_version: 0
  };
}

export function ProviderSettingsWorkspace({ workspaceId = "workspace-release-one" }) {
  const [drafts, setDrafts] = useState(() => Object.fromEntries(PROVIDERS.map((code) => [code, initialDraft(code)])));
  const [bindings, setBindings] = useState({});
  const [bindingVersion, setBindingVersion] = useState(0);
  const [bindingEtag, setBindingEtag] = useState(`"model-policy:${workspaceId}:0"`);
  const [status, setStatus] = useState({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });

  const load = useCallback(async () => {
    setStatus({ kind: "loading", message: "Provider 설정을 불러오는 중입니다." });
    try {
      const [profilesResult, deploymentsResult, policyResult] = await Promise.all([
        providerSettingsApi.listProfiles(workspaceId),
        providerSettingsApi.listDeployments(workspaceId),
        providerSettingsApi.getModelPolicy(workspaceId)
      ]);
      const deployments = new Map(deploymentsResult.payload.data.map((item) => [item.provider_code, item]));
      setDrafts(Object.fromEntries(profilesResult.payload.data.map((profile) => {
        const deployment = deployments.get(profile.provider_code);
        return [profile.provider_code, {
          ...initialDraft(profile.provider_code),
          base_url: profile.base_url,
          active: profile.active,
          credential_configured: profile.credential_configured,
          profile_version: profile.version,
          deployment_id: deployment?.deployment_id ?? `deployment-${profile.provider_code.toLowerCase()}`,
          model_id: deployment?.model_id ?? "",
          roles: deployment?.roles ?? ["text"],
          deployment_active: deployment?.active ?? false,
          selected: deployment?.selected ?? false,
          deployment_version: deployment?.version ?? 0
        }];
      })));
      setBindings(policyResult.payload.data.bindings);
      setBindingVersion(policyResult.payload.data.version);
      setBindingEtag(policyResult.etag ?? `"model-policy:${workspaceId}:${policyResult.payload.data.version}"`);
      setStatus({ kind: "ready", message: "Provider 설정을 조회했습니다." });
    } catch (error) {
      setStatus({ kind: "error", message: `설정을 불러오지 못했습니다. ${error.code ?? "RESOURCE_UNAVAILABLE"}` });
    }
  }, [workspaceId]);

  useEffect(() => { load(); }, [load]);

  const deployments = useMemo(
    () => Object.values(drafts).filter((item) => item.model_id && item.deployment_active),
    [drafts]
  );

  function updateDraft(providerCode, field, value) {
    setDrafts((current) => ({
      ...current,
      [providerCode]: { ...current[providerCode], [field]: value }
    }));
  }

  function toggleRole(providerCode, role) {
    const current = drafts[providerCode];
    const roles = current.roles.includes(role)
      ? current.roles.filter((item) => item !== role)
      : [...current.roles, role];
    updateDraft(providerCode, "roles", roles.length ? roles : [role]);
  }

  async function saveProvider(providerCode) {
    const draft = drafts[providerCode];
    setStatus({ kind: "saving", message: `${providerCode} 설정을 저장하는 중입니다.` });
    try {
      const profileResult = await providerSettingsApi.saveProfile({
        workspace_id: workspaceId,
        provider_code: providerCode,
        base_url: draft.base_url,
        active: draft.active,
        expected_version: draft.profile_version
      }, operationKey(`provider-${providerCode}`));
      let deploymentVersion = draft.deployment_version;
      if (draft.model_id.trim()) {
        const deploymentResult = await providerSettingsApi.saveDeployment({
          workspace_id: workspaceId,
          deployment_id: draft.deployment_id,
          provider_code: providerCode,
          model_id: draft.model_id,
          roles: draft.roles,
          active: draft.deployment_active,
          selected: draft.selected,
          expected_version: draft.deployment_version
        }, operationKey(`deployment-${providerCode}`));
        deploymentVersion = deploymentResult.payload.data.version;
      }
      setDrafts((current) => ({
        ...current,
        [providerCode]: {
          ...current[providerCode],
          profile_version: profileResult.payload.data.version,
          credential_configured: profileResult.payload.data.credential_configured,
          deployment_version: deploymentVersion
        }
      }));
      setStatus({ kind: "ready", message: `${providerCode} 설정을 저장했습니다.` });
    } catch (error) {
      setStatus({ kind: "error", message: `저장하지 못했습니다. ${error.code ?? "RESOURCE_UNAVAILABLE"}` });
    }
  }

  async function saveBindings() {
    setStatus({ kind: "saving", message: "역할 매핑을 저장하는 중입니다." });
    try {
      const result = await providerSettingsApi.saveModelPolicy(
        workspaceId,
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
        <div><h1>모델·Provider 설정</h1><p>Workspace {workspaceId}</p></div>
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
                <label>모델 ID<input value={draft.model_id} onChange={(event) => updateDraft(providerCode, "model_id", event.target.value)} /></label>
                <fieldset><legend>역할</legend>{ROLES.map((role) => <label key={role}><input type="checkbox" checked={draft.roles.includes(role)} onChange={() => toggleRole(providerCode, role)} />{role}</label>)}</fieldset>
                <div className="provider-switches">
                  <label><input type="checkbox" checked={draft.active} onChange={(event) => updateDraft(providerCode, "active", event.target.checked)} />Provider 활성</label>
                  <label><input type="checkbox" checked={draft.deployment_active} onChange={(event) => updateDraft(providerCode, "deployment_active", event.target.checked)} />모델 활성</label>
                  <label><input type="checkbox" checked={draft.selected} onChange={(event) => updateDraft(providerCode, "selected", event.target.checked)} />선택</label>
                </div>
                <button type="button" onClick={() => saveProvider(providerCode)}>저장</button>
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
