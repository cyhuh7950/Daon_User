# R1-M7-03 작업지시서 — Windows Cloud 모델·Daon 선택

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M7-03` |
| Issue ID | `R1-M7-03-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 |
| 설계 근거 | 상세 설계서 §10.4~§10.5, §12, §18.2 |
| 계획 근거 | Release 1 계획 R1-M7-03 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M7-03_progress.md` |

## 목적

Windows Cloud-sync Workspace에서 Local·Internal·External·Daon 모델 선택과 Route·Fallback·Egress·Audit 계보를 고정한다.

## 계약

- 사용자 선택 모드와 실제 deployment·network·egress가 일치한다.
- Local-private 자료는 Cloud/External/Daon으로 전송하지 않는다.
- `auto` Fallback은 승인된 동일 역할 후보로 제한한다.
- 모든 선택은 policy version·deployment·egress·audit 사유를 남긴다.
- 실제 Windows UI·Daon Sandbox·Cloud 호출은 후속 통합 검증이다.

## 허용 변경 파일

- `services/api/src/daon_user_api/windows_cloud_routing.py`
- `services/api/tests/test_windows_cloud_routing.py`
- 본 Work Order 진행·결과 문서
