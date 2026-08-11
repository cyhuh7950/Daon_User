# Daon Evidence Hub와 사용자 제품 분리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 개발·검증용 Evidence Hub를 무인증 로컬 전용 앱으로 분리하고, Web·Windows 제품을 로그인 후 실제 `Workspace → Source → 질문 → Citation → 근거 기반 보고서` 흐름으로 전환한다.

**Architecture:** `apps/evidence-hub`는 정적 Fixture와 SessionStorage만 사용하는 별도 Vite 앱이며 제품 Build Graph와 의존성을 공유하지 않는다. Web 제품은 same-origin BFF, Windows 제품은 고정 Gateway를 호출하는 전용 Tauri Command만 사용하고, 공용 `ProductWorkspace`는 Safe DTO를 반환하는 주입형 Adapter만 소비한다. 작업은 경계 분리, 실제 Web 수직 흐름, Windows Native 수직 흐름, 실제 화면 검증의 네 Gate로 순차 진행한다.

**Tech Stack:** React 19.2.7, Next 16.3.0-canary.93, Vite 8.1.5, Tauri 2.11.4, Rust/reqwest 0.13.4, FastAPI, PostgreSQL Canon tables, Node test runner, pytest 9.0.3.

## Global Constraints

- 공식 작업공간은 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, Branch는 `master`다. `D:\Project\Daon_User`는 수정하지 않는다.
- 한 시점에 어울2 Writer 한 명만 구현한다. 어울1은 설계·검토·작업지시를 소유하고 동시에 코드를 수정하지 않는다.
- 기존 사용자 삭제 31건과 원래 미추적 문서 3건은 신산님의 정확한 경로 복원 승인 전까지 보존한다.
- 제품 Browser 코드는 `/bff/api/...` same-origin 상대 경로만 호출한다. 절대 API URL, `localhost`, `127.0.0.1`, Docker Host·Port, `NEXT_PUBLIC_API_BASE_URL`을 금지한다.
- Desktop WebView는 직접 `fetch`하지 않는다. Rust 내부의 고정 Gateway와 전용 Tauri Command만 사용한다.
- Evidence Hub는 `127.0.0.1` 로컬 개발 명령만 제공하고 인증·API·DB·Upload·Recovery·외부 Network 호출을 모두 금지한다.
- 제품 Build Source와 최종 Bundle에서 `ProductionBoundEvidenceHub`, `prototype_fixture`, `deferred_actual`, `Mock Adapter`, Evidence 앱 Import를 0건으로 유지한다.
- 화면 기준은 1920×1080, 본문·폼 12px, 설명 10px, 보조 9px, 사이드바 제목 14px, 제목 16px다. 설명은 `i` Tooltip·Popover로 제공한다.
- 실제 API 실패를 Fixture 성공으로 대체하지 않는다. Safe 상태와 Trace만 표시하며 Password·Credential·Authorization·내부 URL을 노출하지 않는다.
- Upstage Provider를 사용하는 실제 생성 경로의 Model은 `solar-pro4`로 고정한다.
- Commit·Push는 각 작업 검토 완료 후 어울1이 수행한다. 배포는 D01 Go 승인 전 수행하지 않는다.

---

### Task 0: 기준선과 제한 복원 Gate 확정

**Files:**
- Modify: `AGENTS.md`
- Restore only after explicit approval: `apps/web/app/api/v1/[...path]/route.js`
- Restore only after explicit approval: `apps/web/app/bff/api/[...path]/route.js`
- Restore only after explicit approval: `apps/web/app/bff/shell/runtime/route.js`
- Restore only after explicit approval: `apps/web/app/workspaces/[workspace_id]/page.jsx`
- Create: `docs/02_work_orders/release_1/R1-USER-PRODUCT-SEPARATION-01_work_order.md`
- Create: `docs/02_work_orders/release_1/R1-USER-PRODUCT-SEPARATION-01_prompt.md`
- Create: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_progress.md`

**Interfaces:**
- Consumes: 승인 설계 `docs/superpowers/specs/2026-08-11-evidence-hub-product-separation-design.md`와 본 계획.
- Produces: 정확한 정본 경로, 허용 파일, 복원 4경로, 테스트, 결과 계약이 고정된 어울2 작업 패킷.

- [ ] **Step 1: 정본 경로 규칙을 현재 실제 경로로 고정한다**

`AGENTS.md`의 존재하지 않는 OneDrive 경로를 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`로 바꾸고 `D:\Project\Daon_User` 수정 금지를 유지한다.

