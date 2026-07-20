# ysna-server 개발·통합 환경 변경 승인 기록

## 승인 정보

| 항목 | 값 |
| --- | --- |
| 승인 ID | `APR-DEVENV-YSNA-20260720-01` |
| 승인자 | 신산님 |
| 승인일 | 2026-07-20 |
| 변경 ID | `CHG-R1-DEVENV-001` |
| 결정 ID | `R1-D021` |
| 적용 시작 | R1-M1-05 이후 |
| 상태 | 승인 |

## 승인 범위

- 개발 흐름을 `로컬 수정·기본 검증 → Git Push → ysna-server 소스 배포 → 전용 DB Migration → 서버 테스트 → PR Merge`로 변경한다.
- 접속은 `ssh ysna-server`, 배포 대상은 `/home/ubuntu/deploy/daon-user` 아래로 제한한다.
- Branch/Release별 전용 Compose Project·Network·Volume과 PostgreSQL `18.4` 개발 DB를 사용한다.
- 기존 `shared-db`와 `/home/ubuntu/deploy/common`, `netdata`, `proxy`는 사용하거나 변경하지 않는다.
- Migration 사전점검·Backup·Apply·Rollback, 배포 Commit SHA, Service Health와 서버 테스트를 Merge 전 증거로 남긴다.
- ysna-server의 ARM64 Architecture에 맞는 ARM64 또는 Multi-arch Image·Native Dependency만 사용한다.
- WSL은 필수 Gate에서 제외하고 필요 시 동일 격리 계약을 적용하는 대체 환경으로만 사용한다.

## 유지되는 승인 경계

- 이 승인은 개발·통합 환경에 한정하며 Oracle Cloud 운영 배포를 승인하지 않는다.
- OCI 운영 배포, 운영 데이터 Migration/Restore, 파괴적 복구 훈련은 기존 `G9-DEPLOY`·`G9-DRILL` 사전 승인을 유지한다.
- 제품 기능 범위, 공개 API, 데이터 계약, 보안 경계와 same-origin BFF 원칙은 변경하지 않는다.
