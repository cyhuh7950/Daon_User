# R1-M5-01 ysna-server 검증 요약

## 판정

`PASS` — exact SHA `6d8d079e3b7c23c54f653a986f9d3dd03fa04607`을 `/home/ubuntu/deploy/daon-user/R1-M5-01/<sha>`에 detached clean으로 배치하고 ARM64 격리 PostgreSQL 검증을 완료했다.

## 결과

- Host: `aarch64`, Docker `29.6.1 linux/arm64`
- Database: PostgreSQL `18.4 (Debian 18.4-1.pgdg13+1)`
- pgvector: `0.8.2`; Image digest `sha256:b7337db8fe39d12fe8ecb0003c72680f24479813a744b43154eee6f2eab5a5f3`
- Runner: Python `3.14.3`, uv `0.11.2`, Psycopg `3.3.4`, Alembic `1.18.5`
- Preflight: 대상 DB 정체성·서버 버전 PASS, Secret/DSN 출력 0
- Migration: `upgrade 0001` PASS, 동일 `upgrade head` 재적용 PASS
- 실DB Test: 10/10 PASS; RLS 교차 Tenant, Pool 문맥 제거, Vector 저장/읽기, Audit·Idempotency 원자성, Audit 실패 Rollback, 동일 key 동시성 단일 결과, 상이 key 단일 승자 포함
- 실제 API Process: Cloud DSN 연결 상태에서 `/health/live` 200, `/health/ready` 200, Session·Authorization 200, 정상 종료·동일 Port 재시작 PASS, Process/Listener 잔여 0
- 복구: 사전 논리 Backup 생성, `downgrade base`, 격리 DB 재생성·Restore, `upgrade head`, 실DB Test 10/10 재통과
- 권한: Application Role은 Superuser·CreateDB·CreateRole·Inherit·BYPASSRLS가 모두 false, Audit UPDATE/DELETE 권한 false
- 격리: `shared-db`, `common`, `netdata`, `proxy` 미사용·미변경. 전용 Checkout·Container·Network·Volume·Tool file 잔여 0, 기존 Docker 자원 이름 Snapshot 불변

서버 실행 중 앞선 중단은 PG18 Volume layout, Runner tag/version, distroless Image, source path와 Test Fixture 격리 문제를 순차 분리한 환경·검증 문제였다. 각 시도의 전용 Docker 자원은 Trap으로 제거됐으며 정식 `FAILURE_REPORT`에 해당하지 않는다.