- [ ] **Step 2: 제한 복원 전 상태를 검증한다**

Run:

```powershell
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git diff --check
```

Expected: Desktop 정본, `master`, 승인 HEAD 계보, 사용자 삭제 31건과 원 미추적 3건이 기록과 일치한다.

- [ ] **Step 3: 신산님의 정확한 복원 승인을 확인한다**

승인 문구에는 위 4개 경로만 포함한다. 승인 전에는 `git restore`, `checkout`, `reset`, `clean`을 실행하지 않는다.

- [ ] **Step 4: 승인된 4경로만 HEAD 원본으로 복원한다**

Run:

```powershell
git restore --worktree -- 'apps/web/app/api/v1/[...path]/route.js' 'apps/web/app/bff/api/[...path]/route.js' 'apps/web/app/bff/shell/runtime/route.js' 'apps/web/app/workspaces/[workspace_id]/page.jsx'
```

Expected: 해당 4경로의 `git diff`는 0건이고 나머지 사용자 삭제 27건과 원 미추적 3건은 보존된다.

- [ ] **Step 5: 작업지시서와 실행 프롬프트를 작성한다**

작업지시서는 Task 1~8의 허용 파일, 단계별 TDD, 진행기록, 정식 결과 계약을 포함한다. 프롬프트는 설계·계획·작업지시서를 EOF까지 읽고 실행하라는 지시만 담는다.

- [ ] **Step 6: Gate 문서를 검증하고 Commit한다**

Run:

```powershell
git diff --check
git status --short
```

Commit files: `AGENTS.md`, 복원된 4경로, 작업지시서, 프롬프트, progress.

---

### Task 1: Evidence Hub를 로컬 전용 앱으로 분리

**Files:**
- Create: `apps/evidence-hub/package.json`
- Create: `apps/evidence-hub/index.html`
- Create: `apps/evidence-hub/src/main.jsx`
- Create: `apps/evidence-hub/src/evidence-hub.jsx`
- Create: `apps/evidence-hub/src/evidence-hub-model.js`
- Create: `apps/evidence-hub/src/evidence-hub.css`
- Modify: `packages/ui/src/index.js`
- Modify: `packages/ui/src/workspace.css`
- Delete after content-preserving move: `packages/ui/src/production-bound-evidence-pane.jsx`
- Delete after content-preserving move: `packages/ui/src/production-bound-evidence-model.js`
- Modify: `package.json`
- Modify: `apps/web/package.json`
- Create: `scripts/tests/evidence-hub-boundary.test.mjs`
- Modify: `scripts/tests/platform-prototype-evidence.test.mjs`

**Interfaces:**
- Consumes: 기존 `ProductionBoundEvidenceHub` UI·Model과 M2 Evidence 계약.
- Produces: `EvidenceHubApp`, `createProductionBoundEvidenceState()`, 로컬 명령 `npm run dev:evidence-hub`, 검증 명령 `npm run verify:evidence-hub`.

- [ ] **Step 1: 제품과 Evidence 경계 RED를 작성한다**

```js
test("Evidence Hub는 별도 앱이며 제품 UI export가 아니다", async () => {
  const uiIndex = await read("packages/ui/src/index.js");
  const root = JSON.parse(await read("package.json"));
  assert.doesNotMatch(uiIndex, /ProductionBoundEvidenceHub/);
  assert.equal(root.scripts["dev:evidence-hub"], "npm run dev --workspace @daon-user/evidence-hub -- --host 127.0.0.1");
});
```

- [ ] **Step 2: RED를 실행한다**

Run: `node --test scripts/tests/evidence-hub-boundary.test.mjs`

Expected: Evidence workspace와 명령 부재, 제품 UI export 잔존으로 FAIL.

- [ ] **Step 3: Evidence 자산을 내용 보존 이동한다**

`production-bound-evidence-pane.jsx`는 `EvidenceHubApp`으로 이름만 바꾸고, Model export 이름과 Evidence 수치 계약은 유지한다. 상단에 다음 고정 문구를 추가한다.

