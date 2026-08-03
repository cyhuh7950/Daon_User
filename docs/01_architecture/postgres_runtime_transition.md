# PostgreSQL Runtime 전환 계약

## 목적

로컬 WSL PostgreSQL과 ysna-server 공용 `shared-db`를 동일한 운영 데이터 경계로 사용한다. SQLite는 테스트 전용 Adapter로만 남긴다.

## 전환 범위

- Identity: OIDC 상태·User·Tenant·Device·Session·Step-up
- Authorization: Workspace·Membership·Policy·AccessDecision·Historical Result
- Runtime: Production은 `DAON_CLOUD_DATABASE_DSN`과 PostgreSQL Repository를 사용
- Migration: 기존 `0001~0006` 정본과 충돌하지 않는 추가 Migration으로 수행

## 환경 경계

| 환경 | DB | 네트워크 | 데이터 보존 |
| --- | --- | --- | --- |
| 로컬/WSL | 전용 PostgreSQL | WSL 전용 네트워크 | 테스트용 Volume |
| ysna-server | 공용 `shared-db`의 Daon 전용 Schema/Role | 외부 `proxy-network` | 기존 공용 DB·Volume 보존 |

## 금지

- 운영 API를 SQLite와 PostgreSQL에 분산 저장하지 않는다.
- 공용 `shared-db`의 기존 Schema·Role·Volume을 임의 변경하지 않는다.
- Migration 전 Backup·사전점검·Rollback 계획 없이 서버 적용하지 않는다.
- Web Browser가 DB/API 내부 주소를 직접 호출하지 않는다.

## 완료 조건

- Identity·Authorization PostgreSQL Adapter가 기존 계약 테스트를 통과
- WSL 전용 PostgreSQL에서 Migration·회귀 통과
- ysna-server `shared-db`에서 전용 Schema/Role로 통합 테스트 통과
- Web `3330` + `proxy-network` same-origin E2E 통과
