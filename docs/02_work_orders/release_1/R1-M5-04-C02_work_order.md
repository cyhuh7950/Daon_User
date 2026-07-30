# R1-M5-04-C02 GenerationRequest 전이 정합성 복구 작업지시서

## 판정·근거·Writer

- 판정: C01 검토 과정에서 어울1이 상태 Diagram만 좁게 해석해 상세 설계 §18.3의 명시적 제출 전 설정 변경 계약을 잘못 제거했다. 개발자 실패가 아니며 정식 `FAILURE_REPORT`는 0회다.
- 승인 근거: 상세 설계 §18.3은 제출 전 설정 변경 시 기존 확정을 무효화하고 `confirmed → configuring`으로 복귀한 뒤 새 `GenerationSettingsSnapshot`을 재확정하도록 명시한다. 테스트 `TS-STU-009A`도 같은 결과를 요구한다.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, Branch는 `codex/r1-m5-04-c02`, 기준 HEAD는 `b374348cbe73f2304110c23ea1b6a1f73bb4f286`다.
- 어울2가 유일한 코드 Writer다. 외부 untracked `docs/04_test_reports/release_1/interim_review_2026-07-30.md`, `docs/04_test_reports/release_1_model_provider_queries.md`를 수정·삭제·Stage·Commit하지 않는다.

## 단일 수정 목표

- 승인된 `GenerationRequest` 전이를 `configuring → confirmed → submitted`와 제출 전 설정 변경용 `confirmed → configuring`으로 복원한다.
- `submitted` 상태의 Request와 연결 Snapshot은 계속 불변이며 `submitted → configuring|confirmed`와 그 밖의 Terminal 역행은 거부한다.

## 필수 계약

- PostgreSQL Migration과 API Domain Matrix에 `confirmed → configuring`을 복원하고 승인 전이 총수를 실제 Matrix에서 다시 계산한다.
- 복귀 전이는 기존 GenerationRequest Row에서 `state`와 Optimistic `version`만 바꾼다. 기존 확정 Snapshot을 수정·삭제하지 않으며, 후속 재확정은 새 `GenerationSettingsSnapshot`을 추가하도록 관계 계약을 유지한다.
- 이번 M5 작업은 Studio Service를 선점하지 않는다. 설정 변경 Command와 새 Snapshot 생성·연결·Output Revision 0건의 사용자 흐름은 M8이 구현하되, M5 정본이 그 흐름을 막지 않고 필요한 Version/불변/FK를 제공해야 한다.
- 실제 금지 전이 검증은 `submitted → configuring`, `submitted → confirmed`, OutputVersion `approved → draft`처럼 정본에서 금지된 Edge를 사용한다. 승인된 `confirmed → configuring`을 불법 전이 Test로 사용하지 않는다.
- C01의 성공·거부 Attempt/Audit 영속화, 같은 Attempt Idempotency, 동시 Lost Update, RLS·불변 계약을 그대로 유지한다.
- 승인된 복귀 전이 성공 시 `canon_state_transitions`, `canon_transition_attempts(outcome=succeeded)`, `audit_events(outcome=succeeded)`가 동일 Attempt/Trace로 정확히 1건씩 연결된다.
- `submitted → configuring` 거부 시 상태 불변과 Attempt/Audit denied 보존을 검증한다.

## TDD·회귀·증거

- RED를 먼저 추가해 현재 Domain/DB가 승인된 `confirmed → configuring`을 거부하고 있음을 재현한다.
- Domain Test에 승인 복귀와 `submitted` Terminal 거부를 모두 명시한다.
- PostgreSQL 18.4에서 GenerationRequest를 `configuring → confirmed → configuring → confirmed → submitted`로 실제 실행하고 각 Version·Ledger·Audit 수를 검증한다.
- 빈 DB `0001→0002→0003`, Head 재적용, `0003→0002→0003`, 승인 전이 전수, 금지 전이, RLS/FK/불변/Attempt Idempotency와 실제 동시성 회귀를 실행한다.
- API·Local·Web Build·Lint·독립성·Quality Gate와 Service Health를 회귀한다.
- 기존 R1-M5-04 Evidence Manifest·서버 요약·완료보고·진행 기록을 C02 exact SHA와 복원된 전이 수로 갱신한다.
- 서버는 `/home/ubuntu/deploy/daon-user/R1-M5-04-C02`와 전용 Compose Project·Container·Network·Volume만 사용한다. 정리는 파괴적 작업이므로 exact 자원 삭제 전 신산님의 승인을 받는다.
- 결과는 `판정 → 판단 이유 → 조치`와 표준 상태 계약으로 반환한다.

## 완료 기준

- 설계 §18.3과 TS-STU-009A의 승인 복귀가 Domain·PostgreSQL에서 성공한다.
- `submitted` 이후 기존 Request/Snapshot 변경과 역행은 거부된다.
- C01의 거부 Ledger/Audit·동시성·Idempotency 회귀가 모두 통과한다.
- 실제 DB·Quality Gate·Health·Evidence가 최종 exact SHA에 연결된다.