```jsx
<p className="evidence-local-only" role="status">
  개발·검증 전용 · 사용자 제품 아님 · 외부 API와 상태 변경 없음
</p>
```

- [ ] **Step 4: 로컬 전용 Package와 명령을 연결한다**

```json
{
  "name": "@daon-user/evidence-hub",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": {
    "@daon-user/contracts": "0.0.0",
    "@daon-user/design-tokens": "0.0.0",
    "react": "19.2.7",
    "react-dom": "19.2.7"
  },
  "devDependencies": { "vite": "8.1.5" }
}
```

- [ ] **Step 5: 무외부효과 검증을 구현한다**

Evidence 앱 Source에서 `fetch`, `XMLHttpRequest`, Tauri `invoke`, `auth`, `recovery`, `upload`를 금지하고, `sessionStorage` 외 Storage 사용을 거부한다.

- [ ] **Step 6: GREEN과 독립 Build를 검증한다**

Run:

```powershell
node --test scripts/tests/evidence-hub-boundary.test.mjs scripts/tests/platform-prototype-evidence.test.mjs
npm run build --workspace @daon-user/evidence-hub
```

Expected: Evidence 계약 PASS, 독립 Vite Build PASS.

- [ ] **Step 7: Commit한다**

Commit message: `feat: isolate local evidence hub`

---

### Task 2: 제품 Bundle 경계 Gate 구현

**Files:**
- Create: `scripts/verify-product-ui-boundary.mjs`
- Create: `scripts/tests/product-ui-boundary.test.mjs`
- Modify: `package.json`
- Modify: `quality-gate-policy.json`
- Modify: `packages/ui/package.json`
- Modify: `apps/web/app/operations/recovery-workspace.jsx`
- Modify: `apps/web/components/notification-inbox-workspace.jsx`
- Modify: `scripts/build-local-service-sidecar.mjs`
- Modify: `scripts/run-isolated-desktop-cargo.mjs`

**Interfaces:**
- Consumes: Product Source roots `apps/web`, `apps/desktop`, 공용 제품 UI export.
- Produces: `npm run verify:product-ui-boundary`와 Build 후 Web·Desktop Bundle scan.

- [ ] **Step 1: 금지 Token RED를 작성한다**

```js
const forbidden = [
  "ProductionBoundEvidenceHub",
  "prototype_fixture",
  "deferred_actual",
  "Mock Adapter",
  "@daon-user/evidence-hub"
];
```

Test는 제품 Entry의 실제 Import graph를 재귀 추적해 공용 UI Source와 `apps/web/.next`, `apps/desktop/dist`의 텍스트 Asset을 검사하고, Evidence 앱 자체는 제외한다. Next/Vite Manifest가 참조하는 Route·Chunk·CSS가 하나라도 없거나 Symlink·부분 Build이면 fail-close다.

- [ ] **Step 2: 현재 제품 Source에서 RED를 확인한다**

Run: `node --test scripts/tests/product-ui-boundary.test.mjs`

Expected: Web Home, Desktop Home, Prototype 공용 Workspace import 때문에 FAIL.

- [ ] **Step 3: 검증기를 Root와 Quality Gate에 등록한다**

Root script:

```json
"verify:product-ui-boundary": "node scripts/verify-product-ui-boundary.mjs"
```

Desktop installer wrapper는 Frontend Build 직후, NSIS 실행 전에 Desktop `dist`를 검사한다. Web `build` Script는 Next Production Build 직후 `.next/static`, `.next/server/app` 사용자 Page tree와 `.next/server/chunks`를 검사한다. Server-only exact 예외는 `apps/web/app/bff/shell/runtime/route.js`, `apps/web/lib/web-shell-runtime.js` 및 대응 BFF `route.js`·`.map`·`.nft.json`만 허용한다.

- [ ] **Step 4: 검증기 자체 테스트를 통과시킨다**

Run: `node --test scripts/tests/product-ui-boundary.test.mjs`

Expected: Fixture 디렉터리의 각 금지 Token을 탐지하고 Exit 1, 깨끗한 Fixture는 Exit 0.

- [ ] **Step 5: Commit한다**

