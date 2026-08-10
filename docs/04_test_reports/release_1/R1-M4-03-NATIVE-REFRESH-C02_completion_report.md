# R1-M4-03 Native Refresh Runtime 보정 완료 보고서

## 판정

`COMPLETED`

## 수행 결과

- `POST /api/v1/session/refresh` Runtime Route를 추가했다.
- 요청은 `refresh_credential` 단일 필드만 허용하며, 기존 `IdentityService.rotate_refresh`를 정확히 한 번 호출한다.
- 성공 시 Cookie 없이 Native Login과 동일한 Safe Session Projection 및 새 opaque Access·Refresh Credential을 반환한다.
- Refresh 재사용은 기존 Domain의 Family·Session 철회를 그대로 사용하고, Runtime은 `REFRESH_REPLAYED` Safe Error로 Fail-close 한다.
- Refresh Route만 일반 `Idempotency-Key` 필수 규칙에서 제외했다. 다른 POST 검사는 유지했다.
- OpenAPI 성공 응답을 `NativeCredentialSessionResponse`로 정정하고 Request writeOnly·추가 필드 금지 검증을 강화했다.

## TDD 증거

| 단계 | 명령 | 결과 |
| --- | --- | --- |
| RED HTTP | `uv run --isolated --frozen --project services/api --with pytest==9.0.3 python -m pytest services/api/tests/test_runtime_http.py services/api/tests/test_identity_sessions.py -q` | 2 failed, 20 passed. 새 Route 부재로 404 발생 확인. |
| RED OpenAPI | `node --test scripts/tests/openapi-contract.test.mjs` | 1 failed, 12 passed. Refresh Idempotency Header 잔존 확인. |
| GREEN OpenAPI | `node --test scripts/tests/openapi-contract.test.mjs` | 13/13 PASS |
| Evidence | `node scripts/verify-openapi-contract.mjs --write`; `node scripts/verify-openapi-contract.mjs` | 모두 PASS, SHA `67A0735E298D09622160595E10EDB69613074B264088B5A9E3062F196322399D` |
| GREEN focused | 위 Python focused 명령 | 22 passed |
| 전체 API | `uv run --isolated --frozen --project services/api --with pytest==9.0.3 python -m pytest services/api/tests -q` | 290 passed, 25 skipped, 19 warnings, 132 subtests passed |
| 최종 점검 | `git diff --check`; 변경 파일 Secret scan | PASS; secret match 0 |

## 변경 파일

- `services/api/src/daon_user_api/runtime.py`
- `services/api/tests/test_runtime_http.py`
- `packages/contracts/openapi/v1/openapi.json`
- `scripts/tests/openapi-contract.test.mjs`
- `scripts/verify-openapi-contract.mjs`
- `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json`
- 이 Progress 및 완료 보고서

## 보안·보존 확인

- Password·Access·Refresh 원문을 Log·Audit metadata·Safe Error·OpenAPI Example/Default에 추가하지 않았다.
- 기존 Web Login Cookie, Native Login, OIDC, Identity Domain, Migration, Web/Desktop/Recovery Route는 수정하지 않았다.
- 작업 시작 시 존재한 사용자 삭제 31건과 미추적 문서 3건을 보존했다.
- Commit·Push·배포·Browser·실제 Credential 사용은 수행하지 않았다.

## 남은 사항

이 보정은 Runtime/OpenAPI/API 자동 검증 완료까지다. Windows Credential Vault·HTTPS Client·Tauri Recovery Bridge 실제 화면 검증은 후속 승인 작업 소유다.
