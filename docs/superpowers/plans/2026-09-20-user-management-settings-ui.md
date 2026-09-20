# 사용자 관리·설정 화면 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 설정 복귀 동선, Provider 카드 대비, 사용자 관리 테이블·수정 모달과 관리자용 안전한 비밀번호 초기화를 구현한다.

**Architecture:** 기존 same-origin BFF를 유지하고, 관리자 비밀번호 초기화는 API의 관리자 전용 명령으로 연결한다. 기존 password-reset token/email 발송 계약을 재사용하며 임시 비밀번호·토큰은 반환하지 않는다. Web은 기존 `admin-user-console`의 세션 보호와 API 헬퍼를 확장하고, 기존 디자인 토큰·CSS 경계를 따른다.

**Tech Stack:** Next.js/React, plain CSS, FastAPI, SQLite/PostgreSQL identity repository, pytest, Node test scripts.

**Spec:** `docs/superpowers/specs/2026-09-20-user-management-and-settings-ui-design.md`

## Global Constraints

- 브라우저 API 호출은 same-origin 상대 경로와 BFF를 사용한다.
- 보호된 `admin` 계정과 자기 자신은 일반 관리자 reset 대상으로 허용하지 않는다.
- 비밀번호·hash·reset token·자격증명은 화면·로그·응답에 노출하지 않는다.
- 본문·폼은 최소 12px, 보조 설명은 10px 이상, 제목은 16px 이상으로 유지한다.
- 본문 대비는 WCAG AA 4.5:1 이상을 목표로 하고 focus-visible·label·status/alert를 제공한다.
- 기존 dirty/untracked 파일과 기존 API 의미를 변경하지 않는다.

## Review Focus

- 보호 계정 또는 자기 자신을 reset하면 안전한 오류가 반환되는지 — Task 1 API 계약 테스트.
- 대상 사용자가 이메일 없는 상태일 때 메일 발송 없이 거부되는지 — Task 1 서비스 테스트.
- reset 요청이 재전송되어도 idempotency와 세션/refresh 폐기가 일관적인지 — Task 1 서비스·runtime 테스트.
- API가 반환한 역할/상태 조합과 모바일 화면이 긴 이메일·빈 결과를 깨뜨리지 않는지 — Task 3 UI 테스트.
- 선택되지 않은 Provider 카드와 keyboard focus가 밝은 배경에서 읽히는지 — Task 4 UI 정적/브라우저 테스트.

---

### Task 1: 관리자 비밀번호 초기화 API 계약