Commit message: `test: enforce product evidence boundary`

---

### Task 3: Web·Windows 사용자 진입 화면을 실제 제품 경계로 전환

**Files:**
- Modify: `apps/web/app/page.jsx`
- Modify: `apps/web/app/layout.jsx`
- Modify: `apps/web/lib/auth-pane.jsx`
- Modify: `apps/web/lib/question-answering-api.js`
- Create: `packages/ui/src/product-workspace-shell.jsx`
- Create: `packages/ui/src/product-workspace-model.js`
- Modify: `packages/ui/src/index.js`
- Modify: `apps/web/components/actual-workspace.jsx`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `apps/desktop/src/desktop-shell-model.js`
- Modify: `apps/desktop/src/desktop-shell.css`
- Modify: `apps/desktop/src/native-auth-panel.jsx`
- Modify: `scripts/tests/workspace.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`
- Modify: `scripts/tests/windows-recovery-adapter.test.mjs`

**Interfaces:**
- Consumes: Web Cookie 로그인, `NativeSessionBridge`, `ProductWorkspaceShell`의 `workspaceId`와 Adapter.
- Produces: Web 비인증 Login, Windows 비인증 전면 Login, 인증 후 기본 `WorkspaceDetail`, 권한 기반 Navigation.

- [ ] **Step 1: 사용자 진입 행동 RED를 작성한다**

Web `/`는 Evidence import가 없고 `AuthPane`만 렌더링해야 한다. Windows 실제 React Harness는 비인증 시 Navigation·Workspace DOM 0건, 인증 성공 시 `WorkspaceDetail`이 활성화되는지 검증한다.

- [ ] **Step 2: RED를 실행한다**

Run:

```powershell
node --test scripts/tests/workspace.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/windows-recovery-adapter.test.mjs
```

Expected: 기존 Home Evidence·전역 Login 때문에 FAIL.

- [ ] **Step 3: 공용 실제 Product Workspace Shell을 만든다**

```js
export const PRODUCT_WORKSPACE_STATES = Object.freeze([
  "loading", "empty", "ready", "error", "forbidden", "unavailable"
]);

export function createProductWorkspaceState() {
  return { status: "loading", sources: [], selectedSource: null, answer: null, studioOutputs: [], safeError: null };
}
```

Shell은 3면 Layout만 소유하고 Fixture ID·Prototype Action·가짜 성공 데이터를 만들지 않는다.

- [ ] **Step 4: Web Home을 인증 전용 화면으로 바꾼다**

`apps/web/app/page.jsx`는 `AuthPane`만 렌더링한다. 로그인 성공은 기존 `workspace_id`로 `/workspaces/{id}` 이동하고, Workspace가 없으면 `WORKSPACE_REQUIRED` Safe 상태를 표시한다. 기존 `ActualWorkspace`의 PDF Upload·Processing Status·Question·Citation 실제 연결은 Fixture 없는 Product Adapter로 보존하며 Stage A에서 제거하지 않는다. Source는 `source_state=ready`, `processing_state=ready`, `job_state=completed`가 모두 확인된 때만 질문 가능 상태로 전환한다. Question·Citation 응답은 exact DTO 검증 후 State에 반영하고 이상 응답은 render 밖에서 `QUESTION_RESPONSE_INVALID` Safe 상태로 전환한다.

- [ ] **Step 5: Windows Login을 독립 화면으로 바꾼다**

`DesktopShell`은 `nativeSession.authenticated === false`일 때 `NativeAuthPanel`만 렌더링한다. 인증 후 초기 `activeKey`는 `WorkspaceDetail`이며 `Home`을 Navigation allowlist에서 제거한다.

- [ ] **Step 6: 권한 없는 메뉴를 DOM과 Handler 양쪽에서 차단한다**

기본 Navigation은 Workspace, Notifications, Account다. Organization·Operations는 승인된 Projection이 있을 때만 목록에 포함하고, 직접 Route 요청도 `selectNativeRoute`에서 거부한다. Operations 권한 재조회 중에는 기존 검증 Projection을 유지하거나 갱신 결과를 원자 적용하여 승인 사용자의 현재 Route가 Workspace로 되돌아가지 않게 한다.

- [ ] **Step 7: GREEN과 Product Source Gate를 검증한다**

