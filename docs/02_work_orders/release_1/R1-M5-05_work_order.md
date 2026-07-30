# R1-M5-05 Sync·Copy/Publish·충돌 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M5-05`.
- 공식 작업공간: `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`.
- Branch `codex/r1-m5-05`, 기준 HEAD `4604bb7435ca50afccbf5aa71859fe32e189ad4d`, 시작 시 외부 Untracked 문서 2건만 존재한다.
- 승인 정본: `AGENTS.md`, 상세 설계 0.7의 §6.3·§14.4·§16·§17·§21.3·§27, Release 1 구현계획 1.0의 R1-M5-05, 테스트계획과 TS-SEC-020·021·023·041·042·044·084·084A, TS-OPS-021·022·023·025.
- 공개 API 계약 승인 ID: `APR-R1-M5-05-SYNC-API-20260730-01`, 결정 `R1-D025`.
- 선행 R1-M4-02의 Step-up, R1-M5-03의 암호화 Local-private 저장소, R1-M5-04의 데이터 정본·불변 Version·Transition/Audit를 재사용하고 우회하지 않는다.
- 어울2가 이 Branch와 범위의 유일한 코드 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.
- `D:\Project\Daon_User`와 `C:\tmp`의 Clone·Worktree는 읽기 전용 보존 자료이며 수정·삭제·작업 전환을 금지한다.
- 외부 Untracked `docs/04_test_reports/release_1/interim_review_2026-07-30.md`, `docs/04_test_reports/release_1_model_provider_queries.md`는 수정·삭제·Stage하지 않는다.

## 단일 목표와 완료 조건

- 목표: Local-private 자료를 원본 영역·Version을 보존한 채 명시 승인으로 Cloud-sync에 Copy/Publish하고, Offline Queue 재개와 Version 충돌을 자동 덮어쓰기 없이 처리하는 운영형 계약을 구현한다.
- 사용자는 대상·범위 Preview를 확인하고 Step-up을 포함한 승인을 한 뒤에만 전송할 수 있다. 무승인 직접 호출은 Object·Content 전송과 재색인 요청을 시작하지 않고 원본을 변경하지 않는다.
- 연결 복구 시 승인 Snapshot에 포함되고 현재 권한·정책·승인이 여전히 유효한 항목만 재개 전송한다. Version 충돌은 표시하고 사용자의 명시 선택 전까지 유지한다.
- 전송 완료는 원본·대상 Version, Transfer Batch, 승인 Snapshot, AuditEvent·Trace를 연결한다. M6 재색인은 `reindex_requested`까지만 기록하고 완료를 가장하지 않는다.

## 승인된 공개 API 계약

- `POST /api/v1/workspaces/{id}/sync-operations`: 대상 영역·전송 범위 Preview와 SyncOperation 생성.
- `GET /api/v1/sync-operations/{id}`: 현재 권한으로 상태·항목·Batch·충돌 조회.
- `POST /api/v1/sync-operations/{id}/approve`: 권한·정책·민감정보·Step-up 재검증 후 승인 Snapshot 고정.
- `POST /api/v1/sync-operations/{id}/transfer-batches`: 승인 항목만 Idempotent·재개 가능 Batch 전송.
- `POST /api/v1/sync-operations/{id}/conflicts/{conflict_id}/resolution`: `keep_local_as_new_version | keep_cloud | keep_both` 중 명시 선택.
- 모든 Write는 Idempotency Key, `If-Match`, Tenant·Workspace Scope, 현재 AccessDecision, 안전 오류, Trace·Audit를 적용한다.
- Browser 소비 경로가 필요하면 same-origin BFF만 사용한다. Browser에 API 절대주소·localhost·Container Host·Storage 경로·Credential을 노출하지 않는다.

## Cloud 데이터·상태 계약

