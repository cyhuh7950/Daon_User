# NotebookLM형 작업지원 워크스페이스 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 Source를 안정적으로 관리하고, 대화창은 작업 상담을 수행하며, 업무 Studio는 Source 근거 산출물을 저장하는 NotebookLM형 운영 흐름을 구현한다.

**Architecture:** Source·Conversation·Studio를 독립 reducer와 서버 계약으로 유지한다. Source는 P0로 실제 목록·PDF 업로드·처리·제거를 먼저 완성하고, 대화는 작업 상담과 명시 Source 질의를 라우팅하며, Studio는 Evidence·Citation·Lineage를 검증한 뒤 Library에 저장한다. 기존 same-origin BFF, Provider 선택, Egress Policy, Run/Idempotency 계보는 그대로 재사용한다.

**Tech Stack:** Python 3.12+, FastAPI, PostgreSQL/psycopg, Alembic, React/JSX, Node 24, Next.js, pytest, Node test, ysna-server Docker.

**Spec:** `docs/superpowers/specs/2026-08-21-notebooklm-workspace-redesign-design.md`

## Global Constraints

- 정본 작업공간은 `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`이며 착수 시 Git root·branch·origin·HEAD·dirty 보호 범위를 기록한다.
- Source·Conversation·Studio는 독립적으로 로드하고 한 영역의 실패가 다른 영역의 상태·목록을 지우지 않는다.
- 브라우저는 same-origin 상대 경로와 BFF만 호출하며 API 절대주소·localhost·Docker 내부 주소를 사용하지 않는다.
- 운영 목록과 답변에 테스트 fixture·가짜 Source·가짜 산출물을 투영하지 않는다.
- Source가 없는 사실을 Source 근거처럼 생성하지 않으며, `근거가 부족하여 답변할 수 없습니다`를 단독 사용자 응답으로 사용하지 않는다.
- Provider 자동 fallback, 사용자 승인 없는 Web Research, Oracle Cloud 운영 배포는 수행하지 않는다.
- 한 시점에 한 Writer만 수정하며 보호 dirty/untracked 파일은 stage·restore·삭제하지 않는다.
- 각 작업은 RED 테스트→최소 구현→focused 회귀→progress 기록 순서로 수행한다.
- 구현·자동 테스트는 로그인 UI를 전제하지 않는다. 테스트 세션 주입·인증 경계 mock·서버 통합 테스트로 먼저 기능을 검증하고, 로그인은 최종 브라우저 acceptance에서만 수행한다.
- 모든 작업의 진행은 `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md`에 시각·단계·상태·변경 파일·명령·결과·다음 작업을 기록한다.

---

### Task 1: Source 초기 목록 실패 원인과 상태 계약 고정

**Files:**
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `apps/web/lib/product-workspace-api.js`
- Test: `scripts/tests/product-workspace.test.mjs`
- Test: `scripts/tests/notebook-api.test.mjs`
- Modify: `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md`

**Interfaces:**
- Consumes: `listWorkspaceSources(workspaceId, notebookId, options)` and existing `createProductWorkspaceState`.
- Produces: Source list state `loading | ready | empty | error`, safe error `{code,retryable}`, and a bounded retry that never clears a prior non-empty list.
- Test seam: add or expose `loadSourcesWithRetry({fetchImpl,maxRetries})` as a pure test helper around the existing loader; production UI may keep its current hook shape.

- [ ] **Step 1: Write failing browser-contract tests**

```js
test('initial source transient failure retries once and preserves source contract', async () => {
  const result = await loadSourcesWithRetry({ fetchImpl: transientThenSuccess, maxRetries: 1 });
  assert.equal(result.data.sources[0].source_version_id, 'ver-1');
  assert.equal(transientThenSuccess.calls, 2);
});

test('source 4xx does not become an empty list or retry forever', async () => {
  const result = await loadSourcesWithRetry({ fetchImpl: () => response(503), maxRetries: 1 });
  assert.equal(result.error.code, 'SOURCE_LIST_UNAVAILABLE');
  assert.equal(result.error.retryable, false);
});
```

- [ ] **Step 2: Run focused tests and capture the current failure**

Run:

