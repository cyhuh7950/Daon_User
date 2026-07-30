# R1-M5-04 데이터 정본·계보 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M5-04`.
- 공식 작업공간: `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`.
- Branch `codex/r1-m5-04`, 기준 HEAD `242b8263c767fa2ad3f6f33e1f43c4d78bcc4dab`, 시작 Clean.
- 승인 정본: `AGENTS.md`, 상세 설계 0.7의 §16·§17·§18, Release 1 구현계획 0.9 §6·§15의 R1-M5-04, 테스트계획 0.7의 §8·§9.4와 관련 시나리오.
- 선행 `R1-M5-01`의 PostgreSQL 18.4·강제 RLS·Service Authorization, `R1-M5-02`의 Object/Queue/Worker, `R1-M5-03`의 Local-private 암호화 저장 계약을 재사용하고 우회하지 않는다.
- 어울2가 이 Branch와 범위의 유일한 코드 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.
- `D:\Project\Daon_User`와 `C:\tmp`의 Clone·Worktree는 읽기 전용 보존 자료이며 수정·삭제·작업 전환을 금지한다.

## 단일 목표와 완료 조건

- 목표: 상세 설계 §16의 데이터 정본을 실제 저장 계층의 Entity·FK·Version·상태 전이·불변 Snapshot·계보 계약으로 구현해 M5-05와 M6 이후 기능이 동일한 정본을 사용하게 한다.
- Cloud-sync 정본은 PostgreSQL Migration으로 구현하고, Local-private는 M5-03 암호화 저장소 안에서 같은 Canonical ID·Version·Snapshot Envelope를 보존하는 최소 Projection 계약을 구현한다. 두 영역의 저장 위치·Key·전송 허용은 분리하며 Sync·Copy/Publish는 구현하지 않는다.
- Migration 적용 후 SourceVersion→ProcessingRun→Understanding/Evidence/Index, Routing/ModelAttempt→RunSnapshot/RunResult/Citation, GenerationSettings→OutputVersion→Review/Approval/Delivery/KnowledgeRegistration 계보를 FK로 추적할 수 있어야 한다.
- 저장된 Version·Snapshot·Attempt·Evidence·Approval 판정·등록 지식은 제자리 수정하지 않는다. 변경은 새 Version 또는 새 Run/Request/Decision을 추가하여 표현한다.

## 정본 Entity 범위

- 기존 M5-01 Entity인 Tenant·Workspace·User·Membership·Device·Session·AuditEvent와 M5-02의 ObjectRecord·Outbox·Job을 중복 생성하지 않고 FK로 연결한다.
- 이번 Migration은 상세 설계 §16의 다음 군을 빠짐없이 매핑한 `data_canon_manifest` 문서와 실제 Schema를 제공한다.
  - Workspace 정책: WorkspacePolicy, StepUpAuthorization, AccessDecision.
  - Source·근거: Source, SourceVersion, ProcessingRun, UnderstandingResult, ExtractionEvidence, TranscriptionRun, TranscriptVersion, TranscriptSegment, EvidenceSpan, IndexVersion.
  - 지식·권위: KnowledgeScope, WeightProfile, ScopeSnapshot, ConflictRecord.
  - RuleSet: RuleSetReference, RuleSetVersionSnapshot, RuleSetBinding, RuleEvaluation.
  - Model·Routing: ProviderProfile, RuntimeNode, ModelArtifact, ModelInstallation, ModelDeployment, RoleBinding, RoutingPolicyVersion, RoutingDecision, ModelAttempt.
  - 대화·실행: Conversation, Message, Run, RunStep, RunSnapshot, RunResult, Citation.
  - Studio: GenerationRequest, GenerationSettingsSnapshot, StudioOutput, OutputVersion, EvidenceReference.
  - 검토·전달: ReviewRequest, ApprovalRequest, Approval, Delivery, KnowledgeRegistration.
  - 운영 참조: Connector, ExternalReference, EgressDecision. Notification과 AuditEvent는 기존 정본을 재사용한다.