- Migration `0004`로 SyncOperation, 승인된 Transfer Manifest/Item, Transfer Batch/Attempt, SyncConflict와 Resolution을 구현하거나 동등한 정규화 Schema를 제공한다. 모든 행은 Tenant·Workspace Scope, 복합 FK, 강제 RLS와 최소 권한을 갖는다.
- SyncOperation은 최소 `preview → awaiting_approval → approved → transferring → conflict | reindex_requested | failed | cancelled` 수명주기를 갖는다. 상태 전이는 허용 Matrix와 Optimistic Concurrency로 강제하고 거부 시 변경 0건과 Audit를 남긴다.
- 승인 Snapshot은 대상 영역, 승인된 Item/SourceVersion/Object Digest, 정책 버전, Actor, StepUpAuthorization, 승인 시각을 불변으로 고정한다. 승인 뒤 Scope 확대는 새 Operation과 새 승인을 요구한다.
- Batch는 Cursor·Sequence·Attempt·전송 Digest·결과를 Append-only로 기록하고 같은 Idempotency Key 재요청에 중복 전송 0건을 보장한다. 부분 성공 후 재개 시 완료 Item을 다시 전송하지 않는다.
- Conflict는 Local/Cloud Version·Digest·Base Version을 고정한다. 해결 선택은 새 대상 Version 또는 현 상태 유지로 표현하고 기존 Version을 UPDATE/DELETE하지 않는다.
- 원본 Local-private Object·Version·영역을 변경·삭제하지 않는다. Cloud 대상 Object는 기존 Object Queue/Storage 경계를 통해 서버가 생성하며 Client가 내부 주소나 Credential을 지정하지 않는다.

## Local-private Offline Queue 계약

- M5-03 SQLCipher 저장소 안에 Sync Operation Reference, 승인 상태, Manifest Digest, Batch Cursor, Conflict Reference를 암호화 저장한다. Payload·경로·키·Token을 평문 파일이나 Log에 남기지 않는다.
- Offline에서는 Preview Draft와 사용자 편집 Queue를 보존할 수 있으나 조직 최종 승인·외부 전달·Cloud 전송을 성공 처리하지 않는다.
- Reconnect는 명시 이벤트 또는 화면 동작으로 시작하고 현재 Session·Device·Membership·정책·Step-up/승인 유효성을 서버에서 다시 검사한다. 승인되지 않았거나 철회·만료·권한 축소된 항목은 전송하지 않고 안전 상태와 조치를 표시한다.
- 장치 Revoke 또는 Local Sync Key 폐기 시 Queue 접근·재개를 차단한다. 실제 장치 Pairing/Relay 구현은 R1-M6-04 범위이며 Mock 성공을 만들지 않는다.

## 충돌·안전 오류 계약

- Version/ETag 불일치는 `SYNC_VERSION_CONFLICT`, 승인 없음·만료는 `SYNC_APPROVAL_REQUIRED`, Step-up 없음·불일치는 `STEP_UP_REQUIRED`, 정책·현재 권한 차단은 기존 안전 Code 체계를 사용한다.
- Conflict 해소 전 자동 병합·자동 덮어쓰기·암묵적 Latest 승격은 0건이어야 한다.
- `keep_local_as_new_version`은 승인 Scope 안에서 Cloud에 새 Version을 추가하고, `keep_cloud`는 Local 원본을 변경하지 않은 채 Cloud 선택을 기록하며, `keep_both`는 두 불변 Version과 관계를 보존한다.
- 오류 응답·Log·Evidence에는 Secret, Token, 내부 DB/Object Host·Path, 원문 Content를 포함하지 않는다.

## 허용·제외 범위

- 허용: PostgreSQL `0004` Migration, Sync Domain/Repository/Service/API/OpenAPI, Local 암호화 Queue 연동, 서버 내부 Object/Outbox 연계, Unit·Integration·Migration·Failure Injection·Reconnect/Conflict Test, 진행·Evidence·완료보고.
- 제외: M5-06 삭제·Retention·Legal Hold 실행, M5-07 Backup/Restore 제품 기능, M6 실제 Source 처리·재색인 완료, M6-04 Pairing/Relay, M7 Production Client 전체 UI, 운영 OCI 배포, 실제 외부 Provider 전송.
- 승인된 5개 공개 API 이외의 공개 경로·Request/Response 의미, 데이터 보안 경계나 기존 동작 변경이 필요하면 구현 전에 `BLOCKED`로 어울1에게 증거를 반환한다.
- 관련 없는 리팩터링·의존성·설정 변경과 임시 Mock 성공을 금지한다.