Run:

```powershell
node --test scripts/tests/workspace.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/windows-recovery-adapter.test.mjs
npm run verify:product-ui-boundary
```

Expected: 사용자 진입 계약 PASS, Product Source 금지 Token 0건.

- [ ] **Step 8: Commit한다**

Commit message: `feat: replace evidence home with user entry`

---

### Task 4: 공용 실제 Workspace Adapter 계약과 Web Source·질문·Citation 연결

**Files:**
- Create: `packages/ui/src/product-workspace-adapter.js`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `packages/ui/src/product-workspace-model.js`
- Create: `apps/web/lib/product-workspace-api.js`
- Modify: `apps/web/components/actual-workspace.jsx`
- Modify: `apps/web/lib/source-upload-api.js`
- Modify: `apps/web/lib/question-answering-api.js`
- Create: `scripts/tests/product-workspace.test.mjs`
- Modify: `scripts/tests/source-upload-api.test.mjs`
- Modify: `scripts/tests/question-answering-api.test.mjs`

**Interfaces:**
- Consumes: `workspaceId`, same-origin BFF Source upload/status, Question, Citation content.
- Produces: `ProductWorkspaceAdapter` with `listSources`, `uploadPdf`, `getProcessingStatus`, `askQuestion`, `citationUrl`, `createReport`, `listStudioOutputs`.

- [ ] **Step 1: Adapter exact interface RED를 작성한다**

```js
export function assertProductWorkspaceAdapter(adapter) {
  const methods = ["listSources", "uploadPdf", "getProcessingStatus", "askQuestion", "citationUrl", "createReport", "listStudioOutputs"];
  if (!adapter || methods.some((name) => typeof adapter[name] !== "function")) throw new Error("WORKSPACE_ADAPTER_INVALID");
  return adapter;
}
```

- [ ] **Step 2: 실제 UI 행동 RED를 작성한다**

Fake Adapter로 Source loading/empty/ready/error, PDF 처리 완료, 질문 insufficient/sufficient, Citation Link를 실제 React 렌더에서 검증한다. 실패 시 Fixture Source·답변·Citation이 생성되지 않아야 한다.

- [ ] **Step 3: RED를 실행한다**

Run: `node --test scripts/tests/product-workspace.test.mjs scripts/tests/source-upload-api.test.mjs scripts/tests/question-answering-api.test.mjs`

Expected: 공용 Adapter·실제 Product Shell 연결 부재로 FAIL.

- [ ] **Step 4: Web Adapter를 same-origin으로 구현한다**

`listSources`는 `GET /bff/api/workspaces/{workspaceId}/sources`, Upload·Status·Question은 기존 함수, Citation은 기존 상대 URL을 사용한다. 응답은 exact-key 검증 후 Safe DTO로 Projection한다.

- [ ] **Step 5: 3면 실제 상태 흐름을 연결한다**

왼쪽은 서버 Source만 표시하고, 가운데 질문은 ready SourceVersion 선택 전 비활성화한다. Citation 클릭은 `citationUrl`만 사용한다. 오른쪽 Studio는 Task 5 완료 전 `unavailable` Safe 상태이며 Fixture 버튼은 렌더링하지 않는다.

- [ ] **Step 6: GREEN을 검증한다**

Run: `node --test scripts/tests/product-workspace.test.mjs scripts/tests/source-upload-api.test.mjs scripts/tests/question-answering-api.test.mjs`

Expected: 실제 Adapter 호출 순서와 실패 무가짜데이터 계약 PASS.

- [ ] **Step 7: Commit한다**

Commit message: `feat: connect web product workspace`

---

### Task 5: 근거 기반 보고서 1종 Studio API·DB 수직 흐름 구현

**Files:**
- Create: `services/api/src/daon_user_api/studio_report.py`
- Create: `services/api/src/daon_user_api/studio_report_postgres.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `packages/contracts/openapi/v1/openapi.json`
- Modify: `scripts/verify-openapi-contract.mjs`
- Modify: `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- Create: `services/api/tests/test_studio_report_service.py`
- Create: `services/api/tests/test_studio_report_postgres.py`
- Create: `services/api/tests/test_studio_report_runtime_http.py`
- Modify: `scripts/tests/openapi-contract.test.mjs`

