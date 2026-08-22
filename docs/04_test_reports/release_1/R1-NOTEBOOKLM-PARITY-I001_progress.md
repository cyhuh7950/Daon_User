# R1-NOTEBOOKLM-PARITY-I001 진행 기록

## 현재 상태

- 단계: Task 1 구현·서버 배포 검증
- 상태: `INTEGRATION_CHECK_PENDING`
- 구현: 완료 (`f078085`)
- 배포: ysna-server에 `f07808563119a549ca583076714f286063bd171b` 배포 완료
- 기록 시각: 2026-08-22

## 확정 요구사항

- 운영형 NotebookLM 동등 기능을 목표로 한다.
- 현재 Workspace와 Studio 카드 배치는 유지한다.
- 일반 Source는 PDF로 제한하지 않는다.
- MCP와 Daon 승인 지식을 연결형 Source로 추가한다.
- Source 등록·삭제는 사용자가 즉시 수행한다.
- Source 등록 시 `Notebook과 함께 삭제`와 `Notebook 삭제와 무관하게 보관`을 선택한다.
- 동일 파일 내용은 Digest로 중복 저장하지 않는다.
- 외부 원본 소실은 자동 삭제하지 않고 `사용 불가`로 표시한다.
- 대화와 Studio는 선택된 Source를 사용하고, 일반 업무 상담도 허용한다.

## 생성 문서

- 상세 설계안: `docs/superpowers/specs/2026-08-22-notebooklm-parity-design.md`
- 작업계획: `docs/superpowers/plans/2026-08-22-notebooklm-parity-implementation-plan.md`

## 다음 단계

Task 1 구현·빌드·서버 기동은 완료했다. 다음은 로그인 세션이 필요한 실제 브라우저 Source 등록/삭제와 DB·MinIO 통합 검증이다.

## Task 1 실행 기록 (R1-NOTEBOOKLM-PARITY-I001-T1)

- 착수: 2026-08-22, 공식 작업공간 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`, HEAD `3342d6c88bcb5191322bd1d1e179b5e6c41d3760`; 기존 dirty 변경은 보존.
- 단계 완료: Source 계약 확장. `SourceRecord`에 Notebook 귀속·content type·삭제 정책·unavailable 상태를 추가했고, 파일 형식은 기존 MIME Matrix를 활용하도록 유지했다. 외부 원본 소실을 자동 삭제하지 않고 `unavailable`로 표시하는 변환을 추가했다.
- 단계 완료: 업로드 서비스에 일반 Source 계약(`register_source`)과 기존 `register_pdf` 호환 래퍼를 추가하고, content type·삭제 정책을 Canonical payload로 전달하도록 확장했다. 동일 digest 조회 시 기존 object/source version을 재사용하는 경로를 추가했다.
- 테스트: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_source_lifecycle_contract.py tests/test_source_ingest.py tests/test_source_upload.py -q` → `9 passed, 4 subtests passed`.
- 오류/복구: 저장소 루트 및 API 디렉터리에서 PYTHONPATH 없이 실행한 첫 두 테스트는 `ModuleNotFoundError`로 수집 실패. 패키지 구조에 맞게 `PYTHONPATH=src`로 재실행해 통과.
- 미해결: 실제 HTTP 업로드 경계는 허용 범위 밖 `runtime.py`에서 여전히 `application/pdf`를 강제하므로, 비-PDF 엔드포인트 통합은 상위 에이전트 판단이 필요하다. DB에 삭제 정책 전용 컬럼을 추가하지 않고 canonical JSON payload로 전달했으므로 운영 DB 회귀 검증도 필요하다.
- 다음: 상위 에이전트가 runtime 경계 수정 허용 여부와 DB 계약/마이그레이션 범위를 판단한 뒤 통합 테스트를 진행한다.

### Task 1 재개 승인 및 추가 검증

- 승인: 2026-08-22, 상위 에이전트가 `runtime.py` HTTP 업로드 경계 확장과 DB 계약 판단을 승인하여 Task 1을 재개.
- 변경: `runtime.py`가 파일명 확장자와 MIME Matrix를 함께 검증하도록 변경. PDF 호환을 유지하면서 txt/md/csv/docx/pptx/xlsx/png/jpg/jpeg/m4a/wav/mp3 업로드를 SourceIngestor 계약으로 전달한다. 잘못된 MIME은 415, 잘못된 파일 시그니처는 Source 계약 오류로 거부한다.
- 변경: 업로드 서비스에 요청 content type을 전달하고 canonical JSON에 삭제 정책·content type을 기록한다. 스키마를 확인한 결과 `source_versions.canonical_json`이 기존 정식 불변 payload 저장 지점이므로 별도 DB 컬럼/migration은 만들지 않았다.
- 테스트: `$env:PYTHONPATH='src'; uv run pytest tests/test_source_lifecycle_contract.py tests/test_source_ingest.py tests/test_source_upload.py tests/test_source_upload_runtime.py -q` → `15 passed, 6 warnings, 4 subtests passed`.
- 미실행: 브라우저 E2E, ysna-server 배포·DB/MinIO 통합 검증은 이 Task에서 실행하지 않음.

