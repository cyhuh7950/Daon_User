# R1-PROVIDER-CONNECTION-ACCESS-20261003 개발 기록

- 담당: 어울2, Tasks 1–3 단일 제품 코드 writer.
- 시작 기준: `codex/next-user-development`, `426bad94e81e78d7db8f0eaa0bb340afa86e84e3`, 시작 시 clean.
- 승인 자료: PMO/프로젝트 `AGENTS.md`, `docs/superpowers/specs/2026-10-03-provider-connection-access-design.md`, `docs/superpowers/plans/2026-10-03-provider-connection-access.md`.
- 단계: 사전 조사. 기존 Provider admin, credential, resolver, route, migration 0040/0047과 테스트를 확인 중.
- 변경 파일: 이 기록 파일만 생성. 제품 코드와 DB 변경 없음.
- 테스트: 아직 실행 전. 오류 횟수 0.
- 미검증: API/UI 동작, DB migration, 실제 Provider, WSL/브라우저 전부.
- 예외: 메인 agent의 WSL 개발 DB 읽기 전용 조회에서 `alembic_version=0049`; 현재 source 최신은 `0048`. 기존 0049 출처와 내용 확인 전 신규 migration revision/파일을 확정하지 않는다. WSL DB 실변경 금지.
- 다음 작업: 기존 코드·테스트에 대한 migration 비의존 API/UI RED→GREEN. 0049 출처가 확인되면 migration 번호와 적용 경로를 메인 agent 판단으로 확정.
- 범위 제외: Task 4, WSL DB/Compose/배포, push, PR/merge, ysna/Oracle.

## 2026-10-03 사전 검증 checkpoint

- `uv run --project services/api pytest ...`: 기본 uv cache와 Python lock 접근 권한 오류로 실행 전 중단(환경 오류 2회, 제품 테스트 결과 아님). worktree의 `.venv`는 Linux 형식이며 Windows Python으로 실행할 수 없음.
- `node --test scripts/tests/provider-settings-web.test.mjs`: 1 PASS, 3 FAIL. 세 실패 모두 임시 bundle의 React package 해석 실패(`ERR_MODULE_NOT_FOUND`)이고 UI 기능 판정은 불가.
- `docs/WORK_STATUS.md`와 `docs/DEVELOPMENT_ENVIRONMENT.md`는 이 checkout에서 발견되지 않음. 지정 승인 설계·계획과 실제 Git을 기준으로 진행하며 누락 사실을 메인 agent에게 전달.
- 다음: worktree 로컬 의존성으로 테스트 harness를 복구하고 migration 비의존 RED→GREEN을 진행. 임시 cache/venv는 해당 checkout에만 만들고 사용 후 정리.

## WSL 0049 불일치 배포 gate

- 메인 agent 읽기 전용 확인: WSL API 실제 DSN은 `local-postgres/daon_user`, `alembic_version=0049`. 실행 소스 checkout `f3535ef`의 migrations에는 `0049*.py`가 없고 Provider 연결 테이블은 기존 구조.
- 판정: `0049` 출처·내용을 확인하기 전 신규 migration revision/파일 확정 및 실제 DB 적용 금지. 이 불일치는 Task 4 배포 gate이며 Tasks 1–3의 로컬 서비스/API/UI·테스트 결과로 해소된 것으로 보지 않음.
- 변경·테스트: `npm ci --ignore-scripts`로 이 worktree의 로컬 `node_modules` 521 packages 준비. Web harness의 임시 bundle 위치를 로컬 `node_modules` 아래로 조정하여 React 해석 오류는 해소. Web focused test는 2 PASS/2 FAIL이며 남은 실패는 UI fixture/요청 계약 불일치.
- 오류 횟수: migration 번호 충돌 1건(환경/계보), Python 도구 권한 오류 2회(환경), Web harness 오류 1종 해결, Web 테스트 기능 불일치 2건 조사 중. 동일 근본 원인 반복 시도 없음.

## API/UI 독립 작업 checkpoint

