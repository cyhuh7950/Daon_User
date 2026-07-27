COMPLETED | R1-M3-06-I007 | C30 iOS Settings 알림 행 접근성 타입 정합 | exact Label staticText·cell 단일 행 Helper·계약 Test·Progress·Attempt 31 | 관련 37/37·iOS 44/44·Mobile 전체·Android 11/11·Node 307/307·Toolchain·Workflow/Bash·Diff PASS | 실제 macOS revoke·grant-again Settings Runtime 미확인 | 어울1의 Commit·Push와 exact-SHA macOS CI 판정

# R1-M3-06 Attempt 31 결과보고

## 판정

C30 수정 개발 패킷은 `COMPLETED`이며 전체 상태는 `IMPLEMENTED_PENDING_MACOS_CI`다. iOS Settings의 Notifications/알림 행을 English/Korean exact Label과 `staticText`·기존 `cell` 접근성 표현으로 조회하는 전용 Helper를 추가했다. 같은 Label의 두 Element Type이 동시에 존재하면 하나의 의미 행으로 집계하고 기존 tappable cell을 우선해 중복 UI로 오판하지 않는다. Settings foreground·행 tap·Allow Notifications exact Switch·OFF/ON 전후값·앱 복귀, C29 고정 Method와 Permission Marker·Alert 동작은 변경하지 않았다. failure count는 0이다.

## 판단 이유

- exact Head `55016d3c7dcf84e008a6be8adf6438d404053425`의 iOS Run `30264900365`은 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test를 통과했다.
- Permission Step은 grant-initial의 Camera·Microphone·Notification 실제 System Alert·Allow·결과 확인을 통과했다.
- revoke Phase는 Production 설정 버튼과 Settings foreground까지 통과한 뒤 `SETTINGS_NOTIFICATION_ROW_MISSING / revoke / 65`로 종료됐다.
- 실패 지점은 `settings.cells["Notifications"/"알림"]` exact Query뿐이며 iOS 26.6에서 같은 exact Label이 non-cell 접근성 타입으로 노출되는 차이가 확인 원인이다.
- 기존 `requireExactElement` 후보 배열에 staticText를 단순 추가하면 동일 Label의 cell과 staticText가 함께 존재할 때 두 Match로 집계해 모호성 실패를 만들 수 있어, Label별 표현을 한 의미 행으로 합치는 전용 Helper가 필요했다.

## 조치

### 변경 범위

- `apps/mobile/ios/DaonUITests/DaonUITests.swift`
  - `requireExactNotificationSettingsRow(in:)` 전용 Helper 추가.
  - 고정 Label은 `Notifications`, `알림` 두 개뿐이며 `settings.staticTexts[label]`와 `settings.cells[label]` exact subscript만 사용.
  - 같은 Label의 두 타입은 `staticTextExists || cellExists`로 한 Match만 추가하고 cell이 있으면 기존 행 Element를 반환.
  - English/Korean 서로 다른 Label이 동시에 존재하면 기존 단일 행 계약대로 `matches.count == 1`에서 Fail-close.
  - 기존 Notifications cell 후보 배열을 전용 Helper 호출 한 줄로 교체.
- `scripts/tests/ios-native-shell.test.mjs`
  - exact Label 2종, staticText·cell 지원, 동일 Label 단일 집계, 기존 cell 우선과 Helper 호출 계약 추가.
  - Descendant·광범위 matching/containing, 좌표·Index·`firstMatch`·부분문자열 Query 금지 계약 추가.
- Progress와 본 Attempt 31 보고서.
- 미변경: `apps/mobile/ios/ci/**`, C29 고정 XCTest Method, Alert Selector·순서·Timeout, Permission Marker, Settings Switch 검증, Product Native Host·Bridge·Project, Workflow·Quality 정책, Android, Package/Lockfile.

### RED→GREEN·회귀 결과

| 검증 | 결과 |
| --- | --- |
| C30 RED | 관련 계약 36/37 PASS·1 FAIL: 전용 exact Label row Helper 부재를 예상대로 재현 |
| C30 GREEN | 관련 계약 37/37 PASS |
| 접근성 타입 계약 | English/Korean exact Label, staticText·cell 표현, 동일 Label 단일 행 집계, 기존 cell 호환 우선 PASS |
| 금지 경계 | 좌표·Index·`firstMatch`·부분문자열·Descendant/containing/matching·Private URL·Defaults·TCC 직접 수정 0건 |
| Mobile 전체 | Lint 14 files, Type, Unit 10/10, Contract 15/15, Android 11/11, iOS 44/44, Android/iOS Bundle PASS |
| Bundle | Android 927,195 bytes SHA-256 `418E5CDD751E412360957410AEDBEE369CB34CE0871146D014D346CE68C5AFB8`; iOS 921,193 bytes SHA-256 `DC18A35596D5FED225E95E98217795968BB7F4568EFA67BB0012381B0E770F70`; C29와 동일 |
| 전체 Node | 307/307 PASS |
| Toolchain | 7 npm Manifest, exact Pin, Lockfile PASS |
| Workflow·Script Syntax | Workflow JSON PASS, Git Bash 기준 iOS CI Bash 3/3 PASS, iOS Test Node Syntax PASS |
| 변경 경계 | `git diff --check` PASS; Simulator Script·Product/Native/Bridge/Project/Workflow/Package/Lock Diff 0 |

### 오류·복구 근거

- RED 1건은 승인 C30 계약을 선고정한 예상 실패이며 기존 36개 계약은 모두 통과했다.
- GREEN과 전체 회귀에서 추가 오류는 없었다.
- `verify:mobile`의 기존 `.pytest_cache` 접근 경고는 Scanner가 해당 Cache를 Skip한 뒤 Exit 0이었고 관련 변경은 없다.
- Windows Portable 검증은 실제 macOS Xcode 26.6 Settings 접근성 Runtime을 대체하지 않는다.

## 미해결 사항과 다음 판단

1. 어울1이 단일 Writer 종료를 확인하고 변경을 Commit·Push한다.
2. 새 exact SHA의 macOS Xcode 26.6 Workflow에서 revoke와 grant-again이 Notifications/알림 행을 실제 탭하는지 확인한다.
3. Allow Notifications exact Switch OFF/ON 전후값, 앱 복귀, 세 xcresult와 Evidence Manifest까지 판정한다.
4. 새 실패가 발생하면 기존 Assertion 우선·Stage 차선 Annotation과 Raw Artifact로 위치를 확정하고 다른 타입·Selector를 추측 추가하지 않는다.
5. Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 수행하지 않았다.
