# Studio Workspace Default Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존·신규 Workspace에 보수적인 Studio 기본 정책 Canon 6종을 원자적으로 보장하고 운영 Studio 목록을 정상화한다.

**Architecture:** Alembic `0013`이 기존 Workspace를 idempotent backfill하고 `workspaces AFTER INSERT` Trigger로 신규 Workspace를 같은 PostgreSQL transaction에서 초기화한다. Runtime과 Repository는 기존 fail-close Projection을 유지하며 정책 누락과 실제 DB 장애의 Safe Error를 구분한다.

**Tech Stack:** Python 3.12+, Alembic, PostgreSQL 15/18, psycopg 3, FastAPI, pytest 9, Node 24, Next.js 16.

## Global Constraints

- 승인 설계 `docs/superpowers/specs/2026-08-13-studio-workspace-default-policy-design.md`의 값과 rollback 계약을 그대로 사용한다.
- `0012` Egress Organization/Workspace `deny_external` Binding을 변경하거나 완화하지 않는다.
- 브라우저 코드는 same-origin `/bff/api/...`만 사용한다.
- 기존 Run·RunSnapshot·EgressDecision·RoutingDecision·Source·Output을 소급 수정하지 않는다.
- immutable Canon UPDATE/DELETE 금지. Downgrade의 `0013` 소유 행 제거만 예외이며 결정론 ID와 `created_by`를 함께 검증한다.
- 한 시점에 한 Writer만 수정한다. 보호 dirty와 관련 없는 파일을 stage·restore·삭제하지 않는다.
- commit·push·ysna-server 배포는 어울1의 검토와 승인된 Gate에서만 수행한다.

---

### Task 1: Migration 0013 기본 Canon과 신규 Workspace Trigger

**Files:**
- Create: `services/api/migrations/versions/0013_studio_workspace_default_policy.py`
- Create: `services/api/tests/test_studio_workspace_default_policy_migration.py`
- Create: `docs/03_evidence/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02/actual-postgres-gate.py`

**Interfaces:**
- Consumes: `0003` Canon schema·digest Trigger·RLS, `0012` Egress Binding.
- Produces: `ensure_studio_workspace_defaults(text,text)` DB function과 `studio_workspace_defaults_after_insert` Trigger.

- [ ] **Step 1: 정적 Migration RED 작성**

다음 동작을 실제 migration module import와 source contract로 검증한다.

```python
def test_0013_declares_backfill_trigger_and_owned_rollback():
    assert revision == "0013"
    assert down_revision == "0012"
    assert "ensure_studio_workspace_defaults" in sql
    assert "AFTER INSERT ON workspaces" in sql
    assert "migration:0013" in sql

def test_0013_keeps_egress_and_existing_lineage_unchanged():
    assert "UPDATE egress_" not in sql
    assert "UPDATE runs" not in sql
    assert "UPDATE source" not in sql
```

- [ ] **Step 2: RED 확인**

Run:

```powershell
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_studio_workspace_default_policy_migration.py -q
```

Expected: migration module 부재로 FAIL.

- [ ] **Step 3: Migration 최소 구현**

`ensure_studio_workspace_defaults(p_tenant_id text, p_workspace_id text)`는 아래 결정론 ID를 사용한다.

```text
studio-default:workspace-policy:<md5 tenant|workspace>
studio-default:knowledge-scope:<md5 tenant|workspace>
studio-default:weight-profile:<md5 tenant|workspace>
studio-default:ruleset-reference:<md5 tenant|workspace>
studio-default:ruleset-snapshot:<md5 tenant|workspace>
studio-default:ruleset-binding:<md5 tenant|workspace>
```

Canonical payload는 key 정렬·공백 없는 JSON text로 고정한다.

