# R1-M5-04-C01 상태 전이 계약 수정 작업지시서

## 판정·근거·Writer

- 판정: `R1-M5-04` 제출 결과는 **중대 미진·재작업**이다. 정식 `FAILURE_REPORT`는 아니며 유효 실패 횟수는 0회다.
- 근거 1: 상세 설계 §18.3과 원 작업지시의 GenerationRequest 전이는 `configuring → confirmed → submitted`인데 구현이 `confirmed → configuring`을 추가 허용한다.
- 근거 2: 금지 전이·Terminal 역행·교차 Scope 대상·Concurrent Lost Update는 거부되지만, 원 작업지시가 요구한 거부 Audit 또는 Transition Attempt Ledger가 Transaction Rollback 뒤에도 보존되지 않는다.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, Branch는 `codex/r1-m5-04`, 시작 HEAD는 `63eea9ef25695e63139dd1beb1326acf80512395`다.
- 어울2가 유일한 코드 Writer다. 외부 untracked `docs/04_test_reports/release_1/interim_review_2026-07-30.md`, `docs/04_test_reports/release_1_model_provider_queries.md`를 수정·삭제·Stage·Commit하지 않는다.

## 단일 수정 목표

- GenerationRequest 전이를 승인된 단방향 계약과 일치시키고, 거부된 상태 전이 시도를 별도 Append-only Ledger와 기존 Audit 정본에 안전하게 남긴다.
- 허용 전이 수는 승인 Matrix를 다시 계산해 Migration·Domain·Test·Manifest·Evidence에서 동일하게 맞춘다. 숫자를 맞추기 위한 다른 임의 전이 추가를 금지한다.

## 필수 구현 계약

- `confirmed → configuring`을 PostgreSQL `TRANSITIONS`, API Domain Matrix, 서버 검증 기대값에서 제거한다. 해당 역전이와 `submitted` 이후 모든 전이는 거부 Test를 추가한다.
- 성공 전이는 기존 `canon_state_transitions`와 `audit_events(outcome=succeeded)`를 유지한다.
- 실패 전이는 별도 Append-only `canon_transition_attempts` 또는 동등한 명시적 Ledger에 `tenant_id`, `workspace_id`, `entity_type`, `record_id`, `attempt_id`, expected/current version, source/target state, actor, safe error code, trace, policy version, occurred_at, `outcome=denied`를 남긴다.
- 최소 거부 사유 `CANON_TRANSITION_INVALID`, `CANON_VERSION_CONFLICT`, `CANON_RECORD_NOT_FOUND`를 기록한다. 현재 Scope 밖 Record의 실제 State·Tenant·Workspace 존재 여부는 노출하지 않고 현재 Scope의 거부 시도만 기록한다.
- 거부 기록이 같은 Transaction의 Exception Rollback으로 사라져서는 안 된다. DB 함수는 상태를 바꾸지 않은 채 구조화된 거부 결과를 Commit 가능하게 반환하고, Repository가 Commit 이후 안정 Error로 변환하는 방식 또는 동등한 원자적 방법을 사용한다. 별도 DB 연결·dblink·자체 비밀·외부 Queue를 추가하지 않는다.
- 성공 또는 거부 한 시도당 Attempt Ledger 정확히 1건을 보장한다. 동일 `attempt_id` 재전송은 중복 상태 변경·중복 Ledger를 만들지 않고 기존 결과를 재현하거나 안정적 Idempotency 오류를 반환한다.
- Attempt Ledger는 강제 RLS, Append-only Trigger, `daon_app` 최소 `SELECT/INSERT` 권한을 적용한다. 직접 Update/Delete는 거부한다.
- 기존 Audit 정본은 성공 시 `outcome=succeeded`, 거부 시 기존 허용값인 `outcome=denied`를 기록한다. AuditEvent의 공개 상태 계약이나 허용값을 변경하지 않고 Attempt Ledger와 동일한 attempt/trace를 연결한다.
- Direct illegal transition, stale version, missing/cross-scope record를 실제 `daon_app` Session으로 호출한 뒤 상태 불변과 Attempt Ledger 보존을 같은 DB에서 검증한다.

## 회귀·증거·정리

- RED를 먼저 추가해 역전이 허용과 거부 Ledger 부재를 증명하고 진행 기록에 남긴다.
- 빈 DB `0001→0002→0003`, Head 재적용, `0003→0002→0003`, RLS/FK/불변/허용 전이 전수, API Domain·Cloud·Local 회귀를 다시 실행한다.
- Migration `0003`은 아직 Release Branch에 병합되지 않았으므로 같은 Revision을 정정한다. 새 `0004`로 결함을 덮지 않는다.
- `git diff --check 242b826..HEAD`의 기존 Markdown EOF 경고 2건도 범위 내 문서에서 제거한다.
- 서버 검증은 새 exact SHA로 수행하고 Evidence Manifest·요약·완료보고의 검증 SHA, 전이 수, Ledger 수와 판정을 갱신한다.
- 서버 격리 자원은 정확한 R1-M5-04 범위만 정리하고 Checkout/Container/Network/Volume 0, 보호 자원 불변을 다시 증명한다.
- 진행은 기존 `docs/04_test_reports/release_1/R1-M5-04_progress.md`에 `C01` 단계로 이어 기록한다. 결과는 `판정 → 판단 이유 → 조치`와 표준 상태로 반환한다.

## 완료 기준

- GenerationRequest 승인 전이만 허용하고 역전이·Terminal 전이가 실제 DB와 Domain 양쪽에서 거부된다.
- 성공 전이와 거부 전이 각각 상태·성공 Ledger·거부 Attempt Ledger·Audit의 기대 행 수가 정확히 일치한다.
- 금지 전이·Lost Update·Missing/Cross-scope 시도 후에도 거부 Ledger가 Commit되어 조회 가능하고 대상 상태는 불변이다.
- 기존 52 Entity Mapping, RLS, FK, Snapshot 불변, Local Projection, Build·Quality Gate가 회귀 없이 통과한다.