- 변경 파일: `user_provider_credentials.py`, `test_user_provider_credentials.py`, `provider-settings-workspace.jsx`, `provider-settings-api.js`, `bff-api-proxy.js`, `provider-settings-web.test.mjs`, `api-bff-runtime.test.mjs`, 이 기록 파일.
- 확인: Python 3.14.3 로컬 설치와 `.venv-win`을 지정 checkout 안에 생성. `PYTHONPATH=services/api/src`로 기존 집중 테스트 28 PASS. 개인 연결 시스템 Key fallback 차단·공용 연결 개인 Key 사용 차단 helper 테스트 RED→GREEN, 5 PASS.
- 확인: Node Web focused 4 PASS, BFF credential 삭제 경로 1 PASS. 이는 fixture/route 검증이며 실제 DB·Provider·브라우저 검증은 아님.
- 구현 중: UI는 `access_mode`, `credential_requirement`, `short_code`, `allowed_model_ids`를 명시적으로 표시·편집하고 Keyless 공용·일반 사용자 공용 연결 Key action 금지를 반영. 연결 삭제와 시스템 Key 삭제를 별도 BFF route로 분리.
- 미완료: API 서비스·route·resolver에서 이 필드를 아직 강제하지 않았으며 migration 번호/파일 보류. UI 실제 클릭 및 세 viewport도 미검증. 이 checkpoint는 완료 판정이 아님.
- 다음: 서비스/route/resolver 회귀 테스트와 구현, UI 모델 선택 재조회·개인 연결 action 추가 검증.

## 2026-10-03 migration 계보 판정 갱신

- 메인 agent 확인: WSL의 `0049`는 `services/api/migrations/versions/0041_provider_health_check_settings.py` 안의 `revision="0049"`, `down_revision="0048"`에 해당하며 WSL 과거 commit에도 동일 내용이 있다. 로컬 지정 worktree의 동일 파일도 읽기 전용으로 확인함.
- 결정: 승인 계획의 신규 `0049_provider_connection_access.py` 명칭은 번호 오타. 신규 migration은 `0050_provider_connection_access.py`, `revision="0050"`, `down_revision="0049"`로 작성한다. 이전 `0049 출처 미확인` gate는 이 확인으로 해소됐지만 실제 WSL DB migration·배포는 메인 agent Task 4 gate로 남는다.
- 소유권: 이 checkout의 계획서 변경은 다른 담당자의 작업으로 간주하고 수정·stage하지 않는다. 현재 제품 코드 변경은 어울2 범위이며 동일 제품 파일에 다른 writer가 들어오면 즉시 쓰기를 중단한다.

## WSL 읽기 전용 preflight 추가 근거

- 실제 DSN 대상 DB의 관련 행 수: 연결 8, 카탈로그 모델 45, 사용자 Key 0, Workspace 기본값 2. Secret 원문 조회·기록 없음.
- 연결 ID/code: `ollama-public|OLLAMA`, `provider-eoul_gateway|EOUL_GATEWAY`, `provider-groq|GROQ`, `provider-media_bridge|MEDIA_BRIDGE`, `provider-mistral|MISTRAL`, `provider-omniroute|OMNIROUTE`, `provider-openrouter|OPENROUTER`, `provider-upstage|UPSTAGE`.
- migration backfill 확인점: `OPENROUTER=OR`, OLLAMA/OMNIROUTE 각각 고유 2자, 기존 8/45/0/2 행 보존. 신규 `0050` 소스 작성은 어울2, WSL 실제 적용과 DB backup/rollback은 메인 agent.

## 서비스·API 검증 checkpoint

