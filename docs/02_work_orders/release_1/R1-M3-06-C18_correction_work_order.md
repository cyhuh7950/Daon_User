# R1-M3-06-C18 수정 작업지시서 — Simulator Deep Link 검증의 JS 준비 동기화

## 1. 판정

| 항목 | 값 |
| --- | --- |
| issue_id | `R1-M3-06-I007` |
| Attempt | `19` |
| 사유 | UI Test 3개 PASS 뒤 Shell 검증이 직전 저장 Route `Notifications` 상태에서 앱을 실행하자마자 첫 `Home` URL을 보내 JS 구독 준비를 확인하지 못하고 `Notifications`로 만료됨 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-19.md` |

## 2. 확인된 증거

- exact Head `e8c9b3dc312f786597b3ae2dbaf3be2c6fa0413f`, Run `30242833791`에서 공통 Gate·Pods·Simulator·Build와 Route/Lifecycle/Settings UI Test 3개가 SUCCESS다.
- 후속 `verify-simulator.sh`는 앱 Launch PID를 받은 직후 별도 Shell 준비 확인 없이 `Home` URL을 전송했다.
- 검증 시작 Route Key는 직전 UI Test가 보존한 `Notifications`이며, `wait_for_route Home` 20회가 `Notifications` 그대로 만료됐다. 이후 검증은 Fail-close로 중단됐다.
- 현재 App은 비동기 `restoreIOSNavigationRoute()` 결과를 UI State에만 전달하며, 저장 Route가 없는 최초 기본 `Home` 상태는 Host에 저장하지 않는다. 따라서 Shell 검증이 기존 API만으로 JS Restore·구독 완료를 판별할 준비 신호가 없다.
- 새 공개 API·Native Method·고정 Sleep 없이 기존 `restoreNavigationRoute`와 `saveNavigationRoute`를 이용해 준비를 동기화할 수 있다.

## 3. 필수 작업

### A. 기존 Route 계약으로 JS 준비를 증명한다

1. iOS App의 Restore Promise가 완료된 뒤 저장 Route가 없으면 승인 기본 Route `Home`을 기존 `saveIOSNavigationRoute`로 저장한다.
2. Restore 결과가 존재하면 그 값을 덮어쓰지 않고 기존 복원 동작을 유지한다.
3. Deep Link·Lifecycle Listener 설치와 Restore Callback의 실행 순서를 검토해, 기본 Home 저장 시점이 구독 설치 이후임을 계약 Test로 고정한다.
4. 새 Native Method, 공개 API, 별도 Ready Preference, Secret/환경값과 Android 동작 변경은 금지한다.

### B. Simulator 검증을 결정적으로 초기화한다

1. App 설치 뒤 Launch 전에 해당 격리 Simulator App Container의 `native_route_key`만 제거한다. 다른 Preference·권한·데이터를 삭제하지 않는다.
2. 앱 Launch 후 기존 `wait_for_route Home`으로 JS Restore 완료와 기본 Route 저장을 확인한 뒤 Approved Deep Link Loop를 시작한다.
3. Home은 초기 Route 합격으로 검증하고, 이후 WorkspaceList~AccountSettings의 Warm Deep Link 7종은 기존 `openurl`·`wait_for_route`로 검증한다.
4. Rejected Link·권한 GRANTED→DENIED→GRANTED·Lifecycle·Crash/Secret·종료 검증과 Fail-close 의미는 유지한다.
5. 고정 Sleep을 준비 조건으로 추가하거나 기존 Wait 횟수를 늘리지 않는다.

## 4. TDD와 완료 조건

- 구현 전 App의 Null Restore 기본 Home 저장과 Script의 Key 단독 초기화→Home 준비 확인→Warm URL 순서 계약 RED
- 구현 후 Mobile/iOS·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 기존 저장 Route 복원, UI Test 3개, Route 8종, Rejected Link, Permission 3단계, Lifecycle 계약 완화 0
- Product 변경은 iOS Null Restore 시 기본 Home 저장 1경로뿐; Native/Bridge/Project/Signing 변경 0
- 개인 절대경로·Generated Build/Pods/Gem/Test Temp·Signing Asset 잔존 0
- Progress·Attempt 19에 Run 실패 원문, 준비 경쟁, RED→GREEN과 macOS 재검증 필요를 기록
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속

