COMPLETED | R1-M3-06-I001 | C08 RubyGems 버전 지정 CocoaPods 실행 결속 | exact gem exec 현재 검증·후속 Pods/Manifest 결속·계약 Test·Progress·Attempt 9 보고서 변경 | iOS 24/24·Android 11/11·Mobile 전체·Node 286/286·Toolchain·Workflow JSON/WSL Bash·잔존물/Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 9 결과보고

## 판정

C08 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 승인 CocoaPods 설치와 격리 `GEM_HOME/GEM_PATH`를 유지하면서 현재 버전 확인, 후속 Pods 설치 2회, Manifest 버전 수집을 모두 RubyGems 공식 버전 지정 실행 `gem exec -v 1.16.2 -- pod`로 결속했다. Gem 본체 Script의 Ruby 직접 실행, Wrapper 절대경로와 일반 `pod` Fallback은 제거했다. 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책과 Evidence 상태 의미는 유지했다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30233140401`, Job `89875651654`, Artifact `8640735155`는 PR Head `1000cee621fac4f92d7597357e1459201dce1d56`의 Fail-close 실행 결과다.
- Node·npm·uv와 승인 CocoaPods `1.16.2` 및 45개 Gem 설치는 성공했다.
- 설치 뒤 `ruby "${DAON_POD_SCRIPT}" --version`은 CocoaPods Script의 `bundler/setup`에서 격리 Gem 경로의 존재하지 않는 `Gemfile`을 요구해 `Bundler::GemfileNotFound`로 종료했다.
- RubyGems `gem exec -v 1.16.2 -- pod`는 설치된 Gem 실행 명령을 승인 버전으로 선택하고 실행 문맥을 구성하므로 Gem 본체 Script를 직접 호출하지 않는다.
- 현재 Step은 실제 버전 출력을 로그에 남기고 `1.16.2`와 엄격 비교한다. 후속 Pods Step은 같은 exact 명령으로 버전을 재확인하고 install을 두 번 수행한다.
- Manifest도 같은 exact 명령으로 실제 버전을 수집하며 실행 실패 시 빈 결과를 유지한다. 기존 Evidence Writer가 이를 unknown/INCOMPLETE로 판정하므로 성공으로 조용히 승격되지 않는다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: 현재 버전 확인, 후속 Pods 두 install, Manifest 수집을 exact `gem exec -v 1.16.2 -- pod`로 변경하고 `DAON_POD_SCRIPT`·Ruby 직접 실행 제거.
- `scripts/tests/ios-native-shell.test.mjs`: exact 버전 선택, 실제 `1.16.2` 승인·`1.17.0` 거부, 후속 install 2회, Manifest 동일 결속과 Script/Wrapper/일반 pod Fallback 금지 계약.
- `scripts/tests/ios-phase-a-evidence.test.mjs`: 경로 구현에 종속된 Test 제목을 CocoaPods 버전 증거 공백 Fail-close 의미로 정합화. Fixture 동작과 Writer는 불변.
- Progress와 본 Attempt 9 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C08 RED | iOS Gate 22/24 PASS·2 FAIL: 기존 직접 Script 실행과 exact `gem exec` 부재 재현 |
| C08 GREEN | 1차 23/24, Test 기대 정합화 후 최종 iOS Gate 24/24 PASS |
| 승인·부정 버전 | 실제 출력 `1.16.2` 승인, `1.17.0` 거부, 현재 버전 로그·엄격 비교 PASS |
| exact 실행 경로 | 현재·후속·Manifest 버전 실행 3회, 후속 install 2회 모두 `gem exec -v 1.16.2 -- pod`; 직접 Script·Wrapper·일반 pod Fallback 0건 |
| Evidence Fixture | CocoaPods 증거 공백은 `INCOMPLETE`·`toolchain:cocoapods:unknown`; 기존 setup_uv/cocoapods Outcome Fail-close PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 24/24, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 286/286 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, Test 2개 `node --check` PASS |
| 잔존물 | Repository Gem Directory 0, Test Temp Fixture 0, Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- Workflow 최소 보정 후 첫 GREEN은 23/24였다. 새 Manifest의 `gem exec ... || true`는 실행 실패 시 명령 치환 결과가 자연스럽게 빈 값이 되지만 Test가 이전 구현의 선행 `IOS_COCOAPODS_VERSION=""` 대입을 계속 요구한 기대 불일치였다. 실행·Evidence 의미를 바꾸지 않고 exact 수집 명령과 Writer unknown/INCOMPLETE Fixture를 직접 검증하도록 중복 기대만 제거한 뒤 24/24를 확인했다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 승인 `gem exec` 버전 선택, Pods, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
