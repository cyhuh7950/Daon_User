COMPLETED | R1-M3-06-I007 | C24 Simulator Permission 함수 내부 실패 표식 구현 | Bash errtrace·권한 서비스 allowlist·Fixture·Progress·Attempt 25 생성 | iOS 40/40·Mobile 전체·Android 11/11·Node 303/303·Toolchain·Workflow/Bash·Diff PASS | 새 exact-SHA에서 실제 실패 서비스 미확인 | 어울1의 Commit·Push와 macOS CI 서비스 표식 판정

# R1-M3-06 Attempt 25 결과보고

## 판정

C24 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Simulator Shell의 기존 `errexit`·`nounset`·`pipefail`에 Bash `errtrace`를 추가하고, Permission Phase에서 현재 `simctl privacy` 서비스만 고정 allowlist 표식으로 출력하도록 했다. 실패는 성공으로 바꾸지 않으며 원 Exit와 EXIT Cleanup을 보존한다. 정식 `FAILURE_REPORT`가 아니며 failure count는 0이다.

## 판단 이유

- exact Head `c07fbf50f7cf660f29cdfd6587a0161e02b6429c`의 Quality Gate Run `30252727785`는 성공했다.
- iOS Run `30252726337`은 unsigned Build와 System Open UI Test가 성공한 뒤 Simulator Shell의 첫 Permission Phase에서 `NSPOSIXErrorDomain code=1`, `Failed to set access`, `Operation not permitted`로 실패했다.
- C23 ERR Trap은 함수 내부로 상속되지 않아 단계 표식도 출력하지 못했고 camera·microphone·notifications 중 최초 실패 서비스를 구분할 수 없었다.
- `errtrace`와 명령 직전 고정 서비스 상태만 추가하면 Product·권한 동작·명령 순서를 바꾸지 않고 다음 exact-SHA 실행에서 실패 경계를 식별할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/ci/verify-simulator.sh`
  - `set -Eeuo pipefail`로 기존 세 안전 옵션을 유지하면서 ERR Trap 함수 상속을 활성화.
  - `camera|microphone|notifications`만 허용하는 서비스 allowlist 추가.
  - 각 `simctl privacy` 직전에 현재 서비스를 기록하고, 세 명령 뒤 XCTest 실행 전 빈 값으로 초기화.
  - Permission 실패에서만 기존 단계·원 Exit와 함께 `DAON_SIM_FAILED_PERMISSION_SERVICE=<allowlisted service>` 출력.
  - 비권한 실패와 allowlist 밖 값에는 서비스 표식을 출력하지 않음.
- `scripts/tests/ios-native-shell.test.mjs`
  - 함수 내부 Permission 실패 Exit 23의 단계·서비스·Exit 정확 3줄과 후속 실행 차단 검증.
  - 비권한 실패 및 임의 서비스 값의 서비스 무표식, 원 Exit 보존 검증.
  - privacy 서비스 순서·명령 1:1·XCTest 전 초기화와 retry/ignore 부재 검증.
- Progress와 본 Attempt 25 보고서.
- 미변경: Product Source, XCTest, Native/Bridge, Info.plist/Xcode Project, Workflow/Evidence Writer, Android, 권한 서비스·순서·재시도, Signing, Lockfile와 Toolchain Pin.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C24 RED | iOS 39/40 PASS·1 FAIL: `errtrace`와 서비스 상태 부재 재현, 기존 39개 PASS |
| C24 GREEN | iOS 40/40 PASS |
| Permission 실패 Fixture | 함수 내부 `microphone` 실패에서 단계·서비스·Exit 정확 3줄, Exit 23 유지, 후속 실행 0 PASS |
| 비권한·비허용 Fixture | `INSTALL` 실패 및 `PRIVATE_DIAGNOSTIC` 값은 기존 단계·Exit 2줄만 출력, 서비스/임의 값 노출 0 PASS |
| 원 계약 보존 | camera→microphone→notifications 각 1회·기존 명령/인자/순서 유지, retry/ignore 0, XCTest 전 서비스 초기화, EXIT Cleanup 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 40/40, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C23과 동일 |
| 전체 Node | 303/303 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; 허용 파일 외 Product/XCTest/Native/Bridge/Info/Project/Workflow/Evidence/Android/Lock/Pin Diff 0; Signing 변경 0; Pods/Build/Artifact 잔존 0 |

### 오류·복구 근거

- RED 39/40은 승인 C24의 함수 내부 ERR 상속·서비스 식별 부재를 재현한 예상 실패이며 기존 계약 39개는 모두 통과했다.
- 구현·Portable 회귀 중 기능 오류는 없었다.
- `verify:mobile` 중 읽기 권한이 없는 기존 `services/local-service/.pytest_cache` 탐색 경고가 있었으나 Exit 0이고 관련 파일 변경은 없다.
- Windows Fixture는 ERR Trap 상속·표식·Exit 보존 계약을 검증하지만 실제 macOS Simulator의 실패 서비스는 다음 exact-SHA 실행 전에는 확정할 수 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료 확인 뒤 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행한다.
3. Simulator Step 실패 시 `DAON_SIM_FAILED_STAGE`, 선택적 `DAON_SIM_FAILED_PERMISSION_SERVICE`, `DAON_SIM_FAILED_EXIT`로 최초 실패 서비스를 확정한다.
4. 실패 서비스 확인 뒤 서비스 제거·우회 없이 기존 Artifact와 Apple Simulator 환경 원인을 대조한다.
5. Simulator Verification과 Evidence Manifest까지 성공하면 Phase A를 `SIMULATOR_VERIFIED_PENDING_SIGNING_DEVICE`로 판정한다.
6. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
