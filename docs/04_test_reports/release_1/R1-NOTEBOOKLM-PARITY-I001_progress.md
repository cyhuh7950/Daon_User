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

### 질문 실행 Source 승인 modal 제거 보완 (R1-NOTEBOOKLM-PARITY-I001-T4)

- 착수: 2026-08-22, 상위 지시에 따라 세션 로그인 이후 질문 실행에서 비밀번호를 다시 요구하던 Web UI 경로를 제거했다. 기존 dirty/untracked 변경은 보존했다.
- 변경: `packages/ui/src/product-workspace-shell.jsx`에서 `questionAuthorization` 상태·pending 상태·비밀번호 ref·승인 handler와 `question-authorization` modal 렌더링을 제거했다. 일반 질문 실행은 기존 `askQuestion` 경로만 사용한다.
- 테스트: `scripts/tests/product-workspace.test.mjs`에 Source 질문 승인 modal 미노출 정적 회귀를 추가했다.
- 테스트 완료: `node --test scripts/tests/product-workspace.test.mjs scripts/tests/real-data-conversation-contract.test.mjs scripts/tests/question-answering-api.test.mjs` → `39 passed`.
- 빌드 완료: `npm run build --workspace @daon-user/web` → Next production build·TypeScript 성공, `verify-product-ui-boundary` `violations: []`, `boundaryErrors: []`.
- 미실행: 로그인 브라우저 실제 클릭, ysna-server 배포 및 DB/MinIO 실물 검증은 미실행이다.

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

### Task 5 계약 확장 재개·구현