### ysna-server 배포 확인

- 배포: `f07808563119a549ca583076714f286063bd171b` 기준 API·document-worker·web 이미지를 재빌드하고 재기동했다.
- 빌드: API/worker 이미지 빌드 성공. Web Next.js 빌드·TypeScript·`verify-product-ui-boundary` 성공(`violations: []`).
- 런타임: API `healthy`, Web `healthy`, document-worker 실행 중, 기존 MinIO `healthy`.
- 공개 확인: `https://daon-user.sinsan.kr/notebooks` → HTTP 200.
- 프로필 확인: API 컨테이너 `DAON_RUNTIME_PROFILE=production`; `DAON_DEV_AUTH_BYPASS`는 설정되지 않음.
- 미완료: 로그인된 브라우저에서 실제 PDF/비-PDF 등록·처리 완료·삭제와 DB/MinIO 실물 정합성은 아직 확인하지 못했다. 이 검증 전에는 Task 1을 최종 완료로 판정하지 않는다.

### Task 2 UI Source 추가 구현 (R1-NOTEBOOKLM-PARITY-I001-T2)

- 착수/환경: 2026-08-22, 공식 작업공간 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`; 기존 dirty 변경은 보존하고 새 branch는 생성하지 않음.
- 단계 완료: 기존 3면 배치를 유지하면서 Source 추가를 모달 흐름으로 확장했다. 파일 업로드(일반 MIME), 웹사이트, Drive, 복사한 텍스트 탭을 제공하고, 아직 Connector 계약이 없는 웹사이트·Drive는 mock/API 임의 주소 없이 `연결 준비 필요`로 표시한다.
- 단계 완료: 업로드 클라이언트와 Notebook adapter를 generic `uploadSource` 계약으로 연결했다. 브라우저 요청은 기존 same-origin `/bff/api/workspaces/{workspaceId}/sources` 경로를 유지하고, notebook id는 adapter에서 전달한다. `uploadPdf` 호환 래퍼는 기존 호출부 회귀를 위해 유지했다.
- 단계 완료: 등록 중 상태, 안전한 오류 코드, `unavailable` Source 상태 라벨을 UI에 반영했다. 붙여넣은 텍스트는 text Source로 등록 요청하며 서버 계약을 우회하지 않는다.
- 변경 파일: `packages/ui/src/product-workspace-shell.jsx`, `packages/ui/src/notebook-context-adapter.js`, `packages/ui/src/workspace.css`, `apps/web/lib/source-upload-api.js`, `apps/web/components/actual-workspace.jsx`, `apps/web/lib/product-workspace-api.js`, `scripts/tests/notebook-source-add-flow.test.mjs`.
- 오류/복구: 신규 테스트의 Unicode 정규식 문법 오류를 문자열 assertion으로 수정. 기존 Studio 오류 테스트가 자동 retry 대기 중 상태를 검사하던 문제를 확인하고 계약/list 실패는 즉시 UI 오류로 표시하며 일시적 transport 오류만 자동 retry하도록 분리했다.
- 테스트: `node --test scripts/tests/notebook-source-add-flow.test.mjs scripts/tests/product-workspace.test.mjs` → `22 passed`. `git diff --check` → 공백 오류 없음(기존 dirty 파일의 줄바꿈 경고만 존재).
- 미해결: 웹사이트·Drive 실제 Connector와 로그인 브라우저 E2E, ysna-server 재배포 및 DB/MinIO 실물 등록 검증은 상위 통합 단계에서 수행해야 한다. 이번 Task에서는 임의 mock을 넣지 않았다.

### Task 2 ysna-server 배포 확인

- 배포: `8ca9dcb` 기준 API·document-worker·web 이미지를 재빌드하고 재기동했다.
- 빌드: Web Next.js/TypeScript 및 `verify-product-ui-boundary` 성공(`violations: []`).
- 런타임: API `healthy`, Web `healthy`, document-worker 실행 중, 기존 MinIO `healthy`.
- 공개 확인: `https://daon-user.sinsan.kr/notebooks` → HTTP 200.
- 프로필 확인: API 컨테이너 `DAON_RUNTIME_PROFILE=production`.
- 미완료: 로그인된 브라우저의 실제 Source 등록/삭제 클릭과 DB·MinIO 실물 정합성 검증은 아직 수행하지 못했다. 웹사이트·Drive Connector도 아직 구현 전이다.

