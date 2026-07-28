COMPLETED | R1-M3-06-I007 | C36 exact Label 자식 기반 Settings Cell fallback | direct 우선 Helper·semantic Code parser·계약 Test·Progress·Attempt 37 | RED 35/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS semantic Cell Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 37 결과보고

## 판정

C36 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. direct exact Hittable 요소가 정확히 1개면 기존 경로를 우선하고, direct 후보가 0개일 때만 exact staticText 자식을 포함하는 Hittable Settings Cell을 평가해 단일 행을 반환하도록 구현했다. failure count는 0이다.

## 판단 이유

- exact Head `23f57d8f1a7db305323eaa05376223a34d8c5ad3`의 iOS Run `30277574869`은 Build·일반 UI를 통과했지만 revoke에서 `CODE=SETTINGS_NOTIFICATION_ROW_ZERO PHASE=revoke EXIT=65`로 종료됐다.
- direct exact Label 요소는 존재하더라도 직접 Hittable하지 않음이 확정됐다.
- C30의 실패한 `settings.cells[label]`는 Cell 자신의 Label을 가정했지만, C36은 exact 제목 staticText 자식을 포함하는 상위 Cell을 의미적으로 조회하므로 확인된 실패 방식의 반복이 아니다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - English/Korean exact Label Predicate를 `exactLabelPredicate` 하나로 고정했다.
  - direct Query와 `settings.cells.containing(.staticText, predicate: exactLabelPredicate)` semantic Query가 동일 Predicate를 사용한다.
  - 대기는 direct Hittable 후보를 먼저 평가하고 없을 때 semantic Hittable Cell을 평가하며 기존 총 Timeout 10초를 유지한다.
  - 대기 뒤 direct 후보가 2개 이상이면 기존 AMBIGUOUS, 정확히 1개면 direct 배열을 선택한다.
  - direct 후보가 0개일 때만 semantic Cell을 수집한다. 0개는 `[SEMANTIC_ZERO]`, 2개 이상은 `[SEMANTIC_AMBIGUOUS]`로 Fail-close한다.
  - 선택 배열이 정확히 1건일 때 기존 COUNT_SINGLE→element 준비→tap 경로를 유지한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - `SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO`, `SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS`를 Allowlist에 추가했다.
  - 두 semantic Assertion Code를 generic row Code보다 먼저 판정한다.
  - 기존 direct ZERO/AMBIGUOUS, Assertion 우선·Stage 차선·Unknown 최종, Phase·원 Exit는 유지했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - direct 1건 우선, direct 0건에서만 semantic fallback, semantic 0/다건 Fail-close와 단일 반환을 고정했다.
  - `cell[label]`, button/link, 부분문자열·identifier·firstMatch·Index·좌표 금지와 parser 우선순위를 검증했다.
- Progress와 본 Attempt 37 보고서.
- 미변경: Product, Workflow 구조, Quality, Package/Lock, Project, Settings Switch·Alert·세 Phase Method.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C36 RED | iOS 계약 35/37 PASS·2 FAIL: semantic Helper와 Bash Code 부재에서 예상 실패 |
| C36 GREEN | iOS 계약 37/37 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Workflow YAML Parse 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·안전 경계 보존 근거

- direct 후보 1건은 semantic 후보 존재 여부와 관계없이 우선 선택한다.
- direct 후보가 2건 이상이면 fallback하지 않고 즉시 AMBIGUOUS Fail-close한다.
- semantic Query는 exact staticText 자식 containment 한 건뿐이며 Cell 자신의 Label, button/link 또는 임의 타입 후보를 사용하지 않는다.
- 실제 후보 수, Raw Accessibility Tree·UI Label Dump·좌표·Index·환경값을 출력하지 않는다.
- Marker 순서, 행 tap 이후 Switch OFF/ON, Alert/Timeout과 세 Phase Method는 변경하지 않았다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 revoke·grant-again을 실행해 semantic Cell 단일 선택 또는 고정 semantic Code를 확인한다.
3. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
