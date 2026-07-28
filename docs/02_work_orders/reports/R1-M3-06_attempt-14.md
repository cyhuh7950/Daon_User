COMPLETED | R1-M3-06-I004 | C13 LaunchScreen Storyboard Xcode 문서 계약 복구 | 승인 Template 메타데이터·dependencies·XML/ID 계약 Test·Progress·Attempt 14 보고서 변경 | iOS 27/27·Android 11/11·Mobile 전체·Node 289/289·Toolchain·Workflow/Bash·잔존물/Diff PASS | 새 exact-SHA macOS ibtool·Build·Simulator·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 14 결과보고

## 판정

C13 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 승인 React Native Template Commit `4d7c716d7afddc03ed73ca49c1102a92a0a9ff71`의 LaunchScreen 문서 형식에서 근거가 확인된 Interface Builder 메타데이터와 dependencies/capability만 현재 Storyboard에 추가했다. 기존 표시명 `Daon`, 중앙 정렬, 16pt 제목, 배경색, Auto Layout·ID·참조 의미를 보존했으며 Storyboard 외 Production 파일은 변경하지 않았다. Windows Portable 검증을 macOS `ibtool` 성공으로 주장하지 않는다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30236247138`, Job `89884498651`, Head `36ca17837e88d5bb99eae0fcf90d1ae0b973fa63`의 exact-SHA 실행에서 Checkout, Node/npm/uv, CocoaPods `1.16.2`, Portable 회귀, Pods 재현 설치와 exact Simulator 생성은 성공했다.
- C12 React Bridging Header 적용 뒤 `DaonIOSHost.swift`와 `AppDelegate.swift`의 arm64·x86_64 Swift Compile도 통과했다.
- unsigned Release Build의 유일한 실패 명령은 `CompileStoryboard apps/mobile/ios/Daon/LaunchScreen.storyboard`였다.
- `ibtoold`는 `IBDocumentUnarchiving-ToolsVersion` 값 nil 예외를 남겼고 `ibtool`은 `com.apple.InterfaceBuilder error -1`, 문서 열기 실패와 Exit 65를 반환했다.
- 승인 Template의 실제 Storyboard는 `toolsVersion="15702"`, `propertyAccessControl="none"`, `colorMatched="YES"`, iOS deployment, CocoaTouch Plugin `15704`, Safe Area와 Xcode 8 format capability를 사용한다.
- 승인 Template에는 `systemVersion`·`sourceToolsVersion`이 없으므로 이를 추측해 추가하지 않았다.

## 조치

### 변경 범위

- `apps/mobile/ios/Daon/LaunchScreen.storyboard`: 승인 Template 근거 document 속성 3개와 dependencies 4개 추가.
- `scripts/tests/ios-native-shell.test.mjs`: 승인 Template Commit·문서 메타데이터·dependencies, XML well-formed, ID 고유성, initial VC/Constraint 참조 존재, 기존 Daon UI 의미 보존 계약.
- Progress와 본 Attempt 14 보고서.
- 미변경: Bundle ID, Deployment Target, Signing, 권한, Deep Link, Swift Host, Podfile/Pods, App/UI Test Target, `apps/mobile/src/**`, Android Native, Workflow, Evidence Writer, Lockfile, Toolchain/Quality Policy.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C13 RED | iOS Gate 26/27 PASS·1 FAIL: XML은 유효하나 승인 Interface Builder 문서 계약 부재 재현 |
| C13 GREEN | iOS Gate 27/27 PASS |
| 승인 Template 근거 | exact Commit `4d7c716...`; toolsVersion 15702, Plugin 15704, iOS/Safe Area/Xcode 8 capability PASS |
| XML·참조 무결성 | `fast-xml-parser` XMLValidator PASS, ID 중복 0, initialViewController·Constraint first/secondItem 전부 존재 |
| UI 의미 불변 | `Daon`, center 정렬, bold 16pt, systemBackgroundColor, Safe Area centerX/centerY PASS |
| 최소 Production Diff | 추가 메타데이터/dependencies 제거 시 Storyboard가 HEAD와 동일; 다른 Mobile Production Diff 0 |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 27/27, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 289/289 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, iOS Test 2개 `node --check` PASS |
| 금지·잔존물 | system/sourceToolsVersion 추측·개인 절대경로 0, Pods/Build/Gem/Test Temp/Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, Storyboard 외 Production Diff 0 |
| macOS ibtool·Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 구현과 Portable 검증 중 예상하지 못한 오류는 없었다. RED 26/27은 승인 C13이 지정한 기존 Storyboard 문서 계약 결함을 재현한 결과다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 Xcode 26.6 `CompileStoryboard`, unsigned Build, XCTest, Simulator Verification과 Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 `ibtool`·iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
