# Provider Compatible Protocols Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자가 임의 Provider를 OpenAI Chat Completions 또는 Anthropic Messages 방식으로 시험·등록하고, 모델 목록이 없어도 검증된 모델 ID를 직접 등록해 질문에 사용한다.

**Architecture:** 기존 `CUSTOM` 연결의 `adapter_type`을 규격 구분자로 사용한다. 저장 전 모델 조회는 읽기 전용 관리자 API로, 실제 모델 시험과 저장은 기존 version/idempotency mutation으로 수행한다. resolver가 규격을 질문 실행까지 전달하고 Web은 same-origin BFF만 호출한다.

**Tech Stack:** Python 3.14/FastAPI/pytest, PostgreSQL, React 19/Next.js 16/Node test. 새 DB migration·의존성은 없다.

**Spec:** `docs/superpowers/specs/2026-10-03-provider-compatible-protocols-design.md` (신산님 검토 승인: 2026-10-03)

## Global Constraints

- 지원: `openai_compatible`의 Chat Completions, `anthropic_compatible`의 Messages만. 기존 연결은 ID·Provider 코드·접근 방식·Key·허용 모델·Workspace 기본 모델 불변이다.
- 새 연결은 공개 HTTPS Endpoint를 사용한다. 신산님의 2026-10-04 후속 지시에 따라 신규 등록은 두 호환 방식만 노출하며 Key 필요/불필요를 선택한다. Key 불필요 연결은 공용으로 한정하고 인증 헤더 없이 조회·시험·실행한다. 저장된 규격은 수정하지 않으며 기존 연결은 재분류하지 않는다.
- 조회 불가 시 수동 모델 ID 입력. 허용 모델마다 비스트리밍 최대 1회, 출력 한도 16토큰으로 시험하고 하나라도 실패하면 저장 전 상태를 유지한다. 시험에 비용이 들 수 있음을 화면에 표시한다.
- 공용 Key는 성공 후 암호화 저장, 비공용 관리자 시험 Key는 저장하지 않음. 개인 Key 검증 성공 전 사용 대기; 개인 Key는 관리자 연결/허용 모델을 바꾸지 못한다.
- 기존 `CUSTOM`의 공개 HTTPS/SSRF 방어, no-redirect/응답 크기/시간 제한, 질문 egress, 인증/step-up, version/idempotency, same-origin BFF를 약화하지 않는다.
- 임베딩/Responses/오디오/이미지/RAG, 상태 점검 스위치, 라이선스, ysna/Oracle은 범위 밖. WSL 배포는 별도 gate에서만 한다.
- Windows Python 집중 테스트 전 PowerShell에서 `$env:PYTHONPATH='services/api/src'`를 설정한다. `.venv-win`의 기존 의존성을 사용하며 시스템 Python/캐시는 바꾸지 않는다.

## File Structure

- `services/api/src/daon_user_api/provider_connection_adapters.py`: 두 규격의 모델 조회와 모델별 시험, 안전 오류 변환.
- `services/api/src/daon_user_api/provider_catalog.py`: 조회 또는 시험 성공 모델을 기존 안전한 `DiscoveredModel`로 정규화.
- `services/api/src/daon_user_api/provider_connection_admin.py`, `runtime.py`: 관리자 preview와 시험-후-저장, 일회성 Key·replay 계약.
- `services/api/src/daon_user_api/user_provider_credentials.py`: 개인 Key의 저장 전 모델별 시험.
- `services/api/src/daon_user_api/workspace_model_defaults.py`, `question_answering_service.py`, `question_answering.py`, `document_understanding_adapter.py`: 규격 전달과 Anthropic 실행; 기존 OpenAI 실행 유지.
- `apps/web/components/provider-settings-workspace.jsx`, `apps/web/lib/provider-settings-api.js`, 필요 시 기존 CSS: 관리자·사용자 화면과 same-origin 요청.
- 해당 Python/Node 테스트 및 `docs/04_test_reports/release_1/R1-PROVIDER-COMPATIBILITY_progress.md`: 단계별 검증·오류·미검증 기록.

## Review Focus

