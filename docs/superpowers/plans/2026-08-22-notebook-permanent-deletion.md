# Notebook 영구 삭제 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notebook Home에서 확인된 Notebook과 전용 Source·파생 데이터·Object Storage 파일을 안전하게 영구 삭제하고, 완료·실패 상태를 사용자에게 표시한다.

**Architecture:** 삭제 요청은 Notebook API에서 권한·제목·ETag·idempotency를 검증한 뒤 전용 deletion job을 기록하고 큐에 제출한다. 삭제 Worker는 DB 외래키 의존성 역순과 Object Storage 정리를 단계별로 수행하며 재시작 시 마지막 단계부터 재개한다. 브라우저는 same-origin BFF만 호출한다.

**Tech Stack:** FastAPI, psycopg/PostgreSQL migrations, existing durable job/object queue, React/Next.js, Jest/node test scripts, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-22-notebook-permanent-deletion-design.md`

## Global Constraints

- 모든 SQL은 `tenant_id`, `workspace_id`, `notebook_id` 범위를 함께 검증한다.
- 기존 immutable 테이블에 일반 DELETE 권한을 부여하지 않고 전용 서비스/함수만 사용한다.
- 공유 Source/Object는 다른 Notebook 참조가 남아 있으면 삭제하지 않는다.
- 감사 이벤트에는 최소 메타데이터만 남기고 원문·비밀값을 저장하지 않는다.
- 브라우저 코드는 `/bff/...` 상대 경로만 호출한다.
- 기존 Notebook 생성·조회·제목 수정·Source unbind 계약을 변경하지 않는다.
- 파괴적 삭제는 제목 확인과 `If-Match` 검증을 모두 통과해야 한다.

---

### Task 1: 삭제 작업 데이터 모델과 마이그레이션

**Files:**
- Create: `services/api/migrations/versions/0023_notebook_deletion.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Test: `services/api/tests/test_notebook_deletion_schema.py`

**Interfaces:**
- Produces `notebook_deletion_requests` with `request_id`, scoped Notebook identity, actor, title fingerprint, state (`accepted|deleting|completed|failed`), current step, attempts, safe error, timestamps, idempotency key and version.
- Produces a controlled database function or service-owned deletion transaction; `daon_app` does not receive unrestricted DELETE grants.

- [ ] **Step 1: Write the failing schema/contract tests**

  Add tests that assert the migration creates the deletion request table, allowed state constraint, unique scoped idempotency key, RLS policy, and that an ordinary Notebook delete remains blocked.

- [ ] **Step 2: Run the tests and confirm failure**

  Run `python -m pytest services/api/tests/test_notebook_deletion_schema.py -q`.
  Expected: FAIL because revision `0023` and the deletion table do not exist.

- [ ] **Step 3: Add the migration and scoped deletion state**

  Add the table, indexes, RLS policy, grants for SELECT/INSERT/UPDATE of deletion state only, and an append-only audit-compatible schema. Keep the existing immutable triggers intact; expose deletion only through the service transaction/function that validates scope and state.

- [ ] **Step 4: Run the schema tests**

  Run `python -m pytest services/api/tests/test_notebook_deletion_schema.py -q`.
  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add services/api/migrations/versions/0023_notebook_deletion.py services/api/src/daon_user_api/runtime.py services/api/tests/test_notebook_deletion_schema.py && git commit -m "feat: add notebook deletion job schema"`

### Task 2: Repository/service deletion request API

**Files:**
- Modify: `services/api/src/daon_user_api/notebook.py`
- Modify: `services/api/src/daon_user_api/notebook_postgres.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Test: `services/api/tests/test_notebook_deletion_service.py`

**Interfaces:**
- `NotebookService.request_deletion(context, notebook_id, title_confirmation, expected_etag, idempotency_key) -> NotebookDeletionView`
- `NotebookService.get_deletion(context, notebook_id, request_id) -> NotebookDeletionView`
- API `DELETE /api/v1/workspaces/{id}/notebooks/{notebook_id}` returns `202` with `deletion_request_id`.
- API `GET /api/v1/workspaces/{id}/notebooks/{notebook_id}/deletion-requests/{request_id}` returns the state view.

