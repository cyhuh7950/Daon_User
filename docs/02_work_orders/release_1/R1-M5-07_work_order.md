# R1-M5-07 Backup·Restore·Local 손상 복구 작업지시서

## 승인 기준과 Writer

- 작업지시서 버전: `1.0` · 2026-07-31.
- Work Order ID와 Issue ID는 모두 `R1-M5-07`로 고정한다.
- 공식 작업공간: `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`.
- Branch `codex/r1-m5-07`, 기준 HEAD `5a6d36f5826115e9570f256859c8d26271c8e53f`.
- 승인 결정: `R1-D027`, C2, `APR-R1-M5-07-RECOVERY-API-20260731-01`.
- 승인 정본은 `AGENTS.md`, 상세 설계 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.9의 §14.4·§16.3·§17·§21.4·§27, 구현계획 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 1.2의 §3·§6·§7·§15·§21~§24, 테스트계획 `docs/04_test_reports/release_1_test_plan.md` 0.7, `docs/01_architecture/DECISIONS.md`의 R1-D009·R1-D017·R1-D027, `docs/02_work_orders/release_1_baseline_manifest.json`이다. 모두 EOF까지 읽고 적용한다.
- 선행 R1-M4 Auth·Step-up·Audit, R1-M5-01 Cloud, M5-02 Object Queue, M5-03 Local Encryption, M5-04 Canon, M5-06 Retention 계약을 재사용하고 우회하지 않는다.
- 어울2가 이 Branch와 제품 구현 범위의 유일한 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.
- `D:\Project\Daon_User`와 `C:\tmp`는 수정·삭제·작업 전환하지 않는다.
- 외부 Untracked `docs/04_test_reports/release_1/interim_review_2026-07-30.md`, `docs/04_test_reports/release_1_model_provider_queries.md`는 수정·삭제·Stage하지 않는다.

## 단일 목표와 완료 조건

- 목표: 검증 가능한 Cloud Backup, Preview 후 별도 승인으로 실행하는 격리 Restore, 암호화 격리 기반 Local 손상 복구를 운영 화면·API에서 제공한다.
- 자동 Backup은 Pilot RPO 15분을 만족하고 조직 관리자 수동 요청도 같은 검증 경로를 사용한다.
- Restore는 새 전용 Fixture 목적지만 사용하며 원본 Tenant·Workspace·DB·Bucket을 덮어쓰지 않는다.
- 현재 Retention Ledger·Legal Hold·Deletion Tombstone이 Backup보다 우선하며 Purge된 콘텐츠를 되살리지 않는다.
- 운영 데이터·운영 자원 Restore와 파괴적 손상 주입은 G9-DRILL 없이 실행하지 않는다.
- Local 무결성을 증명할 수 없으면 `manual_recovery_required`로 종료하고 조용히 폐기하거나 성공으로 표시하지 않는다.

## 승인된 공개 API와 상태

Cloud 공개 API는 다음 7개로 제한한다.

1. `POST /api/v1/backups`
2. `GET /api/v1/backups`
3. `GET /api/v1/backups/{id}`
4. `POST /api/v1/backups/{id}/restore-previews`
5. `GET /api/v1/restore-requests/{id}`
6. `POST /api/v1/restore-requests/{id}/execute`
7. `POST /api/v1/restore-requests/{id}/cancel`

Windows Local Loopback API는 다음 3개로 제한한다.

1. `POST /local/v1/recovery/scans`
2. `GET /local/v1/recovery/jobs/{id}`
3. `POST /local/v1/recovery/jobs/{id}/repair`

- Backup: `queued → capturing → verifying → ready`, 대체 `failed | expired`.
- Restore: `requested → preview_ready → authorized → restoring → verifying → completed`, 대체 `cancelled | failed | blocked`.
- Local Recovery: `detected → quarantined → scanning → repairable → repairing → verified`, 대체 `manual_recovery_required | failed`.
- 공개 SafeError를 새로 추가하지 않는다. 승인된 기존 `INVALID_REQUEST`, `RESOURCE_UNAVAILABLE`, `CURRENT_ACCESS_DENIED`, `STEP_UP_REQUIRED`에 내부 오류를 안전하게 매핑한다.

## Cloud Backup·Restore 계약

