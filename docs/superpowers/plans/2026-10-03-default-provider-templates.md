# 기본 Provider 사용 대기 등록 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anthropic·OpenAI·Gemini를 WSL QA의 실제 연결 목록에 Key·모델 없는 비공용 사용 대기 상태로 등록한다.

**Architecture:** 기존 `CUSTOM` 연결과 `anthropic_compatible`/`openai_compatible` Adapter를 재사용한다. 관리자 생성 API에 `CUSTOM + personal + Key 없음 + 허용 모델 없음` 조합만 대기 등록으로 허용하고, 화면에서 이 경로를 시험 성공 저장과 구분한다. WSL DB의 세 행은 배포 후 관리자 경로로 등록하며 schema migration이나 Oracle 데이터 변경은 하지 않는다.

**Tech Stack:** Python 3.14/FastAPI/pytest, PostgreSQL, React 19/Next.js 16/Node test, WSL Docker QA.

**Spec:** `docs/superpowers/specs/2026-10-03-provider-connection-access-design.md`와 `docs/superpowers/specs/2026-10-03-provider-compatible-protocols-design.md`; 신산님의 세 기본 Provider 추가 및 사용 대기 요구가 후자의 신규 연결 시험 Key 규칙보다 우선하는 좁은 예외다.

## Global Constraints

- 세 기본 연결은 `provider_code=CUSTOM`; Anthropic은 `anthropic_compatible`, OpenAI·Gemini는 `openai_compatible`이다.
- Key, 허용 모델, 검증 시각 없이 `personal`, `required`, `unverified`로 등록한다. `verified` 또는 `사용 가능`으로 표시하지 않는다.
- 현재 기존 8개 연결·Key·허용 모델·Workspace 기본값을 수정하지 않는다. 공개 HTTPS Endpoint만 사용한다.
- 모델이 생기거나 공용으로 바뀌는 후속 저장에는 기존 실제 생성 시험을 유지한다. 개인 Key도 허용 모델이 없으면 사용 가능으로 만들지 않는다.
- WSL `http://172.27.253.53:3330/`에서만 실제 등록한다. Oracle·ysna·main 변경 및 실제 유료 Provider 호출은 제외한다.

## Review Focus

1. Key 없는 공용 `CUSTOM` 생성은 기존처럼 실패한다: Task 1 테스트.
2. 모델 ID만 넣고 시험 Key 없는 비공용 생성은 실패한다: Task 1 테스트.
3. 대기 연결은 일반 사용자에게 편집 없이 보이고 실행 후보에는 들어가지 않는다: Task 1·2 테스트.
4. 같은 ID·약어 또는 기존 다른 내용의 행은 WSL 등록에서 건너뛰거나 중단하고 덮어쓰지 않는다: Task 3 사전 점검.
5. 관리자 모델 추가 시 일회성 시험 Key는 저장되지 않고 실제 모델 시험 실패는 기존 대기 행을 불변으로 둔다: Task 1 테스트.

---

### Task 1: `CUSTOM` 비공용 빈 연결의 대기 등록

**Files:** Modify `services/api/src/daon_user_api/provider_connection_admin.py`; Test `services/api/tests/test_provider_connection_admin.py`.

**Interfaces:** 기존 `create_connection(context, command, idempotency_key)`와 `update_connection(...)` 시그니처는 유지한다. 새 대기 분기는 `provider_code == "CUSTOM"`, `access_mode == "personal"`, `credential is None`, `test_credential is None`, `allowed_model_ids == ()`, `logical_model_ids == ()`일 때만 허용한다.

- [x] **Step 1: RED 테스트.** 세 조건을 충족하면 네트워크 호출 0회·Credential 없음·모델/허용 목록 0개·`unverified`로 생성되는지, 모델/Key가 들어간 실패 사례와 기존 행 불변성을 확인한다.
- [x] **Step 2: RED 실행.** `PYTHONPATH=services/api/src`로 `pytest -q services/api/tests/test_provider_connection_admin.py`를 실행해 예상 실패를 확인한다.
- [x] **Step 3: 최소 구현.** 대기 분기 외 기존 `CUSTOM` 모델별 시험·공용 Key 요구·버전·감사·멱등 조건을 보존한다.
- [x] **Step 4: GREEN 실행.** Step 2 명령과 개인 Key/허용 모델 인접 테스트가 통과해야 한다.

### Task 2: 관리자 화면의 대기 등록 동작

**Files:** Modify `apps/web/components/provider-settings-workspace.jsx`; Test `scripts/tests/provider-settings-web.test.mjs`.

**Interfaces:** 기존 same-origin `providerSettingsApi.createConnection`을 사용한다. 대기 조건에서만 저장 버튼을 `사용 대기 연결 등록`으로 표시하고 Key/모델 없이 활성화한다. 그 외 연결의 `연결 시험 및 저장`은 유지한다.

- [x] **Step 1: RED 테스트.** 관리자 새 `CUSTOM` 비공용 빈 연결은 대기 등록이 가능하고 `unverified` 안내를 표시한다. 일반 사용자는 관리자 입력·추가·삭제를 못 하며, 공용/모델 지정 시 시험 Key 없이는 버튼이 활성화되지 않는다.
- [x] **Step 2: RED 실행.** `node --test scripts/tests/provider-settings-web.test.mjs`에서 예상 실패를 확인한다.
- [x] **Step 3: 최소 구현.** 비용 고지와 Key 입력은 실제 시험 경로에 남기고, 대기 등록에는 성공 시험 문구를 쓰지 않는다.
- [x] **Step 4: GREEN·build.** Step 2 명령과 Web build·UI boundary·`git diff --check`를 통과한다.

### Task 3: WSL QA 실제 세 연결 등록 및 확인

**Files:** Update `docs/04_test_reports/release_1/R1-DEFAULT-PROVIDERS_progress.md`; WSL DB는 기존 관리자 API를 통한 데이터 추가만 한다.

**Interfaces:** 화면이 자동 생성한 내부 연결 ID를 사용하고 등록 후 실제 ID를 기록한다. 표시 이름은 `Anthropic`, `OpenAI`, `Gemini`, 약어는 `AN`, `OA`, `GE`; Endpoint는 차례로 `https://api.anthropic.com/v1`, `https://api.openai.com/v1`, `https://generativelanguage.googleapis.com/v1beta/openai`다. 마지막 주소는 [Google 공식 OpenAI 호환 문서](https://ai.google.dev/gemini-api/docs/openai)의 Base URL이다.

- [ ] **Step 1: 사전 점검.** WSL DB에서 세 표시 이름·약어·기존 8개 행·허용 모델·Workspace 기본값 건수만 읽어 충돌을 확인한다. 충돌하면 덮어쓰지 않고 보고한다.
- [ ] **Step 2: exact-SHA 배포.** 로컬 전체 검증·독립 검토 후 GitHub 경유 WSL 격리 checkout으로 API/Web을 배포한다. 기존 checkout·Secret·DB를 덮어쓰지 않는다.
- [ ] **Step 3: 관리자 화면으로 세 행 등록.** 기존 행은 skip; 없는 행만 한 건씩 생성하고 실제 생성 ID를 기록한다. 저장 오류 시 이름·약어와 DB 결과를 재조회한 뒤에만 재시도한다. 결과가 `personal/required/unverified`, Key·허용 모델 없음인지 확인한다.
- [ ] **Step 4: 화면·권한 검증.** `http://172.27.253.53:3330/`에서 관리자 3개 카드, 일반 사용자 사용 대기, same-origin Network, 기존 8개 보존을 확인한다. 실제 Key/모델이 없어 생성 시험은 미검증으로 기록한다.
