# R1 사용자 제품 분리 Stage B 완료 보고

## 판정

`COMPLETED` — 승인된 Stage B Task 4~6의 실제 Source 목록 → grounded Studio API/기존 Canon transaction → Web Studio 수직 흐름과 필수 자동 검증을 완료했다.

독립 검토의 `NEEDS_CHANGES` 5건도 동일 issue 재작업에서 RED→GREEN 및 fresh 전체 회귀로 해소했다.

최종 Important finding인 cross-actor/workspace 파생 Canon·Audit ID 충돌도 scope digest 재작업과 실행 fixture로 해소했다.

## 판단 이유

- Web Product Workspace Adapter는 exact 7메서드와 same-origin `/bff/api/...`만 사용한다.
- 서버 Source 목록만 표시하며 ready SourceVersion과 sufficient Citation 질문 결과 전에는 보고서 생성을 UI와 Handler 양쪽에서 차단한다.
- `evidence_report` 생성은 Source·Run·RunResult·Citation 결속을 확인하고, Citation 0건 또는 insufficient를 `EVIDENCE_REQUIRED`로 fail-close한다.
- 기존 Canon tables에 GenerationSettingsSnapshot(`solar-pro4`) → GenerationRequest → StudioOutput → OutputVersion → EvidenceReference → AuditEvent → Idempotency 결과를 단일 transaction으로 기록한다. 새 Migration은 없다.
- 실제 `0008_document_processing_queue.py`를 대조해 Source 목록의 durable queue를 `document_processing_jobs.state`로 사용하고 tenant/workspace join을 명시했다.
- OpenAPI exact 요청·응답·Safe 오류와 ETag를 검증했고, 제품 DOM/Bundle 경계 위반은 0건이다.
- 최신 ProcessingRun은 `created_at DESC, record_id DESC`로 결정하고 Queue Job은 같은 `processing_run_id`에 결속해 서로 다른 Run 상태의 ready 합성을 차단한다.
- Question Citation은 선택 Source/Version과 전부 exact 일치해야 하며, 불일치는 `QUESTION_RESPONSE_INVALID`와 Studio 호출 0으로 종료한다.
- normalized source/version/run/result/title/purpose fingerprint가 같을 때만 Idempotency Key를 재사용하고, 필드 변경 시 새 16~128자 Safe Key를 발급한다.
- replay는 transition·INSERT·Audit·generation provider 호출 0이며, Audit 실패는 transaction 전체 rollback으로 검증했다.
- Settings·GenerationRequest·StudioOutput·OutputVersion·EvidenceReference·Transition·Audit ID는 모두 normalized `tenant_id|workspace_id|actor_id|operation|idempotency_key` scope를 deterministic Safe digest 입력으로 사용한다. Idempotency lookup도 동일 5개 scope를 명시한다.

## 변경 결과

### Web·공용 UI

- `apps/web/lib/product-workspace-api.js`
- `apps/web/components/actual-workspace.jsx`
- `packages/ui/src/product-workspace-model.js`
- `packages/ui/src/product-workspace-shell.jsx`
- `scripts/tests/product-workspace.test.mjs`
- `scripts/tests/source-upload-api.test.mjs`
- `scripts/tests/studio-report-api.test.mjs`

### API·계약

- `services/api/src/daon_user_api/studio_report.py`
- `services/api/src/daon_user_api/studio_report_postgres.py`
- `services/api/src/daon_user_api/runtime.py`
- `services/api/tests/test_studio_report_service.py`
- `services/api/tests/test_studio_report_postgres.py`
- `services/api/tests/test_studio_report_runtime_http.py`
- `packages/contracts/openapi/v1/openapi.json`
- `scripts/verify-openapi-contract.mjs`
- `scripts/tests/openapi-contract.test.mjs`
- `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`

### 진행·완료 정본

- `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-STAGE-B-01_progress.md`
- `docs/04_test_reports/release_1/R1-USER-PRODUCT-SEPARATION-STAGE-B-01_completion_report.md`

