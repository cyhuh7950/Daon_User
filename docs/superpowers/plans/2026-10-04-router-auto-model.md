# Router Auto Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라우터 연결에만 명시적 Auto 모델을 제공하고 OmniRoute, OpenRouter, OpenAI 호환 Media Bridge Server가 각자의 실제 모델 ID로 호출되게 한다.

**Architecture:** 연결의 nullable `auto_model_id`가 라우터 구분과 실제 호출 ID를 함께 나타낸다. 카탈로그에는 업스트림 모델과 논리적 Auto의 출처를 구분해 저장하고, 기존 허용 목록·Workspace 기본 모델 계약을 유지한다. UI는 Auto를 모델 한 개로 선택하며 서버는 선택된 실제 ID만 기존 Adapter에 전달한다.

**Tech Stack:** PostgreSQL/Alembic, Python 3.14/FastAPI/pytest, React/Next.js/Node test.

**Spec:** `docs/superpowers/specs/2026-10-04-router-auto-model-design.md`

## Global Constraints

- 범위: OmniRoute, OpenRouter, 관리자가 라우터로 지정한 OpenAI 호환 `CUSTOM` 연결(Media Bridge Server 포함). 일반 Provider에 Auto를 추가하지 않는다.
- 고정 ID: OmniRoute=`auto`, OpenRouter=`openrouter/auto`; `CUSTOM/openai_compatible`은 관리자가 실제 ID를 설정한다. `combo` 특별 처리와 새 라우팅 알고리즘은 없다.
- Auto 명시 선택과 모델 미지정의 기준 모델 동작은 별개다. 이번 작업은 기존 미지정 경로·기본 모델을 변경하지 않는다.
- 기존 연결 ID·Provider 코드·Adapter·Key·Endpoint·허용 목록·Workspace 기본값을 migration에서 재작성하지 않는다. 이름/URL로 `CUSTOM` 라우터를 추정하지 않는다.
- 관리자 권한, version, idempotency, 감사, 실패 불변성, Endpoint/egress 보안, same-origin BFF를 유지한다. 실제 유료 Provider 시험과 WSL/Oracle 배포는 별도 승인 경계다.
- Auto는 허용 목록에서 한 개로 계산하고 다른 모델과 공존할 수 있다. 카탈로그 조회는 허용 목록을 자동 확대하지 않는다.

## File Structure

- `services/api/migrations/versions/0052_router_auto_model.py`: nullable `auto_model_id`와 모델 출처 `catalog_origin`의 additive migration. 고정 두 Provider만 backfill.
- `services/api/src/daon_user_api/provider_catalog.py`: 논리적 Auto와 업스트림 모델의 출처를 담은 `DiscoveredModel` 생성.
- `services/api/src/daon_user_api/provider_connection_admin.py`: 연결 설정·검증·idempotency·카탈로그/허용 목록·새로고침의 Auto 계약.
- `services/api/src/daon_user_api/runtime.py`: 관리자 mutation body의 `auto_model_id` 전달과 omission-preserving update.
- `apps/web/components/provider-settings-workspace.jsx`: 라우터 설정, Auto 선택/표시와 기존 OmniRoute 배타 선택 제거.
- 필요할 때만 기존 Provider CSS/BFF 헬퍼를 수정한다. 질문 Adapter는 기존 ID 전달 경로를 테스트로 먼저 고정하고 실패할 때만 최소 변경한다.
- `docs/04_test_reports/release_1/R1-ROUTER-AUTO_progress.md`: 단계·테스트·오류·미검증·다음 조치 기록.

## Review Focus

1. 구형 관리 API 클라이언트가 `auto_model_id` 없이 기존 라우터를 수정해도 값이 지워지지 않아야 한다: Task 1 API 회귀.
2. 이름만 `Media Bridge Server`인 기존 `CUSTOM` 연결은 관리자 지정 전 라우터로 바뀌지 않아야 한다: Task 1 migration/API 회귀.
3. OpenRouter `/models`에 `openrouter/auto`가 이미 있으면 카탈로그에 한 행만 남아야 한다: Task 2 카탈로그 회귀.
4. OmniRoute 조회에서 `auto`가 빠져도 논리적 Auto와 기존 허용 목록은 유지되고, 다른 모델과 함께 선택하면 각각 한 개로 계산되어야 한다: Task 2·3 회귀.
5. `CUSTOM` Auto 생성 시험이 실패하면 기존 Key·허용 목록·기본 모델·라우터 설정이 바뀌지 않아야 한다: Task 2 서비스 회귀.

