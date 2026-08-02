# R1-M6-12 작업지시서 — Daon 승인 지식 Connector

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-12` |
| Issue ID | `R1-M6-12-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §9.3, §12.4, §18.2 |
| 계획 근거 | Release 1 계획 R1-M6-12 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-12_progress.md` |

## 목적

Daon Standard API를 통해 승인 지식을 선택적으로 Read/Search하되 Version·권한·유효기간과 연결 장애 상태를 보존한다.

## 계약

- Connector가 비활성 또는 미설정이면 독립 지식 흐름을 차단하지 않는다.
- 승인 지식 Read/Search에는 권한·Version·만료 시각을 확인한다.
- Timeout·Retry 횟수는 제한하고 연결 끊김은 `disconnected`, 복구는 `connected`로 기록한다.
- 승인되지 않은 API·DB·파일 경로 직접 의존을 추가하지 않는다.

## 제외

실제 Daon Sandbox·네트워크 호출·내부 DB·브라우저 UI·배포.

## 허용 변경 파일

- `services/api/src/daon_user_api/approved_knowledge_connector.py`
- `services/api/tests/test_approved_knowledge_connector.py`
- 본 Work Order 진행·결과 문서
