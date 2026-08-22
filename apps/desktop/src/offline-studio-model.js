const VIEWS = new Set(["settings", "editor", "versions", "review", "sync"]);
const MODES = new Set(["daon_priority", "mixed", "raw_only"]);
const SAFE_ERROR = /^[A-Z][A-Z0-9_]{2,63}$/u;

function frozen(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) frozen(child);
  return Object.freeze(value);
}

function normalizeContext(context) {
  if (!MODES.has(context?.mode) || !Array.isArray(context?.items)) {
    throw new Error("OFFLINE_STUDIO_CONTEXT_INVALID");
  }
  const items = context.items.map((item) => ({ ...item }));
  const origins = new Set(items.map((item) => item.origin));
  if ([...origins].some((origin) => !["daon_knowledge", "raw_source"].includes(origin))) {
    throw new Error("OFFLINE_STUDIO_CONTEXT_INVALID");
  }
  const warnings = context.mode === "raw_only"
    ? ["RAW_SOURCE_ONLY"]
    : [...(context.warnings ?? [])];
  return {
    mode: context.mode,
    snapshotId: context.snapshotId ?? null,
    items,
    warnings,
  };
}

function rawContextItem(source) {
  return {
    origin: "raw_source",
    item_id: source.source_version_id,
    version_id: source.source_version_id,
    authority: "user_source",
    quality_state: source.quality_state,
    digest: source.digest_sha256,
  };
}

export function createOfflineStudioState(overrides = {}) {
  const state = {
    view: "settings",
    status: "idle",
    settingsConfirmed: false,
    confirmed: null,
    context: { mode: "daon_priority", snapshotId: null, items: [], warnings: [] },
    models: [],
    selectedModelDeploymentId: null,
    draft: null,
    versions: [],
    selectedVersionId: null,
    sources: [],
    rawSources: [],
    selectedRawSourceVersionIds: [],
    sync: { state: "draft", operationId: null, conflictId: null },
    safeError: null,
    requestRevision: 0,
    ...overrides,
  };
  if (!VIEWS.has(state.view)) throw new Error("OFFLINE_STUDIO_VIEW_INVALID");
  state.context = normalizeContext(state.context);
  return frozen(state);
}

export function canConfirmOfflineStudioSettings(state) {
  const selected = state?.models?.find(
    (model) => model.deployment_id === state.selectedModelDeploymentId,
  );
  if (
    !selected
    || selected.provider_code !== "OLLAMA"
    || selected.provider_kind !== "server_internal"
    || selected.readiness !== "ready"
  ) {
    return false;
  }
  const origins = new Set(state.context.items.map((item) => item.origin));
  if (state.context.mode === "daon_priority") return origins.has("daon_knowledge");
  if (state.context.mode === "mixed") {
    return origins.has("daon_knowledge") && origins.has("raw_source");
  }
  return state.context.mode === "raw_only" && origins.has("raw_source");
}

export function reduceOfflineStudioState(state, action) {
  if (!state || !action || typeof action.type !== "string") {
    throw new Error("OFFLINE_STUDIO_ACTION_INVALID");
  }
  if (
    Number.isSafeInteger(action.revision)
    && action.type !== "request_started"
    && action.revision !== state.requestRevision
  ) return state;
  let next;
  switch (action.type) {
    case "request_started":
      if (!Number.isSafeInteger(action.revision) || action.revision <= state.requestRevision) return state;
      next = { ...state, status: "loading", safeError: null, requestRevision: action.revision };
      break;
    case "models_ready":
      next = { ...state, models: [...action.models], status: "idle", safeError: null };
      break;
    case "raw_sources_ready": {
      const rawSources = Array.isArray(action.rawSources) ? action.rawSources.map((item) => ({ ...item })) : [];
      const available = new Set(rawSources.map((item) => item.source_version_id));
      next = {
        ...state,
        rawSources,
        selectedRawSourceVersionIds: state.selectedRawSourceVersionIds.filter((id) => available.has(id)),
        context: normalizeContext({
          ...state.context,
          items: [
            ...state.context.items.filter((item) => item.origin !== "raw_source"),
            ...rawSources
              .filter((item) => state.selectedRawSourceVersionIds.includes(item.source_version_id))
              .map(rawContextItem),
          ],
        }),
        status: "idle",
        safeError: null,
      };
      break;
    }
    case "raw_source_selected": {
      const id = action.sourceVersionId;
      if (!state.rawSources.some((item) => item.source_version_id === id)) return state;
      const selected = new Set(state.selectedRawSourceVersionIds);
      if (action.selected) selected.add(id); else selected.delete(id);
      next = {
        ...state,
        selectedRawSourceVersionIds: [...selected],
        context: normalizeContext({
          ...state.context,
          items: [
            ...state.context.items.filter((item) => item.origin !== "raw_source"),
            ...state.rawSources
              .filter((item) => selected.has(item.source_version_id))
              .map(rawContextItem),
          ],
        }),
        settingsConfirmed: false,
        confirmed: null,
      };
      break;
    }
    case "context_ready":
    case "context_changed":
      next = { ...state, context: normalizeContext(action.context), settingsConfirmed: false, confirmed: null, safeError: null };
      break;
    case "model_selected":
      next = { ...state, selectedModelDeploymentId: action.deploymentId, settingsConfirmed: false, confirmed: null };
      break;
    case "settings_confirmed":
      if (!canConfirmOfflineStudioSettings(state)) return state;
      next = { ...state, settingsConfirmed: true, confirmed: frozen({ ...action.confirmed }), view: "editor", status: "ready", safeError: null };
      break;
    case "view_selected":
      if (!VIEWS.has(action.view)) throw new Error("OFFLINE_STUDIO_VIEW_INVALID");
      next = { ...state, view: action.view };
      break;
    case "draft_generated":
      next = {
        ...state, draft: action.draft, versions: [...state.versions, action.draft],
        selectedVersionId: action.draft.output_version_id, view: "editor", status: "ready",
        safeError: null,
      };
      break;
    case "version_appended":
      next = {
        ...state, versions: [...state.versions, action.version],
        selectedVersionId: action.version.output_version_id, view: "versions", status: "ready",
        safeError: null,
      };
      break;
    case "sync_changed":
      next = { ...state, sync: { ...state.sync, ...action.sync }, view: "sync", status: "ready" };
      break;
    case "request_failed":
      next = {
        ...state, status: "error",
        safeError: SAFE_ERROR.test(action.safeError ?? "") ? action.safeError : "OFFLINE_STUDIO_FAILED",
      };
      break;
    default:
      throw new Error("OFFLINE_STUDIO_ACTION_INVALID");
  }
  return createOfflineStudioState(next);
}
