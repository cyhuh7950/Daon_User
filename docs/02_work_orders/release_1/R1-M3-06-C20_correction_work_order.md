# R1-M3-06-C20 수정 작업지시서 — iOS Restore 결과 정규화와 시작 실패 증거 보존

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `21` |
| 사유 | 새 Process 경계를 고정한 C19 exact-SHA 실행에서도 초기 저장값이 비어 있어, Swift의 `nil` Promise 결과가 JavaScript Runtime에서 `undefined`로 전달될 수 있는 Adapter 경계를 정규화해야 함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-21.md` |

## 2. 기준 문서와 확인된 증거

- 승인 상세 설계서: `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` v0.7 전체
- 승인 작업계획서: `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` v0.9 전체
- 승인 테스트계획서: `docs/04_test_reports/release_1_test_plan.md` v0.7 전체
- exact Head `50ccf34ec9e53c9bf560e62408d6c52539f7049b`, Quality Gate Run `30245660436`은 SUCCESS다.
- iOS Phase A Run `30245660449`에서 unsigned Build와 UI Test 3개는 모두 PASS했다. 초기 Process 종료 후 새 Launch도 성공했으나 `wait_for_route Home`은 20회 동안 빈 값으로 종료됐다.
- Swift Promise Resolver의 `nil`은 TypeScript 선언과 달리 JavaScript Runtime에서 `undefined`가 될 수 있다. 현재 Adapter는 Native 결과를 그대로 반환하고 App은 `restoredRoute === null`일 때만 Home을 저장하므로 이 경계에서는 준비 신호가 실행되지 않는다.
- 현재 Script는 초기 Home 대기에서 Fail-fast 종료되어 해당 구간의 Unified Log가 Artifact에 남지 않는다.

## 3. 필수 작업

1. `apps/mobile/src/platform/ios-host.ts`의 Restore Adapter 경계에서 Native 반환값을 `await`하고, 문자열이 아닌 `null`·`undefined` Runtime 결과를 모두 `null`로 정규화한다.
2. `App.tsx`의 Listener 선설치, Null Restore의 Home 저장, 기존 저장 Route 복원 순서와 Android 경로는 변경하지 않는다.
3. `verify-simulator.sh`의 최초 `wait_for_route Home` 실패 시 Daon Process의 최근 Unified Log를 Evidence 디렉터리에 먼저 수집한 뒤 원래 실패 Exit를 유지한다. 성공 경로의 기존 최종 `simulator.log` 수집과 Crash/Secret 검사는 유지한다.
4. 실패 로그 수집은 exact Simulator의 Daon Process로 한정하고, Secret 값을 새로 출력하거나 Route 외 사용자 데이터를 기록하지 않는다.
5. 새 Native Method/API, 고정 Sleep·Wait 증가, 반복 횟수 완화, Simulator Erase/Shutdown, Product Route·Permission·Lifecycle·Signing 변경은 금지한다.

## 4. TDD와 완료 조건

- 구현 전 `undefined`·`null` 정규화와 초기 Home 실패 로그 보존 계약 RED
- 구현 후 Native 문자열은 보존하고 `undefined`·`null`은 `null`로 정규화하는 Adapter 계약 PASS
- `install → terminate → clear → launch → Home` 순서, Warm Deep Link 7종과 Rejected/Permission/Lifecycle/Crash/종료 검증 불변 PASS
- 초기 Home 실패 시 Log Artifact 생성 후 비정상 종료하고, 성공 시 기존 전체 검증을 계속하는 Fail-close 계약 PASS
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 iOS Adapter, Simulator 검증 Script, 관련 계약 Test, Progress와 Attempt 21 보고서뿐이다.
- 개인 절대경로·Generated Build/Pods/Gem/Test Temp·Signing Asset 잔존 0
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속이다.