**Interfaces:**
- Consumes: 현재 Session Workspace, ready SourceVersion, 질문 `run_id`·`run_result_id`, Citation·EvidenceSpan, Canon tables `generation_settings_snapshots`, `generation_requests`, `studio_outputs`, `output_versions`, `evidence_references`.
- Produces: `POST /api/v1/workspaces/{id}/studio/reports`, `GET /api/v1/workspaces/{id}/studio/outputs`, exact `StudioReportCreateRequest`, `StudioOutputProjection`.

- [ ] **Step 1: Domain·Repository·HTTP RED를 작성한다**

Create 요청은 다음 exact shape만 허용한다.

```json
{
  "source_id": "source-id",
  "source_version_id": "source-version-id",
  "run_id": "run-id",
  "run_result_id": "run-result-id",
  "title": "보고서 제목",
  "purpose": "근거 기반 요약"
}
```

Citation 0건 또는 `insufficient=true`면 `EVIDENCE_REQUIRED`, 다른 Workspace/Source/Run 결속이면 `RESOURCE_UNAVAILABLE`, 재사용 Idempotency Key는 기존 결과를 반환해야 한다.

- [ ] **Step 2: RED를 실행한다**

Run:

```powershell
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_studio_report_service.py services/api/tests/test_studio_report_postgres.py services/api/tests/test_studio_report_runtime_http.py -q
node --test scripts/tests/openapi-contract.test.mjs
```

Expected: 모듈·Route·구체 Schema 부재로 FAIL.

- [ ] **Step 3: Domain Service를 최소 구현한다**

보고서 Type은 `evidence_report` 하나만 허용한다. 제목·요약·본문·결론과 Citation 목록을 생성하며 Provider 사용 시 `solar-pro4`를 Snapshot에 기록한다. Source/Citation 없는 생성은 수행하지 않는다.

- [ ] **Step 4: 기존 Canon tables에 Transaction으로 저장한다**

새 Migration을 만들지 않는다. 단일 Transaction에서 GenerationSettingsSnapshot → GenerationRequest → StudioOutput → OutputVersion → EvidenceReference → AuditEvent를 기록하고, Tenant·Workspace·RLS와 ID 결속을 검증한다.

- [ ] **Step 5: 구체 OpenAPI 계약과 Runtime Route를 연결한다**

기존 범용 `/api/v1/studio-*` Path는 호환 보존하되 새 사용자 수직 Route는 구체 Schema와 201/200/400/401/403/404/409/503/504 Safe 응답을 선언한다.

- [ ] **Step 6: GREEN과 전체 API 회귀를 검증한다**

Run:

```powershell
uv run --isolated --with pytest==9.0.3 pytest services/api/tests -q
node --test scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
```

Expected: 신규 Studio focused PASS, API 전체 PASS·기존 skip 수치 별도 기록, OpenAPI write/no-write 일치.

- [ ] **Step 7: Commit한다**

Commit message: `feat: add grounded studio report vertical`

---

### Task 6: Web Studio 실제 연결

**Files:**
- Modify: `apps/web/lib/product-workspace-api.js`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `packages/ui/src/product-workspace-model.js`
- Modify: `scripts/tests/product-workspace.test.mjs`
- Create: `scripts/tests/studio-report-api.test.mjs`

**Interfaces:**
- Consumes: Task 5의 `POST .../studio/reports`, `GET .../studio/outputs`.
- Produces: 오른쪽 Pane의 보고서 제목·목적 입력, 명시 생성, 저장 결과 목록, Citation 계보 표시.

- [ ] **Step 1: 실제 Studio UI RED를 작성한다**

Source·질문 성공 전 생성 버튼 disabled, sufficient 답변 후 1회 생성, 재클릭 Idempotency replay, API 실패 시 결과 0건을 실제 React Harness로 검증한다.

- [ ] **Step 2: RED를 실행한다**

Run: `node --test scripts/tests/product-workspace.test.mjs scripts/tests/studio-report-api.test.mjs`

Expected: Studio Adapter 연결 부재로 FAIL.

- [ ] **Step 3: same-origin Studio Client를 구현한다**