```powershell
node --test scripts/tests/product-workspace.test.mjs scripts/tests/notebook-api.test.mjs
```

Expected: the new retry/error assertions fail against the current generic error projection.

- [ ] **Step 3: Implement the smallest state/API change**

Keep the exact list DTO `{data:{sources},meta:{trace_id,workspace_id}}`. Normalize only network TypeError and the existing approved transient messages as one bounded retry; map non-retryable failures to `SOURCE_LIST_UNAVAILABLE`; keep `viewState.sources` when an error follows a populated list.

- [ ] **Step 4: Run focused and boundary tests**

```powershell
node --test scripts/tests/product-workspace.test.mjs scripts/tests/notebook-api.test.mjs
npm run verify:product-ui-boundary
```

Expected: focused tests pass and the boundary reports zero forbidden browser API targets.

- [ ] **Step 5: Record the browser reproduction**

Use the logged-in browser to reload the notebook five times, click `다시 시도` only when the Source panel reports an error, and record request URL/status/response code without credentials. The progress file must distinguish initial-load failure, retry success, and unresolved provider/server failure.

### Task 2 (P0): 실제 PDF Source 추가·처리·Notebook 제거 흐름

**Files:**
- Inspect/Modify: `apps/web/lib/source-upload-api.js`
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Test: `scripts/tests/source-upload-api.test.mjs`
- Test: `services/api/tests/test_runtime_http.py`
- Modify: `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md`

**Interfaces:**
- Consumes: existing same-origin upload endpoint, source-unbinding endpoint, deletion request/cancel endpoint, and Source list DTO.
- Produces: 실제 사용자가 `Source 추가` 버튼으로 PDF를 등록하고, `uploading → processing → ready|failed` 상태를 확인하며, Notebook에서 제거할 수 있는 흐름과 safe user-action errors.
- Test seams: `uploadSource({file,notebookId})` and `unbindSource({notebookId,sourceId,sourceVersionId})` call the existing same-origin API helpers and return the normalized DTOs.

- [ ] **Step 1: Write failing upload and deletion tests**

```js
test('pdf upload returns source identifiers and processing state', async () => {
  const result = await uploadSource({ file: pdfFile, notebookId: 'nb-1' });
  assert.deepEqual(Object.keys(result.data.source).sort(),
    ['filename','source_id','source_type','source_version_id','status'].sort());
  assert.equal(result.data.source.source_type, 'pdf');
});

test('notebook removal preserves the original source and projects an unbinding', async () => {
  const result = await unbindSource({ notebookId: 'nb-1', sourceId: 'src-1', sourceVersionId: 'ver-1' });
  assert.equal(result.data.state, 'unbound');
});
```

- [ ] **Step 2: Run tests to verify the missing real-data behavior**

```powershell
node --test scripts/tests/source-upload-api.test.mjs
uv run --isolated --with pytest==9.0.3 --with httpx pytest services/api/tests/test_runtime_http.py -q
```

Expected: tests fail if the upload response, processing projection, or unbinding scope is absent.

- [ ] **Step 3: Implement only the missing contract**

Use the existing multipart BFF and server parser; do not add a browser API base URL. Validate PDF size/type server-side, persist SourceVersion and processing state, then list it through the Notebook binding projection. Keep unbinding append-only and leave physical SourceVersion data intact.

- [ ] **Step 4: Run focused tests and actual disposable-DB checks**

```powershell
node --test scripts/tests/source-upload-api.test.mjs scripts/tests/product-workspace.test.mjs
uv run --isolated --with pytest==9.0.3 --with httpx pytest services/api/tests/test_runtime_http.py -q
```

Expected: upload/list/unbind contracts pass, cross-tenant and wrong-notebook requests fail closed, and no fixture rows are created.

- [ ] **Step 5: Verify the complete browser flow after implementation tests pass**

After the implementation and integration tests pass, use the logged-in 1920×1080 browser to click `Source 추가`, select one real PDF, submit the upload, wait for `ready`, confirm the item appears in Raw Source, refresh the Source list, remove it from the Notebook, and verify the original is not physically deleted. Capture Network URLs, visible states, and the Source count in the progress file.

