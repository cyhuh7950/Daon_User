import { useEffect, useRef, useState } from "react";
import { canConfirmOfflineStudioSettings } from "./offline-studio-model.js";


const MODE_LABELS = Object.freeze({
  daon_priority: "Daon 지식 우선",
  mixed: "혼합",
  raw_only: "Raw Source만",
});

function safeCode(error, fallback) {
  return /^[A-Z][A-Z0-9_]{2,63}$/u.test(error?.message ?? "") ? error.message : fallback;
}

export function OfflineStudioPane({
  surface, state, dispatch, studioAdapter, syncAdapter, workspaceId,
}) {
  const revision = useRef(state.requestRevision);
  revision.current = Math.max(revision.current, state.requestRevision);
  const operationSequence = useRef(0);
  const stepUpPasswordRef = useRef(null);
  const [editorSections, setEditorSections] = useState([]);

  useEffect(() => {
    const selected = state.versions.find(
      (version) => version.output_version_id === state.selectedVersionId,
    ) ?? state.draft;
    setEditorSections((selected?.sections ?? []).map((section) => ({
      title: section.title,
      body: section.body,
      unverified: true,
    })));
  }, [state.draft, state.selectedVersionId, state.versions]);

  useEffect(() => {
    if (surface !== "studio" || !studioAdapter) return undefined;
    const current = revision.current + 1;
    revision.current = current;
    dispatch({ type: "request_started", revision: current });
    void studioAdapter.listModels(workspaceId).then(
      (models) => dispatch({ type: "models_ready", revision: current, models }),
      (error) => dispatch({ type: "request_failed", revision: current, safeError: safeCode(error, "LOCAL_MODEL_LIST_FAILED") }),
    );
    return undefined;
  }, [dispatch, studioAdapter, surface]);

  useEffect(() => {
    if (surface !== "studio" || !studioAdapter) return undefined;
    let active = true;
    void studioAdapter.listRawSources(workspaceId).then(
      (rawSources) => {
        if (active) dispatch({ type: "raw_sources_ready", rawSources });
      },
      (error) => dispatch({ type: "request_failed", safeError: safeCode(error, "RAW_SOURCE_LIST_FAILED") }),
    );
    return () => { active = false; };
  }, [dispatch, studioAdapter, surface, workspaceId]);

  useEffect(() => {
    if (surface !== "studio" || !syncAdapter) return undefined;
    let active = true;
    void syncAdapter.listKnowledge({ workspace_id: workspaceId }).then(
      (packages) => {
        if (!active) return;
        const daonItems = packages.map((item) => ({
          origin: "daon_knowledge",
          item_id: item.package_id,
          version_id: item.output_version_id,
          producer: item.producer,
          authority: item.authority,
          quality: item.review_state ?? "approved",
          digest: item.digest_sha256,
        }));
        dispatch({
          type: "context_changed",
          context: {
            ...state.context,
            items: [
              ...daonItems,
              ...state.context.items.filter((item) => item.origin === "raw_source"),
            ],
          },
        });
      },
      (error) => dispatch({ type: "request_failed", safeError: safeCode(error, "KNOWLEDGE_LIST_FAILED") }),
    );
    return () => { active = false; };
  }, [dispatch, state.context.mode, surface, syncAdapter, workspaceId]);

  const nextKey = (kind) => {
    operationSequence.current += 1;
    return `${kind}-${Date.now()}-${operationSequence.current}`;
  };
  const importRawSource = async (event) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file || !studioAdapter || state.status === "loading") return;
    const extension = file.name.toLowerCase().split(".").pop();
    const contentType = file.type || (
      extension === "pdf" ? "application/pdf"
        : extension === "md" || extension === "markdown" ? "text/markdown"
          : extension === "txt" ? "text/plain" : ""
    );
    const current = revision.current + 1;
    revision.current = current;
    dispatch({ type: "request_started", revision: current });
    try {
      const created = await studioAdapter.importRawSource({
        workspace_id: workspaceId,
        filename: file.name,
        content_type: contentType,
        bytes: new Uint8Array(await file.arrayBuffer()),
        idempotency_key: nextKey("raw-source"),
      });
      const rawSources = await studioAdapter.listRawSources(workspaceId);
      dispatch({ type: "raw_sources_ready", rawSources });
      dispatch({
        type: "raw_source_selected",
        sourceVersionId: created.source_version_id,
        selected: true,
      });
    } catch (error) {
      dispatch({ type: "request_failed", revision: current, safeError: safeCode(error, "RAW_SOURCE_IMPORT_FAILED") });
    } finally {
      input.value = "";
    }
  };
  const generate = async () => {
    if (!state.confirmed?.request_id || state.status === "loading") return;
    const current = revision.current + 1;
    revision.current = current;
    dispatch({ type: "request_started", revision: current });
    try {
      const draft = await studioAdapter.generateDraft({
        workspace_id: workspaceId,
        request_id: state.confirmed.request_id,
        idempotency_key: nextKey("generate"),
      });
      dispatch({ type: "draft_generated", revision: current, draft });
    } catch (error) {
      dispatch({ type: "request_failed", revision: current, safeError: safeCode(error, "OFFLINE_GENERATION_FAILED") });
    }
  };
  const appendEdit = async () => {
    if (!state.draft?.draft_id || !state.selectedVersionId || state.status === "loading" || editorSections.length === 0) return;
    const current = revision.current + 1;
    revision.current = current;
    dispatch({ type: "request_started", revision: current });
    try {
      const version = await studioAdapter.appendEdit({
        workspace_id: workspaceId,
        draft_id: state.draft.draft_id,
        previous_version_id: state.selectedVersionId,
        sections: editorSections.map((section) => ({ ...section, unverified: true })),
        idempotency_key: nextKey("edit"),
      });
      dispatch({ type: "version_appended", revision: current, version });
    } catch (error) {
      dispatch({ type: "request_failed", revision: current, safeError: safeCode(error, "OFFLINE_EDIT_FAILED") });
    }
  };
  const queueSync = async () => {
    if (!state.draft?.draft_id || !state.selectedVersionId || state.status === "loading") return;
    const current = revision.current + 1;
    revision.current = current;
    dispatch({ type: "request_started", revision: current });
    try {
      const queued = await studioAdapter.queueSync({
        workspace_id: workspaceId,
        draft_id: state.draft.draft_id,
        output_version_id: state.selectedVersionId,
        source_dependency_ids: state.context.items.map((item) => item.version_id),
        idempotency_key: nextKey("queue"),
      });
      dispatch({
        type: "sync_changed", revision: current,
        sync: {
          state: queued.approval_state ?? queued.state,
          operationId: queued.operation_id,
          conflictId: queued.conflict_id ?? null,
          approvedItemIds: queued.approved_item_ids ?? [],
        },
      });
    } catch (error) {
      dispatch({ type: "request_failed", revision: current, safeError: safeCode(error, "OFFLINE_SYNC_QUEUE_FAILED") });
    }
  };

  if (surface === "editor") {
    return (
      <div data-offline-editor="true">
        <div className="offline-editor-toolbar" role="toolbar" aria-label="Offline Editor View">
          <strong>업무 문서 초안</strong>
          <button type="button" onClick={() => dispatch({ type: "view_selected", view: "versions" })}>Version</button>
          <button type="button" onClick={() => dispatch({ type: "view_selected", view: "review" })}>검토</button>
        </div>
        {state.draft ? (
          <form aria-label="초안 편집" onSubmit={(event) => { event.preventDefault(); void appendEdit(); }}>
            <h3>{state.draft.title ?? "Offline Draft"}</h3>
            {editorSections.map((section, index) => (
              <fieldset key={`${state.selectedVersionId}:${index}`}>
                <legend>섹션 {index + 1}</legend>
                <label>제목<input name={`section-title-${index}`} value={section.title} onChange={(event) => setEditorSections((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, title: event.currentTarget.value } : item))} /></label>
                <label>본문<textarea name={`section-body-${index}`} value={section.body} onChange={(event) => setEditorSections((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, body: event.currentTarget.value } : item))} /></label>
              </fieldset>
            ))}
            <p role="status">편집 내용은 검토 전 상태로 새 Version에 저장됩니다.</p>
          </form>
        ) : <p>설정 확인 후 선택한 Local LLM으로 초안을 생성합니다.</p>}
        {!state.draft ? <button type="button" onClick={generate} disabled={!state.settingsConfirmed || state.status === "loading"}>초안 생성</button> : null}
        {state.draft ? <button type="button" onClick={appendEdit} disabled={state.status === "loading" || editorSections.length === 0}>새 Version 저장</button> : null}
        {state.draft ? <button type="button" onClick={queueSync} disabled={state.status === "loading"}>Sync 대기열</button> : null}
        {state.context.items.map((item) => (
          <span className={`origin-badge origin-${item.origin}`} key={`${item.origin}:${item.version_id}`}>
            {item.origin === "daon_knowledge" ? "Daon Knowledge" : "Raw Source"}
          </span>
        ))}
      </div>
    );
  }

  const setMode = (mode) => dispatch({
    type: "context_changed",
    context: { ...state.context, mode },
  });
  const confirm = async (event) => {
    event.preventDefault();
    if (!canConfirmOfflineStudioSettings(state)) return;
    const current = revision.current + 1;
    revision.current = current;
    dispatch({ type: "request_started", revision: current });
    try {
      const prepared = await studioAdapter.prepareContext({
        workspace_id: workspaceId,
        mode: state.context.mode,
        daon_knowledge_ids: state.context.items.filter((item) => item.origin === "daon_knowledge").map((item) => item.item_id),
        raw_source_version_ids: state.context.items.filter((item) => item.origin === "raw_source").map((item) => item.version_id),
        idempotency_key: nextKey("context"),
      });
      const context = {
        mode: prepared.mode,
        snapshotId: prepared.snapshot_id,
        items: prepared.items,
        warnings: prepared.warnings,
      };
      dispatch({ type: "context_ready", revision: current, context });
      const confirmed = await studioAdapter.confirmSettings({
        workspace_id: workspaceId,
        title: "업무 문서 초안",
        purpose: "선택한 근거로 검토 가능한 초안을 작성",
        temperature: 0.1,
        max_output_tokens: 2048,
        context_snapshot_id: prepared.snapshot_id,
        model_deployment_id: state.selectedModelDeploymentId,
        idempotency_key: nextKey("settings"),
      });
      dispatch({ type: "settings_confirmed", revision: current, confirmed });
    } catch (error) {
      dispatch({ type: "request_failed", revision: current, safeError: safeCode(error, "OFFLINE_SETTINGS_FAILED") });
    }
  };
  const approve = async (event) => {
    event.preventDefault();
    const password = stepUpPasswordRef.current?.value ?? "";
    try {
      await syncAdapter.approveSync({
        operation_id: state.sync.operationId,
        password,
        approved_item_ids: state.sync.approvedItemIds ?? [],
      });
    } catch (error) {
      dispatch({ type: "request_failed", safeError: safeCode(error, "OFFLINE_SYNC_APPROVAL_FAILED") });
    } finally {
      if (stepUpPasswordRef.current) stepUpPasswordRef.current.value = "";
    }
  };

  return (
    <div className="offline-studio-views" data-offline-studio-view={state.view}>
      <nav aria-label="Offline Studio View">
        <button type="button" onClick={() => dispatch({ type: "view_selected", view: "settings" })}>설정</button>
        <button type="button" onClick={() => dispatch({ type: "view_selected", view: "editor" })}>업무 문서 초안</button>
        <button type="button" onClick={() => dispatch({ type: "view_selected", view: "versions" })}>Version</button>
        <button type="button" onClick={() => dispatch({ type: "view_selected", view: "sync" })}>Sync</button>
      </nav>
      {state.view === "settings" ? (
        <form onSubmit={confirm} aria-label="Offline Studio 설정">
          <fieldset>
            <legend>입력 모드 <button type="button" title="Daon 지식과 Raw Source 사용 우선순위를 고정합니다." aria-label="입력 모드 설명">i</button></legend>
            {Object.entries(MODE_LABELS).map(([mode, label]) => (
              <label key={mode}>
                <input type="radio" name="knowledge-mode" checked={state.context.mode === mode} onChange={() => setMode(mode)} />
                {label}
              </label>
            ))}
          </fieldset>
          {state.context.mode === "raw_only" ? <p role="status">⚠ Raw Source only</p> : null}
          <fieldset>
            <legend>로컬 Raw Source <button type="button" title="선택한 파일은 Local Service에 암호화 저장되며 Cloud로 자동 전송되지 않습니다." aria-label="로컬 Raw Source 설명">i</button></legend>
            <label>PDF·텍스트 가져오기
              <input
                type="file"
                accept="application/pdf,text/plain,text/markdown,.pdf,.txt,.md,.markdown"
                disabled={state.status === "loading"}
                onChange={(event) => { void importRawSource(event); }}
              />
            </label>
            {state.rawSources.length === 0 ? <p>가져온 로컬 원문이 없습니다.</p> : (
              <ul>
                {state.rawSources.map((source) => (
                  <li key={source.source_version_id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={state.selectedRawSourceVersionIds.includes(source.source_version_id)}
                        onChange={(event) => dispatch({
                          type: "raw_source_selected",
                          sourceVersionId: source.source_version_id,
                          selected: event.currentTarget.checked,
                        })}
                      />
                      {source.filename} · 검증 전
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>
          <label>Local LLM
            <select value={state.selectedModelDeploymentId ?? ""} onChange={(event) => dispatch({ type: "model_selected", deploymentId: event.currentTarget.value })}>
              <option value="">선택</option>
              {state.models.map((model) => (
                <option key={model.deployment_id} value={model.deployment_id} disabled={model.provider_code !== "OLLAMA" || model.provider_kind !== "server_internal" || model.readiness !== "ready"}>
                  {model.label ?? model.deployment_id}{model.provider_code !== "OLLAMA" || model.provider_kind !== "server_internal" ? " · Offline unavailable" : ""}
                </option>
              ))}
            </select>
          </label>
          <button className="panel-primary-action" type="submit" disabled={!canConfirmOfflineStudioSettings(state) || state.status === "loading"}>설정 확인</button>
        </form>
      ) : null}
      {state.view === "versions" ? <><ul>{state.versions.map((version) => <li key={version.output_version_id}>{version.output_version_id}</li>)}</ul><button type="button" onClick={appendEdit} disabled={!state.draft || state.status === "loading"}>새 Version 저장</button></> : null}
      {state.view === "review" ? <p><span aria-hidden="true">◇</span> unverified · 검토 필요</p> : null}
      {state.view === "sync" ? (
        <form onSubmit={approve} aria-label="Sync 승인">
          <p role="status">{state.sync.state}</p>
          <button type="button" onClick={queueSync} disabled={!state.draft || state.status === "loading"}>Sync 대기열 등록</button>
          <label>추가 인증<input ref={stepUpPasswordRef} type="password" autoComplete="current-password" defaultValue="" /></label>
          <button className="panel-primary-action" type="submit" disabled={!state.sync.operationId}>승인 후 재개</button>
        </form>
      ) : null}
      {state.safeError ? <p role="alert">{state.safeError}</p> : null}
    </div>
  );
}
