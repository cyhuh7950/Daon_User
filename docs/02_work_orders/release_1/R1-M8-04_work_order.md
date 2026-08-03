# R1-M8-04 작업지시서 — 비교·데이터 표

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-04` |
| Issue ID | `R1-M8-04-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.1, §13.4, §7.4 |
| 계획 근거 | Release 1 계획 R1-M8-04 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-04_progress.md` |

## 목적

두 SourceVersion의 기준·값을 비교해 차이·누락·충돌을 표현하고, 각 행의 원문 Cell/Region 근거와 Version 계보를 보존한다.

## 계약

- 출력 행은 `key`, `baseline`, `current`, `difference`, `state`, `evidence`, `baseline_version`, `current_version`을 가진다.
- `state`는 `same`, `changed`, `missing`, `conflict` 중 하나다.
- baseline/current 값이 모두 다르면 `changed`, 양쪽 값이 서로 다른 출처로 충돌하면 `conflict`를 명시한다.
- 양쪽 값이 없거나 한쪽이 없으면 `missing`으로 표시하고 원문 근거 없는 비교 결과는 만들지 않는다.
- 실제 XLSX·CSV·PDF 파일 생성·Open·Layout 렌더 검증은 후속 증거로 분리한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/comparison_table.py`
- `services/api/tests/test_comparison_table.py`
- 본 Work Order 진행·결과 문서
