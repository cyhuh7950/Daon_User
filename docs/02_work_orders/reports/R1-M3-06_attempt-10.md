COMPLETED | R1-M3-06-I001 | C09 RubyGems exec COMMAND 인자 경계 교정 | exact COMMAND 현재 검증·후속 Pods/Manifest 결속·계약 Test·Progress·Attempt 10 보고서 변경 | iOS 24/24·Android 11/11·Mobile 전체·Node 286/286·Toolchain·Workflow JSON/WSL Bash·잔존물/Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 10 결과보고

## 판정

C09 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 승인 CocoaPods 설치와 격리 `GEM_HOME/GEM_PATH`를 유지하면서 현재 버전 확인, 후속 Pods 설치 2회, Manifest 버전 수집의 RubyGems 실행 형식을 모두 `gem exec -v 1.16.2 pod`로 교정했다. COMMAND 앞의 잘못된 `--`만 제거했으며 Pin, 엄격한 버전 비교, Outcome/Evidence Fail-close, 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책은 유지했다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30234129587`, Job `89878471765`, Artifact `8641046506`은 PR Head `30c1c76caaeba2154ffd20874bdbd25f6ee164c1`의 Fail-close 실행 결과다.
- Node·npm·uv와 승인 CocoaPods `1.16.2` 및 45개 Gem 설치는 성공했다.
- 설치 뒤 첫 `gem exec -v 1.16.2 -- pod --version`은 RubyGems가 실행할 COMMAND를 받지 못해 `Please specify an executable to run (Gem::CommandLineError)`로 종료했다.
- RubyGems exec의 COMMAND인 `pod`를 옵션 뒤에 직접 전달하는 `gem exec -v 1.16.2 pod` 형식으로 현재 확인·두 install·Manifest를 동일하게 결속했다.
- 현재 Step은 실제 버전 출력을 로그에 남기고 `1.16.2`와 엄격 비교한다. Manifest 실행 실패는 기존 `|| true`와 Evidence Writer의 unknown/INCOMPLETE 판정으로 성공 승격되지 않는다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: CocoaPods 현재 버전 확인 1회, 후속 Pods 버전 확인 1회와 install 2회, Manifest 버전 수집 1회에서 COMMAND 앞 `--`만 제거.
- `scripts/tests/ios-native-shell.test.mjs`: exact COMMAND 직접 전달, 실제 `1.16.2` 승인·`1.17.0` 거부, 후속 install 2회, Manifest 동일 형식 및 잘못된 `-- pod`·직접 Script·Wrapper·일반 `pod` Fallback 금지 계약.
- Progress와 본 Attempt 10 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C09 RED | iOS Gate 22/24 PASS·2 FAIL: 기존 5개 실행의 잘못된 `-- pod` 형식 재현 |
| C09 GREEN | iOS Gate 24/24 PASS |
| 승인·부정 버전 | 실제 출력 `1.16.2` 승인, `1.17.0` 거부, 현재 버전 로그·엄격 비교 PASS |
| exact 실행 형식 | 버전 실행 3회, install 2회 모두 `gem exec -v 1.16.2 pod`; 잘못된 `-- pod`·직접 Script·Wrapper·일반 pod Fallback 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 24/24, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 286/286 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, iOS Test `node --check` PASS |
| 잔존물 | Repository Gem Directory 0, Test Temp Fixture 0, Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 구현 및 검증 중 예상하지 못한 오류는 없었다. TDD RED 22/24는 승인 C09가 지정한 기존 COMMAND 인자 경계 결함을 재현한 결과이며, Workflow의 다섯 실행에서 `--`만 제거한 뒤 동일 Gate가 24/24로 전환됐다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 CocoaPods 버전 선택, Pods, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
