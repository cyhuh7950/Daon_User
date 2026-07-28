COMPLETED | R1-M3-06-I007 | C33 Settings 알림 행 처리 경계 Stage 세분화 | Query 생성·대기·count 1·element 준비·tap 직전 Marker·Bash allowlist/parser·계약 Test·Progress·Attempt 34 | RED 42/44→GREEN 44/44·Mobile 전체·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS revoke·grant-again Stage Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 34 결과보고

## 판정

C33 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. C31의 exact Label·Hittable·count 1 Selector와 기존 Permission 동작을 유지한 채 Query 생성, 대기 완료, count 1 통과, element 준비, 행 tap 직전의 마지막 성공 경계를 고정 Allowlist Stage로 세분화했다. failure count는 0이다.

## 판단 이유

- exact Head `992d4679dbc2369d5df5db9356a74eede597ecd4`의 iOS Run `30269316851`은 Build와 일반 UI를 통과했지만 Permission revoke에서 `CODE=STAGE_SETTINGS_NOTIFICATION_ROW PHASE=revoke EXIT=65`로 종료됐다.
- C31 Result에는 `SETTINGS_NOTIFICATION_ROW_MISSING` Assertion Code가 없으므로 exact Selector 실패로 단정할 근거가 없었다.
- 기존 Marker는 Helper 호출 직전까지만 증명하므로 Query 생성부터 tap까지 어느 연산에서 종료됐는지 구분할 수 없었다. Selector를 다시 추측 변경하지 않고 성공한 처리 경계만 세분화하는 것이 승인된 진단 범위다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `SETTINGS_NOTIFICATION_QUERY_CREATED`, `SETTINGS_NOTIFICATION_QUERY_WAIT_COMPLETED`, `SETTINGS_NOTIFICATION_COUNT_SINGLE`, `SETTINGS_NOTIFICATION_ELEMENT_READY`, `SETTINGS_NOTIFICATION_ROW_TAP_PENDING` 다섯 Stage를 추가했다.
  - 각 Marker는 대응 연산이 성공한 뒤에만 출력한다. tap 직전 Marker는 기존 `isHittable` Assertion 통과 뒤 `notificationsRow.tap()` 바로 전에 둔다.
  - C31 exact Predicate `(label == %@ OR label == %@) AND isHittable == true`, English/Korean Label, `query.count == 1` Fail-close, 반환 element와 tap 동작은 유지했다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - 다섯 Stage의 `STAGE_<ALLOWLIST>` 변환을 Allowlist/parser에 추가했다.
  - 기존 Assertion Code 탐색 후에만 Stage를 사용하는 우선순위와 원 Phase·Exit 보고를 유지했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - Marker 순서·성공 경계, 최신 허용 Stage 변환, Assertion Code 우선, C31 Selector·금지 Query 불변 계약을 추가했다.
  - C33 element 준비 Marker를 허용하기 위해 기존 직접 반환 문구 검사를 count 선검증→동일 element 접근→Marker→return의 동등 계약으로 정합화했다.
- Progress와 본 Attempt 34 보고서.
- 미변경: Product, Android, Alert/Timeout/Switch, 세 Phase 고정 XCTest Method, Workflow, Quality, Package/Lock, Project 설정.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C33 RED | iOS 계약 42/44 PASS·2 FAIL: 신규 Stage와 Bash 변환 부재를 예상대로 재현 |
| 첫 GREEN | 43/44 PASS: 신규 Stage/Bash는 PASS, 기존 직접 반환 문구 계약 1건만 실패 |
| C33 GREEN | iOS 계약 44/44 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Workflow YAML Parse 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; 승인된 구현·Test·Progress 외 추적 Diff 0; C31 Predicate/count와 금지 Query·Raw Tree/Text/Label/좌표/Index 진단 추가 0 |

### 동작·보안 경계 보존 근거

- Assertion 문구가 존재하는 Fixture에서는 최신 Stage가 있어도 `SETTINGS_NOTIFICATION_ROW_MISSING`이 먼저 보고된다.
- 허용되지 않은 Stage는 기존처럼 `UNKNOWN_XCTEST_FAILURE`로 Fail-close한다.
- 새 출력은 고정 Stage 식별자뿐이며 접근성 Tree, 실제 Label, 좌표, Index, 환경값을 출력하지 않는다.
- `verify:mobile`의 `.pytest_cache` 접근 경고는 기존 scanner 경고이며 전체 명령 Exit 0, 관련 변경 0이다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA의 macOS iOS Workflow에서 revoke·grant-again을 실행해 마지막 `STAGE_*`, 원 `PHASE`, `EXIT`를 확인한다.
3. Assertion Code가 있으면 이를 우선 판정하고, 없으면 마지막 성공 Stage 다음 연산만 근거로 후속 교정 범위를 결정한다.
4. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
