COMPLETED | R1-M3-06-I007 | C37 XCUIElementQuery 공식 Predicate containment API 교정 | Swift 호출 1곳·계약 Test·Progress·Attempt 38 | RED 36/37→GREEN 37/37·Mobile 전체·iOS 44/44·Android 11/11·Node 308/308·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS UI Test Compile·Permission Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 38 결과보고

## 판정

C37 수정 개발 패킷은 `COMPLETED`이며 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. 존재하지 않는 `containing(.staticText, predicate:)` 호출 한 곳을 Apple 공식 Predicate containment API `settings.cells.containing(exactLabelPredicate)`로 교정했다. failure count는 0이다.

## 판단 이유

- exact Head `78d00e7a8b0a934dcb35d67d79b7669f5ee93a50`의 iOS Run `30280074813`은 Toolchain·Portable·Pods·Simulator·unsigned App Build까지 통과했으나 UI Test Step에서 Exit 65로 종료됐다.
- Apple 공식 Predicate 기반 API는 `XCUIElementQuery.containing(_:)`이고, C36의 `containing(.staticText, predicate:)` 오버로드는 존재하지 않는다.
- Receiver `settings.cells`가 이미 Cell 범위를 제한하므로 공식 호출로 바꿔도 exact Label 자식 containment 의미와 기존 선택·Fail-close 동작은 유지된다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - semantic Query 생성 한 곳만 `settings.cells.containing(exactLabelPredicate)`로 교정했다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 공식 호출이 정확히 1건임을 고정하고 존재하지 않는 구 시그니처를 금지했다.
- Progress와 본 Attempt 38 보고서.
- 미변경: Simulator Script, Product, Workflow, Quality, Package/Lock, Project, direct 우선·semantic fallback·Hittable 필터·0/다건 Fail-close·Timeout·Marker·tap·Parser.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C37 RED | iOS 계약 36/37 PASS·1 FAIL: Swift 구 시그니처 잔존에서 예상 실패 |
| C37 GREEN | iOS 계약 37/37 PASS; 공식 호출 1건·구 시그니처 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44 PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70` |
| 전체 Node | 308/308 PASS |
| Toolchain | 7 npm Manifest·exact Pin·Lockfile PASS |
| Workflow·Syntax | Node YAML Parser 2/2, Git Bash iOS Script 3/3, Node Syntax PASS |
| 경계 | `git diff --check` PASS; Simulator Script·Product·Workflow·Quality·Package/Lock·Project Diff 0 |

### 기존 동작·안전 경계 보존 근거

- Swift의 API 호출 인자만 교정했고 direct exact Hittable 우선 및 direct 0건에서만 semantic Cell을 평가하는 분기와 순서는 동일하다.
- semantic Hittable Cell 0건·다건 고정 Code와 총 Timeout 10초, Marker, 반환·tap 흐름은 변경하지 않았다.
- Simulator Script와 Parser는 수정하지 않았고 Raw Accessibility Tree·UI Label·좌표·Index·환경값 출력도 추가하지 않았다.
- Product·Workflow·Quality·Package/Lock·Project에는 Diff가 없다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료와 Diff를 확인하고 Commit·Push한다.
2. 새 exact SHA macOS iOS Workflow에서 UI Test Target Compile 및 revoke·grant-again Permission Runtime을 검증한다.
3. 실제 macOS Simulator Runtime과 최종 Artifact는 본 로컬 환경에서 검증하지 않았다.
4. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing은 수행하지 않았다.