1. `/models`가 404/405 또는 비표준 응답이어도 수동 ID+생성 시험 성공으로 등록: Task 1의 `test_manual_model_works_without_catalog`와 Task 2의 `test_custom_create_registers_verified_manual_model`.
2. 비공용 관리자 시험 Key가 DB·응답·로그·idempotency payload에 남지 않음: Task 2의 `test_private_test_key_is_ephemeral`.
3. 첫 모델은 성공하고 둘째 모델은 실패하면 연결·기존 Key·허용 목록 불변: Task 1의 `test_probe_all_models_fails_closed`와 Task 2의 `test_failed_update_preserves_connection`.
4. `adapter_type`을 무시해 Anthropic 연결에 Bearer/Chat Completions를 보내는 회귀 방지: Task 3의 `test_anthropic_selection_uses_messages_and_version_header`.
5. 기존 8개 연결과 UPSTAGE `solar-pro4`, Ollama, Gateway의 선택·실행·접근 정책 보존: Task 3의 기존 회귀 묶음과 Task 5의 DB 전후 비교.

---

### Task 1: 호환 규격별 모델 조회·실제 시험

**Files:** Modify `services/api/src/daon_user_api/provider_connection_adapters.py`, `provider_catalog.py`; Test `services/api/tests/test_provider_connection_adapters.py`, `test_provider_catalog.py`.

**Interfaces:** `AdapterRegistry.adapter(provider_code: str, adapter_type: str = "") -> ConnectionAdapter`는 기존 호출을 유지하고 `CUSTOM`의 두 타입만 분기한다. `ConnectionAdapter` 계약에 `verify_models(connection: ProviderConnection, credential: str | bytes, model_ids: Sequence[str]) -> tuple[DiscoveredModel, ...]`를 추가하되 기존 Adapter의 기본 동작은 `PROVIDER_ADAPTER_UNSUPPORTED`로 닫는다. 호환 Adapter는 모델별 비스트리밍 probe 후 `ProviderCatalog.from_verified_text_models(connection_id: str, provider_code: str, model_ids: Sequence[str]) -> tuple[DiscoveredModel, ...]`로 검증 모델을 반환한다. `discover_models`는 규격별 `GET /models`를 유지하되 preview 실패가 저장 성공을 암묵 의미하지 않는다.

- [ ] **Step 1: RED 테스트 작성.** 두 규격의 `/models` 헤더/경로, 수동 모델 ID의 16토큰 probe, 2xx 형식 불량·두 번째 모델 실패·redirect/timeout/SSRF 차단을 고정한다. 예: `assert [request.url for request in transport.requests] == ["https://models.example/v1/chat/completions"]`; `assert result[0].model_id == "manual-model"`; 실패 시 `pytest.raises(AdapterError)`.
- [ ] **Step 2: RED 실행.** `PYTHONPATH=services/api/src`에서 `.venv-win/Scripts/python.exe -m pytest services/api/tests/test_provider_connection_adapters.py services/api/tests/test_provider_catalog.py -q`; 신규 테스트가 예상 이유로 FAIL.
- [ ] **Step 3: 최소 구현.** `CUSTOM` 규격 분기, OpenAI Bearer/`choices[0].message.content`, Anthropic Key+`anthropic-version`/text 블록을 검증한다. 수동 ID를 기존 catalog 타입으로 정규화하고 Key/Upstream 원문은 오류에 넣지 않는다.
- [ ] **Step 4: GREEN 실행·commit.** Step 2 명령 PASS, `git diff --check` 통과 후 이 Task의 파일만 commit.

### Task 2: 관리자 조회·시험 후 저장과 수동 카탈로그

**Files:** Modify `services/api/src/daon_user_api/provider_connection_admin.py`, `runtime.py`; Test `services/api/tests/test_provider_connection_admin.py`, `test_provider_settings_runtime_http.py`; Update progress report.

**Interfaces:** 신규 관리자 전용 `POST /api/v1/admin/provider-connections/model-preview`는 `{connection_id, provider_code:"CUSTOM", adapter_type, base_url, credential, step_up_authorization_id}`를 받아 안전한 모델 ID 목록만 반환하고 DB를 쓰지 않는다. preview step-up target은 `provider-connection:<connection_id>`, operation은 `provider_catalog.preview`로 한다. `ProviderConnectionCreateCommand`/`UpdateCommand`에 `test_credential: str | None`(repr 제외)을 추가한다. `CUSTOM` 신규 저장은 `allowed_model_ids` 각 모델에 대한 `verify_models` 성공 후 같은 transaction에서 카탈로그·허용 목록을 저장한다. 공용에는 `credential`, 비공용에는 `test_credential`만 허용한다. fingerprint에는 시험 Key의 keyed digest만 넣는다.

