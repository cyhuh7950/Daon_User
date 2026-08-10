# R1-M5-07 Windows Native Recovery Command C10A-R01 수정 작업지시서

## 1. 판정과 기준

- Work Order ID: `R1-M5-07-WINDOWS-COMMAND-C10A-R01`; Issue ID: `R1-M5-07-WINDOWS-COMMAND-C10A-I001`.
- 상태: `READY` · 2026-08-11 · C10A 내부 독립 검토 Important 1건 보정.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; Branch·Worktree를 생성하지 않는다.
- 원 C10A 구현과 Progress·Completion을 보존하고, 동일 어울2가 이 보정 범위의 유일 Writer다.
- `AGENTS.md`, 승인 설계 1.2, Native Bridge Plan Task 4.5, 원 C10A 작업지시·Progress·Completion, 최신 독립 검토 결과를 EOF까지 읽고 적용 근거를 새 Progress에 기록한다.

## 2. 수정 판정

- 판정: `NEEDS_CHANGES`.
- 이유: `recovery_local_start_scan`, `recovery_local_get_job`, `recovery_local_repair_job`이 동기 Tauri Command로 Loopback TCP I/O를 완료할 때까지 IPC 처리 흐름을 차단할 수 있다.
- Cloud 7종 Command, 공개 이름, DTO, Safe Projection/Error, Rust-only Credential·Context, 앱 수명 LRU와 기존 정상 동작은 변경하지 않는다.

## 3. 단일 수정 목표

1. Local Recovery Command 3개를 `async fn`으로 전환한다.
2. `LocalServiceManager`를 안전하게 clone한 뒤 `tauri::async_runtime::spawn_blocking` 내부에서 기존 동기 `LocalRecoveryPort` 호출을 실행한다.
3. Join 실패·panic은 내부 오류나 경로를 노출하지 않는 기존 승인 Safe Error로 변환한다.
4. 지연 Loopback 호출 중 다른 상태 조회 또는 독립 invoke가 진행될 수 있음을 실제 행동 테스트로 증명한다.
5. Local 3종 Method·Path·입력 매핑, Local 미준비 시 네트워크 0회, Safe Error를 실제 Rust 행동 테스트로 고정한다.

## 4. 허용 변경 경로

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/lib.rs` — Command signature/등록 정합에 필요한 경우만
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `scripts/tests/desktop-recovery-command-surface.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A-R01_progress.md` — 신규
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A-R01_completion_report.md` — 신규
- 원 C10A Completion은 소급 수정하지 않는다.

Cargo/Lock/API/OpenAPI/Web/React/CSP/설정, Command 이름·개수, 허용 경로 밖 변경은 금지한다. 새 의존성 또는 공개 계약 변경이 필요하면 `BLOCKED`로 보고한다.

## 5. TDD와 검증

1. RED: 지연 Local Port 호출 중 독립 상태 작업이 진행되지 못하는 현재 동작 또는 동기 Command 계약을 먼저 실패시킨다.
2. RED: Local 3종 매핑·미준비 network 0·Join 실패 Safe Error의 누락을 행동 테스트로 재현한다.
3. GREEN: 승인 범위 안에서 async/spawn_blocking 최소 수정만 적용한다.
4. 다음을 fresh 실행한다.

```powershell
node --test scripts/tests/desktop-recovery-command-surface.test.mjs
node scripts/run-isolated-desktop-cargo.mjs test
npm run verify:desktop-lint
git diff --check
```

- 허용 Rust 파일 rustfmt-check, Secret·내부 주소 노출 검사, `gen`과 Cargo/Rustc 잔존 Process 0건을 확인한다.
- 사용자 삭제 31건과 원 미추적 문서 3건을 보존한다.
- Commit·Push·배포·Browser·실제 Restore는 수행하지 않는다.

## 6. 진행·결과 계약

- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A-R01_progress.md`.
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A-R01_completion_report.md`.
- 각 단계와 오류·복구·테스트 결과를 즉시 기록한다.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