**Files:**
- Modify: `services/api/src/daon_user_api/admin_users.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `services/api/src/daon_user_api/identity.py`
- Test: `services/api/tests/test_admin_users.py`
- Test: `services/api/tests/test_admin_users_runtime.py`

**Interfaces:**
- Consumes: `IdentityPrincipal`, `AdminUserService`, 기존 `IdentityService.request_password_reset()` 및 audit/transaction 경계.
- Produces: `AdminUserService.request_password_reset(...)`와 `POST /api/v1/admin/users/{user_id}/password-reset` 계약. 성공 응답은 `status`와 `replayed`만 포함한다.

- [ ] **Step 1: Write the failing service and runtime tests**

  Add tests proving that a system admin can request reset for an active local user with verified email, while protected admin, actor self, missing email, non-admin, and non-active targets are rejected. Assert that success does not contain a password/token and that the reset email sender is invoked through the existing reset contract.

  Add the route test with `x-daon-bff-transport: internal`, `Idempotency-Key`, and a session principal. Assert exact envelope fields and safe error codes.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run:

  ```powershell
  python -m pytest services/api/tests/test_admin_users.py services/api/tests/test_admin_users_runtime.py -q
  ```

  Expected: the new tests fail because the service method and route do not exist.

- [ ] **Step 3: Implement the minimal reset command**

  Add a service method that validates admin principal, target existence, active local state, email presence, protected/self restrictions, and idempotency scope. Call the existing identity reset-request logic with the target login/email, preserve the existing mail rate limit, write the matching audit event, and return only status/replayed data. Add the internal FastAPI route and safe error mapping without returning the reset token.

- [ ] **Step 4: Run the focused tests and verify GREEN**

  Run the same pytest command. Expected: all focused admin service/runtime tests pass with no secret-like values in output.

- [ ] **Step 5: Commit the API contract**

  ```powershell
  git add services/api/src/daon_user_api/admin_users.py services/api/src/daon_user_api/runtime.py services/api/src/daon_user_api/identity.py services/api/tests/test_admin_users.py services/api/tests/test_admin_users_runtime.py
  git commit -m "feat: add protected admin user password reset request"
  ```

### Task 2: Web BFF and API client

**Files:**
- Modify: `apps/web/lib/bff-api-proxy.js`
- Modify: `apps/web/lib/admin-users-api.js`
- Test: `scripts/tests/admin-users-bff.test.mjs`
- Test: `scripts/tests/admin-user-management-react.test.mjs`

**Interfaces:**
- Consumes: `POST /api/v1/admin/users/{user_id}/password-reset` from Task 1.
- Produces: `requestAdminUserPasswordReset(userId, options)` calling `/bff/api/admin/users/{userId}/password-reset`, validating `{data:{status,replayed},meta:{trace_id}}`.

- [ ] **Step 1: Write failing BFF/client tests**

  Add route mapping coverage for the new admin path, reject non-POST methods, and add client coverage for same-origin URL, credentials, idempotency header, exact response validation, and safe API error propagation.

- [ ] **Step 2: Run the focused Node tests and verify RED**

  ```powershell
  node --test scripts/tests/admin-users-bff.test.mjs scripts/tests/admin-user-management-react.test.mjs
  ```

  Expected: failures for the missing route mapping and missing client export.

- [ ] **Step 3: Implement the route mapping and client helper**

  Extend the existing admin-user path branch in `bff-api-proxy.js`. Add `requestAdminUserPasswordReset` beside the other admin-user mutations; validate only the safe user id and idempotency key, send `{}` as the body, and reject any response containing fields other than status/replayed in the data envelope.

- [ ] **Step 4: Run the focused Node tests and verify GREEN**

  Re-run the command from Step 2 and confirm all tests pass.

- [ ] **Step 5: Commit the BFF/client contract**

  ```powershell
  git add apps/web/lib/bff-api-proxy.js apps/web/lib/admin-users-api.js scripts/tests/admin-users-bff.test.mjs scripts/tests/admin-user-management-react.test.mjs
  git commit -m "feat: expose admin password reset through same-origin bff"
  ```

### Task 3: 사용자 관리 테이블·수정·초기화 UI

**Files:**
- Modify: `apps/web/components/admin-user-console.jsx`
- Modify: `apps/web/lib/admin-users-api.js`
- Modify: `apps/web/app/globals.css`
- Test: `scripts/tests/admin-user-management-react.test.mjs`
- Test: `scripts/tests/admin-user-management-ui.test.mjs`

**Interfaces:**
- Consumes: list/update/delete/state/approve APIs and `requestAdminUserPasswordReset` from Task 2.
- Produces: table columns, search/status controls, selection actions, edit modal with status/role controls, and per-row `비밀번호 초기화` action with success/error status.

- [ ] **Step 1: Write failing UI tests**

  Assert the table headers, result count, select-all/selected count, refresh and selection-delete controls, role/status edit controls, reset action, protected admin restrictions, and no password/token text in rendered output. Keep existing session redirect and protected content behavior covered.

- [ ] **Step 2: Run focused UI tests and verify RED**

  ```powershell
  node --test scripts/tests/admin-user-management-react.test.mjs scripts/tests/admin-user-management-ui.test.mjs
  ```

  Expected: failures because the current component renders cards and has no role/reset controls.

- [ ] **Step 3: Implement the minimal table and modal behavior**

  Replace only the list rendering with semantic table markup. Preserve current loading/error/session behavior. Add selection state keyed by `user_id`, search/filter result count, refresh, guarded bulk deletion, edit form state for status/roles, and row reset action. Keep `admin` protected and disable its mutation actions. Use `role="status"`/`role="alert"`, labels, focusable buttons, and a modal close path without exposing secret values.

- [ ] **Step 4: Update CSS for the approved viewport and type scale**

  Move the page from compact card-only rules to a readable table layout. Use existing tokens, `font-size: 12px` for body/form, `10px` only for metadata, `16px` for page headings, line-height at least 1.5, visible `:focus-visible`, horizontal overflow only inside the table region on narrow screens, and high-contrast status/action buttons.

- [ ] **Step 5: Run focused UI tests and verify GREEN**

  Re-run the two Node test files and record any pre-existing unrelated failures separately.

- [ ] **Step 6: Commit the user-management UI**

  ```powershell
  git add apps/web/components/admin-user-console.jsx apps/web/lib/admin-users-api.js apps/web/app/globals.css scripts/tests/admin-user-management-react.test.mjs scripts/tests/admin-user-management-ui.test.mjs
  git commit -m "feat: redesign admin user management table and reset flow"
  ```

### Task 4: Settings navigation and Provider contrast

**Files:**
- Modify: `apps/web/app/settings/model-connections/page.jsx`
- Modify: `apps/web/components/provider-settings-workspace.jsx`
- Modify: `apps/web/app/settings/model-connections/provider-settings.css`
- Test: existing model-connection UI tests under `scripts/tests/`

**Interfaces:**
- Consumes: existing `/notebooks` navigation and Provider card selected state.
- Produces: one LLM settings `Notebook으로` link, no duplicate License/User Manual link, and readable unselected Provider cards.

- [ ] **Step 1: Write failing static/UI assertions**

  Add assertions for one `/notebooks` link in the LLM settings header and unselected card class rules that use dark readable text on white cards while preserving selected-card contrast.

- [ ] **Step 2: Run the focused UI checks and verify RED**

  Run the repository’s existing model-settings test command plus the new assertions. Expected: LLM navigation or contrast assertions fail before implementation.

- [ ] **Step 3: Implement the link and contrast rules**

  Add the link to the LLM settings page header only. Adjust unselected Provider name/description/status colors and focus ring in the provider settings stylesheet; do not alter Provider API behavior.

- [ ] **Step 4: Run focused checks and verify GREEN**

  Re-run the same UI checks and confirm the License/User Manual source still contains exactly one existing Notebook link each.

- [ ] **Step 5: Commit settings changes**

  ```powershell
  git add apps/web/app/settings/model-connections/page.jsx apps/web/components/provider-settings-workspace.jsx apps/web/app/settings/model-connections/provider-settings.css scripts/tests
  git commit -m "fix: improve settings navigation and provider contrast"
  ```

### Task 5: Full verification and work-status record

**Files:**
- Modify: `docs/04_test_reports/release_1/R1-USER-AUTH-UI-01_progress.md`
- Modify: `docs/superpowers/plans/2026-09-20-user-management-settings-ui.md`

- [ ] **Step 1: Run API test suites**

  ```powershell
  python -m pytest services/api/tests/test_admin_users.py services/api/tests/test_admin_users_runtime.py services/api/tests/test_identity_local_auth.py -q
  ```

- [ ] **Step 2: Run Web tests and build/type checks**

  Use the package scripts declared by the repository, including the admin-user tests, model-settings tests, Web lint/typecheck, and production build. Record command, exit code, and failures.

- [ ] **Step 3: Run static boundary checks**

  Verify browser code uses only same-origin BFF routes, reset responses contain no token/password fields, and `admin` remains protected.

- [ ] **Step 4: Perform browser checks at 1920×1080, 1440×900, and 430×844**

  Check navigation, table overflow, edit modal keyboard/focus behavior, reset success/error states, and Provider contrast. Do not claim real email delivery or production user password validation without the corresponding environment evidence.

- [ ] **Step 5: Append the work-status record**

  Record status, changed files, test results, error counts, unverified scope, and next action in the ignored progress file without credentials or tokens.

- [ ] **Step 6: Final diff and synchronization check**

  ```powershell
  git diff --check
  git status --short --branch
  git push origin codex/next-user-development
  git rev-parse HEAD
  git rev-parse origin/codex/next-user-development
  ```

  The last two SHAs must match before reporting the branch as synchronized.
