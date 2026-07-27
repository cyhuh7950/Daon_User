COMPLETED | R1-M3-06-I006 | C16 iOS ScrollView 자동화 식별자 결속 | 공용 Navigation·화면 내용 ScrollView testID 2개와 Mobile/iOS 계약 Test·Progress·Attempt 17 변경 | Mobile Unit 10/10·iOS 32/32·Android 11/11·Mobile 전체·Node 295/295·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Route·Lifecycle XCTest 미검증 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 17 결과보고

## 판정

C16 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. C15로 앱 시작 Crash가 해소된 뒤 확인된 iOS XCUI ScrollView Identifier 미결속만 최소 수정했다. 기존 XCTest Query·Swipe·Retry 횟수와 Route/Lifecycle/Permission 시나리오는 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `90c86741befe712c71b0b3f1d9407aa18770bb76`, merge candidate `3b3cfaca75ae45c6339ab5fca7b9185bb34f0d28`의 Run `30240374452`, Artifact `8643271629`에서 Objective-C Bridge·Swift Compile·unsigned Build가 성공했다.
- 이전 `undefined is not a function`, RCTFatalException, SIGABRT와 Daon Crash Report는 0건이며 Root Shell·Route Button·Permission Button이 실제 XCUI Tree에 노출됐다.
- Permission UI Test는 PASS했고 Route·Lifecycle 2건만 `app.scrollViews["공용 Navigation"]`에서 Identifier를 찾지 못한 채 `swipeLeft()`를 호출해 실패했다.
- 공용 Shell의 두 ScrollView는 의미용 `accessibilityLabel`만 있고 React Native iOS 자동화 Identifier를 명시하는 `testID`가 없었다.

## 조치

### 변경 범위

- `apps/mobile/src/MobileShell.tsx`
  - Navigation ScrollView: 기존 `accessibilityLabel="공용 Navigation"`을 유지하고 `testID="공용 Navigation"` 추가.
  - Content ScrollView: 기존 `accessibilityLabel="화면 내용"`을 유지하고 `testID="화면 내용"` 추가.
- `scripts/tests/mobile-shared-shell.test.mjs`: 두 ScrollView의 Label과 동일 testID 및 각 1건 계약 추가.
- `scripts/tests/ios-native-shell.test.mjs`: 기존 Identifier Query, Navigation/Content Swipe 3회, Root/Foreground Fail-close와 좌표·First·Sleep 우회 금지 계약 추가.
- Progress와 본 Attempt 17 보고서.
- 미변경: `DaonUITests.swift`, iOS Project/Swift/Objective-C Bridge, Android Native, C14 진단 Runner·Evidence·Workflow, Route 순서·Scroll 방향·구조·스타일, Bundle ID·Deep Link·Permission·Lifecycle·Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C16 RED | Mobile Unit 9/10 PASS·1 FAIL: 두 ScrollView testID 부재 재현 |
| C16 GREEN | Mobile Unit 10/10, iOS 32/32 PASS |
| 식별자 계약 | `공용 Navigation`, `화면 내용` Label/testID 동일 결속 및 각 1건 PASS |
| XCTest 의미 불변 | Identifier Query 3건, Swipe 3건, 각 `0..<8`, Root/Foreground 유지; 좌표·First·Sleep 우회 0 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 32/32, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,083 bytes SHA-256 `2ECAC3BE4ABF1A29FD1A1C53846B9A58BF697B8B3636BA9D8196089AC85F903E` |
| 전체 Node | 295/295 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, Mobile/iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Production Diff는 testID 2개뿐; Native·Workflow·XCTest·Android·Lock/Pin Diff 0; Signing 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- 구현·Portable 검증 중 예상하지 못한 오류는 없었다. RED 9/10은 승인 C16이 지정한 기존 식별자 결속 누락을 재현한 결과다.
- Windows에서는 XCUI Identifier 노출과 실제 Swipe 성공을 검증할 수 없으므로 macOS XCTest 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 Route·Lifecycle·Permission UI Test와 Simulator Verification을 판정한다.
3. XCUI Tree에서 두 ScrollView Identifier가 각각 `공용 Navigation`, `화면 내용`으로 노출되고 기존 Swipe가 실제 동작하는지 확인한다.
4. 성공 Manifest와 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
