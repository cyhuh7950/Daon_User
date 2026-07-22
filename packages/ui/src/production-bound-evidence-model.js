const CLIENTS = [
  { client_type: "web", label: "Web", proof: "실제 Next Production Build·Chrome Prototype", m3_owners: ["R1-M3-01"], native_runtime_executed: false, dom_ui_imported: true, ipc_or_local_service_verified: false },
  { client_type: "windows", label: "Windows", proof: "공용 React UI·Route·State·Adapter 계약 Projection", m3_owners: ["R1-M3-02", "R1-M3-03"], native_runtime_executed: false, dom_ui_imported: true, ipc_or_local_service_verified: false },
  { client_type: "android", label: "Android", proof: "Navigation·Screen·Mobile Allowlist 계약 Projection", m3_owners: ["R1-M3-04", "R1-M3-05"], native_runtime_executed: false, dom_ui_imported: false, ipc_or_local_service_verified: false },
  { client_type: "ios", label: "iOS", proof: "Navigation·Screen·Mobile Allowlist·Build 준비상태 Projection", m3_owners: ["R1-M3-04", "R1-M3-06"], native_runtime_executed: false, dom_ui_imported: false, ipc_or_local_service_verified: false }
];

const JOURNEYS = [
  {
    id: "workspace_context",
    number: 1,
    title: "Home → Workspace 문맥 보존",
    summary: "선택 Source·Pane·Evidence 위치를 보존하며 Workspace에 진입합니다.",
    routes: [{ route_id: "home", href: "/" }, { route_id: "workspace_detail", href: "/workspaces/workspace-release-one" }],
    screen_ids: ["home", "workspace_detail"],
    mock_adapter: "WorkspaceDetailAdapter",
    evidence: ["R1-M2-02/evidence-manifest.json"]
  },
  {
    id: "knowledge_authority",
    number: 2,
    title: "다섯 지식 원천·권위·가중치·충돌",
    summary: "Daon 승인 지식 우선, 0.5~2.0 가중치와 중요 충돌 차단을 확인합니다.",
    routes: [{ route_id: "workspace_detail", href: "/workspaces/workspace-release-one" }],
    screen_ids: ["workspace_detail"],
    mock_adapter: "SourceKnowledgeAdapter",
    evidence: ["R1-M2-03/evidence-manifest.json"]
  },
  {
    id: "model_lineage",
    number: 3,
    title: "모델 선택·Frozen Snapshot·근거 계보",
    summary: "Local LLM을 포함한 Mode·Fallback·비용·Citation 상태를 추적합니다.",
    routes: [{ route_id: "workspace_detail", href: "/workspaces/workspace-release-one" }, { route_id: "model_connections", href: "/settings/model-connections" }],
    screen_ids: ["workspace_detail", "model_connections"],
    mock_adapter: "RunModelEvidenceAdapter",
    evidence: ["R1-M2-04/evidence-manifest.json"]
  },
  {
    id: "studio_generation",
    number: 4,
    title: "Studio 생성 설정·제출·Version",
    summary: "Tile 선택 뒤 생성 설정을 확정하고 제출·Version 비교를 확인합니다.",
    routes: [{ route_id: "workspace_detail", href: "/workspaces/workspace-release-one" }],
    screen_ids: ["workspace_detail"],
    mock_adapter: "StudioWorkflowAdapter",
    evidence: ["R1-M2-05/evidence-manifest.json"]
  },
  {
    id: "review_delivery_registration",
    number: 5,
    title: "검토·승인·전달·생산 지식 등록",
    summary: "반려·재승인·Export Preview와 명시 등록 Gate를 연결합니다.",
    routes: [{ route_id: "workspace_detail", href: "/workspaces/workspace-release-one" }, { route_id: "inbox", href: "/inbox" }, { route_id: "notifications", href: "/notifications" }],
    screen_ids: ["workspace_detail", "inbox", "notifications"],
    mock_adapter: "StudioDeliveryAdapter",
    evidence: ["R1-M2-05/evidence-manifest.json"]
  },
  {
    id: "account_security",
    number: 6,
    title: "계정·조직·장치·현재 권한",
    summary: "Step-up, 정책 잠금, 장치 철회와 AccessDecision을 확인합니다.",
    routes: [{ route_id: "account_settings", href: "/settings/account" }, { route_id: "organization_settings", href: "/settings/organization" }],
    screen_ids: ["account_settings", "organization_settings"],
    mock_adapter: "AccountSecurityAdapter",
    evidence: ["R1-M2-06/evidence-manifest.json"]
  },
  {
    id: "operations_recovery",
    number: 7,
    title: "운영 경고·재처리·복구·알림",
    summary: "warning→restricted→waiting_model 새 Run→recovered 계보를 확인합니다.",
    routes: [{ route_id: "operations", href: "/operations" }, { route_id: "notifications", href: "/notifications" }],
    screen_ids: ["operations", "notifications"],
    mock_adapter: "OperationsStatusAdapter",
    evidence: ["R1-M2-07/evidence-manifest.json"]
  },
  {
    id: "negative_states",
    number: 8,
    title: "오류·권한·축소·unavailable",
    summary: "정상과 같은 흐름 안에서 실패를 성공으로 위장하지 않습니다.",
    routes: [{ route_id: "home", href: "/" }, { route_id: "workspace_detail", href: "/workspaces/workspace-release-one" }, { route_id: "account_settings", href: "/settings/account" }, { route_id: "operations", href: "/operations" }],
    screen_ids: ["home", "workspace_detail", "account_settings", "operations"],
    mock_adapter: "CompositeEvidenceAdapter",
    evidence: ["R1-M2-03/evidence-manifest.json", "R1-M2-04/evidence-manifest.json", "R1-M2-06/evidence-manifest.json", "R1-M2-07/evidence-manifest.json"]
  }
];