- Migration `0006`으로 BackupRecord·BackupManifest·RestoreRequest·RestorePreview·RestoreVerification과 AuditEvent·Trace 관계를 정규화하거나 동일 계약을 갖는 Schema를 구현한다. Tenant·Workspace Scope, 복합 FK, RLS, 최소 권한, Optimistic Concurrency를 적용한다.
- Backup은 일관된 PostgreSQL Snapshot, Object Inventory·Checksum, Schema Revision, 정책·보존 Watermark, 불투명 계보 Manifest를 한 세트로 고정하고 암호화한다. Secret·원문·Raw Object Path를 사용자 증거에 남기지 않는다.
- Preview는 대상·제외 대상·현재 Retention/Legal Hold/Tombstone 조정·격리 목적지를 보여준다. Preview와 Execute 각각 현재 조직 권한·Workspace ACL·AccessDecision·정책을 다시 확인한다.
- Execute는 `actor + action + target + policy_version`이 정확히 일치하는 새 StepUpAuthorization, 현재 Preview Version, `If-Match`, Idempotency Key를 요구한다. Preview 때의 권한·승인을 재사용하지 않는다.
- Execute 직전과 `completed` 전 Tenant·Workspace·RLS·현재 ACL·계보·Audit Hash Chain·Legal Hold·Deletion Tombstone을 재검증한다.
- Write는 Idempotency Key와 적용 가능한 경우 `If-Match`를 요구하고, GET도 현재 접근 권한을 재검증한다. 중복·stale·다른 결합 Step-up은 효과 0건으로 Fail-close한다.
- Fixture Allowlist 밖 대상, 운영 대상, 제자리 덮어쓰기, G9-DRILL 없는 파괴적 복구는 `blocked`로 종료한다.

## Local 손상 복구 계약

- M5-03 SQLCipher·OS Secure Store 경계를 재사용한다. 손상 대상·격리본·Journal·상태·검증 결과를 암호화하고 평문 Key·Token·원문·Raw Path를 Log·Evidence에 남기지 않는다.
- Scan은 손상 자료를 암호화 격리한 뒤 Snapshot·Journal·정본 Metadata·Checksum으로 복구 가능성을 판정한다.
- Repair는 검증된 자료와 전용 Fixture Allowlist 안에서만 수행한다. 부분 손상·Checksum 불일치·Journal 누락·Restart를 검증하고 무결성을 증명하지 못하면 `manual_recovery_required`로 전환한다.
- Restart 뒤 격리·검사·Repair 상태와 Audit·Trace를 복구한다. 완료 전 원본을 파괴하거나 손상 자료를 조용히 삭제하지 않는다.
- Windows 사용자는 R1-M2 Production-bound `BackupRestoreAdapter`를 승계한 운영 화면과 Local Loopback API로 상태·진행·결과를 확인한다. 사용자가 Python·DB·CLI 명령을 직접 실행하도록 요구하지 않는다.

## 허용·제외 범위

- 허용: Migration `0006`, Backup·Restore Domain/Repository/Service/API/OpenAPI, Object/Queue 연계, Local Recovery Domain·SQLCipher·Loopback API, Web same-origin BFF·Windows 운영 화면의 Production-bound Adapter 승계, Unit·Contract·Integration·Migration·RLS·Failure Injection·Runtime/API/E2E Test, 진행·Evidence·완료보고.
- 제외: 운영 OCI와 운영 데이터 Restore, 제자리 덮어쓰기, 실제 사용자 데이터 손상 주입, M9-07 정식 RTO/RPO 훈련, 승인된 7+3개 외 공개 API, 새 공개 SafeError Code.
- ysna-server 외부 배포·격리 자원 생성은 별도 승인 전 실행하지 않는다. 기존 M5-06 서버 자원과 `shared-db`, `common`, `netdata`, `proxy`를 사용·변경·정리하지 않는다.
- 관련 없는 Refactor·전체 재작성·의존성·설정 변경과 Mock 성공을 금지한다.
- 공개 API·데이터 계약·보안 경계·중요 위험 또는 배포 범위를 바꿔야 하면 코드를 수정하지 말고 증거와 함께 `BLOCKED`로 어울1에게 보고한다.