```json
{"active":true,"authority_policy":"workspace_admin","current":true,"data_area":"cloud_sync","version":1,"workspace_id":"<workspace>"}
{"active":true,"current":true,"scope":"workspace","version":1,"workspace_id":"<workspace>"}
{"active":true,"current":true,"profile":"trusted-source-v2","version":1,"workspace_id":"<workspace>"}
{"active":true,"current":true,"name":"default-review-required","version":1,"workspace_id":"<workspace>"}
{"active":true,"current":true,"review_condition":"review_required","rules":[],"version":1,"workspace_id":"<workspace>"}
{"active":true,"current":true,"review_condition":"review_required","ruleset_version_id":"<snapshot-id>","version":1,"workspace_id":"<workspace>"}
```

각 insert는 `canonical_json`, 위와 exact한 `canonical_text`, `sha256(convert_to(text,'UTF8'))`, `created_by='migration:0013'`, 결정론 trace를 사용한다. 기존 유효 최신 Canon이 있으면 해당 종류는 생성하지 않는다. WeightProfile은 기존 최신 유효 KnowledgeScope가 있으면 그 ID를 참조한다. RuleSet 3종은 `0013` 전용 세트를 완성하되 기존 RuleSet을 변경하지 않는다.

Trigger는 다음 경계로 설치한다.

```sql
CREATE TRIGGER studio_workspace_defaults_after_insert
AFTER INSERT ON workspaces
FOR EACH ROW EXECUTE FUNCTION initialize_studio_workspace_defaults();
```

Trigger 함수는 SECURITY INVOKER 기본값을 유지하고 `NEW.tenant_id`, `NEW.workspace_id`만 전달한다. Upgrade는 기존 `workspaces`를 순회해 helper를 호출한 뒤 Trigger를 설치한다.

Downgrade는 Trigger→Trigger 함수→helper 순으로 제거하고, 결정론 ID와 `created_by='migration:0013'`가 모두 일치하는 행만 FK 역순으로 삭제한다. 삭제 직전에는 `0013` 소유 행이 기존 Run/RuleEvaluation 등 `0013` 비소유 계보에서 참조되는지 검사한다. 참조가 있으면 `STUDIO_DEFAULT_POLICY_ROLLBACK_BLOCKED`로 전체 transaction을 fail-close하며 계보를 끊지 않는다. 참조가 없을 때에만 migration role이 대상 6개 테이블의 `<table>_immutable` Trigger를 잠시 비활성화하고, 소유 행 삭제 후 반드시 다시 활성화한다. 다른 Canon 행·Trigger·공통 검증 함수는 변경하지 않는다.

- [ ] **Step 4: 실제 PostgreSQL Gate 작성·실행**

`actual-postgres-gate.py`는 disposable DB를 받는 `DAON_DB_MIGRATION_DSN`만 소비하고 비밀을 출력하지 않는다. 아래를 실제 SQL로 검증한다.

```text
fresh 0001→0013
기존 Workspace 전체누락/부분누락/완전구성
신규 Workspace INSERT 즉시 6 Canon
digest/FK/immutable/RLS/cross-tenant
0013→0012 시 소유 행만 제거
0013 비소유 계보가 소유 행을 참조하면 downgrade 전체 rollback
0012→0013 reapply 결정론 일치
```

- [ ] **Step 5: GREEN·회귀 확인**

```powershell
uv run --isolated --with pytest==9.0.3 pytest services/api/tests/test_studio_workspace_default_policy_migration.py -q
uv run --isolated --with pytest==9.0.3 --with alembic==1.18.5 --with "psycopg[binary]==3.3.4" docs/03_evidence/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02/actual-postgres-gate.py
```

Expected: 모든 Gate PASS, disposable DB 정리 확인.

---

### Task 2: Studio 정책 Projection과 Safe Error 정합성

**Files:**
- Modify: `services/api/src/daon_user_api/runtime.py`
- Modify: `services/api/tests/test_studio_workspace_postgres.py`
- Modify: `services/api/tests/test_studio_workspace_runtime_http.py`

**Interfaces:**
- Consumes: Task 1의 6 Canon과 기존 `PostgresStudioWorkspaceRepository._policy_projection()`.
- Produces: 정책 누락 409 `POLICY_PROJECTION_UNAVAILABLE`, 실제 DB 장애 503 `STUDIO_DATABASE_UNAVAILABLE`, 정상 목록 `{outputs, studio_locks}`.