- 변경 파일: `0050_provider_connection_access.py`, `provider_connection_admin.py`, `provider_connection_adapters.py`, `provider_catalog.py`, `provider_settings.py`, `user_provider_credentials.py`, `workspace_model_defaults.py`, `question_answering.py`, `question_answering_service.py`, `runtime.py`, 관련 pytest 및 Web UI/BFF·Node 테스트. 계획서 파일은 타인 변경으로 제외.
- 테스트: migration 소스 8 PASS, admin 14 PASS, 사용자 Key 6 PASS, adapter 22 PASS, Workspace 기본값 16 PASS. HTTP 집중 테스트는 초기 5 FAIL(기존 route의 step-up body 거부 및 과거 '연결 삭제=Key 삭제' 계약 테스트), step-up 연결 및 삭제 의미를 승인 설계대로 수정한 뒤 10 PASS. 오류 원인별 1회 수정이며 반복 실패 없음.
- 정책: 허용 목록은 카탈로그와 별도 저장하며 기본값 조회·저장·실행 모두 허용 목록/접근 정책 확인. 개인 연결은 시스템 Key fallback 없이 본인 verified Key만 허용. 실제 probe 실패 시 verified 저장 금지. 삭제 참조 건수 반환 경로 마련.
- 미검증: 실제 격리 PostgreSQL migration, 전체 typecheck/lint/build, 실제 Provider, 브라우저 세 viewport와 WSL URL. WSL URL 실사용 검증은 메인 agent Task 4.
- 설계 판단 필요: 신규 비공용 OpenAI 호환 연결은 관리자 Key 없이 사용 대기 저장이 가능하지만 카탈로그가 비어 관리자 허용 모델 선택 경로가 없다. 기존 카탈로그가 있는 연결은 영향 없음. 사용자 Key 검증이 관리자 카탈로그를 임의로 변경하게 하는 것은 권한 경계 변경이므로 적용하지 않음. 메인 agent가 수동 논리 모델 등록 또는 별도 관리자 카탈로그 검증 방식의 승인 필요 여부를 판단해야 한다.

## 2026-10-03 범위 확인·Provider 이름 checkpoint

- 신산님 직접 보완 지시: 고정 `CUSTOM` label 외 별도 자유 입력 Provider 표시 이름을 실제로 저장한다. `0050`에 `provider_name`을 추가·기존 행 `provider_code`로 backfill하고, admin command/응답/API body/UI에 별도 `provider_name` 입력을 연결. 연결 이름(`display_name`)과 별개로 유지한다.
- 집중 검증: Python API 관련 76 PASS, Node BFF/UI 41 PASS, Web production build와 product UI boundary PASS. 추가 개인 Key 상태·UPSTAGE 허용 목록만 표시하는 UI test PASS; 관리자 삭제 참조 건수 HTTP test PASS. `git diff --check`는 계획서 타인 수정에 대한 line-ending 경고 외 오류 없음.
- 새 비공용 CUSTOM의 임시 관리자 Key 경로: 기술적으로는 DB 미저장 probe 후 모델 ID만 반환→관리자 선택→재probe 및 저장이 가능하다. 현재 승인 계획에는 별도 transient preview API 계약이 없고, CUSTOM 외부 도메인 DNS/egress 제어가 현 로컬 validator만으로 확인되지 않았다. 임시 Key를 시스템 Key로 저장하거나 사용자 Key 검증으로 관리자 허용 목록을 자동 확장하지 않는다. 새 비공용 연결의 사전 모델 선택은 미완료·설계/보안 판단 사항. 기존 카탈로그가 있는 비공용 연결·공용 연결 작업은 계속한다.
- 오류 횟수 갱신: HTTP 초기 5건 원인 2종 해결, UI step-up 도입으로 fixture 기대 1건 조정, provider_name projection fixture 2건 조정. 제품 코드 동일 근본 원인 3회 반복 없음. 정적 lint의 CSS 입력 unsupported 및 기존 브라우저 URL 규칙 경고는 범위 조정 후 재검증 필요.

## Tasks 1–3 로컬 최종 검증·WSL 인계 판정

