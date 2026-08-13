# ysna-server Deployment Evidence

- issue: `R1-M8-09-EGRESS-POLICY-C01-I001-YSNA-DEPLOY`
- approval: `APR-G9-R1-M8-09-EGRESS-20260813-01`
- executed: `2026-08-13T21:09:58+09:00`
- server root: `/home/ubuntu/deploy/daon-user`
- deployed commit: `adafcb363a07d17bd779bf246143c85f11532834`
- previous commit: `1b652ec0858021bb2c78e408cc50c32150a88450`

## Preflight와 backup

- 서버 Git은 `master`/previous commit이었고 dirty는 보호 untracked `backups/`, `secrets/`뿐이었다. 대상 commit tree와 두 보호 경로 충돌은 0이었다.
- disk는 193G 중 126G available이었다.
- 배포 전 Daon API/Web은 healthy, document-worker running, object-storage healthy였다. shared-db, nginx-proxy-manager, netdata도 running이었다.
- 대상 DB는 PostgreSQL 18.4, vector extension present, migration `0011`이었다. 기존 collation version mismatch warning은 변경하지 않았다.
- backup: `backups/daon-user-pre-0012-20260813T210121+0900.dump`, 630138 bytes, SHA-256 `9e9cebbe943361ef809d3ef24194de524c82f4663bdb7f330fd817f2ca641205`, restore-list entries 1133.
- previous image rollback tags: API `2e72f918...de6b5`, Web `a1bfb589...ee3f3`, worker `cffba8fe...449ea`.

## Build, migration, service switch

- server checkout은 exact commit detached HEAD로 전환했으며 보호 untracked는 유지됐다.
- Daon 전용 API/Web/document-worker image build PASS. Web compile, TypeScript, 9 pages, Product boundary 291 files/0 violations.
- 새 API image one-off와 read-only migrations mount로 preflight current `0011`을 확인하고 `0011→0012`를 적용했다.
- post-migration: revision `0012`, workspaces 5, Workspace current deny bindings 5, Organization current deny bindings 5, deny versions 10, forced RLS tables 2.
- Daon 전용 API/document-worker를 먼저 recreate하고 API healthy 후 Web만 recreate했다. object-storage/shared-db/proxy/netdata는 recreate/restart하지 않았다.
- deployed images: API `6b8754d7...a655f79` healthy, Web `3a84f357...60d3c3685` healthy, worker `55225fa6...60d3c4` running.

## Health, same-origin, Browser

- API `/health/ready` 200, public `https://daon-user.sinsan.kr/` 200, `/bff/shell/runtime` 200.
- public same-origin Egress unauthenticated request 401, Question POST 403, response internal-address token count 0. API/Web/worker recent traceback/exception/fatal/panic count는 모두 0이었다.
- Chrome의 기존 production Workspace tab을 reload해 current 5종 Studio UI와 외부 전송 추가 인증 UI를 확인했다. 기존 session은 `AUTHENTICATION_REQUIRED`로 만료되어 authenticated Source/Studio/Egress data journey는 실행하지 않았다.
- Organization settings route는 safe `AUTHENTICATION_REQUIRED`로 표시됐고 page 내부주소 token count는 0이었다. Credential/Cookie/token을 읽거나 기록하지 않았다.

## Postflight와 rollback readiness

- exact deployed HEAD는 `adafcb363a07d17bd779bf246143c85f11532834`, migration은 `0012`다.
- shared-db ID `c962c98c...bfb9d8`, nginx-proxy-manager ID `337026a0...8e1d0`, netdata ID `8cab33d7...e2c2b`는 running이며 배포 중 변경 명령 0이다. object-storage는 기존 image ID와 creation time을 유지하고 healthy다.
- migration one-off container remaining 0, 임시 public-response 파일 0. backup과 rollback image tags는 보존했다.
- server Git status는 보호 untracked `backups/`, `secrets/`만 남았다.

## 판정

`DEPLOYMENT_PASS / AUTHENTICATED_BROWSER_PARTIAL`. Exact commit, backup, build, migration 0012, backfill/RLS, Daon 전용 service switch, health와 public same-origin 기본 경계는 통과했다. 인증 세션 만료로 실제 사용자 데이터 기반 Studio/Egress Browser 여정만 미검증이며 배포 rollback 조건은 발생하지 않았다.
