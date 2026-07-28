# R1-M3-06-C21 수정 작업지시서 — Warm Deep Link 실패 경계 진단 증거

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `22` |
| 사유 | C20에서 초기 Home 준비는 성공했으나 첫 Warm URL `WorkspaceList` 이후 저장 Route가 Home으로 유지되어 Native URL 수신과 JS 처리 사이의 정확한 실패 경계를 확인해야 함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-22.md` |

## 2. 기준 문서와 확인된 증거

- 승인 상세 설계서 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` v0.7, 작업계획서 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` v0.9, 테스트계획서 `docs/04_test_reports/release_1_test_plan.md` v0.7 전체
- exact Head `a407daab3b216088aa6836d49f05a8368ba2ee63`의 Quality Gate Run `30246916501`은 SUCCESS다.
- iOS Run `30246916481`은 Build와 UI Test 3개가 PASS했고 Simulator 검증에서 `Expected persisted route WorkspaceList, got Home`으로 종료했다.
- C20 정규화로 초기 Home 준비는 성공했으므로 Restore/새 Process 경계는 해소됐다.
- AppDelegate는 Warm URL 수신 시 `DAON_PENDING_DEEP_LINK_RECEIVED`, 저장 성공 시 Host가 `DAON_ROUTE_SAVED=<approved route>`를 남긴다. 현재 실패 시점에는 최종 로그 수집 전 종료되어 두 표식의 선후를 직접 확인할 증거가 없다.

## 3. 필수 작업

1. `verify-simulator.sh`에서 최초 Home과 각 Warm Route의 `wait_for_route` 실패를 하나의 Fail-close 진단 경계로 처리한다.
2. 실패한 기대 Route를 파일명 안전한 승인 Route 값으로 사용해 해당 구간의 exact Daon Unified Log를 Evidence에 남긴다.
3. GitHub Step Log에는 전체 Unified Log를 출력하지 말고 `DAON_PENDING_DEEP_LINK_RECEIVED`, `DAON_ROUTE_SAVED`, `DAON_LIFECYCLE_STATE` 표식과 기대/실제 승인 Route만 출력한다.
4. 원래 `wait_for_route` Exit를 그대로 유지하고, 성공 경로의 순서·Warm 7종·Rejected/Permission/Lifecycle·최종 Log/Crash/Secret/종료는 변경하지 않는다.
5. Product Source, Native/Bridge/AppDelegate, API, 고정 Sleep·Wait, 반복 횟수, URL 발송 방식, Signing은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 Warm Route 실패에 대한 Evidence·허용 표식 요약·원 Exit 계약 RED
- 구현 후 Home과 Warm Route 실패 Fixture가 각각 원 Exit, 기대/실제 Route, Evidence 파일, 허용 표식만 출력함을 검증
- 성공 Fixture는 기존 다음 Route로 계속 진행하고 추가 실패 Evidence를 만들지 않음
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Simulator Script, 관련 계약 Test, Progress와 Attempt 22 보고서뿐
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속

