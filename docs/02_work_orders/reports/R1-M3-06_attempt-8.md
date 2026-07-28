COMPLETED | R1-M3-06-I001 | C07 CocoaPods Gem 본체 Script 직접 호출 | DAON_POD_SCRIPT 현재 검증·후속 Pods/Manifest 결속·계약 Test·Progress·Attempt 8 보고서 변경 | iOS 24/24·Android 11/11·Mobile 전체·Node 286/286·Toolchain·Workflow JSON/WSL Bash·잔존물/Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 8 결과보고

## 판정

C07 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. RubyGems Wrapper와 PATH 우선순위에 의존하지 않고 승인 CocoaPods Gem 본체 Script를 결정적 경로의 `DAON_POD_SCRIPT`로 고정했다. 현재 버전 검증, 후속 Pods 설치 2회, Manifest 버전 수집은 모두 `ruby "${DAON_POD_SCRIPT}"`로 결속했다. `GEM_HOME/GEM_PATH`와 RUNNER_TEMP 격리, 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책은 유지했다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30232408873`, Job `89873623882`에서 승인 CocoaPods `1.16.2`와 45개 Gem은 RUNNER_TEMP 격리 경로에 설치 성공했다.
- 설치 성공 직후 첫 `${POD_GEM_BIN}/pod` 실행 가능 검사에서 추가 출력 없이 Step이 종료되어 RubyGems Wrapper 위치·형식 가정이 macOS Runner에서 성립하지 않음이 확인됐다.
- 승인 Gem Repository 안의 `${POD_GEM_HOME}/gems/cocoapods-1.16.2/bin/pod`는 버전에 의해 결정되는 본체 Script 경로이므로 Wrapper와 PATH 선택을 거치지 않는다.
- 설치 Step은 본체 파일 존재를 검사한 뒤 `ruby "${DAON_POD_SCRIPT}" --version` 실제 출력을 로그로 남기고 `1.16.2`와 엄격 비교하며, 성공한 경로만 `$GITHUB_ENV`에 기록한다.
- 후속 Pods Step의 버전 확인과 두 번의 `install`, Manifest의 실제 버전 수집이 모두 동일 Script를 Ruby로 직접 실행한다.
- Script가 누락되면 Pods Step은 즉시 실패하고 Manifest는 빈 CocoaPods 버전을 유지한다. 일반 `pod` 또는 Wrapper로 Fallback하지 않아 기존 Writer의 unknown/INCOMPLETE Fail-close 경계를 보존한다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: `DAON_POD_SCRIPT` 결정 경로, 파일 확인, 실제 버전 로그·엄격 비교, 후속 Pods 두 실행과 Manifest 수집의 Ruby 직접 호출.
- `scripts/tests/ios-native-shell.test.mjs`: 실제 `1.16.2` 승인·`1.17.0` 거부, 현재/후속/Manifest 동일 Script, Wrapper·PATH·일반 pod Fallback 금지 계약 추가 및 C05 계약 정합화.
- Progress와 본 Attempt 8 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C07 RED | iOS Gate 22/24 PASS·2 FAIL: 기존 `DAON_POD_BIN` Wrapper 사용과 `DAON_POD_SCRIPT` 부재 재현 |
| C07 GREEN | iOS Gate 24/24 PASS |
| 실제 버전 계약 | `1.16.2` 출력 승인, `1.17.0` 출력 거부, 실제 버전 로그·엄격 비교 PASS |
| 직접 호출 경로 | 현재 검증·후속 install 2회·Manifest 모두 `ruby "${DAON_POD_SCRIPT}"`; `DAON_POD_BIN`, 일반 pod, `${POD_GEM_BIN}/pod` 실행 Fallback 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 24/24, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 286/286 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, Test 2개 `node --check` PASS |
| 잔존물 | Repository Gem Directory 0, Test Temp Fixture 0, Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 구현·최종 검증 과정의 비예상 오류는 없었다. C07 RED의 2건 실패는 승인 결함을 재현한 TDD 예상 결과이며 GREEN 후 모두 해소됐다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 Gem 본체 Script의 버전 검증, Pods, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