const NEGATIVE_STATE_LINKS = [
  { code: "loading", label: "불러오는 중", route_id: "home", href: "/?evidence_state=loading" },
  { code: "empty", label: "자료 없음", route_id: "home", href: "/?evidence_state=empty" },
  { code: "warning", label: "주의 필요", route_id: "operations", href: "/operations" },
  { code: "error", label: "안전 오류", route_id: "operations", href: "/operations" },
  { code: "forbidden", label: "현재 권한 거부", route_id: "account_settings", href: "/settings/account" },
  { code: "unavailable", label: "후속 실제 Adapter 미연결", route_id: "home", href: "/?evidence_state=unavailable" },
  { code: "IMPORTANT_KNOWLEDGE_CONFLICT", label: "중요 충돌", route_id: "workspace_detail", href: "/workspaces/workspace-release-one" },
  { code: "COST_LIMIT_EXCEEDED", label: "비용 차단", route_id: "workspace_detail", href: "/workspaces/workspace-release-one" },
  { code: "STEP_UP_REQUIRED", label: "추가 인증 필요", route_id: "account_settings", href: "/settings/account" },
  { code: "G9_DRILL_APPROVAL_REQUIRED", label: "복구 훈련 승인 필요", route_id: "operations", href: "/operations" },
  { code: "G9_DEPLOY_APPROVAL_REQUIRED", label: "배포 승인 필요", route_id: "operations", href: "/operations" },
  { code: "APPROVAL_DELIVERY_BLOCKED", label: "Evidence Store 장애", route_id: "operations", href: "/operations" }
];

const M3_OWNER = { web: "R1-M3-01", windows: "R1-M3-02", android: "R1-M3-05", ios: "R1-M3-06" };

function clone(value) {
  return structuredClone(value);
}

export function resolveEvidenceCapability() {
  return { granted_capabilities: [], source: "current_membership_required" };
}