### Task 3 MCP·Daon 승인 지식 Connector 계약

- 착수/환경: 2026-08-22, 공식 작업공간과 기존 `codex/user-auth-screen-split` branch에서 진행. 기존 dirty 변경은 보존하고 새 branch는 생성하지 않음.
- 단계 완료: `mcp_connector.py`에 공통 Connector·ConnectorSource·ConnectorView·Registry 계약을 추가했다. 등록·목록·재연결·해제와 unavailable 상태 전이를 정의했으며, Connector 장애나 원본 소실 시 Source를 삭제하지 않고 `unavailable`로 투영한다.
- 단계 완료: 국가법령정보센터 `open.law.go.kr` 샘플 Connector를 서버 전용 메타데이터로 추가했다. API key가 없으면 연결된 것으로 가장하지 않고 unavailable로 표시한다. Daon 승인 지식도 `as_connector()`로 동일한 연결형 Source 계약에 노출한다.
- 단계 완료: runtime에 workspace Connector 목록·등록·재연결·해제·Connector Source 목록 API를 추가했다. 인증정보는 서버 환경변수에서만 읽으며 브라우저에는 same-origin BFF 경로만 제공한다.
- 단계 완료: API client에 Connector 응답 검증과 same-origin 목록/등록/재연결/해제 호출을 추가하고, 기존 Source pane 배치를 유지하면서 연결형 Source 목록과 unavailable 상태를 표시하도록 UI를 연결했다.
- 테스트: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_mcp_connector.py tests/test_approved_knowledge_connector.py -q` → `7 passed`. Python compileall 및 `node --check apps/web/lib/product-workspace-api.js` 통과.
- 미해결: Connector 상태·Source binding의 Postgres 영속화와 국가법령정보센터 실제 API 호출/인증 검증, 로그인 브라우저 클릭 검증은 통합 단계에서 수행해야 한다. 이번 구현은 upstream을 mock으로 완료 처리하지 않고 자격증명 부재를 unavailable로 표시한다.

### Task 3 Adapter 보완

- 보완: `apps/web/components/actual-workspace.jsx`의 실제 Web Adapter에 `listConnectors`와 `reconnectConnector`를 product-workspace-api same-origin 함수로 연결했다. 외부 주소·mock 호출은 추가하지 않았다.
- 보완: Source 추가 정적 계약 테스트에 Adapter 함수 연결, BFF 상대 경로, 연결형 Source의 `사용 불가` 표시를 추가했다.
- 테스트: `node --test scripts/tests/notebook-source-add-flow.test.mjs` → `4 passed`; `node --check apps/web/lib/product-workspace-api.js` 통과. JSX 파일은 Node 단독 syntax-check 대상이 아니므로 Next 빌드에서 검증해야 한다.

### Task 3 ysna-server 배포 확인

- 배포: `4b7e5ba` 기준 API·document-worker·web 이미지를 재빌드하고 재기동했다.
- 빌드: API/worker 이미지와 Web Next.js 빌드 성공. TypeScript 및 `verify-product-ui-boundary` 성공(`violations: []`).
- 런타임: API `healthy`, Web `healthy`, document-worker 실행 중, 기존 MinIO `healthy`.
- 공개 확인: `https://daon-user.sinsan.kr/notebooks` → HTTP 200.
- 프로필 확인: API 컨테이너 `DAON_RUNTIME_PROFILE=production`; `DAON_DEV_AUTH_BYPASS`는 설정되지 않음.
- 미완료: Connector 상태·Source binding의 Postgres 영속화, 국가법령정보센터 실제 API 호출/인증 검증, 로그인된 브라우저의 실제 Connector·Source 클릭 검증, DB·MinIO 실물 정합성은 아직 수행하지 못했다. 이 검증 전에는 Task 3을 최종 완료로 판정하지 않는다.

### Task 4 대화·근거 동작 착수 (R1-NOTEBOOKLM-PARITY-I001-T4)

