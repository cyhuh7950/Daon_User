COMPLETED | R1-M3-06-I003 | C12 Swift Native Module React Bridge Type 노출 | App Bridging Header·Debug/Release 상대 경로·Target 격리 계약 Test·Progress·Attempt 13 보고서 변경 | iOS 26/26·Android 11/11·Mobile 전체·Node 288/288·Toolchain·Workflow/Bash·잔존물/Diff PASS | 새 exact-SHA macOS Native Compile·Simulator·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 13 결과보고

## 판정

C12 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. App 전용 Objective-C Bridging Header에 승인 React Native `RCTBridgeModule.h`를 import하고, Daon App Target의 Debug·Release Build Configuration에만 동일 Repository 상대경로를 적용했다. `DaonIOSHost`의 Module Name·Method Selector·8 Route·Permission·Settings·Lifecycle·JS Bridge 동작과 Pods/UI Test Target·Signing·Bundle ID를 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30235512898`, Job `89882478654`, Artifact `8641602401`은 Head `9b6f5ca328936885bdb5dd35de0a9c0daed46667`의 Fail-close 실행 결과다.
- CocoaPods `1.16.2`, npm/Portable 회귀, Pods 2회 재현성과 exact Simulator 생성은 성공했다.
- unsigned Release Build는 4분 37초 컴파일 후 `DaonIOSHost.swift:7:36`의 `cannot find type 'RCTBridgeModule' in scope`로 종료했다.
- Swift Host는 `RCTBridgeModule`, `RCTPromiseResolveBlock`, `RCTPromiseRejectBlock`을 사용하지만 App Target에 React Objective-C Type을 Swift로 노출할 Bridging Header 설정이 없었다.
- 승인 React Native `0.86.0`의 `React/Base/RCTBridgeModule.h`가 Protocol과 두 Promise Type을 함께 선언하므로 Header 하나의 공식 import로 필요한 Type 전부를 노출한다.
- Bridging Header는 `SWIFT_OBJC_BRIDGING_HEADER` Build Setting이 직접 소비하므로 PBXFileReference·Sources Membership이나 임시 Header Search Path를 추가할 필요가 없다.

## 조치

### 변경 범위

- `apps/mobile/ios/Daon/Daon-Bridging-Header.h`: `#import <React/RCTBridgeModule.h>` 한 줄 추가.
- `apps/mobile/ios/Daon.xcodeproj/project.pbxproj`: Daon App Target Debug·Release에 `SWIFT_OBJC_BRIDGING_HEADER = Daon/Daon-Bridging-Header.h` 적용.
- `scripts/tests/ios-native-shell.test.mjs`: React Type 3종, Header 내용, App Config 2개 적용, UI Test/Project Config 4개 미적용, 임시 Search Path 금지, Module Name 보존 계약.
- Progress와 본 Attempt 13 보고서.
- 미변경: `DaonIOSHost.swift`, `DaonUITests/**`, Pods Target/설정, `apps/mobile/src/**`, `apps/mobile/android/**`, Workflow, Evidence Writer, Lockfile, Toolchain/Quality Policy, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C12 RED | iOS Gate 25/26 PASS·1 FAIL: Bridging Header ENOENT로 Type 노출 부재 재현 |
| C12 GREEN | iOS Gate 26/26 PASS |
| React Type 노출 | Header는 승인 `React/RCTBridgeModule.h`만 import; Protocol·Resolve·Reject Type 3종 사용 계약 PASS |
| Target 격리 | Daon App Debug·Release 2개만 동일 상대경로 적용; UI Test Debug·Release와 Project Debug·Release 미적용 PASS |
| Project 최소 Diff | Bridging Header Build Setting 2개를 제거한 현재 Project 내용이 HEAD와 동일; PBXFileReference·Membership·Search Path 추가 0건 |
| Host·계약 불변 | `DaonIOSHost.swift` Diff 0, Module Name·Method Selector·Route·권한·Lifecycle 계약 기존 Test PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 26/26, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 288/288 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, iOS Test 2개 `node --check` PASS |
| 금지·잔존물 | 개인 절대경로·Header/Library Search Path 0, Pods/Build/Gem/Test Temp/Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Podfile·Host·UI Test·Lockfile·Toolchain Pin·Quality Policy Diff 0 |
| macOS Native Compile·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 최종 Host 불변 확인에서 현재 Checkout SHA와 `git show | Set-Content` 임시 파일 SHA를 비교했으나 PowerShell 직렬화의 줄바꿈 차이로 불일치했다. 현재 파일 자체를 `git diff --quiet -- apps/mobile/ios/Daon/DaonIOSHost.swift`로 다시 검증해 실제 Diff 0을 확인했다. Production 결함이나 정식 실패보고가 아니다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 실제 Swift Native Compile, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Compile·Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
