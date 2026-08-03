# R1-M8-06 작업지시서 — 업무 문서 초안

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-06` |
| Issue ID | `R1-M8-06-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.1, §13.4, §13.5 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-06_progress.md` |

## 목적

Template·Section·근거·편집·검토 상태를 보존하는 업무 문서 초안 내부 계약을 구현한다.

## 계약

- 초안은 `template_id`, `sections`, `evidence`, `review_state`, `lineage`를 가진다.
- Section은 제목·본문·근거 목록을 보존한다.
- 허용 검토 상태는 `draft`, `in_review`, `revision_requested`, `approved`다.
- 근거 없는 Section은 `unverified` 경고를 가진다.
- 실제 DOCX·PDF 생성·Open·Layout 검증은 후속 증거로 분리한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/document_draft.py`
- `services/api/tests/test_document_draft.py`
- 본 Work Order 진행·결과 문서
