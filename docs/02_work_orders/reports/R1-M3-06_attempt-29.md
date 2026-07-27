COMPLETED | R1-M3-06-I007 | C28 Permission Phase 입력 결속 Marker 세분화 | 성공 경계 Marker 5종·Shell Allowlist·계약 Test·Quality 로컬 진단·Progress·Attempt 29 | 관련 35/35·iOS 42/42·Mobile 전체·Android 11/11·Node 305/305·Toolchain·Workflow/Bash·Diff PASS; Quality NOT_REPRODUCED_LOCALLY/ENVIRONMENT_BLOCKED | 실제 macOS 실패 Guard와 Quality 실패 항목 미확인 | 어울1의 Commit·Push·exact-SHA macOS CI 및 별도 Quality 증거 판단

# R1-M3-06 Attempt 29 결과보고

## 판정

C28 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 `PHASE_EXPECTED_BINDING` 다음의 Phase 환경값 존재·허용값 변환, Expected 환경값 존재·허용값, Phase-Expected 일치의 다섯 성공 경계를 고정 Marker로 세분화했다. `APP_LAUNCH_ROOT`와 이후 Marker·Assertion·Selector·Timeout·검증 순서·권한 동작은 변경하지 않았다. 지정 Quality 로컬 진단은 환경 `EPERM`으로 `NOT_REPRODUCED_LOCALLY / ENVIRONMENT_BLOCKED`이며, 어울1 판단에 따라 정식 `FAILURE_REPORT`나 전체 `BLOCKED`로 분류하지 않는다. failure count는 0이다.

## 판단 이유

- exact Head `e35164675e1aaf2b145d84d830dfd86aa501ccfd`의 iOS Run `30261307378`은 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test가 성공했다.
- Permission Step은 `grant-initial`에서 Exit 65이며 Annotation은 `CODE=STAGE_PHASE_EXPECTED_BINDING PHASE=grant-initial EXIT=65`다.
- 이 Marker와 `APP_LAUNCH_ROOT` 사이에는 Phase 존재·변환, Expected 존재·허용, 상호 일치의 다섯 입력 검증이 있어 Product 실행 전 어느 경계에서 실패했는지 추가 분리가 필요했다.
- 같은 SHA의 Quality Run `30261307332`은 공통 Gate에서 두 번째 연속 Exit 1이지만 공개 세부 실패가 없었다. C28은 로컬 재현·보고만 수행하고 Workflow·정책·제품 코드를 수정하지 않는 경계다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - 기존 `PHASE_EXPECTED_BINDING`을 유지하고 각 성공 경계 뒤에 `PHASE_ENV_PRESENT`, `PHASE_ALLOWED`, `EXPECTED_ENV_PRESENT`, `EXPECTED_ALLOWED`, `PHASE_EXPECTED_MATCHED` 고정 Enum Marker 추가.
  - Expected 허용과 Phase-Expected 일치는 기존 `XCTAssertTrue`/`XCTAssertEqual`을 유지하고 실패 시 즉시 반환해 성공하지 않은 뒤 Marker가 출력되지 않도록 함.
  - 실제 Phase·Expected 값, 경로·UDID·URL·사용자 데이터는 출력하지 않음.
  - `let app = XCUIApplication()`부터 Permission 시나리오 끝까지 기존 실행문 변경 0건.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - 신규 다섯 `STAGE_*`를 기존 Failure Code와 Marker 추출 allowlist에만 추가.
  - 기존 Assertion 우선·마지막 exact 허용 Marker 차선·Unknown 최종, 단일 안전 Annotation, 원 Exit, Raw Log·Console·xcresult 계약 유지.
- `scripts/tests/ios-native-shell.test.mjs`
  - 다섯 Marker의 Guard 성공 후 순서, 고정 Enum 출력과 Shell allowlist를 검증.
  - 복수 신규 Marker의 마지막 `EXPECTED_ALLOWED`만 `STAGE_EXPECTED_ALLOWED`로 분류되는 Exit 65 Fixture 추가.
  - 기존 Assertion 우선, Unknown·접두사 위조, 성공·원 Exit Fixture 유지.
