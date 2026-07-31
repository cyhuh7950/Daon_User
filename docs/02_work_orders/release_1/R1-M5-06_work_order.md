# R1-M5-06 삭제·보존·Legal Hold 작업지시서

## 승인 기준과 Writer

- 작업지시서 버전: `1.0` · 2026-07-31.
- Work Order ID와 Issue ID는 모두 `R1-M5-06`으로 고정한다.
- 공식 작업공간: `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`.
- Branch `codex/r1-m5-06`, 기준 HEAD `5038cb6334b339bb750ac40dff062beffd690733`.
- 승인 결정: `R1-D026`, C2, `APR-R1-M5-06-RETENTION-API-20260731-01`.
- 승인 정본은 `AGENTS.md`, 상세 설계 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.8의 §14.5·§16.2·§17·§20·§27, 구현계획 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.1의 §3·§6·§7·§15·§21~§24, 테스트계획 `docs/04_test_reports/release_1_test_plan.md` 0.7, `docs/01_architecture/DECISIONS.md`의 R1-D009·R1-D017·R1-D026, `docs/02_work_orders/release_1_baseline_manifest.json`이다. 모든 정본을 EOF까지 읽고 적용한다.
- 선행 R1-M4 Auth·Step-up·Audit, R1-M5-01 Cloud, R1-M5-02 Object Queue, R1-M5-03 Local Encryption, R1-M5-04 Canon, R1-M5-05 Sync 계약을 재사용하고 우회하지 않는다.
- 어울2가 이 Branch와 범위의 유일한 코드 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.
- `D:\Project\Daon_User`와 `C:\tmp`는 수정·삭제·작업 전환하지 않는다.
- 외부 Untracked `docs/04_test_reports/release_1/interim_review_2026-07-30.md`, `docs/04_test_reports/release_1_model_provider_queries.md`는 수정·삭제·Stage하지 않는다.

## 단일 목표와 완료 조건

- 목표: Source 삭제 요청을 즉시 비활성화·30일 유예·Legal Hold 우선·정규화 파생 정리·검증 가능한 영구 Purge로 처리하고, 콘텐츠와 분리된 최소 Audit 계보와 Known Local Copy 접근 불가 증거를 보존하는 운영형 계약을 구현한다.
- 삭제 요청 즉시 새 Run·Sync·Export·KnowledgeRegistration 사용을 차단하지만 SourceVersion·Object를 즉시 물리 삭제하지 않는다.
- 모든 파생 항목이 실제로 정리되고 Local Copy 접근 불가가 증명되기 전에는 `purged` 성공으로 표시하지 않는다.
- 동일 Idempotency Key의 요청·Purge 재실행은 중복 상태·삭제·Audit 효과를 만들지 않는다.
- 전용 Fixture만 생성·Purge하며 기존 사용자·운영 데이터는 변경하거나 삭제하지 않는다.

## 승인된 공개 API 6종

1. `POST /api/v1/sources/{id}/deletion-requests`
2. `GET /api/v1/deletion-requests/{id}`
3. `POST /api/v1/deletion-requests/{id}/cancel`
4. `POST /api/v1/deletion-requests/{id}/purge`
5. `POST /api/v1/sources/{id}/legal-holds`
6. `POST /api/v1/legal-holds/{id}/release`

- 모든 Write는 `Idempotency-Key`, `If-Match`, Tenant·Workspace Scope, 현재 AccessDecision, 안전 오류, Trace·Audit를 요구한다. GET도 현재 권한을 재검증한다.
- 영구 Purge와 Legal Hold 적용·해제는 조직 권한과 현재 정책을 작업 시작 직전에 재검증하고 `actor + action + target + policy_version`이 모두 일치하는 유효한 StepUpAuthorization을 요구한다. Invalid·Expired·Reused·다른 결합의 Step-up은 작업 시작 전 거부한다.
- 공개 안전 오류는 `DELETION_GRACE_PERIOD_ACTIVE`, `LEGAL_HOLD_ACTIVE`, `DELETION_CLEANUP_PENDING`, 기존 `STEP_UP_REQUIRED`, `CURRENT_ACCESS_DENIED`만 사용한다. 필요 이상의 새 Code와 내부 Host·Object Path·Secret·원문·Content Digest 노출을 금지한다.
- Browser 코드는 변경하지 않는다. 향후 Browser 소비 경로도 same-origin BFF만 사용한다.

## 상태·정규화 Cloud 데이터 계약