`POST /bff/api/workspaces/{workspaceId}/studio/reports`와 `GET /bff/api/workspaces/{workspaceId}/studio/outputs`만 사용하고 exact DTO를 검증한다.

- [ ] **Step 4: Product Studio Pane을 연결한다**

Prototype 5종 Tile·Fixture Review·Delivery·Registration은 제품 DOM에서 제거한다. 이번 단계는 `근거 기반 보고서` 1종 생성·저장·목록만 제공한다.

- [ ] **Step 5: GREEN과 Web Build를 검증한다**

Run:

```powershell
node --test scripts/tests/product-workspace.test.mjs scripts/tests/studio-report-api.test.mjs
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
```

Expected: Studio 수직 흐름 PASS, Web Product Bundle 금지 Token 0건.

- [ ] **Step 6: Commit한다**

Commit message: `feat: connect web grounded studio report`

---

### Task 7: Windows 전용 Workspace Tauri Command와 제품 연결

**Files:**
- Create: `apps/desktop/src-tauri/src/workspace_bridge.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Create: `apps/desktop/src-tauri/tests/workspace_bridge_contract.rs`
- Create: `apps/desktop/src/windows-workspace-adapter.js`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `scripts/run-isolated-desktop-cargo.mjs`
- Create: `scripts/tests/windows-workspace-adapter.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`

**Interfaces:**
- Consumes: Native Credential Vault의 Access, 고정 Gateway, Task 4·5 API 계약.
- Produces Tauri Commands: `workspace_list_sources`, `workspace_upload_pdf`, `workspace_processing_status`, `workspace_ask_question`, `workspace_citation_content`, `workspace_create_report`, `workspace_list_studio_outputs`.
- Produces JS: `WindowsWorkspaceAdapter` implementing `ProductWorkspaceAdapter`.

- [ ] **Step 1: 전용 Command Surface RED를 작성한다**

Node test는 정확한 7개 Command만 허용하고 `method`, `path`, `url`, `gateway`, `authorization`을 WebView 입력에서 거부한다. Rust test는 Session 없음 network 0, Workspace mismatch network 0을 검증한다.

- [ ] **Step 2: RED를 실행한다**

Run:

```powershell
node --test scripts/tests/windows-workspace-adapter.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
node scripts/run-isolated-desktop-cargo.mjs test
```

Expected: Command·Adapter 부재로 FAIL.

- [ ] **Step 3: Rust 고정 Transport를 구현한다**

기존 Native Session의 redirect none, timeout, 응답 128KiB 상한, Secret owner, exact status·Content-Type·DTO 검증을 재사용한다. Upload만 승인된 PDF bytes를 Tauri binary input으로 받고 filename·size·MIME를 엄격 검증한다.

- [ ] **Step 4: 7개 Command와 State를 등록한다**

각 Command는 명시 DTO만 받고 Vault Access를 Rust 내부에서 읽는다. 질문·보고서 POST는 Idempotency Key를 Rust에서 생성·유지하고, Write는 자동 재시도하지 않는다.

- [ ] **Step 5: Windows Adapter와 Product Shell을 연결한다**

`DesktopShell`은 인증 후 `WindowsWorkspaceAdapter` 한 인스턴스를 만들고 `ProductWorkspaceShell`에 주입한다. WebView Source에 `fetch`·Gateway·localhost 문자열이 없어야 한다.

- [ ] **Step 6: GREEN과 전체 Desktop 회귀를 검증한다**

Run:

```powershell
node --test scripts/tests/windows-workspace-adapter.test.mjs scripts/tests/desktop-tauri-shell.test.mjs scripts/tests/product-workspace.test.mjs
node scripts/run-isolated-desktop-cargo.mjs test
npm run verify:desktop-lint
npm run verify:desktop-build
npm run verify:product-ui-boundary
```

Expected: Node/Rust/Desktop Build PASS, Product Source·Bundle 금지 Token 0건.

- [ ] **Step 7: Commit한다**

Commit message: `feat: connect windows product workspace`

---

### Task 8: 실제 화면·배포 Gate와 D01 재개

**Files:**
- Create: `docs/03_evidence/release_1/R1-USER-PRODUCT-SEPARATION-01/manifest.json`
- Create: `docs/03_evidence/release_1/R1-USER-PRODUCT-SEPARATION-01/web-user-journey.md`
- Create: `docs/03_evidence/release_1/R1-USER-PRODUCT-SEPARATION-01/windows-user-journey.md`
- Create: `docs/03_evidence/release_1/R1-USER-PRODUCT-SEPARATION-01/evidence-hub-local-boundary.md`
- Create: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_completion_report.md`
- Modify: `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-01_progress.md`
- Modify: `docs/02_work_orders/release_1/R1-M5-07-WINDOWS-NATIVE-01-D01_deployment_work_order.md`

