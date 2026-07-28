# R1-M3-06-C34 수정 작업지시서 — exact Label Query와 Hittable 평가 분리

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `35` |
| 사유 | C33 exact-SHA에서 Helper 진입 뒤 Query 생성 완료 Marker 전 Exit 65가 재현되어 `isHittable` Predicate 결합이 Runtime 실패 경계로 확정됨 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-35.md` |

## 2. 확인된 증거

- exact Head `9a2451c0c19be7f4a23e4a5ad869a39dbaaca28b`의 iOS Run `30272461997`은 Build·일반 UI Test를 통과했다.
- Permission은 revoke Phase에서 다시 `CODE=STAGE_SETTINGS_NOTIFICATION_ROW PHASE=revoke EXIT=65`다.
- C33의 첫 추가 Marker `SETTINGS_NOTIFICATION_QUERY_CREATED`가 출력되지 않았으므로 실패는 `settings.descendants(...).matching(NSPredicate(...isHittable...))` 생성식 안이다.
- Label exact Match 자체는 XCTest의 지원 경계이며, `isHittable`은 Element 속성 평가로 분리할 수 있다.

## 3. 설계 판단과 필수 작업

1. XCUI Query Predicate에서는 English/Korean exact `label ==` 조건만 사용하고 `isHittable` 조건을 제거한다.
2. Query 생성 성공 직후 기존 `SETTINGS_NOTIFICATION_QUERY_CREATED` Marker를 유지한다.
3. Query의 접근성 요소 전체를 배열로 평가해 `isHittable == true`인 요소만 Swift에서 필터링한다. `firstMatch`, `element(boundBy:)`, 좌표, 임의 타입·identifier·부분문자열 추측은 금지한다.
4. 대기는 Hittable exact-Label 요소가 하나 이상 생길 때까지로 한다. 대기 완료 뒤 Hittable Match 전체를 다시 수집한다.
5. Hittable Match가 정확히 1개일 때만 해당 단일 요소를 반환한다. 0개 또는 2개 이상은 기존 Fail-close를 유지한다. count 검증 뒤 단일 Array에서 `popLast()` 같은 비선택적 추출은 허용하되 모호한 후보 우선순위는 금지한다.
6. C33 Marker 순서, 행 tap, Settings Switch OFF/ON, Alert/Timeout, 세 Phase Method와 Bash Assertion 우선순위를 보존한다.
7. Raw Accessibility Tree·Label Dump·좌표·Index·환경값 출력은 추가하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 Predicate의 `isHittable` 금지, exact Label-only Query, Element 배열의 Hittable 필터, count 1 뒤 단일 추출 계약 RED
- 구현 후 C33 Marker 순서와 기존 Fail-close·tap 동작 PASS
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow JSON/Bash·`git diff --check` PASS
- 허용 변경은 Notifications 행 Helper, 관련 계약 Test, Progress와 Attempt 35뿐이다.
- Simulator Script·Product·Workflow·Quality·Package/Lock·Project, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing 금지.