- 결과: 승인 범위 중 기존 카탈로그 보유 연결의 공용/개인 접근, 본인 Key probe/저장, 허용 모델·약어, 관리자 Provider 표시 이름, 별도 시스템 Key/연결 삭제의 로컬 코드와 테스트를 구현. 신규 비공용 CUSTOM 사전 모델 카탈로그 선택은 미완료로 분리한다.
- 최종 집중 테스트: API 77 PASS (`test_provider_connections_migration`, `test_provider_connection_admin`, `test_provider_connection_adapters`, `test_user_provider_credentials`, `test_workspace_model_defaults`, `test_provider_settings_runtime_http`), Node BFF/UI 42 PASS. 추가 Provider/QA 서비스 관련 64 PASS·1 SKIP·3 subtests PASS, Postgres/question 및 기존 migration 관련 15 PASS. Web production build·TypeScript 단계·product UI boundary 475 files/0 violations PASS.
- 별도 회귀 묶음: `test_question_answering_runtime_http.py` 12 FAIL은 HEAD와 동일한 `test_identity_support.py:40`의 정의되지 않은 `user_id`에서 공통 발생. 현재 작업이 수정하지 않은 baseline helper 오류로, 질문 HTTP 검증은 통과로 표시하지 않는다. 단순히 실패를 숨기기 위해 해당 helper를 수정하지 않음.
- 정적 lint: 대상 JS lint는 `provider-settings-workspace.jsx`의 기존 URL 파싱 1곳과 서버 전용 `bff-api-proxy.js`의 기존 내부 URL 2곳을 `forbidden-browser-url`로 검출. 대상 CSS를 lint 입력에 포함한 첫 시도는 unsupported extension 오류여서 JS만 재실행. 이번 변경의 same-origin BFF는 Node tests와 product UI boundary PASS로 확인; lint 전체 PASS라고 선언하지 않는다.
- DB: `0050` 계보는 `0049` 내부 revision 소스에 연결되고 정적 migration test PASS. 격리 실 PostgreSQL 적용은 로컬 postgres/docker 미가용으로 미실행. WSL 실제 `daon_user` migration·backup·rollback은 메인 agent Task 4 전용이다. 기존 8 connections/45 models/0 personal keys/2 defaults 보존과 `OR` 예약/약어 충돌은 WSL DB 적용 전 preflight 및 직후 검증 gate이다.
- 브라우저: 로컬 React fixture/DOM 테스트만 수행. 실제 1920×1080, 1440×900, 430×844 클릭·Network, 실제 Provider probe, 사용자 로그인/사용 가능 판정, WSL URL `http://172.27.253.53:3330/`는 모두 미검증이며 메인 agent Task 4 대상.
- WSL QA 인계 판정: 이 로컬 SHA는 **기존 카탈로그 보유 연결·공용/개인 Key 흐름의 조건부 QA 후보**이지 배포 검증 완료본이 아니다. 메인이 DB backup/실 schema preflight와 0050 격리 적용·rollback을 확인하고, 신규 비공용 CUSTOM 사전 카탈로그 미지원의 QA 제외 범위를 명시한 경우에만 WSL 후보로 진행 가능. 신규 비공용 CUSTOM 전체 목표 또는 사용자 인수 완료는 선언할 수 없다.
- 오류 누계/유형: Python 환경 준비 권한 오류 2회, Web harness React 해석 1종 해결, 기능 RED 2종 해결, HTTP 테스트 계약 불일치 2종 해결, provider_name fixture 1종 해결, 범위 밖 baseline 질문 HTTP 12건 공통 helper 원인 미해결, lint 기존 규칙 경고 1종 미해결. Secret 원문 출력·WSL DB 변경 없음.
- 다음: 메인 agent는 exact SHA 인수 후 migration 안전성·실 URL 관리자/일반 사용자 확인과 Network same-origin을 검증한다. 별도 설계 판단으로 신규 비공용 CUSTOM transient admin probe/catalog 계약 및 DNS/egress 강제를 결정한다.

## 로컬 제품 commit 인계

