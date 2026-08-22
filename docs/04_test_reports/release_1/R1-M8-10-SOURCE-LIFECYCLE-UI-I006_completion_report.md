# R1-M8-10-SOURCE-LIFECYCLE-UI-I006 완료 보고

## 판정

`CODE_VERIFIED / BROWSER_ACTUAL_PENDING`

## 판단 이유

- Source·Knowledge·Conversation·Studio 오류 소유권과 stale load 차단을 구현하고 actual React 회귀를 통과했다.
- `Notebook에서 제거`는 append-only unbinding으로 구현되어 원본 Source를 보존하며 선택 Context·질문·Studio에서 즉시 제외된다.
- `Source 삭제 요청`은 Browser inventory 입력을 제거하고 서버 authoritative exact6 inventory, 30일 grace, Legal Hold 우선 상태, cancel, idempotency, Audit, RLS와 재시작 지속성을 PostgreSQL에 결속했다.
- REWORK1의 서버 즉시 비활성화, durable Hold/Purge, refresh 복원, unbind concurrency, inventory mutation serialization과 exact DTO를 닫았다.
- actual PostgreSQL fresh migration/rollback/reapply, 10개 실제 테스트, API full 493개, Node focused 23개, OpenAPI, Web build/TypeScript/boundary가 통과했다.
- 실제 운영 사용자 Source의 변경은 0이다. 1920×1080 actual Browser에서 두 동작의 dialog·tooltip·오류 상태를 최종 확인하는 Gate는 아직 실행하지 않았으므로 전체 `COMPLETED`로 과장하지 않는다.

## 변경 결과

- Domain/DB: Notebook unbinding ledger(`0021`), durable request/hold/release/fixture purge runtime(`0022`), Source lifecycle predicate와 inventory mutation serialization.
- API/OpenAPI: 승인된 source-unbindings POST, 기존 deletion request/get/cancel 경로의 server-authoritative inventory 계약.
- Web/UI: same-origin client, Notebook Source action menu의 연결 해제와 삭제 요청 분리, pane별 오류 상태와 retry ownership.
- 안전성: 물리 DELETE0, purge UI call0, cross-scope0, duplicate/replay write0, unverified Local Copy purge 차단, 내부 URL/secret projection0.

## 테스트 결과

- API full: `493 passed, 42 skipped, 137 subtests passed`.
- actual PostgreSQL: `10 passed`; fresh upgrade/empty rollback/reapply/live downgrade block; cleanup `db=0 role=0`.
- Node focused: `23 passed` (REWORK1 exact selection).
- OpenAPI: `75 paths / 94 operations / 120 schemas / 31 errors`, verifier PASS.
- Web: lint PASS, Next production build+TypeScript PASS, boundary `violations=0`, `boundaryErrors=0`.

## 미해결 사항

- 1920×1080 actual Browser read-only/harness 검증과 화면 캡처가 남아 있다.
- 현재 branch는 승인된 I006 임시 통합 기준선이며 추후 `master` 통합 판단이 필요하다.

## 다음 판단

- 어울1은 독립 검토 후 actual Browser Gate 수행 여부와 `master` 통합 범위를 판단한다.
- actual Browser Gate 전 commit/push/deploy와 운영 사용자 Source delete/unbind는 수행하지 않는다.
