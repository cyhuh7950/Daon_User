# R1-M6-15 작업지시서 — 문서·표·이미지 형식 확장

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-15` |
| Issue ID | `R1-M6-15-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §8.2, §18.1 |
| 계획 근거 | Release 1 계획 R1-M6-15 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-15_progress.md` |

## 목적

DOCX·PPTX·XLSX·CSV·TXT·Markdown·주요 이미지가 Vision/LLM-first 의미 이해를 거친 뒤 Parser/OCR 검증·보완과 Cell·Region 원문 계보를 보존하도록 한다.

## 계약

- 지원 형식은 `docx`, `pptx`, `xlsx`, `csv`, `txt`, `markdown`, `png`, `jpeg`이다.
- 의미 이해 결과가 없으면 `ready`가 되지 않는다.
- Parser/OCR은 `validation_only` 역할이며 Parser-only 완료를 금지한다.
- 표는 Cell, 이미지는 Region, 문서는 Page/paragraph 위치를 evidence로 보존한다.
- 형식 불일치·지원하지 않는 형식은 안정적인 거부 사유를 반환한다.

## 제외

실제 Office·OCR·Vision Provider, 외부 Storage, 브라우저 E2E와 배포.

## 허용 변경 파일

- `services/api/src/daon_user_api/format_understanding.py`
- `services/api/tests/test_format_understanding.py`
- 본 Work Order 진행·결과 문서
