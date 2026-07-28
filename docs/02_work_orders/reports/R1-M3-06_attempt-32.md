COMPLETED | R1-M3-06-I007 | C31 Settings 알림 행 exact Label·Hittable 타입 비의존 Query | 전체 Tree exact Predicate·count 1·단일 element·계약 Test·Progress·Attempt 32 | 관련 37/37·iOS 44/44·Mobile 전체·Android 11/11·Node 307/307·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS revoke·grant-again Settings Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 32 결과보고

## 판정

C31 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. iOS Settings의 전체 접근성 Tree에서 Element Type과 무관하게 `Notifications`/`알림` exact Label과 `isHittable == true`만 결합해 조회하고, 합산 count가 정확히 1일 때만 Query의 단일 Element를 반환하도록 교체했다. Settings foreground·행 tap·Allow Notifications exact Switch·OFF/ON 전후값·앱 복귀, C29 고정 Method와 Permission Marker·Alert 동작은 변경하지 않았다. failure count는 0이다.

## 판단 이유

- exact Head `8bfed8df9a024283ef347583698731e5fa81f76a`의 iOS Run `30267022852`는 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test를 통과했다.
- Permission Step은 3분 42초 뒤 revoke Phase의 Settings foreground 이후 `SETTINGS_NOTIFICATION_ROW_MISSING / revoke / 65`로 종료됐다.
- C30에서 exact Label의 `cell`과 `staticText` 표현을 모두 지원했지만 같은 지점에서 재현돼, 실제 iOS 26.6 접근성 Element Type은 두 타입 모두 아닌 것으로 확인됐다.
- 타입 후보를 계속 추가하면 OS별 접근성 구현에 종속되므로, C31 승인 계약대로 전체 Tree의 exact Label·Hittable 조건과 단일 Match만 사용해야 했다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `requireExactNotificationSettingsRow(in:)`의 C30 typed 후보와 우선순위를 제거했다.
  - `settings.descendants(matching: .any)`에 `(label == %@ OR label == %@) AND isHittable == true` Predicate를 적용했다.
  - 출현 대기는 Query count가 0보다 큰지만 확인하며 Element를 선택하지 않는다.
  - 대기 후 `query.count == 1`을 먼저 검증하고, 0건 또는 2건 이상은 동일 오류로 Fail-close하며, 그 뒤에만 `query.element`를 반환한다.
- `scripts/tests/ios-native-shell.test.mjs`
  - 타입 비의존 전체 Tree, exact Label·Hittable Predicate, count 선검증 뒤 단일 Element 반환 계약을 추가했다.
  - typed Query·identifier·부분문자열·정규식·`firstMatch`·`element(boundBy:)`·좌표 선택 금지를 Helper 범위에서 검증한다.
- Progress와 본 Attempt 32 보고서.
- 미변경: `apps/mobile/ios/ci/**`, C29 고정 XCTest Method, Alert Selector·순서·Timeout, Permission Marker, Settings Switch 검증, Product Native Host·Bridge·Project, Workflow·Quality 정책, Android, Package/Lockfile.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C31 RED | 관련 계약 36/37 PASS·1 FAIL: C30 typed Helper에 타입 비의존 Query가 없어 예상 실패 |
| C31 GREEN | 관련 계약 37/37 PASS |
| 접근성 Query 계약 | `.any` 전체 Tree, English/Korean exact `label ==`, `isHittable == true`, count 1 선검증, 단일 `query.element` PASS |
| 금지 경계 | Helper 범위 typed Query·identifier·부분/정규식·`firstMatch`·Index·좌표·임의 우선순위 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C30과 동일 |
| 전체 Node | 307/307 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON 2/2 PASS, Git Bash 기준 iOS CI Bash 3/3 PASS, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Simulator Script·Product/Native/Bridge/Project/Workflow/Package/Lock Diff 0 |

### 오류·복구 근거

- RED 1건은 승인 C31 계약을 선고정한 예상 실패이며 기존 36개 계약은 모두 통과했다.
- 최초 정적 Workflow 검사에서 존재하지 않는 파일명 `mobile-native.yml`을 지정해 명령이 중단됐다. 저장소의 실제 두 Workflow 경로를 확인한 뒤 JSON 2/2 PASS로 재검증했으며 코드·설정 변경은 없었다.
- GREEN과 전체 회귀에서 기능 오류는 없었다.
- `verify:mobile`의 기존 `.pytest_cache` 접근 경고는 Scanner가 해당 Cache를 Skip한 뒤 Exit 0이었고 관련 변경은 없다.
- Windows Portable 검증은 실제 macOS Xcode 26.6 Settings 접근성 Runtime을 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료를 확인하고 변경을 Commit·Push한다.
2. 새 exact SHA의 macOS Xcode 26.6 Workflow에서 revoke와 grant-again이 Notifications/알림 행을 실제 탭하는지 확인한다.
3. Allow Notifications exact Switch OFF/ON 전후값, 앱 복귀, 세 xcresult와 Evidence Manifest까지 판정한다.
4. 새 실패가 발생하면 기존 Assertion 우선·Stage 차선 Annotation과 Raw Artifact로 위치를 확정하고 타입·Selector를 추측 추가하지 않는다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