- [ ] **Step 1: RED 테스트 작성.** `test_preview_is_admin_only_and_read_only`, `test_private_test_key_is_ephemeral`, `test_custom_create_registers_verified_manual_model`, `test_failed_update_preserves_connection`, `test_manual_catalog_refresh_failure_preserves_allowlist`, `test_existing_custom_adapter_type_immutable`와 replay 테스트를 추가한다. 예: `assert saved["allowed_model_ids"] == ["manual-model"]`; `assert persisted.encrypted_credential is None`; `assert before == after`; 다른 규격으로 수정하면 409, 같은 idempotency Key의 다른 시험 Key도 409.
- [ ] **Step 2: RED 실행.** `PYTHONPATH=services/api/src`에서 `.venv-win/Scripts/python.exe -m pytest services/api/tests/test_provider_connection_admin.py services/api/tests/test_provider_settings_runtime_http.py -q`; 신규 테스트가 예상 이유로 FAIL.
- [ ] **Step 3: 최소 구현.** 기존 auth/step-up/version/replay 경로를 재사용하고 preview에 관리자 권한을 강제한다. 조회만으로 verified를 기록하지 않는다. `CUSTOM` 저장·Key 교체·모델 변경은 실제 probe 후 기존 `_replace_models`/`_set_allowed_models` 계약을 사용한다. 기존 행에는 migration/UPDATE를 하지 않는다.
- [ ] **Step 4: GREEN 실행·commit.** Step 2 명령 PASS, API 계약·Secret 비노출 확인, `git diff --check` 통과 후 관련 파일만 commit.

### Task 3: 개인 Key 시험과 질문 실행 규격 전달

**Files:** Modify `services/api/src/daon_user_api/user_provider_credentials.py`, `workspace_model_defaults.py`, `question_answering_service.py`, `question_answering.py`, `document_understanding_adapter.py`; Test `services/api/tests/test_user_provider_credentials.py`, `test_workspace_model_defaults.py`, `test_question_answering.py`, `test_question_answering_service.py`, `test_question_answering_runtime_http.py`; Update progress report.

**Interfaces:** `ResolvedModel.adapter_type: str = ""`를 후방 호환 필드로 추가하고 resolver가 DB `c.adapter_type`을 채운다. `QuestionAdapterRegistry`는 `CUSTOM/anthropic_compatible`만 새 Anthropic Messages adapter로 보낸다. `TextGenerationTransport.post_json_headers(*, url: str, headers: Mapping[str,str], payload: dict[str,object], timeout_seconds: float) -> dict[str,object]`는 서버가 생성한 제한된 헤더에만 사용한다. 개인 Key 등록은 DB 허용 모델을 읽어 `verify_models` 성공 후 암호화 저장한다.

- [ ] **Step 1: RED 테스트 작성.** Anthropic 일반/근거 질문의 URL·헤더·본문·응답/usage, 인용 검증, 개인 Key 실패 불변, 기존 OpenAI/Ollama/Gateway/UPSTAGE 분기를 고정한다. 예: `assert request.url.endswith("/messages")`; `assert request.headers["anthropic-version"] == "2023-06-01"`; `assert result.cited_chunk_ids == ("chunk-1",)`.
- [ ] **Step 2: RED 실행.** `PYTHONPATH=services/api/src`에서 `.venv-win/Scripts/python.exe -m pytest services/api/tests/test_user_provider_credentials.py services/api/tests/test_workspace_model_defaults.py services/api/tests/test_question_answering.py services/api/tests/test_question_answering_service.py -q`; 신규 테스트가 예상 이유로 FAIL.
- [ ] **Step 3: 최소 구현.** `adapter_type`을 resolver→registry까지 전달한다. Anthropic의 최상위 `system`/`messages`/`max_tokens`와 text 블록을 처리하고 기존 근거·일반 답변 검증을 재사용한다. 개인 Key는 본인 연결의 허용 모델만 시험하고 공용 Key fallback을 추가하지 않는다.
- [ ] **Step 4: GREEN·회귀·commit.** Step 2 명령과 `test_question_answering_runtime_http.py`를 실행한다. 기존 baseline helper 오류가 재현되면 새 실패와 분리 기록하고 PASS로 선언하지 않는다. `git diff --check` 후 관련 파일만 commit.