## TDD RED → GREEN

- Task 4 RED: Product Adapter exact 계약과 실제 Source 목록 연결 부재를 확인했다. GREEN에서 exact Safe DTO, ready 선택, 기존 upload/status/question/citation 흐름을 연결했다.
- Task 5 RED: Studio Domain·Repository·Runtime Route와 구체 OpenAPI Path/Schema 부재를 확인했다. GREEN 후 실제 Migration 대조에서 큐 테이블명 회귀를 별도 테스트로 고정했다.
- Task 6 RED: 근거 충족 전 생성 차단과 Studio 생성·목록 Client 연결 부재를 확인했다. GREEN에서 보고서 입력·명시 생성·저장 목록·Citation 계보만 렌더했다.
- 독립 검토 Finding RED: 교차 Run 상태 합성, Citation lineage 불일치 통과, 변경 form key 재사용, 15자 key 허용, replay·rollback·HTTP negative 실행 근거 부재를 각각 재현했다.
- 독립 검토 Finding GREEN: 동일 Run SQL 결속, Citation exact gate, form fingerprint key, 16~128 경계, transaction/Runtime negative fixture로 고정했다.
- 최종 Important RED에서 같은 key를 다른 actor/workspace가 사용하면 파생 ID가 동일해지는 것을 재현했다. GREEN에서 세 scope의 Canon·Evidence·Audit ID가 모두 독립적이고, 동일 scope replay는 같은 ID와 부작용 0임을 실행 검증했다.

## 검증 결과

- Product Node focused: `14 passed, 0 failed`.
- Studio API focused: `13 passed, 8 warnings`.
- API 전체: `306 passed, 25 skipped, 27 warnings, 134 subtests passed`.
- OpenAPI Node: `17 passed, 0 failed`.
- OpenAPI 결정적 검증: `paths=70`, `operations=96`, `schemas=104`, `errors=31`, SHA-256 `A229ECD726855E4E838888E7F4E369623ED40255173FDAA99CB9BC618F3F7857`.
- Web build: Next compile, TypeScript, page data, static pages PASS.
- Web target boundary: `221` files, violation `0`, boundary error `0`.
- 전체 Product boundary: `232` files, violation `0`, boundary error `0`.
- `git diff --check`: exit `0` (기존/변경 파일의 LF→CRLF 경고만 존재).

작업지시의 repo-root `uv ... pytest services/api/tests...` 명령은 이 저장소의 `package=false` src-layout에서 `daon_user_api` import가 설정되지 않아 환경 차단됐다. 동일 isolated 의존성과 테스트 대상을 `services/api`에서 `src`를 명시해 실행한 위 결과로 제품 실패와 분리했다.

Web build의 최초 sandbox 실행은 `.next/trace-build` 쓰기 `EPERM`으로 2회 차단됐다. Daon Node 잔류 프로세스가 없고 ACL에 Modify가 있음을 확인한 뒤 승인된 sandbox 외 동일 build 명령으로 PASS했다.

## 보존 상태

- 정본: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, Branch `master`.
- HEAD와 `origin/master`: `aee0d7aab1d00e09d3b8caf46f6edd8a3d2442c1` 일치.
- Stage A exact Web 4경로 diff: 0건.
- 기존 사용자 삭제: 27건 유지.
- 기존 `apps/desktop/src-tauri/Cargo.toml` 표시, Native Evidence·보고서, 기존 미추적 3건 유지.
- Commit·Push·배포·Browser·Installer: 미실행.

## 미검증 범위

- 실제 Browser 클릭·Network, 실제 PostgreSQL/RLS transaction, 운영·배포 환경은 이번 Stage B에서 검증하지 않았다.
- API 전체의 25개 skip은 환경 의존 테스트이며 PASS로 승격하지 않는다.

## 조치

- 어울1이 허용 파일 diff와 본 근거를 검토해 Commit 여부를 판단한다.
- 실제 Browser/DB/Native 연결은 별도 승인된 Stage C에서 수행한다.
