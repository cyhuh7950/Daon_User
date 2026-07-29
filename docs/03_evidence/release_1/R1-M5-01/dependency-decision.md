# R1-M5-01 의존성 결정

| 구성 | 고정 버전 | 선택 근거 | 대안과 제외 이유 |
|---|---:|---|---|
| Psycopg | 3.3.4 | 공식 지원 범위가 Python 3.10~3.14, PostgreSQL 10~18이며 binary·pool extra를 제공한다. LGPL-3.0-only. | psycopg2는 신규 개발용 현행 세대가 아니고 명시적 Pool 계약이 약해 제외했다. asyncpg는 SQLAlchemy/Alembic 운영 경로와 별도 Driver 계층이 필요해 이번 최소 범위에서 제외했다. |
| Alembic | 1.18.5 | 2026-06-25 공개 버전이며 Python 3.10+를 지원하고 Transactional DDL·재적용·Downgrade를 표준화한다. MIT. | 독자 SQL Runner는 Revision/재적용/복구 이력을 다시 구현해야 하므로 제외했다. |
| SQLAlchemy | 2.0.51 | Alembic의 잠금된 전이 의존성으로만 사용한다. MIT. | Application Repository는 Psycopg parameter binding을 직접 사용해 불필요한 ORM 의미 변환을 두지 않는다. |
| pgvector | 0.8.2 | 공식 PG18 Image·Source가 제공되고 최소 vector type 저장/읽기 계약에 충분하다. PostgreSQL License. | 별도 Vector DB는 Release 1 데이터 경계와 운영 범위를 확대하므로 제외했다. |

`uv.lock`은 Python 3.14.3 기준으로 재생성했다. Secret·DSN은 문서·로그·Lockfile에 포함하지 않는다. 취약점과 ARM64 설치 가능성은 로컬 정적 검사와 ysna-server 실제 설치 증거를 분리해 기록한다.