### Task 3: Work-support conversation routing and response contract

**Files:**
- Modify: `packages/ui/src/conversation-intent.js`
- Modify: `services/api/src/daon_user_api/question_answering.py`
- Modify: `services/api/src/daon_user_api/question_answering_service.py`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `services/api/src/daon_user_api/question_answering_postgres.py`
- Test: `services/api/tests/test_question_answering.py`
- Test: `services/api/tests/test_runtime_http.py`
- Test: `scripts/tests/product-workspace.test.mjs`

**Interfaces:**
- Consumes: selected Notebook Source context and current provider/egress authorization.
- Produces: `work_support`, `explicit_source_lookup`, `source_backed_action`, `approved_web_research` modes; response fields `mode`, `grounding`, `source_scope_summary`, `next_actions`, `citations`.
- Test seams: `classify_question(question)` returns `{mode}` and `parse_question_result(payload)` validates the structured answer; production adapters may map these seams to existing service methods.

- [ ] **Step 1: Add failing routing and prompt tests**

```python
def test_work_support_question_without_source_is_not_context_invalid():
    result = classify_question("다음 작업을 어떻게 진행할까?")
    assert result.mode == "work_support"

def test_source_mismatch_returns_scope_and_next_actions_not_bare_refusal():
    result = parse_question_result({
        "answer": "현재 Source는 디지털 헬스케어 자료를 다룹니다.",
        "grounding": "source_evidence_unavailable",
        "source_scope_summary": "디지털 헬스케어와 AI 시장",
        "next_actions": ["Source 추가", "승인된 웹 조사"],
        "cited_chunk_ids": [],
    })
    assert result.next_actions
    assert result.answer != "근거가 부족하여 답변할 수 없습니다."
```

- [ ] **Step 2: Run the red tests**

```powershell
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_question_answering.py services/api/tests/test_runtime_http.py -q
node --test scripts/tests/product-workspace.test.mjs
```

Expected: current allowlist/general fallback and response schema fail the new work-support cases.

- [ ] **Step 3: Implement routing and prompt changes**

Keep explicit Source lookup grounded and citation-validated. Expand classification for work-progress, planning, review, next-action, and product-help questions. For no-evidence work support, call the selected Provider through the existing egress authorizer with `context_mode="work_support_ungrounded"`; for a Source mismatch return a structured explanation and next actions without fabricating Source facts. Preserve Provider selection, Idempotency, RunSnapshot, and no-fallback behavior.

- [ ] **Step 4: Run focused domain and API tests**

```powershell
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_question_answering.py services/api/tests/test_runtime_http.py -q
node --test scripts/tests/product-workspace.test.mjs
```

Expected: `안녕`, “다음 작업”, general help, explicit Source lookup, and Source mismatch each return the correct mode and grounding without a bare refusal.

- [ ] **Step 5: Verify actual LLM behavior**

With the approved configured Provider, run browser questions `안녕`, `다음 작업은 무엇인가?`, `이 Source에 여행 정보가 있어?`, and an unrelated topic. Confirm the first two receive natural work-support responses, the explicit Source question has citations or an explained mismatch, and the unrelated question offers next actions without claiming Source support.

### Task 4: Source-backed Studio generation and Library lineage

**Files:**
- Modify: `packages/ui/src/product-workspace-shell.jsx`
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `services/api/src/daon_user_api/studio_report_postgres.py`
- Test: `scripts/tests/product-studio.test.mjs`
- Test: `services/api/tests/test_studio_workspace_runtime_http.py`
- Test: `services/api/tests/test_studio_workspace_postgres.py`

**Interfaces:**
- Consumes: ready SourceVersion IDs, Evidence/Citation projection, selected Provider, and effective Egress Policy.
- Produces: Studio request with `notebook_id`, `source_version_ids`, `artifact_type`, `instruction`, `run_id`; saved output with `lineage`, `citations`, and `verification_required`.
- Test seam: `generate_studio_result(evidence)` returns `{text,verification_required}` from the structured Studio adapter; production code must use the existing repository transaction.

- [ ] **Step 1: Write failing lineage tests**

