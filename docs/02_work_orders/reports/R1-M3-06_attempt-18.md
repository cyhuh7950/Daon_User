COMPLETED | R1-M3-06-I006 | C17 React Native Scroll Host XCUI Query 정합 | Navigation·화면 내용 Host `otherElements` Query 3건과 존재 선검증·iOS 계약 Test·Progress·Attempt 18 변경 | iOS 32/32·Mobile 전체·Android 11/11·Node 295/295·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Route·Lifecycle XCTest 미검증 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 18 결과보고

## 판정

C17 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. React Native가 `testID`를 부여한 바깥 Scroll Host와 실제 Scroll 동작을 수행하는 내부 `XCUIElementTypeScrollView`의 차이에 맞춰 XCTest Query만 최소 수정했다. 제품 Source·UI 구조·Swipe 방향·Retry 횟수와 Route/Lifecycle/Permission 시나리오는 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `fbe78aad72a90a64cc2b37961909f94eca43e21d`, merge candidate `d3cf0a53b11d0f57297cd23707d3f25417018000`의 Run `30241706006`, Job `89900142973`, Artifact `8643657783`에서 공통 Gate·Pods·Simulator·unsigned Build와 Permission UI Test가 성공했다.
- C15의 Native Method 오류와 App Crash는 0건이며 C16의 `testID`는 Release Bundle과 Build에 포함됐다.
- Route/Lifecycle 2건은 내부 ScrollView 2개에 Identifier가 없다는 동일 24행 오류로 실패했다. `testID`는 내부 `scrollViews`가 아니라 바깥 React Native Host View에 결속되므로 기존 Element Type 한정 Query가 원인이었다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - Navigation 1건을 `app.otherElements["공용 Navigation"]`으로 변경하고 Swipe 전 존재를 Fail-close 확인.
  - Content 2건을 `app.otherElements["화면 내용"]`으로 변경하고 두 경로 모두 Swipe 전 존재를 Fail-close 확인.
- `scripts/tests/ios-native-shell.test.mjs`: Host Query 3건, 정확 Identifier, 존재 선검증 3건, 내부 `scrollViews` Query 금지와 기존 Swipe 의미 불변 계약 추가.
- Progress와 본 Attempt 18 보고서.
- 미변경: `MobileShell.tsx`의 두 `accessibilityLabel`·`testID`, Product Source, Swift/Objective-C Bridge, Xcode Project, Android Native, Evidence/Workflow, Bundle ID·Deep Link·Permission·Lifecycle·Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C17 RED | iOS 31/32 PASS·1 FAIL: `app.otherElements["공용 Navigation"]` 0건으로 기존 Element Type 오사용 재현 |
| C17 GREEN | iOS 32/32 PASS |
| Host Query 계약 | Navigation 1건·Content 2건 `otherElements`, 내부 `scrollViews` Query 0, 존재 선검증 3건 PASS |
| XCTest 의미 불변 | `0..<8` 3건, Navigation `swipeLeft` 1건, Content `swipeUp` 2건, Button Hittable·Route Title·Root/Foreground 유지; 좌표·firstMatch·Sleep·Skip 우회 0 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 32/32, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,083 bytes SHA-256 `2ECAC3BE4ABF1A29FD1A1C53846B9A58BF697B8B3636BA9D8196089AC85F903E` |
| 전체 Node | 295/295 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Product Source·Bridge·Evidence/Workflow·Android·Lock/Pin Diff 0; 개인 절대경로 신규 0; Signing 0; Pods/Build/Artifact/Test Temp 잔존 0 |

### 오류·복구 근거

- RED 31/32는 승인 C17이 지정한 기존 `scrollViews` Element Type 오사용을 재현한 결과이며, 다른 iOS/Evidence/Deep Link 계약 31개는 모두 통과했다.
- 구현과 Portable 회귀 중 예상하지 못한 오류는 없었다.
- Windows에서는 XCUI Host의 실제 Swipe 전달과 Route/Lifecycle 성공을 검증할 수 없으므로 macOS XCTest 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 Route·Lifecycle·Permission UI Test와 Simulator Verification을 판정한다.
3. Host `otherElements`의 Swipe가 내부 ScrollView에 전달되어 8개 Route와 Lifecycle 복원이 실제 성공하는지 Artifact에서 확인한다.
4. 성공 Manifest와 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