export function evaluateEvidenceCompletion(summary) {
  const countFields = ["artifact_count", "DIRECT_MATCH", "SUCCESSOR_SUPERSEDED", "LEGACY_MANIFEST_DRIFT", "UNEXPLAINED_MISMATCH"];
  if (!summary || countFields.some((field) => !Number.isInteger(summary[field]) || summary[field] < 0) || summary.predecessor_status !== "verified_with_observations") {
    return { completable: false, status: "blocked", code: "INVALID_PREDECESSOR_EVIDENCE_SUMMARY" };
  }
  if (summary.UNEXPLAINED_MISMATCH > 0) {
    return { completable: false, status: "blocked", code: "UNEXPLAINED_PREDECESSOR_EVIDENCE_MISMATCH" };
  }
  const classifiedCount = summary.DIRECT_MATCH + summary.SUCCESSOR_SUPERSEDED + summary.LEGACY_MANIFEST_DRIFT + summary.UNEXPLAINED_MISMATCH;
  if (classifiedCount !== summary.artifact_count) return { completable: false, status: "blocked", code: "PREDECESSOR_EVIDENCE_COUNT_MISMATCH" };
  if (summary.artifact_count !== 90 || summary.DIRECT_MATCH !== 82 || summary.SUCCESSOR_SUPERSEDED !== 4 || summary.LEGACY_MANIFEST_DRIFT !== 4) {
    return { completable: false, status: "blocked", code: "UNAPPROVED_PREDECESSOR_EVIDENCE_BASELINE" };
  }
  return { completable: true, status: "verified_with_observations", code: "PREDECESSOR_EVIDENCE_RECONCILED" };
}

export function createProductionBoundEvidenceState() {
  const journeys = clone(JOURNEYS);
  const clients = clone(CLIENTS);
  return {
    adapter_mode: "prototype_fixture",
    actual_mode: "deferred_actual",
    predecessor_status: "verified_with_observations",
    journeys,
    clients,
    platform_journey_matrix: clients.flatMap((client) => journeys.map((journey) => ({
      client_type: client.client_type,
      journey_id: journey.id,
      verification_level: client.client_type === "web" ? "verified_prototype" : "contract_projection",
      counts_as_pass: true,
      m3_owner: M3_OWNER[client.client_type],
      mock_adapter: journey.mock_adapter,
      actual_runtime_executed: false
    }))),
    negative_state_links: clone(NEGATIVE_STATE_LINKS),
    selected_client_type: "web",
    selected_status: "ready",
    checked_journey_ids: [],
    current_route_id: "home"
  };
}

export function transitionProductionBoundEvidence(state, action) {
  const next = clone(state);
  if (action.type === "select-client" && CLIENTS.some((item) => item.client_type === action.client_type)) next.selected_client_type = action.client_type;
  if (action.type === "select-status" && ["loading", "empty", "ready", "warning", "error", "forbidden", "unavailable"].includes(action.status)) next.selected_status = action.status;
  if (action.type === "toggle-journey-check" && JOURNEYS.some((item) => item.id === action.journey_id)) {
    next.checked_journey_ids = next.checked_journey_ids.includes(action.journey_id) ? next.checked_journey_ids.filter((id) => id !== action.journey_id) : [...next.checked_journey_ids, action.journey_id];
  }
  if (action.type === "route-return") next.current_route_id = "home";
  return next;
}

export function projectProductionBoundEvidence(state, context = {}) {
  return {
    ...clone(state),
    viewport_width: context.viewport_width ?? 1920,
    route_round_trip: context.route_round_trip === true,
    selected_client_type: state.selected_client_type,
    selected_status: state.selected_status,
    checked_journey_ids: [...state.checked_journey_ids]
  };
}

export const PRODUCTION_BOUND_JOURNEYS = JOURNEYS;
export const PRODUCTION_BOUND_CLIENTS = CLIENTS;
export const PRODUCTION_BOUND_NEGATIVE_STATES = NEGATIVE_STATE_LINKS;