- 제품 commit: `87296791ec04bedbd006c827c2b5f7839fa8ceee` (`codex/next-user-development`). push/PR/merge 없음. 메인 agent가 수정한 승인 계획서 `docs/superpowers/plans/2026-10-03-provider-connection-access.md`는 stage/commit하지 않고 worktree에 그대로 보존했다.
- 제품 변경 파일: `apps/web/app/globals.css`, `apps/web/components/provider-settings-workspace.jsx`, `apps/web/lib/bff-api-proxy.js`, `apps/web/lib/provider-settings-api.js`, `scripts/tests/api-bff-runtime.test.mjs`, `scripts/tests/provider-settings-web.test.mjs`, `services/api/migrations/versions/0050_provider_connection_access.py`, `services/api/src/daon_user_api/provider_catalog.py`, `provider_connection_adapters.py`, `provider_connection_admin.py`, `provider_settings.py`, `question_answering.py`, `question_answering_service.py`, `runtime.py`, `user_provider_credentials.py`, `workspace_model_defaults.py`, `services/api/tests/test_provider_connection_adapters.py`, `test_provider_connection_admin.py`, `test_provider_connections_migration.py`, `test_provider_settings_runtime_http.py`, `test_user_provider_credentials.py`, `test_workspace_model_defaults.py`.
- `git diff --cached --check` 0 이슈, staged 경로 22개를 확인한 뒤 commit. 작업용 `.npm-cache`만 검증된 worktree 내부 경로에서 삭제했고, 로컬 `node_modules`, `.venv-win`, `.uv-python`은 무시된 개발 의존성으로 남아 있다. WSL·서버·DB·원격에는 쓰지 않았다.

## 독립 검토 Important 후속: 무요청 verified 차단

- 시작 기준: `codex/next-user-development` HEAD `d0af572edfe00c8969ee6f126b26450a2c4ec32c`, 시작 시 clean. 단일 제품 writer 어울2. WSL/DB/배포/원격 작업 없음.
- 원인: `EoulGatewayAdapter.verify`는 빈 모델 결과일 때 `_ready`를 반환하는 방어 누락이 있었다(현 `ProviderCatalog.from_logical_models`는 일반적으로 빈 목록을 먼저 거부하지만, adapter 자체는 fail-open). `MediaBridgeAdapter.verify`는 endpoint 검증만 하고 네트워크 요청 없이 `_ready`를 반환했다. 접근 불가능한 WSL `127.0.0.1` Endpoint가 이 때문에 성공처럼 보일 수 있었다.
- RED: `test_provider_connection_adapters.py`에 빈 Eoul 결과 차단, Media Bridge 실제 비생성 `GET /models`, 127.0.0.1 연결 실패·잘못된 200 응답 차단 테스트를 추가. 수정 전 4 FAIL/21 PASS이며 네 실패 모두 기존 무요청 성공 경로로 인한 예상 실패.
- GREEN: Eoul 빈 결과는 `PROVIDER_LOGICAL_MODEL_INVALID` 409. Media Bridge는 검증된 Endpoint에 5초·redirect 금지의 `GET /models` 요청을 보내고 카탈로그 응답 형식만 확인하며 모델 카탈로그를 자동 확장하지 않는다. 인증정보가 있으면 해당 요청에만 Bearer로 사용한다. 타임아웃·상위 서버 오류·형식 불량은 안전 오류이고 verified가 아니다. 실제 WSL 127.0.0.1 연결이 불가능하면 명시적 실패해야 한다.
- 검증: adapter 테스트 25 PASS, 인접 admin/API/개인 Key/기본 모델/health/catalog 회귀 묶음 85 PASS, `git diff --check` 0 이슈. 실제 Media Bridge·Eoul 네트워크 및 WSL URL은 메인 Task 4의 미검증 범위. 전체 API pytest는 현재 환경의 외부 DB 대상 가능성과 이전에 확인된 무관한 `test_identity_support.py` baseline 오류 때문에 수행하지 않았으며 PASS 주장 없음.
- 변경 파일: `services/api/src/daon_user_api/provider_connection_adapters.py`, `services/api/tests/test_provider_connection_adapters.py`, 이 report. 오류 횟수: 새 기능 RED 4건(원인 2종) → 단일 최소 수정으로 0건; 반복 실패 0회.
- 미해결: 신규 비공용 CUSTOM의 관리자 미저장 임시 Key 사전 카탈로그는 여전히 설계 판단 사항이며 이번 후속 수정에 포함하지 않는다. WSL 개인 Key 행 0건 재확인은 메인 agent 담당이다.
- Git stage 경고 1회: `docs/04_test_reports` ignore 규칙 때문에 `git add`가 종료 코드 1을 냈지만 `git ls-files`에서 해당 report가 이미 추적 중임을 확인했고, `git diff --cached --name-only`에는 의도한 세 파일만 있다. 제품 코드/테스트 실패가 아니며 stage 상태를 재검증하고 commit한다.
