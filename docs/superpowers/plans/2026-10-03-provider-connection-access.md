# Provider Connection Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 공용/비공용 Provider 연결, 사용자별 Key 검증, 모델 허용 목록, 두 글자 연결 약어와 실제 삭제를 구현하고 WSL 개발 URL에서 검증한다.

**Architecture:** 기존 `system_provider_connections`, `system_provider_models`, `user_provider_credentials`를 유지하며 additive migration으로 접근 정책·허용 모델을 확장한다. API service/resolver에서 정책을 강제하고 Web은 same-origin BFF로 상태·action을 보여준다. 검증된 exact SHA만 WSL 격리 checkout에 배포한다.

**Tech Stack:** PostgreSQL/Alembic, Python 3.14/FastAPI/pytest, React/Next.js/Node test, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-03-provider-connection-access-design.md`

## Global Constraints

- 관리자 외에는 연결/모델/공용 여부를 수정하지 못한다. 비공용 연결은 사용자 Key만 사용하며 공용 Key fallback이 없다.
- Key 없는 연결은 항상 공용이다. 공용 Key 연결은 probe 성공 전 활성화하지 않는다.
- 모델 카탈로그와 허용 목록을 분리한다. `solar-pro4`만 허용한 UPSTAGE는 다른 모델을 실행하지 않는다.
- Secret·Upstream 원문 비노출, 기존 dirty WSL checkout/`secrets/` 보존, same-origin BFF, WSL만 배포.
- 사용자 UI는 `D:/Project/PMO/docs/PRODUCT_DESIGN_STANDARD.md`의 12px 본문·폼, 대비, focus, 3 viewport를 준수한다.

## Review Focus

1. 기존 `Ollama Public`은 Key가 없어도 공용 사용 가능해야 한다: Task 2의 resolver/API 테스트.
2. 실패한 Key 시험은 기존 정상 연결/Key를 교체하지 않아야 한다: Task 2의 실패 불변성 테스트.
3. 자동 카탈로그 새로고침은 관리자 허용 모델을 넓히지 않아야 한다: Task 3의 회귀 테스트.
4. 같은 Provider의 두 연결도 다른 약어를 가져야 하며 `OPENROUTER=OR`이어야 한다: Task 1의 migration/중복 테스트.
5. 연결 삭제는 사용자 Key와 기본 모델을 연쇄 삭제하지 않아야 한다: Task 2의 참조 차단 테스트.

---

### Task 1: 접근 정책·약어·허용 모델 저장

**Files:** `services/api/migrations/versions/0049_provider_connection_access.py`(new), `services/api/src/daon_user_api/provider_connection_admin.py`, `services/api/tests/test_provider_connections_migration.py`, `services/api/tests/test_provider_connection_admin.py`.

**Interfaces:** 기존 `ProviderConnectionCreateCommand`/`UpdateCommand`, 목록 응답에 `access_mode`, `credential_requirement`, `short_code`, `allowed_model_ids`를 명시적으로 추가한다. 모델 카탈로그는 기존 응답으로 유지한다.

- [ ] 기존 migration과 admin 서비스 테스트를 읽고 신규 컬럼/제약/Backfill의 실패 테스트를 추가한다. 기존 행/Key/기본 모델이 그대로 남고 `OR` 및 약어 uniqueness가 충족되어야 한다.
- [ ] `uv run --project services/api pytest services/api/tests/test_provider_connections_migration.py services/api/tests/test_provider_connection_admin.py -q`로 RED를 확인한다.
- [ ] additive migration과 admin 서비스 저장·조회 구현. 허용 모델이 기본값에 사용 중이면 변경을 거부한다.
- [ ] 위 테스트 GREEN, `git diff --check`를 확인하고 관련 파일만 commit한다.

### Task 2: 연결 시험·키 정책·삭제와 실행 강제

**Files:** `services/api/src/daon_user_api/provider_connection_admin.py`, `provider_connection_adapters.py`, `user_provider_credentials.py`, `workspace_model_defaults.py`, 해당 FastAPI route 파일, `services/api/tests/test_provider_connection_admin.py`, `test_user_provider_credentials.py`, `test_workspace_model_defaults.py`, `test_provider_settings_runtime_http.py`.

**Interfaces:** 관리자 probe/저장, 사용자 Key probe/저장, 연결 삭제는 인증·step-up·version·idempotency 계약을 유지한다. resolver는 연결 `access_mode`와 허용 모델을 확인한다.

- [ ] 무인증 공용, 관리자 Key 공용, 개인 Key 필수, 실패 probe 기존 상태 보존, 허용 외 모델 차단, 참조 연결 삭제 차단의 RED 테스트를 추가한다.
- [ ] `uv run --project services/api pytest services/api/tests/test_provider_connection_admin.py services/api/tests/test_user_provider_credentials.py services/api/tests/test_workspace_model_defaults.py services/api/tests/test_provider_settings_runtime_http.py -q`로 RED 확인.
- [ ] 최소 service/API/resolver 구현. 신규 Provider는 OpenAI 호환 Adapter만 허용하며 임의 이름만으로 지원 완료를 표시하지 않는다.
- [ ] 위 테스트 GREEN, 관련 계약·보안 테스트 추가 실행, `git diff --check` 확인 후 관련 파일만 commit한다.

### Task 3: 관리자·일반 사용자 화면

**Files:** `apps/web/components/provider-settings-workspace.jsx`, `apps/web/app/settings/model-connections/provider-settings.css`, `apps/web/lib/provider-settings-api.js`, `scripts/tests/provider-settings-web.test.mjs`.

**Interfaces:** 관리자 설정/시험/저장/삭제, 일반 사용자 읽기 전용/본인 Key 시험·저장·삭제. API 응답의 명시적 access/allowed 상태만 렌더링하고 Key 존재로 공용 여부를 추론하지 않는다.

- [ ] 약어 2글자, 공용/비공용, 사용 대기/가능, 권한별 action, 선택 모델 재조회, 에러·focus 상태의 RED UI 테스트를 추가한다.
- [ ] `node --test scripts/tests/provider-settings-web.test.mjs` RED 확인. 임시 디렉터리 React 해석 오류가 재발하면 test bundling 경로를 안전하게 수정해 먼저 harness를 정상화한다.
- [ ] 최소 UI/BFF client 변경. 모델 선택값은 허용 목록에서 가져오고 카탈로그 전체와 구분한다.
- [ ] 위 테스트 GREEN, Web build/typecheck/lint 및 1920×1080·1440×900·430×844 실제 화면 검증. 관련 파일만 commit한다.

### Task 4: WSL 개발 환경 검증·배포

**Files:** `docs/04_test_reports/release_1/R1-PROVIDER-CONNECTION-REDESIGN_progress.md`, 필요한 배포 가이드/사용자 매뉴얼.

**Interfaces:** exact Git commit → WSL 전용 격리 checkout → DB backup/preflight/migration → Compose build/up → 실제 URL 검증. 기존 `/home/daon/deploy/daon-user` dirty checkout 및 현재 `secrets/`는 보존한다.

- [ ] 전체 로컬 test/typecheck/lint/build와 migration dry-run 또는 격리 DB 검증, diff/영향/rollback 검토.
- [ ] 안전 commit과 원격 branch push. WSL의 현재 Compose 경로·사용 중 Secret mount·DB revision을 읽기 전용으로 재확인하고 백업의 대상/복구 검증을 기록한다.
- [ ] 승인 범위의 WSL 개발 DB migration과 exact SHA 배포. 실패 시 기존 이미지/DB로 rollback하며, 임시 자원은 정확한 대상으로만 정리한다.
- [ ] `http://172.27.253.53:3330/`에서 관리자·일반 사용자 흐름과 브라우저 Network same-origin을 실제 확인. 확인 못한 것은 미검증으로 보고한다.
