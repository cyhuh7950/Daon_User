COMPLETED | R1-M3-06-I007 | C40 Settings bounded scroll search | Swift bounded search·Stage Parser·계약 Test·Progress·Attempt 41 | RED 35/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS scroll Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 41 결과보고

## 판정

C40 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 direct exact Label, semantic Cell, delimiter-anchored composite Cell Query와 선택 우선순위를 유지하면서 모든 후보와 exact Label 자체가 0건일 때만 최대 4회 Settings 상향 swipe 후 동일 Query를 재평가하도록 구현했다. failure count는 0이다.

## 판단 이유

- Head `98ca1ac2437ec8f72259a83b0877a4eb793bcaab`의 iOS Run `30286751361`은 Build·UI Test를 통과했으나 revoke에서 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`로 종료됐다.
- 승인된 세 Query에서 후보가 모두 0건이므로 Query 범위를 확대하기 전에 Notifications 행이 현재 Viewport 밖에 있는지를 고정 횟수 탐색으로 확인할 필요가 있다.
- 4회 상한, 후보 발견 즉시 중단, 기존 Query 재사용을 함께 고정하면 무제한 제스처와 선택 범위 확대 없이 화면 밖 노출만 검증할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - 기존 direct exact, semantic, delimiter-anchored composite 후보 수집을 재사용 가능한 로컬 함수로 정리했다.
  - 모든 후보와 exact Label 자체가 0건일 때 `SETTINGS_NOTIFICATION_SCROLL_SEARCH` Stage를 기록하고 `for _ in 0..<4` 범위에서만 `settings.swipeUp()`을 수행한다.
  - 각 swipe 직후 동일 Query를 재평가하며 후보가 하나라도 나타나면 즉시 중단한다.
  - 탐색 후에도 direct → semantic → exact Label non-Hittable → composite 순서와 기존 단일성·Fail-close 판정을 유지한다. 최종 0건은 `[COMPOSITE_ZERO]`다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - `STAGE_SETTINGS_NOTIFICATION_SCROLL_SEARCH`를 허용 실패 Code와 마지막 Stage parser에 추가했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 전체 0건 진입 조건, Stage, 4회 상한, 단일 swipe source, 동일 Query 재평가, 발견 즉시 중단과 기존 우선순위를 계약으로 고정했다.
  - sleep·무제한 반복과 승인되지 않은 Selector 사용 금지를 함께 검증한다.
- Progress와 본 Attempt 41 보고서.
- 미변경: Query Predicate, 10초 최초 대기, direct·semantic·composite 단일성 및 다건 Fail-close, Marker, tap, Product, Workflow, Quality, Package/Lock, Project.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C40 RED | iOS 계약 35/37 PASS·2 FAIL: Swift Stage/loop와 Bash Stage Allowlist 부재에서 예상 실패 |
| C40 GREEN | 기존 우선순위 계약을 동등한 후보 구조에 맞춰 정합화한 뒤 iOS 계약 37/37 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Node YAML Parser 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·안전 경계 보존 근거

- 최초 후보 중 하나라도 존재하면 swipe를 실행하지 않고 기존 선택·실패 판정으로 바로 진행한다.
- direct exact, semantic Cell, delimiter-anchored composite Cell의 Predicate와 평가 우선순위를 확대하지 않았다.
- bounded loop 안의 Settings swipe source는 한 곳이고 상한은 4회다. 좌표, Index, sleep, `while`, `repeat`를 사용하지 않는다.
- 탐색 후 direct 다건, semantic 다건, exact Label non-Hittable, composite 다건은 기존 Code로 Fail-close한다.
- 실제 후보 수·Raw Accessibility Tree·UI Text·환경값을 출력하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 revoke·grant-again을 실행해 bounded scroll 이후 단일 선택 또는 고정 실패 Code를 확인한다.
3. 실제 macOS Simulator의 scroll Runtime과 최종 Artifact는 본 Windows 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
