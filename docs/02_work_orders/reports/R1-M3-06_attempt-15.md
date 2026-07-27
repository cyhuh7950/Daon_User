COMPLETED | R1-M3-06-I005 | C14 iOS UI 조기 종료의 Fail-close 진단 수집과 Root Readiness 정밀화 | 전용 진단 Runner·Workflow 연결·UI Test 선검증·Evidence 결속·계약 Test·Progress·Attempt 15 보고서 생성 | iOS 30/30·Android 11/11·Mobile 전체·Node 292/292·Toolchain·Workflow/Bash·진단 오류 Fixture·Diff PASS | 기존 Artifact만으로 Crash 단일 원인 미확정, 새 exact-SHA macOS Xcode 26.6 진단 실행 미수행 | 어울1의 Commit·Push와 macOS 진단 Run·Artifact 원인 판정

# R1-M3-06 Attempt 15 결과보고

## 판정

C14 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_DIAGNOSTIC_RUN`이다. Run `30237187483`의 기존 Artifact는 UI Suite 3 Test·5 Failure·Exit 65와 앱 조기 종료를 입증하지만 Crash 단일 원인은 입증하지 못한다. 승인 경계에 따라 Product 동작은 추측 수정하지 않았고, 다음 macOS 실행이 원인을 단일 판정할 수 있도록 진단 증거와 Root Readiness만 보강했다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- PR Head는 `26045362e83472b20e194566d75902fb6e2e9a84`, 실제 merge candidate Checkout SHA는 `c5c882416d2c93d1ba5395118be8be095ed89df6`다.
- Run `30237187483`, Job `89887116611`, Artifact `8642229194`에서 Toolchain·Pods·Simulator·CompileStoryboard·unsigned Release Build는 성공했다.
- 일반 UI Suite는 Route/Lifecycle의 앱 not running과 Permission의 버튼 부재·`com.sinsan.daon crashed in <external symbol>`을 남기고 3 Test·5 Failure·Exit 65로 종료됐다.
- 기존 `.xcresult`는 Artifact에 있으나 Windows에서 Xcode 전용 Crash 종료 원문을 독립 추출할 수 없고, Daon Unified Log·Crash Report·진단 명령 상태가 없었다. 따라서 현재 증거로 Product 원인을 추측하는 것은 C14 경계를 위반한다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: UI Test 실행을 전용 Runner에 연결. 기존 `always()` Cleanup·Artifact Upload 순서와 Fail-close Outcome을 보존했다.
- `apps/mobile/ios/ci/run-ui-tests-with-diagnostics.sh`: 원래 `xcodebuild test` Exit를 저장한 뒤 현대 `xcresulttool get test-results summary/tests`, Attachment export, exact Simulator의 UI Test 구간 Daon Unified Log, Daon Crash/Diagnostic Report 원본·파일명 목록과 각 수집 상태를 기록하고 원래 Exit로 종료한다.
- `apps/mobile/ios/DaonUITests/DaonUITests.swift`: 각 Test launch와 Lifecycle 재활성화 전에 `runningForeground`와 접근성 Root `Daon ios 공용 Shell`을 확인하며 첫 실패에서 Scenario 상호작용을 중단한다.
- `apps/mobile/ios/ci/write-evidence.mjs`: 새 진단 Runner Source를 exact-SHA Evidence 파일 Hash 목록에 결속했다.
- `scripts/tests/ios-native-shell.test.mjs`, `scripts/tests/ios-phase-a-evidence.test.mjs`: Exit 보존, 진단 오류, Cleanup/Upload 순서, 현대 xcresult 명령, Root Readiness, Evidence Source 계약과 Fixture를 추가했다.
- Progress와 본 Attempt 15 보고서.
- 미변경: `apps/mobile/src/**`, Android Native, Bundle ID, Deep Link 8 Route, Permission·Lifecycle Product 계약, Signing, 공개 API·데이터 계약, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C14 RED | iOS 27/30 PASS·3 FAIL: Runner/Workflow 연결과 Root Readiness 부재 재현 |
| C14 GREEN | iOS 30/30 PASS |
| 진단 오류 Fixture | mock XCTest Exit 65, xcresult/log 수집 Exit 44에서도 최종 Exit 65와 각 실패 상태 보존 PASS |
| Fixture 안정성 | Windows 절대경로 변환 의존 제거 후 iOS Gate 3회 연속 30/30 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 30/30, Android/iOS Bundle PASS |
| Bundle | Android 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; iOS 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 292/292 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Mobile Product·Android·Lock/Pin Diff 0; Signing Asset 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- 첫 GREEN은 기존 Signing 정적 계약이 Workflow 내부 문자열만 보던 문제와 Windows Fixture의 경로 표현 문제로 26/30이었다. 실행 계약이 Workflow에서 전용 Runner로 이동한 사실에 맞춰 Workflow+Runner를 함께 검증하고 Fixture를 격리 상대 경로로 변경했다.
- 첫 Mobile 전체 회귀에서 같은 Windows Fixture 경로가 간헐적으로 Evidence 위치를 벗어나 iOS 29/30으로 중단됐다. 환경의 절대경로 변환을 제거한 뒤 iOS Gate 3회 연속, Mobile 전체와 전체 Node를 재실행해 모두 통과했다. Product 오류가 아니며 동일 시도를 반복하지 않았다.
- 현재 Xcode 26.6 실행 환경은 Windows에 없으므로 실제 `xcresulttool` 출력·Crash Report 생성 성공을 주장하지 않는다. Runner는 Legacy `get object`/`--legacy`를 사용하지 않고 현대 subcommand를 사용하며 각 수집 실패를 원래 XCTest 결과와 분리 기록한다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행하고 `diagnostics/diagnostic-status.txt`, xcresult Summary/Test/Attachment, Daon Unified Log와 Crash Report를 판정한다.
3. 새 Artifact가 단일 Crash 원인을 입증하면 그 원인만 별도 승인 작업지시로 최소 수정한다. 입증 전 Product 수정은 금지한다.
4. macOS 진단 Run 전에는 UI Runtime 결함 해결이나 Simulator 완료로 판정할 수 없다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
