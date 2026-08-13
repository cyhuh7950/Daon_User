# R1-M8-09-STUDIO-DEFAULT-POLICY-C02 ysna 배포 Gate 증거

## 판정

`DEPLOYMENT_BLOCKED_ROLLED_BACK` — 제품 서비스 전환 전 Migration 0013이 승인된 fail-close 조건으로 중단되어 사전 상태를 복구했다.

## Preflight·backup·rollback 자산

- 서버 root: `/home/ubuntu/deploy/daon-user`
- 배포 전 HEAD: `9845890c95d002ce0cb13b4e346cf2bcdc174d5a`
- 대상: `6bfd10b1f9cb8e3db6e5ad5e4c74caedf62f820f`, parent `d42c1b28e32ad46ce92601ac5e0a9bc0d7fb454c`
- PostgreSQL: 18.4, current migration `0012`
- 보호 dirty: 기존 `backups/`, `secrets/`만 존재했고 변경·삭제하지 않았다.
- disk: 193G 중 109G available, 44% used.
- backup: `backups/daon-user-pre-0013-20260814T003546+0900.dump`
- backup size: 651266 bytes
- backup SHA-256: `20a0a2d47412a9914da6cb39b1252106da5fc22058f03f8f2c5b4f35335c0f38`
- restore-list: 1180 lines
- API rollback tag: `daon_user-api:rollback-pre-0013-20260814T0036`
- rollback image: `sha256:6b8754d7a843aaee33b1a8fa4c62a77f8c92654419639871775880142a655f79`

첫 app-role backup은 FORCE RLS 때문에 중단됐다. 생성된 불완전 파일 하나만 exact 삭제하고 shared-db 내부 superuser 환경으로 다시 생성했으며 비밀값은 출력·파일화하지 않았다. PostgreSQL은 기존 collation version mismatch warning을 출력했지만 custom-format backup·restore-list 검증은 성공했다.

## Build·Migration 결과

- exact target detached checkout과 API image build 성공.
- built API image: `sha256:e90d09de08bbbb69967ccfed4a0f7141b1145c2eea3dc8202a85af98ad556e45`
- Web source 변경이 없어 Web build/recreate를 수행하지 않았다.
- 첫 one-off는 Alembic 변수명 alias 누락으로 DB 접속 전 중단됐다.
- app DSN alias 재실행은 schema CREATE 권한 부족으로 transaction 전환 없이 중단됐다.
- shared-db migration role을 stdin으로만 전달한 실제 migration은 `STUDIO_DEFAULT_POLICY_LATEST_INVALID` SQLSTATE 55000에서 fail-close했다.
- migration transaction은 rollback되어 revision `0012`가 보존됐고 API/Web/worker recreate는 0이다.

Read-only 해시 진단 결과 최신 KnowledgeScope v1이 단일 행인 Workspace 한 곳에서 canonical payload의 `workspace_id`와 `scope`가 모두 누락됐다.

```text
workspace_hash=9bf2a6732cdeba0bea3f0ebba9ed07f8
latest_version=1
latest_count=1
has_workspace_id=false
has_scope=false
```

원본 Tenant·Workspace·record ID와 canonical 내용, DSN·cookie·credential은 기록하지 않았다. 이 상태를 성공시키려면 운영 Canon 데이터 교정 또는 승인 migration 계약 변경이 필요하므로 현재 배포 승인 범위를 벗어난다.

## Rollback·불변성

- server checkout: `9845890c95d002ce0cb13b4e346cf2bcdc174d5a`
- API latest image: rollback image로 복구
- DB revision: `0012`
- running API ID: `a19a878f3663ce80aacca695407f6d2a54e9c340d4cc21e8e2bd0236853fbd4b`
- running Web ID: `9053452c7307f4520a57027a0ed4257c709d2e117018438f3d1448e0b3c4466d`
- running worker ID: `b7902ce2dc1e81bf7d0628d8c86d5307ebc3ababdb7838477c30176514a19666`
- object-storage/shared-db/proxy/netdata IDs는 preflight와 동일.
- API/Web health: healthy/healthy
- public root: 200
- public same-origin `/bff/api/session`: 401 정상 unauthenticated
- 최근 10분 API safe error 검색: 0
- migration one-off remaining: 0
- 서버 dirty는 보호 `backups/`, `secrets/`만 유지.

서비스가 target commit으로 전환되지 않았으므로 production Browser Studio Gate는 실행하지 않았다. 기존 운영 세션·탭에는 영향을 주지 않았다.

## 승인 후 local remediation 증거

