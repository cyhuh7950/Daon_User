# R1-M5-01 Cloud 정본·격리 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M5-01`.
- Branch `codex/r1-m5-01`, 기준 HEAD `ee71d83fea6aef2e53d23cae2f8863e0741cd659`, 시작 Clean.
- 승인 정본: `AGENTS.md`, 상세 설계서 §16·§20·§22·§24, Release 1 작업계획 §15의 R1-M5-01, 테스트계획 M4·M5 항목, `verification_M4.md`.
- 선행조건 `R1-M4-04`, `R1-M4-05`와 M4 Exit PASS가 충족됐다. M4 Identity·Authorization·Audit·Notification 공개 의미를 보존한다.
- 어울2가 이 Worktree와 범위의 유일한 코드 Writer다. 설계·PR·CI·Merge·완료 판정은 어울1 소유다.

## 단일 목표와 사용자 완료 조건

- 목표: PostgreSQL `18.4` 배포 기준과 PostgreSQL `15~18` 호환 preflight를 갖춘 pgvector Cloud 정본을 도입하고, 반복 가능한 Migration·Transaction·RLS·Service Authorization 이중 격리 기반을 완성한다.
- 사용자는 별도 DB 명령을 실행하지 않는다. 서비스가 Migration 상태와 Health를 API/운영 경계에서 확인할 수 있어야 한다.
- 같은 Tenant·Workspace의 권한 있는 요청만 정본을 읽고 변경하며, 다른 Tenant·Workspace·권한 없는 Service Role의 직접·우회 접근은 DB와 Service 양쪽에서 차단돼야 한다.
- Migration은 깨끗한 DB 적용, 재적용, 전진 호환 점검, 승인된 Rollback/복원 경로가 재현 가능해야 한다.

## Cloud 정본·Migration 계약

- 배포 기준은 PostgreSQL `18.4` 전용 개발 DB와 pgvector Extension이다. `cloud_admin preflight`의 호환 판정은 PostgreSQL major `15~18`을 허용하되 실제 서버 버전을 결과에 기록한다. 기존 `shared-db`, `/home/ubuntu/deploy/common`, `netdata`, `proxy` 또는 다른 프로젝트 DB·Volume을 사용하거나 변경하지 않는다.
- Repository Port와 PostgreSQL Adapter를 분리해 M4 Reference Adapter의 외부 의미를 보존한다. Test 전용 In-memory Adapter를 운영 기본값이나 자동 Fallback으로 사용하지 않는다.
- Migration Framework와 DB Driver는 Python 3.14.3·ARM64 호환의 고정 버전을 사용한다. 새 의존성은 선택 이유·대안·Lock Diff·취약점/License 점검 근거를 남긴다.
- 최초 Migration은 Tenant·Workspace·Membership/Role·Session/Device·Authorization Policy/Binding·Audit·Idempotency·Notification/Inbox 기반 Entity 중 M4에서 실제 영속 계약이 존재하는 최소 정본을 포함한다. M5-04가 소유하는 Source/Run/RuleSet/Model/Studio 전체 정본을 선점하지 않는다.
- 모든 Tenant/Workspace 소속 Row는 불변 식별자와 Tenant/Workspace Key를 가진다. FK·Unique·Check·Version 제약으로 고아 Row, 교차 Tenant FK, 잘못된 상태와 Version 감소를 차단한다.
- pgvector는 Extension 활성·Version 확인과 최소 Vector 저장/조회 Contract까지만 제공한다. Retrieval Schema와 Index 전략은 M6을 선점하지 않는다.
- Migration 적용 전 사전점검, 대상 DB 식별, Backup, 적용, 검증, 실패 시 Rollback/격리 Restore 절차를 자동화 가능한 명령으로 제공한다. 운영 대상 적용은 금지한다.

## RLS·Service Authorization·Transaction 계약

- RLS는 Tenant와 Workspace 경계를 fail-close하며 대상 Table에 `ENABLE ROW LEVEL SECURITY`와 필요한 경우 `FORCE ROW LEVEL SECURITY`를 적용한다. Application Role은 Superuser·Table Owner·`BYPASSRLS`를 사용하지 않는다.
- 요청별 DB Transaction 시작 후 Server가 검증한 Tenant·Workspace·Actor·Capability Context만 Transaction-local 설정으로 주입한다. Client가 보낸 Role·Grant·Tenant·Workspace를 직접 신뢰하지 않는다.
- Service Authorization의 현재 AccessDecision을 먼저 통과한 요청만 Repository에 도달한다. RLS는 두 번째 독립 방어선이며 둘 중 하나라도 거부하면 fail-close한다.
- Connection Pool 재사용 시 이전 요청 Context가 다음 요청으로 누출되지 않아야 한다. Transaction 종료·Rollback·취소·Timeout 후 Context 잔존 0건을 경쟁 테스트로 증명한다.
- Identity·Authorization·Audit·Notification의 하나의 업무 Write와 Idempotency 결과·Audit 기록은 단일 DB Transaction에서 모두 성공하거나 모두 Rollback한다.
- Notification 읽음 경쟁은 동일 Key 재요청이 같은 결과·Audit 1건, 서로 다른 Key와 같은 ETag는 성공 1건·나머지 412가 되어야 한다. Audit 실패 시 상태·Idempotency 결과도 남지 않아야 한다.
- Append-only Audit은 일반 Application Role의 UPDATE/DELETE를 차단한다. 정식 보존·Partition·서명 확장은 후속 범위이나 기존 Hash Chain 의미는 보존한다.
- Idempotency Record는 Tenant·Actor·Route/Operation·Key·Request Fingerprint·결과/상태·만료 정보를 분리하며 다른 Tenant나 Operation에서 Key가 충돌하지 않는다.

