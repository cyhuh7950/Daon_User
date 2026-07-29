# R1-M5-01 완료 보고서

## 판정

`COMPLETED`

## 판단 이유

PostgreSQL 18.4/pgvector Cloud 정본의 첫 Migration, 강제 RLS, 최소권한 Application Role, Transaction-local 접근 문맥, Notification·Audit·Idempotency 원자성, 안전 오류 분류, Migration-aware Readiness와 격리 복구 절차를 구현했다. 로컬 회귀와 ysna-server exact SHA 실제 DB/API 검증을 모두 통과했고 기존 공개 API·Browser same-origin 경계를 변경하지 않았다.

## 구현 결과

- Alembic `0001`에 Tenant/Workspace, Membership/Role, Session/Device, Authorization Policy/Binding, Audit, Idempotency, Notification/Inbox와 최소 Vector Entity를 정의했다.
- 모든 Tenant 정본에 Tenant key, Workspace 정본에 복합 Workspace key·FK를 부여하고 RLS를 `ENABLE`+`FORCE`했다.
- `PostgresCloudStore`가 bounded Pool, parameter binding, Transaction-local Context, 안전 오류와 명시적 retryability를 제공한다.
- 동일 Idempotency key는 advisory lock으로 단일 결과를 재생하며, 상이 key·동일 Version 경쟁은 한 요청만 성공한다.
- Production 설정은 Cloud DSN이 없으면 fail-close하고 `/health/ready`가 Migration·Extension 상태를 포함한다.
- 격리 Compose와 운영 절차는 PostgreSQL 18 공식 Volume layout과 전용 Project·Network·Volume만 사용한다.

## 테스트 결과

| 범위 | 결과 |
|---|---|
| TDD RED | Cloud module·readiness·production DSN 부재를 각각 재현 |
| Cloud 로컬 | 정적 4 PASS, 실DB 6 SKIP(로컬 Docker 없음) |
| API 전체 | 90개 중 80 PASS, PostgreSQL/POSIX 환경 전용 10 SKIP |
| 기존 전용 검증 | Audit 13, Identity 18, Authorization 22, Runtime Python 11/Node 10, Notification Python 10/Node 21 PASS |
| Build·정적 품질 | Web Build, Ruff, 신규 strict mypy, Toolchain, 독립성 PASS |
| 보안 | pip-audit 알려진 취약점 0, Secret/DSN literal hit 0 |
| ysna-server 실DB | 10/10 PASS, 복구 후 10/10 재통과 |
| ysna-server 실제 API | live/ready/session/authorization 200, 정상 종료·재시작 PASS |
| Migration·복구 | preflight·backup·upgrade·reapply·downgrade·restore·re-upgrade PASS |
| 격리 정리 | Checkout·Process·Listener·Container·Network·Volume·Tool file 0, 공용 자원 불변 |

## Commit·Push

- 단일 구현 Commit: `6d8d079e3b7c23c54f653a986f9d3dd03fa04607`
- Branch: `codex/r1-m5-01`
- Local·Origin·서버 검증 SHA 일치
- 서버 검증 중 발견한 교정은 단일 Commit을 amend하고 소유 Branch에만 `force-with-lease`했다. 이전 후보 SHA는 정본이 아니다.

## 미해결 사항

- Object Storage, Queue/Worker/Outbox 전달, Source/Run/Generation 정본, Retrieval/Index 전략, Local-private 저장소와 운영 배포는 후속 Work Order 범위다.
- 이번 단계의 실제 DB Transaction 검증은 Cloud Repository Port와 Readiness 경계다. 기존 M4 SQLite Reference를 전체 Domain별 Postgres Adapter로 교체하는 후속 구현은 승인 작업계획 순서를 따른다.
- 운영 Oracle Cloud 배포와 실제 운영 DB Migration은 수행하지 않았다.

## 조치

R1-M5-01을 합격으로 닫고 다음 승인 Work Order로 진행할 수 있다. 별도 제품 테스트 웨이브 도달은 아니다.
