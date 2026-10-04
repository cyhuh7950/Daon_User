# Daon_User 개발·정식 WSL 검증 환경

기준: 2026-10-04 신산님의 `3330 정식 검증 승인`. 이 문서는 Router Auto PR #39의 WSL 검증 대상과 복구 경계를 기록한다. Secret 값은 기록하지 않는다.

## 정식 WSL 대상

- 서버: SSH alias `WSL-server` (호스트 `SINSAN`). 소스는 Git 원격 `cyhuh7950/Daon_User`의 `codex/next-user-development` exact commit을 사용한다. 이 Stage의 정식 배포 소스 checkout은 `/home/daon/deploy/daon-user-router-auto-24343ac1`이다. 다른 dirty checkout `/home/daon/deploy/daon-user`는 사용하거나 덮어쓰지 않는다.
- Compose 프로젝트 `daon_user`, Web 포트 `3330`, API 내부 포트 `8000`. 정식 컨테이너 `daon_user-web-1`, `daon_user-api-1`, `daon_user-document-worker-1`, `daon_user-studio-worker-1`, `daon_user-object-storage-1`.
- 네트워크 `daon_user_network`, `proxy-network`, API의 `postgres_env_default`; 볼륨 `daon_user_api_runtime`, `daon_user_object_storage`. DB는 `local-postgres`의 `daon_user`. API/Web은 `deploy/daon-user/compose.yaml`과 `compose.wsl-postgres.yaml`을 사용한다. 실제 실행 이미지 ID, checkout SHA, DB revision은 검증 시마다 기록한다.
- 기존 설정은 `/home/daon/deploy/daon-user/.env`를 env-file로 참조한다. 추가 Secret file 참조는 실행 중 컨테이너의 read-only bind 경로와 일치시켜야 하며, 원문을 출력하거나 Git에 넣지 않는다. Web의 BFF는 같은 Compose 네트워크의 `http://api:8000`을 사용하고 브라우저는 same-origin만 사용한다.
- Router Auto Stage에서는 API/Web만 교체한다. 두 worker와 object-storage는 기존 실행 이미지·볼륨을 유지한다. 다른 Compose 프로젝트, 3331의 임시 격리 자원, 기존 dirty checkout은 정식 대상으로 간주하지 않는다.

## DB migration·복구

- 출발 DB revision `0051`, 목표 `0052`. 적용 직전 원본 `daon_user`의 새 custom-format dump를 `/home/daon/deploy/daon-user-qa-backups/2026-10-04-router-auto-24343ac1/`에 mode 600으로 보존하고 SHA-256/행 수를 기록한다. 해당 디렉터리는 mode 700이다. 기존 격리 QA dump와 새 정식 백업을 혼동하지 않는다.
- `0052`는 nullable `auto_model_id`와 기본값이 있는 `catalog_origin`을 추가하고 고정 라우터만 backfill한다. downgrade는 차단된다. 장애 시 먼저 이전 API/Web 이미지로 복귀하고 DB는 additive `0052`를 유지한다. DB dump 복원은 이후 쓰기 손실 가능성을 별도 확인·승인한 경우에만 수행한다.
- API/Web 이미지의 이전 ID와 새 digest, Compose config, Secret mount 경로, network/volume을 배포 전후 대조한다. 새 소스를 Windows에서 복사하거나 서버에서 수정하지 않는다.

## 정식 검증 entity와 완료 조건

- 전용 QA 관리자 계정은 `qa-router-auto-admin` 명명 규칙을 사용한다. 현재 DB에는 `qa%` 로그인 계정이 없으므로 아직 seed되지 않았다. 생성·역할·인증은 정식 Identity 흐름과 DB 역할/감사 기록으로 확인하며, 실제 사용자 계정의 비밀번호·세션·권한은 변경하지 않는다. 반복 실행은 기존 QA 계정을 재사용하고 실패 시 불변으로 둔다. QA Secret은 환경변수 또는 Secret 파일만 사용한다.
- 최소 검증: exact SHA 및 이미지 digest, migration/기존 연결·모델·허용·기본값 보존, `/health/ready`, 3330 HTTP와 same-origin BFF, QA 관리자 로그인 후 모델 조회·Auto 허용/저장·선택, 실제 Adapter의 모델 ID. 외부 유료 Provider 호출은 별도 비용 승인 전 하지 않는다.
- QA 계정/세션이나 정식 브라우저 검증이 준비되지 않으면 health/build만 통과해도 정식 E2E PASS로 표시하지 않는다. Oracle staging 대상·Secret 경로는 이 문서의 WSL 승인에 포함되지 않으며 별도 확인·승인이 필요하다.
