# R1-M8-09 작업지시서 — NotebookLM형 Web Studio E2E

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-09` |
| issue_id | `R1-M8-09-I001` |
| 목표 | 승인된 NotebookLM 참고 3면 Workspace의 오른쪽 업무 Studio를 다섯 실제 산출물의 전체 Web 수명주기로 연결한다. |
| 선행조건 | `R1-M7-06`, `R1-M8-01~08` 내부 계약 완료 및 `R1-USER-PRODUCT-SEPARATION-STAGE-B-01` 실제 Source·질문·Citation·보고서 1종 수직 연결 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-09_progress.md` |
| 결과보고 | `docs/04_test_reports/release_1/R1-M8-09_completion_report.md` |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` · 단일 Writer |

착수 전에 `AGENTS.md`, 승인 설계 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md`, 승인 계획 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md`의 Global Constraints·M8·R1-WEB-02, 테스트 계획 `docs/04_test_reports/release_1_test_plan.md`의 TP-4·M8, `docs/04_test_reports/release_1/scenarios/04_studio.md`, 본 작업지시서와 실행 프롬프트를 EOF까지 읽고 Hash와 적용 범위를 Progress에 기록한다.

## 2. 사용자 완료 여정

Web 로그인 후 실제 Workspace에서 다음 흐름이 한 화면에서 동작해야 한다.

1. 왼쪽에서 실제 ready Source와 근거 범위를 선택한다.
2. 가운데 질문·Citation 결과 또는 선택 Source 범위를 Studio 생성 근거로 사용한다.
3. 오른쪽 Studio에서 `근거 기반 보고서`, `제약·준수 점검표`, `비교·데이터 표`, `지식 구조도`, `업무 문서 초안` 중 하나를 선택한다.
4. 유형 선택만으로 생성하지 않고 목적·독자·SourceVersion·RuleSet·분량/구성·출력 형식·검토 조건을 확인·확정한다.
5. 확정된 `GenerationSettingsSnapshot`으로 명시 생성하고 저장된 산출물·현재 Version·Citation/Evidence 계보를 확인한다.
6. 사용자 편집과 AI 재생성은 각각 새 불변 OutputVersion으로 남고 이전 Version과 변경 사유를 확인한다.
7. 검토 요청→수정 요청 또는 승인→재제출을 수행하며 승인 후 변경은 새 Version과 재승인을 강제한다.
8. 승인된 Version만 내보내기·전달할 수 있고, 별도 명시 동작과 Step-up 뒤에만 생산 지식 등록이 가능하다.

## 3. 제품 UI 계약

- 데스크톱 1920×1080의 `자료·지식 / 대화·실행 / 업무 Studio` 3면을 유지한다. 본문·폼 12px, 제목 16px, 설명은 상시 박스가 아니라 `i` Tooltip·Popover로 제공한다.
- 기존 `StudioWorkflowPane`과 `studio-workflow-model.js`의 Prototype Fixture를 제품 DOM이나 Product Bundle에 Import하지 않는다. 실제 DTO만 소비하는 Product 전용 Model·Pane을 분리한다.
- 초기 Studio는 산출물 유형 Tile, 저장된 산출물 목록, 선택 산출물 상세로 구성한다. 빈 상태·loading·error·forbidden·unavailable을 Safe 상태로 표시하고 Fixture 성공을 만들지 않는다.
- 생성 설정은 목적, 독자, 선택 SourceVersion, RuleSet Version 또는 없음, 분량, 유형별 구성, 출력 형식, 검토 조건을 포함한다. 강제 RuleSet·필수 검토·권위/가중치·데이터 영역·Egress 정책은 서버 Projection을 잠금 상태와 사유로 표시하며 완화 Handler를 제공하지 않는다.
- 긴 실행은 상태를 표시하고 중복 제출을 차단한다. 내부 Chain-of-Thought·Provider 비밀·내부 URL·Stack을 표시하지 않는다.
- 산출물 목록은 유형·제목·현재 Version·상태·경고를 표시하고, 상세는 Content·근거·설정 Snapshot·Version 이력·검토·승인·내보내기·등록 상태를 표시한다.
- 로그인·가입 UI, Source Upload/Processing, 질문/Citation의 기존 same-origin 의미와 DTO를 변경하지 않는다.

## 4. 실제 API·DB 계약

- Browser는 `/bff/api/...` same-origin 상대 경로만 호출한다. BFF는 승인된 `/api/v1/studio-generation-requests`, `/api/v1/studio-outputs`, `/api/v1/studio-outputs/{id}/versions`, `/api/v1/reviews`, `/api/v1/approval-requests`, `/api/v1/approvals`, `/api/v1/deliveries`, `/api/v1/knowledge-registrations` 의미만 전달한다.
- 기존 `/api/v1/workspaces/{id}/studio/reports`와 `/studio/outputs`는 호환 경계로 보존하되 새 제품 UI는 다섯 유형 공통 계약을 사용한다. 기존 보고서 DTO·Idempotency·Citation 결속을 깨지 않는다.
- 다섯 output type은 `evidence_report`, `compliance_checklist`, `comparison_table`, `knowledge_map`, `business_draft`로 고정한다.
- 생성 제출은 현재 Tenant·Workspace·Membership·SourceVersion·Run/Citation·RuleSet·정책을 다시 확인하고, Canon의 GenerationSettingsSnapshot → GenerationRequest → StudioOutput → OutputVersion → EvidenceReference → AuditEvent를 한 Transaction으로 기록한다.
- 저장된 OutputVersion은 수정하지 않는다. 편집·AI 재생성·설정 변경은 `previous_version_id`, `revision_type`, `change_reason`을 가진 새 Version으로만 기록한다.
- ReviewRequest·ApprovalRequest·Approval·Delivery·KnowledgeRegistration은 현재 AccessDecision을 다시 확인한다. 외부 전달·최종 승인·생산 지식 등록은 exact `actor + action + target + policy_version`의 단기 Step-up 없이는 쓰기 0건으로 거부한다.
- 승인 후 Content·Evidence·가중치·Model·RuleSet·설정이 달라지면 승인 상태를 승계하지 않는다. 자동 승인·자동 전달·Daon 자동 승격 경로를 만들지 않는다.
- 새 Migration은 만들지 않고 기존 `0003_data_canon_lineage.py`의 Canon tables·FK·RLS·Audit·Idempotency 계약을 사용한다. 실제 구조가 부족하면 제품 코드를 우회하지 말고 `FAILURE_REPORT`로 근거를 제출한다.

## 5. 실제 파일 계약

- 보고서·업무 문서 초안: DOCX·PDF.
- 점검표·비교표: XLSX·CSV·PDF.
- 지식 구조도: JSON·SVG·PNG·PDF.
- 파일은 메타 이름만 반환하지 않고 실제 bytes로 생성·저장·다운로드한다. 각 파일에는 Output Version, 생성 시각, 지식 범위, 허용된 근거 부록을 포함한다.
- 새 Runtime Dependency와 설정값은 임의로 추가하지 않는다. 표준 라이브러리와 기존 의존성으로 계약을 충족할 수 없으면 필요한 의존성·영향·대안을 증거로 보고하고 어울1 판단을 받는다.
- 다운로드는 현재 권한을 재확인하고 정확한 Media Type, 안전한 Filename, `nosniff`, `no-store`를 적용한다. 최대 크기와 Object checksum을 검증하고 HTML·내부 오류를 파일로 위장하지 않는다.

## 6. 허용 파일

- `packages/ui/src/product-workspace-model.js`
- `packages/ui/src/product-workspace-shell.jsx`
- 신규 `packages/ui/src/product-studio-model.js`
- 신규 `packages/ui/src/product-studio-pane.jsx`
- `packages/ui/src/workspace.css`
- `packages/ui/src/index.js`
- `apps/web/components/actual-workspace.jsx`
- `apps/web/lib/product-workspace-api.js`
- `apps/web/lib/bff-api-proxy.js`
- `services/api/src/daon_user_api/runtime.py`
- `services/api/src/daon_user_api/generation_settings.py`
- `services/api/src/daon_user_api/report_generation.py`
- `services/api/src/daon_user_api/compliance_check.py`
- `services/api/src/daon_user_api/comparison_table.py`
- `services/api/src/daon_user_api/knowledge_graph.py`
- `services/api/src/daon_user_api/document_draft.py`
- `services/api/src/daon_user_api/approval_workflow.py`
- `services/api/src/daon_user_api/knowledge_registration.py`
- `services/api/src/daon_user_api/studio_report.py`
- `services/api/src/daon_user_api/studio_report_postgres.py`
- 신규 `services/api/src/daon_user_api/studio_workspace.py`
- 신규 `services/api/src/daon_user_api/studio_workspace_postgres.py`
- 신규 `services/api/src/daon_user_api/studio_export.py`
- `packages/contracts/openapi/v1/openapi.json`
- `scripts/verify-openapi-contract.mjs`
- `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- `scripts/tests/product-workspace.test.mjs`
- `scripts/tests/studio-workflow.test.mjs`
- 신규 `scripts/tests/product-studio.test.mjs`
- `scripts/tests/api-bff-runtime.test.mjs`
- 신규 `services/api/tests/test_studio_workspace_service.py`
- 신규 `services/api/tests/test_studio_workspace_postgres.py`
- 신규 `services/api/tests/test_studio_workspace_runtime_http.py`
- 신규 `services/api/tests/test_studio_export.py`
- 본 Work Order·Prompt·Progress·Completion과 전용 Evidence

