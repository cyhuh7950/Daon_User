# R1-M6-14 작업지시서 — 모델·Routing 확장

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-14` |
| Issue ID | `R1-M6-14-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §10.4, §10.5, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-14 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-14_progress.md` |

## 목적

Local·Internal·External 모델 역할과 `auto`·`local_only`·`pinned` 정책, 승인된 후보만의 Fallback, 비용 한도·waiting_model 판정을 확장한다.

## 계약

- 역할은 `text`, `vision`, `audio_understanding`, `speech_to_text`, `embedding`, `reranker`로 구분한다.
- `local_only`는 Local 후보만, `pinned`는 지정 후보만 사용한다.
- `auto` Fallback은 동일 역할의 승인된 후보만 사용한다.
- 비용 한도 초과는 `COST_LIMIT_EXCEEDED`로 종료하며 자동 재시도하지 않는다.
- 후보가 없으면 `NO_AVAILABLE_DEPLOYMENT`, 일시적 모델 불가 상태는 `waiting_model`로 구분한다.
- 데이터 영역·Egress 정책은 기존 Routing 계약을 우회하지 않는다.

## 제외

실제 외부 Provider 호출, Web UI, 서버 배포, M6-10의 실제 E2E 재실행.

## 허용 변경 파일

- `services/api/src/daon_user_api/model_routing_expansion.py`
- `services/api/tests/test_model_routing_expansion.py`
- 본 Work Order 진행·결과 문서