### Task 4: 관리자·일반 사용자 화면

**Files:** Modify `apps/web/components/provider-settings-workspace.jsx`, `apps/web/lib/provider-settings-api.js`, 필요 시 `apps/web/app/settings/model-connections/provider-settings.css`; Test `scripts/tests/provider-settings-web.test.mjs`, `scripts/tests/api-bff-runtime.test.mjs`, `scripts/tests/provider-endpoint-ui-contract.test.mjs`; Update progress report.

**Interfaces:** `providerSettingsApi.previewModels(input)`는 `/bff/api/admin/provider-connections/model-preview` 상대 경로를 호출한다. 신규 연결 화면은 `호환 방식 → API 유형 → Endpoint/Key → 모델 조회 또는 수동 ID → 시험 후 저장` 순서. 저장된 연결의 규격과 일반 사용자 모델 설정은 읽기 전용이다. 사용자 Key 화면은 관리자 허용 모델만 보여준다.

- [ ] **Step 1: RED 테스트 작성.** 신규 두 규격 선택, 지원하지 않는 API 유형 숨김, 조회 실패 후 수동 ID, 비용 고지·대상 모델 수, 버튼 비활성/실패 상태, 관리자/일반 사용자 action, 새로고침 후 모델 허용 목록 보존을 확인한다. 예: `assert.equal(request.url, "/bff/api/admin/provider-connections/model-preview")`; `assert.match(screenText, /사용료/)`.
- [ ] **Step 2: RED 실행.** `node --test scripts/tests/provider-settings-web.test.mjs scripts/tests/api-bff-runtime.test.mjs scripts/tests/provider-endpoint-ui-contract.test.mjs`; 신규 테스트가 예상 이유로 FAIL.
- [ ] **Step 3: 최소 UI/BFF 변경.** Provider 이름은 자유 입력으로 남기고 신규 연결에만 두 호환 규격을 보여준다. Key는 화면 메모리에만 유지하며 모델 조회 실패 후에도 수동 ID 시험에 사용할 수 있게 한다. 최종 시험/저장 후 Key 입력을 비우고 same-origin 경로만 사용한다.
- [ ] **Step 4: GREEN·build·commit.** Step 2 명령, `npm run build --workspace @daon-user/web`, `npm run verify:product-ui-boundary`, 대상 JS lint를 실행한다. 1920×1080·1440×900·430×844 실제 UI 클릭은 가능한 환경에서 확인하며, 미실행이면 미검증으로 기록한다. `git diff --check` 후 관련 파일만 commit.

### Task 5: Main agent 독립 검토·WSL 개발 QA gate

**Files:** Update `docs/04_test_reports/release_1/R1-PROVIDER-COMPATIBILITY_progress.md`; 필요 시 사용자 매뉴얼의 해당 Provider 부분만 갱신.

**Interfaces:** Tasks 1–4는 단일 개발 writer가 순차 수행한다. Main agent는 최신 설계서와 최종 diff/테스트를 독립 검토하고, 승인된 범위를 넘는 외부 비용·권한·배포 변경은 먼저 신산님에게 보고한다.

- [ ] **Step 1: 로컬 통합 검증.** API 집중/인접 테스트, Node UI/BFF, Web build/정적 검사, `git diff --check`, `git status`를 재실행하고 기존 8개 연결과 0050 데이터 계약을 fixture/격리 DB에서 비교한다. 실패/skip은 통과로 바꾸지 않는다.
- [ ] **Step 2: 독립 검토.** 최신 설계·계획·diff로 Critical/Important finding, Secret/SSRF/egress/권한, 기존 UPSTAGE/Ollama/Gateway 회귀를 확인한다. 해결 전 배포하지 않는다.
- [ ] **Step 3: WSL 별도 gate.** 신산님이 WSL 개발 배포를 승인한 경우에만 기존 checkout/Secret·DB 상태를 읽기 전용 preflight 후 백업, exact SHA 격리 checkout 배포, `http://172.27.253.53:3330/`의 관리자·일반 사용자 실제 클릭과 Network same-origin 검증을 수행한다. 승인 또는 실제 Provider Key가 없으면 미실행으로 남긴다.
- [ ] **Step 4: 최종 기록.** 변경 파일, 시험 PASS/FAIL/미검증, 외부 비용 호출 수, 데이터 전후, rollback, 다음 승인/판정을 progress report에 기록한다.
