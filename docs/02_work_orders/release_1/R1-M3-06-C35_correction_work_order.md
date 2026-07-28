# R1-M3-06-C35 수정 작업지시서 — Notifications Hittable 후보 0건·다건 분류

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `36` |
| 사유 | C34 exact-SHA에서 Query 생성 오류는 해소됐으나 최종 Hittable 후보 count가 1이 아니며 현재 Code로는 0건과 다건을 구분할 수 없음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-36.md` |

## 2. 확인된 증거

- exact Head `8049fd97020948d026485b661e816925797abb30`의 iOS Run `30275056750`은 Build·일반 UI Test를 통과했다.
- Permission은 revoke Phase에서 `CODE=SETTINGS_NOTIFICATION_ROW_MISSING PHASE=revoke EXIT=65`다.
- C34 이전의 Query 생성 Stage 오류가 generic row missing Assertion으로 진전했으므로 Label-only Query와 Element Hittable 평가 자체는 실행됐다.
- 다음 설계 판단에는 Hittable exact Label 후보가 0건인지 2건 이상인지가 필요하다. 실제 count 값이나 Tree Dump는 필요하지 않다.

## 3. 설계 판단과 필수 작업

1. C34의 Label-only Query, Hittable 전체 필터, 대기, count 1 성공 경로와 반환·tap 동작은 변경하지 않는다.
2. 대기 뒤 Hittable 후보가 0건이면 고정 Assertion 문구와 Code `SETTINGS_NOTIFICATION_ROW_ZERO`로 Fail-close한다.
3. 후보가 2건 이상이면 고정 Assertion 문구와 Code `SETTINGS_NOTIFICATION_ROW_AMBIGUOUS`로 Fail-close한다.
4. 후보가 정확히 1건이면 기존 `COUNT_SINGLE` 이후 경로를 그대로 수행한다.
5. Bash parser는 두 고정 Assertion Code를 기존 generic `SETTINGS_NOTIFICATION_ROW_MISSING`보다 먼저 판정하고 Allowlist에 추가한다. 기존 Assertion 우선·Stage 차선·Phase·Exit 보존은 유지한다.
6. 실제 숫자, Raw Accessibility Tree, UI Label Dump, 좌표, Index, 환경값은 출력하지 않는다.
7. Selector·타입·Label·우선순위·Timeout·Workflow·Product 동작은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 0건/다건/1건 분기와 Parser Code·우선순위 계약 RED
- 구현 후 두 고정 Code, generic 이전 판정, 기존 Stage 차선과 count 1 성공 경로 PASS
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow JSON/Bash·`git diff --check` PASS
- 허용 변경은 Notifications 행 count 분기, Simulator Script의 Allowlist/parser, 관련 계약 Test, Progress와 Attempt 36뿐이다.
- Query Selector·Product·Workflow 구조·Quality·Package/Lock·Project, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing 금지.

