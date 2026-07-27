# R1-M3-06-C30 수정 작업지시서 — iOS Settings 알림 행 접근성 타입 정합

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `31` |
| 사유 | C29 exact-SHA에서 grant-initial은 통과하고 revoke Phase의 Settings 진입 뒤 정확한 Notifications 행 조회만 실패함 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-31.md` |

## 2. 확인된 증거

- exact Head `55016d3c7dcf84e008a6be8adf6438d404053425`의 iOS Run `30264900365`은 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test를 모두 통과했다.
- Permission Step은 3분 27초 실행 후 `CODE=SETTINGS_NOTIFICATION_ROW_MISSING PHASE=revoke EXIT=65`로 종료됐다.
- 따라서 C29 고정 Method와 grant-initial의 Camera·Microphone·Notification 실제 System Alert·Allow·결과 확인은 통과했다.
- revoke Phase는 Production 설정 버튼 탭과 Settings foreground까지 통과했고, `settings.cells["Notifications"/"알림"]` 조회에서만 실패했다.
- iOS 26.6 Settings의 exact Label은 유지되지만 행이 XCUI `cell`로 노출되지 않는 접근성 Element Type 차이가 확인 원인이다.

## 3. 필수 작업

1. Notifications/알림 행은 현재 iOS 26.6에서 실제 노출되는 exact Label 접근성 요소 타입을 추가 지원한다. 우선 `staticTexts` exact Label을 승인 후보로 검증하되, 근거 없이 광범위한 Descendant Query를 사용하지 않는다.
2. 기존 `cells` 후보와 신규 exact 후보가 동시에 존재할 때 중복 UI를 두 행으로 오판하지 않도록 동일 Label의 단일 행 의미를 유지한다. 구현 전 XCTest Query 동작과 기존 `requireExactElement`의 중복 집계 영향을 검토한다.
3. 좌표·Index·`firstMatch`·부분 문자열·Private URL·Settings Defaults·TCC 직접 수정은 금지한다.
4. Settings foreground, Notifications 행 탭, Allow Notifications exact Switch, OFF/ON 전후값, 앱 복귀, 동일 설치·세 Phase 계약을 유지한다.
5. C29 고정 Method, Alert Selector·순서·Timeout, APP_LAUNCH_ROOT 이전/이후 Marker, Product·Native·Bridge·Project·Workflow는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 iOS 26.6 exact Label의 비-cell 접근성 요소 지원과 금지 경계 계약 RED
- 구현 후 English/Korean exact Label 후보, 단일 행 의미, 기존 cell 호환성 유지
- 좌표·Index·`firstMatch`·부분 문자열 탐색 0
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Permission XCTest의 Notifications 행 Query Helper, 관련 계약 Test, Progress와 Attempt 31뿐이다.
- Simulator Script·Product·Workflow·Quality 정책, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 변경 또는 수행하지 않는다.