## TDD·필수 검증

- RED부터 기록한다: 무승인 전송, 잘못된 Step-up, Scope 확대, 교차 Tenant/Workspace, 중복 Batch, Lost Update, Offline 미승인 재개, Version 충돌 자동 덮어쓰기 테스트가 기준선에서 실패해야 한다.
- Migration: PostgreSQL 18.4 빈 DB `0001→0002→0003→0004`, 재적용, `0004→0003→0004`, Backup→적용→Rollback→Restore를 전용 Fixture에서 검증한다.
- API/OpenAPI: 승인된 5개 경로, Schema, Auth, Step-up, Idempotency, `If-Match`, 안전 오류와 실제 Runtime 등록을 검증한다.
- RLS/무결성: 실제 `daon_app` Session으로 Tenant/Workspace 격리, 승인 Snapshot 불변, 교차 Scope FK 거부, Append-only Batch/Resolution, 중복 전송 0건을 실제 DB에서 증명한다.
- Copy/Publish: §6.3의 5단계, 원본 영역·Version 변경 0건, 대상 새 Version·Object·Audit/Trace 연결, 재색인은 `reindex_requested`까지만 검증한다.
- Offline/Reconnect: 암호화 Queue Restart 복구, 평문 Canary 0건, Offline 외부 Network 0건, 승인 항목만 재개, 부분 Batch 재개, 승인 만료·철회·권한 축소·장치 Revoke 차단을 검증한다.
- Conflict: 두 Client의 Base/ETag 불일치에서 Conflict 생성·표시, 세 Resolution 각각의 Version 결과, 선택 전 자동 병합·덮어쓰기 0건을 검증한다.
- 회귀: M4 Step-up/Auth/Audit, M5-01 Cloud Storage, M5-02 Queue/Worker, M5-03 Local Storage, M5-04 Canon/Transition, API·Local·OpenAPI·Quality Gate·독립성 검사를 실행한다.
- ysna-server는 `/home/ubuntu/deploy/daon-user` 아래 새 격리 Checkout·Compose Project·Network·Volume·PostgreSQL 18.4만 사용한다. `shared-db`, `common`, `netdata`, `proxy`를 사용·변경하지 않고 Commit SHA, Migration, Service Health와 실제 DB/API 증거를 남긴다.
- 서버의 격리 자원 정리는 정확한 대상과 보호 범위를 어울1에게 보고하고 신산님의 별도 승인 후에만 수행한다.
- 화면을 사용하면 종료 즉시 App·Simulator·Browser를 모두 닫는다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-05_progress.md`에 착수, 영향 분석, RED, Migration/Cloud, Local Queue, API, Copy/Publish, Reconnect, Conflict, 로컬 검증, Commit·Push, 서버 배포·Migration, 오류·복구와 종료 직전을 시각·상태·변경 파일·명령/결과·다음 작업과 함께 즉시 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-05/`, 완료보고는 `docs/04_test_reports/release_1/R1-M5-05_completion_report.md`에 작성한다.
- Evidence Manifest는 exact Commit SHA, Migration Revision, OpenAPI/API, RLS/승인 Snapshot, Step-up, Batch Idempotency/Resume, Local Encryption/Restart, Reconnect, Conflict Resolution, 원본 불변, 무승인 Network 0건, Audit/Trace, 보호 자원 Before/After를 연결한다.
- 결과는 `판정 → 판단 이유 → 조치`와 `COMPLETED | FAILURE_REPORT | INCOMPLETE | BLOCKED` 계약으로 반환한다. 실제 PostgreSQL 18.4와 암호화 Local DB, Runtime API 증거가 없으면 `COMPLETED`로 보고하지 않는다.
- 단일 구현 Commit과 Evidence-only Commit을 구분하고, 종료 전 Local HEAD·Origin Branch·검증 exact SHA, Working Tree 상태, 잔여 Process·Listener·App 0, 서버 격리 자원 상태와 정식 실패보고 횟수를 보고한다.
