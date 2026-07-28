COMPLETED | R1-M3-06-I007 | C23 Simulator Shell 단계 실패 표식 구현 | allowlist 단계 상태·ERR 단계/Exit 표식·Fixture·Progress·Attempt 24 생성 | iOS 39/39·Mobile 전체·Android 11/11·Node 302/302·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA Simulator 실패 단계 미확인 | 어울1의 Commit·Push와 macOS CI 단계 표식 판정

# R1-M3-06 Attempt 24 결과보고

## 판정

C23 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Simulator Shell에 allowlist 현재 단계와 ERR 진단 경계를 추가해 명령 실패 시 `DAON_SIM_FAILED_STAGE=<allowlisted stage>`와 `DAON_SIM_FAILED_EXIT=<numeric>` 두 표식만 진단 경계에서 출력하고 원 Exit로 종료하도록 했다. 원 명령, `set -euo pipefail`, EXIT Cleanup과 기능 흐름은 변경하지 않았다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `7973e5def59ca41396d1eabec2172c0b5a8586d5`의 Quality Gate Run `30251018326`은 성공했다.
- iOS Run `30251018373`은 Build와 System Open Deep Link Test를 포함한 UI Test Step이 성공했으나 후속 Simulator Step만 17초 안에 Exit 1로 종료했다.
- C22에서 Shell Deep Link Loop가 제거됐으므로 초기 App Artifact·Boot·Install·Route Clear·Launch·Home Ready 또는 Permission 진입 경계의 실패 가능성이 남았지만, 인증 만료로 상세 GitHub Log를 확인할 수 없었다.
- Product 동작을 다시 변경하지 않고 현재 단계와 원 Exit만 출력하면 다음 exact-SHA 실행에서 실패 명령 구간을 안전하게 분리할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - `INITIALIZE`, 실행 단계 21개와 `UNCLASSIFIED`만 허용하는 단계 allowlist 추가.
  - ERR Trap이 현재 값도 allowlist로 재검증하고 실패 단계·숫자 Exit 두 줄만 stderr에 출력한 뒤 동일 Exit로 종료.
  - App Artifact, Boot Status, Install, Initial Terminate, Route Clear, Launch, Home Ready, Permission 3단계, Lifecycle Appearance/Terminate/Relaunch/Ready/State, Final Log Capture/Scan, Binary Scan, Final Terminate/Process Check, Status Write 앞에 단계 할당.
  - 기존 원 명령과 순서·인자·Exit·`set -euo pipefail`·EXIT Cleanup은 그대로 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 단계 21개 할당과 allowlist/fallback, 표식 형식 및 민감 Context 비포함 정적 계약.
  - `INSTALL` 중간 실패 Exit 23 Fixture의 정확한 stderr 두 줄·원 Exit·후속 실행 차단 계약.
  - 성공 Fixture의 실패 표식 0건과 후속 진행 계약.
- Progress와 본 Attempt 24 보고서.
- 미변경: Product Source, XCTest, Native/Bridge, Info.plist/Xcode Project, Workflow/Evidence Writer, Android, Wait/Retry, 권한/Lifecycle 동작, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C23 RED | iOS 38/39 PASS·1 FAIL: 단계 상태와 ERR Trap 부재 재현 |
| C23 GREEN | iOS 39/39 PASS |
| 실패 Fixture | `INSTALL` 실패에서 `DAON_SIM_FAILED_STAGE=INSTALL`, `DAON_SIM_FAILED_EXIT=23`만 stderr 출력, Exit 23 유지, 후속 실행 0 PASS |
| 성공 Fixture | Exit 0, 실패 표식 0, 후속 진행 PASS |
| 원 계약 보존 | 원 명령 Diff 0, `set -euo pipefail`, EXIT Cleanup, Home Wait 20회, Permission 3단계, Lifecycle·Crash/Secret·Binary·종료 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 39/39, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C22와 동일 |
| 전체 Node | 302/302 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; 허용 파일 외 Product/XCTest/Native/Bridge/Info/Project/Workflow/Evidence/Android/Lock/Pin Diff 0; Signing 변경 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- RED 38/39는 승인 C23의 단계 진단 부재를 재현한 예상 실패이며 기존 iOS/Evidence/Deep Link 계약 38개는 모두 통과했다.
- 구현·Portable 회귀 중 기능 오류는 없었다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 Exit 0이고 관련 파일 변경은 없다.
- Windows Fixture는 ERR Trap·Exit 보존 계약을 검증하지만 실제 macOS Simulator의 새 실패 단계는 다음 exact-SHA 실행 전에는 확정할 수 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행한다.
3. Simulator Step 실패 시 두 `DAON_SIM_FAILED_*` 표식으로 최초 실패 단계를 확정하고 해당 단계의 기존 Artifact만 대조한다.
4. Simulator Verification과 Evidence Manifest까지 성공하면 Phase A를 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 판정한다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
