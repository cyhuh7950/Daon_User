# R1-M5-02 Object·Queue·Worker 저장 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M5-02`.
- Branch `codex/r1-m5-02`, 기준 HEAD `c3db55ff9caea282441853c7fbe7c4b873f54bb7`, 시작 Clean.
- 승인 정본: `AGENTS.md`, 상세 설계서 §16·§20·§22·§24, Release 1 작업계획 §15의 R1-M5-02, 테스트계획 M5·M6 항목.
- 선행 `R1-M4-05`, `R1-M5-01`이 Release 기준선에 병합됐다. PostgreSQL 18.4·pgvector·RLS·Transaction·Readiness 계약을 재사용하고 우회하지 않는다.
- 어울2가 이 Worktree와 범위의 유일한 코드 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.

## 단일 목표와 사용자 완료 조건

- 목표: 원본·산출물 Binary를 S3-compatible Object Storage에 안전하게 보존하고, DB Transaction과 결속된 Durable Queue·Worker가 비동기 저장 작업을 유실·중복 성공 없이 처리하며 실패·재처리 상태를 API/운영 계층에서 확인할 수 있는 기반을 완성한다.
- 사용자나 운영자는 Python·DB·Object CLI를 직접 실행하지 않는다. 서비스/API 계층이 작업 상태·오류 분류·재처리 결과를 제공하고 후속 운영 UI가 이 계약을 사용한다.
- 원본과 산출물은 Tenant·Workspace·영역이 분리된 불투명 Object Key, SHA-256 Digest, Byte Size, 검증 MIME, Version/ETag, 생성 Actor·Trace·Audit와 연결된다.
- Domain Write가 성공했는데 Queue가 사라지거나, Queue는 완료됐는데 Object/DB 상태가 불일치하거나, Retry로 Object·Audit가 중복 생성되는 경우가 없어야 한다.

## Object Storage 계약

- S3-compatible Adapter는 Port와 분리하며 환경별 전용 Bucket·Credential·Endpoint를 Server에서만 사용한다. Browser·Native Bundle과 공개 응답에 내부 Endpoint·Bucket·Credential·실제 Prefix를 노출하지 않는다.
- Object Key는 Server가 불투명 ID로 생성하고 Tenant·Workspace·영역 Prefix Policy를 적용한다. Client가 보낸 경로·파일명·Prefix를 Key로 직접 사용하지 않는다.
- `..`, 절대경로, 역슬래시, URL/Scheme, Control 문자, Unicode 혼동과 Prefix 탈출을 fail-close한다. 같은 Tenant의 다른 Workspace와 다른 Tenant Object는 IAM/Policy와 Service Authorization 양쪽에서 조회·쓰기·목록화가 0건이어야 한다.
- 저장 전후 SHA-256·Byte Size를 비교하고, 저장 Metadata의 Digest/Size/Content Type과 PostgreSQL Object Record를 일치시킨다. Client MIME만 신뢰하지 않으며 실제 형식 검사는 R1-M6-05가 승계한다.
- Put은 Idempotency/Deduplication 계약을 가지며 동일 Digest 재시도는 정책에 따라 같은 Object Version을 재사용하거나 새 Version으로 명시 기록한다. 부분 Upload·Connection 중단·Checksum 불일치는 성공으로 표시하지 않는다.
- Get은 현재 AccessDecision과 RLS를 다시 통과한 뒤 Server-side Stream 또는 짧은 수명의 Scope 제한 URL을 반환한다. 이번 작업에서 Browser 직접 내부 Endpoint 호출과 무제한 Public URL을 만들지 않는다.
- Delete·Retention·Legal Hold의 정식 동작은 R1-M5-06 소유다. 이번 단계는 Object 상태와 후속 정리 가능 Reference만 남기고 임의 삭제를 제공하지 않는다.

## Durable Queue·Outbox·Worker 계약

- Domain Transaction 안에서 Object 등록 의도와 Outbox/Job Row를 함께 Commit한다. DB Commit 후 별도 메모리 Queue에만 의존하거나 dual-write로 Object와 DB를 각각 성공 처리하지 않는다.
- 최소 상태는 `pending | leased | retry_wait | completed | dead_letter`이며 불변 Job ID, Tenant·Workspace, Job Kind, Payload Reference, Deduplication Key, Attempt, `next_attempt_at`, Lease Owner/Until, Last Safe Error Code, Trace·Audit, 생성/완료 시각과 Version을 가진다.
- Payload에는 Secret·Token·원문 개인정보·대용량 Binary를 넣지 않고 검증된 Object/Domain Reference만 저장한다. Job Kind와 Payload Schema는 명시 Allowlist·Version으로 검증한다.
- Worker Claim은 PostgreSQL Transaction과 `FOR UPDATE SKIP LOCKED` 또는 동등한 원자적 Lease로 단일 소유한다. Lease 만료 Worker는 작업을 계속 Commit할 수 없고 새 Worker가 안전하게 회수한다.
- Ack는 Object 검증과 DB 상태·Audit가 모두 성공한 뒤 수행한다. Worker Crash가 Put 전·중·후·DB Commit 전후 어느 지점에 발생해도 재실행이 유실 없이 멱등이어야 한다.
- Retry는 오류별 Retryable 분류, 제한된 횟수, bounded exponential backoff+jitter와 최대 지연을 사용한다. 무제한 Retry·Busy Loop·동일 작업 동시 실행을 금지한다.
- 최대 시도 소진은 `dead_letter`로 전환하고 안전 오류·다음 조치·마지막 시각을 남긴다. 재처리는 권한 있는 운영 Service가 새 Job/Attempt로 명시 수행하며 기존 실패 이력·Audit를 덮어쓰지 않는다.
- Worker Shutdown은 새 Claim을 중단하고 진행 중 Lease를 제한 시간 내 완료하거나 안전하게 반환한다. 종료 후 Orphan Process·Lease·Listener가 남지 않는다.

