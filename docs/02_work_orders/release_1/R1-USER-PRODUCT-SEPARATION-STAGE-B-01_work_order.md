# R1 사용자 제품 분리 Stage B 작업지시서

## 1. 목적

Stage A에서 분리한 사용자 Product Workspace에 실제 Source 목록과 근거 기반 보고서 Studio 1종을 수직 연결한다. 승인 계획 `docs/superpowers/plans/2026-08-11-evidence-hub-product-separation.md`의 Task 4~6만 수행한다.

## 2. 승인 경계

- 기준 Branch: `master`, 기준선은 착수 시 `origin/master`와 일치해야 한다.
- Evidence Hub, Windows Tauri Workspace Command, 관리자 `/operations`, 배포·Browser·Installer는 범위 밖이다.
- 새 DB Migration을 만들지 않고 기존 Canon tables와 RLS·Audit 계약을 사용한다.
- Browser Client는 same-origin `/bff/api/...`만 호출한다.
- 사용자 기존 삭제·Cargo 표시·Native Evidence·기존 미추적 문서를 보존한다.
- Commit·Push는 어울1이 수행한다.

## 3. 허용 파일

- `apps/web/lib/product-workspace-api.js`
- `apps/web/components/actual-workspace.jsx`
- `packages/ui/src/product-workspace-model.js`
- `packages/ui/src/product-workspace-shell.jsx`
- `services/api/src/daon_user_api/studio_report.py`
- `services/api/src/daon_user_api/studio_report_postgres.py`
- `services/api/src/daon_user_api/runtime.py`
- `packages/contracts/openapi/v1/openapi.json`
- `scripts/verify-openapi-contract.mjs`
- `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- `scripts/tests/product-workspace.test.mjs`
- `scripts/tests/source-upload-api.test.mjs`
- `scripts/tests/question-answering-api.test.mjs`
- `scripts/tests/studio-report-api.test.mjs`
- `scripts/tests/openapi-contract.test.mjs`
- `services/api/tests/test_studio_report_service.py`
- `services/api/tests/test_studio_report_postgres.py`
- `services/api/tests/test_studio_report_runtime_http.py`
- 본 작업의 Progress·Completion 문서

필요한 기존 파일이 실제 계약과 충돌하면 수정하지 말고 증거와 함께 어울1의 판단을 요청한다.

## 4. 구현 계약

### 4.1 Product Workspace Adapter

- exact 메서드: `listSources`, `uploadPdf`, `getProcessingStatus`, `askQuestion`, `citationUrl`, `createReport`, `listStudioOutputs`.
- Source 목록은 `GET /bff/api/workspaces/{workspaceId}/sources`를 사용하고 exact Safe DTO만 반환한다.
- 서버에 존재하는 Source만 표시하며 Fixture Source·답변·Citation·Studio 결과를 만들지 않는다.
- Stage A의 150초 전체 Deadline, 1초 Poll, Status 10초, AbortSignal·lineage·Question/Citation 검증을 보존한다.

### 4.2 Studio API·DB

- Route: `POST /api/v1/workspaces/{id}/studio/reports`, `GET /api/v1/workspaces/{id}/studio/outputs`.
- Create Request exact keys: `source_id`, `source_version_id`, `run_id`, `run_result_id`, `title`, `purpose`.
- 보고서 Type은 `evidence_report` 하나만 허용한다.
- Citation 0 또는 insufficient는 `EVIDENCE_REQUIRED`; Workspace·Source·Run 결속 불일치는 Safe fail-close한다.
- Idempotency replay는 Provider·DB 중복 생성 없이 기존 결과를 반환한다.
- 기존 Canon Transaction에서 GenerationSettingsSnapshot → GenerationRequest → StudioOutput → OutputVersion → EvidenceReference → AuditEvent를 원자 기록한다.
- Tenant·Workspace·RLS·Audit 실패는 성공으로 승격하지 않는다.

### 4.3 Web Studio

- sufficient 질문 결과와 ready SourceVersion 전에는 생성 버튼을 비활성화하고 Handler에서도 호출 0을 보장한다.
- 오른쪽 Pane은 제목·목적 입력, 명시 생성, 저장 결과 목록, Citation 계보만 제공한다.
- Prototype Tile·Fixture Review·Delivery·Registration을 제품 DOM에 넣지 않는다.
- `POST /bff/api/workspaces/{workspaceId}/studio/reports`, `GET /bff/api/workspaces/{workspaceId}/studio/outputs`만 사용한다.

## 5. TDD 순서

1. Product Adapter·actual React 행동 RED를 작성한다.
2. Source 목록 same-origin 연결을 최소 GREEN한다.
3. Studio Domain·Repository·HTTP·OpenAPI RED를 작성한다.
4. 기존 Canon transaction과 Runtime Route를 최소 GREEN한다.
5. Web Studio actual React RED→GREEN을 수행한다.
6. focused → API 전체 → OpenAPI write/no-write → Web Build → Product Gate 순서로 fresh 검증한다.

## 6. 필수 검증

```powershell
node --test scripts/tests/product-workspace.test.mjs scripts/tests/source-upload-api.test.mjs scripts/tests/question-answering-api.test.mjs scripts/tests/studio-report-api.test.mjs
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_studio_report_service.py services/api/tests/test_studio_report_postgres.py services/api/tests/test_studio_report_runtime_http.py -q
uv run --isolated --with pytest==9.0.3 pytest services/api/tests -q
node --test scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
git diff --check
```

실행 환경 차단은 제품 실패와 분리한다. 자동 테스트·Build를 Browser/Production PASS로 승격하지 않는다.

## 7. 완료 보고

`docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-STAGE-B-01_completion_report.md`에 판정, 변경 파일, RED→GREEN, 실행 명령·수치, 보존 상태, 미검증 범위와 다음 Stage C 판단을 기록한다.
