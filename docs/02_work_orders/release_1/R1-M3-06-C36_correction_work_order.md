# R1-M3-06-C36 수정 작업지시서 — exact Label 자식 기반 Settings Cell Fallback

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `37` |
| 사유 | C35 exact-SHA에서 직접 Hittable exact Label 후보가 0건으로 확정됨 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-37.md` |

## 2. 확인된 증거

- exact Head `23f57d8f1a7db305323eaa05376223a34d8c5ad3`의 iOS Run `30277574869`은 Build·일반 UI Test를 통과했다.
- Permission은 revoke에서 `CODE=SETTINGS_NOTIFICATION_ROW_ZERO PHASE=revoke EXIT=65`다.
- C30의 `settings.cells[label]`는 Cell 자신의 exact Label을 가정해 실패했다. C35는 자식 exact Label 요소가 직접 Hittable하지 않음을 확인했다.
- 따라서 exact 제목 자식을 포함하는 Hittable 상위 Cell을 의미적으로 조회하는 방식은 기존 실패 방식과 다르며, 좌표·부분문자열 없이 행 의미를 보존한다.

## 3. 설계 판단과 필수 작업

1. English/Korean exact Label Predicate를 하나의 정본으로 유지한다.
2. 직접 exact Label 요소 중 Hittable 후보가 정확히 1개면 기존 경로를 사용한다. 2개 이상은 AMBIGUOUS Fail-close한다.
3. 직접 Hittable 후보가 0개일 때만 `settings.cells.containing(.staticText, predicate: exactLabelPredicate)`로 상위 행을 조회하고 Hittable Cell만 평가한다.
4. 상위 Hittable Cell이 정확히 1개면 반환한다. 0개는 `SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO`, 2개 이상은 `SETTINGS_NOTIFICATION_SEMANTIC_ROW_AMBIGUOUS`로 Fail-close한다.
5. `cell[label]`, Cell 자신의 Label 가정, button/link 후보, 부분문자열·identifier·firstMatch·Index·좌표는 금지한다.
6. 대기는 직접 Hittable exact Label 또는 Hittable semantic Cell 중 하나가 나타날 때까지로 제한하고 기존 총 Timeout을 늘리지 않는다.
7. Bash Allowlist/parser는 두 새 고정 Code를 generic보다 먼저 판정한다. 기존 Phase·Exit, Assertion 우선·Stage 차선을 유지한다.
8. Settings Switch·tap 이후 흐름, Alert, 세 Phase Method, Product·Workflow 구조는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 direct 1건 우선, direct 0건 semantic Cell fallback, semantic 0/다건 Fail-close, 금지 Query 계약 RED
- 구현 후 exact staticText containment와 단일 Hittable Cell 반환, Parser Code 우선순위 PASS
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow JSON/Bash·`git diff --check` PASS
- 허용 변경은 Notifications 행 Helper, Simulator Script Allowlist/parser, 관련 계약 Test, Progress와 Attempt 37뿐이다.
- Product·Workflow 구조·Quality·Package/Lock·Project, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing 금지.

