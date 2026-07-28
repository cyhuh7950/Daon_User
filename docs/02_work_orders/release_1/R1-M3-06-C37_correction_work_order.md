# R1-M3-06-C37 수정 작업지시서 — XCUIElementQuery 공식 containing API 교정

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `38` |
| 사유 | C36 exact-SHA가 App Build 후 UI Test Target 단계에서 Exit 65로 실패했고 사용한 `containing(.staticText, predicate:)`는 Apple 공식 API에 없는 호출임 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-38.md` |

## 2. 확인된 증거와 설계 판단

- exact Head `78d00e7a8b0a934dcb35d67d79b7669f5ee93a50`의 iOS Run `30280074813`은 unsigned App Build까지 통과했으나 `Run iOS UI route, lifecycle and Settings tests`에서 1분 20초 뒤 Exit 65다.
- Apple 공식 `XCUIElementQuery`의 Predicate 기반 descendant containment API는 `containing(_ predicate: NSPredicate)`다.
- 기존 `containing(_:identifier:)`는 Element Type과 identifier용이며 `containing(.staticText, predicate:)` 오버로드는 없다.
- `settings.cells` receiver 자체가 Cell 범위를 제한하므로 `settings.cells.containing(exactLabelPredicate)`가 C36의 exact Label 자식 containment 의미와 동일하다.

## 3. 필수 작업과 완료 조건

1. semantic Query를 `settings.cells.containing(exactLabelPredicate)`로만 교정한다.
2. direct 우선, semantic fallback, Hittable 필터, 0/다건 Fail-close, Timeout, Marker, tap과 Parser Code는 변경하지 않는다.
3. 계약 Test에서 존재하지 않는 시그니처를 금지하고 공식 `containing(exactLabelPredicate)` 호출을 고정한다.
4. iOS 관련 Test, Mobile 전체, Node 전체, Toolchain, Workflow JSON/Bash와 `git diff --check`를 수행한다.
5. 허용 변경은 Notifications Helper의 API 호출 한 곳, 관련 계약 Test, Progress와 Attempt 38뿐이다.
6. Simulator Script·Product·Workflow·Quality·Package/Lock·Project, Commit·Push·PR·GitHub·SSH·GUI·Signing 금지.

