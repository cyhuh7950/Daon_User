# R1-M8-03 작업지시서 — 제약·준수 점검표

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-03` |
| Issue ID | `R1-M8-03-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.1, §13.4, §7.4 |
| 계획 근거 | Release 1 계획 R1-M8-03 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-03_progress.md` |

## 목적

RuleSet과 근거를 사용해 제약·준수 점검 결과를 생성하고, 각 항목의 판정·근거·조치·RuleSet 계보를 보존한다.

## 계약

- 결과 항목은 `item_id`, `judgement`, `evidence`, `ruleset_id`, `action`을 가진다.
- 허용 판정은 `compliant`, `non_compliant`, `needs_review`다.
- 근거가 없는 `compliant` 판정은 허용하지 않고 `needs_review`와 `missing_evidence` 경고로 바꾼다.
- RuleSet 계보(`ruleset_id`, `ruleset_version`)와 요청 계보(`request_id`, `model_id`)를 결과에 남긴다.
- 실제 XLSX·CSV·PDF 파일 생성·Open·Layout 렌더링은 후속 증거로 분리한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/compliance_check.py`
- `services/api/tests/test_compliance_check.py`
- 본 Work Order 진행·결과 문서
