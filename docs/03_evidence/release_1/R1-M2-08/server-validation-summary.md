# R1-M2-08 ysna-server 검증 요약

- 판정: `PASS`
- 검증 Commit: `a408cb903a4e756db11d966e055af9d44dc1189a`
- 격리 Checkout: `/home/ubuntu/deploy/daon-user/R1-M2-08/a408cb903a4e756db11d966e055af9d44dc1189a`
- 환경: ARM64 Ubuntu, 일회성 Container, Node 24.18.0, npm 11.12.1, uv 0.11.2
- 설치·의존성: `npm ci`, `npm ls --all`, Critical Audit PASS
- 검증: 전체 186/186, Lint 11 files, Web Build 7 routes, Quality Gate 7개 범주 PASS
- DB: Schema/Migration 파일 0건으로 R1-M2-08에서는 `NOT_APPLICABLE`
- 격리성: 검증 전후 기존 Container·Network·Volume 목록 Hash가 각각 동일
- 정리: 일회성 Container, `node_modules`, `.next`, 임시 uv, Gate 생성 파일을 제거·복원했고 Checkout은 변경 0건

첫 통합 시도에서 Node 전용 Container에 uv가 없어 Quality Gate의 Build 범주가 1건 실패했다. 이미 서버에 고정되어 있던 `ghcr.io/astral-sh/uv:0.11.2` 이미지의 `/uv` Binary를 임시 추출해 read-only로 제공한 뒤 동일 Commit의 Gate가 통과했다. 임시 Binary와 디렉터리는 검증 후 제거했다.

초기 원격 명령의 잘못된 PowerShell 변수 확장으로 `/home/ubuntu/` 아래에 이름 Byte가 `20 5c`인 빈 디렉터리가 생겼다. 경로 Byte와 empty 상태를 확인한 뒤 그 디렉터리만 `rmdir`로 제거하고 부재를 확인했다.