- [ ] **Step 1: Runtime/Repository 행동 RED 작성**

```python
def test_missing_default_policy_is_public_fail_closed_409():
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "POLICY_PROJECTION_UNAVAILABLE"

def test_default_policy_returns_empty_outputs_and_six_locks():
    assert response.status_code == 200
    assert response.json()["data"]["outputs"] == []
    assert len(response.json()["data"]["studio_locks"]) == 6
```

실제 DB pool 오류 fixture는 503 `STUDIO_DATABASE_UNAVAILABLE`을 계속 검증한다.

- [ ] **Step 2: RED 확인**

```powershell
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx pytest services/api/tests/test_studio_workspace_postgres.py services/api/tests/test_studio_workspace_runtime_http.py -q
```

Expected: 정책 오류가 공개 allowlist에 없어 `INVALID_REQUEST`로 투영되는 테스트 FAIL.

- [ ] **Step 3: 최소 GREEN**

`runtime.py`의 `StudioError` public code에 아래만 추가한다.

```python
"POLICY_PROJECTION_UNAVAILABLE",
```

Repository SQL·필수값·fail-close 로직은 변경하지 않는다. 정상 기본값 fixture는 Task 1의 exact payload를 사용한다.

- [ ] **Step 4: 관련 회귀**

```powershell
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx pytest services/api/tests/test_studio_workspace_postgres.py services/api/tests/test_studio_workspace_runtime_http.py services/api/tests/test_egress_policy_runtime_http.py -q
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx pytest services/api/tests -q
```

---

### Task 3: 전체 계약·배포 증거

**Files:**
- Create: `docs/02_work_orders/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02_work_order.md`
- Create: `docs/02_work_orders/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02_prompt.md`
- Create: `docs/04_test_reports/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02_progress.md`
- Create: `docs/04_test_reports/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02_completion_report.md`
- Create: `docs/03_evidence/release_1/R1-M8-09-STUDIO-DEFAULT-POLICY-C02/manifest.json`

**Interfaces:**
- Consumes: Tasks 1·2의 migration/runtime artifacts.
- Produces: 검증 가능한 Evidence와 ysna-server 배포 Gate.

- [ ] **Step 1: 전체 자동 검증**

```powershell
uv run --isolated --with pytest==9.0.3 --with argon2-cffi --with httpx pytest services/api/tests -q
node --test scripts/tests/source-upload-api.test.mjs scripts/tests/product-workspace.test.mjs scripts/tests/product-studio.test.mjs scripts/tests/api-bff-runtime.test.mjs scripts/tests/openapi-contract.test.mjs
node scripts/verify-openapi-contract.mjs
npm run build --workspace @daon-user/web
npm run verify:product-ui-boundary
git diff --check
git diff --cached --name-only
```

- [ ] **Step 2: 독립 검토**

Migration ownership, Trigger transaction, RLS, downgrade, 정책 오류, 보호 dirty와 테스트 증거를 최신 diff로 검토한다. Critical/Important가 있으면 같은 issue로 재작업한다.

- [ ] **Step 3: commit·push·ysna 배포**

어울1 승인 경계에서만 수행한다. 서버 사전 backup·restore-list, exact commit checkout, API image rollback tag, migration `0012→0013`, API만 필요 시 recreate한다. Web 변경이 없으면 Web를 recreate하지 않는다.

- [ ] **Step 4: 운영 Browser Gate**

로그인 세션에서 아래를 확인한다.

```text
Source 목록 5건 유지
STUDIO_DATABASE_UNAVAILABLE 0
POLICY_PROJECTION_UNAVAILABLE 0
저장 산출물 empty 정상 표시
Studio 잠금 6종 표시
내부 URL/stack/secret 0
```

서버에서 current migration `0013`, backfill counts, RLS, API health/log, 공용 컨테이너 ID 불변과 rollback 자원을 기록한다.
