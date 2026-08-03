# R1-M8-05 작업지시서 — 지식 구조도·마인드맵

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-05` |
| Issue ID | `R1-M8-05-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.1, §13.4, §7.2 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-05_progress.md` |

## 목적

지식 Node·Edge·조건·근거·신뢰 상태를 구조화해 JSON/SVG/PNG/PDF 변환 계보가 유지 가능한 내부 계약을 만든다.

## 계약

- Node는 `id`, `label`, `confidence`, `evidence`를 가진다.
- Edge는 `source`, `target`, `relation`, `evidence`를 가진다.
- 신뢰 상태는 `verified`, `unverified`, `needs_review`만 허용한다.
- Node·Edge ID는 결과 안에서 고유해야 하며, 근거 없는 verified는 거부한다.
- 실제 SVG/PNG/PDF 렌더링은 후속 증거로 분리한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/knowledge_graph.py`
- `services/api/tests/test_knowledge_graph.py`
- 본 Work Order 진행·결과 문서
