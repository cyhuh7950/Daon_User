# R1-M8-01 작업지시서 — Studio 생성 설정·공통 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-01` |
| Issue ID | `R1-M8-01-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.2, §18.3 |
| 계획 근거 | Release 1 계획 R1-M8-01 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-01_progress.md` |

## 목적

Studio 산출물을 즉시 생성하지 않고 목적·독자·Source·RuleSet·분량·형식·검토 조건을 설정·확정한 뒤 불변 Snapshot으로 제출한다.

## 계약

- `configuring → confirmed → submitted` 상태 전이를 강제한다.
- confirmed 이후 설정은 잠기며 변경은 새 Revision/Request가 된다.
- GenerationSettingsSnapshot은 목적·독자·SourceVersion·RuleSet·format·review 조건을 보존한다.
- 필수 설정이 없으면 제출하지 않는다.
- 실제 DOCX/PDF/XLSX/SVG/PNG 생성·Layout 검증은 후속 Work Order다.

## 허용 변경 파일

- `services/api/src/daon_user_api/generation_settings.py`
- `services/api/tests/test_generation_settings.py`
- 본 Work Order 진행·결과 문서
