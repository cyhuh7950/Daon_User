# Provider 연결 상태 주기 점검 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 시스템 설정에 저장한 60분 기본 주기로 Active Provider 연결만 상태 확인하는 기능을 추가한다.

**Architecture:** PostgreSQL에 시스템 전역 점검 주기를 저장하고 관리자 same-origin API로 조회·수정한다. API lifespan이 주기 루프를 실행하며, 각 활성 연결은 기존 Adapter의 `verify`를 사용해 독립적으로 확인한다. UI는 관리자 화면에 주기 설정과 마지막 확인 상태를 표시한다.

**Tech Stack:** Python 3.14, FastAPI, PostgreSQL/Alembic, React/Next.js, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-19-provider-health-monitor-design.md`

## Global Constraints

- 기본 주기는 60분이다.
- `system_provider_connections.enabled = true`인 연결만 외부 확인한다.
- 모델 카탈로그·모델 기본값·Credential은 자동 변경하지 않는다.
- 브라우저는 same-origin BFF 경로만 사용한다.
- Credential 원문과 Provider 응답 원문은 로그·응답·감사 데이터에 기록하지 않는다.
- 기존 사용자 수정 파일 `apps/web/components/admin-user-console.jsx`는 변경·스테이징하지 않는다.
- 테스트용 데이터와 계정은 완료 후 제거하고 잔여 0을 확인한다.

## Review Focus

- 비활성 연결이 외부 호출되지 않는지 — Task 2의 선택 대상 테스트.
- 한 Provider 실패가 다른 Active Provider 점검을 중단하지 않는지 — Task 2의 격리 테스트.
- 1분 미만·24시간 초과·소수가 거부되는지 — Task 1의 설정 검증 테스트.
- API 종료 시 점검 task가 취소되고 중복 루프가 생기지 않는지 — Task 3의 lifespan 테스트.
- 관리자 외 요청과 브라우저 절대 URL이 차단되는지 — Task 1·4의 API/BFF 테스트.

### Task 1: 점검 주기 저장소와 관리자 API

**Files:**
- Create: `services/api/migrations/versions/0041_provider_health_check_settings.py`
- Create: `services/api/src/daon_user_api/provider_health_settings.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Test: `services/api/tests/test_provider_health_settings.py`

**Interfaces:**
- `ProviderHealthCheckSettingsService.get(context) -> ProviderHealthCheckSettings`
- `ProviderHealthCheckSettingsService.save(context, interval_minutes, expected_version) -> ProviderHealthCheckSettings`
- `ProviderHealthCheckSettings(interval_minutes: int, version: int)`

- [ ] Write tests for default 60, valid range, invalid values, and optimistic version conflict.
- [ ] Run the focused tests and verify they fail because the service and table do not exist.
- [ ] Add migration table with one system row, integer-minute validation, version and audit metadata.
- [ ] Add repository/service validation for 1–1440 minutes and safe error codes.
- [ ] Add authenticated system-admin GET/PATCH endpoints and public error mapping.
- [ ] Add BFF GET/PATCH routes under `/bff/api/admin/provider-health-settings`.
- [ ] Run focused API and migration tests; commit the task.

### Task 2: Active-only provider monitor

**Files:**
- Create: `services/api/src/daon_user_api/provider_health_monitor.py`
- Modify: `services/api/src/daon_user_api/provider_connection_admin.py`
- Test: `services/api/tests/test_provider_health_monitor.py`

**Interfaces:**
- `ProviderHealthMonitor.run_once() -> tuple[ProviderHealthCheckResult, ...]`
- `ProviderHealthMonitor.run_forever(stop_event) -> None`
- `PostgresProviderConnectionService.check_active_connection(connection_id, context) -> dict`

- [ ] Write tests proving disabled connections are skipped, active connections are checked, and per-connection failures are isolated.
- [ ] Run the focused monitor tests and verify they fail.
- [ ] Add a service method that loads the encrypted Credential only for the selected connection, invokes the existing adapter verification, and updates only status/time.
- [ ] Add monitor selection query restricted to `enabled = true`.
- [ ] Add failure-safe status update without storing upstream body or Credential.
- [ ] Run focused monitor tests and commit the task.

### Task 3: Runtime lifecycle integration

**Files:**
- Modify: `services/api/src/daon_user_api/runtime.py`
- Test: `services/api/tests/test_runtime_provider_health_monitor.py`

- [ ] Write lifespan tests proving one monitor task starts, waits 60 minutes before the first run, reloads the saved interval between cycles, and cancels cleanly.
- [ ] Run the tests and verify they fail.
- [ ] Start one cancellable task from FastAPI lifespan only when the provider service and cloud store are available.
- [ ] Use the persisted interval for `asyncio.wait_for`/event wake-up and prevent task leakage on shutdown.
- [ ] Run runtime tests and API startup/health checks; commit the task.

### Task 4: Settings UI and browser contract

**Files:**
- Modify: `apps/web/components/provider-settings-workspace.jsx`
- Modify: `apps/web/lib/provider-settings-api.js`
- Modify: `apps/web/lib/bff-api-proxy.js`
- Test: `scripts/tests/provider-health-settings-ui-contract.test.mjs`

- [ ] Write UI contract tests for the 60-minute default, selectable interval, Active-only copy, same-origin API path, and no automatic model lookup.
- [ ] Run the test and verify it fails.
- [ ] Add a compact administrator-only interval control and save status beside the Provider connection heading.
- [ ] Display last-check state/time on each connection card without exposing credentials or upstream response data.
- [ ] Add BFF client methods and route mapping using relative paths only.
- [ ] Run UI tests and existing provider endpoint tests; commit the task.

### Task 5: Integrated verification and deployment

**Files:**
- Modify: `docs/04_test_reports/provider_health_monitor_progress.md`

- [ ] Run Python focused tests, UI tests, build/type checks, and `git diff --check`.
- [ ] Deploy the exact branch commit to the WSL validation checkout.
- [ ] Create a uniquely named temporary test connection/account only if required by the integration path.
- [ ] Verify setting save, Active-only selection, successful check, failure isolation, and 60-minute default in the deployed environment.
- [ ] Remove all temporary accounts, connections, idempotency/audit records, and files; verify zero remaining rows.
- [ ] Record exact commit, container health, HTTP result, tests, and unverified browser/provider scope.
- [ ] Commit the progress report and prepare the user-facing deployment report.
