# R1-M3-06-C31 수정 작업지시서 — Settings 알림 행 exact Label·Hittable Query

## 1. 판정

| 항목 | 값 |
| --- | --- |
| 원 issue_id | `R1-M3-06-I007` |
| Attempt | `32` |
| 사유 | C30 exact-SHA에서 cell·staticText 두 타입을 지원했음에도 revoke Phase의 Notifications 행이 동일하게 미검출됨 |
| 실패보고 | 0회 · 어울2 정식 `FAILURE_REPORT` 없음 |
| 단일 Writer | 동일 어울2 |
| 결과보고 | `docs/02_work_orders/reports/R1-M3-06_attempt-32.md` |

## 2. 확인된 증거

- exact Head `8bfed8df9a024283ef347583698731e5fa81f76a`의 iOS Run `30267022852`은 Build와 일반 UI Test를 통과했다.
- Permission은 3분 42초 뒤 다시 `CODE=SETTINGS_NOTIFICATION_ROW_MISSING PHASE=revoke EXIT=65`다.
- C30은 English/Korean exact Label의 `cell`과 `staticText`를 모두 지원했으므로 iOS 26.6 Settings 행은 둘이 아닌 접근성 타입으로 노출된다.
- 타입을 button/link 등으로 계속 추측하면 OS 버전별 취약성이 반복된다. System Settings 경계에서는 타입보다 exact Label·실제 Tap 가능성·단일성 계약이 본질이다.

## 3. 설계 판단과 필수 작업

1. Settings App 전체 접근성 Tree에서 exact Label이 `Notifications` 또는 `알림`이고 `isHittable == true`인 요소를 타입 비의존으로 조회한다.
2. Predicate는 `label ==`과 `isHittable == true`의 exact 조건만 허용한다. 부분 문자열·정규식·contains·begins/ends·identifier 추측은 금지한다.
3. English/Korean 후보를 합쳐 정확히 한 요소일 때만 반환한다. 0개 또는 2개 이상이면 기존 Fail-close를 유지한다.
4. `firstMatch`, `element(boundBy:)`, 좌표, Index, 임의 우선순위 선택은 사용하지 않는다. Count 1을 검증한 Query의 단일 `element`만 사용한다.
5. C30 typed Helper는 이 exact Label·Hittable Query로 대체하며 타입별 후보와 중복 우선순위 로직은 제거한다.
6. Settings foreground·행 탭·Switch OFF/ON·앱 복귀, C29 고정 Method, Alert/Marker/Timeout, Product·Simulator Script·Workflow는 변경하지 않는다.

## 4. TDD와 완료 조건

- 구현 전 exact Label+Hittable 타입 비의존 Query·단일성·금지 경계 계약 RED
- 구현 후 English/Korean exact Predicate, count==1, Query element 반환
- 부분 문자열·정규식·identifier 추측·좌표·Index·firstMatch 0
- iOS·Mobile·Android·전체 Node·Toolchain·Workflow/Bash·`git diff --check` PASS
- 허용 변경은 Permission XCTest의 Notifications 행 Helper, 관련 계약 Test, Progress와 Attempt 32뿐이다.
- Product·Simulator Script·Workflow·Quality 정책, Commit·Push·PR·Merge·GitHub 실행·SSH·서버·GUI·Signing 금지.