---

### Task 1: additive schema와 연결 계약

**Files:** Create `services/api/migrations/versions/0052_router_auto_model.py`; modify `services/api/src/daon_user_api/provider_connection_admin.py`, `services/api/src/daon_user_api/runtime.py`; test `services/api/tests/test_provider_connections_migration.py`, `test_provider_connection_admin.py`, `test_provider_settings_runtime_http.py`.

**Interfaces:** `ProviderConnectionCreateCommand.auto_model_id: str | None = None`; `ProviderConnectionUpdateCommand.auto_model_id: str | None = None`, `auto_model_id_specified: bool = False`. 목록·상세 응답은 `auto_model_id: str | None`를 반환한다. `ProviderConnectionUpdateBody`는 `model_fields_set`으로 필드 누락과 명시적 `null`을 구분해 이전 값을 보존하거나 제거한다. `system_provider_models.catalog_origin`은 `upstream|logical`, 기본 `upstream`이다.

- [ ] `test_migration_0052_adds_router_auto_without_rewriting_existing_data`: `revision/down_revision=(0052,0051)`, nullable `auto_model_id`, 기본값 `upstream`의 additive `catalog_origin`, OmniRoute·OpenRouter만 정확한 값으로 backfill, `CUSTOM`/Key/allowlist/default UPDATE·DELETE 없음. `test_update_omits_auto_model_id_preserves_existing_router`와 `test_named_custom_is_not_router_until_admin_marks_it`를 RED로 추가한다.
- [ ] `.venv-win/Scripts/python.exe -m pytest services/api/tests/test_provider_connections_migration.py services/api/tests/test_provider_connection_admin.py services/api/tests/test_provider_settings_runtime_http.py -q`를 실행해 새 테스트의 예상 FAIL을 확인한다.
- [ ] migration과 body/command/summary/fingerprint 계약을 구현한다. 고정 Provider의 ID 변조를 409로 거부하고 `CUSTOM`은 `openai_compatible`일 때만 null 또는 기존 모델 ID 규칙을 통과한 값을 허용한다. 기존 update payload의 필드 누락은 현재 값을 보존한다.
- [ ] 같은 테스트를 GREEN으로 재실행하고 `git diff --check` 후 이 Task 파일만 commit한다.

### Task 2: Auto 카탈로그·저장·허용 목록

**Files:** Modify `services/api/src/daon_user_api/provider_catalog.py`, `provider_connection_admin.py` 및 필요 시 `provider_connection_adapters.py`; test `services/api/tests/test_provider_catalog.py`, `test_provider_connection_admin.py`, `test_provider_connection_adapters.py`.

**Interfaces:** `DiscoveredModel.catalog_origin: Literal["upstream", "logical"] = "upstream"`; `ProviderCatalog.logical_auto_model(connection_id: str, provider_code: str, auto_model_id: str) -> DiscoveredModel`. `_replace_models`는 출처를 저장하고, 같은 ID가 업스트림에 있으면 한 행만 사용한다. `refresh_catalog`는 등록된 논리적 Auto를 보존하되 허용 목록을 넓히지 않는다.

- [ ] `test_openrouter_listed_auto_is_not_duplicated`, `test_omniroute_unlisted_auto_survives_refresh_and_keeps_allowlist`, `test_omniroute_auto_and_explicit_models_count_separately`, `test_custom_auto_probe_failure_preserves_key_allowlist_and_default`, `test_removing_router_flag_with_referenced_auto_rejects_without_mutation`을 추가한다. 논리적 항목은 응답 `catalog_origin="logical"`, 조회 항목은 `"upstream"`을 확인한다.
- [ ] `.venv-win/Scripts/python.exe -m pytest services/api/tests/test_provider_catalog.py services/api/tests/test_provider_connection_admin.py services/api/tests/test_provider_connection_adapters.py -q`에서 새 테스트의 예상 FAIL을 확인한다.
- [ ] Auto 행 생성·중복 방지·새로고침 보존을 구현한다. OmniRoute의 암묵적 Auto 대체·배타 선택을 제거하되 이미 저장된 허용 목록은 변경하지 않는다. 선택 변경 시 Auto도 최대 4개 제한의 한 개로 계산한다. `CUSTOM` Auto 허용 시 기존 `verify_models` probe를 사용하고 실패 시 transaction을 변경하지 않는다.
- [ ] 같은 테스트를 GREEN으로 재실행하고 `git diff --check` 후 이 Task 파일만 commit한다.

