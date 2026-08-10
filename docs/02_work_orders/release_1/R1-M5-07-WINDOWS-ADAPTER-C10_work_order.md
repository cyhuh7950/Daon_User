# R1-M5-07 Windows React Recovery Adapter C10 작업지시서

## 1. 승인 기준과 Writer

- Work Order ID: `R1-M5-07-WINDOWS-ADAPTER-C10`; Issue ID: `R1-M5-07-WINDOWS-ADAPTER-C10-I001`.
- 상태: `READY` · 2026-08-11.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; 승인 기준 HEAD: `bdbb7f6ab3bc347fcd15d462c26871bf2ac428ea`.
- 신산님이 `master` 단독 작업을 명시했으므로 Branch·Worktree를 생성하지 않는다. 어울2가 이 범위의 유일 Writer다.
- 착수 전 `AGENTS.md`, `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md`의 Global Constraints·Task 5·Completion Contract, `docs/superpowers/specs/2026-08-10-windows-recovery-adapter-design.md`, `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-CLOUD-RECOVERY-C09-R02_completion_report.md`를 EOF까지 읽고 SHA-256과 적용 조항을 진행 기록에 남긴다.
- 선행 완료: C09/R01/R02 Cloud Recovery Native Port는 제품 Commit `e1c8681`, 수락 기록 Commit `bdbb7f6`으로 `origin/master`에 반영됐고 독립 검토 Critical 0·Important 0이다. C10 구현은 `R1-M5-07-WINDOWS-COMMAND-C10A` 완료 후 재개한다.

## 2. 단일 목표와 완료 조건

- 목표: 승인된 Rust Native Session·Cloud 7종·Local 3종 Recovery 경계를 Windows React 운영 화면에 Tauri `invoke` 전용 Adapter로 연결한다.
- `desktop-shell.jsx`는 Native Session Bridge와 Windows Recovery Adapter를 한 번 생성해 Operations 화면에 주입한다.
- React/UI는 `fetch`, XHR, WebSocket, 절대 API 주소, `localhost`, `127.0.0.1`, Docker Host/Port, `NEXT_PUBLIC_*`를 사용하지 않는다. CSP `connect-src 'none'`을 유지한다.
- 로그인 전 Cloud는 `AUTHENTICATION_REQUIRED`, Local Service 미준비는 `LOCAL_SERVICE_UNAVAILABLE`로 분리하며 Fixture 성공으로 대체하지 않는다.
- Web의 기존 same-origin Cloud Recovery UI·권한 Fail-close·Production-bound 상태는 보존한다.
- 실제 설치형·운영 Restore·외부 배포는 이 작업 완료로 주장하지 않는다. 이는 Task 6 검증 범위다.

## 3. 구현 계약

### 3.1 TDD 순서

1. RED로 Adapter 부재, Session 부재, Local Service 미준비, 허용되지 않은 Command/Method, Unsafe DTO/Error, React 직접 Network 호출을 먼저 실패로 고정한다.
2. `WindowsRecoveryAdapter`의 Cloud 7종과 Local 3종 메서드를 기존 Rust Command 표면에만 매핑한다. 새 공개 API·SafeError·Rust Command가 필요하면 구현하지 말고 `BLOCKED`로 보고한다.
3. `native-session-bridge.js`는 로그인·상태·Refresh·Logout의 Safe Projection만 JS에 전달한다. Access/Refresh/Password/Vault Blob·Gateway 내부주소를 반환·Log·Error·State에 보존하지 않는다.
4. Adapter는 Safe DTO와 Safe Error만 투영하고 unknown field·unknown code·Command를 Fail-close한다. Write의 Step-up·Idempotency·If-Match를 Rust 계약대로 전달하며 자동 재실행하지 않는다.
5. 공용 UI에 Local Scan→Job 조회→Repair 상태를 추가하고 Cloud와 Local 오류·진행·결과를 분리한다. 설명은 `i` Tooltip/Popover를 사용하고 기준 폰트·화면 표준을 유지한다.
6. `desktop-shell.jsx`는 Adapter를 단일 수명주기로 생성·주입하며 Session/Local 상태 전환과 unmount 후 갱신을 안전하게 처리한다.

### 3.2 허용 변경 경로

- Create: `apps/desktop/src/windows-recovery-adapter.js`
- Create: `apps/desktop/src/native-session-bridge.js`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `packages/ui/src/operations-recovery-pane.jsx`
- Modify: `packages/ui/src/operations-recovery-model.js`
- Create: `scripts/tests/windows-recovery-adapter.test.mjs`
- Modify: `scripts/tests/operations-recovery.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`
- Create/append: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10_completion_report.md`

허용 경로 밖 수정, Rust/API/OpenAPI/Cargo/Lock/CSP 설정 변경, 사용자 삭제 31건 복원, 사용자 미추적 문서 3건 Stage를 금지한다.

## 4. 기존 기능·Dirty 보호

- 착수 시 `git rev-parse --show-toplevel`, Branch, origin, HEAD, `git status --short`를 기록한다.
- 사용자 기존 삭제 31건과 미추적 문서 3건을 그대로 보존하며 이 작업의 실패로 오인하지 않는다.
- Web Adapter와 Windows Adapter를 호출 주체 기준으로 분리한다. 공용 UI에 내부 주소나 Tauri 전용 구현을 직접 넣지 않는다.
- 기존 Prototype Fixture·Preview는 실제 Adapter 미연결 시 성공으로 보이지 않아야 한다. 실제 API 상태와 Prototype 상태를 명확히 분리한다.
- 요구되지 않은 리팩터링·전체 재작성·의존성·설정 변경을 금지한다.

## 5. 필수 검증

```powershell
node --test scripts/tests/windows-recovery-adapter.test.mjs scripts/tests/operations-recovery.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
node --check apps/desktop/src/windows-recovery-adapter.js
node --check apps/desktop/src/native-session-bridge.js
npm run verify:desktop-lint
npm run verify:workspace
git diff --check
```

- 테스트는 실제 실행 기반으로 Session→Adapter→Pane 순서, unmount/cancel, Cloud 7·Local 3 Mapping, Safe Error, 권한 Fail-close를 검증한다. 문자열 Regex만으로 완료하지 않는다.
- 정적 Scan으로 Desktop/공용 UI Browser 코드의 `fetch|XMLHttpRequest|WebSocket|localhost|127.0.0.1|NEXT_PUBLIC_` 0건을 확인하되 테스트 Fixture와 계약 문구는 제품 코드와 분리한다.
- 기존 사용자 삭제 Web Route로 인한 선행 실패가 있으면 명령·실패 파일·허용 범위 밖임을 증거로 분리하고, 이번 변경 관련 테스트는 모두 통과해야 한다.

## 6. 진행·결과 계약

- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10_progress.md`.
- 착수, RED, 각 구현 단계, 오류·원인·복구, 각 검증, 종료 직전에 시각·상태·변경 파일·명령·결과·다음 작업을 즉시 기록한다.
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10_completion_report.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
- `COMPLETED`는 허용 구현·필수 검증·Dirty 보존 증거가 모두 있을 때만 사용한다. 공식 `FAILURE_REPORT` 또는 `INCOMPLETE`면 issue_id와 재현 증거를 포함한다.
- Commit·Push·배포·Browser·실제 Restore는 어울1 소유이므로 수행하지 않는다.
