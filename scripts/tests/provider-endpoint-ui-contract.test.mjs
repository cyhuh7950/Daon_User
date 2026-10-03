import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../../apps/web/components/provider-settings-workspace.jsx", import.meta.url), "utf8");
const modelConnectionsPage = await readFile(new URL("../../apps/web/app/settings/model-connections/page.jsx", import.meta.url), "utf8");
const providerStyles = await readFile(new URL("../../apps/web/app/settings/model-connections/provider-settings.css", import.meta.url), "utf8");
const adminSource = await readFile(new URL("../../services/api/src/daon_user_api/provider_connection_admin.py", import.meta.url), "utf8");

test("LLM 설정은 Notebook 복귀 링크와 선택되지 않은 카드의 읽기 쉬운 텍스트 색상을 제공한다", () => {
  assert.match(modelConnectionsPage, /showNotebookLink/u);
  assert.match(source, /href="\/notebooks"/u);
  assert.match(source, /Notebook으로/u);
  assert.match(providerStyles, /\.provider-card:not\(\[aria-pressed="true"\]\).*color:/u);
  assert.match(providerStyles, /\.provider-card:not\(\[aria-pressed="true"\]\) small.*color:/u);
});

test("provider connection draft keeps endpoint, admin step-up and read-only user boundary", () => {
  assert.match(source, /base_url:\s*connection\.base_url\s*\|\|\s*""/u);
  assert.match(source, /canRefreshCatalog\(selectedConnection, busy\)/u);
  assert.doesNotMatch(source, /NO_CREDENTIAL_PROVIDERS/u);
  assert.match(source, /MANAGED_MODEL_PROVIDERS/u);
  assert.match(source, /이 Provider가 모델을 직접 관리합니다/u);
  assert.match(source, /저장된 API Key로 모델 목록을 수동 조회합니다/u);
  assert.match(source, /canSaveCredential\(selectedConnection, draft, credential, busy\)/u);
  assert.match(source, /CREDENTIAL_REQUIRED_PROVIDERS/u);
  assert.match(source, /providerRequiresCredential\(providerCode, baseUrl/u);
  assert.match(source, /관리자 재인증 비밀번호/u);
  assert.match(source, /issueStepUp/u);
  assert.match(source, /step_up_authorization_id/u);
  assert.match(source, /연결 이름과 허용 모델은 읽기 전용입니다/u);
  assert.match(source, /호환 방식/u);
  assert.match(source, /모델 ID 직접 입력/u);
  assert.doesNotMatch(source, /기능별 모델 선택|모델 기능 보정/u);
});

test("provider catalog defaults use the installed OmniRoute and Media Bridge endpoints", () => {
  assert.match(adminSource, /"OMNIROUTE":\s*"http:\/\/localhost:20128\/v1"/u);
  assert.match(adminSource, /"MEDIA_BRIDGE":\s*"http:\/\/127\.0\.0\.1:8642\/v1"/u);
});