```js
test('studio output preserves source version and citation lineage', async () => {
  const output = await createStudioOutput({ notebookId: 'nb-1', sourceVersionIds: ['ver-1'], artifactType: 'report' });
  assert.deepEqual(output.data.lineage.source_version_ids, ['ver-1']);
  assert.ok(Array.isArray(output.data.citations));
});
```

```python
def test_unsupported_claim_is_marked_for_verification_and_not_fabricated():
    result = generate_studio_result(evidence=["only supplied fact"])
    assert result.verification_required
    assert "only supplied fact" in result.text
```

- [ ] **Step 2: Run red tests**

```powershell
node --test scripts/tests/product-studio.test.mjs
uv run --isolated --with pytest==9.0.3 --with httpx pytest services/api/tests/test_studio_workspace_runtime_http.py services/api/tests/test_studio_workspace_postgres.py -q
```

Expected: the current Studio response does not expose the complete Source lineage or verification state.

- [ ] **Step 3: Implement the minimum Studio contract**

Require at least one ready selected SourceVersion for Source-backed artifacts, reuse Evidence/Citation validators, persist immutable Run/Output lineage, and project saved outputs in Library. Keep existing six Studio policy locks and safe policy errors unchanged.

- [ ] **Step 4: Run focused Studio and API regression**

```powershell
node --test scripts/tests/product-studio.test.mjs scripts/tests/product-workspace.test.mjs
uv run --isolated --with pytest==9.0.3 --with httpx pytest services/api/tests/test_studio_workspace_runtime_http.py services/api/tests/test_studio_workspace_postgres.py -q
```

Expected: report/table/checklist outputs save and reload with the same SourceVersion/Citation lineage; unsupported claims remain flagged.

### Task 5: End-to-end acceptance, deployment evidence, and handoff

**Files:**
- Modify: `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006_completion_report.md`
- Create: `docs/03_evidence/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006/manifest.json`

**Interfaces:**
- Consumes: Tasks 1–4 code, tests, browser recordings, and selected commit SHA.
- Produces: auditable completion report and deployment decision; no automatic Oracle production release.

- [ ] **Step 1: Run the complete local verification**

```powershell
node --test scripts/tests/product-workspace.test.mjs scripts/tests/source-upload-api.test.mjs scripts/tests/product-studio.test.mjs scripts/tests/notebook-api.test.mjs
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx pytest services/api/tests -q
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
git diff --check
```

Expected: all selected tests pass; boundary, build, and diff checks report zero violations.

- [ ] **Step 2: Run the ysna-server isolated deployment gate**

Record exact commit SHA, migration precheck/backup/rollback, Web/API health, recent safe errors, and container scope. Recreate only services changed by the approved commit. Do not alter shared `proxy`, `netdata`, shared DB containers, or Oracle Cloud.

- [ ] **Step 3: Execute the browser acceptance matrix**

At 1920×1080 with a logged-in session, verify:

```text
Source initial load and retry; PDF add; processing→ready; Notebook removal; deletion request separation
안녕; 다음 작업 상담; explicit Source question with Citation or scope mismatch explanation
Studio report/table generation; Library reload; SourceVersion/Citation lineage
Source·Conversation·Studio isolated errors; Cloud status semantics; same-origin Network
fixture rows 0; internal URL/stack/credential 0; console errors 0
```

- [ ] **Step 4: Review and record the final decision**

Use the fixed order `판정 → 판단 이유 → 조치`. Mark each gate `PASS`, `FAIL`, or `UNEXECUTED`; do not call a static build pass a browser or Provider pass. If a P0 Source gate fails, stop completion and report the exact blocker and next diagnostic action.

## Self-review checklist

- Spec sections map to Tasks 1–5: Source lifecycle to Tasks 1–2, conversation contract to Task 3, Studio lineage to Task 4, operational gates to Task 5.
- Placeholder scan must return no unfinished-marker text or vague instruction without an executable command or assertion.
- Later tasks use the exact fields and modes produced by earlier tasks: `mode`, `grounding`, `source_scope_summary`, `next_actions`, `citations`, and `source_version_ids`.
- No task changes public Egress/Provider contracts without preserving current authorization, replay, audit, and same-origin boundaries.
