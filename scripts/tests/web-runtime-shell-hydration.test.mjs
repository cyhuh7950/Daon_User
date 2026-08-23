import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const PANE_PATH = "apps/evidence-hub/src/evidence-hub.jsx";
const pane = fs.readFileSync(PANE_PATH, "utf8");
const model = await import("../../apps/evidence-hub/src/evidence-hub-model.js");

test("server와 browser 첫 hydration render는 같은 기본 reducer state로 시작한다", () => {
  assert.match(
    pane,
    /useReducer\(transitionProductionBoundEvidence, undefined, createProductionBoundEvidenceState\)/
  );
  const reducerLine = pane.split("\n").find((line) => line.includes("useReducer("));
  assert.doesNotMatch(reducerLine, /window|sessionStorage|restoreState/);
});

test("저장 session 복원은 hydration 이후 effect에서 안전 transition으로 수행한다", () => {
  assert.match(pane, /useEffect\(\(\) => \{[\s\S]*sessionStorage\.getItem\(STORAGE_KEY\)[\s\S]*dispatch\(\{ type: "select-client"/);
  assert.match(pane, /dispatch\(\{ type: "select-status"/);
  assert.match(pane, /dispatch\(\{ type: "toggle-journey-check"/);
  assert.match(pane, /Array\.isArray\(saved\.checked_journey_ids\)/);
  assert.match(pane, /new Set\(saved\.checked_journey_ids\)/);
});

test("복원 완료 전 persistence는 기존 저장 payload를 덮어쓰지 않는다", () => {
  assert.match(pane, /const \[sessionRestored, setSessionRestored\] = useState\(false\)/);
  assert.match(pane, /finally \{\s*setSessionRestored\(true\);\s*\}/);
  assert.match(pane, /useEffect\(\(\) => \{\s*if \(!sessionRestored\) return;[\s\S]*sessionStorage\.setItem/);
  assert.match(pane, /\}, \[sessionRestored, state\]\);/);
});

test("손상·부분·허용되지 않은 저장값은 기존 model transition으로 fail-close한다", () => {
  let state = model.createProductionBoundEvidenceState();
  state = model.transitionProductionBoundEvidence(state, { type: "select-client", client_type: "root" });
  state = model.transitionProductionBoundEvidence(state, { type: "select-status", status: "success" });
  state = model.transitionProductionBoundEvidence(state, { type: "toggle-journey-check", journey_id: "unknown" });
  assert.equal(state.selected_client_type, "web");
  assert.equal(state.selected_status, "ready");
  assert.deepEqual(state.checked_journey_ids, []);
  state = model.transitionProductionBoundEvidence(state, { type: "select-client", client_type: "ios" });
  state = model.transitionProductionBoundEvidence(state, { type: "select-status", status: "unavailable" });
  state = model.transitionProductionBoundEvidence(state, { type: "toggle-journey-check", journey_id: "negative_states" });
  assert.deepEqual(
    { client: state.selected_client_type, status: state.selected_status, checks: state.checked_journey_ids },
    { client: "ios", status: "unavailable", checks: ["negative_states"] }
  );
});
