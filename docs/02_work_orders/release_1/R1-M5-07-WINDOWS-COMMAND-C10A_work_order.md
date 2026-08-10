# R1-M5-07 Windows Native Recovery Command C10A 작업지시서

## 1. 승인 기준과 Writer

- Work Order ID: `R1-M5-07-WINDOWS-COMMAND-C10A`; Issue ID: `R1-M5-07-WINDOWS-COMMAND-C10A-I001`.
- 상태: `READY` · 2026-08-11 · 신산님 공개 Tauri Command·보안 경계 추가 승인.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; 착수 기준은 최신 `origin/master` SHA를 기록한다.
- 신산님이 `master` 단독 작업을 명시했으므로 Branch·Worktree를 생성하지 않는다. 어울2가 이 범위의 유일 Writer다.
- `AGENTS.md`, 상세 설계 0.9, Release 1 구현계획, 테스트계획, `docs/superpowers/specs/2026-08-10-windows-recovery-adapter-design.md` 1.2, `docs/superpowers/plans/2026-08-10-windows-recovery-native-bridge.md` Task 4.5·Global Constraints·Completion Contract, C09-R02 완료보고를 EOF까지 읽고 SHA와 적용 조항을 진행 기록에 남긴다.

## 2. 단일 목표

- 목표: 기존 Rust CloudRecoveryPort 7종과 LocalRecoveryPort 3종을 Windows React가 호출할 수 있도록 전용 Tauri Command 10개와 앱 수명주기 `NativeRecoveryRuntime`을 구현한다.
- 범용 `method/path/body`, Gateway 선택, Authorization 입력 Command를 만들지 않는다.
- Credential·Gateway·Loopback Port·App Instance Secret은 Rust 밖으로 반환하지 않고 Safe Projection/Error만 직렬화한다.
- 이 작업은 React Adapter·화면 연결·설치형 검증을 수행하지 않는다. 완료 후 C10이 재개한다.

## 3. 허용 구현 계약

### 3.1 전용 Command Allowlist

- `recovery_cloud_create_backup`
- `recovery_cloud_list_backups`
- `recovery_cloud_get_backup`
- `recovery_cloud_preview_restore`
- `recovery_cloud_get_restore`
- `recovery_cloud_execute_restore`
- `recovery_cloud_cancel_restore`
- `recovery_local_start_scan`
- `recovery_local_get_job`
- `recovery_local_repair_job`

추가·별칭·범용 Recovery Command는 금지한다.

### 3.2 상태와 데이터 경계

- `NativeRecoveryRuntime`은 고정 `NativeCloudRecoveryClient`와 Idempotency·Step-up SHA-256 Digest 제한 LRU를 앱 수명주기 동안 소유한다.
- 기존 `NativeSessionRuntime`에서만 Cloud Credential을 읽고 기존 `LocalServiceManager`에서만 Loopback Context를 읽는다.
- Command별 `#[serde(deny_unknown_fields)]` 입력 DTO가 사용자 입력을 받으며, 승인 Method·Path·Query·Body·Header 의미는 Rust가 내부 조립한다.
- 각 호출은 현재 Session·Workspace·Local readiness를 재검증한다. Cloud Session 없음은 `AUTHENTICATION_REQUIRED`, Local 미준비는 `LOCAL_SERVICE_UNAVAILABLE`이다.
- Cloud Write 자동 재실행, Step-up 재사용, Idempotency 재사용, ETag 누락·불일치, unknown field/code는 Fail-close한다.
- Safe 반환은 기존 `CloudRecoveryProjection`, `LocalRecoveryJob`, `LocalRecoveryError` 직렬화 계약을 재사용하고 비밀·내부 Context를 추가하지 않는다.

### 3.3 허용 변경 경로

- `apps/desktop/src-tauri/src/recovery_bridge.rs`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- `scripts/tests/desktop-recovery-command-surface.test.mjs` — 신규
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A_progress.md` — 신규·단계별 갱신
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A_completion_report.md` — 신규

Cargo/Lock/API/OpenAPI/Web/React/CSP/설정과 허용 경로 밖 수정은 금지한다. 새 의존성이 필요하면 구현하지 말고 `BLOCKED`로 보고한다.

## 4. TDD·필수 검증

1. RED: Command 0개, 범용 Method·Path 주입, unknown input, 소비 이력 초기화, Credential·주소·Token 반환 가능성을 실제 행동 또는 컴파일 계약으로 먼저 실패시킨다.
2. GREEN: `NativeRecoveryRuntime`과 10개 전용 Command만 최소 구현한다.
3. 실제 Rust 행동 테스트로 동일 Runtime의 Idempotency·Step-up 재사용 차단, Session 없음, Local 미준비, Safe DTO/Error, Cloud 7·Local 3 정확한 매핑을 검증한다.
4. 정적 Node 검사는 `generate_handler!`의 exact Command 집합·State 관리·금지 범용 Command·비밀 반환 패턴을 보조 검증한다. Regex만으로 완료하지 않는다.
5. 다음을 실행한다.

```powershell
node --test scripts/tests/desktop-recovery-command-surface.test.mjs
node scripts/run-isolated-desktop-cargo.mjs test
npm run verify:desktop-lint
git diff --check
```

- 사용자 삭제 31건과 원 미추적 문서 3건을 보존한다.
- 기존 C10 Progress의 BLOCKED 기록은 보존하고 이 작업 완료로 소급 변경하지 않는다.
- Commit·Push·배포·Browser·실제 Restore는 어울1 소유이므로 수행하지 않는다.

## 5. 진행·결과 계약

- 진행 기록: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A_progress.md`.
- 착수, RED, 구현 단계, 오류·원인·복구, 검증, 종료 직전에 시각·상태·변경 파일·명령·결과·다음 작업을 기록한다.
- 완료 보고: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-COMMAND-C10A_completion_report.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
- `COMPLETED`는 전용 Command 10개·상태 지속·Safe 경계·필수 검증·Dirty 보존 근거가 모두 있을 때만 사용한다.
