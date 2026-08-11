# R1-M5-07 Windows React Recovery Adapter C10-R02 수정 작업지시서

## 1. 판정과 기준

- Work Order ID: `R1-M5-07-WINDOWS-ADAPTER-C10-R02`; Issue ID: `R1-M5-07-WINDOWS-ADAPTER-C10-I001`.
- 상태: `READY` · 2026-08-11 · C10-R01 내부 독립 검토 Important 2건 보정.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; Branch·Worktree를 생성하지 않는다.
- 원 C10/C10-R01과 승인 C10B/R01 변경·보고를 보존하고 동일 어울2가 단일 Writer로 재작업한다.
- C10-R01 작업지시·Progress·Completion과 최신 검토 결과를 EOF까지 읽고 새 Progress에 근거를 기록한다.

## 2. 수정 목표

1. Windows Adapter Safe Trace는 Rust 정본과 동일한 정확한 32자리 소문자 hex만 허용한다.
2. 실제 React 렌더 경로에서 Login 실패·성공·권한 없음·Logout 경쟁의 UI/Invoke/Remount 계약을 검증한다.

## 3. 계약

### 3.1 Trace

- `trace_id`/`traceId`는 `^[0-9a-f]{32}$`만 UI Safe Trace로 투영한다.
- 허용 Error code라도 Credential 유사, 짧은 값, 대문자, 점·콜론·밑줄·하이픈 포함 Trace는 해당 Cloud/Local `*_RESPONSE_REJECTED`로 닫는다.
- 거부 Trace 원문은 Error message/property, UI, Log에 반사하지 않는다.

### 3.2 실제 React 행동 검증

- 새 의존성을 추가하지 않고 현재 Workspace의 React 테스트 가능 도구 또는 최소 실제 렌더 Harness를 사용한다. Regex만으로 완료하지 않는다.
- singleton fake Tauri invoke를 제품 Component Tree에 주입해 다음을 실행 검증한다.
  1. Login reject: Password DOM value 즉시 빈 값, Session unauthenticated, Recovery Command invoke 0건.
  2. Login success: Safe Session → Authorization exact7 → 새로운 `authorizationRevision` Tree/Key → Cloud list 정확히 1회.
  3. Authorization reject 또는 `[]`: 모든 Cloud Action의 버튼 click과 Handler 직접 경로에서 Recovery invoke 0건.
  4. Logout 시작 후 늦은 Poll·Authorization completion: 이전 Session·권한·결과 UI 재노출 0건, Recovery invoke 0건.
- 기존 Web bypass와 Local 3종 독립 동작 회귀를 보존한다.

## 4. 허용 변경 경로

- 원 C10-R01 허용 제품·테스트 경로 전체
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R02_progress.md` — 신규
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R02_completion_report.md` — 신규
- 원 C10/R01 Completion은 소급 수정하지 않는다.

API/OpenAPI/Rust/Cargo/Lock/DB/CSP/환경 설정과 새 의존성 추가는 금지한다.

## 5. TDD·검증

- RED: 허용 code+Credential 유사 Trace가 통과하는 현재 동작을 실제 Adapter에서 실패시킨다.
- RED: 제품 React Tree에서 위 4개 행동의 증거가 없거나 위반되는 상태를 실패시킨다.
- GREEN: Trace 검증 최소 수정과 필요한 테스트 가능 주입 경계만 보정한다. 제품 동작을 테스트 전용 구조로 대체하지 않는다.
- C10-R01 필수 명령을 fresh 재실행하고 실제 React 행동 테스트 건수, lint·workspace·diff·secret/network scan을 기록한다.
- C10B/R01과 사용자 삭제 31건·원 미추적 문서 3건을 보존한다.
- Commit·Push·배포·Browser·설치·실제 Login/Recovery는 수행하지 않는다.

## 6. 결과 계약

- Progress: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R02_progress.md`.
- Completion: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R02_completion_report.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
