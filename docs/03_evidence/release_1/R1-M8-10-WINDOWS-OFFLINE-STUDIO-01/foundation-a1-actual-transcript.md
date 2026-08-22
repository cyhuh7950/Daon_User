# Foundation A1 actual Gate transcript

- 최종 재실행 시각: 2026-08-14T22:39:27+09:00 이전 완료
- 실행 소스: 현재 Working Tree의 `0015_security_audit_step_up_idempotency.py`, `audit.py`, `foundation-a1-postgres-gate.py/.sh`
- 자격정보: production secret 출력·파일 저장·CLI 인자 사용 0. PostgreSQL password는 실행 프로세스 환경에서만 사용했다.

## PostgreSQL 15.18

실행: WSL `local-postgres`의 고유 DB `daon_a1_security_it_20260814`. 공용 DB·서비스·설정은 변경하지 않았다.

```text
0001 -> 0015 PASS
0015 -> 0014 (empty) PASS
0014 -> 0015 PASS
{"app_update":"denied","cross_tenant_read":0,"cross_tenant_write":0,"immutable_trigger":"denied_55000","integrity":"AUDIT_CHAIN_VALID","postgres_security_audit_restart":"pass","tenant_a_events":2,"tenant_b_events":1}
DOWNGRADE_BLOCKED_55000_PASS
CURRENT_0015_PASS
CLEANUP_REMAINING_0
```

## PostgreSQL 18

실행: 고유 ephemeral `daon-a1-pg18-it` 컨테이너와 DB `daon_a1_security_pg18_it_20260814`. Volume은 생성하지 않았다.

```text
0001 -> 0015 PASS
0015 -> 0014 (empty) PASS
0014 -> 0015 PASS
{"app_update":"denied","cross_tenant_read":0,"cross_tenant_write":0,"immutable_trigger":"denied_55000","integrity":"AUDIT_CHAIN_VALID","postgres_security_audit_restart":"pass","tenant_a_events":2,"tenant_b_events":1}
DOWNGRADE_BLOCKED_55000_PASS
CURRENT_0015_PASS
CLEANUP_REMAINING_0
PG18_CONTAINER_CLEANUP_0
```

## 판정

- Session/ACL/Step-up 공통 Security Audit의 PostgreSQL append-only chain, restart persistence, Tenant RLS와 immutable contract: PASS.
- Step-up raw grant/ciphertext database 저장: 0.
- Step-up issuance/consumption idempotency의 권위 원장은 Identity 저장소의 schema-versioned `step_up_idempotency`·`step_up_consumptions`이며 발급·소비 상태와 원자적으로 갱신된다. PostgreSQL의 미사용 이중 원장: 0.
- 데이터가 존재하는 0015 downgrade: SQLSTATE 55000 fail-close. Transaction rollback 후 revision 0015 유지: PASS.
- disposable DB/Container 잔류: 0.

## Windows guarded Rust contract

실행: `node scripts/run-isolated-desktop-cargo.mjs test`를 sandbox 밖의 고유 격리 Target에서 실행했다. Production Credential target은 사용하지 않았고, 테스트마다 고유 Credential을 생성한 뒤 Drop guard와 정상 종료 경로에서 revoke했다.

```text
Exit code: 0
Wall time: 155.3 seconds
lib 30/30 PASS
local_service_contract 5/5 PASS
native_session_contract 22/22 PASS
offline_studio_bridge_contract 3/3 PASS
offline_sync_bridge_contract 7/7 PASS
recovery_bridge_contract 44/44 PASS
workspace_bridge_contract 2/2 PASS
current gen absent
current isolated target absent
cargo/rustc/local-service-lifecycle-host process 0
```

Temp에 본 실행 이전부터 존재한 `daon-user-desktop-test-*` 3개는 사용자 보호 자산으로 간주해 삭제하지 않았다. 이번 실행의 고유 Target `daon-user-desktop-test-UwL1ir`은 wrapper가 제거했다.
