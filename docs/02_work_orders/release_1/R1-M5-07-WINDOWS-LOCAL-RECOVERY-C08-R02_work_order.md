# R1-M5-07 Windows Local Recovery Native Port C08-R02 작업지시서

## 1. 문서 상태

- 상태: `READY`
- issue_id: `R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-I001`
- 선행 판정: 원 C08 `INCOMPLETE` 1회, R01 `INCOMPLETE` 2회, 유효 `FAILURE_REPORT` 0회
- 정본: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, branch `master`
- 기준 HEAD: `0fb709377cb27c15962bf92af01a58c9ba23895b`
- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R02_progress.md`
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-LOCAL-RECOVERY-C08-R02_completion_report.md`

## 2. 목적

C08-R01에서 남은 실제 Python Local Service와 Rust Native Adapter의 Timestamp 상호운용 결함과, 승인 접두사 안에 삽입된 Native Context가 Safe DTO로 통과하는 결함을 TDD로 해소한다. 기존 Deadline, Parser 오류 분류, Version 정본, Lifecycle, Native Session 및 Local Service 동작은 유지한다.

## 3. 필수 정본

작업 전 아래 문서를 EOF까지 읽고 SHA-256과 적용 조항을 진행 기록에 남긴다.

- `AGENTS.md`
- 승인된 상세 설계서와 Release 1 구현계획서·테스트계획서
- C08, C08-R01 작업지시서·진행기록·완료보고
- `services/local-service/src/daon_user_local_service/recovery.py`
- `services/local-service/src/daon_user_local_service/local_storage.py`
- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/local_service.rs`

## 4. 허용 변경 범위

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/local_service.rs` — 실행 Context 전달에 필요한 최소 변경만 허용
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `apps/desktop/src-tauri/tests/local_service_contract.rs` — 필요한 경우 최소 변경
- `scripts/run-isolated-desktop-cargo.mjs` — 새 테스트 활성화에 필요한 최소 변경만 허용
- 이 작업의 R02 progress/completion 문서

Python Local Service 제품·테스트, Native Session, Cargo/Cargo.lock, Web, Cloud Recovery, 사용자 삭제 31건과 기존 미추적 문서 3건은 수정하지 않는다.

## 5. 구현 계약

### R02-1 Python–Rust UTC Timestamp 상호운용

- Python 정본이 생성하는 `YYYY-MM-DDTHH:MM:SS.ffffffZ`와 소수초 없는 `...SSZ`를 모두 허용한다.
- Rust는 `Z` UTC, RFC3339 실제 달력/시간, 합리적 최대 길이를 검증한다.
- Offset, 잘못된 달력/시간, trailing data, 과대 문자열은 Fail-closed한다.
- 실제 Python 응답과 동일한 JSON Fixture가 Manager→Loopback TCP→Parser→Port 전체 경로를 통과해야 한다.

### R02-2 Native Context 포함 누출 차단

- 실행 중 Port, Request Token, Root Secret, Storage Root, 격리 경로가 응답 원문 또는 모든 문자열 Projection의 어느 위치에 포함돼도 `LOCAL_RECOVERY_RESPONSE_REJECTED`, non-retryable로 거부한다.
- `prefix + canary`, `canary + suffix`, 중간 삽입을 모두 검증한다.
- Job ID는 실제 생성 계약에 맞는 고정 접두사와 고정 길이 소문자 hex 계약으로 좁힌다. Target ID도 정본 형식 외 임의 Context 포함을 허용하지 않는다.
- 비교용 Secret/Canary 사본은 불필요한 복제를 금지하고, 소유 사본은 Drop 시 Zeroize한다.

### R02-3 경미 보강

- OS 난수 실패 시 동일 `000...0` trace_id를 반복하지 않도록 충돌을 피하는 안전한 불투명 Trace 대안을 적용한다. Secret·Port·Path를 포함하지 않는다.
- Slow-drip 테스트는 실제 TCP I/O 진입 Barrier로 동기화하고 절대 25ms 같은 취약한 시간 단언을 제거한다.

## 6. TDD 및 검증

각 결함은 기존 구현에서 실패하는 RED를 먼저 기록한 후 최소 구현으로 GREEN을 만든다.

필수 검증:

1. `node scripts/run-isolated-desktop-cargo.mjs test`
2. Recovery 표적 Rust 계약
3. `uv run --isolated --project services/local-service --frozen python -m pytest services/local-service/tests/test_recovery.py services/local-service/tests/test_app.py -q`
4. `node --test scripts/tests/desktop-local-service.test.mjs`
5. `npm run verify:desktop-lint`
6. 허용 Rust 파일 `rustfmt --check`
7. `git diff --check`
8. Secret/내부 Context 노출, 금지 주소, 허용 범위, 사용자 삭제·미추적 보존 확인

환경·도구 중단은 정식 실패로 세지 않는다. 같은 명령을 중복 실행하지 말고 잔존 프로세스와 생성 경로를 먼저 확인한다.

## 7. 완료 조건

- Important 2건과 Minor 2건의 RED→GREEN 근거가 있다.
- 실제 Python fractional timestamp Fixture가 Rust 전체 제품 경로를 통과한다.
- 접두사 내부·접미사·중간 삽입 Native Context가 실제 제품 경로에서 모두 거부된다.
- 전체 회귀와 보존 검사가 통과한다.
- Commit·Push·배포·Browser·실제 Restore는 수행하지 않고 어울1 검토를 요청한다.

## 8. 결과보고 형식

`판정 → 판단 이유 → 조치` 순서로 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나를 제출한다. 변경 파일, RED/GREEN, 전체 회귀, 보존 상태, 미해결 위험과 다음 조치를 포함한다.
