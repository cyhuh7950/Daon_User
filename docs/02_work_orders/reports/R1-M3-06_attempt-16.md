COMPLETED | R1-M3-06-I005 | C15 Swift Native Module 승인 메서드 Export 복구 | Objective-C 외부 Bridge·Swift 등록 정합화·App Target Sources·Evidence·계약 Test·Progress·Attempt 16 변경 | iOS 31/31·Android 11/11·Mobile 전체·Node 293/293·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA macOS Native Compile·UI Runtime 미검증 | 어울1의 Commit·Push와 macOS CI·Artifact 판정

# R1-M3-06 Attempt 16 결과보고

## 판정

C15 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. C14 진단 Run이 확정한 `consumePendingDeepLink` JavaScript Method 미Export 원인만 최소 복구했다. TypeScript optional chaining·UI Test·승인 Route/Permission/Lifecycle 계약을 완화하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- PR Head `ff4ae7b92223edf04790831788bebb6e157c4390`, merge candidate `2c9e30706a521ad3c67be55436acd7b72fed1fae`의 Run `30238990044`, Artifact `8642713026`에서 Pods·Simulator·CompileStoryboard·unsigned Build는 성공했다.
- C14 Runner는 XCTest Exit 65와 xcresult·exact Simulator Unified Log·Daon Crash Report 3개를 Cleanup 전에 보존했다.
- Unified Log의 최초 치명 오류는 Bundle 45110행 `nativeHost?.consumePendingDeepLink().then(accept)`의 `TypeError: undefined is not a function`이다. 이어진 RCTFatalException·SIGABRT와 Route/Lifecycle/Permission 실패는 후속 증상이다.
- 기존 Swift Class에는 7개 `@objc` Selector가 있었지만 `RCT_EXTERN_MODULE`·`RCT_EXTERN_METHOD` Metadata와 App Target Sources Bridge가 없었다.
- React Native 0.86.0의 `RCTBridgeModule.h`가 제시하는 Swift 외부 Module 계약에 따라 Bridge Category가 Protocol·Module 이름을 소유하도록 중복 Swift `RCTBridgeModule` 채택과 `moduleName()`은 제거했다.

## 조치

### 변경 범위

- `apps/mobile/ios/Daon/DaonIOSHostBridge.m`: `RCT_EXTERN_MODULE(DaonIOSHost, NSObject)` 1개와 승인된 7개 Method만 Export했다.
  - `saveNavigationRoute`
  - `restoreNavigationRoute`
  - `getLifecycleState`
  - `consumePendingDeepLink`
  - `checkPermission`
  - `requestPermission`
  - `openApplicationSettings`
- `apps/mobile/ios/Daon/DaonIOSHost.swift`: 외부 Bridge가 Protocol·Module 등록을 담당하도록 중복 `RCTBridgeModule` 채택과 `moduleName()`을 제거하고 `requiresMainQueueSetup()`을 Objective-C Class Method로 노출했다. 기존 Method 구현·Selector·Route/Permission/Lifecycle 로직은 보존했다.
- `apps/mobile/ios/Daon.xcodeproj/project.pbxproj`: Bridge File Reference·Build File을 생성해 Daon App Sources에 한 번만 포함했다. UI Test·Pods Target에는 포함하지 않았다.
- `apps/mobile/ios/ci/write-evidence.mjs`, `scripts/tests/ios-phase-a-evidence.test.mjs`: 새 Bridge Source를 exact-SHA Evidence 파일 목록에 결속했다.
- `scripts/tests/ios-native-shell.test.mjs`: Module 1개·Method 7개·Swift Selector 1:1·App Target 단독 Membership과 잘못된 이름, Resolver 누락, 중복 Module, Target 누락 거부 Fixture를 추가했다.
- Progress와 본 Attempt 16 보고서.
- 미변경: `.github/workflows/**`, C14 진단 Runner, `DaonUITests`, `apps/mobile/src/**`, Android Native, Bundle ID, Deep Link 8 Route, Permission 3종, Lifecycle, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C15 RED | iOS 29/31 PASS·2 FAIL: 기존 중복 등록 경로와 외부 Bridge 파일 부재 재현 |
| C15 GREEN | iOS 31/31 PASS |
| Export 계약 | Module 1개, 승인 Method 7개, 추가 Export 0, Swift `@objc` Selector 7개와 1:1 PASS |
| 잘못된 계약 Fixture | Method 오명, Resolver 누락, 중복 Module, App Sources Membership 누락 모두 거부 PASS |
| Target Membership | App Sources 1건, UI Test 0건, Pods 0건 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 31/31, Android/iOS Bundle PASS |
| Bundle | Android 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; iOS 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 293/293 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Workflow·C14 Runner·UI Test·Mobile Product·Android·Lock/Pin Diff 0; Signing 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- 구현 후 첫 GREEN은 29/31이었다. 기존 C12 Test가 Swift Class의 직접 `RCTBridgeModule` 채택을 계속 기대했고 신규 PBX Fixture가 실제 BuildFile Comment의 `in Sources`를 누락해 오판했다.
- Product Patch는 첫 적용부터 승인 외부 Bridge 구조를 충족했다. React Native Header의 실제 외부 Module 계약과 PBX Diff를 근거로 Test 대상과 Matcher만 정합화했고 iOS 31/31 및 전체 회귀로 검증했다.
- Windows에서는 Objective-C/Swift Native Compile과 Simulator Runtime을 실행할 수 없으므로 Export 복구의 macOS 성공을 주장하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 Objective-C Bridge Compile, Swift Link, 앱 Root, Route/Lifecycle/Permission UI Test와 Simulator Verification을 판정한다.
3. C14 진단 Artifact에서 `undefined is not a function`·RCTFatalException·SIGABRT가 재발하지 않는지 확인한다.
4. 성공 Manifest와 진단 Artifact 확인 전에는 iOS Phase A 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
