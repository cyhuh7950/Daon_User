# R1-M3-06-C40 수정 작업지시서 — Settings bounded scroll search

## 판정

- `issue_id`: `R1-M3-06-I007`, Attempt `41`, 정식 실패 0회.
- 근거: Head `98ca1ac2437ec8f72259a83b0877a4eb793bcaab`, Run `30286751361`은 Build·UI Test 성공 후 revoke에서 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO / 65`.

## 필수 작업

1. 기존 direct exact·semantic·delimiter-anchored composite Query와 단일성 규칙을 변경하지 않는다.
2. 최초 평가에서 후보가 모두 0건일 때만 Settings 앱을 최대 4회 `swipeUp()`하고 각 회차 뒤 동일 Query를 재평가한다.
3. 후보 발견 즉시 스크롤을 중지한다. 직접/semantic/composite 우선순위와 다건 Fail-close는 유지한다.
4. 좌표·Index·무제한 반복·sleep·추가 Label/identifier/부분문자열은 금지한다.
5. 고정 Stage `SETTINGS_NOTIFICATION_SCROLL_SEARCH`를 추가하고, 최종 0건은 기존 COMPOSITE_ZERO를 유지한다.
6. XCTest·Bash Parser/Allowlist·계약 Test를 TDD하고 Mobile·Node·Toolchain·Workflow/Bash·Diff를 검증한다.
7. Progress와 `docs/02_work_orders/reports/R1-M3-06_attempt-41.md` 작성. 외부 작업 금지.