- `DeletionRequest` 정상 상태는 `requested → deactivated → grace_period → cleanup_pending → purged`, 대체 상태는 `cancelled | blocked_by_hold | failed`다. 허용 전이는 서비스와 DB 계약에서 결정론적으로 강제한다.
- `grace_until` 전 Purge를 금지한다. Cancel은 Purge 시작 전만 허용하며 기존 SourceVersion을 새로 쓰지 않고 Source 사용 가능 상태를 복구한다.
- Active LegalHold는 어느 단계에서든 Purge보다 우선하여 `blocked_by_hold`로 만든다. Hold 해제 뒤 기한 전이면 `grace_period`, 기한 경과면 `cleanup_pending`으로 복귀한다.
- 영구 Purge는 유예 종료, Active Hold 0건, 유효 Step-up, 현재 권한·정책, `If-Match`가 모두 유효할 때만 시작한다.
- Migration `0005`로 `DeletionRequest`, `DeletionCleanupItem`, `DeletionAttempt`, `LegalHold`, `LegalHoldTarget`과 AuditEvent·Trace 관계를 정규화하거나 같은 계약을 갖는 Cloud Schema를 구현한다. Tenant·Workspace Scope, 복합 FK, 강제 RLS, 최소 권한, Optimistic Concurrency를 적용한다.
- Derivative Inventory는 original object/content, Index, Preview, Cache, known Local Copy, Sync target/reference를 개별 항목으로 고정한다. 각 상태·시도·결과·Audit·Trace는 Append-only이며 한 항목이라도 미정리면 `purged`를 금지한다.
- 부분 실패는 `cleanup_pending` 또는 재시도 가능한 `failed`로 남기고 실패 항목만 재시도한다. 완료 항목을 중복 삭제하거나 완료 전에 성공으로 가장하지 않는다.
- 콘텐츠 삭제 후 1년간 보존할 최소 계보는 opaque ID, actor/action/target, timestamp, policy/hold/deletion decision, trace/hash chain뿐이다. 콘텐츠와 분리하고 현재 접근 권한을 부여하지 않는다.

## Local-private Tombstone·Ack 계약

- M5-03 SQLCipher 경계에 Known Local Copy의 암호화 Tombstone, Ack 상태, 장치 Reference, Revoke/Key Destruction 증거 Reference를 저장한다. Payload·경로·Key·Token·원문·Content Digest를 평문 파일·Log·Evidence에 남기지 않는다.
- 온라인 장치 Ack 또는 Revoke/Key Destruction으로 접근 불가가 증명되기 전에는 해당 Local Copy를 완료로 표시하지 않는다. Restart 뒤에도 Tombstone·미확인 Ack·재시도 상태가 복구되어야 한다.
- 실제 Device Pairing·Relay는 R1-M6-04 범위다. 이번 작업은 기존 Local 경계와 전용 Fixture로 계약을 검증하며 실제 Ack 수신을 Mock 성공으로 가장하지 않는다.

## 허용·제외 범위

- 허용: Migration `0005`, 삭제·보존·Legal Hold Domain/Repository/Service/API/OpenAPI, 서버 내부 Object/Queue 연계, Local SQLCipher Tombstone/Ack 연동, Unit·Contract·Integration·Migration·RLS·Failure Injection·Runtime/API Test, 진행·Evidence·완료보고.
- 제외: Browser 코드, R1-M5-07 Backup·Restore 제품 기능, R1-M6-04 실제 Device Pairing·Relay, 운영 OCI, 외부 서버 배포·격리 자원 생성, 실제 사용자·운영 데이터 Purge, 승인된 6개 외 공개 API.
- 관련 없는 Refactor·전체 재작성·의존성·설정 변경, 임시 운영 구조와 Mock 성공을 금지한다.
- 공개 API·데이터 계약·보안 경계·중요 위험 또는 배포 범위를 바꿔야 하면 코드를 수정하지 말고 증거와 함께 `BLOCKED`로 어울1의 판단을 요청한다.

## TDD와 필수 검증

