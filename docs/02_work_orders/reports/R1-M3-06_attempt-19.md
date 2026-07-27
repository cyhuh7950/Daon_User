COMPLETED | R1-M3-06-I007 | C18 Simulator Deep Link JS 준비 동기화 | iOS Null Restore 기본 Home 저장과 Route Key 단독 초기화·Home 준비·Warm Deep Link 7종 계약·Progress·Attempt 19 변경 | iOS 34/34·Mobile 전체·Android 11/11·Node 297/297·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Simulator 전체 검증 미실행 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 19 결과보고

## 판정

C18 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. UI Test 뒤 남은 Route와 JS Restore·Deep Link 구독 준비 경쟁을 기존 `restoreNavigationRoute`·`saveNavigationRoute` 계약만으로 동기화했다. 새 공개 API·Native Method·Ready Preference·고정 Sleep·Wait 증가 없이 iOS Null Restore 1경로와 Simulator 검증 순서만 최소 수정했다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `e8c9b3dc312f786597b3ae2dbaf3be2c6fa0413f`의 Run `30242833791`에서 공통 Gate·Pods·Simulator·Build와 Route/Lifecycle/Settings UI Test 3개가 모두 성공했다.
- 후속 `verify-simulator.sh`는 직전 UI Test가 저장한 `Notifications` 상태에서 App Launch 직후 JS 준비 확인 없이 첫 `Home` URL을 전송했다.
- `wait_for_route Home`은 기존 20회 동안 `Notifications`를 유지해 `Expected persisted route Home, got Notifications`로 Fail-close 종료했고 후속 검증은 실행되지 않았다.
- 기존 App은 Restore 결과가 없는 기본 `Home`을 Host에 저장하지 않아, Shell Script가 기존 API로 Restore·Listener 준비 완료를 판별할 수 없었다.

## 조치

### 변경 범위

- `apps/mobile/src/App.tsx`
  - iOS Deep Link·Lifecycle Listener를 먼저 설치하고 Restore Promise를 실행.
  - Restore 결과를 기존 UI State에 그대로 전달하며, 결과가 `null`일 때만 기존 `saveIOSNavigationRoute("Home")`로 기본 Route 저장.
  - 기존 저장 Route가 있으면 덮어쓰지 않으며 Android Branch는 변경하지 않음.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - App 설치 뒤 Launch 전에 격리 Simulator App Container의 `native_route_key`만 `plutil -remove`.
  - Launch 뒤 기존 `wait_for_route Home`으로 JS 준비를 확인.
  - `Home`은 초기 Route로 합격 처리하고 WorkspaceList~AccountSettings 7종만 기존 `openurl`·`wait_for_route` Warm Loop로 검증.
- `scripts/tests/ios-native-shell.test.mjs`: Listener/Restore/Null 저장 순서, Key 단독 제거와 설치→초기화→Launch→Home 준비→Warm 7종 순서, Home URL 선전송 금지와 Wait 20회 유지 계약 추가.
- Progress와 본 Attempt 19 보고서.
- 미변경: Native/Bridge/Xcode Project, UI Test 3개, Mobile Shell, Android, Evidence/Workflow, Bundle ID·Deep Link Parser·Permission·Lifecycle·Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C18 RED | iOS 32/34 PASS·2 FAIL: Null Restore 기본 Home 저장과 Script Key 초기화·준비 순서 부재 재현 |
| C18 GREEN | 첫 실행 33/34는 Test Matcher의 `plutil` 인자 순서 불일치; 실제 명령에 정합화 후 iOS 34/34 PASS |
| App 준비 계약 | Deep Link·Lifecycle Listener가 Restore보다 먼저 설치되고 Null 결과만 Home 저장; 저장 Route·Android 덮어쓰기 0 |
| Script 준비 계약 | Install→`native_route_key` 단독 제거→Launch→Home 준비→Warm URL 7종; Home 선전송·전체 Preference 삭제·고정 Sleep/Wait 증가 0 |
| 기존 시나리오 | Route 8종, Rejected Link 5종, Permission GRANTED→DENIED→GRANTED, Lifecycle·Crash/Secret·종료 계약 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 34/34, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,145 bytes SHA-256 `5391D213589D09F8FFA91B6F76B878972D02DA3BF8663FDC462FD87706E8DE52` |
| 전체 Node | 297/297 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Native/Bridge/Project/UI Test/Workflow/Android/Lock/Pin Diff 0; 개인 절대경로 신규 0; Signing 0; Pods/Build/Artifact/Test Temp 잔존 0 |

### 오류·복구 근거

- RED 32/34는 승인 C18의 준비 신호 부재와 Script 순서 문제를 재현한 결과다.
- 첫 GREEN의 33/34는 구현 오류가 아니라 Test Regex가 실제 `plutil -remove key plist` 인자 순서를 반대로 기대한 오류였다. Matcher를 실제 Syntax와 Key·Plist 경로에 맞춰 수정한 뒤 34/34와 전체 회귀를 재검증했다.
- Windows에서는 실제 Simulator Container·JS Bundle 준비와 8 Route 전체 실행을 검증할 수 없으므로 macOS 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 UI Test 3개와 후속 Simulator Verification 전체를 판정한다.
3. Artifact에서 `native_route_key` 단독 초기화 뒤 Home 준비, Warm Route 7종, Rejected Link·Permission·Lifecycle·Crash/Secret·종료 성공을 확인한다.
4. 성공 Manifest와 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
