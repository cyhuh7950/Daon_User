# R1-M4-03 Native Refresh Runtime 보정 작업지시서

## 1. 문서 정보

| 항목 | 내용 |
| --- | --- |
| Work Order ID | `R1-M4-03-NATIVE-REFRESH-C02` |
| issue_id | `R1-M4-03-NATIVE-REFRESH-C02-I001` |
| 버전 | `1.0` |
| 상태 | `READY` |
| 성격 | 승인된 Native Refresh 공개 계약의 Runtime 누락 보정 |
| 공식 작업공간 | `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User` |
| Branch | `master` |
| 기준선 | `c08f5bf` 이상 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M4-03-NATIVE-REFRESH-C02_progress.md` |
| 완료보고 | `docs/04_test_reports/release_1/R1-M4-03-NATIVE-REFRESH-C02_completion_report.md` |

## 2. 승인 기준

`AGENTS.md`, 상세 설계 §4.3, 계획 1.8 R1-M4-03·R1-D028, Windows Recovery 설계 1.1 §5.3.1, `APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01`, Task 1 완료보고와 현재 OpenAPI `/api/v1/session/refresh`·`NativeRefreshRequest`를 EOF/해당 계약 전체까지 읽고 진행 기록에 남긴다.

## 3. 확인된 결함

- `IdentityService.rotate_refresh`와 OpenAPI Path는 존재하지만 FastAPI Runtime Route·Request Body가 없다.
- 현재 OpenAPI Refresh 성공 응답은 새 opaque Access·Refresh를 전달하지 않는 구형 `IdentitySessionResponse`를 가리킨다.
- 현재 OpenAPI는 일반 POST 규칙의 `Idempotency-Key`를 요구하지만 승인 보안 계약은 Refresh Credential의 단일 사용과 재사용 시 Family·Session 철회를 요구한다. 동일 Refresh 재시도를 성공 응답으로 재생하면 이 계약을 약화한다.

## 4. 구현 계약

1. `POST /api/v1/session/refresh` Runtime Route를 추가한다.
2. Body는 `refresh_credential` 하나만 허용하고 추가 필드는 거부한다. Cookie·Bearer·Client Platform 입력은 받지 않는다.
3. `IdentityService.rotate_refresh`를 단 한 번 호출하고 별도 회전 로직을 복제하지 않는다.
4. 성공 응답은 Task 1과 같은 `NativeCredentialSessionResponse`: user/tenant/workspace/session/device, `native`, `native_https_opaque_bearer`, 새 opaque Access·Refresh, Access 만료 시각이다. Cookie는 0건이다.
5. Workspace는 현재 Tenant의 Primary Workspace 또는 기존 Personal Workspace 규칙으로 계산한다. 새 Tenant·Membership을 만들지 않는다.
6. 기존 Refresh 재사용은 `REFRESH_REPLAYED`, Family·Session 철회로 Fail-close하고 성공 응답을 재생하지 않는다.
7. 이 정확한 Refresh Route는 일반 업무 Write용 `Idempotency-Key`를 요구하지 않는다. OpenAPI와 Validator의 정확한 Path 예외를 추가하고 다른 POST의 Header 검사는 유지한다.
8. OpenAPI 성공 응답을 `NativeCredentialSessionResponse`로 정정하고 Request Credential은 `writeOnly`, Example·Default 없음, Response Credential도 기존 비노출 Validator를 통과한다.
9. Password·Refresh·Access 원문은 Log·Audit metadata·Safe Error·Example·Default에 포함하지 않는다.
10. Domain·Migration·Web·Desktop·Recovery Route는 수정하지 않는다.

## 5. TDD·검증

- RED: Route 404, 성공 응답 Credential/Workspace/Cookie, extra field, invalid·expired·replayed Refresh, OpenAPI 성공 Schema·Idempotency Header 부재.
- GREEN: 최소 Runtime Body/Route와 OpenAPI/Validator 정합화.
- 필수 명령:

```powershell
$env:PYTHONPATH='services/api/src;services/api'; uv run --isolated --frozen --project services/api --with pytest==9.0.3 python -m pytest services/api/tests/test_runtime_http.py services/api/tests/test_identity_sessions.py -q
node --test scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
$env:PYTHONPATH='services/api/src;services/api'; uv run --isolated --frozen --project services/api --with pytest==9.0.3 python -m pytest services/api/tests -q
git diff --check
```

## 6. 허용 변경 경로

- `services/api/src/daon_user_api/runtime.py`
- `services/api/tests/test_runtime_http.py`
- 필요 시 `services/api/tests/test_identity_sessions.py`는 기존 Domain 회귀 강화만 허용
- `packages/contracts/openapi/v1/openapi.json`
- `scripts/tests/openapi-contract.test.mjs`
- `scripts/verify-openapi-contract.mjs`
- `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- `docs/04_test_reports/release_1/R1-M4-03-NATIVE-REFRESH-C02_progress.md`
- `docs/04_test_reports/release_1/R1-M4-03-NATIVE-REFRESH-C02_completion_report.md`

허용 경로 밖 변경이 필요하면 어울1에게 되돌린다.

## 7. 보존·결과 계약

사용자 삭제 31건·미추적 문서 3건, 기존 Web Login, Task 1 Native Login, OIDC, Identity Domain, DB·운영 데이터를 보존한다. Commit·Push·배포·Browser·실제 Credential 사용은 하지 않는다.

진행 기록 형식:

`recorded_at | stage | status | completed | changed_files | commands_and_tests | error_cause_recovery | next_step | commit_or_build`

결과 형식:

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`
