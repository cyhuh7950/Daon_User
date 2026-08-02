# R1-M7-01 작업지시서 — Web Cloud-sync 지식 대화

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M7-01` |
| Issue ID | `R1-M7-01-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 · CP3 선행 미실증 |
| 설계 근거 | 상세 설계서 §4.1, §7~§10, §23.1 |
| 계획 근거 | Release 1 계획 R1-M7-01 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M7-01_progress.md` |

## 목적

Web Cloud-sync Workspace에서 파일·직접 입력·인터넷·Daon·생산 지식 범위를 하나의 대화 요청으로 연결하고 Run/Citation 계보를 보존한다.

## 계약

- 대화 요청은 same-origin 상대 경로를 전제로 하며 내부 API 절대주소를 보관하지 않는다.
- Workspace·Tenant·SourceVersion 범위를 고정하고 질문 결과에 Run ID와 Citation을 연결한다.
- Cloud-sync 범위 밖 Local-private Source는 자동 포함하지 않는다.
- LLM 일반 지식은 인용 Source로 위장하지 않는다.
- 실제 Browser·DB·Object Storage·Provider 호출은 이 작업에서 수행하지 않는다.

## 허용 변경 파일

- `services/api/src/daon_user_api/workspace_conversation.py`
- `services/api/tests/test_workspace_conversation.py`
- 본 Work Order 진행·결과 문서