- Progress와 본 Attempt 29 보고서.
- 미변경: `APP_LAUNCH_ROOT` 이후 C27 Marker, C25 Alert/Settings Selector·Timeout·검증 순서·권한 동작, Product Native Host·Bridge, Workflow/Runner, Quality 정책·실행 코드, Android, Package/Lockfile, Signing.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C28 RED | 관련 계약 34/35 PASS·1 FAIL: 첫 신규 `phaseEnvironmentPresent` Marker 부재를 예상대로 재현 |
| C28 GREEN | 관련 계약 35/35 PASS |
| 성공 경계 | Phase 존재→허용, Expected 존재→허용, Phase-Expected 일치 Marker가 성공 뒤에만 순서대로 기록되는 계약 PASS |
| 마지막 신규 Marker | `PHASE_ENV_PRESENT`→`EXPECTED_ENV_PRESENT`→`EXPECTED_ALLOWED` Log에서 `STAGE_EXPECTED_ALLOWED`만 Annotation PASS |
| 기존 안전 분류 | 승인 Assertion Code 우선, Unknown 최종, Prefix 위조 거부, 단일 Annotation·Exit 65·Raw Log 계약 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 42/42, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C27과 동일 |
| 전체 Node | 305/305 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON, iOS CI Bash 3/3, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Product/Native/Bridge/Info/Project/Workflow/Quality 정책·코드/Android/Package/Lock Diff 0; Pods/Build/Artifact/DerivedData 잔존 0 |

### Quality 로컬 진단

- 실행 명령: `npm run verify:quality-gate`.
- 결과: Exit 1, `QUALITY_GATE_EXECUTION_ERROR EPERM`. 기존 `services/local-service/.pytest_cache` 읽기 권한 문제로 개별 Check 실행과 새 결과 JSON 생성 전에 중단됐다.
- 판정: `NOT_REPRODUCED_LOCALLY / ENVIRONMENT_BLOCKED`. CI의 공통 Gate Exit 1 세부 실패 항목을 로컬에서 재현·확정하지 못했다.
- 실행 전후 기존 `quality-gate-result.json` SHA-256은 `D73A718E4060D1386DB9F3805737F1D1DE3E1857547A433D31EFD980535B37A3`, Summary는 `906E5B13E7268220D47D4431CA70E0BCE0258DA37D494E61C2BDCD6467C542CE`로 불변이다.
- 기존 JSON은 Git SHA `8a9a934b4ae8e69a51ad3f630ba3eeae46458d12`, PASS/Exit 0의 과거 증거이므로 C28 또는 현재 exact-SHA 근거로 사용하지 않았다.
- 권한 확장 실행은 내부 `npm audit`가 공개 npm 서비스로 의존성 Metadata를 전송할 수 있고 사용자 명시 승인이 없어 거절됐다. 권한 확장 재시도, npm egress, Cache 삭제·권한 변경, 우회 실행, Workflow·정책·제품 변경은 수행하지 않았다.

### 오류·복구 근거

- RED 34/35는 승인 C28 계약을 선고정한 예상 실패이며 기존 34개 계약은 모두 통과했다.
- Quality EPERM은 Permission 구현 실패가 아닌 로컬 환경 차단이다. 어울1 판단에 따라 전체 작업은 계속 검증하고 Quality만 별도 상태로 기록했다.
- `verify:mobile`에서도 동일 기존 `.pytest_cache` 접근 경고가 있었으나 해당 Scanner는 Skip 후 Exit 0이었고 관련 변경은 없다.
- Windows Portable 검증은 실제 macOS Xcode 26.6 Guard Marker, Permission Runtime과 CI Quality 실패 항목을 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료를 확인하고 변경을 Commit·Push한다.
2. 새 exact SHA로 macOS Xcode 26.6 Workflow를 실행해 마지막 입력 Marker를 확인한다.
3. 끊긴 Guard가 확정된 뒤 Phase 전달 수정 필요 여부를 판단하며 전달 방식·Product·Selector를 추측 수정하지 않는다.
4. exact-SHA Quality CI에서 실패 Check와 새 증거 JSON을 확보하거나, 별도 Quality 증거 개선이 필요한지 어울1이 판단한다.
5. 세 Permission Phase와 Evidence Manifest까지 성공하면 Phase A 상태를 다음 Gate 기준으로 판정한다.
6. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
