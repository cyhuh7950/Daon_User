# Windows Recovery Native Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Windows 설치형이 WebView에 Credential·내부 주소를 노출하지 않고 별도 Native 로컬 로그인으로 Cloud Recovery 7종과 인증된 Local Recovery 3종을 실제 사용하게 한다.

**Architecture:** React는 Tauri `invoke`만 호출하고 Rust가 Native Session Vault, HTTPS Cloud Port, 인증된 Loopback Local Port를 소유한다. 기존 Web 로그인·same-origin BFF·CSP `connect-src 'none'`은 변경하지 않으며 Server와 Local Service가 권한·Step-up·경로 Allowlist를 최종 재검증한다.

**Tech Stack:** Python 3.14.3, FastAPI, Pydantic v2, OpenAPI 3.1, Rust 1.97.1, Tauri 2.11.4, `reqwest = 0.13.4` rustls, Windows Credential Manager, React 19.2.7, Node 24.18.0.

## Global Constraints

- 공식 작업공간 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`, Branch `master`만 사용한다.
- 승인 기준은 설계 개정본, 계획 1.8, Windows Recovery 설계 1.1, `R1-D028`, `APR-R1-M5-07-WINDOWS-NATIVE-LOGIN-20260810-01`이다.
- 기존 사용자 삭제 31건과 미추적 문서 3건을 복원·수정·Stage하지 않는다.
- 한 시점 한 Writer만 코드를 수정하고 모든 구현은 TDD RED→최소 GREEN→회귀 순서로 진행한다.
- 기존 `POST /api/v1/auth/login` Web Cookie 계약, Web same-origin BFF, CSP `connect-src 'none'`, Cloud 7개·Local 3개 Method/Path를 변경하지 않는다.
- Access·Refresh·Password·Authorization Header·Gateway URL·Loopback Port·Local Token은 JavaScript·Log·Evidence에 노출하지 않는다.
- 운영 Restore·파괴적 손상 주입·G9-DRILL 우회는 수행하지 않는다.

---

## Task 1: Native 로컬 로그인 Domain·Runtime·OpenAPI

**Files:**

- Modify: `services/api/src/daon_user_api/identity.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `packages/contracts/openapi/v1/openapi.json`
- Modify: `services/api/tests/test_identity_local_auth.py`
- Modify: `services/api/tests/test_runtime_http.py`
- Modify: `scripts/tests/openapi-contract.test.mjs`

- [ ] `IdentityService.local_login` Web 고정 동작 보존 테스트와 별도 Native 발급 RED 테스트를 추가한다. Native는 `DevicePlatform.WINDOWS`, `ClientKind.NATIVE`, 단일 사용 Refresh Family, `native_opaque_refresh_rotation`을 요구한다.
- [ ] Runtime RED에 `POST /api/v1/auth/native/login`, 요청 필드 `login_id`·`password`만 허용, `platform`·`client_kind` 거부, 성공 Cookie 0건, opaque Access·Refresh와 Safe Session Projection 반환, 잘못된 자격 401을 고정한다.
- [ ] `IdentityService.local_native_login(...)`을 최소 구현한다. 기존 Password 검증·Membership 조회·Audit를 재사용하되 Device는 `windows`, Session은 `native`, Refresh Family/Token은 OIDC Native와 같은 회전 계약으로 생성한다.
- [ ] Runtime에 별도 Body·Route를 추가한다. 기본 Workspace는 Web 로그인과 같은 Repository 규칙으로 결정하되 Client 입력은 받지 않는다.
- [ ] OpenAPI에 `NativeLocalLoginRequest`, `NativeCredentialSession`, Envelope와 Route를 추가한다. Credential 필드는 `writeOnly`, opaque 표현을 사용하고 예제·기본값을 넣지 않는다.
- [ ] 다음을 실행한다.

```powershell
uv run --project services/api python -m pytest services/api/tests/test_identity_local_auth.py services/api/tests/test_runtime_http.py -q
node --test scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
```

- [ ] 기존 Web 로그인 응답에 Token이 없고 Secure·HttpOnly Cookie가 유지되는지 회귀 증거를 남긴다.

## Task 2: Windows Native Session Vault와 HTTPS Client

**Files:**

- Modify: `apps/desktop/src-tauri/Cargo.toml`
- Modify: `apps/desktop/src-tauri/Cargo.lock`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Create: `apps/desktop/src-tauri/src/native_session.rs`
- Create: `apps/desktop/src-tauri/tests/native_session_contract.rs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`

