# ysna-server deployment transcript — c4d626c

- 실행 시각: 2026-08-15T08:28:49+09:00 ~ 2026-08-15T09:04:00+09:00
- 대상 Commit: `c4d626c020b8ff5ec42c9ab22f359c25b6dedf18`
- 대상 Branch: `origin/codex/user-auth-screen-split`
- 배포 Root: `/home/ubuntu/deploy/daon-user`

## 사전점검과 복구 지점

- 이전 HEAD: `dbe67f9bfe778b1ffa10b31f1e3e0faf807dd42b`
- 이전 Migration: `0013`
- Backup: `backups/daon-user-pre-0017-20260815T082849+0900.dump`
- Backup size: `681962 bytes`
- Backup SHA-256: `0c4918b58a60200bb190eb88315b23354d471808383591cce5023a9e4cd5efbc`
- `pg_restore --list`: `1183` entries
- Rollback image tags: API `sha256:92efb1f...`, Web `sha256:97b1559...`, Worker `sha256:e664bd7...`
- 신규 Step-up token key는 server `secrets/`에 48 random bytes·mode 0600으로 생성했다. 원문·Hash·Command line 출력과 Git 저장은 0이다.

## Build·Migration·전환

- API/Web/Worker build PASS. Web ARM64 production build는 compile·TypeScript·9 pages·boundary `295/0` PASS.
- Migration은 PostgreSQL admin credential을 stdin memory-only로 전달해 `0013→0014→0015→0016→0017` PASS.
- `offline_knowledge_copy_grants`, `security_audit_events`, `workspace_output_version_settings`, `workspace_output_version_settings_idempotency` 모두 RLS enabled+forced PASS.
- API/Worker/Web만 recreate했다. Object Storage·shared-db·Proxy·Netdata는 재생성하지 않았다.

## 최종 Service

- API container `1d65a0a82650...`, image `sha256:f1db649e...`, healthy
- Worker container `019562f20789...`, image `sha256:6654cce8...`, running
- Web container `1638b41cc78a...`, image `sha256:8ca88db1...`, healthy
- Object Storage `e5c606...`, shared-db `c962c98...`, Proxy `337026...`, Netdata `8cab33...`: ID 불변
- Public root HTTP 200, `/bff/shell/runtime` HTTP 200, unauthenticated `/bff/api/session` HTTP 401
- API/Web/Worker recent fatal·traceback·panic·internal URL·secret scan 0, one-off container 0
- Server Git dirty는 보호 `backups/`, `secrets/`만 존재

## 인증된 운영 Browser Gate

- 운영 URL: `https://daon-user.sinsan.kr/workspaces/workspace-be846e417dc13c1ec9f866ff`
- 로그인된 Chrome Session에서 Workspace `준비 · Cloud`와 Raw Source 5건(사용 가능 1)을 확인했다.
- 질문 Context는 선택된 Raw Source에 결속되고, Studio 구현 Tile 5종과 향후 Tile 6종 disabled 상태를 확인했다.
- `LLM 설정`: 9 Provider, UPSTAGE active·credential configured, Endpoint 원문 비노출, 모델·Role mapping 조회 PASS.
- `출력·버전`: 구현 산출물 5종 형식과 append-only 저장 원칙 조회 PASS.
- `동기화·승인`: 자동 전송 금지와 현재 승인 대기 0건 조회 PASS. 승인 실행 0.
- `조직 정책`: 조직 강제 8필드와 Workspace effective deny를 입력 Control 0인 읽기 전용으로 조회 PASS.
- `운영상태`: Provider/API/Storage/Sync/Queue 5개 실제 상태 조회 PASS.
- Browser console warning/error 0, internal URL·credential·token·SQLSTATE·stack 노출 0.
- 정책 변경·승인·산출물 생성은 수행하지 않았다.

## 판정

- `YSNA_DEPLOYMENT_PASS / AUTHENTICATED_WEB_BROWSER_PASS`
- 로컬 Phase A·B 구현과 ysna-server Web/API/DB 통합 배포 범위는 완료했다.
- 전체 Work Order의 Windows Desktop provider actual Gate는 별도 환경 Gate로 계속 OPEN이다.
