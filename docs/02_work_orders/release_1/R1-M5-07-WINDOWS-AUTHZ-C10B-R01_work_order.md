# R1-M5-07 Windows Recovery 권한 Projection C10B-R01 수정 작업지시서

## 1. 판정과 기준

- Work Order ID: `R1-M5-07-WINDOWS-AUTHZ-C10B-R01`; Issue ID: `R1-M5-07-WINDOWS-AUTHZ-C10B-I001`.
- 상태: `READY` · 2026-08-11 · C10B 내부 독립 검토 Important 3건 보정.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; Branch·Worktree를 생성하지 않는다.
- 원 C10B 구현·Progress·Completion과 C10 미커밋 변경을 보존하고 동일 어울2가 단일 Writer로 재작업한다.
- C10B 작업지시, 설계 1.3, Plan Task 4.6, 최신 검토 결과를 EOF까지 읽고 새 Progress에 근거를 기록한다.

## 2. 수정 목표

1. Rust가 `recovery_operations == []` 또는 설계의 정확한 정렬 7종 전체만 허용하게 한다. 1~6개 부분집합은 Fail-close한다.
2. 고정 `GET /api/v1/session` 권한 조회는 HTTP `200`만 성공으로 허용한다. 동일 Safe body의 201·202·206은 실패하며 과거 성공을 재사용하지 않는다.
3. `/api/v1/session` OpenAPI에 기존 `ServiceUnavailable` 503 응답을 추가하고 실제 `AUDIT_WRITE_FAILED`·저장소 unavailable 보존 계약과 함께 고정한다.

## 3. 허용 변경 경로

- 원 C10B 작업지시의 허용 경로 전체
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B-R01_progress.md` — 신규
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B-R01_completion_report.md` — 신규
- 원 C10B Completion은 소급 수정하지 않는다.

새 Endpoint·Role·Permission·의존성·DB Migration·Vault Schema·React 변경은 금지한다.

## 4. TDD·검증

- RED: 실제 Wire/Runtime에서 정렬된 부분집합 1개 이상이 성공하는 현재 동작을 실패로 고정한다.
- RED: 같은 Safe body의 201·202·206이 성공하는 현재 동작과 과거 Projection 재사용 가능성을 실패로 고정한다.
- RED: API 503 행동은 존재하지만 OpenAPI Session 503이 없는 불일치를 고정한다.
- GREEN: 최소 검증과 OpenAPI/Verifier/Summary만 보정한다.
- 원 C10B 필수 명령을 fresh 재실행하고 Rust 전체 회귀 수치, API·Node·OpenAPI no-write, lint·rustfmt·diff·secret·gen/process 결과를 기록한다.
- C10 미커밋 변경, 사용자 삭제 31건과 원 미추적 문서 3건을 보존한다.
- Commit·Push·배포·Browser·실제 Login/Recovery는 수행하지 않는다.

## 5. 결과 계약

- Progress: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B-R01_progress.md`.
- Completion: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B-R01_completion_report.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
