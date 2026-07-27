COMPLETED | R1-M3-06-I001 | C10 CocoaPods Gem 이름과 pod 실행 파일 결속 | exact Gem/COMMAND 현재 검증·후속 Pods/Manifest 결속·계약 Test·Progress·Attempt 11 보고서 변경 | iOS 24/24·Android 11/11·Mobile 전체·Node 286/286·Toolchain·Workflow JSON/WSL Bash·잔존물/Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 11 결과보고

## 판정

C10 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 승인 CocoaPods 설치와 격리 `GEM_HOME/GEM_PATH`를 유지하면서 현재 버전 확인, 후속 Pods 설치 2회, Manifest 버전 수집의 RubyGems 실행을 모두 `gem exec -g cocoapods -v 1.16.2 pod`로 결속했다. 실제 Gem 이름 `cocoapods`와 실행 파일 `pod`를 명시했으며 Pin, 엄격한 버전 비교, Outcome/Evidence Fail-close, 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책은 유지했다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30234549624`, Job `89879720903`, Artifact `8641203838`은 Head `3498a4776d1cab548e2abe00473d34a6c6c3a75c`의 Fail-close 실행 결과다.
- Node·npm·uv와 승인 `cocoapods` Gem `1.16.2` 및 45개 Gem 설치는 성공했다.
- 설치 뒤 `gem exec -v 1.16.2 pod --version`은 실행 파일 이름 `pod`를 Gem 이름으로 추정해 `Could not find a valid gem 'pod' (= 1.16.2) in any repository`로 종료했다.
- RubyGems `gem exec`의 `-g, --gem GEM` 옵션으로 실제 Gem 이름 `cocoapods`를 지정하고, 실행 파일 `pod`를 COMMAND로 직접 전달하도록 교정했다.
- 현재 Step은 실제 버전 출력을 로그에 남기고 `1.16.2`와 엄격 비교한다. Manifest 실행 실패는 기존 `|| true`와 Evidence Writer의 unknown/INCOMPLETE 판정으로 성공 승격되지 않는다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: CocoaPods 현재 버전 확인 1회, 후속 Pods 버전 확인 1회와 install 2회, Manifest 버전 수집 1회에 `-g cocoapods` 추가.
- `scripts/tests/ios-native-shell.test.mjs`: Gem 이름·실행 파일 exact 결속, 실제 `1.16.2` 승인·`1.17.0` 거부, 후속 install 2회, Manifest 동일 형식 및 Gem 이름 누락·잘못된 `-- pod`·직접 Script·Wrapper·일반 `pod` Fallback 금지 계약.
- Progress와 본 Attempt 11 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C10 RED | iOS Gate 22/24 PASS·2 FAIL: 기존 5개 실행의 Gem 이름 명시 누락 재현 |
| C10 GREEN | iOS Gate 24/24 PASS |
| 승인·부정 버전 | 실제 출력 `1.16.2` 승인, `1.17.0` 거부, 현재 버전 로그·엄격 비교 PASS |
| exact 실행 형식 | 버전 실행 3회, install 2회 모두 `gem exec -g cocoapods -v 1.16.2 pod`; Gem 이름 누락·잘못된 `-- pod`·직접 Script·Wrapper·일반 pod Fallback 0건 |
| Evidence Fixture | CocoaPods 증거 공백은 `INCOMPLETE`·`toolchain:cocoapods:unknown`; 기존 setup_uv/cocoapods Outcome Fail-close PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 24/24, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 286/286 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, iOS Test 2개 `node --check` PASS |
| 잔존물 | Repository Gem Directory 0, Test Temp Fixture 0, Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 최초 Workflow Patch는 JSON 한 줄로 직렬화된 Step 내부의 일반 Shell 행을 독립 문맥으로 찾지 못해 원자적으로 미적용됐다. 파일 구조를 확인하고 동일 세 Step 전체 행에서 정확한 다섯 실행 문자열만 `apply_patch`로 교정했다. 기능·정책·Evidence 의미 변경은 없었고 이후 iOS Gate 24/24와 전체 회귀를 확인했다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 CocoaPods Gem/실행 파일 결속, Pods, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
