# R1-M4-03-C01 완료 보고서

## 판정

`COMPLETED` — C01 보안 검토 지적을 승인 범위 안에서 보완했고 필수 관련 검증이 모두 통과했다.

## 판단 이유

- `POST /api/v1/session/revoke`와 동일 의미의 `IdentityService.revoke_session`을 추가했다.
- 철회 권한은 `device_session_or_sync_key_revoke` Step-up을 Actor·현재 Session·Device·Tenant·Target Session·Policy에 결합하며, 대상 Session과 Refresh family를 하나의 DB transaction에서 철회한다.
- 다른 Tenant와 존재하지 않는 대상은 같은 `SESSION_TARGET_UNAVAILABLE` 404와 고정된 안전 Audit target으로 처리한다.
- access·refresh의 invalid·expired·revoked 거부를 알려진 계보 또는 `identity-public` anonymous 계보로 감사하며 Credential 원문·digest를 Audit에 기록하지 않는다.
- Device trust 성공과 binding 거부를 감사하고 Audit append 실패 시 보안 상태 쓰기를 rollback한다. trust에 Step-up은 추가하지 않았다.
- Refresh 회전과 Session 철회 경합은 동일 Service lock으로 직렬화되어 철회 후 모든 대상 Credential이 401로 수렴한다.

## 변경 범위

- Identity Core·공개 Export와 C01 보안 Test
- Identity Architecture·API README
- OpenAPI Session revoke Path·Request·Response와 Contract verifier
- C01 Identity Evidence 및 갱신된 OpenAPI Evidence
- C01 Work Order·Prompt·Progress·본 보고서

DB Migration, Audit Core, UI, HTTP Runtime, 의존성, Lockfile은 변경하지 않았다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| C01 보안 회귀 | 7/7 PASS |
| `verify:api-identity -- --write` / `--no-write` | 각각 PASS, 18 tests, 7 actions, source `C588F9DE...7CCE6` |
| `verify:api-audit -- --no-write` | PASS, 13/13 |
| `verify:openapi-contract -- --no-write` | PASS, 42 paths·65 operations·38 schemas, SHA `00DCEC99...6165` |
| Python compile·Public export | PASS |
| Workspace | 34/34 PASS |
| Independence | 8 components·10 edges·141 scanned files·0 violations |
| Toolchain | 7 npm manifests, exact pins, lockfiles PASS |
| Node syntax·OpenAPI JSON parse·Diff check | PASS |

장시간 공통 Quality Gate는 C01 지시대로 재실행하지 않았다. Policy에서 Python Identity lint·type·unit·build가 사용하는 관련 Capability인 `verify:api-identity`를 write/no-write로 직접 통과했다.

## 미해결·후속 경계

- 실제 HTTP Route, Web Cookie, PostgreSQL/RLS, 외부 IdP, Sync-key 폐기 실행은 후속 작업 소유이며 이번 완료 주장에 포함하지 않는다.
- Audit Core와 Identity SQLite 간 crash-atomic durable outbox는 기존 설계대로 M5 영속 계층에서 완성한다.
- PR·CI·Merge는 어울1 소유다.