**Interfaces:**
- Consumes: Task 1~7 전체 자동 검증, Production Chrome 로그인 Session, 새 NSIS 설치 Artifact.
- Produces: Web·Windows 실제 사용자 흐름 증거, Evidence 로컬 전용 증거, D01 Go/No-Go 판단 자료.

- [ ] **Step 1: 전체 자동 회귀를 실행한다**

Run:

```powershell
npm run verify:evidence-hub
npm run verify:product-ui-boundary
npm run verify:workspace
npm run verify:desktop-unit
npm run verify:desktop-type
uv run --isolated --with pytest==9.0.3 pytest services/api/tests -q
node scripts/verify-openapi-contract.mjs
git diff --check
```

각 PASS·skip·선행 사용자 삭제 파생 실패를 별도로 기록한다.

- [ ] **Step 2: Production Web 실제 사용자 흐름을 검증한다**

Chrome에서 비인증 Login, 인증 후 Workspace, ready PDF Source 선택, 질문, page Citation, 근거 기반 보고서 생성·목록 누적을 확인한다. Network는 same-origin `/bff/api/...`만 허용하고 내부 URL 직접 호출 0건을 기록한다.

- [ ] **Step 3: 새 Windows NSIS를 Build·설치한다**

Run: `npm run build:desktop-installer`

Installer SHA256·size·서명 상태를 기록하고, 이전 설치 프로세스·Sidecar·Port 상태를 보호 확인한 뒤 current-user 범위에서만 설치한다.

- [ ] **Step 4: Windows 실제 사용자 흐름을 검증한다**

첫 화면은 전면 Native Login이어야 하며 Evidence Hub·개발 배지·Mock 문구는 0건이어야 한다. 로그인 후 Workspace 기본 진입, Source→질문→Citation→보고서 흐름을 수행하고 Tauri Command·Trace·Process·Port를 기록한다.

- [ ] **Step 5: Evidence Hub 로컬 경계를 검증한다**

`npm run dev:evidence-hub`는 `127.0.0.1`에서만 열리고 로그인 UI·Network 외부 요청·제품 Session Storage 공유·상태변경 0건이어야 한다.

- [ ] **Step 6: Evidence Manifest와 Completion을 검증한다**

Manifest JSON parse, 각 Evidence SHA256, Secret/Internal URL scan, `git diff --check`, 허용 경로, 사용자 삭제·미추적 보존을 확인한다.

- [ ] **Step 7: 독립 검토와 D01 Go/No-Go를 요청한다**

최신 설계·계획·최종 Diff·실제 화면 증거를 독립 검토자에게 전달한다. Critical/Important 0건과 실제 사용자 흐름 PASS가 모두 있어야 D01의 `HOLD_PRODUCT_UI_CORRECTION` 해제를 신산님에게 요청한다.

---

## Self-Review 결과

- Spec coverage: Evidence 분리, 무인증·무외부효과, Web/Windows 인증 진입, 3면 실제 Workspace, Source·질문·Citation·Studio 1종, Product Bundle 차단, 실제 Chrome·NSIS 검증을 Task 1~8에 모두 연결했다.
- Placeholder scan: 미정 값과 구현자 재량 문구를 제거하고 exact 파일, Route, Command, DTO, 실행 명령을 지정했다.
- Type consistency: `ProductWorkspaceAdapter` 7 Method와 Web·Windows 구현 이름, Studio Route·DTO, Tauri Command 7종을 Task 사이에서 동일하게 유지했다.
- 승인 경계: 사용자 삭제 4경로 복원, 공개 Studio Route 구체화, 신규 Tauri Command 7종, D01 배포는 각각 승인·검토 Gate에 배치했다.
