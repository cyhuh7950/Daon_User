# R1-M3-06-C38 수정 작업지시서 — exact Label 존재 여부 분류

## 판정

- `issue_id`: `R1-M3-06-I007`
- Attempt: `39`
- 근거: exact Head `6be953aafb3851f694772db77059a499d5ce0338`, iOS Run `30282607565`은 Build·UI Test 성공 후 revoke에서 `SETTINGS_NOTIFICATION_SEMANTIC_ROW_ZERO / 65`.
- 정식 `FAILURE_REPORT`: 0회.

## 필수 작업

1. 현재 direct exact Label Query의 전체 접근성 요소를 대기 뒤 수집한다.
2. 전체 exact Label 요소가 0건이면 `SETTINGS_NOTIFICATION_LABEL_ZERO`, 1건 이상이나 Hittable 0건이면 `SETTINGS_NOTIFICATION_LABEL_NONHITTABLE`로 고정 분류한다.
3. 기존 direct Hittable 1건 우선, semantic Cell fallback, 다건 Fail-close와 성공 경로는 변경하지 않는다.
4. Bash Allowlist/parser는 새 두 Code를 generic보다 먼저 판정한다.
5. 실제 count·Raw Tree·UI Text·좌표·환경값을 출력하지 않는다.
6. 관련 XCTest·Bash 계약 RED→GREEN, Mobile·Node·Toolchain·Workflow/Bash·Diff 검증 후 Progress와 `docs/02_work_orders/reports/R1-M3-06_attempt-39.md`를 작성한다.
7. Commit·Push·PR·GitHub·SSH·GUI·Signing 금지.

