# R1-M3-06-C33 수정 작업지시서 — Settings 알림 행 처리 경계 세분화

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `34` |
| 사유 | C31 exact-SHA가 Notifications 행 처리 Stage에서 Exit 65이나 기존 Assertion Code가 없어 Query·count·element·tap 경계를 구분할 수 없음 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-34.md` |

## 2. 확인된 증거

- exact Head `992d4679dbc2369d5df5db9356a74eede597ecd4`의 iOS Run `30269316851`은 Toolchain·Portable 회귀·Pods·Simulator·unsigned Build·일반 UI Test를 통과했다.
- 실제 Permission 검증은 revoke Phase에서 `CODE=STAGE_SETTINGS_NOTIFICATION_ROW PHASE=revoke EXIT=65`로 종료됐다.
- C30의 `SETTINGS_NOTIFICATION_ROW_MISSING`과 달리 C31 Result에는 기존 Assertion Code가 검출되지 않았다.
- 따라서 exact Label·Hittable Query의 실패라고 단정할 수 없고, Query 생성·대기·count·element 반환·tap 중 실제 마지막 성공 경계를 먼저 확정해야 한다.

## 3. 설계 판단과 필수 작업

1. C31의 exact Label·Hittable·count 1 Selector와 동작은 변경하지 않는다.
2. 기존 `SETTINGS_NOTIFICATION_ROW` 뒤에 다음 고정 Stage를 순서대로 추가한다: Query 생성 완료, Query 대기 완료, count 단일성 통과, element 반환 준비, 행 tap 직전.
3. Stage 이름은 Allowlist enum과 Bash parser에만 고정 추가하고, 모든 출력은 기존 `DAON_PERMISSION_XCTEST_STAGE=<ALLOWLIST>` 형식을 유지한다.
4. Marker는 해당 작업이 실제 성공한 뒤에만 다음 경계를 출력한다. 마지막 Marker가 실패 직전 경계를 정확히 나타내야 한다.
5. count가 0 또는 2건 이상일 때 기존 Fail-close와 오류 의미는 유지한다. Raw Accessibility Tree, UI Text 덤프, 임의 Label, 좌표, Index, 환경값은 출력하지 않는다.
6. Bash Annotation은 새 Stage를 `STAGE_<ALLOWLIST>`로만 변환한다. 기존 우선 Assertion Code 검출·Phase·Exit 보존 순서를 변경하지 않는다.
7. grant-initial·revoke·grant-again 고정 XCTest Method, Alert/Timeout, Settings Switch, Product·Workflow 구조·Simulator Script의 다른 기능은 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 새 Stage enum·정확한 실행 순서·Bash Allowlist/parser/Annotation 계약 RED
- 구현 후 Query 생성→대기→count 1→element 준비→tap 직전 Marker 순서 PASS
- 기존 Assertion Code가 있으면 Stage보다 우선하고, 없을 때만 최신 Stage가 사용되는 계약 유지
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow JSON/Bash·`git diff --check` PASS
- 허용 변경은 Permission XCTest Stage/호출, Simulator 검증 Script의 Allowlist/parser, 관련 계약 Test, Progress와 Attempt 34뿐이다.
- C31 Selector, Product, Package/Lock, Quality 정책, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing 금지.

