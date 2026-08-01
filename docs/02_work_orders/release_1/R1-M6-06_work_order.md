# R1-M6-06 작업지시서 — CP3 단일 PDF Vision/LLM-first 이해

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-06` |
| Issue ID | `R1-M6-06-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §8.2, §18.1, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-06 · CP3 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-06_progress.md` |

## 목적

단일 PDF Source에 대해 Vision/LLM 의미·문맥 이해를 먼저 수행하고, Parser/OCR을 검증·보완과 원문 위치 재현에만 사용하여 `ready` 판정을 보장한다.

## 계약

- 이해 모델 결과가 없거나 실패하면 `ready`가 될 수 없다.
- 처리 순서는 `vision_llm_understanding → parser_ocr_validation → evidence_reconciliation`이다.
- Parser/OCR-only 결과는 `parser_only_not_ready`로 남긴다.
- 불일치·근거 부족은 숨기지 않고 검토 상태와 Page Evidence를 보존한다.
- 모델·Prompt·Policy·보조 도구 계보와 PDF Page를 결과에 남긴다.

## 제외

실제 Vision Provider·OCR 엔진·Object Storage·브라우저 E2E·외부 배포.

## 허용 변경 파일

- `services/api/src/daon_user_api/pdf_understanding.py`
- `services/api/tests/test_pdf_understanding.py`
- 본 Work Order 진행·결과 문서

## 완료 증거

TDD RED→GREEN, 전용·API 전체 unittest, Parser-only `ready` 0건, 외부주소·비밀값 로그·공개 API 추가 0건.
