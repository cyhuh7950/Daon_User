# Cloud 데이터 저장소 기반선

## 결정

Release 1 Cloud 정본은 PostgreSQL 18.4와 pgvector 0.8.2를 사용한다. Migration은 Alembic 1.18.5, Application Driver와 Pool은 Psycopg 3.3.4로 고정한다. 테스트·개발용 M4 SQLite Reference는 회귀 검증에 남지만 Production profile은 Cloud DSN 없이는 시작 설정을 승인하지 않으며 Postgres Adapter는 메모리 대체 성공 경로를 갖지 않는다.

## 경계

- Service는 현재 AccessDecision을 통과한 뒤 `CloudAccessContext`를 만든다.
- Repository는 매 Transaction에 서버 검증된 `tenant_id`, `workspace_id`, `actor_id`, `capability`를 `set_config(..., true)`로 주입한다.
- Connection Pool 반환 전 Commit/Rollback이 Transaction-local 문맥을 제거한다.
- PostgreSQL RLS `ENABLE`+`FORCE`는 두 번째 방어선이며 Application Role은 Owner·Superuser·`BYPASSRLS`가 아니다.
- Tenant/Workspace 복합 FK가 교차 Tenant 연결을 차단한다.
- Notification 읽음, Audit append, Idempotency 결과는 한 Transaction이다. 동일 key는 Transaction advisory lock으로 단일 결과를 재생하고, 서로 다른 key의 동일 Version 경쟁은 한 요청만 성공한다.
- Idempotency key의 유효 범위는 Tenant·Workspace·Actor·Operation 조합이다. 같은 Tenant·Actor라도 Workspace가 다르면 서로 독립된 요청으로 처리한다.
- Audit UPDATE/DELETE 권한을 회수하고 DB Trigger로도 변경을 차단한다.
- Vector는 이번 단계에서 3차원 최소 저장·읽기만 검증하며 Retrieval/Index 전략은 후속 Work Order 소유다.

## 운영

`/health/live`는 Process 생존만, `/health/ready`는 Runtime 상태와 Cloud Migration·pgvector 준비 상태를 함께 판정한다. Cloud Pool은 Process 기동 시 DB 연결을 강제하지 않고 첫 Transaction 또는 준비 확인 시 제한 시간 안에서 연결한다. 준비 확인은 Event Loop 밖에서 실행하므로 DB 장애 중에도 Liveness를 차단하지 않으며, 같은 Process가 DB 복구 후 Ready로 전환한다. DB 내부 주소·계정·SQL·연결 문자열은 응답에 포함하지 않는다. Migration은 대상 DB 확인, 논리 Backup, Upgrade·재적용, 검증, Downgrade·격리 Restore 순서로 자동화하며 사용자·운영자는 DB/Python CLI를 직접 실행하지 않는다.
