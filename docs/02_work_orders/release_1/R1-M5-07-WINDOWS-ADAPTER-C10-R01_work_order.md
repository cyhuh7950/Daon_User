# R1-M5-07 Windows React Recovery Adapter C10-R01 수정 작업지시서

## 1. 판정과 기준

- Work Order ID: `R1-M5-07-WINDOWS-ADAPTER-C10-R01`; Issue ID: `R1-M5-07-WINDOWS-ADAPTER-C10-I001`.
- 상태: `READY` · 2026-08-11 · C10 내부 독립 검토 Important 4건 및 신산님 권한 Projection·로그인 UI 승인 반영.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; Branch·Worktree를 생성하지 않는다.
- 원 C10 변경·Progress·Completion, 승인된 C10B/R01 변경과 사용자 Dirty를 보존하고 동일 C10 어울2가 단일 Writer로 재작업한다.
- `AGENTS.md`, 설계 1.3 §5.1·§5.3.2·§5.5·§7·§9, Plan Task 5, C10B/R01 Completion, C10 최신 독립 검토를 EOF까지 읽고 새 Progress에 근거를 기록한다.

## 2. 수정 목표

1. Native Session과 Recovery Authorization 응답을 Rust 정본과 동일한 exact-key DTO로 검증하고 Unknown·Credential 유사 필드를 Fail-close한다.
2. Windows Adapter는 승인된 Rust Safe Error를 손실 없이 보존하고 신규 JS 공개 Error code를 만들지 않는다.
3. Cloud 버튼과 Handler는 최신 `recovery_operations`를 이중 확인하며 미허용 Operation의 Recovery Tauri invoke를 0건으로 만든다.
4. 최초 설치 사용자가 실제 `native_login`·`native_logout`을 실행할 Windows UI를 제공하고 성공 시 최신 권한 Projection으로 Operations Tree를 재생성한다.

## 3. 구현 계약

### 3.1 Session·Authorization strict Projection

- `native_session_status/login/logout` 응답 outer key는 정확히 `authenticated`, `session` 두 개다.
- unauthenticated는 `authenticated:false`, `session:null`만 허용한다.
- authenticated session key는 정확히 `user_id`, `tenant_id`, `workspace_id`, `session_id`, `device_id`, `expires_at` 여섯 개다.
- `native_recovery_authorization_status` 응답은 정확히 `recovery_operations` 하나이고 값은 `[]` 또는 정확히 정렬된 Cloud 7종 전체다.
- Unknown key, Access/Refresh/Password/Authorization/Gateway/Role/Permission 유사 key, 잘못된 타입·문자열은 `AUTHENTICATION_REQUIRED`로 닫고 Callback·State·UI에 전달하지 않는다.

### 3.2 Safe Error

- Cloud Rust Safe Error `FORBIDDEN`, `CURRENT_ACCESS_DENIED`, `STEP_UP_REQUIRED`, `INVALID_REQUEST`, `RESOURCE_UNAVAILABLE`, `CONFLICT`, `NOT_FOUND`와 기존 `AUTHENTICATION_REQUIRED`, `CLOUD_RECOVERY_*`, Restore/ETag Safe Error를 정확히 보존한다.
- Local Rust Safe Error는 기존 `LOCAL_SERVICE_UNAVAILABLE`, `LOCAL_COMMAND_NOT_ALLOWED`, `LOCAL_RECOVERY_*`만 보존한다.
- JS 입력 오류는 승인된 `INVALID_REQUEST` 또는 기존 Local 입력 오류로, JS 응답 검증 실패는 기존 `CLOUD_RECOVERY_RESPONSE_REJECTED` 또는 `LOCAL_RECOVERY_RESPONSE_REJECTED`로 닫는다.
- `RECOVERY_INPUT_INVALID`, `RECOVERY_RESPONSE_REJECTED`, `RECOVERY_COMMAND_FAILED` 같은 신규 범용 공개 코드를 제거한다.
- Unknown Rust code·잘못된 Trace는 승인된 Response Rejected로 닫고 내부 메시지·URL·Stack을 반사하지 않는다.

### 3.3 권한·UI

