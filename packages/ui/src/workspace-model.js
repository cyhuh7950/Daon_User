import { createSourceKnowledgeViewState, transitionSourceKnowledgeState } from "./source-knowledge-model.js";
import { createRunViewState, transitionRunViewState } from "./run-model-evidence-model.js";

export const PANE_IDS = Object.freeze(["knowledge", "conversation", "studio"]);
export const LAYOUT_MODES = Object.freeze(["three-pane", "two-pane", "single-pane", "bottom-tabs"]);

const DEFAULT_STATE = Object.freeze({
  workspace_id: "workspace-release-one",
  active_pane: "knowledge",
  secondary_pane: "conversation",
  open_drawer: null,
  return_drawer: null,
  selected_source_id: "source-daon-guidance",
  conversation_id: "conversation-release-planning",
  run_id: "run-prototype-unavailable",
  run_status: "unavailable",
  artifact_id: "artifact-evidence-report-draft",
  artifact_cursor: "section-2:paragraph-3",
  evidence_id: "evidence-source-page-12",
  evidence_position: "page-12:paragraph-4",
  evidence_source_id: "source-daon-guidance",
  evidence_source_version_id: "source-daon-guidance-v2",
  evidence_name: "승인 운영 지침.pdf",
  evidence_excerpt: "승인된 기준선과 검증 증거를 함께 보존합니다.",
  evidence_kind: "document-region",
  source_knowledge: createSourceKnowledgeViewState(),
  run_model: createRunViewState(),
  pane_sizes: Object.freeze({ knowledge: 30, conversation: 38, studio: 32 }),
  last_transition: "prototype-seed"
});

export function getLayoutMode(width) {
  if (!Number.isFinite(width) || width < 0) throw new RangeError("width must be a non-negative finite number");
  if (width <= 599) return "bottom-tabs";
  if (width <= 1023) return "single-pane";
  if (width <= 1439) return "two-pane";
  return "three-pane";
}

export function createWorkspaceViewState(overrides = {}) {
  return {
    ...DEFAULT_STATE,
    ...overrides,
    pane_sizes: { ...DEFAULT_STATE.pane_sizes, ...(overrides.pane_sizes ?? {}) },
    source_knowledge: createSourceKnowledgeViewState(overrides.source_knowledge ?? DEFAULT_STATE.source_knowledge),
    run_model: createRunViewState(overrides.run_model ?? DEFAULT_STATE.run_model)
  };
}

function twoPanePair(state) {
  if (state.active_pane === "studio") return ["conversation", "studio"];
  if (state.active_pane === "knowledge") return ["knowledge", "conversation"];
  if (state.secondary_pane === "studio") return ["conversation", "studio"];
  return ["knowledge", "conversation"];
}

export function projectWorkspace(state, width) {
  const layoutMode = getLayoutMode(width);
  const visiblePanes = layoutMode === "three-pane"
    ? [...PANE_IDS]
    : layoutMode === "two-pane"
      ? twoPanePair(state)
      : [state.active_pane];
  const hiddenPanes = PANE_IDS.filter((pane) => !visiblePanes.includes(pane));
  return {
    state,
    layoutMode,
    visiblePanes,
    hiddenPanes,
    drawerPane: state.open_drawer && PANE_IDS.includes(state.open_drawer)
      ? state.open_drawer
      : layoutMode === "two-pane" ? hiddenPanes[0] : null,
    evidencePresentation: state.open_drawer === "evidence"
      ? layoutMode === "bottom-tabs" ? "fullscreen" : "drawer"
      : null
  };
}

export function resizePaneSizes(sizes, pane, delta) {
  const index = PANE_IDS.indexOf(pane);
  const neighbor = PANE_IDS[index + 1];
  if (index < 0 || !neighbor) return { ...sizes };
  const current = Number(sizes[pane]);
  const adjacent = Number(sizes[neighbor]);
  const pairTotal = current + adjacent;
  const minimum = Math.max(20, pairTotal - 55);
  const maximum = Math.min(55, pairTotal - 20);
  const next = Math.max(minimum, Math.min(maximum, current + delta));
  return { ...sizes, [pane]: next, [neighbor]: pairTotal - next };
}

export function transitionWorkspace(state, action, transition = new Date().toISOString()) {
  const next = { ...state, pane_sizes: { ...state.pane_sizes }, source_knowledge: createSourceKnowledgeViewState(state.source_knowledge), run_model: createRunViewState(state.run_model), last_transition: transition };
  switch (action.type) {
    case "activate-pane":
      if (!PANE_IDS.includes(action.pane)) return state;
      next.active_pane = action.pane;
      next.secondary_pane = action.pane === "studio" ? "conversation" : action.pane === "knowledge" ? "conversation" : next.secondary_pane;
      next.open_drawer = null;
      return next;
    case "open-drawer":
      if (!PANE_IDS.includes(action.pane)) return state;
      next.open_drawer = action.pane;
      next.return_drawer = null;
      return next;
    case "close-overlay":
      next.open_drawer = next.open_drawer === "evidence" ? next.return_drawer : null;
      next.return_drawer = null;
      return next;
    case "open-evidence":
      next.return_drawer = PANE_IDS.includes(next.open_drawer) ? next.open_drawer : null;
      next.open_drawer = "evidence";
      if (action.evidence) {
        next.evidence_id = action.evidence.evidenceId ?? action.evidence.id;
        next.evidence_position = action.evidence.position;
        next.evidence_source_id = action.evidence.sourceId;
        next.evidence_source_version_id = action.evidence.sourceVersionId;
        next.evidence_name = action.evidence.name;
        next.evidence_excerpt = action.evidence.excerpt;
        next.evidence_kind = action.evidence.kind;
      }
      return next;
    case "set-evidence-position":
      next.evidence_position = action.position;
      return next;
    case "select-source":
      if (typeof action.sourceId !== "string" || action.sourceId.length === 0) return state;
      next.selected_source_id = action.sourceId;
      return next;
    case "source-knowledge":
      next.source_knowledge = transitionSourceKnowledgeState(next.source_knowledge, action.domainAction);
      return next;
    case "run-model":
      next.run_model = transitionRunViewState(next.run_model, action.domainAction);
      next.run_id = next.run_model.run.id;
      next.run_status = next.run_model.run.status;
      return next;
    case "set-artifact-cursor":
      next.artifact_cursor = action.cursor;
      return next;
    case "resize-pane":
      next.pane_sizes = resizePaneSizes(next.pane_sizes, action.pane, Number(action.delta) || 0);
      return next;
    default:
      return state;
  }
}
