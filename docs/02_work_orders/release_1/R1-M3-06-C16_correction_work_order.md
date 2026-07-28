# R1-M3-06-C16 수정 작업지시서 — iOS ScrollView 자동화 식별자 결속

## 1. 판정

| 항목 | 값 |
| --- | --- |
| issue_id | `R1-M3-06-I006` |
| Attempt | `17` |
| 사유 | C15로 앱 시작 Crash는 해소됐으나 iOS XCTest가 Navigation ScrollView의 `accessibilityLabel`을 Identifier로 조회하지 못해 2개 시나리오가 Swipe 전에 실패함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-17.md` |

## 2. 확인된 증거

- exact Head `90c86741befe712c71b0b3f1d9407aa18770bb76`, merge candidate `3b3cfaca75ae45c6339ab5fca7b9185bb34f0d28`의 Run `30240374452`, Artifact `8643271629`을 검토했다.
- Objective-C Bridge·Swift Compile·unsigned Build가 성공했고 이전 `undefined is not a function`, `RCTFatalException`, SIGABRT와 Crash Report는 0건이다.
- Root Shell과 Route Button, Permission Button이 실제 XCUI Tree에 노출됐고 `testPermissionControlsAndSettingsBoundary`는 PASS했다.
- 실패 2건은 모두 `DaonUITests.swift:24`의 `app.scrollViews["공용 Navigation"].swipeLeft()`이며, XCTest는 존재하는 ScrollView 2개에서 Identifier `공용 Navigation`을 찾지 못했다고 보고했다.
- 공용 Shell은 두 ScrollView에 `accessibilityLabel`만 선언한다. React Native iOS에서 자동화 Identifier의 명시적 계약인 `testID`가 없어 XCUI Identifier Subscript와 결속되지 않았다.
- 공통 Quality Gate Run `30240374416`은 SUCCESS다.

## 3. 필수 작업

1. 공용 Navigation과 화면 내용 ScrollView에 각각 기존 의미와 같은 고정 `testID`를 추가한다.
   - Navigation: `공용 Navigation`
   - Content: `화면 내용`
2. 기존 `accessibilityLabel`, 구조, 스타일, Route 순서, Scroll 방향과 사용자 동작은 변경하지 않는다.
3. XCTest의 Identifier Query는 유지한다. 좌표 기반 Swipe, 전체 App Swipe, Sleep, Retry 증가, First/Index 기반 선택으로 실패를 숨기지 않는다.
4. Permission 결정론 단계도 `화면 내용` Identifier를 사용하므로 두 ScrollView 모두 계약 Test에 포함한다.
5. iOS 외 플랫폼의 공용 Shell 렌더링과 Android 동작에 회귀가 없음을 검증한다.

## 4. TDD와 완료 조건

- 구현 전 Mobile/iOS 계약 Test가 두 ScrollView의 `accessibilityLabel`과 동일 `testID` 결속 누락으로 RED
- 구현 후 iOS Native·Mobile Unit/Contract/Type/Lint·Android·Bundle·전체 Node·Toolchain PASS
- XCTest는 기존 Route·Lifecycle·Permission 시나리오, Root/Foreground Fail-close, Swipe 횟수와 합격 의미를 유지
- C14 진단·Evidence·Workflow, Native Bridge, Bundle ID·Deep Link·Permission·Lifecycle·Signing 변경 0
- `git diff --check`, 개인 절대경로·Generated Build/Pods/Gem/Test Temp·Signing Asset 잔존 0
- Progress·Attempt 17에 exact Run/Artifact, Crash 해소, 새 Identifier 원인, RED→GREEN과 macOS 재검증 필요를 기록
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속

