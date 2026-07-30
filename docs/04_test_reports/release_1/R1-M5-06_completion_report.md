# R1-M5-06 구현·검증 보고서

## 판정

`REVIEW_PENDING` — 로컬 구현·자동 회귀·Quality Gate와 승인된 ysna-server 격리 PostgreSQL 18.4/RLS·Object Fixture 검증까지 수행했다. 최종 완료 판정은 어울1이 증거를 검토한 뒤 결정한다.

## 판단 이유

- Migration `0005`, 삭제·보존·Legal Hold Domain, 승인 API/OpenAPI 6종, Local SQLCipher Tombstone/Ack 계약을 구현했다.
- 요청 즉시 비활성화, 30일 유예, Hold 우선, 멱등·Optimistic Concurrency, 실패 항목만 재시도, Local Ack 전 `purged` 금지와 Fixture Allowlist를 자동 검증했다.
- API 전체 139건, Local 전체 92건과 최종 Quality Gate 7개 범주가 통과했다.
- C01에서 내부 Retention Domain 오류의 직접 공개를 제거하고 승인 신규 3코드만 OpenAPI에 추가했다. 현재 Domain 오류 19종의 공개 매핑을 전수 확인했으며 관련 Contract/Runtime 6건, OpenAPI 9건, Quality Gate Contract/Security Test 37건과 Workspace Lint가 통과했다.
- ysna-server 전용 Project에서 PostgreSQL 18.4 Migration `0001→0005`, 0005 재적용, `0005→0004→0005`, 실제 `daon_app` NOBYPASSRLS, Cross-Tenant·Workspace, FK·Append-only와 Active Hold Guard가 통과했다.
- 전용 MinIO Bucket과 Fixture만 사용해 유예·Hold·부분 실패·실패 항목 재시도·Local Ack·Purge를 검증했다. Runtime Health는 실제 PostgreSQL·MinIO 연결에서 Live·Ready 200이었다.
- Server Target은 Retention/API/MinIO 13건, PostgreSQL 1건, Local Tombstone 2건이 통과했다. Local 전체는 91건 통과·Platform 2건 Skip이다.
- Server API 전체 139건 중 138건이 통과했다. Python Runtime Image에 Node/Next가 없어 발생한 존재성 계약 1건은 Ephemeral Harness에서 별도 통과했다. 동일 DB 재실행의 고정 Fixture 충돌은 유효 판정에서 제외하고 증거에 원인을 기록했다.
- Browser 코드·의존성·설정·외부 서버·기존 운영 데이터는 변경하지 않았다.
- 최신 구현 Commit은 `0f3b1c1a1a19615c5986449ffe89cee005f0371b`이다.

## 조치

- 서버 검증 Evidence Commit을 Branch `codex/r1-m5-06`에 Push한다.
- 어울1이 Evidence Manifest·Progress·이 보고서와 서버 자원 상태를 검토해 최종 판정을 내린다.
- 전용 서버 자원은 승인 조건대로 정리하지 않고 유지한다. Cleanup이 필요하면 별도 승인을 받는다.

## 변경 결과

- Cloud: 정규화 Retention/Legal Hold Schema, RLS, 상태·Hold·Cleanup Guard, 최소 계보와 Append-only Trigger.
- API: 승인된 6 Route, 현재 권한, Idempotency Key, If-Match, Step-up actor/action/target/policy 결합, 안전 오류와 Trace.
- Local: 암호화 Tombstone Version Chain과 Ack·Revoke·Key Destruction 증거.
- Test: Domain, Migration/OpenAPI Contract, Runtime HTTP, PostgreSQL 조건부 Integration, Local 암호화/Restart.

세부 명령과 복구 이력은 `R1-M5-06_progress.md`, 안전 요약은 Evidence Pack의 `verification-summary.md`와 `manifest.json`에 기록했다.