- 착수: 2026-08-22 12:41 KST, 공식 작업공간 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`, HEAD `815764861a70944450d7a1e777d2fc833a13a418`; 기존 dirty 변경은 보존.
- 현재 확인: Source 미선택 일반 상담과 한국어 응답 메타데이터는 기존 구현에 있으나, 선택 컨텍스트에 `사용 불가` Source가 포함되면 서버 검색 단계가 전체 질문을 실패시킬 수 있다.
- 진행 상태: 사용 가능한 Source만 grounding 대상으로 필터링하고, 모두 unavailable이면 일반 LLM 상담 경로로 전환하는 최소 수정 및 회귀 테스트를 진행한다.

### Task 4 구현·로컬 검증

- 변경: `QuestionAnsweringService`가 선택 컨텍스트의 Source를 먼저 `load_ready_source`로 확인하고 `QUESTION_SOURCE_UNAVAILABLE`만 제외한다. 일부가 사용 가능하면 남은 Source만 검색·저장·Citation 범위에 사용하고, 모두 unavailable이면 `general_ungrounded` 일반 LLM 경로로 전환한다. DB 장애 등 다른 오류는 그대로 전파한다.
- 변경: Workspace 대화 UI에서 ready Source를 다시 클릭하면 선택을 해제할 수 있도록 하고, Source 미선택 시 일반 상담 안내·입력 문구를 표시한다. 기존 첫 ready Source 자동 선택과 화면 배치는 유지했다.
- 변경 파일: `services/api/src/daon_user_api/question_answering_service.py`, `packages/ui/src/product-workspace-shell.jsx`, `services/api/tests/test_notebooklm_chat_grounding.py`.
- 테스트: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_notebooklm_chat_grounding.py tests/test_question_answering_service.py tests/test_question_answering_runtime_http.py -q` → `25 passed, 19 warnings`. 루트에서 `node --test scripts/tests/product-workspace.test.mjs scripts/tests/real-data-conversation-contract.test.mjs scripts/tests/question-answering-api.test.mjs` → `38 passed`.
- 미해결/판단 필요: 현재 허용 파일 범위 밖인 `apps/web/lib/question-answering-api.js`가 Source 미선택 요청을 기존 exact 일반대화 문구로 제한한다. 따라서 UI에서 일반 질문을 생성해도 “인사·사용법” 이외 질문은 `QUESTION_INPUT_INVALID`가 될 수 있다. Task 4 요구사항을 완전히 충족하려면 이 클라이언트 계약과 관련 회귀 테스트/OpenAPI 설명을 함께 넓힐지 어울1의 판단이 필요하다.

### Task 4 보완 착수 (R1-NOTEBOOKLM-PARITY-I001-T4)

- 착수: 2026-08-22 12:49:06 KST, 상위 지시에 따라 Source 미선택 질문의 클라이언트 고정 allowlist 제한을 제거한다. 기존 dirty/untracked 변경은 보존한다.
- 확인: `apps/web/lib/question-answering-api.js`와 Windows native adapter가 Source 미선택 질문을 `isGeneralConversationIntent`에 의존해 제한하고 있으며, OpenAPI 설명과 회귀 테스트도 narrow general conversation을 전제로 한다.
- 진행 상태: Source·Knowledge Context가 제공된 경우의 기존 검증은 유지하고, 둘 다 없을 때 임의의 일반 업무 질문을 기존 일반 LLM 경로로 전달하는 최소 변경 및 Web/Desktop 회귀 테스트를 진행한다.

### Task 4 보완 구현 완료·로컬 검증

- 변경: `apps/web/lib/question-answering-api.js`의 Source 미선택 분기를 질문 문구 allowlist 없이 `{}`로 전송하도록 변경했다. Source 또는 Knowledge Context가 있으면 기존 exact 입력 검증을 그대로 유지한다.
- 변경: `apps/desktop/src/windows-workspace-adapter.js`도 Source 미선택 질문을 임의의 일반 업무 질문으로 기존 Native 일반 상담 경로에 전달하도록 동일하게 맞췄다.
- 변경: OpenAPI 설명에서 no-context 질문을 인사·제품 도움말로 제한하던 문구를 일반 업무 지원/LLM 경로로 정정했다. oneOf 구조와 응답 검증 계약은 변경하지 않았다.
- 테스트: `node --test scripts/tests/real-data-conversation-contract.test.mjs scripts/tests/question-answering-api.test.mjs scripts/tests/product-workspace.test.mjs` → `38 passed`.
- 회귀 테스트: Web/Desktop에서 `다음 작업을 어떻게 진행하지?`, `한국어로 답해줘`, 임의 업무 질문을 포함한 Source 미선택 요청이 각각 `source_id`·`knowledge_context` 없이 전송되는 것을 확인했다.
- 다음: API 지정 테스트와 Web 빌드를 실행하고 변경 파일만 커밋·push한다. 브라우저·DB/MinIO 실물 검증은 통합 단계 미실행으로 유지한다.