- [ ] RED로 `DaonUser/NativeSession/v1`이 `DaonUser/LocalStorage/v1`과 분리되고 Debug·Safe DTO에 Credential 원문이 없으며 로그인 실패·Refresh 재사용에서 Vault가 폐기되는 계약을 고정한다.
- [ ] `reqwest = { version = "=0.13.4", default-features = false, features = ["json", "rustls"] }`를 정확히 Pin한다. 다른 HTTP/TLS 의존성을 임의 추가하지 않는다.
- [ ] `NativeSessionVault`는 Windows Credential Manager Generic Credential에 Access·Refresh·Session 만료 Projection을 저장·읽기·철회한다. 메모리 Secret은 Debug에서 Redact하고 Drop 시 가능한 버퍼를 지운다.
- [ ] `NativeIdentityClient`는 서명·패키징된 HTTPS Public Gateway만 허용하고 `/api/v1/auth/native/login`, `/api/v1/session/refresh`만 호출한다. HTTP·localhost·127.0.0.1·Docker Host·환경 변수 Gateway를 거부한다.
- [ ] Tauri `native_login`, `native_logout`, `native_session_status` Command를 추가한다. JavaScript 반환에는 로그인 상태, user/tenant/workspace/session/device와 Safe Error만 포함한다.
- [ ] Access 만료 시 Refresh는 최대 1회 회전하고 원 상태변경 요청은 자동 재실행하지 않는다. Refresh 실패·재사용·철회는 Vault 삭제 후 `AUTHENTICATION_REQUIRED`다.
- [ ] 다음을 실행한다.

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
node --test scripts/tests/desktop-tauri-shell.test.mjs
node scripts/lint-workspace.mjs apps/desktop/src/desktop-shell.jsx
```

## Task 3: Local Recovery Native Port

**Files:**

- Modify: `apps/desktop/src-tauri/src/local_service.rs`
- Create: `apps/desktop/src-tauri/src/recovery_bridge.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/desktop/src-tauri/tests/local_service_contract.rs`
- Create: `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`

- [ ] RED로 기존 Runtime/Storage Allowlist가 보존되고 `recovery.write/recovery.scan`, `recovery.read/recovery.job.read`, `recovery.write/recovery.repair` 세 쌍만 추가됨을 고정한다.
- [ ] `LocalServiceManager`의 준비된 동적 Loopback Context를 Rust 내부 Port가 사용하도록 최소 메서드를 추가하되 Port·Token·Root Secret은 공개 DTO로 반환하지 않는다.
- [ ] 정확히 `POST /local/v1/recovery/scans`, `GET /local/v1/recovery/jobs/{id}`, `POST /local/v1/recovery/jobs/{id}/repair`만 호출하고 ID·Body·응답 크기·Timeout을 제한한다.
- [ ] Local Service 미준비, 잘못된 Job ID, Capability 불일치, 위조 응답은 Cloud Fallback 없이 Safe Error로 닫는다.
- [ ] 다음을 실행한다.

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
uv run --project services/local-service python -m pytest services/local-service/tests/test_recovery.py -q
node --test scripts/tests/desktop-local-service.test.mjs
```

## Task 4: Cloud Recovery Native Port

**Files:**

- Modify: `apps/desktop/src-tauri/src/recovery_bridge.rs`
- Modify: `apps/desktop/src-tauri/src/native_session.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/desktop/src-tauri/tests/recovery_bridge_contract.rs`
- Modify: `apps/desktop/src-tauri/tests/native_session_contract.rs`
- Modify: `services/api/tests/test_recovery_runtime_http.py`

- [ ] Mock Transport RED로 다음 7개 계약만 허용한다: `POST /api/v1/backups`, `GET /api/v1/backups`, `GET /api/v1/backups/{id}`, `POST /api/v1/backups/{id}/restore-previews`, `GET /api/v1/restore-requests/{id}`, `POST /api/v1/restore-requests/{id}/execute`, `POST /api/v1/restore-requests/{id}/cancel`.
- [ ] `CloudRecoveryPort`가 Vault Access를 Authorization Bearer에 넣되 Header·Token·URL을 Debug·Error·DTO에 포함하지 않게 한다.
- [ ] 실제 코드 구조에 맞춰 `NativeSessionRuntime`이 Vault Credential을 외부 DTO로 반환하지 않고 Rust 내부의 승인된 Cloud Recovery 실행 경계에만 전달한다. Credential 원문을 반환하는 Public 함수·Tauri Command는 추가하지 않는다.
- [ ] Preview·Execute는 서로 다른 Step-up, Execute·Cancel은 `If-Match`, 모든 Write는 `Idempotency-Key`를 요구한다. 누락·재사용·ETag 충돌은 자동 재시도하지 않는다.
- [ ] Access 만료는 Task 2 Session 계층에서 1회 회전하되 상태 변경 요청은 새 Credential로 자동 재실행하지 않는다.
- [ ] 다음을 실행한다.

