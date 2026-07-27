# R1-M3-06-C27 수정 작업지시서 — Permission XCTest 마지막 단계 Marker

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `28` |
| 사유 | C26 공개 Annotation은 동작했으나 xcodebuild 출력에 승인 Assertion 원문이 없어 `UNKNOWN_XCTEST_FAILURE / grant-initial / 65`로만 분류됨 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-28.md` |

## 2. 확인된 증거

- exact Head `8eed1d910db82ccb2e32b7f4800c75193cc395c1`의 iOS Run `30259306171`은 Build와 선행 UI Test가 SUCCESS다.
- Permission Step은 약 93초 후 Exit 65이며 공개 Annotation은 `CODE=UNKNOWN_XCTEST_FAILURE PHASE=grant-initial EXIT=65`다.
- C26의 원문 기반 분류기는 xcodebuild가 Custom Assertion 원문을 Console에 제공하지 않는 경우를 구분하지 못한다.
- 같은 Run의 Quality Gate 공통 검사는 Exit 1이었으나 C26 로컬 전체 회귀와 직전 exact-SHA Quality는 PASS다. 다음 exact-SHA에서 함께 재검증하며 현 시점에 별도 코드 수정 근거로 삼지 않는다.

## 3. 필수 작업

1. Permission XCTest의 실제 검증 직전에 `DAON_PERMISSION_XCTEST_STAGE=<allowlisted code>` Marker를 Console에 남긴다.
2. 최소 단계는 Phase/Expected 결속, App Launch/Root, Camera Request/Result, Microphone Request/Result, Notification Request, Alert Title/Count/Allow/Dismissal, Settings Foreground/Row/Switch Read/Toggle/Verify, App Return Root, Notification Result를 구분한다.
3. Marker 값은 고정 Literal Allowlist만 사용하고 Phase·경로·UDID·URL·사용자 데이터·Raw Element 값은 포함하지 않는다.
4. Shell 분류기는 기존 승인 Assertion Code가 있으면 이를 우선하고, 없으면 Log의 마지막 허용 Stage Marker를 `STAGE_<code>`로 매핑한다. Marker도 없으면 기존 UNKNOWN을 유지한다.
5. 공개 Annotation 형식·Phase·숫자 Exit·원 Exit·Raw Evidence Log·xcresult 계약을 유지한다.
6. C25 Alert/Settings Selector·Timeout·검증 순서·권한/제품 동작과 Workflow는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 마지막 Stage Marker 기반 안전 Code 계약 RED
- 구현 후 여러 Marker와 미지 Marker가 섞인 Log에서 마지막 허용 Marker만 `STAGE_*`로 출력하고 Raw 값은 Annotation에 노출하지 않음
- 기존 Assertion Code 우선순위와 Unknown/성공/원 Exit Fixture 유지
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Permission XCTest의 고정 Marker, Simulator Script 분류, 관련 계약 Test, Progress와 Attempt 28뿐이다.
- Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Apple Signing은 어울1 후속이다.

