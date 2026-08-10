# R1-M5-07 Windows Native Recovery Command C10A-R01 진행 기록

## 2026-08-11 KST — 착수·검토 확인

- 상태: `IN_PROGRESS`; Issue `R1-M5-07-WINDOWS-COMMAND-C10A-I001` 1차 재작업.
- 정본: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, `master`, `HEAD=origin/master=ac5b2c70030b08c30f6533aabd13d42113454398`.
- Writer: 원 C10A를 구현한 동일 어울2 단일 Writer. Cloud 7종·Command 이름/개수·DTO·Safe 경계·앱 수명 LRU는 변경하지 않는다.
- 검토 판정 확인: Local 3 Tauri Command가 동기 Loopback TCP I/O를 IPC 처리 흐름에서 직접 수행하는 Important 1건은 코드와 일치한다. `LocalServiceManager`는 `Arc<ManagerInner>` 기반 `Clone`이므로 `spawn_blocking`으로 안전하게 소유권을 이동할 수 있다.
- 보존: 사용자 삭제 31건과 원 미추적 문서 3건, 원 C10A Progress·Completion을 변경·Stage하지 않는다. Branch·Worktree·Commit·Push·배포·Browser·실제 Restore는 금지한다.
- 다음 작업: 동기 Command 계약을 RED로 재현하고 Local 행동 RED를 추가한다.

## 2026-08-11 KST — 정적 TDD RED

- 상태: `RED 확인`.
- 변경: Node 계약검사에 Local 3 Command의 `pub async fn`과 각 1회 `spawn_blocking`을 요구했다.
- 명령: `node --test scripts/tests/desktop-recovery-command-surface.test.mjs`.
- 결과: 예상 실패 1/1. 첫 실패는 `recovery_local_start_scan`이 현재 `pub fn`인 동기 계약이라서 발생했다.
- 다음 작업: 행동 RED에 비동기 지연 중 독립 상태 진행, exact 매핑, 미준비 network 0, Join Safe Error를 추가한다.

## 2026-08-11 KST — 행동 TDD RED

- 상태: `RED 확인`.
- 변경: 실제 제품 Helper를 호출하는 Rust 계약 테스트에 지연 Local I/O 중 독립 비동기 작업 진행, Local 3종 Method/Path/Input exact 매핑, 미준비 시 Network 0, Blocking Task Join 실패의 Safe Error 검증을 추가했다.
- 명령: `node scripts/run-isolated-desktop-cargo.mjs test`.
- 결과: 예상 실패. 신규 계약 Helper 3종이 아직 제품에 없어 `E0432 unresolved imports`로 실패했으며, 기존 회귀 실패는 없었다.
- 다음 작업: Local 3 Command만 비동기화하고 각 동기 TCP 호출을 `spawn_blocking`으로 격리하는 최소 구현을 적용한다.

## 2026-08-11 KST — 최소 GREEN 구현 및 1차 회귀

- 상태: `GREEN 보정 중`.
- 변경: Local 3 Tauri Command를 `async`로 바꾸고 `LocalServiceManager` 내부 Transport Clone을 각 1회 `tauri::async_runtime::spawn_blocking`으로 실행했다. Join 실패는 기존 공개 Safe Error `LOCAL_RECOVERY_REQUEST_FAILED`로 변환했다. Cloud 7 Command와 등록 Surface는 유지했다.
- 정적 계약: `node --test scripts/tests/desktop-recovery-command-surface.test.mjs` 1/1 PASS.
- 전체 회귀 1차: `node scripts/run-isolated-desktop-cargo.mjs test`에서 기존 Lib 18/18, Local 5/5, Native 19/19 및 신규 비동기/미준비·Join 행동 검증은 PASS했다. 신규 exact 매핑 테스트 1건만 테스트 기대 문자열의 불필요한 역슬래시 때문에 실패했다.
- 원인/복구: 실제 요청 Body는 정상 JSON `"workspace_id":"..."`, `"expected_version":4`였으나 테스트가 역슬래시 포함 문자열을 기대했다. 제품 코드는 변경하지 않고 테스트 기대값만 실제 JSON 계약으로 바로잡았다.
- 다음 작업: Rust 파일 Format 후 전체 회귀를 중복 없이 다시 실행하고 최종 검증한다.

## 2026-08-11 KST — fresh 전체 회귀·종료 점검

- 상태: `COMPLETED`.
- 전체 회귀: `node scripts/run-isolated-desktop-cargo.mjs test` 86/86 PASS(18+5+19+44), 실패·무시 0. Windows MSVC Linker의 산출 라이브러리 생성 안내만 있었고 Test 실패/경고 원인은 아니었다.
- 정적 계약: `node --test scripts/tests/desktop-recovery-command-surface.test.mjs` 1/1 PASS.
- 품질: `npm run verify:desktop-lint` PASS(4 files), `rustfmt --edition 2024 --check` PASS(허용 Rust 2 files), `git diff --check` PASS(CRLF 전환 안내만 존재).
- 안전·범위: `gen` 0건, Private Key/AWS Key/localhost/0.0.0.0 리터럴 0건, Cargo/Rustc Process 0건, 현재 작업의 격리 Cargo 임시 Target 0건.
- 보존: 사용자 삭제 31건, 원 미추적 문서 3건을 그대로 보존했다. Branch·Worktree·Commit·Push·배포·Browser·실제 Restore는 수행하지 않았다.
- 종료: Local 3 Command 비동기 격리와 행동 계약을 충족했다. 어울1의 결과 검토가 다음 단계다.
