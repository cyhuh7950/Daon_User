# R1-M3-06-C17 수정 작업지시서 — React Native Scroll Host XCUI Query 정합

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I006` |
| Attempt | `18` |
| 사유 | `testID` 추가 뒤에도 XCUI의 내부 ScrollView 2개에는 Identifier가 없으며, React Native가 Identifier를 부여한 Scroll Host View를 기존 Test가 잘못된 Element Type으로 조회함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-18.md` |

## 2. 확인된 증거

- exact Head `fbe78aad72a90a64cc2b37961909f94eca43e21d`, merge candidate `d3cf0a53b11d0f57297cd23707d3f25417018000`의 Run `30241706006`, Job `89900142973`, Artifact `8643657783`을 검토했다.
- 공통 Gate `30241705995`, Portable 회귀, Pods, Simulator, unsigned Build는 SUCCESS다.
- UI Test 3개 중 Permission/Settings는 PASS하고 Route/Lifecycle 2개만 동일 실패했다. App Crash·Native Method 오류는 없다.
- C16 `testID`는 Release Bundle과 Build에 포함됐지만 `app.scrollViews["공용 Navigation"]` Query는 실제 내부 ScrollView 2개에서 Identifier를 찾지 못했다.
- React Native ScrollView의 자동화 Identifier는 Scroll Content를 수행하는 내부 `XCUIElementTypeScrollView`가 아니라 `testID`가 설정된 바깥 Host View에 결속된다. 따라서 Element Type을 `scrollViews`로 한정한 현재 Query가 틀렸다.

## 3. 필수 작업

1. Navigation과 화면 내용의 XCUI Query를 각 고정 `testID`를 가진 React Native Host `otherElements`로 변경한다.
2. 기존 정확 Identifier 문자열, Swipe 방향, `0..<8`, Button Hittable·Route Title·Root/Foreground Fail-close와 Permission 합격 기준은 유지한다.
3. Product의 두 `accessibilityLabel`·`testID`, UI 구조·스타일·Scroll 방향은 변경하지 않는다.
4. 좌표, Index/firstMatch, App 전체 Swipe, Sleep, Retry 증가, 존재하지 않는 경우 우회/Skip은 금지한다.
5. Host Query가 `waitForExistence`로 존재함을 먼저 Fail-close 확인한 뒤 Swipe하도록 하며, Navigation/Content 각각 정확한 Identifier만 사용한다.

## 4. TDD와 완료 조건

- 구현 전 iOS 계약 Test가 `scrollViews` Element Type 오사용으로 RED
- 구현 후 Host `otherElements` Query 3건, 정확 Identifier, 존재 선검증, 기존 Swipe 3건·`0..<8` 3건 PASS
- iOS Native·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- Product Source·Native Bridge·Evidence/Workflow·Bundle ID·Deep Link·Permission·Lifecycle·Signing 변경 0
- 개인 절대경로·Generated Build/Pods/Gem/Test Temp·Signing Asset 잔존 0
- Progress·Attempt 18에 exact Run/Artifact, C16 재현, RN Host/내부 ScrollView Element Type 차이와 macOS 재검증 필요를 기록
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속