허용 파일 외 변경이 필요하면 수정하지 말고 정확한 파일·이유·계약 충돌을 어울1에게 보고한다. 기존 사용자 삭제·Cargo 표시·Native Evidence·인증 화면 변경과 관련 미추적 문서를 보존한다.

## 7. 금지

- Prototype Fixture·가짜 Source·가짜 산출물·가짜 승인·가짜 파일을 실제 제품 성공으로 표시
- 브라우저 절대 API URL, `localhost`, Docker 내부 주소, `NEXT_PUBLIC_API_BASE_URL`
- Password·Credential·Authorization·내부 URL·원문·Chain-of-Thought 기록
- API·DTO·DB·보안 의미를 테스트 편의를 위해 약화
- 기존 OutputVersion 수정, 자동 승인, 자동 Daon 승격, 승인 없는 외부 전달
- 관련 없는 리팩터링, 기존 보호 변경 복원·삭제·Stage
- 어울2의 Commit·Push·PR·배포·실제 Credential 입력

## 8. TDD 실행 순서

1. 현재 Git root·Branch·origin·HEAD·dirty·staged0와 보호 변경을 Progress에 기록한다.
2. Product Studio 실제 React 행동 RED를 작성한다. 초기 5 Tile, 설정 확인 전 호출 0, 잠금 해제 Handler 0, 생성·목록·상세·Version·검토·승인·내보내기·등록 흐름을 실제 DOM과 Adapter 호출로 검증한다.
3. Domain·PostgreSQL·Runtime HTTP·OpenAPI·BFF RED를 작성한다. 다섯 유형, Snapshot, Idempotency, Workspace/Tenant 결속, 불변 Version, 재승인, Step-up, 파일 bytes·Media Type을 검증한다.
4. focused RED를 실행해 미구현 계약 때문에 예상 실패하는지 확인하고 Progress에 실패 원인을 기록한다.
5. API·Repository를 최소 GREEN하고 focused API 테스트를 실행한다.
6. same-origin Web Adapter와 Product Studio Pane을 최소 GREEN하고 실제 React 테스트를 실행한다.
7. 실제 파일 생성·다운로드 테스트에서 DOCX/PDF/XLSX/CSV/JSON/SVG/PNG의 signature·구조·필수 메타·근거 부록을 검증한다.
8. 관련 전체 API·OpenAPI·Web·Product Boundary 회귀를 실행한다.
9. 실제 브라우저·ysna-server 배포는 코드·정적·자동 테스트·Build가 모두 통과하고 어울1 검토가 끝난 뒤 별도 Gate로 남긴다.

