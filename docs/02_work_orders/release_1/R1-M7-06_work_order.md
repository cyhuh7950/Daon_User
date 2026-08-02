# R1-M7-06 작업지시서 — 오류·만료·축소 운영 회귀

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M7-06` |
| Issue ID | `R1-M7-06-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §18.1~§18.3, §21.3~§21.4 |
| 계획 근거 | Release 1 계획 R1-M7-06 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M7-06_progress.md` |

## 목적

Source 만료·Index/Daon/LLM 장애·Evidence Store 차단·Reconnect 상황에서 사용자에게 안전한 상태와 복구 경로를 제공한다.

## 계약

- Source 만료는 `source_expired`, 자동 인용을 금지한다.
- Index 장애는 `retrieval_degraded`, LLM 장애는 `model_unavailable`로 구분한다.
- Evidence Store 차단 시 근거 없는 결과를 만들지 않고 `evidence_blocked`다.
- Reconnect는 `recovery_pending`에서 정상 연결 후 `recovered`로 전환한다.
- 운영 상태·경고·재처리 사유를 기록한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/operations_regression.py`
- `services/api/tests/test_operations_regression.py`
- 본 Work Order 진행·결과 문서
