# R1-M3-06-C12 수정 작업지시서 — Swift Native Module의 React Bridge Type 노출

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I003` |
| Attempt | `13` |
| 사유 | 실제 Xcode Compile에서 Swift `DaonIOSHost`가 `RCTBridgeModule` Type을 찾지 못함 |
| 실패보고 | 0회 · 처음 도달한 Native Compile 계약 결함이며 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-13.md` |

## 2. 확인된 증거

- Run `30235512898`, Job `89882478654`, Artifact `8641602401`은 Head `9b6f5ca328936885bdb5dd35de0a9c0daed46667`의 실행 결과다.
- CocoaPods 1.16.2, npm/Portable 회귀, Pods 2회 재현성, exact Simulator 생성은 모두 성공했다.
- unsigned Release Build는 4분 37초 컴파일 후 `apps/mobile/ios/Daon/DaonIOSHost.swift:7:36: error: cannot find type 'RCTBridgeModule' in scope`로 종료했다.
- 현재 `DaonIOSHost.swift`는 `import React`와 `RCTBridgeModule`, `RCTPromiseResolveBlock`, `RCTPromiseRejectBlock`을 사용하지만 Project에 React Bridge Header를 Swift에 노출하는 Bridging Header와 Build Setting이 없다.
- Fail-close Manifest·Simulator terminate/shutdown/delete·Artifact Upload는 성공했다.

## 3. 필수 수정

1. 승인 React Native 0.86 Project 방식으로 Swift Native Module에 `RCTBridgeModule`과 Promise Type을 노출한다.
2. 최소 수정은 App Target 전용 Objective-C Bridging Header에 승인 React Header를 import하고, Xcode Project의 App Target Build Configuration들이 이를 Repository 상대경로로 참조하도록 한다.
3. Debug·Release 등 App Target의 모든 실제 Build Configuration에 동일 설정을 적용하되 Pods/UI Test Target·Signing·Bundle ID를 변경하지 않는다.
4. `DaonIOSHost`의 Module Name, Method Selector, 8 Route, Permission·Settings·Lifecycle 동작과 JS Bridge 계약은 변경하지 않는다.
5. Bridging Header File Membership/Project Reference가 필요한지 실제 Xcode Template·Build Setting에 맞게 확인하고 중복 Module Export나 임시 Header Search Path를 추가하지 않는다.
6. Swift Host가 사용하는 React Type마다 승인 Header 노출 계약, Build Setting 경로 존재, App Target 적용, Signing/Android/기능 불변을 TDD로 고정한다.

## 4. 완료 조건

- Bridging Header·Xcode App Target 계약 RED→GREEN
- iOS Root Gate, Evidence Fixture, Workflow Parse/Bash Syntax, Mobile·Android·Node·Toolchain, `git diff --check` PASS
- Bundle ID·Signing·Pods Target·UI Test Target·기능 Source·Android Native 변경 0
- 개인 절대경로·임시 Search Path·Generated Pods/Build/Gem/Temp 잔존 0
- Progress·Attempt 13에 Run/Job/Artifact와 RED/GREEN 기록
- Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 어울1 후속

