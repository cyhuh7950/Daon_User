# Actual PostgreSQL Gate Transcript

- 기록 시각: `2026-08-13T11:40:20+09:00`
- 환경: WSL2 Ubuntu, container `local-postgres`, PostgreSQL `15.18 (Debian 15.18-1.pgdg12+1)`, image `pgvector/pgvector:0.8.2-pg15`
- 비밀 보호: DSN user/password는 container environment에서 process memory로만 조합했고 출력·파일 기록하지 않았다. 아래 명령 표기는 `<MASKED_DSN>`을 사용한다.
- 공용 경계: container/service/network/volume restart·변경 0. 전용 disposable DB만 생성·삭제했다.

| 순서 | 안전 명령 요약 | 결과 |
| --- | --- | --- |
| 1 | `wsl -d Ubuntu -- /bin/true` | exit 0 |
| 2 | `docker ps --format names`, `docker inspect local-postgres` | running=`true`, PostgreSQL 15 image 확인 |
| 3 | `createdb daon_r1_m8_09_egress_it_20260813_110348` | exit 0 |
| 4 | `DAON_DB_MIGRATION_DSN=<MASKED_DSN> alembic upgrade head` | `0001→0012`, exit 0 |
| 5 | `alembic downgrade 0011`, fixture tenant 1/workspace 2 insert, `alembic upgrade 0012` | rollback/reapply 모두 exit 0 |
| 6 | `actual-postgres-gate.sql` | `ACTUAL_POSTGRES_SCHEMA_GATE_PASS` |
| 7 | backfill assertions | policy versions=3, current bindings=3, deterministic IDs=3 |
| 8 | canonical assertions | canonical text/json/digest/deny mode 일치=3 |
| 9 | negative constraints | immutable `55000`, bad digest `22023`, scope mismatch `23514`, second current unique rejection PASS |
| 10 | RLS | 두 table FORCE RLS, tenant/workspace visibility PASS, cross-tenant rows=0 |
| 11 | `actual-postgres-runtime-gate.py` | Organization deny precedence/write0, ETag, denial Audit+1 PASS |
| 12 | two PostgreSQL connections, same Question advisory key | second transaction blocked until first commit; concurrency PASS |
| 13 | final `0012→0011`, table count, `0011→0012` | rollback table count=0, reapply exit 0 |
| 14 | `dropdb daon_r1_m8_09_egress_it_20260813_110348` | exit 0 |
| 15 | prior interrupted same-issue DB `..._1035` active connection check/drop | active=0, exact drop exit 0 |
| 16 | `createdb daon_r1_m8_09_fk_it_20260813_1141`, `upgrade head` | exit 0 |
| 17 | `actual-postgres-fk-gate.sql` | nonexistent Workspace FK insert rejected, SQLSTATE `23503`, invalid rows=0 |
| 18 | FK DB exact drop; prefix remaining query; public state | remaining=0, `local-postgres` running=`true` |

## 결론

Migration apply/rollback/reapply, backfill, canonical digest, immutable, explicit FK, scope/current uniqueness, RLS, Organization deny, ETag/Audit와 Question advisory-lock concurrency가 actual PostgreSQL에서 통과했다.
