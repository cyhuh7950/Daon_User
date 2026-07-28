# R1-M4-02 Audit Event Core 작업지시서

## 승인 기준

- 기준 Branch `codex/r1-m4-02`, 기준 HEAD `cc941e964437d2c2c89c81147a636693545bb66b`.
- 승인 상세 설계서 v0.7, Release 1 구현계획 v0.9, 테스트계획 v0.7과 TS-SEC v0.5를 적용한다.
- 어울2가 이 Worktree의 유일한 코드 작성자다.

## 단일 목표

Actor·Trace·Policy Version·안전한 변경 전후를 가진 불변 `AuditEvent`, append-only 저장 계약과 SHA-256 hash-chain 무결성 검증을 구현한다. 실제 HTTP/FastAPI, DB·Migration, Auth·RLS, Notification은 구현하지 않는다.

## 허용 범위

- `services/api/src/daon_user_api/audit.py`와 최소 Package init
- `services/api/tests/test_audit*.py`, `services/api/README.md`, `services/api/pyproject.toml` 최소 경계
- OpenAPI AuditChange·AuditEvent·Audit 목록 계약, M4-01 결정적 증거 갱신
- Root `verify:api-audit` 명령과 무의존 최소 검증기
- `docs/01_architecture/audit_event_core_contract.md`
- `docs/03_evidence/release_1/R1-M4-02/audit-core-summary.json`
- 본 Work Order·Prompt·Progress·결과보고

App·UI·Local Service·Runtime HTTP·Workflow·Lockfile·외부 의존성을 변경하지 않는다.

## Domain 계약

1. 불변 `AuditEvent`는 opaque `event_id`, UTC `occurred_at`, `actor_id`·`actor_type`, `tenant_id`, optional `workspace_id`, `action`, `target_type`·`target_id`, `outcome`, `trace_id`, `policy_version`, 안전한 before/after 변경 Projection, metadata, `previous_event_hash`, `event_hash`를 가진다. 필수값·Enum·길이·UTC를 fail-close 검증한다.
2. Actor·Trace·Policy Version·Tenant·Target 누락, 빈값, naive/non-UTC 시각, 중복 Event ID는 append 전에 거부하고 저장·Chain을 변경하지 않는다.
3. 변경 전후와 Metadata는 승인된 JSON 값만 받는다. 모든 중첩 Key에서 Password·Secret·Token·Credential·API Key·Raw Provider Error·Internal URL/Host 계열을 거부하고, localhost·127.0.0.1·Docker Host·절대 Internal URL과 비직렬화 값을 거부한다. 원문 전체 객체를 자동 Dump하지 않는다.
4. Event Hash는 `event_hash` 자체만 제외한 모든 감사 의미 필드를 canonical JSON(정렬 Key·고정 Separator·UTF-8)로 직렬화하고 Previous Hash와 함께 SHA-256으로 계산한다.
5. Append는 Lock 안에서 Sequence·Previous Hash·Event Hash·Event ID 중복을 원자적으로 확정한다. Public API는 append·read/list·verify_integrity만 제공하고 Update/Delete/Replace/Clear를 제공하지 않는다. 반환값으로 내부 List를 노출하지 않는다.
6. 무결성 검증은 순서·누락·필드·Previous Hash·Event Hash 변조를 검출하며 오류에 원문 민감값을 포함하지 않는다.
7. 같은 Trace 계보 조회와 Tenant·Workspace·Action·Outcome·Time·Cursor Filter를 순수 Read로 제공한다. 권한 강제는 M4-04/M4-05 소유이고 Cursor 의미는 OpenAPI와 맞춘다.
8. Audit 보존 1년은 R1-D009 정책 Metadata와 후속 M5 저장 책임이며 Core에 삭제 기능을 넣지 않는다.

## OpenAPI 승계

- `AuditChange`, `AuditEvent`, Audit 목록 Envelope와 Query를 추가한다.
- `/api/v1/audit-events` GET 200은 generic Resource가 아니라 AuditEvent 목록을 참조한다.
- Actor·Trace·Policy·Before/After·Hash를 포함하고 Hash는 Hex Pattern, 시각은 date-time, ID는 기존 OpaqueId를 사용한다.
- M4-01 검증·Test를 유지하고 `verify:openapi-contract -- --write` 후 no-write 일치를 증명한다.

## TDD와 검증

- 핵심 계약 부재 RED를 먼저 기록한다.
- Unit: 정상 Append·불변·결정적 Hash, 필수값·안전 Key·시각·중복 거부, 실패 무변경, Filter·Cursor.
- Integration: 연속 3개 변경 계보, 같은 Trace 조회와 Before/After 보존.
- Tamper: Field·순서·누락·Previous Hash·Event Hash 변조 모두 검출, 정상 Chain 통과.
- Python 3.14.3 표준 라이브러리만 사용하고 Root `npm run verify:api-audit`로 실행한다.
- 완료 시 Target, OpenAPI write/no-write, 전체 Node, Workspace, Independence, 장시간 Quality Gate, Toolchain, API 범위 회귀, JSON Parse, Python Compile/Test, Package Export, Node Syntax, Diff와 보호 경계를 검증한다.
- 기준선 실패는 exact base 격리 비교 없이 통과로 간주하지 않는다.

## 진행·보고·전달

`docs/04_test_reports/release_1/R1-M4-02_progress.md`에 착수·단계 완료·오류/복구·테스트·종료 직전 상태를 Append한다. 결과는 `판정 → 판단 이유 → 조치`와 표준 상태 형식을 사용한다. 공개 계약을 승인 범위 밖으로 확장해야 하면 `INCOMPLETE`로 질의한다. 완료 후 단일 목적 Commit을 `codex/r1-m4-02`에 Push하고 exact SHA·원격 일치·Clean을 보고한다. PR·CI·Merge는 어울1 소유다.
