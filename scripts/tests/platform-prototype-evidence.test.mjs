import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { APPROVED_PREDECESSOR_SPECIAL_CASES, APPROVED_PREDECESSOR_SUMMARY, buildPredecessorReconciliation, classifyPredecessorArtifact, normalizeManifestExpected, validateOriginCommit } from "../lib/predecessor-evidence-reconciliation.mjs";

export { buildPredecessorReconciliation };

const EVIDENCE_DIR = "docs/03_evidence/release_1/R1-M2-08";
const RECONCILIATION_PATH = `${EVIDENCE_DIR}/predecessor-evidence-reconciliation.json`;

if (process.argv.includes("--write-predecessor-reconciliation")) {
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
  fs.writeFileSync(RECONCILIATION_PATH, `${JSON.stringify(buildPredecessorReconciliation(), null, 2)}\n`, "utf8");
  console.log(`wrote ${RECONCILIATION_PATH}`);
} else {
  const model = await import("../../packages/ui/src/production-bound-evidence-model.js").catch(() => ({}));
  const currentReconciliation = buildPredecessorReconciliation();
  const expectedJourneys = ["workspace_context", "knowledge_authority", "model_lineage", "studio_generation", "review_delivery_registration", "account_security", "operations_recovery", "negative_states"];
  const expectedClients = ["web", "windows", "android", "ios"];

  test("8개 여정과 4개 client projection을 누락 없이 고정한다", () => {
    const state = model.createProductionBoundEvidenceState();
    assert.deepEqual(state.journeys.map((item) => item.id), expectedJourneys);
    assert.deepEqual(state.clients.map((item) => item.client_type), expectedClients);
    assert.equal(state.platform_journey_matrix.length, 32);
  });

  test("각 플랫폼 판정은 M3 owner와 실제 실행 수준을 정직하게 분리한다", () => {
    const state = model.createProductionBoundEvidenceState();
    for (const row of state.platform_journey_matrix) {
      assert.match(row.m3_owner, /^R1-M3-0[1-6]$/);
      assert.ok(["verified_prototype", "contract_projection", "deferred_actual", "blocked"].includes(row.verification_level));
      if (["deferred_actual", "blocked"].includes(row.verification_level)) assert.equal(row.counts_as_pass, false);
    }
  });

  test("route와 screen은 승인 정본 밖 값을 만들지 않는다", () => {
    const state = model.createProductionBoundEvidenceState();
    const navigation = JSON.parse(fs.readFileSync("packages/contracts/navigation.json", "utf8"));
    const screens = JSON.parse(fs.readFileSync("packages/contracts/screens.json", "utf8"));
    const routeIds = new Set(navigation.routes.map((item) => item.route_id));
    const screenIds = new Set(screens.screens.map((item) => item.screen_id));
    for (const journey of state.journeys) {
      for (const route of journey.routes) assert.ok(routeIds.has(route.route_id));
      for (const screenId of journey.screen_ids) assert.ok(screenIds.has(screenId));
    }
  });

  test("persona, client_type, viewport는 membership capability를 만들지 않는다", () => {
    assert.equal(model.resolveEvidenceCapability({ persona: "operator", client_type: "windows", viewport_width: 1920 }).granted_capabilities.length, 0);
  });

  test("Windows와 Mobile projection은 Native 실행 완료로 위장하지 않는다", () => {
    const state = model.createProductionBoundEvidenceState();
    const windows = state.clients.find((item) => item.client_type === "windows");
    const mobile = state.clients.filter((item) => ["android", "ios"].includes(item.client_type));
    assert.equal(windows.native_runtime_executed, false);
    assert.equal(windows.ipc_or_local_service_verified, false);
    for (const item of mobile) {
      assert.equal(item.dom_ui_imported, false);
      assert.equal(item.native_runtime_executed, false);
    }
  });

  test("필수 부정 상태와 권한·축소 운영 연결을 전부 포함한다", () => {
    const state = model.createProductionBoundEvidenceState();
    for (const required of ["loading", "empty", "warning", "error", "forbidden", "unavailable", "IMPORTANT_KNOWLEDGE_CONFLICT", "COST_LIMIT_EXCEEDED", "STEP_UP_REQUIRED", "G9_DRILL_APPROVAL_REQUIRED", "G9_DEPLOY_APPROVAL_REQUIRED", "APPROVAL_DELIVERY_BLOCKED"]) {
      assert.ok(state.negative_state_links.some((item) => item.code === required));
    }
  });

  test("선행 Reconciliation은 80/6/4/0이며 legacy drift를 PASS로 세지 않는다", () => {
    const reconciliation = currentReconciliation;
    assert.deepEqual(reconciliation.summary, { artifact_count: 90, DIRECT_MATCH: 80, SUCCESSOR_SUPERSEDED: 6, LEGACY_MANIFEST_DRIFT: 4, UNEXPLAINED_MISMATCH: 0, predecessor_status: "verified_with_observations" });
    assert.deepEqual(reconciliation.summary, APPROVED_PREDECESSOR_SUMMARY);
    assert.equal(reconciliation.entries.filter((item) => item.status === "LEGACY_MANIFEST_DRIFT").every((item) => item.current_m2_08_impact.includes("no predecessor hash-completeness PASS")), true);
    assert.equal(reconciliation.entries.filter((item) => item.expected_bytes_source === "LEGACY_SHA_MATCHED_REPRESENTATION").length, 24);
    assert.equal(reconciliation.entries.filter((item) => item.expected_bytes_source === "VERIFIED_ORIGIN_BLOB").length, 2);
  });

  test("설명되지 않은 Manifest 변조는 완료를 fail-close한다", () => {
    const result = model.evaluateEvidenceCompletion({ ...currentReconciliation.summary, UNEXPLAINED_MISMATCH: 1 });
    assert.deepEqual(result, { completable: false, status: "blocked", code: "UNEXPLAINED_PREDECESSOR_EVIDENCE_MISMATCH" });
  });

  test("C01 일반 Artifact는 Hash와 Byte의 실제 일치 없이는 DIRECT_MATCH가 아니다", () => {
    const expected = { sha256: "A".repeat(64), bytes: 3 };
    assert.equal(classifyPredecessorArtifact({ expected, raw: { available: true, sha256: "B".repeat(64), bytes: 3 }, canonical: { available: true, sha256: "B".repeat(64), bytes: 3 } }).status, "UNEXPLAINED_MISMATCH");
    assert.equal(classifyPredecessorArtifact({ expected, raw: { available: true, sha256: expected.sha256, bytes: 4 }, canonical: { available: false } }).status, "UNEXPLAINED_MISMATCH");
    assert.equal(classifyPredecessorArtifact({ expected, raw: { available: true, sha256: "B".repeat(64), bytes: 3 }, canonical: { available: true, sha256: "C".repeat(64), bytes: 3 } }).status, "UNEXPLAINED_MISMATCH");
    assert.equal(classifyPredecessorArtifact({ expected, raw: { available: false }, canonical: { available: false } }).status, "UNEXPLAINED_MISMATCH");
  });

  test("C01 유효한 Raw 또는 Git Canonical Hash·Byte 쌍만 DIRECT_MATCH다", () => {
    const expected = { sha256: "A".repeat(64), bytes: 3 };
    assert.deepEqual(classifyPredecessorArtifact({ expected, raw: { available: true, sha256: expected.sha256, bytes: 3 }, canonical: { available: false } }), { status: "DIRECT_MATCH", verification_representation: "RAW", code: "RAW_HASH_AND_BYTES_MATCH" });
    assert.deepEqual(classifyPredecessorArtifact({ expected, raw: { available: true, sha256: "B".repeat(64), bytes: 3 }, canonical: { available: true, sha256: expected.sha256, bytes: 3 } }), { status: "DIRECT_MATCH", verification_representation: "GIT_CANONICAL", code: "GIT_CANONICAL_HASH_AND_BYTES_MATCH" });
    assert.deepEqual(classifyPredecessorArtifact({ expected, raw: { available: false }, canonical: { available: true, sha256: "B".repeat(64), bytes: 2 }, canonical_crlf: { available: true, sha256: expected.sha256, bytes: 3 } }), { status: "DIRECT_MATCH", verification_representation: "GIT_CRLF", code: "GIT_CRLF_HASH_AND_BYTES_MATCH" });
  });

  test("C01 Manifest 기대값과 Special Case 계보가 유효하지 않으면 fail-close한다", () => {
    const validExpected = { sha256: "A".repeat(64), bytes: 3 };
    for (const expected of [{}, { sha256: null, bytes: 3 }, { sha256: "A".repeat(63), bytes: 3 }, { sha256: validExpected.sha256, bytes: null }, { sha256: validExpected.sha256, bytes: -1 }, { sha256: validExpected.sha256, bytes: 1.5 }]) {
      assert.equal(classifyPredecessorArtifact({ expected, raw: { available: false }, canonical: { available: false } }).status, "UNEXPLAINED_MISMATCH");
    }
    assert.equal(classifyPredecessorArtifact({ expected: validExpected, raw: { available: true, sha256: "B".repeat(64), bytes: 3 }, canonical: { available: false }, special_case: { status: "SUCCESSOR_SUPERSEDED", lineage_verified: false } }).status, "UNEXPLAINED_MISMATCH");
    assert.equal(classifyPredecessorArtifact({ expected: validExpected, raw: { available: true, sha256: "B".repeat(64), bytes: 3 }, canonical: { available: false }, special_case: { status: "LEGACY_MANIFEST_DRIFT", lineage_verified: false } }).status, "UNEXPLAINED_MISMATCH");
    assert.equal(classifyPredecessorArtifact({ expected: validExpected, raw: { available: true, sha256: "B".repeat(64), bytes: 3 }, canonical: { available: false }, special_case: { status: "UNKNOWN_SUCCESSOR", lineage_verified: true } }).status, "UNEXPLAINED_MISMATCH");
    assert.equal(classifyPredecessorArtifact({ expected: validExpected, raw: { available: false }, canonical: { available: false }, special_case: { status: "SUCCESSOR_SUPERSEDED", lineage_verified: true } }).status, "UNEXPLAINED_MISMATCH");
  });

  test("C01 legacy bytes 정규화는 필드 부재와 Manifest SHA 일치 표현에만 한정한다", () => {
    const sha = "A".repeat(64);
    const matching = { available: true, sha256: sha, bytes: 7 };
    assert.deepEqual(normalizeManifestExpected({ sha256: sha }, [matching]), { expected: { sha256: sha, bytes: 7 }, bytes_source: "LEGACY_SHA_MATCHED_REPRESENTATION" });
    assert.deepEqual(normalizeManifestExpected({ sha256: sha, bytes: null }, [matching]), { expected: { sha256: sha, bytes: null }, bytes_source: "MANIFEST" });
    assert.deepEqual(normalizeManifestExpected({ sha256: sha }, [{ available: true, sha256: "B".repeat(64), bytes: 7 }]), { expected: { sha256: sha, bytes: undefined }, bytes_source: "MISSING_UNVERIFIED" });
  });

  test("C01 Summary는 완전한 승인 기준선 90·80/6/4/0만 완료한다", () => {
    const approved = { artifact_count: 90, DIRECT_MATCH: 82, SUCCESSOR_SUPERSEDED: 4, LEGACY_MANIFEST_DRIFT: 4, UNEXPLAINED_MISMATCH: 0, predecessor_status: "verified_with_observations" };
    assert.deepEqual(model.evaluateEvidenceCompletion(approved), { completable: true, status: "verified_with_observations", code: "PREDECESSOR_EVIDENCE_RECONCILED" });
    for (const invalid of [
      {},
      { ...approved, DIRECT_MATCH: undefined },
      { ...approved, UNEXPLAINED_MISMATCH: null },
      { ...approved, DIRECT_MATCH: "82" },
      { ...approved, DIRECT_MATCH: Number.NaN },
      { ...approved, DIRECT_MATCH: -1 },
      { ...approved, DIRECT_MATCH: 81.5 },
      { ...approved, DIRECT_MATCH: 81 },
      { ...approved, artifact_count: 89 },
      { ...approved, predecessor_status: "ready" }
    ]) assert.equal(model.evaluateEvidenceCompletion(invalid).completable, false);
  });

  test("C02 Legacy 4건은 고정 Work Order·경로·SHA·Byte·계보가 모두 정확할 때만 승인된다", () => {
    const legacyCases = APPROVED_PREDECESSOR_SPECIAL_CASES.filter((item) => item.status === "LEGACY_MANIFEST_DRIFT");
    assert.equal(legacyCases.length, 4);
    for (const item of legacyCases) {
      const base = {
        source_work_order: item.source_work_order,
        artifact_path: item.artifact_path,
        expected: { sha256: item.expected_sha256, bytes: item.expected_bytes },
        raw: { available: true, sha256: "A".repeat(64), bytes: 1 },
        canonical: { available: true, sha256: "B".repeat(64), bytes: 2 },
        special_case: { lineage_verified: true }
      };
      assert.equal(classifyPredecessorArtifact(base).status, "LEGACY_MANIFEST_DRIFT");
      assert.equal(classifyPredecessorArtifact({ ...base, expected: { ...base.expected, sha256: "C".repeat(64) } }).status, "UNEXPLAINED_MISMATCH");
      assert.equal(classifyPredecessorArtifact({ ...base, expected: { ...base.expected, bytes: base.expected.bytes + 1 } }).status, "UNEXPLAINED_MISMATCH");
      assert.equal(classifyPredecessorArtifact({ ...base, expected: { sha256: "D".repeat(64), bytes: base.expected.bytes + 1 } }).status, "UNEXPLAINED_MISMATCH");
      assert.deepEqual(
        classifyPredecessorArtifact({ ...base, expected: { sha256: base.raw.sha256, bytes: base.raw.bytes } }),
        { status: "UNEXPLAINED_MISMATCH", verification_representation: null, code: "LEGACY_EXPECTATION_MISMATCH" }
      );
      assert.deepEqual(
        classifyPredecessorArtifact({ ...base, expected: { sha256: base.canonical.sha256, bytes: base.canonical.bytes } }),
        { status: "UNEXPLAINED_MISMATCH", verification_representation: null, code: "LEGACY_EXPECTATION_MISMATCH" }
      );
      assert.equal(classifyPredecessorArtifact({ ...base, source_work_order: "R1-M2-99" }).status, "UNEXPLAINED_MISMATCH");
      assert.equal(classifyPredecessorArtifact({ ...base, artifact_path: `${item.artifact_path}.swapped` }).status, "UNEXPLAINED_MISMATCH");
    }
    assert.equal(classifyPredecessorArtifact({ source_work_order: "R1-M2-99", artifact_path: "unknown", expected: { sha256: "A".repeat(64), bytes: 1 }, raw: { available: true, sha256: "B".repeat(64), bytes: 1 }, canonical: { available: false }, special_case: { lineage_verified: true } }).status, "UNEXPLAINED_MISMATCH");
  });

  test("FIX-05 Lockfile 2건은 경로·이전 Hash/Byte·Origin·Successor·현재 Hash/Byte가 모두 정확할 때만 승인된다", () => {
    const lockfileCases = APPROVED_PREDECESSOR_SPECIAL_CASES.filter((item) => item.artifact_path === "package-lock.json");
    assert.deepEqual(
      lockfileCases.map((item) => ({
        source_work_order: item.source_work_order,
        artifact_path: item.artifact_path,
        expected_sha256: item.expected_sha256,
        expected_bytes: item.expected_bytes,
        origin_commit: item.origin_commit,
        successor_commit: item.successor_commit,
        current_sha256: item.current_sha256,
        current_bytes: item.current_bytes
      })),
      [
        {
          source_work_order: "R1-M2-06",
          artifact_path: "package-lock.json",
          expected_sha256: "69E87A118E89CF8ADF8CE35E571EB2EB6B7D5277EB405609FEC83F04B75DC161",
          expected_bytes: 156787,
          origin_commit: "780ca50725233227076a40f5adb2b5f1e05b1070",
          successor_commit: "8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa",
          current_sha256: "96E9044F4B91A5C5872A460EBAAA3C9C86EEFD7DD3CF5A5764E7664C6E93FDC5",
          current_bytes: 181571
        },
        {
          source_work_order: "R1-M2-07",
          artifact_path: "package-lock.json",
          expected_sha256: "69E87A118E89CF8ADF8CE35E571EB2EB6B7D5277EB405609FEC83F04B75DC161",
          expected_bytes: 156787,
          origin_commit: "ab2a3b055581fcaea75cceafc3bb8bedb2a80066",
          successor_commit: "8fafe2fd1a4a828ea7d90e44c2de4320f4b9a0aa",
          current_sha256: "96E9044F4B91A5C5872A460EBAAA3C9C86EEFD7DD3CF5A5764E7664C6E93FDC5",
          current_bytes: 181571
        }
      ]
    );
    for (const item of lockfileCases) {
      const base = {
        source_work_order: item.source_work_order,
        artifact_path: item.artifact_path,
        expected: { sha256: item.expected_sha256, bytes: item.expected_bytes },
        raw: { available: true, sha256: item.current_sha256, bytes: item.current_bytes },
        canonical: { available: false },
        special_case: {
          lineage_verified: true,
          origin_commit: item.origin_commit,
          successor_commit: item.successor_commit,
          current_sha256: item.current_sha256,
          current_bytes: item.current_bytes
        }
      };
      assert.equal(classifyPredecessorArtifact(base).status, "SUCCESSOR_SUPERSEDED");
      for (const invalid of [
        { ...base, source_work_order: "R1-M2-99" },
        { ...base, artifact_path: `${item.artifact_path}.swapped` },
        { ...base, expected: { ...base.expected, sha256: "A".repeat(64) } },
        { ...base, expected: { ...base.expected, bytes: base.expected.bytes + 1 } },
        { ...base, special_case: { ...base.special_case, origin_commit: "a".repeat(40) } },
        { ...base, special_case: { ...base.special_case, successor_commit: "b".repeat(40) } },
        { ...base, special_case: { ...base.special_case, current_sha256: "C".repeat(64) } },
        { ...base, special_case: { ...base.special_case, current_bytes: base.special_case.current_bytes + 1 } },
        { ...base, special_case: { ...base.special_case, lineage_verified: false } },
        { ...base, raw: { available: false } },
        { ...base, raw: { ...base.raw, sha256: "D".repeat(64) } },
        { ...base, raw: { ...base.raw, bytes: base.raw.bytes + 1 } }
      ]) assert.equal(classifyPredecessorArtifact(invalid).status, "UNEXPLAINED_MISMATCH");
    }
  });

  test("C02 90개 Origin 계보는 문자열/null이고 일반 80·Special 10의 선언 출처와 일치한다", () => {
    const manifestOrigins = new Map();
    for (let number = 2; number <= 7; number += 1) {
      const workOrder = `R1-M2-0${number}`;
      const manifest = JSON.parse(fs.readFileSync(`docs/03_evidence/release_1/${workOrder}/evidence-manifest.json`, "utf8"));
      manifestOrigins.set(workOrder, manifest.validated_head ?? manifest.source_commit ?? manifest.head_sha ?? manifest.implementation_sha ?? null);
    }
    const approvedSpecial = new Map(APPROVED_PREDECESSOR_SPECIAL_CASES.map((item) => [`${item.source_work_order}|${item.artifact_path}`, item]));
    assert.equal(currentReconciliation.entries.length, 90);
    for (const entry of currentReconciliation.entries) {
      assert.ok(entry.origin_implementation_or_evidence_commit === null || /^[0-9a-f]{7,40}$/i.test(entry.origin_implementation_or_evidence_commit));
      if (entry.origin_implementation_or_evidence_commit !== null) assert.equal(entry.origin_commit_exists, true);
      const special = approvedSpecial.get(`${entry.source_work_order}|${entry.artifact_path}`);
      assert.equal(entry.origin_implementation_or_evidence_commit, special?.origin_commit ?? manifestOrigins.get(entry.source_work_order));
    }
    assert.equal(currentReconciliation.entries.filter((item) => item.status === "DIRECT_MATCH").length, 80);
    assert.equal(currentReconciliation.entries.filter((item) => approvedSpecial.has(`${item.source_work_order}|${item.artifact_path}`)).length, 10);
  });

  test("C02 Origin은 null 또는 존재하는 7~40자 Git SHA만 허용한다", () => {
    assert.equal(validateOriginCommit(null, null), true);
    assert.equal(validateOriginCommit("682cd3c", true), true);
    assert.equal(validateOriginCommit("a".repeat(40), true), true);
    for (const invalid of ["", "abc123", "g".repeat(40), "a".repeat(6), "a".repeat(41), {}, []]) {
      assert.equal(validateOriginCommit(invalid, true), false);
    }
    assert.equal(validateOriginCommit("682cd3c", false), false);
  });

  test("상태와 check 선택은 폭·route 왕복 뒤 보존된다", () => {
    let state = model.createProductionBoundEvidenceState();
    state = model.transitionProductionBoundEvidence(state, { type: "select-client", client_type: "ios" });
    state = model.transitionProductionBoundEvidence(state, { type: "select-status", status: "unavailable" });
    state = model.transitionProductionBoundEvidence(state, { type: "toggle-journey-check", journey_id: "negative_states" });
    const projected = model.projectProductionBoundEvidence(state, { viewport_width: 500, route_round_trip: true });
    assert.equal(projected.selected_client_type, "ios");
    assert.equal(projected.selected_status, "unavailable");
    assert.equal(projected.checked_journey_ids.includes("negative_states"), true);
  });

  test("Home은 전용 Evidence Hub에 연결되고 실제 기존 Route 5종을 제공한다", () => {
    const page = fs.readFileSync("apps/web/app/page.jsx", "utf8");
    const pane = fs.readFileSync("packages/ui/src/production-bound-evidence-pane.jsx", "utf8");
    assert.match(page, /ProductionBoundEvidenceHub/);
    for (const href of ["/workspaces/workspace-release-one", "/settings/account", "/settings/organization", "/operations", "/notifications"]) assert.match(pane, new RegExp(`href=\\"${href.replaceAll("/", "\\/")}\\"`));
    assert.match(page, /route_id === "home"/);
    assert.match(page, /screen_id === "home"/);
    assert.match(pane, /data-route-id=\{route\.route_id\}/);
    assert.match(pane, /data-screen-id=\{screen\.screen_id\}/);
  });

  test("Hub Browser source에는 직접 API·내부주소·Mobile DOM Import·Native 성공 주장이 없다", () => {
    const sources = ["apps/web/app/page.jsx", "packages/ui/src/production-bound-evidence-pane.jsx", "packages/ui/src/production-bound-evidence-model.js"].map((file) => fs.readFileSync(file, "utf8")).join("\n");
    assert.doesNotMatch(sources, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_API_BASE_URL|fetch\s*\(/);
    assert.doesNotMatch(sources, /react-native|@react-native/);
    assert.match(sources, /native_runtime_executed:\s*false/);
    assert.match(sources, /deferred_actual/);
  });
}
