# R1-M8-07 작업지시서 — 검토·승인·전달

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M8-07` |
| Issue ID | `R1-M8-07-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §13.5, §14, §18.3 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-07_progress.md` |

## 목적

ReviewRequest·ApprovalRequest·Approval·Delivery의 안전한 상태 전이와 만료·회수·재승인 계보를 구현한다.

## 계약

- 승인 요청 기본 만료는 7일이며 허용 범위는 1~30일이다.
- 허용 상태는 `pending`, `approved`, `rejected`, `expired`, `withdrawn`, `delivered`다.
- 승인 후 내용 변경은 새 승인 요청을 요구한다.
- 만료·회수는 자동 승인하지 않는다.
- 외부 전달은 추가 인증 상태 없이는 거부한다.

## 허용 변경 파일

- `services/api/src/daon_user_api/approval_workflow.py`
- `services/api/tests/test_approval_workflow.py`
- 본 Work Order 진행·결과 문서