### Task 4 보완 검증·종료

- 테스트 완료: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_notebooklm_chat_grounding.py tests/test_question_answering_service.py tests/test_question_answering_runtime_http.py -q` → `25 passed, 19 warnings`.
- 테스트 완료: `node --test scripts/tests/real-data-conversation-contract.test.mjs scripts/tests/question-answering-api.test.mjs scripts/tests/product-workspace.test.mjs` → `38 passed`.
- 빌드 완료: `npm run build --workspace @daon-user/web` → Next production build 성공, TypeScript 성공, `verify-product-ui-boundary` `violations: []`, `boundaryErrors: []`.
- 정적 확인: `git diff --check` 통과. 기존 unrelated dirty/untracked 변경은 staging하지 않았다.
- 미실행: 로그인 브라우저 실제 클릭, ysna-server 배포, DB/MinIO 실물 정합성은 이번 보완 범위에서 실행하지 않았다.

### Task 4 ysna-server 배포 확인

- 배포: `47f15ea` 기준 API·document-worker·web 이미지를 재빌드하고 재기동했다.
- 빌드: API/worker 이미지와 Web Next.js 빌드 성공. TypeScript 및 `verify-product-ui-boundary` 성공(`violations: []`).
- 런타임: API `healthy`, Web `healthy`, document-worker 실행 중, 기존 MinIO `healthy`.
- 공개 확인: `https://daon-user.sinsan.kr/notebooks` → HTTP 200.
- 프로필 확인: API 컨테이너 `DAON_RUNTIME_PROFILE=production`; `DAON_DEV_AUTH_BYPASS`는 설정되지 않음.
- 미완료: 로그인된 브라우저의 실제 일반 질문·Source 질문 클릭 검증, Connector 상태·Source binding의 Postgres 영속화, 국가법령정보센터 실제 API 호출/인증 검증, DB·MinIO 실물 정합성은 아직 수행하지 못했다.

### Task 5 Studio 산출물 기능 정합화 착수·판정 (R1-NOTEBOOKLM-PARITY-I001-T5)

- 착수: 2026-08-22, 공식 작업공간 `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`; 기존 dirty/untracked 변경은 보존하고 새 branch를 생성하지 않음.
- 확인: 현재 Studio 생성 API는 `POST /api/v1/studio-generation-requests`에서 Postgres 트랜잭션 안에서 `runs`의 기존 답변·Citation을 동기적으로 읽고 `studio_outputs`/`output_versions`를 즉시 생성한다. 별도 Studio 생성 작업 큐·worker·상태 조회 계약은 확인되지 않았다.
- 확인 명령: `Get-ChildItem services/api/src/daon_user_api,services/api/migrations/versions -File | Select-String -Pattern 'studio_generation|generation_requests|studio_outputs'` 결과는 기존 동기 `studio_workspace_postgres.py` 및 API 계약뿐이며, Studio 전용 queue/worker 계약은 없음.
- 판정: `BLOCKED`. 승인 Task 5는 11개 Studio 기능을 기존 백그라운드 작업과 Library에 연결하고 provider/job 미연결 시 unavailable을 반환해야 한다. 현재 허용 파일 범위만으로는 백그라운드 생성 job/worker·상태 API를 추가할 수 없고, 현재 동기 생성 경로를 UI에서 11개 카드에 매핑하면 실제 provider 결과가 아닌 동기/가짜 완료를 만들 위험이 있다.
- 영향: `studio_workspace.py`, `studio_export.py`, `report_generation.py`, `studio-workflow-pane.jsx`, `product-workspace-shell.jsx`만 수정하여 진행하면 백그라운드·unavailable 계약을 충족하지 못한다. 공개 API/runtime·DB/worker 계약과 최소 테스트 범위 확장이 필요하다.
- 미변경: 위 BLOCKED 판단으로 Task 5 관련 코드를 수정하거나 mock/fake 결과를 만들지 않았다.
- 다음 판단 필요: 어울1이 Studio 생성 작업용 기존 공통 job/worker 계약을 지정하거나, runtime·DB migration·worker·BFF 공개 계약 확장을 승인해야 Task 5 구현을 재개할 수 있다.