- Entity 이름과 Table 수는 PostgreSQL 관례에 맞게 정규화하거나 안전한 공통 Ledger로 통합할 수 있으나, Manifest에서 각 설계 Entity가 어느 Table·Column·Constraint로 구현됐는지 1:1로 추적되어야 한다. 단일 무제약 JSON 문서나 범용 EAV만으로 대체하지 않는다.

## ID·격리·관계 계약

- 모든 신규 Cloud 행은 `tenant_id`, Workspace 범위 Entity는 `workspace_id`를 포함하고 기존 복합 Key에 연결한다. 교차 Tenant·Workspace FK를 구조적으로 만들 수 없어야 한다.
- 외부 제공 ID·파일명·URL을 PK나 저장 경로로 사용하지 않고 불투명 ID와 명시적 ExternalReference를 사용한다.
- 모든 신규 Tenant/Workspace Table은 `ENABLE ROW LEVEL SECURITY`와 `FORCE ROW LEVEL SECURITY`, 기존 `daon_app` 최소 권한을 적용한다. 소유자 우회가 아닌 실제 `daon_app` Session으로 0-row/거부를 검증한다.
- Object 원본·산출물은 M5-02 `object_records`의 Digest·Version 참조로 연결하고 Blob, Prompt 원문, Model Secret, Provider Credential을 정본 Table에 중복 저장하지 않는다.
- 삭제·보존·Legal Hold의 실행은 M5-06 범위다. 이번 작업은 `disabled/deleting/deleted` 등 승인 상태와 참조 무결성을 정의하되 실제 Cascade 삭제 API나 파괴적 Cleanup을 공개하지 않는다.

## Version·Snapshot·계보 불변 계약

- SourceVersion, TranscriptVersion, IndexVersion, RuleSetVersionSnapshot, RoutingPolicyVersion, RunSnapshot, GenerationSettingsSnapshot, OutputVersion은 생성 후 `UPDATE`·`DELETE`를 DB에서 거부한다. AuditEvent와 ModelAttempt 등 Append-only Ledger도 동일하다.
- 불변 Row는 `schema_version`, 생성 시각, 원본/이전 Version 참조, 내용 또는 Canonical JSON의 SHA-256 Digest를 갖는다. JSON Snapshot은 Object만 허용하며 필수 Field의 Null/누락을 검사한다.
- RunSnapshot은 상세 설계 §16.1의 지식 범위·Source Version·권위·가중치 requested/effective/clamp·RuleSet Snapshot·Routing Policy/후보 순서·Data Area/분류/Egress·사용자/조직 정책·비용/통화·Prompt/Tool Version과 적용 시 GenerationSettingsSnapshot 참조를 고정한다.
- ModelAttempt는 Append-only이며 후보 순서, Deployment/Artifact Digest, 시작/종료, 결과·안전 오류·비용/사용량을 기록한다. 최종 선택과 Fallback 결과는 RoutingDecision·RunResult에 연결한다.
- Citation/EvidenceReference는 당시 SourceVersion과 Page·Cell·Region·Time Range 등 위치 식별자를 고정하고, 최신 Source로 자동 이동하지 않는다.
- 생산 지식 등록은 승인된 특정 OutputVersion을 새 불변 SourceVersion으로 명시 등록하는 관계만 정의하며 자동 등록·Daon 승인 지식 자동 승격 경로를 만들지 않는다.
- DB 불변 Trigger의 오류 코드는 안정적으로 식별 가능해야 하며, Update 우회·직접 SQL·잘못된 Previous Version·Digest 불일치를 실패시킨다.

## 상태 전이 계약

