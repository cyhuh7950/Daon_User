const DENY = Object.freeze({
  mode: "deny_external",
  allowed_provider_kinds: [],
  allowed_destinations: [],
  classification: "restricted",
  max_bytes: 0,
  masking_required: true,
  redaction_required: true,
  required_approver: "organization_admin",
});

export function createEgressPolicyDraft(overrides = {}) {
  return { ...DENY, ...overrides };
}

const INITIAL = Object.freeze({
  status: "idle", effective: null, draft: createEgressPolicyDraft(),
  canSave: false, errorCode: null,
});

function canSaveDraft(effective, draft) {
  const parentDenied = effective?.parent_locked === true
    && effective?.editable_scope !== "organization";
  const triesToRelax = draft.mode === "allow_approved_external";
  return !parentDenied || !triesToRelax;
}

export function egressPolicyReducer(state = INITIAL, action) {
  if (action.type === "context_loading") return {
    ...INITIAL, status: "loading", draft: createEgressPolicyDraft(),
  };
  if (action.type === "loading") return { ...state, status: "loading", errorCode: null };
  if (action.type === "loaded") {
    const scopePolicy = action.data.editable_scope === "organization"
      ? action.data.organization_policy
      : action.data.workspace_policy;
    const draft = createEgressPolicyDraft(scopePolicy || action.data);
    return {
      status: "ready", effective: action.data,
      draft, canSave: canSaveDraft(action.data, draft), errorCode: null,
    };
  }
  if (action.type === "drafted") {
    return {
      ...state, draft: action.draft,
      canSave: canSaveDraft(state.effective, action.draft), errorCode: null,
    };
  }
  if (action.type === "saving") return { ...state, status: "saving", errorCode: null };
  if (action.type === "failed") return { ...state, status: "error", errorCode: action.code };
  return state;
}
