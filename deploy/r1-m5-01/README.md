# R1-M5-01 격리 PostgreSQL 검증

이 Compose는 `ysna-server`의 `/home/ubuntu/deploy/daon-user/R1-M5-01/<exact-sha>`에서만 사용한다. `shared-db`, `common`, `netdata`, `proxy`와 기존 Network·Volume을 참조하지 않는다.

고정 기준은 PostgreSQL `18.4`, pgvector `0.8.2`, Alembic `1.18.5`, Psycopg `3.3.4`다. Image tag를 사용하기 전에 Digest를 증거에 기록하고 `cloud_admin preflight`가 실제 서버 버전과 대상 DB 정체성을 검증한다. Secret과 연결 문자열은 `.env`나 저장소에 기록하지 않고 검증 Process 환경에만 주입한다.

자동 검증 순서는 다음과 같다.

1. 기존 Docker 자원 이름 Snapshot과 exact SHA detached clean 확인
2. 전용 Compose Project·Network·Volume 시작
3. 관리자 연결로 `cloud_admin preflight`
4. 논리 Backup 후 Alembic `upgrade head`, 재적용, `bootstrap-role`
5. application role 연결로 `test_cloud_storage.py` 실제 통합 테스트
6. Alembic `downgrade base` 후 격리 DB Restore, `upgrade head` 재검증
7. Checkout clean, 전용 자원 제거, 기존 자원 Snapshot 불변 확인

사용자·운영자는 Python/DB CLI를 실행하지 않는다. 제품 운영에서는 API `/health/live`와 `/health/ready`가 Process 생존과 Migration·Extension 준비 상태를 분리해 제공한다.
