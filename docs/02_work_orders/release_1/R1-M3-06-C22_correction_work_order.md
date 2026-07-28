# R1-M3-06-C22 수정 작업지시서 — Apple 공식 XCUITest Deep Link 검증 전환

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `23` |
| 사유 | `simctl openurl`은 성공 Exit를 반환했지만 AppDelegate 수신 표식이 없었으므로, Apple이 Custom URL Scheme UI 자동화에 제시한 XCUITest System Open으로 OS→App→화면 경계를 직접 검증해야 함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-23.md` |

## 2. 근거와 기준 문서

- 승인 상세 설계서 v0.7, 작업계획서 v0.9, 테스트계획서 v0.7 전체
- exact Head `2ba7f772a7831a686caad40ed3c8a7f54072737e`의 Quality Gate Run `30248902466` SUCCESS.
- iOS Run `30248902445`은 Build·UI Test 3개 PASS 후 `WorkspaceList` Warm URL에서 실패했다. 실패 표식에는 현재 Process의 Home 저장만 있고 `DAON_PENDING_DEEP_LINK_RECEIVED`가 없으므로 Shell URL 발송이 AppDelegate에 전달되지 않았다.
- 현재 AppDelegate의 `application(_:open:options:) → RCTLinkingManager.application`은 React Native 공식 iOS Linking 계약과 일치한다.
- Apple WWDC25 UI Automation은 Custom URL Scheme을 `XCUIDevice.shared.system.open(customURL)`로 열고 대상 App의 foreground·화면을 확인하는 방식을 제시한다.

## 3. 필수 작업

1. `DaonUITests.swift`에 App을 1회 실행한 뒤 `XCUIDevice.shared.system.open`으로 승인 Warm Route 7종을 순서대로 열고, 매번 runningForeground·Root·해당 Route 제목을 Fail-close 확인하는 독립 Test를 추가한다.
2. 같은 Test에서 마지막 승인 Route를 유지한 채 비정상 URL 5종을 열고, App이 foreground를 유지하며 Route가 AccountSettings에서 변하지 않는지 확인한다.
3. URL 문자열은 기존 승인·비정상 목록과 정확히 같아야 하며, Parser·Product·Native 코드에 Test Hook·Launch Argument·우회 API를 추가하지 않는다.
4. `verify-simulator.sh`의 `simctl openurl` Warm·Rejected Loop는 제거하고, 선행 UI Test Step 성공이 위 XCUITest를 포함하도록 계약 Test와 Evidence 결속을 갱신한다.
5. Shell 후반 Permission과 Lifecycle 재실행은 Home 같은 승인 Route의 저장·복원을 검증하도록 정합화한다. 기존 UI Lifecycle Test의 Notifications 보존 검증은 유지한다.
6. Xcode 26.6/iOS Simulator에서 공식 System Open API를 사용하되 Deployment Target 호환성 Guard가 필요하면 명시하고, 지원 Runtime 미충족을 성공으로 처리하지 않는다.
7. Product Source·AppDelegate·Native/Bridge·Info.plist·공개 API·Signing·Wait/Retry·권한 동작은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 XCUITest 승인 7·비정상 5·foreground·화면 Assert와 Shell `simctl openurl` 제거 계약 RED
- 구현 후 URL 12종 1:1, 단일 Warm App Session, 매 승인 Route UI Assert, 비정상 Route 불변 Assert PASS
- UI Test Runner가 새 Test를 자동 포함하고 실패를 그대로 CI 실패로 연결함을 정적·Fixture로 검증
- Shell Home 준비·Permission 3단계·Lifecycle·Crash/Secret·Binary·종료 검증 유지
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 `DaonUITests.swift`, UI Test Runner의 explicit Test 목록, Simulator Script, 관련 계약 Test, Progress와 Attempt 23 보고서뿐
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속