- [ ] **Step 1: Write failing service tests**

  Cover title mismatch, invalid ETag, missing Notebook, duplicate idempotency replay, concurrent deletion conflict, successful `accepted` creation, and scoped authorization.

- [ ] **Step 2: Run tests and confirm failure**

  Run `python -m pytest services/api/tests/test_notebook_deletion_service.py -q`.
  Expected: FAIL because the service methods and repository persistence do not exist.

- [ ] **Step 3: Implement validation and persistence**

  Reuse existing `_SAFE_ID`, `_key`, `_fingerprint`, `NotebookError`, `CloudAccessContext`, and transaction helpers. Lock the Notebook scope with `pg_advisory_xact_lock`, verify the current metadata title and ETag, insert/replay the deletion request, write `notebook.deletion_requested` audit metadata, and enqueue one durable job.

- [ ] **Step 4: Add FastAPI routes**

  Require `Action.EDIT`, `Idempotency-Key`, `If-Match`, and a JSON title confirmation body. Map errors to `401/403/404/409/423` safe codes without exposing SQL errors.

- [ ] **Step 5: Run service/API tests**

  Run `python -m pytest services/api/tests/test_notebook_deletion_service.py -q` and the existing notebook runtime tests.
  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add services/api/src/daon_user_api/notebook.py services/api/src/daon_user_api/notebook_postgres.py services/api/src/daon_user_api/runtime.py services/api/tests/test_notebook_deletion_service.py && git commit -m "feat: accept notebook deletion requests"`

### Task 3: Durable deletion worker and data cleanup

**Files:**
- Create: `services/api/src/daon_user_api/notebook_deletion.py`
- Modify: `services/api/src/daon_user_api/object_queue.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Test: `services/api/tests/test_notebook_deletion_worker.py`

**Interfaces:**
- `NotebookDeletionWorker.process(request_id) -> NotebookDeletionResult`
- `NotebookDeletionWorker.resume_pending() -> int`
- `ObjectStoragePort.delete(key) -> None` (server-only adapter method)

- [ ] **Step 1: Write failing worker tests**

  Use a transaction fixture with representative source, processing, output, index, binding and object rows. Assert dependency-order cleanup, shared object preservation, legal-hold blocking, retry after injected failure, and idempotent completion.

- [ ] **Step 2: Run tests and confirm failure**

  Run `python -m pytest services/api/tests/test_notebook_deletion_worker.py -q`.
  Expected: FAIL because the worker and object deletion method do not exist.

- [ ] **Step 3: Implement scoped dependency cleanup**

  Snapshot bindings, reject unknown/shared targets with `DELETE_SHARED_DATA_BLOCKED`, cancel eligible queued work, delete dependent rows in reverse foreign-key order, delete only unreferenced objects from MinIO, update the request step/state after each committed stage, and write completion/failure audit events.

- [ ] **Step 4: Wire worker startup/resume**

  Register the worker with the existing document/object worker lifecycle. On startup, claim `accepted|deleting` requests with leases and resume from the recorded step.

- [ ] **Step 5: Run worker tests**

  Run `python -m pytest services/api/tests/test_notebook_deletion_worker.py -q`.
  Expected: PASS.

- [ ] **Step 6: Commit**

  `git add services/api/src/daon_user_api/notebook_deletion.py services/api/src/daon_user_api/object_queue.py services/api/src/daon_user_api/runtime.py services/api/tests/test_notebook_deletion_worker.py && git commit -m "feat: process notebook permanent deletion"`

### Task 4: BFF and browser API contract

**Files:**
- Modify: `apps/web/lib/bff-api-proxy.js`
- Modify: `apps/web/lib/notebook-api.js`
- Test: `apps/web/lib/notebook-api.test.js`