- 2026-08-14 승인된 §4.1 계약으로 exact Question legacy KnowledgeScope만 동일 aggregate v2 append하도록 Migration 0013을 보완했다.
- 고유 disposable PostgreSQL에서 valid v1→v2, idempotency, v2 WeightProfile FK, 8개 malformed/missing/cross-workspace negative, compat ID collision, non-owned v2 reference downgrade 차단, v1 보존 downgrade와 deterministic reapply가 PASS했다.
- focused `16 passed, 1 skipped`, 전체 API `360 passed, 26 skipped, 134 subtests`, Node 61, OpenAPI 75/94/120/31, Web build·TypeScript와 Product Boundary가 PASS했다.
- disposable DB는 exact drop되어 remaining 0이고 공용 `local-postgres`는 running이다.
- 이 절은 local 재배포 준비 증거이며 production 재배포 성공을 뜻하지 않는다. 운영 HEAD/revision/service는 위 rollback 상태 그대로다.

## f8ed4e4 재배포 Gate

판정: `DEPLOYED_BROWSER_AUTH_PENDING`

- target: `f8ed4e4acf360c3d21848df2c1b8bbb6ec5b8a26`; `ca00719`, `6bfd10b` ancestry와 detached checkout 확인.
- 기존 backup SHA-256 `20a0a2d47412a9914da6cb39b1252106da5fc22058f03f8f2c5b4f35335c0f38`, restore-list 1180, rollback image `sha256:6b8754d7a843aaee33b1a8fa4c62a77f8c92654419639871775880142a655f79` 재검증.
- Migration은 비밀을 stdin으로만 전달한 one-off에서 `0012→0013 (head)` 성공. 성공 전 service recreate는 0.
- 운영 Canon: Workspace 5, latest valid WorkspacePolicy 5, exact legacy v1 1, compat v2 1, compat v2를 참조하는 WeightProfile 1, RuleSet Reference/Snapshot/Binding 각 5. RLS app role own 1/cross 0.
- API image `sha256:49d09e80221db2e445582a67d58622ff1f33ce221af605565113504ab8ae156f`, worker image `sha256:e664bd7b1e5dfdaa665769ad102cc56f19e7452857e14d932d8da8620776fc95`. API와 document-worker만 recreate했고 Web는 기존 ID `9053452c7307f4520a57027a0ed4257c709d2e117018438f3d1448e0b3c4466d` 유지.
- API healthy, worker running, public root 200, same-origin BFF session unauthenticated 401, 최근 API/worker safe error 0, one-off remaining 0.
- target Workspace Source는 5건(`failed=3`, `needs_review=1`, `ready=1`), 저장 Studio Output은 0건.
- object storage `e5c606a...`, shared-db `c962c98...`, proxy `337026a...`, netdata `8cab33d...` ID와 running/health는 preflight 대비 불변.
- Chrome의 새 production 탭과 기존 Workspace 탭 모두 session expired. 실제 DOM은 `AUTHENTICATION_REQUIRED`; 자격 추측·입력 없이 로그인 화면을 handoff했다. 따라서 Source 5·Studio 오류 0·잠금 6의 authenticated Browser 확인만 미실행이며 서버/DB/API 배포 성공과 분리한다.
- 서버 보호 dirty는 `backups/`, `secrets/`만 유지했고 비밀·cookie·내부 URL을 Evidence에 기록하지 않았다.

## Task 4 local Repository remediation

- 운영 API image의 product `_policy_projection`을 app DSN·RLS context로 재현했을 때 `jsonb_build_object`의 `workspace_id` parameter `$9`에서 SQLSTATE `42P18 IndeterminateDatatype`가 발생했다. 정책별 subquery, 목록 query, literal projection, GRANT와 RLS는 각각 성공해 원인을 해당 untyped bind 한 곳으로 분리했다.
- 승인된 계획 예외대로 그 bind만 `%s::text`로 변경했다. unit RED `1 failed, 10 passed, 1 skipped`, focused GREEN `17 passed, 1 skipped`, 전체 API `361 passed, 26 skipped, 134 subtests passed`.
- PostgreSQL 15.18의 고유 disposable DB `daon_c02_task4_20260814021242`에서 fresh 0001→0013과 기존 fail-close·downgrade·reapply 회귀를 수행했다. `daon_app` 역할과 tenant/workspace RLS context의 actual product `list_outputs` Repository는 SQLSTATE 0, outputs 0, locks 6, cross-tenant 0이었다.
- DB는 exact drop되어 remaining 0이고 공용 `local-postgres`는 running이다. 이 절은 local fix 증거이며 API-only 재배포나 authenticated production Browser 성공을 뜻하지 않는다.
