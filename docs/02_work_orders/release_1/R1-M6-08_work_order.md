# R1-M6-08 작업지시서 — CP3 Core Index·Retrieval

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-08` |
| Issue ID | `R1-M6-08-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §7.1, §8.3, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-08 · CP3 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-08_progress.md` |

## 목적

단일 PDF의 의미 Chunk를 SourceVersion과 함께 색인하고 질문에 필요한 Chunk만 검색하도록 한다.

## 계약

- Chunk는 `source_id`, `source_version`, `page`, `text`, `chunk_id`를 가진다.
- 검색은 지정 SourceVersion에 고정되고 다른 버전·Source를 섞지 않는다.
- 질문 토큰과 일치하는 Chunk를 점수 내림차순으로 반환한다.
- 빈 질문·미색인 Source는 안전하게 빈 결과 또는 안정 오류를 반환한다.
- 외부 Vector DB·공개 API·브라우저 호출은 추가하지 않는다.

## 허용 변경 파일

- `services/api/src/daon_user_api/pdf_index.py`
- `services/api/tests/test_pdf_index.py`
- 본 Work Order 진행·결과 문서

## 완료 증거

TDD RED→GREEN, 전용·API 전체 unittest, SourceVersion 격리와 검색 결과 근거 보존 확인.
