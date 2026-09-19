import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../../apps/web/components/provider-settings-workspace.jsx", import.meta.url), "utf8");
const adminSource = await readFile(new URL("../../services/api/src/daon_user_api/provider_connection_admin.py", import.meta.url), "utf8");

test("provider connection draft exposes a provider endpoint and removes legacy default-model controls", () => {
  assert.match(source, /base_url:\s*connection\.base_url\s*\|\|\s*""/u);
  assert.match(source, /API Key를 저장했습니다/u);
  assert.doesNotMatch(source, /API Key를 저장했습니다\. 이제 모델 조회를 눌러/u);
  assert.match(source, /API Key 저장과 <strong>모델 조회<\/strong>는 선택 사항입니다/u);
  assert.match(source, /canRefreshCatalog\(selectedConnection, busy\)/u);
  assert.doesNotMatch(source, /NO_CREDENTIAL_PROVIDERS/u);
  assert.match(source, /MANAGED_MODEL_PROVIDERS/u);
  assert.match(source, /모델 목록을 Daon에 저장하지 않으며/u);
  assert.match(source, /연결할 모델 \(선택\)/u);
  assert.match(source, /Provider 기준 모델을 사용합니다/u);
  assert.match(source, /저장된 API Key로 모델 목록을 수동 조회합니다/u);
  assert.match(source, /canSaveCredential\(selectedConnection, draft, credential, busy\)/u);
  assert.match(source, /API Key 또는 Client Key \(선택\)/u);
  assert.match(source, /canSaveCredential\(selectedConnection, draft, credential, busy\)/u);
  assert.doesNotMatch(source, /기능별 모델 선택|모델 기능 보정/u);
  assert.doesNotMatch(source, /stepUp|step_up|관리자 확인|관리자 비밀번호|현재 비밀번호|추가 암호/u);
});

test("provider catalog defaults use the installed OmniRoute and Media Bridge endpoints", () => {
  assert.match(adminSource, /"OMNIROUTE":\s*"http:\/\/localhost:20128\/v1"/u);
  assert.match(adminSource, /"MEDIA_BRIDGE":\s*"http:\/\/127\.0\.0\.1:8642\/v1"/u);
});
