# R1-M6-16 작업지시서 — 전체 권위·가중 Retrieval·충돌

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-16` |
| Issue ID | `R1-M6-16-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §7.1~§7.4, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-16 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-16_progress.md` |

## 목적

다섯 지식 원천을 권위 Tier·사용자 가중치·신선도로 검색하고, Daon 승인 지식·RuleSet의 우선순위를 보존하며 충돌을 검토 상태로 전환한다.

## 계약

- Tier 순위: `daon_approved` > `user_registered` > `user_file` > `internet` > `llm_general`.
- 사용자 가중치는 `0.5~2.0`으로 Clamp한다. 가중치가 Tier 권위를 뒤집지 못한다.
- 같은 Tier 내에서는 relevance·weight·freshness 점수로 정렬한다.
- 상위 권위와 하위 권위가 상충하면 상위 권위를 유지하고 충돌 기록을 남긴다.
- 같은 중요도 충돌은 `review` 상태로 전환한다.
- 중복 Source를 곱셈으로 중복 반영하지 않는다.

## 제외

실제 Embedding/Reranker·외부 Vector DB·브라우저 UI·배포.

## 허용 변경 파일

- `services/api/src/daon_user_api/knowledge_retrieval.py`
- `services/api/tests/test_knowledge_retrieval.py`
- 본 Work Order 진행·결과 문서
