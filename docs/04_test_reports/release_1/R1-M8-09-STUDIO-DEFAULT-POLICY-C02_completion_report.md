# R1-M8-09-STUDIO-DEFAULT-POLICY-C02 완료 보고

## 판정

IN_PROGRESS — `R1-M8-09-STUDIO-DEFAULT-POLICY-C02-I001`

## 구현 결과

- Migration `0013`이 기존 Workspace를 backfill하고 신규 Workspace INSERT transaction에서 Studio 기본 Canon 6종을 생성한다.
- 기존 유효 WorkspacePolicy·KnowledgeScope·WeightProfile은 보존하며, WeightProfile은 선택된 유효 KnowledgeScope를 FK로 참조한다.
- 최신 Binding이 가리키는 유효한 RuleSetReference·RuleSetVersionSnapshot·RuleSetBinding complete 계보는 그대로 보존하고 신규 행을 만들지 않는다. 3종 일부 또는 전체 누락일 때만 결정론적 `0013` 전용 세트 3종 전체를 생성하며 기존 행은 수정·삭제하지 않는다.
- deterministic ID 충돌 후 exact canonical text·digest·`created_by`·FK가 `0013` 소유 기본값과 일치하지 않으면 `STUDIO_DEFAULT_POLICY_ID_CONFLICT` SQLSTATE 55000으로 전체 migration을 fail-close한다. Helper 종료 시 필수 6종 postcondition도 검증한다.
- WorkspacePolicy·KnowledgeScope·WeightProfile과 RuleSet Binding은 유효성 필터 전에 최고 version 행 수를 판정한다. 최고 version 동률은 `STUDIO_DEFAULT_POLICY_HISTORY_AMBIGUOUS`, 최고 단일 행의 inactive/stale/wrong-scope/필수값·FK 불일치는 `STUDIO_DEFAULT_POLICY_LATEST_INVALID` SQLSTATE 55000으로 fail-close해 Runtime 선택과 일치시킨다.
- Question Repository가 만든 exact legacy KnowledgeScope v1만 record ID·2-key payload·단일 SourceVersion·Tenant/Workspace 결속을 확인한 뒤 같은 aggregate의 결정론적 v2로 append한다. 기존 v1은 immutable 보존하며, v2는 exact 확장 payload와 `previous_version_id=v1`을 사용하고 WeightProfile은 v2를 FK로 참조한다. 동일 v2는 idempotent이고 유사 legacy 또는 compat ID 충돌은 transaction 전체를 fail-close한다.
- canonical text는 key 정렬·공백 없는 exact JSON이며 SHA-256 digest, Canon FK, immutable trigger와 RLS를 유지한다.
- downgrade는 비소유 계보 참조가 있으면 `STUDIO_DEFAULT_POLICY_ROLLBACK_BLOCKED`로 전체 fail-close한다. 참조가 없을 때만 6 immutable trigger를 migration role로 잠시 비활성화하고 결정론 ID와 `created_by='migration:0013'`가 함께 일치하는 소유 행만 FK 역순 삭제한다.
- Studio 정책 누락은 공개 409 `POLICY_PROJECTION_UNAVAILABLE`, DB 장애는 기존 503 `STUDIO_DATABASE_UNAVAILABLE`로 구분된다.

## 검증 결과

- Migration 정적 계약: 7 passed(legacy RED는 구현 전 1 failed, 6 passed).
- Studio focused: 15 passed, 1 skipped(전용 PostgreSQL DSN 환경변수 기반 별도 test; actual Gate로 실행 범위 대체 검증).
- Studio+Egress 관련: 16 passed, 1 skipped.
- 전체 API: 360 passed, 26 skipped, 134 subtests passed.
- Node Workspace/Studio/BFF/OpenAPI: 61 passed.
- OpenAPI verifier: paths 75, operations 94, schemas 120, errors 31.
- Web production build 및 TypeScript: PASS.
- Product UI Boundary: scanned 281, violations 0, boundary errors 0.
- `git diff --check`: PASS. staged files: 0.

## 실제 PostgreSQL 증거

- 실제 공용 PostgreSQL 컨테이너의 고유 disposable DB에서 fresh `0001→0013` 실행.
- 전체누락·부분누락 backfill, 완전구성 RuleSet 신규 소유 행 0과 기존 ID 보존, helper 반복 idempotency, 신규 Workspace trigger 6종, digest, explicit FK rejection, immutable SQLSTATE 55000, daon_app RLS cross-tenant 0을 확인.
- deterministic ID의 inactive/non-owned 선점 충돌은 `STUDIO_DEFAULT_POLICY_ID_CONFLICT`로 실패하고 migration revision 0012 및 기존 충돌 행이 보존됨을 확인.
- 유효 v1 뒤 inactive v2가 있는 history와 동일 max version 2행 history는 각각 latest-invalid/history-ambiguous로 실패하고 revision 0012 및 기존 행이 모두 보존됨을 확인.
- exact legacy Question Scope v1은 동일 aggregate v2로 승격되고 helper 2회에도 2행만 유지되며 WeightProfile FK가 v2를 가리킴을 확인했다.
- extra/missing key, wrong mode, 2-element/string array, wrong record ID, missing/cross-workspace SourceVersion 8종과 compat ID collision은 모두 revision 0012·기존 행 보존 상태로 fail-close함을 확인했다.
- non-owned ScopeSnapshot이 migration-owned compat v2를 참조하면 downgrade 전체 rollback, 참조 제거 후에는 v2만 삭제하고 v1을 보존하며 reapply가 동일 v2 ID를 복원함을 확인했다.
- 비소유 RuleEvaluation 참조 상태의 downgrade 전체 rollback과 revision/행 보존을 확인.
- 참조 제거 후 owned-only `0013→0012`, 기존 Canon 보존, `0012→0013` 결정론 reapply를 확인.
- 최종 disposable DB 삭제 후 matching prefix remaining 0, 공용 `local-postgres` running=true.

## 범위와 미해결

- 제품 공개 API, Repository projection SQL, Egress 정책값·보안경계, 의존성은 변경하지 않았다.
- target `6bfd10b` ysna 배포는 승인받아 backup·rollback image·build까지 수행했으나 운영 KnowledgeScope 최신 Canon 한 행의 필수 `workspace_id`·`scope` 누락으로 Migration 0013이 `STUDIO_DEFAULT_POLICY_LATEST_INVALID` fail-close했다.
- DB revision0012와 기존 데이터가 보존됐고 서비스 recreate 없이 checkout/API image tag를 사전 `9845890` 상태로 복구했다. 운영 Browser Gate는 target이 배포되지 않아 수행하지 않았다.
- 다음 진행에는 해당 운영 Canon을 immutable 계보에 맞게 교정하는 별도 데이터 변경 또는 migration 호환 계약 변경에 대한 신산님 승인이 필요하다.
- 신산님이 승인한 exact legacy Question KnowledgeScope v2 append-only 호환 구현과 local actual PostgreSQL Gate는 완료했다. 운영은 여전히 rollback 상태인 revision 0012이므로 어울1 검토·commit/push와 재배포 Gate 전에는 최종 COMPLETED로 승격하지 않는다.
