# R1-M3-06-C15 수정 작업지시서 — iOS Swift Native Module 메서드 Export 복구

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I005` |
| Attempt | `16` |
| 사유 | C14 진단 Run이 앱 시작 직후 `subscribeIOSDeepLinks`의 `nativeHost.consumePendingDeepLink` 호출에서 `TypeError: undefined is not a function`을 확정함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-16.md` |

## 2. 확인된 증거

- PR Head `ff4ae7b92223edf04790831788bebb6e157c4390`, merge candidate Checkout `2c9e30706a521ad3c67be55436acd7b72fed1fae`의 Run `30238990044`, Artifact `8642713026`을 검토했다.
- CocoaPods·Portable Gate·Pods 재현 설치·Simulator 생성·CompileStoryboard·unsigned Release Build는 성공했다.
- UI Test Runner는 원래 Exit `65`를 보존했고 xcresult, exact Simulator Unified Log와 Daon Crash Report 3개를 Cleanup 전에 모두 수집했다.
- Unified Log의 최초 치명 오류는 `subscribeIOSDeepLinks@45110:50`의 `TypeError: undefined is not a function`이며, 보존된 exact Bundle 45110행은 `nativeHost?.consumePendingDeepLink().then(accept)`다.
- 이어진 `RCTFatalException`과 세 Crash Report의 `SIGABRT`는 같은 JavaScript 예외의 결과다. Route·Lifecycle·Permission UI Test 실패는 앱 시작 종료의 후속 증상이다.
- `DaonIOSHost.swift`는 `RCTBridgeModule`을 채택하고 `@objc` Selector를 선언하지만, Project에는 Swift 외부 Module의 JavaScript Method Metadata와 정적 등록을 제공하는 `RCT_EXTERN_MODULE`/`RCT_EXTERN_METHOD` Objective-C Bridge 구현이 없다.
- React Native `RCTBridgeModule.h`의 현재 Pin은 Swift Class 등록에 `RCT_EXTERN_MODULE`, Method Export Metadata에 `RCT_EXTERN_METHOD`를 제공한다. 따라서 현재 Native Module 객체의 승인 메서드가 JavaScript Function으로 노출되지 않은 것이 확인된 원인이다.
- 같은 SHA의 공통 Quality Gate Run `30238990037`은 성공했다.

## 3. 필수 작업

### A. Swift Native Module Export를 최소 복구한다

1. `DaonIOSHost`를 React Native가 지원하는 Swift Native Module 외부 Bridge 방식으로 등록하고 아래 기존 메서드만 JavaScript에 Export한다.
   - `saveNavigationRoute`
   - `restoreNavigationRoute`
   - `getLifecycleState`
   - `consumePendingDeepLink`
   - `checkPermission`
   - `requestPermission`
   - `openApplicationSettings`
2. 각 `RCT_EXTERN_METHOD`의 Objective-C Selector 조각, 인수형과 Promise resolver/rejecter 순서는 `DaonIOSHost.swift`의 기존 `@objc` Selector와 정확히 일치해야 한다.
3. 외부 Bridge 구현 파일을 Daon App Target Sources에만 포함한다. UI Test Target·Pods Target·Android에는 포함하지 않는다.
4. 중복 또는 불안정한 Module 등록을 만들지 말고, 현재 React Native Pin의 `RCT_EXTERN_MODULE` 계약에 맞춰 Swift Class의 Protocol/Module Name 선언을 최소 정합화한다.
5. TypeScript의 optional chaining이나 UI Test를 완화하여 결함을 숨기지 않는다. 승인 Native Module을 사용할 수 없는 경우의 기존 fallback 의미도 확대하지 않는다.

### B. TDD와 계약 검증

1. 구현 전 계약 Test로 다음을 RED로 고정한다.
   - 외부 Bridge가 Module 1개와 승인 7개 Method만 Export
   - Swift `@objc` Selector와 외부 Bridge Selector 1:1 일치
   - App Target Sources Membership 1건, UI Test/Pods Membership 0건
   - Route 8종·Permission 3종·Lifecycle·Bundle ID·Deep Link·Signing 계약 불변
2. 잘못된 Method 이름, Resolver/Rejecter 누락, 중복 Module 등록, 브리지 파일 Target 누락을 Fixture 또는 정적 계약으로 검출한다.
3. C14 진단 Runner와 Fail-close Evidence 계약을 유지한다.

## 4. 완료 조건

- C15 계약 RED→GREEN
- iOS Native, Evidence, Mobile·Android·전체 Node·Toolchain, Workflow JSON/Bash, `git diff --check` PASS
- 외부 Bridge 승인 7개 Method와 Swift Selector 1:1, App Target Sources Membership PASS
- UI Test·Pods·Android Target와 Product Route/Permission/Lifecycle/Bundle/Deep Link/Signing 변경 0
- 개인 절대경로·Generated Pods/Build/Gem/Test Temp·Signing Asset 잔존 0
- Progress와 Attempt 16에 Run/Artifact/Crash 근거, RED→GREEN, 변경·검증·미해결 macOS 실행을 기록
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속

