COMPLETED | R1-M3-06-I007 | C29 Permission Phase 고정 XCTest 진입점 구현 | 고정 Method 3종·공통 Helper·Shell exact 매핑·환경 비의존 Fixture·Progress·Attempt 30 | 관련 36/36·iOS 43/43·Mobile 전체·Android 11/11·Node 306/306·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS 세 Phase XCTest Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 30 결과보고

## 판정

C29 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. Permission Phase를 환경 상속에 의존하지 않는 고정 XCTest 진입점 3종으로 분리하고, Shell의 고정 Phase를 허용된 Method 이름에 exact 매핑했다. C28 환경 Guard와 전용 Marker는 제거했으며 `PHASE_EXPECTED_BINDING`과 `PHASE_EXPECTED_MATCHED`는 고정 Helper 입력 결속 전·후에 유지했다. `APP_LAUNCH_ROOT` 이후 Assertion·Selector·Timeout·검증 순서·권한·제품 코드는 변경하지 않았다. failure count는 0이다.

## 판단 이유

- exact Head `b8960d2493ac08d58d9cc970de8d5b550e657ec8`의 iOS Run `30262705084`은 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test를 통과했다.
- Permission Step은 `grant-initial` Exit 65와 `STAGE_PHASE_EXPECTED_BINDING`만 남겼고, C28의 다음 `PHASE_ENV_PRESENT`가 없었다. 따라서 XCTest Process에 `DAON_PERMISSION_PHASE`가 존재하지 않는 첫 환경 Guard 종료가 확정됐다.
- Alert·Settings·Product 실행 전 실패이므로 해당 Selector·Timeout·권한 동작이나 제품 코드를 수정할 근거는 없다.
- 승인 설계는 Runtime 문자열·동적 Selector 대신 세 고정 진입점과 Shell allowlist exact 매핑을 요구한다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `testPermissionGrantInitial`, `testPermissionRevoke`, `testPermissionGrantAgain` 고정 진입점 추가.
  - 각 Method가 공통 Private Helper에 각각 `.grantInitial/GRANTED`, `.revoke/DENIED`, `.grantAgain/GRANTED`를 Literal로 전달.
  - `ProcessInfo`의 `DAON_PERMISSION_PHASE`·`DAON_PERMISSION_EXPECTED` Guard와 C28 전용 Marker 4종 제거.
  - 공통 Helper에서 `PHASE_EXPECTED_BINDING` 뒤 Phase-Expected 일치를 검증하고 성공 뒤 `PHASE_EXPECTED_MATCHED` 유지.
  - Swift diff는 진입점·환경 Guard 제거·전용 Marker Enum 제거에 한정되며 `APP_LAUNCH_ROOT` 이후 시나리오 실행문 변경 0건.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - `grant-initial`, `revoke`, `grant-again`을 세 고정 XCTest Method에 `case` exact 매핑하고 그 변수만 `-only-testing`에 전달.
  - 허용되지 않은 Phase는 Exit 64이며 XCTest를 실행하지 않음.
  - C28 전용 Stage allowlist 제거. 기존 동일 설치, 세 Phase 순서, camera/microphone privacy, 실제 notification Alert·Production Settings OFF/ON, Phase별 Raw Log·xcresult, 원 Exit·단일 안전 Annotation 계약 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 세 고정 Method와 고정 Helper 입력, 환경변수 비의존, Shell exact 매핑을 정적·실행 Fixture로 검증.
  - 세 정상 Phase가 각각 정확히 한 Method만 실행하고 잘못된 Phase가 Exit 64·XCTest 0회를 만드는 계약 추가.
  - 기존 Assertion 우선·Stage 차선·Unknown 최종·Raw 비노출·원 Exit·단일 Annotation 회귀 유지.
- Progress와 본 Attempt 30 보고서.
- 미변경: Product Native Host·Bridge·공용 Mobile Source, Xcode Project·Info·Workflow/Runner, Quality Workflow·정책·제품 코드, Android, Package/Lockfile, Signing.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C29 RED | 관련 계약 32/36 PASS·4 FAIL: 고정 진입점 부재와 기존 환경변수·단일 Method 계약을 예상대로 재현 |
| 첫 GREEN | 35/36 PASS: C29 신규 계약은 PASS, 기존 상위 CI Test만 제거된 Shell Expected 세 번째 인자를 요구 |
| 최종 GREEN | 상위 Test 호출 형식만 두 인자로 정합화 후 36/36 PASS |
| Phase exact Fixture | 세 정상 Phase별 정확히 한 고정 Method PASS; 잘못된 Phase Exit 64·XCTest 미실행 PASS |
| 기존 안전 분류 | 승인 Assertion Code 우선, 마지막 허용 Stage 차선, Unknown 최종, 단일 Annotation·원 Exit·Raw Log 계약 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 43/43, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C28과 동일 |
| 전체 Node | 306/306 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON PASS, Git Bash 기준 iOS CI Bash 3/3 PASS, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; C28 Production 환경변수·전용 Marker 잔존 0; 보호 Product/Native/Bridge/Project/Workflow Diff 0 |

### 오류·복구 근거

- RED 4건은 승인 C29 계약을 선고정한 예상 실패이며 나머지 32개 기존 계약은 모두 통과했다.
- 첫 GREEN의 1건은 제거된 Shell Expected 세 번째 인자를 요구한 기존 Test 호출의 계약 불일치였다. 기능 코드 추가 변경 없이 해당 Test 호출만 두 인자로 정합화했다.
- 최초 Bash 구문 확인은 Windows `bash.exe`가 WSL `CreateInstance/E_ACCESSDENIED`로 실행되지 않았고 PowerShell이 뒤 문구를 계속 출력했다. 이 출력은 근거에서 제외하고 설치된 Git Bash를 사용해 세 파일을 각각 Exit 0으로 재검증했다.
- `verify:mobile`의 기존 `.pytest_cache` 접근 경고는 Scanner가 Skip한 뒤 Exit 0이었고 관련 변경은 없다.
- C29 지시에 따라 C28에서 환경 차단이 확인된 Quality 로컬 Gate는 재실행하지 않았고 Workflow·정책·제품 코드를 변경하지 않았다.
- Windows Portable 검증은 실제 macOS Xcode 26.6 Permission Runtime을 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료를 확인하고 변경을 Commit·Push한다.
2. 새 exact SHA의 macOS Xcode 26.6 Workflow에서 세 고정 XCTest Method가 grant-initial→revoke→grant-again 순서로 실행되는지 확인한다.
3. 실제 System Alert 승인, Production Settings 알림 Switch OFF/ON, 앱 복귀와 세 xcresult·Evidence Manifest를 판정한다.
4. 새 실패가 발생하면 Assertion 우선·Stage 차선 Annotation과 Raw Artifact로 위치를 확정하고 Selector·제품 동작을 추측 수정하지 않는다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
