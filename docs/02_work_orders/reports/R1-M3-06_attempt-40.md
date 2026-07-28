COMPLETED | R1-M3-06-I007 | C39 delimiter-anchored composite Cell Label fallback | Swift fallback·Simulator parser·계약 Fixture·Progress·Attempt 40 | RED 35/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS composite Cell Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 40 결과보고

## 판정

C39 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 direct exact Label과 semantic Cell 경로를 우선 유지하고, 두 경로와 exact Label 자체가 모두 0건일 때만 Settings Cell의 쉼표 구분자 고정 복합 Label fallback을 평가하도록 구현했다. failure count는 0이다.

## 판단 이유

- Head `5489b13f5926767ae7ccb3986a3b11f5a61f0ac2`의 iOS Run `30284615872`는 Build·UI Test를 통과했으나 revoke에서 `SETTINGS_NOTIFICATION_LABEL_ZERO / 65`로 종료됐다.
- exact Label 접근성 요소 자체가 0건으로 확인되어, Settings Cell이 제목과 상태를 하나의 복합 Label로 노출하는 경계를 승인된 Cell 범위 안에서 확인할 필요가 있다.
- `Notifications,`와 `알림,`의 쉼표 구분자를 포함한 Prefix만 허용하면 구분자 없는 임의 Prefix보다 선택 범위를 제한할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - direct Hittable 0건, semantic Cell 0건, exact Label 전체 0건일 때만 복합 Cell Predicate를 생성한다.
  - Cell Label은 `label == "Notifications"`, `label BEGINSWITH "Notifications,"`, `label == "알림"`, `label BEGINSWITH "알림,"` 네 조건만 허용한다.
  - Hittable 복합 Cell 1건은 기존 선택 배열에 전달하고, 0건은 `[COMPOSITE_ZERO]`, 다건은 `[COMPOSITE_AMBIGUOUS]`로 Fail-close한다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO`, `SETTINGS_NOTIFICATION_COMPOSITE_ROW_AMBIGUOUS`를 Allowlist에 추가하고 generic row Code보다 먼저 판정한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - fallback 진입 순서, 허용 Predicate 한 건, Cell 한정 Query, 0/다건 Code와 금지 Selector를 고정했다.
  - Parser 확장으로 Windows `bash -c` 명령 길이 한계를 넘은 관련 두 Fixture는 동일 Script 내용을 임시 파일로 기록해 Bash에 전달하도록 교정했다. 기존 환경·Exit·로그·정리 계약은 유지한다.
- Progress와 본 Attempt 40 보고서.
- 미변경: direct·semantic 우선순위, 10초 Timeout, direct/semantic 다건 Fail-close, Marker, tap, Product, Workflow, Quality, Package/Lock, Project.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C39 RED | iOS 계약 35/37 PASS·2 FAIL: Swift fallback과 Bash Code 부재에서 예상 실패 |
| 첫 GREEN | 35/37 PASS·2 FAIL: Production 계약은 충족했으나 Windows `bash -c` Fixture가 명령 길이 한계로 잘려 Exit 2 |
| C39 GREEN | Fixture를 temp Script 실행으로 복구 후 iOS 계약 37/37 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Node YAML Parser 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·안전 경계 보존 근거

- direct exact Hittable 1건은 그대로 우선하고 direct 다건은 즉시 Fail-close한다.
- direct 0건이면 semantic Cell을 먼저 평가하며, semantic 단일 Cell 성공과 다건 Fail-close를 유지한다.
- 복합 Query는 `semanticCells.isEmpty && exactLabelElements.isEmpty` 안에서만 생성·평가된다.
- 구분자 없는 Prefix, `CONTAINS`, regex, identifier, button/link, 좌표와 Index는 사용하지 않는다.
- 실제 후보 수·Raw Accessibility Tree·UI Text·환경값을 출력하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 revoke·grant-again을 실행해 composite Cell 단일 선택 또는 고정 ZERO/AMBIGUOUS Code를 확인한다.
3. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
