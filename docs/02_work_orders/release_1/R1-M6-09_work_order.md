# R1-M6-09 작업지시서 — CP3 Core 근거·결과 상태

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-09` |
| Issue ID | `R1-M6-09-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §7.4, §8.3, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-09 · CP3 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-09_progress.md` |

## 목적

질문 결과에 PDF Page Citation을 연결하고 근거 충분·부분·부족 상태를 숨기지 않도록 한다.

## 계약

- Citation은 source_id·source_version·page·chunk_id·문맥을 가진다.
- SourceVersion 불일치 Citation은 거부한다.
- 근거 개수에 따라 `sufficient`, `partial`, `insufficient`를 산출한다.
- Citation은 원문 Page 재현에 필요한 계보만 보존하며 원문·비밀값을 로그에 남기지 않는다.

## 허용 변경 파일

- `services/api/src/daon_user_api/citation.py`
- `services/api/tests/test_citation.py`
- 본 Work Order 진행·결과 문서

## 완료 증거

TDD RED→GREEN, 전용·API 전체 unittest, Version mismatch 차단과 세 상태 판정.