- 상태 값만 CHECK하는 것으로 끝내지 않고, 허용 전이 Matrix와 Transition Ledger 또는 동등한 DB 강제 장치를 구현한다. 전이마다 actor, source state, target state, reason/safe code, trace, policy version, occurred_at을 남긴다.
- Source: `registered → security_check → processing → indexing → ready`; 분기 `waiting_model | partial_understanding | needs_review | failed | expired | disabled | deleting | deleted`.
- ProcessingRun은 Modality별 하위 단계를 구분한다. 문서는 Vision/LLM-first 이해→Parser/OCR 검증→근거 조정, 오디오는 audio understanding 또는 speech-to-text→LLM 의미 이해→Transcript 검토/근거 조정으로 기록한다. 실패 Run은 불변이며 재처리는 `retry_of`, `trigger_type`, Readiness Event 또는 수동 요청을 가진 새 Run이다.
- Run: `accepted → planning → retrieving → generating → validating → completed`; 분기 `waiting_user | waiting_approval | policy_blocked | failed | cancelled`.
- GenerationRequest: `configuring → confirmed → submitted`; 제출 후 Snapshot은 불변이다.
- OutputVersion: `generating → draft → review_requested → in_review → revision_requested` 또는 `approved → delivered`. 승인 후 변경은 새 OutputVersion과 재승인을 요구한다.
- ApprovalRequest: `pending → approved | rejected | expired | withdrawn`; KnowledgeRegistration: `requested → registered | rejected`.
- 정의되지 않은 전이, Terminal 상태 역행, 다른 Tenant/Workspace 대상 전이, Concurrent Lost Update는 거부하고 Audit 또는 Transition Ledger에 안전하게 남긴다.

## Local-private Projection 계약

- M5-03의 암호화 SQLite/File/Vector Port를 확장하되 Key·Cipher·Loopback·Workspace/Area 격리 계약을 변경하지 않는다.
- Local에는 Offline 동작에 필요한 Source/Version, Processing/Run/Snapshot/Result, Evidence/Citation, Output/Version과 Pending Operation Reference의 Canonical Envelope만 보존한다. Cloud 전용 조직·승인·Provider Secret을 복제하지 않는다.
- Envelope는 Cloud와 동일한 ID·Version·schema_version·digest·created_at·previous_version_id 의미를 갖고 불변 저장하며, 영역 이동 상태는 `local_private`로 고정한다.
- 이번 작업에서 자동 Upload·Sync·Merge·Conflict Resolution·Cloud Fallback을 만들지 않는다. M5-05가 승인 전송과 Version 비교를 구현한다.

## API·서비스 경계

- 이번 작업은 Migration, Repository/Domain Contract, 상태 전이 Service와 내부 검증 API까지 허용한다. 완성되지 않은 Source 처리, LLM Routing, Studio 생성 기능을 가장한 Mock 성공 API는 만들지 않는다.
- Write는 권한·소유권·Idempotency·Optimistic Concurrency를 검사하고 모든 실제 상태 변경을 기존 Audit 정본과 Trace로 연결한다.
- 과거 Result·Citation·Output Read는 현재 Membership/ACL을 재검증할 수 있는 AccessDecision 참조를 보존한다. 실제 전체 Read/Mask UI는 후속 기능 범위이나 우회 가능한 직접 Object 경로는 만들지 않는다.
- Browser 코드는 same-origin을 유지하며 내부 DB/Object/Container 주소, `localhost`, Secret을 노출하지 않는다.

## 허용·제외 범위

- 허용: PostgreSQL `0003` Migration, RLS/Grant/FK/Constraint/Trigger/Index, Canon manifest, Domain/Repository/Transition Contract, Local 암호화 Projection, Unit·Integration·Migration·Failure Injection Test, Progress·Evidence·완료보고.
- 제외: M5-05 Sync/Copy/Publish, M5-06 삭제 실행/Retention/Legal Hold, M5-07 Backup/Restore, 실제 Source 처리·ASR·Embedding·LLM 호출, Retrieval·Rule 평가, Studio 파일 생성, UI 기능 확장, 운영 Oracle 배포.
- 기존 Auth·Authorization·Audit·Notification·Object/Queue·Local Encryption·Tauri Lifecycle과 공개 API를 암묵적으로 변경하지 않는다. 필요한 구조 변경이 승인 계약과 충돌하면 구현 전 `BLOCKED`로 보고한다.