```powershell
node scripts/run-isolated-desktop-cargo.mjs test
uv run --project services/api python -m pytest services/api/tests/test_recovery_runtime_http.py services/api/tests/test_recovery_contract.py -q
```

## Task 5: Windows React Adapter와 운영 화면 연결

**Files:**

- Create: `apps/desktop/src/windows-recovery-adapter.js`
- Create: `apps/desktop/src/native-session-bridge.js`
- Modify: `apps/desktop/src/desktop-shell.jsx`
- Modify: `packages/ui/src/operations-recovery-pane.jsx`
- Modify: `packages/ui/src/operations-recovery-model.js`
- Create: `scripts/tests/windows-recovery-adapter.test.mjs`
- Modify: `scripts/tests/operations-recovery.test.mjs`
- Modify: `scripts/tests/desktop-tauri-shell.test.mjs`

- [ ] RED로 React가 fetch/XHR를 사용하지 않고 Tauri `invoke`만 호출하며 Adapter 부재·Session 부재·Local Service 미준비에서 Fixture 성공을 만들지 않는 계약을 고정한다.
- [ ] `WindowsRecoveryAdapter`에 Cloud 7개와 Local 3개 메서드를 구현하고 Safe DTO·Safe Error만 통과시킨다.
- [ ] `desktop-shell.jsx`가 Adapter를 한 번 생성하여 Operations 화면에 주입한다. 로그인 전 Cloud는 `AUTHENTICATION_REQUIRED`, Local 미준비는 `LOCAL_SERVICE_UNAVAILABLE`로 분리한다.
- [ ] 공용 UI에 Local Scan→Job 조회→Repair 상태 영역을 추가하되 Web 기존 Cloud 화면과 권한 Fail-close를 보존한다.
- [ ] CSP `connect-src 'none'`, 브라우저 절대주소·localhost·`NEXT_PUBLIC_*` 0건을 정적·실행 테스트로 확인한다.
- [ ] 다음을 실행한다.

```powershell
node --test scripts/tests/windows-recovery-adapter.test.mjs scripts/tests/operations-recovery.test.mjs scripts/tests/desktop-tauri-shell.test.mjs
node --check apps/desktop/src/windows-recovery-adapter.js
npm run verify:desktop-lint
npm run verify:workspace
```

## Task 6: 통합 회귀·설치형 실제 증거

**Files:**

- Create: `docs/03_evidence/release_1/R1-M5-07-WINDOWS-NATIVE-01/manifest.json`
- Create: `docs/03_evidence/release_1/R1-M5-07-WINDOWS-NATIVE-01/verification-summary.md`
- Create: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-01_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M5-07-WINDOWS-NATIVE-01_completion_report.md`

- [ ] API 전체 회귀, OpenAPI no-write, Desktop Node/Rust, Local Service Recovery, Workspace/Lint를 실행한다.
- [ ] `npm run build:desktop-installer`로 NSIS를 생성하고 설치·시작·로그인·Cloud 목록/Fixture Preview·Local Scan/Job/Repair·종료를 실제 Windows 화면에서 검증한다.
- [ ] 운영 데이터 Restore와 Execute는 수행하지 않는다. Preview도 승인된 전용 Fixture만 사용한다.
- [ ] Credential·내부 URL·Loopback Port Secret scan 0건, CSP 유지, Process·Port 잔여 0건, 실제 Trace/Audit 상관관계를 Evidence Manifest에 기록한다.
- [ ] 화면·API·설치형 증거가 없으면 자동 테스트 PASS와 Windows 제품 PASS를 분리해 `VERIFYING` 또는 `BLOCKED`로 보고한다.
- [ ] 다음 최종 회귀를 실행한다.

```powershell
uv run --project services/api python -m pytest services/api/tests -q
uv run --project services/local-service python -m pytest services/local-service/tests -q
node scripts/verify-openapi-contract.mjs
npm run verify:desktop-unit
npm run verify:desktop-type
npm run verify:workspace
npm run lint:workspace
npm run build:desktop-installer
git diff --check
```

## Completion Contract

- Native 로그인 API, Rust Session Vault, Cloud 7·Local 3 Port, React Adapter가 실제 계약으로 연결되어야 한다.
- Web 로그인·Web Recovery·기존 Local Service Lifecycle 회귀가 통과해야 한다.
- Credential·내부 주소의 JavaScript·Log·Evidence 노출은 0건이어야 한다.
- 자동 테스트와 설치형 실제 검증을 구분하고 실제 설치형 검증 전에는 R1-WIN-01 또는 M5 Exit PASS를 주장하지 않는다.
- 어울2는 각 단계와 오류·복구·테스트 결과를 지정 Progress 파일에 즉시 기록하고 `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나로 결과를 제출한다.
