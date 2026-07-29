# R1-M5-01-C01 DB 장애 Liveness·Workspace Idempotency 보완 작업지시서

## 판정

`R1-M5-01`은 구현·Migration·RLS·서버 검증의 대부분을 충족했으나, 어울1 독립 검토에서 운영 가용성과 Workspace 격리 계약을 위반하는 중대 미진 2건이 확인되어 `VERIFYING → CORRECTION_REQUIRED`로 전환한다. 이 보완은 정식 `FAILURE_REPORT`가 아니며 누적 실패 횟수는 0회다.

## 판단 이유

1. `PostgresCloudStore.__init__()`가 Pool을 즉시 열고 `wait()`하므로 Production 시작 시 DB가 내려가 있으면 `build_dependencies()`가 실패한다. 따라서 서비스가 기동되지 않아 `/health/live` 200과 `/health/ready` 503을 분리할 수 없다. 작업지시서의 “Liveness는 DB 장애와 분리, Readiness는 DB 연결·Migration 상태 반영” 계약 위반이다.
2. `idempotency_records` 기본키가 `(tenant_id, actor_id, operation, idempotency_key)`라 `workspace_id`를 포함하지 않는다. 같은 Tenant·Actor가 서로 다른 Workspace에서 같은 Key를 정상 사용하면 RLS로 기존 Row는 보이지 않지만 INSERT는 Unique 충돌한다. Workspace 격리와 요청 Scope별 Idempotency 의미 위반이다.
3. 서버 실DB Test는 교차 Tenant만 검증해 같은 Tenant·다른 Workspace RLS와 위 Key 충돌을 포착하지 못했다.

## 조치 목표

- DB가 시작 시점에 없거나 일시 장애여도 API Process는 제한 시간 안에 기동하고 `/health/live`는 200, `/health/ready`는 안전한 503을 반환한다.
- 같은 Process에서 DB가 복구되고 Migration/Extension이 정상이 되면 재기동 없이 Readiness가 200으로 회복한다.
- Readiness DB 점검은 Async Request Event Loop를 장시간 동기 차단하지 않는다. bounded timeout과 안전 오류만 사용한다.
- Idempotency Scope를 Tenant·Workspace·Actor·Operation·Key로 분리하고 같은 Tenant·Actor·Key의 서로 다른 Workspace 요청이 각각 독립 성공한다.
- 같은 Tenant·다른 Workspace의 Row는 RLS로 상호 조회·수정할 수 없다.

## 구현 범위와 제약

- Branch와 Worktree는 기존 `codex/r1-m5-01`, `C:\tmp\Daon_User-r1-m4-06`을 계속 사용한다. 기준 HEAD는 `aec8fd442d6409be238c2b79b2ef9e06c3902a60`이다.
- 허용 경로: R1-M5-01 Cloud Store·Runtime·최초 Migration·직접 테스트·배포 검증·Evidence·Progress·보완 완료보고.
- 운영 배포 전 최초 Migration이므로 `0001` 교정은 허용하되, 이미 검증한 DB에 대한 Backup→재생성/Upgrade→Restore 경로와 Schema Drift 결과를 다시 입증한다.
- 공개 OpenAPI, 기존 M4 Auth·Authorization·Audit·Notification 의미, Browser same-origin, 다른 Milestone 범위를 변경하지 않는다.
- 새 Dependency를 추가하지 않는다. DB 실패 세부, DSN, Host, SQL, Stack, Secret 이름을 응답·Log·Evidence에 노출하지 않는다.
- `force_audit_failure` 같은 기존 Test seam은 이번 보완 범위를 불필요하게 확장하지 않되, Production 공개 경로에 노출되지 않음을 재확인한다.

## TDD·필수 검증

- RED 1: 사용 불가능한 격리 DB 주소로 Production Dependencies/App을 만들 때 현재 기동 실패를 재현한다.
- GREEN 1: DB 부재 상태에서 Process 기동, live 200, ready 503, 내부정보 노출 0. 이후 같은 Process에서 DB와 Schema가 준비되면 ready 200으로 회복한다.
- Event Loop: 느린/실패 Readiness 점검 중 별도 live 또는 경량 요청이 bounded 시간 안에 처리되는지 실제 동시 HTTP로 확인한다.
- RED 2: 같은 Tenant·Actor·Operation·Key를 서로 다른 Workspace에서 사용하면 현재 Unique/Constraint 충돌하는 상황을 실DB에서 재현한다.
- GREEN 2: 두 Workspace가 각각 독립 Idempotency 결과·Audit 1건을 가지며 서로의 Row 조회·재생·수정이 0건임을 검증한다.
- 같은 Tenant·다른 Workspace RLS 직접 조회/Write 차단과 Pool Context 제거를 독립 Connection/동시 요청으로 검증한다.
- 기존 Migration 재적용, 실DB 10/10, Notification 동일 Key/상이 Key 경쟁, Audit 실패 Rollback, API 전체·Runtime·Authorization·Audit·Notification·Web Build·Quality·독립성 회귀를 다시 실행한다.
- exact SHA를 ysna-server 전용 PostgreSQL `18.4`/pgvector `0.8.2` 격리 환경에서 검증한다. DB down→API live/ready 분리→DB up/Migration→동일 API Process ready 회복, Backup·Rollback/Restore, 공용 자원 불변과 전용 자원 잔여 0을 증명한다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-01-C01_progress.md`에 착수, RED, 구현, 로컬 검증, Commit·Push, 서버 장애/회복·Workspace 격리·복구 검증, 오류·복구와 종료 직전을 즉시 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-01-C01/`, 완료보고는 `docs/04_test_reports/release_1/R1-M5-01-C01_completion_report.md`에 작성한다.
- 결과는 `판정 → 판단 이유 → 조치`와 `COMPLETED | FAILURE_REPORT | INCOMPLETE` 계약으로 반환한다.
- 완료 전 Local HEAD·Origin Branch·서버 exact SHA를 일치시키고 Worktree Clean, 잔여 Process/Listener/Container/Network/Volume 0을 보고한다.