## TDD·필수 검증

- RED: 누락 Entity Mapping, 교차 Scope FK, 불법 상태 전이, Snapshot Update/Delete, 잘못된 Digest/Previous Version, Concurrent Lost Update가 기존 기준선에서 실패함을 먼저 기록한다.
- Migration: PostgreSQL 18.4 빈 DB `0001→0002→0003`, 재적용, `0003→0002→0003`, Backup→적용→Rollback→Restore를 전용 Fixture로 검증한다. 기존 M5-01/02 데이터와 Migration Head를 보존한다.
- Schema: Manifest의 모든 설계 Entity가 실제 Table/Column/Constraint에 매핑되고 Orphan FK 0, cross-tenant/workspace link 0, 필요한 Index와 Unique/Idempotency 제약이 동작해야 한다.
- Immutability: Version/Snapshot/Ledger의 Update·Delete 0건, 새 Version append 성공, Previous Version chain과 SHA-256 Digest 일치, 당시 Citation 재현을 실제 DB로 검증한다.
- Transition: 각 허용 전이 1건 이상, 모든 금지 전이·Terminal 역행·Concurrent version 충돌을 검증한다. 문서/오디오 Processing 분기와 retry_of/trigger 기록을 포함한다.
- RLS: 실제 `daon_app`로 Tenant A/Workspace A, Tenant A/Workspace B, Tenant B 조합을 조회·삽입·갱신·전이하고 교차 결과 0건 또는 거부를 증명한다.
- Local: 암호화 DB Restart 후 Canonical Envelope와 Snapshot 재현, Update/Delete 거부, 다른 Workspace/Area 조회 0건, 평문 Canary 0건, 외부 Network 0건을 검증한다.
- 회귀: API Unit/Integration, Migration, M5-01 Cloud Storage, M5-02 Queue/Worker, M5-03 Local Storage, Web/API Build·Typecheck·Quality Gate·독립성 검사를 실행한다.
- ysna-server는 `/home/ubuntu/deploy/daon-user` 아래 새 격리 Checkout·Compose Project·Network·Volume·PostgreSQL 18.4만 사용한다. `shared-db`, `common`, `netdata`, `proxy`를 사용·변경하지 않고 Commit SHA, Migration 사전점검·Backup·적용·Rollback·Restore, Service Health, 실제 DB 증거를 남긴다.
- 화면을 사용하면 검증 종료 즉시 App·Simulator·Browser를 모두 닫아 신산님의 화면을 점유하지 않는다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-04_progress.md`에 착수, 영향·Entity Mapping, RED, Migration/Cloud, Local Projection, 상태 전이, 로컬 검증, Commit·Push, 서버 배포·Migration, 오류·복구와 종료 직전을 시각·상태·변경 파일·명령/결과·다음 작업과 함께 즉시 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-04/`, 완료보고는 `docs/04_test_reports/release_1/R1-M5-04_completion_report.md`에 작성한다.
- Evidence Manifest에는 exact Commit SHA, Migration Revision, Schema/Entity Mapping, FK/RLS/불변/상태 전이/Local Projection Test, Backup/Rollback/Restore, Service Health, 보호 자원 Before/After를 연결한다.
- 결과는 `판정 → 판단 이유 → 조치`와 `COMPLETED | FAILURE_REPORT | INCOMPLETE | BLOCKED` 계약으로 반환한다. 실제 PostgreSQL 18.4와 암호화 Local DB 증거가 없으면 `COMPLETED`로 보고하지 않는다.
- 단일 구현 Commit과 Evidence-only Commit을 구분하고, 종료 전 Local HEAD·Origin Branch·검증 exact SHA, Working Tree Clean, 잔여 Process·Listener·App 0, 서버 격리 자원 상태와 정식 실패보고 횟수를 보고한다.
