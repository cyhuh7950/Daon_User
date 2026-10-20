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

export function egressPolicyMode(policy) {
  return policy?.mode === "allow_approved_external" && policy.question_step_up_required === false
    ? "allow_external" : policy?.mode;
}

export function egressPolicyModeLabel(policy) {
  return ({ deny_external: "외부 전송 차단", allow_approved_external: "추가 인증 후 외부 전송 허용",
    allow_external: "추가 인증 없이 외부 전송 허용" })[egressPolicyMode(policy)];
}

export function selectEgressPolicyMode(draft, mode) {
  if (!["deny_external", "allow_approved_external", "allow_external"].includes(mode)) throw new Error("EGRESS_POLICY_MODE_INVALID");
  if (mode === "deny_external") return createEgressPolicyDraft({ ...draft, mode,
    allowed_provider_kinds: [], allowed_destinations: [], max_bytes: 0, question_step_up_required: true });
  return createEgressPolicyDraft({ ...draft, mode: mode === "allow_external" ? "allow_approved_external" : mode,
    question_step_up_required: mode !== "allow_external" });
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
