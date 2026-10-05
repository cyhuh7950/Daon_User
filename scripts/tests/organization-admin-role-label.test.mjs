import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("조직 역할 선택기는 워크플로 표기와 실제 실행 권한의 분리를 알린다", async () => {
  const source = await read("apps/web/components/organization-admin-console.jsx");
  const roleLabel = source.match(/<label className="org-console-select">([^<]+)<select\b/u)?.[1];
  assert.ok(roleLabel, "기존 조직 역할 선택기가 있어야 한다");
  assert.match(roleLabel, /조직\s*워크플로\s*표기\s*역할/u);
  assert.match(roleLabel, /실제\s*실행\s*권한(?:과|은)?\s*별개/u);
});

test("기존 조직 역할 변경과 상태 변경 요청은 그대로 분리된다", async () => {
  const [consoleSource, apiSource] = await Promise.all([
    read("apps/web/components/organization-admin-console.jsx"),
    read("apps/web/lib/organization-admin-api.js"),
  ]);
  assert.match(consoleSource, /changeMemberRole\(tenantId, member\.user_id, \{ role: value, expected_version: member\.version \}\)/u);
  assert.match(consoleSource, /changeMemberState\(tenantId, member\.user_id, active, \{ expected_version: member\.version \}\)/u);
  assert.match(apiSource, /export const changeMemberRole[^\n]*\/tenants\/\$\{encodeURIComponent\(tenantId\)\}\/members\/\$\{encodeURIComponent\(userId\)\}\/role/u);
});

test("C9 관리자 사용자 화면은 구 조직 역할 변경 API를 호출하지 않는다", async () => {
  const source = await read("apps/web/components/admin-user-console.jsx");
  assert.doesNotMatch(source, /organization-admin-api|changeMemberRole|\/organization\/tenants\//u);
});