- 먼저 RED를 기록한다. 최소 시나리오는 유예 전 Purge, Hold 적용·해제, Invalid·Expired·Reused·결합 불일치 Step-up, Cross-Tenant/Workspace, Duplicate Request/Purge, `If-Match` Lost Update, 부분 Derivative 실패·실패 항목만 Retry, Local Tombstone Restart/Ack, Audit 최소 계보, Fixture-only Guard다.
- RED가 승인 계약 위반을 정확히 증명한 뒤 최소 구현으로 GREEN을 만들고, 통과 중인 기존 테스트를 임의 수정해 RED를 제거하지 않는다.
- Migration은 전용 PostgreSQL 18.4 빈 DB에서 `0001→0002→0003→0004→0005`, `0005` 재적용, `0005→0004→0005`를 검증한다. 실제 Schema·FK·RLS·Append-only·Rollback/Reapply 증거를 남긴다.
- RLS/무결성은 실제 `daon_app` Session으로 Tenant·Workspace 교차 접근 0, Cross-scope FK 거부, Hold 우선, 중복 삭제 0, 완료 항목 재삭제 0을 증명한다.
- Object/Derivative는 전용 Object Fixture로 original content·Index·Preview·Cache·Sync reference의 결과와 부분 실패/재시도를 검증한다. 기존 Object를 삭제하지 않는다.
- Local은 실제 암호화 DB에서 Tombstone 저장·Restart 복구·Ack/미확인·Revoke/Key Destruction 접근 불가를 검증하고 평문 Canary 0건을 확인한다.
- API/OpenAPI는 6개 Route의 정확한 Method·Path·Schema·Runtime 등록을 결정론적으로 대조한다. Auth·Step-up·Idempotency·`If-Match`·현재 AccessDecision·안전 오류와 실제 HTTP 의미를 검증한다.
- Audit은 콘텐츠와 분리된 최소 계보, Append-only Trace/Hash Chain, 사용자 증거의 원문·Object Path·Secret·Content Digest 0건을 검증한다.
- 회귀는 M4 Auth/Step-up/Audit, M5-01 Cloud, M5-02 Object Queue, M5-03 Local Encryption, M5-04 Canon, M5-05 Sync와 API·OpenAPI·Quality Gate·독립성 검사를 실행한다.
- 테스트와 Purge는 새 전용 Tenant/Workspace/Source/Object/Local Fixture만 사용한다. Fixture ID Allowlist 밖 대상이 하나라도 있으면 Purge를 Fail-close하고 기존 데이터 Before/After 불변을 증명한다.

## 구현·증거·배포 Gate

- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M5-06_progress.md`.
- Evidence Pack: `docs/03_evidence/release_1/R1-M5-06/`; Manifest: `docs/03_evidence/release_1/R1-M5-06/manifest.json`.
- 완료보고 정본: `docs/04_test_reports/release_1/R1-M5-06_completion_report.md`.
- Evidence Manifest는 exact Commit SHA, Migration Revision/재적용/Rollback-Reapply, PostgreSQL 18.4/RLS, Derivative·Object 결과, Local Encryption/Restart/Tombstone/Ack, API/OpenAPI 6 Route, 상태 전이, Step-up 결합, Idempotency·Concurrency, 최소 Audit 계보, Fixture Guard, 기존 데이터 불변과 회귀를 연결한다.
- 로컬 구현·검증이 모두 통과하면 구현 SHA를 Push한 시점에서 멈춘다. 외부 서버 배포와 격리 자원 생성은 이번 승인에 포함되지 않으므로 `BLOCKED / approval-needed`로 보고하고, 어울1이 신산님에게 별도 배포 승인을 받은 뒤에만 수행한다.
- 외부 배포 증거가 없음을 로컬 구현 실패로 가장하지 않으며, 승인 전 서버 명령·Migration·자원 생성·정리는 실행하지 않는다.

## 진행·결과 계약

- 진행 파일에 착수, 영향 분석, RED, Migration/Cloud, Object/Derivative, Local Tombstone, API/OpenAPI, 부분 실패·재시도, 회귀, Commit/Push와 종료 직전을 시각·상태·변경 파일·명령/결과·오류/원인/복구·다음 작업과 함께 기록한다.
- 종료 전 Local HEAD·Origin Branch·검증 exact SHA, Working Tree, 변경 파일, 잔여 Process·Listener, 보호 Untracked와 정식 실패보고 횟수를 확인한다.
- 결과는 `판정 → 판단 이유 → 조치` 순서와 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환하며 `issue_id`는 항상 `R1-M5-06`을 사용한다.
- 실제 PostgreSQL 18.4/RLS, Object/Derivative, 암호화 Local DB, Runtime API/OpenAPI 6 Route, 회귀와 전용 Fixture Guard 증거가 없으면 `COMPLETED`로 보고하지 않는다.