## 데이터·보안·운영 경계

- R1-M5-01의 Transaction-local Tenant·Workspace Context, `FORCE RLS`, Application Role 최소권한을 Object Record·Outbox·Job·Attempt Table에도 적용한다.
- Worker는 Superuser·Table Owner·`BYPASSRLS`를 사용하지 않는다. 대상 Job의 Server 검증 Scope만 Transaction-local로 설정한다.
- Object Credential은 Secret Reference로만 주입한다. 오류·Audit·Evidence에는 내부 Endpoint, Bucket, DB/Object Credential, Stack, Provider 원문을 기록하지 않는다.
- Liveness는 Object/Queue 장애와 분리하고 Readiness/Dependency 상태는 DB·Object·Worker를 각각 구분해 안전 코드로 표시한다. 일시 Object 장애 중 API Process가 불필요하게 종료되지 않아야 한다.
- 운영 상태는 최소한 Service/API 내부 계약으로 조회 가능해야 한다. 공개 Admin API나 화면 추가가 필요하면 기존 승인 계약 여부를 확인하고 임의 공개하지 말고 어울1에게 반환한다.
- Queue 깊이·가장 오래된 대기 시간·Retry/Dead-letter 수·Worker Lease·Object 실패율을 구조화된 안전 지표로 제공한다. 전체 운영 화면·알람 연결은 M9가 승계한다.

## 허용·제외 범위

- 허용: Object/Queue/Worker Port와 Adapter, PostgreSQL Migration, Transactional Outbox, Worker Runtime/Graceful Shutdown, Dependency Health/Metric 내부 계약, 격리 S3-compatible 개발 자원, 직접 Unit·Integration·Failure Injection·Server 검증, Architecture·Evidence·Progress·완료보고.
- 제외: Source MIME/악성/압축폭탄 검사(R1-M6-05), 전체 Source/Run/Studio 정본(R1-M5-04), Sync(R1-M5-05), Delete/Retention/Legal Hold(R1-M5-06), Backup 정식화(R1-M5-07), 공개 Upload UI/API 확장, 운영 Oracle 배포, 공용 Server 자원 변경.
- 신규 인프라·Python/Container 의존성은 Python 3.14.3·ARM64·License·취약점·공식 유지 상태와 대안을 조사해 고정 Version/Digest 근거를 남긴다. 중요 보안·데이터 경계를 바꿔야 하면 구현 전 어울1에게 반환한다.

## TDD·필수 검증

- RED: Object Adapter·Outbox/Job Schema·Worker 부재, DB Commit/Queue 유실, Prefix 탈출, Tenant/Workspace 교차 Object, Put 중단, 동일 Job 동시 Claim, Lease 만료, Retry 소진·재처리, Shutdown 중 Claim의 기존 실패를 먼저 증명한다.
- Object: 정상 Put/Get, Digest·Size·Metadata 일치, 동일 요청 Replay, Checksum/중단 실패, 다른 Tenant/Workspace 0건, Key 공격 Matrix, 내부 Endpoint/Secret 노출 0건을 검증한다.
- Queue: Domain+Outbox 원자성, 동시 Worker 단일 Claim, Crash 지점별 재실행, Lease 만료 회수, Ack 전후 멱등, Backoff 상·하한, Dead-letter, 권한 있는 새 재처리와 이력 보존을 실DB에서 검증한다.
- 장애: Object down 상태에서 API live 유지·dependency not-ready/축소 상태, Object 복구 후 같은 Worker/Process 처리 재개, DB down/up 회복, Worker Graceful Shutdown과 Orphan 0을 실제 Process로 검증한다.
- 회귀: R1-M5-01/C01 Cloud·Migration·RLS·Readiness, M4 Auth·Authorization·Audit·Notification, API/OpenAPI/BFF, Web Build, Quality·Independence를 실행한다.
- 로컬 기본 검증 후 Commit·Push하고 `/home/ubuntu/deploy/daon-user` 아래 exact SHA의 격리 Compose Project·Network·Volume·Bucket·PostgreSQL 18.4에서 Migration 사전점검·Backup·적용·재적용·Rollback/Restore와 실제 Object·Queue·Worker 장애/복구를 검증한다.
- 기존 `shared-db`, `common`, `netdata`, `proxy`와 다른 Bucket/Volume을 사용·변경하지 않는다. 종료 전 이 작업 소유 Checkout·Process·Listener·Container·Network·Volume·Bucket/Test Object를 정리하고 공용 자원 Snapshot 불변을 확인한다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-02_progress.md`에 착수, 영향·의존성 조사, RED, Schema/Object/Queue/Worker 구현, 로컬 검증, Commit·Push, 서버 Migration·Object·Queue·장애/복구·정리, 오류·복구, 종료 직전을 즉시 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-02/`, 완료보고는 `docs/04_test_reports/release_1/R1-M5-02_completion_report.md`에 작성한다.
- 결과는 `판정 → 판단 이유 → 조치`와 `COMPLETED | FAILURE_REPORT | INCOMPLETE` 계약으로 반환한다. 기본 테스트와 실제 검증이 없으면 `COMPLETED`로 보고하지 않는다.
- 단일 구현 Commit과 Evidence-only Commit을 구분하고, 종료 전 Local HEAD·Origin Branch·서버 exact binding, Worktree Clean, 잔여 작업 자원 0, 정식 실패보고 횟수를 보고한다.
