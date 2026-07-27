COMPLETED | R1-M3-06-I007 | C22 Apple System Open XCUITest Deep Link 검증 전환 | Warm 7·Rejected 5 단일 Session UI Test·Runner 결속·Shell openurl 제거·Progress·Attempt 23 생성 | iOS 38/38·Mobile 전체·Android 11/11·Node 301/301·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Compile·Simulator Runtime 미실행 | 어울1의 Commit·Push와 macOS CI·xcresult 판정

# R1-M3-06 Attempt 23 결과보고

## 판정

C22 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Apple WWDC25의 공식 Custom URL Scheme UI 자동화 방식인 `XCUIDevice.shared.system.open(customURL)`을 적용해 승인 Warm URL 7종과 비정상 URL 5종을 한 App Session의 실제 foreground·Root·화면으로 검증하도록 전환했다. 비작동이 확인된 Shell `simctl openurl` Loop만 제거하고 후반 Permission·Lifecycle은 초기화된 승인 `Home` Route의 저장·복원을 검증하도록 정합화했다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `2ba7f772a7831a686caad40ed3c8a7f54072737e`의 Quality Gate Run `30248902466`은 성공했다.
- iOS Run `30248902445`은 Build와 기존 UI Test 3개가 PASS했으나 Shell `simctl openurl` 뒤 기대 `WorkspaceList`·실제 `Home`으로 실패했고 `DAON_PENDING_DEEP_LINK_RECEIVED`가 0건이었다.
- Product AppDelegate의 React Native Linking 계약은 정상이며, Apple 공식 [WWDC25 UI automation 예제](https://developer.apple.com/videos/play/wwdc2025/344/)는 Custom URL을 `XCUIDevice.shared.system.open`으로 열고 대상 App의 `runningForeground`와 화면을 확인한다.
- XCUITest는 App 내부 Test Hook 없이 OS→AppDelegate→React Native→공용 화면 전체 경계를 검증하므로 승인된 Product/API 계약을 바꾸지 않는다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `testSystemOpenDeepLinksPreserveForegroundAndRoute` 독립 Test 추가.
  - App 1회 Launch 뒤 승인 Warm URL `WorkspaceList`→`AccountSettings` 7종을 순차 System Open하고 매번 `runningForeground`, 공용 Root와 해당 Route 제목을 Fail-close 확인.
  - 마지막 `AccountSettings`를 유지한 상태에서 기존 비정상 URL 5종을 System Open하고 매번 foreground·Root·`AccountSettings` 화면 불변 확인.
  - iOS 26 미만 Runtime은 `XCTFail`로 종료해 지원 미충족을 성공으로 처리하지 않음.
- `apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh`
  - 새 Test를 선행 UI Test Step의 explicit `only-testing` 목록에 추가. 기존 XCTest Exit·xcresult·Unified Log·Crash 진단 결속 유지.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - Warm 7종과 Rejected 5종의 `simctl openurl` Loop만 제거.
  - 설치→초기 종료→Route Key 초기화→Launch→Home 준비, Permission 3단계 뒤 Lifecycle 재실행의 Home 복원, 최종 Log·Crash/Secret·Binary·종료 검증 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 정확 URL 12종 1:1, App Launch 1회, 두 System Open Loop, foreground·Root·승인/불변 화면 Assert, iOS 26 Fail-close Guard와 Runner 포함 계약.
  - Shell openurl 부재와 Home 준비·복원, 기존 Wait 20회·Permission·Lifecycle·Crash/Secret·종료 계약.
- Progress와 본 Attempt 23 보고서.
- 미변경: Product Source, AppDelegate, Native/Bridge, Info.plist, Xcode Project, 공개 API, Android, Workflow/Evidence Writer, Signing, Wait/Retry, 권한 동작, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C22 RED | iOS 33/38 PASS·5 FAIL: 새 XCUITest/Runner 부재와 Shell openurl Loop·AccountSettings 복원 잔존 재현 |
| 첫 GREEN | iOS 37/38 PASS·1 Test Matcher 오탐: 새 함수부터 EOF까지 잘라 후속 Permission Launch 2건을 포함 |
| Matcher 복구 | 함수 범위를 다음 Test 전까지 한정, 기능 코드 추가 변경 없이 iOS 38/38 PASS |
| System Open 계약 | 정확 URL 12종, Warm 7·Rejected 5, App Launch 1회, System Open Loop 2개, 매회 foreground·Root·화면 Assert, iOS 26 미만 Fail-close PASS |
| Runner·실패 연결 | 새 Test explicit 포함, 기존 원 XCTest Exit 65 보존·진단 Fixture PASS |
| Shell 경계 | `simctl openurl` 0, Home 준비·복원 2회, Wait 20회 1개, Permission 3단계·Lifecycle·Crash/Secret·Binary·종료 유지 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 38/38, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C21과 동일 |
| 전체 Node | 301/301 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Product/AppDelegate/Native/Bridge/Info/Project/API/Workflow/Evidence/Android/Lock/Pin Diff 0; Signing 변경 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- RED 33/38은 승인 C22의 XCUITest 전환 부재를 재현한 예상 실패이며 기존 iOS/Evidence/Deep Link 계약 33개는 통과했다.
- 첫 GREEN의 37/38은 실제 단일 Session 위반이 아니라 계약 Test가 새 함수부터 파일 EOF까지 포함한 Matcher 범위 오류였다. 다음 Test 선언 전까지로 범위를 제한해 실제 새 Test Launch 1회를 검증했다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 Exit 0이고 관련 파일 변경은 없다.
- Windows에서는 Swift Compile, Xcode 26.6 API Availability와 실제 Simulator System Open 동작을 실행할 수 없으므로 macOS 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 Xcode 26.6·가용 최신 iOS 26 Simulator Workflow를 실행한다.
3. 새 UI Test가 Compile되고 승인 7종·비정상 5종을 단일 Session에서 모두 통과하는지 xcresult·Video/Attachment·Unified Log로 판정한다.
4. Permission 3단계, Home Lifecycle 복원과 후속 Simulator Verification·Evidence Manifest까지 모두 성공해야 Phase A를 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 판정할 수 있다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
