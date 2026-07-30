# R1-M5-06 구현·검증 보고서

## 판정

`BLOCKED` — 로컬 구현, 자동 회귀와 Quality Gate는 통과했지만, 작업지시서가 `COMPLETED`에 요구한 실제 PostgreSQL 18.4/RLS와 Object Fixture 검증 환경이 없어 완료로 선언하지 않는다.

## 판단 이유

- Migration `0005`, 삭제·보존·Legal Hold Domain, 승인 API/OpenAPI 6종, Local SQLCipher Tombstone/Ack 계약을 구현했다.
- 요청 즉시 비활성화, 30일 유예, Hold 우선, 멱등·Optimistic Concurrency, 실패 항목만 재시도, Local Ack 전 `purged` 금지와 Fixture Allowlist를 자동 검증했다.
- API 전체 136건, Local 전체 92건과 최종 Quality Gate 7개 범주가 통과했다.
- PostgreSQL·MinIO 환경 변수가 없고 Docker CLI가 없으며 WSL 접근도 거부돼 실제 Migration/RLS/Object 검증은 조건부 Skip이었다. 이를 Mock 성공이나 정적 통과로 대체하지 않는다.
- Browser 코드·의존성·설정·외부 서버·기존 운영 데이터는 변경하지 않았다.

## 조치

- 구현 Commit을 Branch `codex/r1-m5-06`에 Push한다.
- 외부 ysna-server 배포와 격리 PostgreSQL 18.4·Object 자원 생성은 별도 승인 전 실행하지 않는다.
- 승인 후 전용 Fixture만으로 Migration `0001→0005`, `0005→0004→0005`, `daon_app` RLS, Object 부분 실패·재시도와 Before/After 불변을 실행하고 Manifest를 실제 Commit SHA와 결과로 갱신해야 한다.

## 변경 결과

- Cloud: 정규화 Retention/Legal Hold Schema, RLS, 상태·Hold·Cleanup Guard, 최소 계보와 Append-only Trigger.
- API: 승인된 6 Route, 현재 권한, Idempotency Key, If-Match, Step-up actor/action/target/policy 결합, 안전 오류와 Trace.
- Local: 암호화 Tombstone Version Chain과 Ack·Revoke·Key Destruction 증거.
- Test: Domain, Migration/OpenAPI Contract, Runtime HTTP, PostgreSQL 조건부 Integration, Local 암호화/Restart.

세부 명령과 복구 이력은 `R1-M5-06_progress.md`, 안전 요약은 Evidence Pack의 `verification-summary.md`와 `manifest.json`에 기록했다.