- Desktop Shell은 singleton Session Bridge·Recovery Adapter를 유지하고, 로그인 성공·Session ID 변경 때 최신 Recovery Authorization을 조회해 Tree를 재생성한다.
- Cloud 각 Operation은 설계 7종 Operation과 정확히 매핑한다. 버튼 disabled/hidden과 Handler 진입 시 모두 확인한다.
- 미인증·권한 빈 목록·조회 실패·Workspace/Session 전환 중에는 Cloud Recovery Tauri invoke가 0건이다.
- Preview·Execute는 해당 Operation 권한과 Step-up 입력을 모두 요구한다. 자동 Backup·Restore·Cancel은 금지한다.
- Local 3종은 Cloud 권한 목록에 의존하지 않고 기존 Local Service readiness·Safe Error·명시 클릭 계약을 유지한다.

### 3.4 Native 로그인·로그아웃 UI

- Windows 제품 화면에 Login ID·Password 입력, 로그인·로그아웃·현재 인증 상태를 제공한다.
- Password는 uncontrolled input에서 제출할 때만 읽고 즉시 input을 비우며 React State·Storage·Log·Error·DOM 재표시에 보존하지 않는다.
- Login 실패는 `AUTHENTICATION_REQUIRED`, Cloud Recovery invoke 0건이며 Password input은 비워진다.
- Login 성공은 Safe Session만 표시하고 최신 권한을 조회한 뒤 Operations를 재생성한다. Logout은 Safe Session·권한·화면 결과를 즉시 제거한다.
- 기존 Web 가입·로그인 화면과 Web same-origin 계약은 수정하지 않는다.

## 4. 허용 변경 경로

- `apps/desktop/src/windows-recovery-adapter.js`
- `apps/desktop/src/native-session-bridge.js`
- `apps/desktop/src/native-auth-panel.jsx` — 신규 허용
- `apps/desktop/src/desktop-shell.jsx`
- `packages/ui/src/operations-recovery-pane.jsx`
- `packages/ui/src/operations-recovery-model.js` — 필요한 권한 상태 모델만
- `scripts/tests/windows-recovery-adapter.test.mjs`
- `scripts/tests/operations-recovery.test.mjs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R01_progress.md` — 신규
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R01_completion_report.md` — 신규
- 원 C10 Completion은 소급 수정하지 않는다.

API/OpenAPI/Rust/Cargo/Lock/DB/CSP/환경 설정과 허용 경로 밖 수정은 금지한다. 새 Command·Error·Endpoint·권한 Operation이 필요하면 `BLOCKED`로 보고한다.

## 5. TDD·필수 검증

1. RED: Session outer/session Unknown·Credential 필드가 성공하는 현재 동작을 실패시킨다.
2. RED: Rust Safe Error 7종 훼손과 신규 JS 범용 Error를 실패시킨다.
3. RED: 무권한·Projection 실패에서 Cloud 버튼 Handler가 invoke하는 현재 동작을 실패시킨다.
4. RED: 최초 실행 Login/Logout UI 부재, 실패 후 Password 잔존·Cloud invoke 가능성을 실패시킨다.
5. GREEN: 최소 strict Projection·Safe Error·권한 이중 차단·로그인 UI만 구현한다.
6. 다음을 fresh 실행한다.

```powershell
node --test scripts/tests/windows-recovery-adapter.test.mjs scripts/tests/operations-recovery.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
node --check apps/desktop/src/windows-recovery-adapter.js
node --check apps/desktop/src/native-session-bridge.js
npm run verify:desktop-lint
npm run verify:workspace
git diff --check
```

- 실행 기반으로 무권한·로그인 실패·Projection 실패 Cloud Recovery invoke 0건, 로그인 성공 remount, Logout 정리를 검증한다. Regex만으로 완료하지 않는다.
- Network/Secret/Internal Context 금지 scan, C10B 변경, 사용자 삭제 31건·원 미추적 문서 3건을 보존한다.
- Commit·Push·배포·Browser·설치·실제 Login/Recovery는 수행하지 않는다.

## 6. 결과 계약

- Progress: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R01_progress.md`.
- Completion: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-ADAPTER-C10-R01_completion_report.md`.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
