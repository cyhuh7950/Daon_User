import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const workspace = fs.readFileSync("apps/web/components/provider-settings-workspace.jsx", "utf8");
const api = fs.readFileSync("apps/web/lib/provider-settings-api.js", "utf8");
const proxy = fs.readFileSync("apps/web/lib/bff-api-proxy.js", "utf8");

test("provider health settings expose a sixty minute default and admin save control", () => {
  assert.match(workspace, /interval_minutes: 60/);
  assert.match(workspace, /상태 확인 주기/);
  assert.match(workspace, /value=\{60\}/);
  assert.match(workspace, /Active|활성|사용 후보/);
  assert.match(workspace, /모델 조회.*선택 사항/);
});

test("provider health settings use same-origin BFF paths", () => {
  assert.match(api, /\/bff\/api\/admin\/provider-health-settings/);
  assert.match(proxy, /provider-health-settings/);
  assert.doesNotMatch(api, /https?:\/\//);
});
