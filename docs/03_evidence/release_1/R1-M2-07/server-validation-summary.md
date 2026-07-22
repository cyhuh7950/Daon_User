# R1-M2-07 GitHub·ysna-server 검증 요약

## 판정

`PASS` — 구현 Commit `6fdcfa20c80f0d512e2b4d299446b8b6e917bd11`이 GitHub 필수 검사와 ysna-server ARM64 격리 검증을 모두 통과했다.

## GitHub

- Draft PR: `#13`
- Required Check: `Release 1 Quality Gate`
- Run/Job: `29909966026` / `88890280290`
- 결과: PASS

## ysna-server

- 승인 Root: `/home/ubuntu/deploy/daon-user`
- 격리 Checkout: `/home/ubuntu/deploy/daon-user/R1-M2-07/6fdcfa20c80f0d512e2b4d299446b8b6e917bd11`
- 상태: exact detached SHA, 검증 후 clean
- Architecture: Host `aarch64`, Docker `arm64`
- Toolchain: Node 24.18.0, npm 11.12.1, Corepack 0.35.0, uv 0.11.2 ARM64
- 검증: npm ci 260 packages, npm ls·Audit PASS, 전체 순차 167/167, Lint 11 files, Production Build 7 Route, 공통 Gate 7범주·Failures 0·Exit 0
- DB: Schema·Migration 파일 0건, `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 0건

## 격리·정리

- 기존 Container·Network·Volume 이름 Hash는 사전·사후 일치했다.
- 임시 Container는 모두 제거됐고 Listen Port를 만들지 않았다.
- 검증 생성물 `node_modules`, `apps/web/.next`만 제거했으며 소스 제거는 0건이다.
- 공통 Gate가 갱신한 R1-M1-05 결과 2개는 Git 정본으로 복원했다.
- 첫 Container는 Git SHA 확인식의 원격 Shell 인용 오류로 의존성 설치 전에 중단됐다. `--rm`으로 자동 제거됐고 Checkout·Docker 자원 변화 없이 같은 SHA에서 인용만 고쳐 재실행했다.

R1-D022에 따라 이 결과는 개발·통합 검증이며 운영 Release 승인이 아니다.
