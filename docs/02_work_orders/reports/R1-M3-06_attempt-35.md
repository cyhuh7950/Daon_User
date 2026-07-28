COMPLETED | R1-M3-06-I007 | C34 exact Label Query와 Element Hittable 평가 분리 | Notifications 행 Helper·계약 Test·Progress·Attempt 35 | RED 36/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS revoke·grant-again Query/Stage Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 35 결과보고

## 판정

C34 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. exact English/Korean Label Query 생성과 Element `isHittable` 평가를 분리하고, Hittable 전체 후보가 정확히 1개일 때만 반환하도록 기존 Fail-close 의미를 보존했다. failure count는 0이다.

## 판단 이유

- exact Head `9a2451c0c19be7f4a23e4a5ad869a39dbaaca28b`의 iOS Run `30272461997`은 Build·일반 UI를 통과했지만 revoke에서 `CODE=STAGE_SETTINGS_NOTIFICATION_ROW PHASE=revoke EXIT=65`로 종료됐다.
- C33의 첫 추가 Marker `SETTINGS_NOTIFICATION_QUERY_CREATED`가 없으므로 실패 경계는 Helper 진입 뒤 `isHittable` 결합 Query 생성식 안으로 좁혀졌다.
- Label exact Match는 Query에 유지하고 동적 `isHittable` 속성은 Query가 반환한 전체 Element 배열에서 평가하면 후보 선택 의미를 추가하지 않고 같은 단일 행 계약을 보존할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - Query Predicate를 `label == %@ OR label == %@`로 제한하고 `isHittable`을 제거했다.
  - Query 생성 직후 기존 `SETTINGS_NOTIFICATION_QUERY_CREATED` Marker를 유지했다.
  - 대기는 `allElementsBoundByAccessibilityElement` 전체를 `isHittable`로 filter한 배열이 비지 않을 때까지 수행한다.
  - 대기 뒤 전체 Hittable Element를 다시 수집하고 `count == 1`을 먼저 검증한 뒤, 단일 배열의 `popLast()`로 비선택 추출한다.
  - 0개 또는 2개 이상은 기존 Assertion 문구와 Error로 Fail-close한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - Predicate의 `isHittable` 금지, Label-only Query, 전체 Element Hittable filter, 대기 뒤 재수집, count 1 선검증과 비선택 추출을 고정했다.
  - C33 Marker 순서, tap 직전 Marker, 금지 타입·identifier·부분문자열·좌표·Index 계약을 유지했다.
- Progress와 본 Attempt 35 보고서.
- 미변경: Simulator Script, Product, Android, Alert/Timeout/Switch, 세 Phase XCTest Method, Workflow, Quality, Package/Lock, Project 설정.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C34 RED | iOS 계약 36/37 PASS·1 FAIL: 기존 결합 Predicate에서 예상 실패 |
| 첫 GREEN | 36/37 PASS: Swift `.contains`가 기존 Predicate `CONTAINS` 금지 검사에 부분 오탐 |
| 두 번째 GREEN | 36/37 PASS: 배열명 `matches`가 기존 Predicate `MATCHES` 금지 검사에 부분 오탐 |
| C34 GREEN | 금지 검사 완화 없이 `filter`/`isEmpty`와 `hittableElements`로 정합화 후 37/37 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Workflow YAML Parse 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Helper·계약 Test·Progress 외 추적 Diff 0; Simulator Script·Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·보안 경계 보존 근거

- English/Korean exact Label, `.any` 타입 비의존 범위, Hittable 후보 전체 평가를 유지한다.
- `firstMatch`, `element(boundBy:)`, 좌표, Index, 임의 타입·identifier·부분문자열 우선순위를 추가하지 않았다.
- C33의 Query 생성→대기 완료→count 1→element 준비→tap 직전 Marker 순서와 행 tap을 유지했다.
- Raw Accessibility Tree·Label Dump·좌표·Index·환경값 출력은 추가하지 않았다.
- Simulator Script와 Assertion Code 우선순위, 원 Phase·Exit 보고는 변경하지 않았다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA의 macOS iOS Workflow에서 revoke·grant-again을 실행해 Query 생성 이후 마지막 성공 Stage와 원 Phase·Exit를 확인한다.
3. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
