# R1-M5-02-C01 Object 통합시험 Fixture·완료 증거 보완 작업지시서

## 판정

`R1-M5-02` 구현과 로컬 Quality Gate, ysna-server의 PostgreSQL 18.4·MinIO·Worker 장애/복구 검증은 수행됐으나, 실제 Object 통합시험에서 테스트 Fixture 결함으로 `12 PASS·3 FAIL·1 ERROR`가 남았고 Evidence·완료보고가 정본 Branch에 없다. 따라서 `VERIFYING → CORRECTION_REQUIRED`로 전환한다. 이는 제품 코드의 정식 `FAILURE_REPORT`가 아니며 누적 실패 횟수는 0회다.

## 승인 기준과 작업공간

- Issue ID는 기존 `R1-M5-02`를 유지하고 보정 Work Order ID만 `R1-M5-02-C01`로 기록한다.
- 공식 작업공간은 `C:\Users\cyhuh\OneDrive\바탕 화면\D Driver\Project\Daon_User`, Branch는 `codex/r1-m5-02`, 인계 HEAD는 `2d4245da7ab59b2626c1d55bbac8a466d4c2bcb0`이다.
- 승인 정본은 `AGENTS.md`, 상세 설계 `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 0.7, 구현계획 `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` 0.9, 테스트계획 `docs/04_test_reports/release_1_test_plan.md` 0.7, 원 작업지시서 `R1-M5-02_work_order.md`다. 모두 EOF까지 읽는다.
- `D:\Project\Daon_User`, `C:\tmp`의 Clone·Worktree는 읽기 전용 보존 자료이며 수정·삭제·작업 전환을 금지한다.
- 어울2가 이 범위의 유일한 코드 Writer다. 어울1은 결과 검토 전까지 같은 범위를 수정하지 않는다.

## 판단 이유

1. `test_domain_object_outbox_job_are_atomic_and_replay_safe`가 최초 제출과 Replay 반환값 전체를 동일 비교한다. Replay는 같은 Object·Outbox·Job 식별자를 재사용하되 `replayed` 표시가 `false → true`로 바뀌는 것이 계약이므로 식별자·상태와 Replay Flag를 분리 검증해야 한다.
2. Claim·Crash·Retry 계열 Fixture의 고정 `now`가 ysna-server DB가 기록한 `next_attempt_at`보다 과거가 되어 Claim 대상이 되지 않는다. 제품 Backoff를 우회하지 말고 DB가 반환한 `job.next_attempt_at`을 기준 시각으로 사용해야 한다.
3. 서버에서 이미 확인한 Migration 적용·재적용, Cloud `11/11`, Runtime `15/15`, live/ready 장애·복구, Object Queue 흐름, Digest·Prefix·Replay·교차 Workspace, Worker SIGTERM, Retry·Dead-letter·권한 재처리·이력 보존, Rollback·Restore 결과를 정본 Evidence와 완료보고로 남겨야 한다.

## 조치 목표

- 위 두 Fixture만 계약에 맞게 최소 수정하고 제품 구현을 Fixture에 맞춰 약화하지 않는다.
- 수정된 Object 통합시험을 실제 PostgreSQL 18.4·MinIO 환경에서 전부 PASS시킨다.
- 원 작업지시서의 로컬 회귀와 서버 장애·복구·격리·정리 조건을 다시 확인한다.
- 검증 명령, exact SHA, 결과 수, 안전하게 마스킹한 핵심 출력과 자원 정리 결과를 Evidence·진행 기록·완료보고에 남긴다.

## 허용·제외 범위

- 허용: `services/api/tests/test_object_queue.py`의 해당 Fixture, 테스트 실행에 직접 필요한 최소 Support, `docs/03_evidence/release_1/R1-M5-02-C01/`, `R1-M5-02_progress.md`, `R1-M5-02-C01_progress.md`, `R1-M5-02_completion_report.md`, 배포 검증 기록.
- 제품 코드 수정은 금지한다. 테스트 결과가 실제 제품 결함을 새로 증명하면 수정하지 말고 원인·증거·영향과 필요한 판단을 `FAILURE_REPORT`로 반환한다.
- 공개 API·Schema·Migration·보안·Object Key·Queue 상태·Retry 정책·Dependency Version·Compose 구조를 바꾸지 않는다.
- Secret, Credential, 내부 Endpoint, 개인정보와 Stack 원문을 Evidence에 기록하지 않는다.

## TDD·필수 검증

- 변경 전 서버 실패 의미를 Fixture와 제품 계약으로 재확인하고 진행 기록에 남긴다.
- Replay: Object ID·Job ID 등 불변 식별자는 같고 최초 `replayed=false`, 재호출 `replayed=true`, Entity Count와 Audit/Attempt 중복이 없음을 검증한다.
- Claim·Crash·Retry: 고정 과거 시각을 제거하고 저장된 `next_attempt_at`을 기준으로 Claim 가능 직전/이후 경계와 Backoff 계약을 검증한다.
- 로컬: Object Queue 직접 Test, Ruff, 해당 strict Mypy, Runtime·Cloud 회귀, 공식 Quality Gate를 실행한다.
- 서버: exact Push SHA를 `/home/ubuntu/deploy/daon-user`의 격리 Compose Project에서 PostgreSQL `18.4`·고정 MinIO Image로 검증한다. Migration `0002` 적용·재적용과 수정 Object Suite 전체 PASS, Runtime `15/15`, Cloud `11/11`, live/ready 장애·복구, Worker SIGTERM, Retry·Dead-letter·권한 재처리·이력 보존을 확인한다.
- 종료 전 이 작업 소유 Process·Listener·Container·Network·Volume·Bucket·Test Object를 정리하고 잔여 0, 기존 `shared-db`, `common`, `netdata`, `proxy` 불변을 확인한다.

## 진행·결과 계약

- `docs/04_test_reports/release_1/R1-M5-02-C01_progress.md`에 착수, 정본 확인, 실패 재해석, Fixture 수정, 로컬 검증, Commit·Push, 서버 exact SHA 검증, 오류·복구, 자원 정리와 종료 직전을 기록한다.
- 기존 `R1-M5-02_progress.md`의 작업 위치·현재 단계·다음 작업을 OneDrive 정본과 실제 상태로 정합화하되 기존 이력은 삭제하지 않는다.
- Evidence는 `docs/03_evidence/release_1/R1-M5-02-C01/`, 완료보고는 `docs/04_test_reports/release_1/R1-M5-02_completion_report.md`에 작성한다.
- 결과보고는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`을 포함하고, `판정 → 판단 이유 → 조치` 순서로 반환한다.
- 완료 전 Local HEAD·Origin Branch·서버 exact SHA, Working Tree Clean, 잔여 작업 자원 0과 정식 `FAILURE_REPORT` 0회를 보고한다.