## Health·오류·운영 경계

- Liveness는 DB 장애와 분리하고 Readiness는 Migration 상태·DB 연결·필수 Extension/Schema 불일치를 반영한다. 내부 Host·DB명·사용자·SQL·Stack·Secret 이름을 응답이나 Log에 노출하지 않는다.
- DB Timeout·Deadlock·Serialization 실패·Constraint/RLS 거부는 기존 안전 오류 계약으로 매핑하고 Retry 가능 여부를 명시한다. 무제한 자동 재시도와 Write 중복 실행을 금지한다.
- Browser 코드는 계속 same-origin BFF만 사용한다. DB·내부 API 주소나 Credential을 Client Bundle에 추가하지 않는다.
- Secret 원문은 저장소·Evidence·Log에 기록하지 않는다. 환경값은 존재 여부와 마스킹된 식별만 보고한다.

## 허용·제외 범위

- 허용: API의 Migration/DB 설정, PostgreSQL Repository Adapter, RLS/Role/Policy, Transaction·Idempotency·Audit 영속 경계, Health/Readiness, Docker/Compose 격리 개발 자원, 직접 관련 Unit·Integration·Security·Migration 테스트와 문서·Evidence.
- 제외: Object Storage·Queue·Worker(`R1-M5-02`), Local SQLite/File/Vector(`R1-M5-03`), Source/Run/RuleSet/Model/Studio 전체 정본(`R1-M5-04`), Sync·삭제·Backup 정식 구현(`R1-M5-05~07`), 운영 Oracle 배포, 공용 서버 자원 변경, UI 전면 변경.
- 기존 공개 OpenAPI·BFF·Native Gateway·Auth·Authorization·Audit·Notification 의미를 암묵적으로 변경하지 않는다. 공개 API 또는 데이터 계약 변경이 필요하면 구현 전에 어울1에게 증거와 선택지를 반환한다.

## TDD·필수 검증

- RED에서 영속 Adapter 부재, RLS 미적용, 교차 Tenant/Workspace FK·조회·Write, Pool Context 누출, Migration 재적용, Audit/Idempotency 비원자성의 기존 실패를 먼저 증명한다.
- Migration: 빈 DB 적용, Schema/Extension/Role/Policy 검사, 같은 Revision 재실행, 지원하는 직전 Revision에서 Upgrade, 실패 주입 후 원상태/복원, Checksum 또는 Drift 탐지를 검증한다.
- Security: Service Authorization 허용+RLS 허용, Service 거부, RLS 단독 거부, Tenant/Workspace 교차 직접 SQL, `BYPASSRLS`/Owner 여부, Transaction-local Context 누출, SQL Injection과 과대 입력을 검증한다.
- Transaction: 업무 Write+Audit+Idempotency 성공/실패 원자성, 중복 Key, Fingerprint 충돌, stale ETag, 실제 동시 요청을 독립 Connection/Process에서 검증한다.
- 회귀: M4 Identity·Authorization·Audit·Notification 전체 테스트, API 계약, Web BFF/Build, Quality·Independence를 실행한다.
- 로컬 기본 검증 후 Commit·Push하고 `/home/ubuntu/deploy/daon-user` 아래 격리 Compose Project·Network·Volume과 PostgreSQL `18.4` 전용 DB에서 Migration 사전점검→Backup→적용→재적용→Rollback/격리 복원→실제 API/DB 검증을 수행한다.
- ysna-server는 정확 Commit SHA와 ARM64 호환 Image를 사용한다. 종료 전 생성한 Container·Network·Volume·임시 Image를 이 작업 소유 범위에서만 정리하고 기존 공용 자원 Hash/상태 불변을 확인한다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-01_progress.md`에 착수, 영향 분석, RED, Migration/Schema, RLS/Transaction, 로컬 검증, Push, 서버 사전점검·Backup·Migration·Rollback/Restore·통합 검증, 오류·복구, 종료 직전을 즉시 기록한다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-01/`에 정적·자동·실제 DB/Process 증거를 구분해 저장하고 Secret·Connection String 원문은 제거한다.
- 완료보고는 `docs/04_test_reports/release_1/R1-M5-01_completion_report.md`에 `판정 → 판단 이유 → 조치` 순서와 표준 상태 계약으로 작성한다.
- 단일 구현 Commit을 Push하고 Local/Remote SHA·Clean, 변경 파일, Dependency/Lock Diff, Test/Build/서버 결과, 잔여 Process·자원 0건을 보고한다.
- 첫 오류로 실패보고하지 않는다. 승인 경계를 넘지 않는 대안을 조사하고, 외부 계약·데이터·보안 경계 변경이 필요할 때만 구현을 멈추고 어울1에게 반환한다.
