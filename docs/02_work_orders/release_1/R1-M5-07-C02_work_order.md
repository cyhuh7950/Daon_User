# R1-M5-07-C02 운영 화면 실제 Session Workspace 연결 보정 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M5-07-C02` |
| issue_id | `R1-M5-07-C02-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 성격 | R1-M5-07 실제 Web 증거 차단 결함 보정 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M5-07-C02_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M5-07-C02_completion_report.md` |

## 2. 승인 기준

다음 문서를 EOF까지 읽고 Hash·승인 상태·적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9
- `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.7
- `docs/04_test_reports/release_1_test_plan.md` 0.9
- `docs/02_work_orders/release_1/R1-M5-07_work_order.md`
- `docs/02_work_orders/release_1/R1-M5-07-WEB-EVIDENCE-01_work_order.md`
- `docs/04_test_reports/release_1/R1-M5-07-WEB-EVIDENCE-01_completion_report.md`
- `docs/03_evidence/release_1/R1-M5-07-WEB-EVIDENCE-01/manifest.json`
- `docs/02_work_orders/approvals/APR-CP3-PASS-GO-20260809-01.md`

## 3. 확인된 결함과 근거

- 로그인 후 `/operations`의 Backup 목록 자동 조회와 읽기 전용 새로고침은 `RESOURCE_UNAVAILABLE`을 반환했다.
- 화면 모델 `createOperationsRecoveryViewState()`는 기본 `workspaceId: "workspace-release-one"`을 가진다.
- 실제 Web Wrapper는 Session을 조회하지 않고 이 Prototype Workspace를 그대로 `recoveryApi.listBackups(workspaceId)`에 전달한다.
- API는 현재 Session의 실제 Workspace가 아닌 대상에 대해 존재를 숨기는 `RESOURCE_UNAVAILABLE`을 반환한다.
- ysna-server의 PostgreSQL `backup_records` 테이블, `daon_app` Role, Recovery RLS Context와 읽기 Query는 정상이다.

따라서 DB·Object Storage·Recovery Service 자체가 아니라 Web 운영 화면의 실제 Session Workspace 미연결이 직접 원인이다.

## 4. 목표

로그인된 Web Session을 same-origin 경로로 조회하고, 반환된 `user_id`·`tenant_id`·`workspace_id`를 운영 화면 상태에 주입한 뒤 실제 Workspace로 Backup 목록 GET을 수행한다. Session 확인 전에는 Prototype Workspace로 실제 API를 호출하지 않는다.

## 5. 구현 계약

1. Recovery Web Adapter 또는 전용 Session Adapter에 same-origin Session GET을 추가한다.
2. Browser 코드는 상대 경로만 사용하고 `localhost`, 내부 Host/Port, API 절대주소, `NEXT_PUBLIC_API_BASE_URL`을 사용하지 않는다.
3. `RecoveryOperationsWorkspace`는 Session 조회가 성공한 뒤에만 실제 Recovery Pane을 렌더링한다.
4. Session 응답의 비어 있지 않은 `user_id`·`tenant_id`·`workspace_id`를 검증한다. 실패하면 안전한 `AUTHENTICATION_REQUIRED` 또는 `RESOURCE_UNAVAILABLE` 상태를 표시하고 Fixture Workspace로 Fallback하지 않는다.
5. `OperationsRecoveryWorkspace`는 주입된 Session Context로 ViewState의 Actor·Tenant·Workspace와 Membership Scope를 구성한다.
6. Prototype 전용 내부 상태와 기존 재처리·복구 Preview 기능은 보존한다. 실제 Backup 생성·Restore·Update·Rollback을 실행하지 않는다.
7. 기존 Provider 설정, 로그인, Workspace, Recovery 7 Route와 BFF allowlist 동작을 변경하지 않는다.

## 6. TDD·검증

- 먼저 다음 RED 계약을 추가하고 실패를 기록한다.
  - 운영 Web Wrapper가 Session GET 후 실제 Workspace를 Pane에 전달한다.
  - Session 확인 전 Fixture Workspace로 Backup 목록을 호출하지 않는다.
  - ViewState는 주입된 Actor·Tenant·Workspace와 Membership Scope를 일치시킨다.
  - Session·Recovery 요청은 same-origin 상대 경로이며 내부주소 직접 호출이 없다.
- 최소 수정 후 관련 테스트를 GREEN으로 만든다.
- 최소 검증:
  - `node --test scripts/tests/operations-recovery.test.mjs scripts/tests/recovery-api.test.mjs`
  - 관련 Workspace/BFF 회귀 테스트
  - 변경 JS/JSX lint 또는 프로젝트 지정 정적 검사
  - `git diff --check`
- OneDrive 생성물 잠금 등으로 Build가 코드 컴파일 전에 실패하면 1회만 실행하고 환경 실패를 정확히 기록한다.
- 자동 테스트 통과는 실제 Browser PASS가 아니다. 배포 후 별도 읽기 전용 Browser Evidence 재검증이 필요하다.

## 7. 허용 변경 경로

- `apps/web/app/operations/recovery-workspace.jsx`
- `apps/web/lib/recovery-api.js`
- `packages/ui/src/operations-recovery-pane.jsx`
- `scripts/tests/operations-recovery.test.mjs`
- `scripts/tests/recovery-api.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-C02_progress.md`
- `docs/04_test_reports/release_1/R1-M5-07-C02_completion_report.md`

다른 제품·설계·계획·Migration·OpenAPI 파일은 수정하지 않는다. 허용 파일만으로 해결할 수 없으면 근거와 함께 어울1에게 되돌린다.

## 8. 보존 대상

- 사용자 삭제 표시 33건과 미추적 사용자 문서 3개
- 기존 DB·Object Storage·Backup·Restore 데이터
- 기존 인증·Provider·Workspace·BFF 기능
- 기존 R1-M5-07 및 Web Evidence Pack

## 9. 진행 기록

착수, RED, 최소 구현, 각 테스트, 오류·복구, 종료 직전에 다음 형식으로 기록한다.

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

## 10. 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