### Task 3: 관리자·사용자 모델 화면

**Files:** Modify `apps/web/components/provider-settings-workspace.jsx`; 필요 시 `apps/web/app/settings/model-connections/provider-settings.css`; test `scripts/tests/provider-settings-web.test.mjs`, `provider-endpoint-ui-contract.test.mjs`, `api-bff-runtime.test.mjs`.

**Interfaces:** Draft에 `auto_model_id`를 보존한다. 관리자에게만 `CUSTOM/openai_compatible`의 라우터/Auto ID 설정을 보여준다. 고정 라우터는 읽기 전용 ID를 보여준다. 모델 체크박스는 Auto의 실제 ID를 저장하고, `catalog_origin="logical"`일 때만 조회 외 논리 모델임을 표시한다. 일반 사용자는 읽기 전용이다.

- [ ] `test_custom_router_auto_only_after_admin_setting`, `test_openrouter_auto_uses_real_id`, `test_omniroute_auto_can_coexist_with_explicit_model`, `test_router_auto_refresh_preserves_selection`, `test_regular_provider_has_no_auto`, `test_combo_is_not_synthesized`를 RED로 추가한다. mutation body의 `auto_model_id`와 same-origin BFF URL을 확인한다.
- [ ] `node --test scripts/tests/provider-settings-web.test.mjs scripts/tests/provider-endpoint-ui-contract.test.mjs scripts/tests/api-bff-runtime.test.mjs`에서 새 테스트의 예상 FAIL을 확인한다.
- [ ] 신규/기존 연결 Draft와 저장 body, Auto 옵션, 비용 안내를 최소 변경한다. 사용 허용 모델 목록의 기존 고정 높이·내부 스크롤을 유지하고 새 외부 스크롤을 만들지 않는다.
- [ ] 같은 테스트 GREEN, `npm run build --workspace @daon-user/web`, `npm run verify:product-ui-boundary`, `git diff --check`를 확인하고 이 Task 파일만 commit한다.

### Task 4: 실행 ID 회귀와 로컬 통합 gate

**Files:** Test `services/api/tests/test_question_answering_service.py`, `test_workspace_model_defaults.py`, `test_provider_settings_runtime_http.py`; modify 실행 코드만 실패로 입증된 경우. Update `docs/04_test_reports/release_1/R1-ROUTER-AUTO_progress.md`와 영향받은 사용자 매뉴얼의 해당 단락.

**Interfaces:** 허용된 Auto는 각 연결의 실제 ID를 그대로 기존 Adapter에 전달한다. 모델 미지정·Workspace 기본 모델 선택, Credential, Provider URL, fallback 의미는 변경하지 않는다.

- [ ] fixture에서 OmniRoute=`auto`, OpenRouter=`openrouter/auto`, OpenAI 호환 Media Bridge=`auto`의 질문 본문 `model`을 검증한다. 일반 Provider와 기존 Workspace 기본 모델 회귀를 함께 고정하고, 실행 경로 수정이 필요하면 먼저 예상 FAIL을 확인한다.
- [ ] `.venv-win/Scripts/python.exe -m pytest services/api/tests/test_question_answering_service.py services/api/tests/test_workspace_model_defaults.py services/api/tests/test_provider_settings_runtime_http.py -q`로 RED(수정 필요 시)와 GREEN을 확인한다.
- [ ] 집중 API/Node 테스트, API 전체 suite, Web build·정적 경계, `git diff --check`를 실행한다. 기존 실패·skip은 이름과 원인으로 구분하고 통과로 선언하지 않는다. migration은 격리 DB에서 기존 연결/Key/허용/기본값 보존을 검증한다.
- [ ] 현황 파일에 branch/HEAD, 변경 파일, 테스트·오류 횟수, 미검증, rollback을 기록하고 관련 파일만 commit한다. 실제 유료 Provider 호출, WSL DB migration·배포, 기존 Media Bridge Server 설정 변경, Oracle 배포는 대상·비용·영향을 보고해 별도 승인받기 전 수행하지 않는다.
