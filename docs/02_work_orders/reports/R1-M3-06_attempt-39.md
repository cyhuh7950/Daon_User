COMPLETED | R1-M3-06-I007 | C38 exact Label ZERO/NONHITTABLE 고정 분류 | Swift 실패 진단·Simulator parser·계약 Test·Progress·Attempt 39 | RED 35/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS 두 분류 Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 39 결과보고

## 판정

C38 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 기존 선택 경로는 유지한 채 semantic Cell 0건으로 이미 실패하는 지점에서 exact Label 자체 0건과 존재하지만 Hittable 0건을 각각 고정 Code로 분류했다. failure count는 0이다.

## 판단 이유

- exact Head `6be953aafb3851f694772db77059a499d5ce0338`의 iOS Run `30282607565`는 Build·UI Test를 통과했으나 revoke에서 `SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO / 65`로 종료됐다.
- 이 증거만으로는 exact Label 접근성 요소 자체가 0건인지, 요소는 존재하지만 Hittable 후보가 0건인지 구분할 수 없다.
- 대기 뒤 direct Query의 전체 요소를 보존하고 기존 Hittable 배열을 그 전체 배열에서 파생하면 선택 의미를 바꾸지 않고 실패 원인만 분리할 수 있다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - 기존 10초 대기 뒤 `exactLabelElements` 전체 배열을 한 번 수집하고 기존 `directElements`를 동일 배열의 Hittable 필터로 유지했다.
  - direct Hittable 0건·semantic Cell 0건인 기존 실패 지점에서 전체 exact Label 0건은 `[LABEL_ZERO]`, 1건 이상은 `[LABEL_NONHITTABLE]`로만 분류했다.
- `apps/mobile/ios/ci/verify-simulator.sh`
  - `SETTINGS_NOTIFICATION_LABEL_ZERO`, `SETTINGS_NOTIFICATION_LABEL_NONHITTABLE`를 Allowlist에 추가하고 generic row Code보다 먼저 판정했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 대기 뒤 전체 Label 수집, 두 고정 분기, parser 우선순위와 원 Phase·Exit 보존을 고정했다.
- Progress와 본 Attempt 39 보고서.
- 미변경: direct Hittable 1건 우선, semantic Cell fallback 성공, direct/semantic 다건 Fail-close, Timeout, Marker, tap, Product, Workflow, Quality, Package/Lock, Project.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C38 RED | iOS 계약 35/37 PASS·2 FAIL: Swift 분류와 Bash Code 부재에서 예상 실패 |
| C38 GREEN | iOS 계약 37/37 PASS; LABEL_ZERO/NONHITTABLE·parser 우선순위 PASS |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Node YAML Parser 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·안전 경계 보존 근거

- 대기 Predicate, 총 Timeout 10초, direct Hittable 1건 우선과 direct 다건 즉시 Fail-close 순서는 변경하지 않았다.
- direct 0건일 때 semantic Cell을 평가하는 기존 경로와 semantic 단일 Cell 성공·다건 Fail-close는 변경하지 않았다.
- 새 분류는 semantic Cell도 0건인 기존 실패 지점에서 Assertion Code만 세분화하며 후보 선택이나 tap을 추가하지 않는다.
- 실제 후보 수·Raw Accessibility Tree·UI Text·좌표·Index·환경값을 출력하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 revoke·grant-again을 실행해 `LABEL_ZERO` 또는 `LABEL_NONHITTABLE` 고정 분류를 확인한다.
3. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
