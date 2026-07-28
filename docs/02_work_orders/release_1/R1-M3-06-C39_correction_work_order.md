# R1-M3-06-C39 수정 작업지시서 — Settings 복합 Cell Label anchored fallback

## 판정

- `issue_id`: `R1-M3-06-I007`, Attempt `40`, 정식 실패 0회.
- 근거: Head `5489b13f5926767ae7ccb3986a3b11f5a61f0ac2`, iOS Run `30284615872`은 Build·UI Test 성공 후 revoke에서 `SETTINGS_NOTIFICATION_LABEL_ZERO / 65`.

## 필수 작업

1. 기존 direct exact Label·semantic Cell 경로를 우선 유지한다.
2. exact Label 자체가 0건이고 semantic Cell도 0건일 때만 Settings Cell의 복합 Label fallback을 평가한다.
3. 허용 Predicate는 Cell에 한정해 `label == "Notifications"`, `label BEGINSWITH "Notifications,"`, `label == "알림"`, `label BEGINSWITH "알림,"`만 사용한다. 구분자 없는 prefix·CONTAINS·regex·identifier·button/link·좌표·Index는 금지한다.
4. Hittable 복합 Cell이 정확히 1개면 반환하고, 0개는 `SETTINGS_NOTIFICATION_COMPOSITE_ROW_ZERO`, 다건은 `..._AMBIGUOUS`로 Fail-close한다.
5. Bash Allowlist/parser·계약 Test를 갱신하고 Mobile·Node·Toolchain·Workflow/Bash·Diff를 검증한다.
6. Progress와 `docs/02_work_orders/reports/R1-M3-06_attempt-40.md`를 작성한다. 외부 작업 금지.

