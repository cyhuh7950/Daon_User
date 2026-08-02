# R1-M6-10 작업지시서 — CP3 초기 Web Thin Vertical E2E

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M6-10` |
| Issue ID | `R1-M6-10-I001` |
| 상태 | DIRECT_IMPLEMENTATION · 어울1 · CP3 보고 Gate |
| 설계 근거 | 상세 설계서 §18.2, §23.1, §24 |
| 계획 근거 | Release 1 계획 R1-M6-10 · CP3 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M6-10_progress.md` |

## 목적

단일 PDF 여정의 비동기 Run 상태와 계보 계약을 구현하고 CP3 실제 Web E2E 검증을 위한 내부 기준선을 만든다.

## 계약

- Run 상태는 `accepted→planning→retrieving→generating→validating→completed` 순서만 허용한다.
- 모든 상태 전이는 RunSnapshot에 기록한다.
- model attempt, citation, source version, audit trace가 동일 Run ID로 연결된다.
- 실패 전이는 `failed`로 종료하고 완료 상태로 되돌릴 수 없다.
- 브라우저 절대주소·Mock 성공 경로·외부 배포는 추가하지 않는다.

## CP3 검증 경계

내부 자동 테스트는 실행하지만, 실제 Process·DB·Object Storage·모델·Production Chrome을 연결한 단일 PDF Web E2E는 이 환경에서 수행하지 않는다. 따라서 이 Work Order 결과는 CP3 `VERIFYING`이며 신산님 Go/No-Go 판단이 필요하다.

## 허용 변경 파일

- `services/api/src/daon_user_api/run_orchestration.py`
- `services/api/tests/test_run_orchestration.py`
- 본 Work Order 진행·결과 문서