- 재개: 상위 승인에 따라 기존 Studio/Library 계약을 재사용하는 범위에서 구현을 재개했다. 별도 Studio worker·provider가 없는 상태에서 완료를 가장하지 않는 원칙은 유지한다.
- 변경: Studio 출력 타입을 5종에서 11종(`evidence_report`, `compliance_checklist`, `comparison_table`, `knowledge_map`, `business_draft`, `slides`, `infographic`, `flashcards`, `quiz`, `audio`, `video`)으로 확장하고 생성 설정의 형식 검증을 맞췄다.
- 변경: 슬라이드·인포그래픽·플래시카드·퀴즈는 기존 근거/Citation 기반 생성 결과 구조와 Library export 경로를 재사용한다. Source lineage와 생성 시각은 기존 `studio_outputs`/`output_versions` 저장 계약을 그대로 사용한다.
- 변경: UI의 기존 Studio 카드 배치와 3열 화면은 유지하면서 11개 카드를 선택 가능하게 하고, 새 구조화 결과를 Library 상세 화면에서 표시한다. 브라우저 호출 경로는 기존 same-origin BFF를 유지한다.
- 제한: 오디오·동영상은 연결된 provider·바이너리 인코더가 없으므로 `STUDIO_OUTPUT_UNAVAILABLE`(409)로 fail-closed 처리한다. 가짜 미디어나 완료 결과를 생성하지 않는다.
- 변경 파일: `services/api/src/daon_user_api/studio_workspace.py`, `services/api/src/daon_user_api/studio_export.py`, `apps/web/lib/product-workspace-api.js`, `packages/ui/src/product-studio-model.js`, `packages/ui/src/product-studio-pane.jsx`, `services/api/tests/test_notebooklm_studio_outputs.py`.
- 미해결/판단 필요: 별도 Studio 백그라운드 worker·job/status API는 기존 코드베이스에 계약이 없어 추가하지 않았다. 현재 지원 출력은 기존 동기 Postgres 트랜잭션을 사용한다. 진정한 비동기 생성과 오디오·동영상 provider 연결을 요구하면 별도 migration/worker/provider 설계 승인이 필요하다.
- 검증: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_notebooklm_studio_outputs.py tests/test_studio_workspace_service.py tests/test_studio_export.py -q` → `13 passed`. `node --check apps/web/lib/product-workspace-api.js` 통과. `node --test scripts/tests/product-workspace.test.mjs` → `19 passed`. `npm run build --workspace @daon-user/web` → Next 빌드·TypeScript·`verify-product-ui-boundary` 성공(`violations: []`). 관련 diff `git diff --check` 통과.

### Task 5 비동기 Studio 작업 계약 구현 (R1-NOTEBOOKLM-PARITY-I001-T5-ASYNC)

- 착수: 상위 승인에 따라 동기 Studio 생성 경로를 durable job 접수·상태 조회·worker 처리 흐름으로 확장했다. 기존 branch와 unrelated dirty/untracked 변경은 보존했다.
- 변경: `0024_studio_generation_jobs` migration과 `PostgresStudioGenerationQueue`를 추가했다. tenant/workspace/actor/idempotency 범위의 queued·leased·completed·failed·unavailable 상태, lease/version 경쟁 방지, safe error와 output ID를 보존한다.
- 변경: `studio_generation_worker`가 기존 `create_generation`/Library 저장 계약을 재사용한다. 9개 구조화 출력은 실제 provider/기존 grounding 경로 결과만 완료 처리하며, audio/video는 `STUDIO_OUTPUT_UNAVAILABLE`로 종료한다. 예외도 안전한 failed 상태로 회수한다.
- 변경: POST Studio generation은 job을 반환하고 GET `/api/v1/studio-generation-jobs/{job_id}` 상태 API를 추가했다. UI는 기존 Studio 카드 배치를 유지한 채 queued/leased polling 후 completed 결과를 Library 상태로 반영한다. 브라우저 호출은 same-origin BFF 상대 경로다.
- 변경 파일: `services/api/migrations/versions/0024_studio_generation_jobs.py`, `services/api/src/daon_user_api/studio_generation_queue.py`, `services/api/src/daon_user_api/studio_generation_worker.py`, `services/api/src/daon_user_api/studio_workspace_postgres.py`, `services/api/src/daon_user_api/studio_workspace.py`, `services/api/src/daon_user_api/runtime.py`, `services/api/src/daon_user_api/cloud_storage.py`, `apps/web/lib/product-workspace-api.js`, `apps/web/components/actual-workspace.jsx`, `packages/ui/src/product-studio-pane.jsx`, `deploy/daon-user/compose.yaml`, 관련 테스트.
- 테스트: `services/api`에서 `$env:PYTHONPATH='src'; uv run pytest tests/test_studio*.py -q` → `58 passed, 1 skipped`. 비동기 전용 worker/API 테스트 포함. `uv run python -m compileall -q services/api/src/daon_user_api` 통과. `npm run build --workspace @daon-user/web` → Next/TypeScript/`verify-product-ui-boundary` 성공(`violations: []`).
- 미실행: migration 실제 적용, ysna-server worker 재배포, Postgres/MinIO 실물 큐 처리, 로그인 브라우저에서 카드 생성·polling·Library 클릭은 아직 검증하지 않았다. 해당 검증 전에는 운영 완료로 판정하지 않는다.
- 다음: 상위 agent가 변경 파일을 검토한 뒤 관련 파일만 commit/push하고, ysna-server 격리 환경에서 migration·studio-worker·BFF·실제 Studio 흐름을 검증한다.

### Task 5 worker 네트워크 보완

- 변경: `deploy/daon-user/compose.yaml`의 `studio-worker`에 `proxy-network`를 추가해 `DAON_CLOUD_DATABASE_DSN`의 `shared-db` 접근 경로를 `document-worker`와 동일하게 맞췄다. 기존 `daon_user` 네트워크는 유지했다.
- 검증: worker 모듈 import smoke 통과. DSN 미설정 startup은 `STUDIO_WORKER_DATABASE_REQUIRED`로 즉시 안전 실패했다. `git diff --check` 통과.
- 미실행: 현재 Codex 환경에 Docker CLI가 없고 WSL 호출도 `E_ACCESSDENIED`로 차단되어 `docker compose config`, migration 전/후 실제 DB, worker 컨테이너 기동은 실행하지 못했다.

### Task 5 ysna-server 배포·마이그레이션 검증

- 배포: `b15ec55` 기준 API·document-worker·studio-worker·web 이미지를 ysna-server에서 재빌드·기동했다. Web Next 빌드/TypeScript 및 `verify-product-ui-boundary`는 `violations: []`로 통과했다.
- 런타임: API `healthy`, Web `healthy`, document-worker 실행 중, MinIO `healthy`, 공개 `/notebooks` HTTP 200. 운영 API 환경은 `DAON_RUNTIME_PROFILE=production`이며 개발 인증 우회값은 설정되지 않았다.
- 초기 오류: studio-worker가 `DATABASE_UNAVAILABLE`로 재시작했다. 원인 조사 결과 `0023` DB 마이그레이션이 아직 적용되지 않았고, 실제 worker ID `daon_user-studio-worker`의 `_`가 DB 함수 검증식에 허용되지 않아 `STUDIO_JOB_CLAIM_INVALID`도 발생했다.
- 조치: `0023_notebook_deletion.py`의 기존 테이블 재개 가능성을 보장하도록 `CREATE TABLE/INDEX IF NOT EXISTS` 및 정책 재생성을 적용했고, worker ID를 검증 가능한 `studio-worker-1`로 고정했다. 기존 `notebook_deletion_requests` 4건과 테이블 구조는 보존했다.
- 마이그레이션: 전용 ephemeral `daon_user-api` 컨테이너로 ysna-server `shared-db`에 `0022→0023→0024`를 적용했다. 최종 `alembic_version=0024`, `studio_generation_jobs` 테이블과 `claim_studio_generation_job` 함수가 확인됐다.
- 최종 런타임: `studio-worker`가 `running`, RestartCount `0`으로 안정화됐고 API/Web/기존 worker/MinIO도 정상 상태를 유지한다.
- 미실행: 로그인된 브라우저에서 실제 Studio 카드 클릭·polling·Library 결과 확인, provider가 연결된 실제 생성 완료, audio/video provider 연결은 아직 검증하지 않았다. 따라서 Task 5는 서버 배포·마이그레이션 단계까지 완료됐지만 운영 기능 전체 완료로 판정하지 않는다.

### Task 6 브라우저 통합 검증 중단·원인 확인

- 증상: ysna 운영 화면에서 ready Source를 선택하고 `문서의 핵심 내용을 간단히 요약해줘.`를 실행했으나 `대화를 불러오지 못했습니다. 다시 시도해 주세요.`가 표시됐다. Studio 생성은 grounded 답변이 없어 `STUDIO_SETTINGS_INCOMPLETE`로 진행되지 않았다.
- 확인: 같은-origin BFF에 Origin/Referer를 포함해 직접 호출하면 CSRF 검증은 통과하지만 인증 쿠키가 없는 요청은 `401 AUTHENTICATION_REQUIRED`가 반환된다. API 컨테이너는 `DAON_RUNTIME_PROFILE=production`이고 `DAON_DEV_AUTH_BYPASS`가 없어, 실제 세션 주체가 없으면 질문·생성 API를 거부하는 구성이 맞다.
- 판정: 브라우저 통합 검증은 인증 세션 상태가 확인되지 않아 `BLOCKED`이며, 기능 성공으로 보고하지 않는다. 현재 화면의 Source 목록 표시만으로 질문 API 인증이 완료됐다고 볼 수 없다.
- 영향: 질문 답변·Studio grounded 설정·생성 polling·Library 반영을 아직 검증할 수 없다. 코드 수정으로 인증을 우회하지 않는다.
- 다음 조치: 신산님이 현재 브라우저에서 정상 로그인 세션을 확인한 뒤 같은 Notebook을 새로고침하고, 재시도 결과를 확인한다. 세션이 유효한데도 동일하면 그때 브라우저 요청의 trace와 API 인증 로그를 대조해 수정한다.
## 2026-08-22 Source 질문 재현 결과

- 상태: BLOCKED — 로그인/노트북 로딩 문제가 아니라 Source 질문의 외부 LLM 승인 단계에서 중단됨.
- 확인: 일반 질문 `안녕`은 `/questions` 200, `runs`·`egress_decisions` 생성 및 `completed` 확인.
- 확인: Raw Source 선택 질문은 프록시 접근 로그에서 `/questions` 403으로 확인되며 새 `run`/`egress_decision`이 생성되지 않음.
- 원인: 현재 실제 워크스페이스의 활성 egress 정책은 `allow_approved_external`이지만, Source 근거가 포함된 외부 LLM payload에는 `approved_authorization`이 필요하다. 현재 화면의 질문 흐름은 `/questions/authorization` step-up을 거치지 않아 `EGRESS_POLICY_DENIED`로 거절된다.
- 영향: Source 기반 질문과 그에 의존하는 Studio 생성은 현재 브라우저에서 수행 불가. 로그인 재시도나 노트북 재생성으로 해결되지 않음.
- 미검증: 승인 API를 통한 실제 step-up 완료 후 Source 질문 성공, 또는 정책/개발 전용 모델 변경 후 성공.
- 다음 조치: 신산님 승인 없이 외부 전송 승인 정책을 우회하지 않는다. 승인 UX를 연결할지, 개발 검증에서 외부 LLM 승인을 완화할지 결정 필요.

## 2026-08-22 Source 질문 Step-up 승인 연결

- 상태: IMPLEMENTED_LOCAL / 배포 전
- 담당: main agent (신산님 승인 후 직접 구현)
- 변경 파일: `apps/web/lib/question-answering-api.js`, `apps/web/components/actual-workspace.jsx`, `packages/ui/src/product-workspace-shell.jsx`
- 조치: Source 질문이 `EGRESS_POLICY_DENIED`를 받으면 현재 비밀번호를 저장하지 않는 일회성 승인 창을 표시하고 `/questions/authorization` 호출 후 `step_up_authorization_id`를 포함해 질문을 재실행하도록 연결했다. 일반 질문은 기존 경로를 유지한다.
- 검증: workspace lint 3 files PASS; product workspace/source knowledge tests 39/39 PASS; web production build 및 UI boundary PASS; `git diff --check` PASS.
- 미검증: API pytest는 로컬 환경에 `psycopg_pool` 의존성이 없어 수집 단계에서 중단됨; ysna Docker 재배포 및 실제 브라우저에서 비밀번호 입력 후 Source 질문 성공은 아직 미검증.
- 다음 조치: 변경을 커밋·푸시하고 ysna에 배포한 뒤 로그인 세션에서 Source 질문 승인·응답을 실제 검증한다.
- 배포: `a967fb8`을 ysna-server `web` 및 의존 API 재기동으로 배포했고 이미지 빌드·TypeScript·UI boundary가 PASS했다.
- 현재 예외: API 재기동으로 기존 브라우저 세션이 만료되어 실제 브라우저는 로그인 화면이다. 로그인 전 Source 승인창·응답 검증은 수행하지 않았다.

## 2026-08-22 질문 전송 인증 제거

- 상태: IMPLEMENTED_LOCAL / 배포 전
- 조치: 최초 접속 로그인 이후 질문·외부 LLM 전송마다 요구하던 `Source 질문 승인` 모달과 `step_up_authorization` 소비를 제거했다. Provider API Key는 서버의 LLM 연결 설정에서 사용하고, 사용자 비밀번호는 전송 요청에 재사용하지 않는다.
- 개발 프로필: `DAON_RUNTIME_PROFILE=development` + `DAON_DEV_AUTH_BYPASS=true`에서 세션 인증도 우회한다.
- 검증: API `py_compile` PASS; workspace lint PASS; Product Workspace/Source Knowledge 테스트 39/39 PASS.
- 미검증: ysna 재배포 후 실제 브라우저 Source 질문 성공은 아직 실행하지 않았다.
- 다음 조치: 승인된 개발 프로필로 API/Web를 배포하고, Source 질문 1회 및 일반 질문 1회를 실제 브라우저에서 확인한다.

## 2026-08-22 웹 로그인 세션 자동 만료 제거

- 상태: IMPLEMENTED_LOCAL / 검증 완료 / 배포 진행
- 조치: 웹 세션은 명시적 로그아웃 전 자동 만료하지 않도록 서버 검증 만료를 UTC 최댓값으로 변경하고, 로그인 쿠키를 영속 쿠키(10년)로 변경했다. 네이티브 access TTL은 유지했다.
- 변경 파일: `services/api/src/daon_user_api/identity.py`, `services/api/src/daon_user_api/runtime.py`
- 검증: API `py_compile` PASS; `git diff --check` PASS; Product Workspace/Source Knowledge 테스트 39/39 PASS.
- 미검증: 전원 종료 후 재접속을 포함한 실제 브라우저 수동 검증.
- 배포: commit `da635f1`을 origin에 push하고 ysna-server의 API/Web를 재빌드·재기동했다. 두 컨테이너 모두 `healthy` 확인.
- 미검증: 브라우저 캐시를 갱신한 뒤 전원 종료·재접속과 명시적 로그아웃 폐기를 실제 수동 검증해야 한다.

## 2026-08-22 Studio 생성 설정 보완

- 상태: IMPLEMENTED_LOCAL / 서버·브라우저 미배포
- 담당: 어울2 (Task5)
- 확인: 카드 11종은 모두 실제 설정 화면으로 진입하고, 설정 확인 후 비동기 generation job 접수·상태 polling·완료 Library 반영 경로를 사용한다. Source 계보는 기존 grounded Source Version Snapshot을 그대로 사용한다.
- 보완: 생성 설정에 `출력 언어`(한국어/English)를 추가하고 UI 입력, same-origin API 요청, job payload, settings snapshot, 재생성 settings 계약에 보존되도록 연결했다. 기존 카드 배치와 audio/video `unavailable` 정책은 변경하지 않았다.
- 변경 파일: `packages/ui/src/product-studio-model.js`, `packages/ui/src/product-studio-pane.jsx`, `services/api/src/daon_user_api/runtime.py`, `services/api/src/daon_user_api/studio_workspace.py`, `services/api/src/daon_user_api/studio_workspace_postgres.py`, `scripts/tests/product-studio.test.mjs`
- 검증: `node --test scripts/tests/product-studio.test.mjs` 8/8 PASS; API Studio async 관련 pytest 12/12 PASS.
- 미검증: 로그인 브라우저에서 실제 카드 클릭·언어 선택·job polling·Library 반영, ysna Docker 재배포, provider가 연결된 실제 생성 결과. 현재 Source 질문 승인 흐름과 외부 provider 상태는 별도 BLOCKED 항목을 유지한다.
- 다음 조치: 관련 diff 검토 후 commit/push하고, ysna 배포·로그인 브라우저 통합 검증 여부를 main agent가 판단한다.

## 2026-08-22 Source-only Studio 생성 경로

- 상태: IMPLEMENTED_LOCAL / 배포 전
- 담당: 어울2 (R1-NOTEBOOKLM-PARITY-I001-T5-SOURCE-ONLY)
- 조치: 질문 실행·Grounded run 없이 선택한 준비 완료 Source Version 목록만으로 Studio 생성 설정을 확정하고 기존 비동기 generation job에 접수하도록 연결했다. 서버는 Notebook/Workspace Source binding, Source 상태·삭제 요청, Evidence Span 계보를 확인하며, 근거가 없으면 완료를 가장하지 않고 `RESOURCE_UNAVAILABLE`로 거부한다.
- 변경: 기존 질문 기반 요청은 유지하면서 `source_only`, nullable run/run_result 계약을 추가했고, Source-only 결과의 settings snapshot·evidence reference·생성 시각은 기존 Library 저장 경로를 재사용한다. UI 3열 카드 배치는 유지하고 생성 설정 화면에 사용 Source Version 선택을 추가했다.
- 변경 파일: `apps/web/lib/product-workspace-api.js`, `packages/ui/src/product-studio-model.js`, `packages/ui/src/product-studio-pane.jsx`, `packages/ui/src/product-workspace-shell.jsx`, `services/api/src/daon_user_api/runtime.py`, `services/api/src/daon_user_api/studio_workspace.py`, `services/api/src/daon_user_api/studio_workspace_postgres.py`, `scripts/tests/product-studio.test.mjs`, `services/api/tests/test_notebooklm_studio_outputs.py`
- 검증: Source-only UI/API 입력 테스트 9/9 PASS; API Studio·worker·runtime HTTP pytest 13/13 PASS; Web production build 및 UI boundary PASS; `git diff --check` 오류 없음.
- 미검증: 실제 Postgres/worker에서 Source Version의 Evidence Span을 조회하는 Source-only 생성, 로그인 브라우저 클릭, ysna 배포·실제 provider 결과는 미검증. 오디오/동영상은 provider 미연결 `STUDIO_OUTPUT_UNAVAILABLE` 정책을 유지한다.
- 다음 조치: 관련 파일만 commit/push한 뒤 main agent가 ysna 배포 및 실제 Source 선택→job→Library 통합 검증을 판단한다.

## 2026-08-22 Source-only 정책 정규화 재작업

- 상태: IMPLEMENTED_LOCAL / 커밋·푸시 전
- issue_id: R1-NOTEBOOKLM-PARITY-I001-T5-SOURCE-ONLY
- 판정: ysna 실제 Source-only job의 `POLICY_PROJECTION_MISMATCH`는 Source-only 요청의 `ruleset_version_id=null`/입력 정책값을 grounded 경로와 동일하게 비교한 원인으로 확인됐다.
- 조치: Source-only 생성에서는 현재 유효한 Workspace 정책의 `review_condition`과 `ruleset_version_id`를 서버 기준으로 settings snapshot·generation request·output lineage에 정규화한다. 기존 grounded 생성은 기존 strict policy comparison을 유지한다.
- 검증: Postgres repository 포함 API Studio·worker·runtime HTTP pytest 32/32 PASS, 1개 외부 DB 의존 테스트는 기존 조건으로 skip. 실제 ysna 재배포·재시도는 아직 미실행.
- 미해결: 변경은 아직 commit/push하지 않았으며 실제 운영 DB/worker에서 `POLICY_PROJECTION_MISMATCH` 해소 및 Library 완료 상태 확인이 필요하다.
- 다음 조치: main agent 검토 후 커밋·푸시와 ysna 재배포를 진행한다.
- 추가 검증: Source-only 정책 정규화 회귀 테스트를 `test_studio_workspace_postgres.py`에 추가했으며 Postgres repository 포함 관련 pytest는 33 passed, 1 skipped이다. 로컬 compile 및 `git diff --check`도 통과했다.

## 2026-08-22 Studio Library stale context 필터 수정

- 상태: IMPLEMENTED_LOCAL / 커밋·푸시 전
- issue_id: R1-NOTEBOOKLM-PARITY-I001-T5-SOURCE-ONLY
- 판정: ysna에서 Source-only job과 `studio_outputs`/`output_versions`/`notebook_bindings` 저장이 완료됐지만 Library가 0으로 보인 원인은 Notebook context 생성 시점의 `studio_output_ids`·`output_version_ids`를 Product Studio 목록에 다시 적용한 stale snapshot 필터였다.
- 조치: `listProductStudioOutputs`는 `notebookId`를 전달한 서버 API가 이미 Notebook 범위 격리를 수행하므로 context ID 목록으로 재필터링하지 않고 API의 최신 `outputs`와 `studioLocks`를 그대로 반환하도록 수정했다. 기존 `listStudioOutputs` 및 생성/버전 작업의 context ID 검증은 유지했다.
- 검증: stale context에서 새 output을 보존하는 회귀 테스트를 추가했다. 관련 테스트 및 `git diff --check` 실행 결과는 작업 종료 보고에 기록한다.
- 미해결: 실제 ysna 브라우저 Library 표시 및 재배포 검증은 미실행. 이번 adapter 변경은 커밋·푸시 전이다.
- 다음 조치: main agent가 관련 diff 검토 후 커밋·푸시 및 ysna 재배포·브라우저 검증을 판단한다.