**Interfaces:**
- `requestNotebookDeletion(workspaceId, notebookId, title, { idempotencyKey, etag, signal })`
- `getNotebookDeletion(workspaceId, notebookId, requestId, { signal })`

- [ ] **Step 1: Write failing API contract tests**

  Assert the BFF maps DELETE and status GET to same-origin routes, sends `If-Match` and `Idempotency-Key`, validates response shapes, and rejects unsafe IDs or malformed states.

- [ ] **Step 2: Run tests and confirm failure**

  Run `node --test apps/web/lib/notebook-api.test.js`.
  Expected: FAIL because route mapping and client functions do not exist.

- [ ] **Step 3: Implement BFF route mapping and client validation**

  Add only the two notebook-scoped route mappings and strict response validators. Do not add absolute API URLs or browser-visible internal addresses.

- [ ] **Step 4: Run API contract tests**

  Run `node --test apps/web/lib/notebook-api.test.js`.
  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add apps/web/lib/bff-api-proxy.js apps/web/lib/notebook-api.js apps/web/lib/notebook-api.test.js && git commit -m "feat: expose notebook deletion through same-origin BFF"`

### Task 5: Notebook Home deletion UI

**Files:**
- Modify: `apps/web/components/notebook-home-workspace.jsx`
- Modify: `packages/ui/src/notebook-home.jsx`
- Modify: `packages/ui/src/notebook-home.css`
- Test: `packages/ui/src/notebook-home.test.jsx`

**Interfaces:**
- `NotebookHomeWorkspace.handleDelete(notebook, titleConfirmation)` calls the API client, tracks pending request, polls status, and refreshes the list.
- `NotebookCard` exposes a separate delete menu button without triggering card navigation.

- [ ] **Step 1: Write failing UI tests**

  Cover menu accessibility, title confirmation mismatch, delete request submission, `삭제 중` disabled state, completed removal, failure retry, and card click isolation.

- [ ] **Step 2: Run tests and confirm failure**

  Run `node --test packages/ui/src/notebook-home.test.jsx`.
  Expected: FAIL because the menu, dialog, callbacks and state are absent.

- [ ] **Step 3: Implement the UI flow**

  Add a card menu with `노트북 삭제`, a modal requiring exact title input, safe error rendering, pending state, status polling with abort cleanup, and list refresh after completion. Preserve existing create/open/settings/logout behavior and screen standards.

- [ ] **Step 4: Run UI tests**

  Run `node --test packages/ui/src/notebook-home.test.jsx`.
  Expected: PASS.

- [ ] **Step 5: Commit**

  `git add apps/web/components/notebook-home-workspace.jsx packages/ui/src/notebook-home.jsx packages/ui/src/notebook-home.css packages/ui/src/notebook-home.test.jsx && git commit -m "feat: add notebook deletion confirmation UI"`

### Task 6: Integrated verification and deployment evidence

**Files:**
- Modify: `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M8-10-NOTEBOOK-DELETE-I001_completion_report.md`

- [ ] **Step 1: Run focused backend and browser-contract tests**

  Run the Task 1–5 test commands plus `npm run verify:product-ui-boundary` and `npm run build --workspace @daon-user/web`.

- [ ] **Step 2: Run Docker integration verification**

  Apply migration in the isolated deployment database, create a disposable Notebook with a disposable PDF Source, issue DELETE through the same-origin BFF, poll to `completed`, verify the Notebook returns `404`, verify the object is absent from MinIO, and verify an unrelated Notebook remains intact.

- [ ] **Step 3: Record evidence**

  Record commit SHAs, migration precheck/apply result, container health, API responses, deletion state transitions, browser-visible result and any unexecuted checks in the progress and completion reports.

- [ ] **Step 4: Commit evidence**

  `git add docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md docs/04_test_reports/release_1/R1-M8-10-NOTEBOOK-DELETE-I001_completion_report.md && git commit -m "docs: record notebook deletion verification"`
