COMPLETED | R1-M3-06-I007 | C25 Notification 실제 시스템 권한 3단계 검증 구현 | System Alert 직접 승인·Production Settings OFF/ON·Phase 결속·계약 Test·Progress·Attempt 26 | iOS 40/40·Mobile 전체·Android 11/11·Node 303/303·Toolchain·Workflow/Bash·Diff PASS | macOS Xcode 26.6 실제 Alert·Settings Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 26 결과보고

## 판정

C25 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. iOS 26.6 Simulator가 지원하지 않는 notification `simctl privacy` 호출만 제거하고, 동일 설치 상태에서 Production 권한 요청과 공개 Settings 이동을 사용해 Notification `grant-initial → revoke → grant-again`을 실제 시스템 UI로 검증하도록 했다. MAIN 검토에서 확인한 비동기 Alert 생성 Race는 exact 제목 대기 완료 후 Alert 1건을 검사하도록 순서만 보정했다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `e5422934d52328597e8071105d549a032ef618b8`의 Quality Gate Run `30254672711`은 성공했다.
- iOS Run `30254672664`은 unsigned Build와 System Open UI Test가 성공했고 첫 Permission Phase에서 camera·microphone 설정 뒤 notification 서비스만 `Operation not permitted`, Exit 1로 실패했다.
- camera·microphone의 기존 `simctl privacy` 전환은 실제 성공했으므로 유지하고, Notification만 Apple System Alert와 공개 Settings App UI를 직접 검증하는 것이 C25 승인 경계다.
- 재설치나 Private URL·Defaults/TCC DB·좌표/Index 우회 없이 Production 버튼과 exact 접근성 Label allowlist를 사용해야 동일 설치의 실제 OS 상태 전환을 보존한다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - notification `simctl privacy` 호출과 해당 현재 서비스 할당만 제거.
  - camera→microphone 기존 순서·action·Fail-close 동작 유지.
  - `grant-initial|revoke|grant-again` 외 Phase를 Exit 64로 거부.
  - `DAON_PERMISSION_PHASE`와 기존 `DAON_PERMISSION_EXPECTED`를 각 XCTest에 함께 전달.
  - 기존 3개 `permission-<phase>.xcresult`, 단일 App Install과 EXIT Cleanup 유지.
- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - Phase 미지정·미허용 및 Phase/Expected 불일치를 Fail-close.
  - `grant-initial`: Production notification 요청 뒤 SpringBoard Notification Alert 1건과 exact 제목 allowlist를 확인하고 exact `Allow`/`허용` 버튼을 직접 탭한 뒤 Alert 닫힘 확인.
  - MAIN 검토 보정: notification 버튼 탭 직후 Alert 개수를 검사하지 않고, 제목 exact allowlist의 `waitForExistence`가 성공한 뒤 Alert 1건과 Allow 버튼을 순서대로 검증.
  - `revoke`·`grant-again`: Production `앱 권한 설정 열기`를 탭해 Settings App의 exact `Notifications|알림` Row와 `Allow Notifications|알림 허용` Switch를 확인하고 각각 OFF/ON 전환.
  - Switch 전 상태를 읽고 목표와 다를 때만 탭하며, 목표값 도달·최종값·Daon 복귀 `runningForeground`와 Root Ready를 확인.
  - 세 Phase 모두 camera·microphone·notification Production 버튼과 UI `GRANTED`/`DENIED` 결과를 검증.
- `scripts/tests/ios-native-shell.test.mjs`
  - notification privacy 0, camera/microphone 각 1회·순서, Phase 결속, System Alert/Settings/Switch/복귀, 단일 Install과 금지 방식 0 계약.
  - 기존 Scroll 계약에 승인된 Production Settings 탐색 Loop 1건 반영.
- Progress와 본 Attempt 26 보고서.
- 미변경: Product Native Host·Bridge·권한 결과 매핑·Settings API, Deep Link, Lifecycle, Workflow/Runner, Android, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C25 RED | iOS 38/40 PASS·2 FAIL: notification privacy 잔존과 Phase/System UI 계약 부재 재현 |
| 첫 GREEN 복구 | iOS 39/40: C25 핵심 계약 PASS, 기존 Scroll Loop 기대 수가 새 승인 Loop를 누락한 Test 오탐 1건 |
| C25 GREEN | Scroll 계약 기대 수만 정합화 후 iOS 40/40 PASS |
| MAIN 검토 보정 RED | 관련 계약 32/33 PASS·1 FAIL: 제목 대기 전에 Alert count를 검사하던 Race 순서 재현 |
| MAIN 검토 보정 GREEN | 제목 exact allowlist 대기→Alert 1건→Allow lookup 순서 보정 후 관련 계약 33/33·iOS Gate 40/40 PASS |
| Notification System UI | expected Alert 1건·제목 allowlist·Allow 버튼·Alert 닫힘, Settings Row·Switch 전후값·OFF/ON·앱 복귀 계약 PASS |
| 동일 설치·금지 경계 | App Install 정확 1회, uninstall/erase·Private URL·Defaults/TCC·좌표·firstMatch·element index 0 PASS |
| Camera·Microphone | `simctl privacy` 각 1회, camera→microphone 순서와 기존 action 유지 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 40/40, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C24와 동일 |
| 전체 Node | 303/303 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Product/Native/Bridge/Info/Project/Workflow/Android/Lock/Pin Diff 0; Signing 변경 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- RED 38/40은 승인 C25 경계를 재현한 예상 실패이며 나머지 38개 계약은 통과했다.
- 첫 GREEN의 39/40은 새 Production Settings Button 탐색으로 고정 Scroll Loop가 3건에서 4건이 된 실제 Source를 기존 Test가 누락한 오탐이었다. 기능 변경 없이 기대 수만 정합화해 40/40을 확인했다.
- MAIN 검토 보정 RED 32/33은 비동기 Alert 생성 전에 count를 즉시 검사하던 순서 문제를 정확히 재현했다. 기존 exact 제목 조회가 가진 `waitForExistence`를 먼저 완료하도록 두 문장만 이동했으며 Selector·timeout·Allow 처리·Settings/Phase 동작은 바꾸지 않았다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 Exit 0이고 관련 파일 변경은 없다.
- Windows Portable 검증은 Swift/Xcode Compile과 실제 iOS 26.6 System Alert·Settings Runtime을 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행한다.
3. grant-initial Alert 직접 승인, revoke OFF, grant-again ON과 세 Phase Production 결과 및 xcresult를 확인한다.
4. Alert/Settings exact 접근성 Label이 Runner 실제 언어와 일치하지 않으면 Artifact의 실제 Label 증거를 기준으로 allowlist 보완 여부를 어울1이 판단한다. 좌표·Index·Private URL·재설치로 우회하지 않는다.
5. Simulator Verification과 Evidence Manifest까지 성공하면 Phase A를 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 판정한다.
6. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
