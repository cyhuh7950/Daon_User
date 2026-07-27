COMPLETED | R1-M3-06-I007 | C35 Notifications Hittable 후보 ZERO·AMBIGUOUS 고정 분류 | Assertion 분기·Bash Allowlist/parser·계약 Test·Progress·Attempt 36 | RED 35/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS ZERO·AMBIGUOUS Runtime 분류 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 36 결과보고

## 판정

C35 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. C34의 exact Label Query, Hittable 전체 필터와 1건 성공 경로를 변경하지 않고, 대기 뒤 후보가 0건인지 2건 이상인지 고정 Assertion Code로 분리했다. failure count는 0이다.

## 판단 이유

- exact Head `8049fd97020948d026485b661e816925797abb30`의 iOS Run `30275056750`은 Build·일반 UI를 통과했지만 revoke에서 `CODE=SETTINGS_NOTIFICATION_ROW_MISSING PHASE=revoke EXIT=65`로 종료됐다.
- C34 이전의 Query 생성 Stage 오류가 generic count Fail-close로 진전했으므로 Label-only Query와 Element Hittable 평가는 실행됐지만 최종 후보가 0건인지 2건 이상인지는 알 수 없었다.
- Selector를 다시 추측 변경하기 전에 실제 숫자나 Accessibility Tree를 노출하지 않는 고정 분류만 추가해야 다음 기술 판단의 근거가 된다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - Hittable 배열이 비면 기존 generic 문구에 고정 `[ZERO]` 접미사를 붙여 Fail-close한다.
  - 배열이 비지 않고 `count != 1`이면 고정 `[AMBIGUOUS]` 접미사로 Fail-close한다.
  - 정확히 1건이면 기존 `SETTINGS_NOTIFICATION_COUNT_SINGLE` Marker, `popLast()`, element 준비와 tap 경로를 그대로 수행한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - `SETTINGS_NOTIFICATION_ROW_ZERO`, `SETTINGS_NOTIFICATION_ROW_AMBIGUOUS`를 안전 Allowlist에 추가했다.
  - 두 특수 Assertion을 generic `SETTINGS_NOTIFICATION_ROW_MISSING`보다 먼저 판정한다.
  - 기존 Assertion 우선·Stage 차선·Unknown 최종, Phase·원 Exit 보고는 유지했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 0건·다건·1건 분기와 특수 Code Allowlist, generic 이전 parser 순서를 고정했다.
  - Bash Fixture에서 ZERO·AMBIGUOUS·generic·Stage 차선과 원 Phase·Exit를 검증했다.
- Progress와 본 Attempt 36 보고서.
- 미변경: Query Selector·Label·타입·Hittable filter·Timeout·tap, Product, Workflow 구조, Quality, Package/Lock, Project 설정.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C35 RED | iOS 계약 35/37 PASS·2 FAIL: Swift 분기와 Bash Code 부재에서 예상 실패 |
| C35 GREEN | iOS 계약 37/37 PASS |
| 분류 계약 | ZERO·AMBIGUOUS가 generic보다 먼저 판정되고 generic Assertion과 Stage 차선도 유지 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Workflow YAML Parse 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Selector·Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·보안 경계 보존 근거

- C34의 Label-only Predicate, 전체 Element Hittable filter, 대기와 재수집은 변경하지 않았다.
- ZERO·AMBIGUOUS Assertion 모두 실제 count 값을 포함하지 않는다.
- Raw Accessibility Tree·UI Label Dump·좌표·Index·환경값 출력은 추가하지 않았다.
- 두 특수 문구도 기존 generic 문구에 포함되므로 parser 순서를 계약 테스트로 고정했다.
- C33 Marker 순서, 1건 반환과 tap, Settings Switch OFF/ON, Alert/Timeout과 세 Phase Method는 유지했다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA의 macOS iOS Workflow에서 revoke를 실행해 `SETTINGS_NOTIFICATION_ROW_ZERO` 또는 `SETTINGS_NOTIFICATION_ROW_AMBIGUOUS`를 판정한다.
3. 해당 고정 분류가 확인된 뒤에만 후속 Selector 또는 Settings 탐색 기술 판단을 한다.
4. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
