COMPLETED | R1-M3-06-I001 | C05 CocoaPods 승인 버전 격리 실행 | RUNNER_TEMP Gem Home/Bin·현재/후속 PATH/GEM 결속·계약 Test·Progress·Attempt 6 보고서 변경 | iOS 22/22·Android 11/11·Mobile 전체·Node 284/284·Toolchain·Workflow JSON/WSL Bash·잔존물/Diff PASS | 새 exact-SHA macOS CI·Signing/실기기 미수행 | 어울1의 Commit·Push와 새 exact-SHA macOS CI·Artifact 판정

# R1-M3-06 Attempt 6 결과보고

## 판정

C05 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 승인 CocoaPods `1.16.2`를 `${RUNNER_TEMP}` 전용 Gem Repository/Bin에 설치하고 현재 Step과 후속 Step 모두 전용 `pod`를 우선 선택하도록 보정했다. Runner 선설치 Gem은 삭제·덮어쓰기하지 않았고 기능 Source·Android Native·Signing·Toolchain/Quality/Security 정책과 Evidence Writer는 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니고 failure count는 0이다.

## 판단 이유

- Run `30231012154`, Job `89869787656`은 Node/npm/uv까지 통과했고 `gem install cocoapods -v 1.16.2`도 설치 성공했지만 직후 버전 확인에서 실패했다.
- Runner 선설치 `pod 1.17.0`이 PATH에서 새로 설치한 승인 `1.16.2`보다 우선된 실행 파일 선택 충돌이었다.
- Artifact `8640093821`은 실패를 기록하고 후속 Build·Simulator·XCTest를 실행하지 않아 Fail-close는 유지됐다.
- 전용 Gem Home과 Bin을 `${RUNNER_TEMP}/daon-cocoapods-1.16.2` 아래로 제한하고 `gem install`에 `--install-dir`·`--bindir`를 명시했다.
- 설치 전에 `gem env path`를 보존한 뒤 `GEM_PATH=전용 Home:기존 기본 경로`로 결합한다. 전용 Bin은 현재 `PATH` 선두와 후속 `$GITHUB_PATH`에, `GEM_HOME/GEM_PATH`는 현재 환경과 `$GITHUB_ENV`에 기록한다.
- 현재 Step은 `command -v pod`의 전용 Bin 선택과 `pod 1.16.2`를 확인하고, 별도 후속 Pods Step도 `pod 1.16.2`를 다시 확인한 뒤 설치한다.

## 조치

### 변경 범위

- `.github/workflows/release-1-ios-phase-a.yml`: CocoaPods 격리 설치·현재/후속 PATH/GEM 결속·후속 버전 확인.
- `scripts/tests/ios-native-shell.test.mjs`: Runner `pod 1.17.0`과 격리 `1.16.2` Fixture로 현재·후속 Step 선택, 기존 Gem 경로 보존, Runner 삭제 금지, Manifest 원문 유지 계약 추가.
- Progress와 본 Attempt 6 보고서.
- 미변경: `apps/mobile/src/**`, `apps/mobile/android/**`, `apps/mobile/ios/ci/write-evidence.mjs`, `toolchain-versions.json`, `quality-gate-policy.json`, Signing Asset, 공개 API·데이터·보안 경계.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C05 RED | iOS Gate 21/22 PASS·1 FAIL: RUNNER_TEMP 격리 계약 부재 재현 |
| C05 GREEN | iOS Gate 22/22 PASS |
| 현재/후속 Step Fixture | Runner `pod 1.17.0`보다 격리 `pod 1.16.2` 선택 PASS; 기존 `/existing/default/gems` GEM_PATH 보존 PASS |
| Manifest·Fail-close | 실제 `pod --version` 원문 보존, cocoapods Outcome과 실패/Skip 기존 계약 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 9/9, Contract 15/15, Android 11/11, iOS 22/22, Bundle PASS |
| Android·iOS Bundle | 927,127 bytes SHA-256 `5932DA46331CAEF7A3DBE1711FA61D36DE7D8DE12544E23D736037F1E6C1A5ED`; 921,015 bytes SHA-256 `CFA44AE6E533E262FEC9A8854951DC1062EFD922B527D5AFDBB1DAA95A30AC56` |
| 전체 Node | 284/284 PASS |
| Toolchain | `npm run verify:toolchain` PASS: 7 npm manifests, exact pins, lockfiles |
| Workflow·Script Syntax | Workflow JSON Parse PASS, WSL Bash 내장 Script 9개 `bash -n` PASS, Test `node --check` PASS |
| 잔존물 | Repository Gem Directory 0, Test Temp Fixture 0, Signing Asset 0 |
| Diff 경계 | `git diff --check` PASS, 기능 Source·Android Native·Evidence Writer·Toolchain Pin·Quality Gate Policy Diff 0 |
| macOS Native Build·Simulator | 새 SHA에서 미실행; 성공 주장 없음 |

### 오류·복구 근거

- 개발 중 반복 오류나 범위 우회는 없었다. 신규 계약 Test가 기존 전역 설치 방식을 RED 1건으로 재현했고, Workflow의 CocoaPods Step과 후속 Pods 버전 확인만 보정한 뒤 GREEN으로 전환됐다.

### 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push해야 한다.
2. 새 exact SHA로 GitHub macOS Workflow를 재실행하고 CocoaPods 격리 선택, Pods, Build, XCTest, Simulator Verification, Evidence Manifest를 판정해야 한다.
3. 새 macOS CI 성공 전에는 iOS Native Build·Simulator 완료로 판정할 수 없다.
4. Apple Signing·실기기 Phase B는 별도 승인 후속이다.
5. Commit·Push·PR·Merge·GitHub 재실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