## 9. 필수 검증

```powershell
node --test scripts/tests/product-studio.test.mjs scripts/tests/product-workspace.test.mjs scripts/tests/studio-workflow.test.mjs scripts/tests/api-bff-runtime.test.mjs
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_studio_workspace_service.py services/api/tests/test_studio_workspace_postgres.py services/api/tests/test_studio_workspace_runtime_http.py services/api/tests/test_studio_export.py -q
uv run --isolated --with pytest==9.0.3 pytest services/api/tests -q
node --test scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
npm run lint --workspace @daon-user/web
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
git diff --check
git diff --cached --name-only
```

정적·자동 테스트·Build와 실제 Browser·파일 Open·운영 검증을 구분한다. 실제 Word·Excel·PDF·SVG/PNG Open과 Production Chrome R1-WEB-02는 자동 테스트만으로 PASS로 승격하지 않는다.

## 10. 완료 조건

- 다섯 Tile과 생성 설정 확인·확정이 실제 Product DOM에서 동작하고 선택 즉시 생성 호출은 0건이다.
- 다섯 유형의 실제 저장 Output·불변 Version·Evidence·Snapshot·Audit 계보가 존재한다.
- 편집·AI 재생성·설정 변경은 새 Version을 만들고 승인 후 변경은 재승인을 강제한다.
- 검토·승인·전달·생산 지식 등록은 역할·현재 권한·Step-up 계약을 지킨다.
- 각 허용 형식의 실제 파일 bytes·다운로드 계약과 부정 경로가 자동 검증된다.
- 기존 Source→질문→Citation→보고서 1종과 로그인·Workspace redirect 회귀가 통과한다.
- Product Bundle 금지 Token과 Browser 내부 주소 직접 호출이 0건이다.
- 실제 Browser·Office 파일 Open·ysna 배포가 미실행이면 `CONTRACT_COMPLETE / JOURNEY_UNVERIFIED`로 보고하며 `COMPLETED`나 TP-4 PASS로 승격하지 않는다.

## 11. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

Progress는 착수, RED, 각 GREEN, 오류·복구, 회귀, 종료 직전에 시각·단계·상태·변경 파일·명령/결과·오류/원인/복구·다음 작업을 기록한다.
