export const PANE_IDS = Object.freeze(["knowledge", "conversation", "studio"]);
export const LAYOUT_MODES = Object.freeze(["three-pane", "two-pane", "single-pane", "bottom-tabs"]);

const DEFAULT_STATE = Object.freeze({
  workspace_id: "workspace-release-one",
  active_pane: "knowledge",
  secondary_pane: "conversation",
  open_drawer: null,
  return_drawer: null,
  selected_source_id: "source-quarterly-guidance",
  conversation_id: "conversation-release-planning",
  run_id: "run-prototype-unavailable",
  run_status: "unavailable",
  artifact_id: "artifact-evidence-report-draft",
  artifact_cursor: "section-2:paragraph-3",
  evidence_id: "evidence-source-page-12",
  evidence_position: "page-12:paragraph-4",
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
    pane_sizes: { ...DEFAULT_STATE.pane_sizes, ...(overrides.pane_sizes ?? {}) }
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
  const next = { ...state, pane_sizes: { ...state.pane_sizes }, last_transition: transition };
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
      return next;
    case "set-evidence-position":
      next.evidence_position = action.position;
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
