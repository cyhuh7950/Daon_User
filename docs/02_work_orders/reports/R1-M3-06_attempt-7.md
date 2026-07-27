COMPLETED | R1-M3-06-I001 | C06 승인 CocoaPods 절대 실행경로 결속 | DAON_POD_BIN 현재 검증·GITHUB_ENV·후속 Pods/Manifest 결속·계약 Test·Progress·Attempt 7 보고서 변경 | iOS 24/24·Android 11/11·Mobile 전체·Node 286/286·Toolchain·Workflow JSON/WSL Bash·잔존물/Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 7 결과보고

## 판정

C06 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 설치된 승인 CocoaPods 실행 파일을 절대경로로 직접 검증하고 `DAON_POD_BIN`으로 현재·후속 Pods·Manifest에 동일 결속했다. 일반 `pod` Fallback은 제거했고 변수 누락 시 CocoaPods 증거를 빈 값으로 전달해 Writer가 `INCOMPLETE`로 판정한다. `GEM_HOME/GEM_PATH`·RUNNER_TEMP 격리, 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책은 유지했다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30231718788`, Job `89871737982`에서 승인 CocoaPods `1.16.2`와 45개 Gem은 RUNNER_TEMP 격리 경로에 설치 성공했다.
- 설치 직후 CocoaPods Step은 실패했고 Artifact `8640306747`은 `failed_steps:[cocoapods]`를 정확히 기록했다.
- Manifest가 일반 `pod --version`을 `1.17.0`으로 기록해 macOS Runner에서는 PATH 기반 우선순위가 승인 실행 파일 선택을 보장하지 못함이 확인됐다.
- 설치 완료 후 `${POD_GEM_BIN}/pod`를 `DAON_POD_BIN`으로 지정하고 실행 가능 여부와 실제 `1.16.2`를 절대경로 호출로 검증한 다음 `$GITHUB_ENV`에 기록한다.
- 후속 Pods Step의 버전 확인과 두 번의 `install`, Manifest의 실제 버전 수집이 모두 `${DAON_POD_BIN}`만 호출한다.
- Manifest는 `DAON_POD_BIN`이 누락되거나 실행 불가능하면 `IOS_COCOAPODS_VERSION=""`을 유지한다. Runner 일반 `pod`로 Fallback하지 않으며 Writer의 기존 unknown 판정으로 성공을 금지한다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: 승인 pod 절대경로 검증·`DAON_POD_BIN` 지속·후속 Pods 두 실행·Manifest 수집 결속.
- `scripts/tests/ios-native-shell.test.mjs`: 현재·후속 Pods·Manifest 동일 절대경로, 일반 pod Fallback 0 계약 추가 및 C05 계약 정합화.
- `scripts/tests/ios-phase-a-evidence.test.mjs`: CocoaPods 증거 공백 시 `INCOMPLETE`·unknown 사유 계약 추가.
- Progress와 본 Attempt 7 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C06 RED | iOS Gate 23/24 PASS·1 FAIL: `DAON_POD_BIN` 절대경로 계약 부재 재현 |
| C06 GREEN | iOS Gate 24/24 PASS |
| 누락 Fail-close | `IOS_COCOAPODS_VERSION=""` Fixture가 `INCOMPLETE`, `toolchain:cocoapods:unknown` PASS |
| 일반 pod 실행 경로 | Workflow에서 `pod --version`, `pod install`, `command -v pod` Literal 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 24/24, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 286/286 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, Test 2개 `node --check` PASS |
| 잔존물 | Repository Gem Directory 0, Test Temp Fixture 0, Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 일반 pod 실행 0건 확인의 첫 `rg` 정규식은 PowerShell 이중 Escape로 괄호가 닫히지 않아 명령만 실패했다. Source 변경 없이 `Select-String -SimpleMatch`의 세 Literal 검사로 전환해 0건과 `git diff --check` PASS를 확인했다. 검증 도구 표현 문제이며 정식 실패보고가 아니다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 승인 절대경로 CocoaPods, Pods, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