## TDD와 필수 검증

- 먼저 RED를 기록한다. 최소 부정 시나리오는 Preview 없는 Execute, Invalid·Expired·Reused·결합 불일치 Step-up, 권한·정책 변경, Cross-Tenant/Workspace, Duplicate/Stale Write, Fixture Allowlist 밖 대상, G9-DRILL 없는 운영 대상, Purged Content 부활 시도, Object 누락·Checksum 불일치, Local Restart·Journal 누락·검증 불가다.
- Migration은 전용 PostgreSQL 18.4 빈 DB에서 `0001→0002→0003→0004→0005→0006`, `0006` 재적용, `0006→0005→0006`을 검증한다. 실제 Schema·FK·RLS·Append-only·Rollback/Reapply 증거를 남긴다.
- 실제 `daon_app` Session으로 Tenant·Workspace 교차 접근 0, Cross-scope FK 거부, 현재 Retention·Hold·Tombstone 우선, Purged Content 부활 0을 증명한다.
- MinIO 전용 Fixture로 Manifest·Object Inventory·Checksum, 누락 Object, 손상 Object, 격리 목적지와 기존 Object 불변을 검증한다.
- Cloud Runtime/OpenAPI는 정확한 7개 Route, Local Runtime은 정확한 3개 Route를 결정론적으로 대조한다. Auth·Step-up·Idempotency·Concurrency·현재 AccessDecision·안전 오류와 실제 HTTP 의미를 검증한다.
- Local은 실제 암호화 DB에서 격리·Restart·Scan·Repair·`manual_recovery_required`와 평문 Canary 0건을 검증한다.
- Web·Windows 실제 화면에서 요청·목록·Preview·진행·결과를 확인한다. Browser Network의 Cloud 호출은 same-origin이어야 하며 내부 주소·`localhost`·`NEXT_PUBLIC_API_BASE_URL` 직접 호출이 0건이어야 한다.
- 회귀는 M4 Auth/Step-up/Audit, M5-01~06, API·OpenAPI·Web·Windows·Quality Gate·독립성 검사를 실행한다.

## 구현·증거·배포 Gate

- 진행 복구 기록: `docs/04_test_reports/release_1/R1-M5-07_progress.md`.
- Evidence Pack: `docs/03_evidence/release_1/R1-M5-07/`; Manifest: `docs/03_evidence/release_1/R1-M5-07/manifest.json`.
- 완료보고 정본: `docs/04_test_reports/release_1/R1-M5-07_completion_report.md`.
- Evidence Manifest는 exact Commit SHA, Migration Revision/재적용/Rollback-Reapply, PostgreSQL 18.4/RLS, MinIO Manifest·Checksum·누락/손상, Cloud 7·Local 3 Route, 상태 전이, Preview/Execute 권한·Step-up, Idempotency·Concurrency, Retention 조정, Local Encryption/Restart/Repair, actual 화면·same-origin Network, Fixture Guard와 회귀를 연결한다.
- 로컬 구현·검증·Push가 끝나면 멈춘다. ysna-server 배포·전용 DB Migration·서버 테스트는 어울1이 신산님에게 별도 승인을 받은 뒤에만 수행한다.
- 서버 승인이 없음을 로컬 구현 실패로 가장하지 않는다.

## 진행·결과 계약

- 진행 파일에 착수, 영향 분석, RED, Migration/Cloud, Object/Retention, Local Recovery, API/OpenAPI, 화면/Network, 회귀, Commit/Push와 종료 직전을 시각·상태·변경 파일·명령/결과·오류/원인/복구·다음 작업과 함께 기록한다.
- 종료 전 Local HEAD·Origin Branch·검증 exact SHA, Working Tree, 변경 파일, 잔여 Process·Listener, 보호 Untracked와 정식 실패보고 횟수를 확인한다.
- 결과는 `판정 → 판단 이유 → 조치` 순서와 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환하며 `issue_id`는 항상 `R1-M5-07`을 사용한다.
- 실제 PostgreSQL 18.4/RLS, MinIO, 암호화 Local DB, Runtime API/OpenAPI 7+3 Route, actual 화면·same-origin Network, 회귀와 Fixture Guard 증거가 없으면 `COMPLETED`로 보고하지 않는다.
