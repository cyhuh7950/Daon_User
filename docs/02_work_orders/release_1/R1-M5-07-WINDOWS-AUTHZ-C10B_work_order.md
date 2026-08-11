# R1-M5-07 Windows Recovery 권한 Projection C10B 작업지시서

## 1. 승인 기준과 Writer

- Work Order ID: `R1-M5-07-WINDOWS-AUTHZ-C10B`; Issue ID: `R1-M5-07-WINDOWS-AUTHZ-C10B-I001`.
- 상태: `READY` · 2026-08-11 · 신산님 Recovery 최소 권한 Projection·Windows 로그인 UI 승인.
- 공식 작업공간: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`; Branch: `master`; 착수 기준은 최신 `origin/master` SHA다.
- Branch·Worktree를 생성하지 않는다. 어울2 한 명이 이 범위의 유일 Writer다.
- `AGENTS.md`, 상세 설계 0.9, Release 1 구현계획, Windows Recovery 설계 1.3 §5.3.2·§5.5·§7·§9, Native Bridge Plan Task 4.6·Global Constraints·Completion Contract, C10 독립 검토를 EOF까지 읽고 SHA와 적용 조항을 Progress에 기록한다.
- C10의 미커밋 React Adapter 변경과 사용자 삭제 31건·원 미추적 문서 3건을 보존한다. C10B는 허용된 API·OpenAPI·Rust·계약 테스트만 수정한다.

## 2. 단일 목표

- 서버의 현재 Workspace `Action.POLICY_MANAGE` 판정을 Cloud Recovery 7종 최소 Operation 목록으로만 안전하게 투영한다.
- Rust는 Vault Credential을 JavaScript에 반환하지 않고 현재 `GET /api/v1/session`을 조회하는 읽기 전용 `native_recovery_authorization_status` Command를 제공한다.
- Projection은 Vault에 저장하지 않으며 실패·거부·Unknown·Session/Workspace 불일치에서 빈 목록으로 Fail-close한다.
- 이 작업은 React 버튼·로그인 UI·Local 3종 UI를 수정하지 않는다. C10-R01이 후속 연결한다.

## 3. 계약

### 3.1 API Safe Projection

- 기존 `GET /api/v1/session` Safe Session 응답에 `recovery_operations`만 추가한다.
- 허용 값은 다음 7개 정렬·중복 없는 문자열뿐이다.
  - `cloud_backup_create`
  - `cloud_backup_list`
  - `cloud_backup_get`
  - `cloud_restore_preview`
  - `cloud_restore_get`
  - `cloud_restore_execute`
  - `cloud_restore_cancel`
- 현재 Principal·Workspace의 `Action.POLICY_MANAGE` 허용 시 전체 7개, `ACTION_DENIED` 시 빈 배열을 반환한다.
- 역할, Role scope, Effective Permission, 정책 거부 사유, Credential은 반환하지 않는다. 인증·저장소·감사 오류를 권한 거부로 축약하지 않는다.
- 기존 Web Cookie와 Native login/refresh Credential 계약은 변경하지 않는다.

### 3.2 Rust 읽기 전용 경계

- `native_recovery_authorization_status`는 입력을 받지 않는다.
- Vault Access는 Rust 내부에서만 `GET /api/v1/session` Bearer에 사용하고, 고정 Public Gateway·Path만 호출한다.
- 응답의 user/tenant/workspace/session/device/client_kind와 현재 Vault Safe Session이 정확히 일치해야 한다.
- Operation 집합은 exact allowlist·정렬·중복 없음이어야 한다. Unknown field·Operation·내부 URL·Credential 유사 필드는 거부한다.
- Projection을 `DaonUser/NativeSession/v1` Vault JSON 또는 다른 지속 저장소에 추가하지 않는다.
- 실패 시 이전 성공 Projection을 재사용하지 않고 Safe Error와 빈 목록으로 닫는다.

## 4. 허용 변경 경로

- `services/api/src/daon_user_api/runtime.py`
- `services/api/tests/test_runtime_http.py`
- `services/api/tests/test_identity_local_auth.py`
- `packages/contracts/openapi/v1/openapi.json`
- `scripts/tests/openapi-contract.test.mjs`
- `scripts/verify-openapi-contract.mjs`
- `docs/03_evidence/release_1/R1-M5-07/openapi-contract-summary.json` — 승인 OpenAPI 변경의 검증기 파생 요약만 갱신
- `apps/desktop/src-tauri/src/native_session.rs`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/tests/native_session_contract.rs`
- `scripts/tests/desktop-tauri-shell.test.mjs`
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B_progress.md` — 신규
- `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B_completion_report.md` — 신규

DB Migration, Cargo/Lock, Recovery Port, React/UI/CSS/CSP/환경 설정과 허용 경로 밖 수정은 금지한다. 새 의존성·Endpoint·Role·Permission이 필요하면 `BLOCKED`로 보고한다.

## 5. TDD·필수 검증

1. API RED: Projection 부재, 권한 거부, 역할·Permission 노출, Workspace 불일치를 먼저 실패시킨다.
2. API GREEN: 기존 AuthorizationService·Session Route를 최소 확장하고 OpenAPI를 동기화한다.
3. Rust RED: Command 부재, Vault 저장, Unknown·불일치·오류에서 과거 권한 재사용을 먼저 실패시킨다.
4. Rust GREEN: 고정 읽기 전용 조회·strict Safe Projection·빈 목록 Fail-close를 최소 구현한다.
5. 다음을 fresh 실행한다.

```powershell
uv run --project services/api python -m pytest services/api/tests/test_runtime_http.py services/api/tests/test_identity_local_auth.py -q
node --test scripts/tests/openapi-contract.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
node scripts/verify-openapi-contract.mjs
node scripts/run-isolated-desktop-cargo.mjs test
git diff --check
```

- 허용 Rust rustfmt-check, Secret·내부 주소·Role/Permission 노출 scan, `gen`과 Cargo/Rustc 잔존 0건을 확인한다.
- C10 미커밋 변경, 사용자 삭제 31건과 원 미추적 문서 3건을 보존한다.
- Commit·Push·배포·Browser·실제 Login/Recovery는 수행하지 않는다.

## 6. 진행·결과 계약

- Progress: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B_progress.md`.
- Completion: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-AUTHZ-C10B_completion_report.md`.
- 착수·RED·구현·오류/복구·검증·종료 직전에 시각·상태·변경·명령·결과·다음을 기록한다.
- 결과는 `status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단` 형식으로 반환한다.
