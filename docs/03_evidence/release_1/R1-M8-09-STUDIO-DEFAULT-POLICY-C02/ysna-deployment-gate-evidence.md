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
