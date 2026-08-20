# R1-M8-10 Windows Offline Studio 완료 보고

- Issue: `R1-M8-10-WINDOWS-OFFLINE-STUDIO-01-I001`
- 판정: `INCOMPLETE`
- Checkout: `codex/user-auth-screen-split` / baseline `dbe67f9bfe778b1ffa10b31f1e3e0faf807dd42b` + uncommitted Foundation A1 changes

## 판단 이유

Task 3B 재작업과 Task 3C Raw Source 보완으로 독립 검토의 코드 결함은 닫혔다. production `main.py`는 encrypted store projector와 Ollama-only OfflineStudioService를 조립하고, Desktop은 current session workspace에 결속된 exact raw import/context/settings/generate/edit/queue flow를 실행한다. Raw Source 원본은 Local AES-GCM 저장소에 보관되고 SourceVersion·IndexVersion·EvidenceSpan을 거쳐서만 Provider prompt로 전달된다. Settings/Run/Output은 동일 immutable Provider lineage를 공유하며 Operations/Settings는 실제 상위 상태를 사용한다. 다만 Ollama generation, Groq product deployment/compatibility, Provider actual encrypted generation persistence와 인증된 Desktop WebView 환경 Gate가 열려 있어 최종 판정은 `INCOMPLETE`다.

## 생성·변경 결과

- Knowledge Package, versioned Source/Output Sync와 Migration `0014`
- canonical Output import, dependency order와 reindex
- immutable KnowledgeContext/ModelSelection snapshots, encrypted Local Canon과 공통 Provider Draft Port
- Ollama installed-model catalog/chat, Groq·Upstage server-side structured-output Adapter
- command-bound Local API 11종과 Tauri Studio/Knowledge/Sync commands
- no-auto-transfer reconnect approval orchestration과 actual `/approve`
- 기존 Source/Conversation·Editor/Studio 3열을 유지한 Desktop-only Offline Studio
- Notebook Violet Desktop token, compact App Bar, Operations/Settings accessible Modal
- deterministic Evidence manifest/hash test
- Task 3B production composition, HMAC-bound workspace request, actual React DOM flow와 immutable Provider lineage
- Task 3C Local encrypted PDF/text/markdown Raw Source import, Canon Evidence lineage와 명시적 Desktop Source 선택
- Foundation A1 Security Audit PostgreSQL 영속화, Step-up exact replay/소비 멱등성, Migration `0015`
- Foundation A2 OutputVersion 내용 계보와 상태 전이 Version 분리, 동시 replay, Migration `0016`

## 검증 결과

- Fresh Local Service: `155 passed, 2 skipped`, coverage `84.93%`
- Fresh API: `381 passed, 27 skipped, 134 subtests passed`
- Task 3C 관련 Node `26 passed`, actual React raw import→context→generate/edit/queue `1 passed`
- Guarded Rust Full은 lib30/local5/native22/offline Studio3/offline Sync7/recovery44/workspace2 모두 PASS(155.3초). Windows 고유 Test Credential은 round-trip 후 revoke됐고 현재 실행의 격리 Target·Sidecar·관련 Process 잔류는 0이다.
- Desktop build PASS, Web production build PASS
- Product UI boundary: Desktop 29/Web 269 files, violations 0
- OpenAPI default `594AED…E0A`, R1-M8 `9C4803…6ECB` PASS
- Evidence hash test PASS

## Actual Gate

- PostgreSQL 15/18 disposable migration/rollback/reapply, downgrade fail-close, Source/Output import: PASS; cleanup remaining 0
- Foundation A1 PostgreSQL 15.18/18: `0001→0015`, 빈 `0015→0014→0015`, Security Audit 재시작 보존·Tenant RLS·교차 Tenant read/write 0·immutable chain·데이터 존재 downgrade SQLSTATE 55000·cleanup 0 PASS
- Step-up issuance는 raw/ciphertext 저장 없이 Key ID/Version 결속 HMAC으로 동일 Idempotency-Key 재시작 replay를 재구성하고, 동일 민감 작업의 lost-response retry만 허용하며 `identity.step_up.used` Audit는 최초 1건만 기록한다.
- Foundation A2 actual PostgreSQL: OutputVersion v2 previous chain, same-key 동시 요청 one-create/one-replay, RunSnapshot required/FK/immutable, RLS·transaction rollback, 빈 0016→0015→0016, 다중 Version downgrade SQLSTATE 55000, cleanup 0 PASS
- 기존 fixture subprocess는 test-only compatibility로도 제품 성공 경계에서 제거됨
- Native disposable TCP: Knowledge list→Step-up→copy/content→AES Local import→Preview→approval 전 transfer 0→`/approve`→Source→Output→reindex→revoke refresh PASS; listener/process/temp remaining 0
- 패키징 Local Service actual: Raw Source import→encrypted 저장→재시작 후 목록1, 저장 파일 평문 일치0 PASS; Sidecar SHA-256 `E58E65C6CD1A3F2052D4EF40E9AC654436464258A286ECC85267481F4B980A03`
- Ollama installed completion model: exact name/digest/capability 조회와 bounded request 도달 PASS, runner load 실패로 generation INCOMPLETE; 모델·서비스 변경 0
- Upstage active deployment actual: HTTP 200, 1174ms, schema true, citation true PASS; raw secret/response 증거 0
- Groq product selection actual: active deployment 없음. 공식 strict 후보 availability HTTP 403, chat 호출 0, compatibility를 PASS로 승격하지 않음
- Provider encrypted RunSnapshot·OutputVersion persistence actual: NOT RUN; 자동 계약만 PASS
- 격리 Desktop WebView actual: NSIS build 및 직접 실행 PASS, dark 로그인 화면 1202×932와 Tab 진입 확인. Native session 만료로 인증된 Raw Source·Operations·Settings, 1920×1080/200%는 NOT RUN
- 외부 배포: NOT AUTHORIZED

## 독립 검토 Findings

- CLOSED: production composition과 fixture 없는 safe unavailable model catalog
- CLOSED: exact React→Rust→Local flow와 DOM action/result
- CLOSED: immutable Settings/Run/Output Provider lineage 및 stale transport 0
- CLOSED: truthful Operations/Settings와 current-session workspace isolation
- CLOSED: Local production process Ollama-only, Groq·Upstage instance/credential 0
- OPEN(environment): actual Ollama/Groq/Provider generation persistence와 인증된 Desktop WebView 1920×1080/200% gates

## 조치

신산님 승인으로 어울1이 직접 인수해 Task 3C와 Windows Credential/Rust Gate를 닫았다. 후속은 Ollama runner 정상화, Groq active product deployment, 실제 Provider generation의 encrypted RunSnapshot·OutputVersion persistence, 인증된 Desktop 1920×1080/200% 화면 Gate다. 모든 actual Gate가 닫히기 전에는 `COMPLETED`로 판정하지 않는다.

## 2026-08-15 Foundation B3 후속 판정

- 판정: `FOUNDATION_B3_PASS / WORK_ORDER_INCOMPLETE`
- 지식 Source는 형식으로 등록을 거부하지 않는다. 원본과 digest를 보존하고, 형식별 수집 Adapter가 가능한 Representation을 만들며, 선택 LLM이 사용할 수 없을 때만 해당 Run을 안전하게 거부한다.
- Daon 2·2.5·3 생성 지식은 일반 text Evidence로 등록되어 Raw Source와 같은 Question Context에서 함께 사용된다.
- actual PostgreSQL 15에서 generated Source/Evidence/Index와 text Citation section locator를 확인했고 disposable DB/role remaining 0이다.
- actual Browser same-origin에서 Daon 승인 지식1+Raw Source1을 동시에 선택해 Question 200, 두 Resource 결속, `지식 구간`+`2쪽` Citation을 확인했다.
- BFF locator header와 OpenAPI locator enum 결함은 RED→GREEN으로 교정했다.
- B3는 PASS지만 전체 Work Order의 별도 Provider/Desktop 환경 Gate 때문에 상단 `INCOMPLETE` 판정은 유지한다.

## 2026-08-15 Foundation-first·Menu-by-menu 로컬 완료 판정

- 판정: `LOCAL_PHASE_A_B_COMPLETE / DELIVERY_PENDING`
- Phase A의 인증·Canon·Provider·입력·생성·same-origin 공통 계약 A1~A6을 완료했다.
- Phase B의 13개 메뉴를 `Domain → Repository/DB → API → BFF → React → actual Browser` 순서로 하나씩 완료했다.
- 대표 LLM 기능 시험은 승인 원칙대로 Upstage를 사용해 실제 schema·Citation 생성과 저장 계보를 검증했으며 9개 Provider 모두의 생성 성공을 요구하지 않는다.
- 마지막 메뉴 조직 정책은 기존 편집 권한을 넓히지 않고 Workspace에서 조직 강제 8필드와 effective 교집합만 읽기 전용으로 표시한다.
- fresh API `405 PASS·29 SKIP·137 subtests`, 주요 Node `124/124`, Web production build·TypeScript, boundary `273/0`, OpenAPI `81/103/138/31`을 통과했다.
- actual PostgreSQL 15은 Migration `0017`, 조직/Workspace 정책 분리와 cleanup0을 확인했다. actual Browser는 1920×1080에서 13번째 메뉴까지 same-origin과 내부값 비노출을 확인했다.
- 전체 Work Order 상단 판정은 Commit·Push·ysna 배포가 별도 승인 경계이므로 `INCOMPLETE`를 유지한다. 추가 산출물 Tile 6종은 계획대로 별도 설계 승인 전 disabled 상태를 유지한다.

## 2026-08-15 ysna-server 통합 배포 판정

- 판정: `YSNA_DEPLOYMENT_PASS / AUTHENTICATED_WEB_BROWSER_PASS / WORK_ORDER_INCOMPLETE`
- exact Commit `c4d626c020b8ff5ec42c9ab22f359c25b6dedf18`을 `origin/codex/user-auth-screen-split`에 Push하고 ysna-server에 적용했다.
- Backup SHA·restore-list·rollback images를 보존한 상태에서 Migration `0013→0017`, RLS/FORCE, API·Worker·Web build/recreate, public root/BFF health를 PASS했다.
- 로그인된 운영 Chrome에서 Source 5건, LLM 설정 9 Provider, 출력·버전, 동기화·승인, 조직 정책 8필드, 운영상태 5항목을 실제 조회했다. Browser console error/warn 및 내부 URL·credential·token 노출은 0이다.
- Commit·Push·ysna 배포 blocker는 해소됐다. 상단 전체 판정의 남은 이유는 Windows Desktop provider actual Gate뿐이다.
- 추가 산출물 Tile 6종은 승인 계획대로 disabled 상태를 유지하며 이번 배포 완료를 과장하지 않는다.

## 2026-08-15 Phase C 메뉴 1 화면 설정 판정

- 판정: `PHASE_C_MENU_1_COMPLETE / DESKTOP_WEBVIEW_ACTUAL_PASS`
- Web·Desktop 공통 Theme 계약, PostgreSQL 0018 저장·RLS·Replay·Rollback/Reapply, Windows Credential round-trip/revoke와 Desktop startup early paint를 확인했다.
- actual Desktop WebView 1920×1080에서 System·Light·Dark, 앱 재시작 저장값 복원, 초기화와 키보드 경로를 확인했다.
- 200%는 Windows 전역 DPI 변경이 아니라 명시적 evidence-only WebView 시각 배율 Harness로 확인했으며 이 제한을 증거 Transcript에 기록했다.
- 화면 설정 변경 전후 `Test Notebook` fixture hash, Source2, Output1은 동일했다. 로그인·Session·외부 Network·Credential 원문 사용은 0이다.
- Phase C 메뉴 1은 완료했다. 전체 Work Order의 별도 Provider/Desktop 생성 Gate 또는 후속 메뉴를 이 판정으로 완료 처리하지 않는다.

## 2026-08-15 Phase C 메뉴 2 라이선스 판정

- 판정: `PHASE_C_MENU_2_COMPLETE`
- 서명 License document의 schema/signature/product/organization/period를 서버에서 검증하고, 일반 사용자 safe read-only와 조직 관리자 Step-up apply를 Domain→PostgreSQL→Runtime/OpenAPI→same-origin BFF→Web/Windows에 연결했다.
- Private signing key는 production/Git/Browser/Desktop/로그/Evidence 0이며, test-only ephemeral RSA fixture만 process memory에서 사용한다.
- actual PostgreSQL은 fresh0019/rollback/reapply, FORCE RLS cross-tenant write0, append-only, Audit·Idempotency·live-row downgrade block, cleanup0을 통과했다.
- actual Browser 1920×1080은 read-only/admin/expired/limit 4상태와 exact same-origin GET을 통과했다. 만료·한도 도달은 신규 생성만 막고 기존 조회·Export 허용을 표시한다.
- API 전체 `419 PASS·30 SKIP·137 subtests`, Rust full 114 PASS, Node 관련 38 PASS, OpenAPI, Web/Desktop build와 boundary를 통과했다.
- actual Tauri contract-test Desktop WebView 1920×1080에서 read-only/admin/expired 3상태를 실제 Windows 키보드와 CDP로 확인했다. read-only apply control0, admin file/password/apply 각1, expired 신규 생성 중단·기존 조회/Export 허용을 screenshot으로 보존했다.
- License document·비밀번호 입력과 apply transport 호출은 0이다. evidence Tauri/Cargo/Vite·고유 temp target/dist/log는 exact cleanup했고 CDP9346·Vite4199 listener0을 확인했다.

### 2026-08-16 독립 리뷰 보완 판정

- 판정: `PHASE_C_MENU_2_REVIEW_FINDINGS_CLOSED`
- RS256 modulus 상한, verifier/기간 만료 뒤 exact replay-before-Step-up, changed fingerprint write0, feature별 Action과 resource별 actual 생성 한도 enforcement를 닫았다.
- Studio 생성·보고서, SourceVersion, source object bytes는 각 실제 생성 transaction 내부에서 tenant advisory lock과 한도 검사를 수행한 뒤 생성 row와 함께 commit한다. 조회·Export는 계속 허용한다.
- actual PostgreSQL 15 두 연결에서 same/different fingerprint apply와 source_versions/storage_bytes 한도 경쟁을 확인했고, Audit1·부분 소비0·cross-tenant write0·cleanup0이다.
- safe projection은 OpenAPI와 동일한 status/feature ID/masked ID/timestamp/resource/warning 경계를 Web JS·Desktop JS·Rust 모두 fail-close한다.
- 최초 Browser evidence의 실제 1920×1071 오표기를 바로잡았다. Chrome 151 actual viewport 1920×1080으로 read-only/admin/expired/limit 4상태를 재캡처하고 Network GET4를 새 Evidence에 결속했다.
- fresh 전체 결과는 API `424 PASS·31 SKIP·137 subtests`, Rust contract `115 PASS`, 관련 Node+Manifest `41 PASS`, OpenAPI `84/107/143/31`, Web/Desktop production build, boundary Web299/Desktop30 violations0이다. Manifest 갱신 전 예상 hash RED1은 현재 bytes로 갱신한 뒤 GREEN했다.

### 2026-08-16 2차 독립 리뷰 보완 판정

- 판정: `PHASE_C_MENU_2_SECOND_REVIEW_FINDINGS_CLOSED`
- License Idempotency는 tenant·workspace와 canonical 전체 envelope를 결속하며 최초 apply와 선행 replay가 같은 fingerprint 함수를 사용한다. claims가 같아도 key/algorithm/signature 변경은 conflict/write0이다.
- PostgreSQL Studio 생성은 transaction 내부 replay를 License 만료·한도 검사보다 먼저 판정한다. 성공 뒤 한도 도달 또는 만료 뒤 같은 key는 기존 결과/write0, 신규 key는 fail-close하며 non-PG/Fake는 Runtime 선행 검사를 유지한다.
- `storage_bytes` pending 정책은 projection과 enforcement 모두 `pending|completed`로 통일했다. actual PostgreSQL projection used/remaining/status/creation_allowed가 실제 차단과 일치한다.
- 최초 Browser GET1 기록은 최종 PASS 근거에서 폐기했고, 실제 1920×1080 4상태 Network GET4만 사용한다.
- fresh 결과는 actual PG `3 PASS·cleanup0`, focused `46 PASS·1 SKIP`, API 전체 `431 PASS·32 SKIP·137 subtests`, Node/Manifest `10 PASS`, OpenAPI default `75/94/120/31`·R1-M8 `84/107/143/31`, Web/Desktop build, boundary Web299/Desktop30, 관련 Ruff PASS다. Rust/Desktop 제품 변경은 없어 직전 full Rust115와 actual Windows evidence를 유지한다.

### 2026-08-16 3차 독립 리뷰 보완 판정

- 판정: `PHASE_C_MENU_2_THIRD_REVIEW_FINDING_CLOSED`
- Runtime은 duck-typed capability flag를 신뢰하지 않고 exact PostgreSQL Workspace/Report repository와 실제 creation enforcer 조합만 authoritative transaction으로 인정한다.
- Fake repository가 true 속성을 위조하거나 실제 Service로 감싸져도 선행 License fail-close를 유지한다.
- 공개 계약 변경0이며 focused `46 PASS·1 SKIP`, API 전체 `431 PASS·32 SKIP·137 subtests`를 fresh 통과했다.

### 2026-08-16 Phase C 메뉴 3 사용자 설명서 판정

- 판정: `PHASE_C_MENU_3_VERIFIED_COMPLETE`
- Settings의 `사용자 설명서`에 공통 Daon 문서 Hub를 연결했다. Release 1.0.0, 검색, Web 읽기, DOCX/PDF 다운로드를 제공하며 Web은 strict same-origin 상대 경로와 allowlisted manifest만 사용한다.
- 한국어 Markdown 정본 `Daon Getting Started`, `Daon 사용자 설명서`, `Daon 지식·LLM 활용 가이드`를 작성했다. 현재 검증된 Notebook 3열 화면·Source·질문/Citation·Studio·Library·설정·운영상태·Version/검토/승인/Export만 기술하며 Notebook 홈·생성과 로그인 연결은 후속 Phase로 명시한다.
- `compact_reference_guide` exact token과 `editorial_cover` pattern으로 DOCX 3종·PDF 3종을 생성했다. 각 6페이지이며 DOCX/PDF 각각 18페이지 전수 렌더, 한글 glyph·TOC·내부 링크·표·이미지 alt text·머리말/꼬리말·페이지 번호·잘림/overlap과 a11y high0/medium0/low0을 확인했다.
- actual Browser 1920×1080에서 목록 3종, 검색 1건, Web 읽기, DOCX/PDF 다운로드를 실제 클릭했고 same-origin Network 11행·unique path 4개, console/internal URL/secret 노출0, listener/process cleanup0이다.
- focused Manual `5/5`, Web production build/TypeScript, Desktop Vite build, product lint 3파일, Product UI boundary 300파일 violations0을 통과했다. 관련 회귀의 선행 stale exact-shape test 2건은 Phase C2 adapter 및 기존 Studio state 확장에 대한 기대값 부채로 분리했으며 이번 Manual 변경과 무관하다.
- Release/Web manifest와 Phase C3 Evidence manifest는 파일명·bytes·SHA256·MIME·version·language·scope 및 screenshot/Network/document bytes를 deterministic하게 결속한다. commit/push/deploy와 로그인·Notebook Domain/API/Home 변경은 0이다.

### 2026-08-16 Phase C 메뉴 3 독립 리뷰 보완 판정

- 판정: `PHASE_C_MENU_3_REVIEW_FINDINGS_CLOSED`
- Browser client의 승인 trust anchor를 Release `1.0.0`과 정본 document ID 3종 exact set으로 고정했다. 문서 version은 승인 Release와 exact 일치해야 하며 미승인 `9.9.9`, rogue ID, 혼합 version은 `MANUAL_MANIFEST_INVALID`로 fail-close한다.
- Evidence builder는 Network JSONL을 직접 parse해 `captured_requests=11`과 `unique_request_paths=4`를 분리 계산하며 hardcode를 제거했다.
- DOCX/PDF 제품 bytes와 Release/Web manifest는 변경하지 않았고 기존 36페이지 렌더·a11y 결과와 각 artifact hash는 유지된다.

### 2026-08-16 Phase C 메뉴 3 2차 독립 리뷰 보완 판정

- 판정: `PHASE_C_MENU_3_SECOND_REVIEW_FINDING_CLOSED`
- `readManualDocument`·`downloadManualAsset`의 caller 전달 manifest도 내부 asset fetch 전에 strict projection으로 재검증한다.
- absolute URL, traversal href, rogue document ID/version, MIME/SHA/shape 변조는 `MANUAL_MANIFEST_INVALID`로 차단하며 실제 fetch count는 0이다.
- 정상 projected manifest의 same-origin·bytes/SHA/MIME 검증과 문서 산출물 bytes는 변경하지 않았다.

## Phase D Notebook Home implementation

- 판정: `PHASE_D_NOTEBOOK_HOME_PASS`.
- Notebook Domain·Migration0020·PostgreSQL repository·Runtime/OpenAPI·same-origin BFF·strict Web client·Notebook Home UI를 수직 연결했다. 공개 Route는 create/list/get/update-title만 있으며 delete/share/recommend/template는 0이다.
- actual PostgreSQL disposable Gate는 fresh migration, rollback/reapply, non-superuser RLS/scope, immutable/appended metadata, same-key replay, two-connection notebook quota atomicity, live-row downgrade block과 cleanup0을 통과했다.
- actual Browser는 1920×1080 DOM viewport에서 Home states, search/sort/Grid/List/settings, existing reentry와 create→empty context를 확인했고 Network는 same-origin Notebook path만 사용했다.
- 독립 리뷰 재작업에서 Notebook binding은 실제 PostgreSQL target 존재·tenant/workspace scope를 검증한 뒤 immutable하게 저장하고, selected context를 Source/Knowledge/Conversation/Studio Output/Output Version/Generation Settings ID로 투영한다. actual PostgreSQL review4c `4/4`가 이 ID 결속·scope·empty projection을 보증한다. Bounded Browser Harness는 해당 actual ID projection을 production adapter에 주입하되 filename·answer·output 표시 내용은 test-only fixture로 해석해 `ProductWorkspaceShell`의 기존-versus-empty 소비 동작을 확인했다. 따라서 화면 증거는 표시 본문이 PostgreSQL에서 조회되었다는 증거가 아니다. 공개 HTTP/OpenAPI는 승인된 4 route 그대로이며 context route 추가0이다.
- Web ETag는 server와 동일한 exact `"notebook:[1-9][0-9]*"`만 허용하고 create/get/update 응답 및 PATCH 입력에서 fail-close한다. 생성 dialog는 공통 modal helper로 initial focus, Tab/Shift+Tab wrap, Escape, background inert, opener focus return을 제공하며 실패 시 allowlisted 오류만 표시하고 중복 submit을 차단한다.
- title update는 Metadata/Activity/Idempotency와 중앙 `notebook.title_updated` Audit을 하나의 PostgreSQL transaction으로 기록한다. exact replay의 Audit duplicate는 0이고 Audit 실패 시 네 종류 write가 모두 0임을 actual PostgreSQL에서 확인했다.
- Evidence Harness 기본 body margin이 overflow/scrollbar를 만들어 캡처를 줄인 원인을 확정했다. test-only margin/overflow만 교정한 뒤 inner/client/scroll 1920×1080, DPR1에서 단일 정상 screenshot을 저장했다. 원본 JPEG/JFIF는 `image/jpeg`, 54,401 bytes, 정확히 1920×1080이며 후처리·format conversion 없이 정식 Screenshot Gate를 통과했다. 제품 코드 변경은 0이다. Phase E 운영 Route/login assembly와 deployment는 범위 밖이다.

## Phase E Product assembly and login

- 판정: `PHASE_E_PRODUCT_ASSEMBLY_LOGIN_LOCAL_COMPLETE`.
- 신산님 승인 계약에 따라 `GET .../notebooks/{notebook_id}/context`를 Runtime·OpenAPI·same-origin BFF·Web Adapter에 연결하고 Source·Question·Studio의 read/write에 canonical `notebook_id`를 필수화했다. 다른 Tenant/Workspace/Notebook binding은 조회·쓰기 0이며 Workspace만으로 default Notebook을 선택하지 않는다.
- Source 등록은 Source/Version binding, Question은 Conversation/Run/Result/Citation/Thread binding/Audit, Studio는 Generation settings/Output/Version binding을 각 생성 transaction에 원자 결속한다. 동일 idempotency replay는 중복 Resource/Binding/Run/Audit 0이고 실패 transaction은 write0이다.
- actual PostgreSQL 15 disposable Gate는 Notebook 기반 `4/4`와 Source·Question·Studio `6/6`을 통과했다. FORCE RLS cross-scope0, selected Context empty/existing, same-key replay/different payload conflict, 2-connection License quota 원자성, title Audit 원자성과 cleanup db0/role0을 확인했다.
- 로그인 성공은 `/notebooks` Home으로 이동하고, 선택 Notebook만 3열 route로 조립한다. actual Browser 1920×1080에서 로그인→Home→기존 Notebook Source1·보존 Conversation·Library1, back/reload/direct URL, session expiry와 새/기존 Context 재진입을 확인했다. production route/build의 test Harness import는 0이다.
- current Web session logout은 Step-up 없이 해당 cookie-bound session만 revoke하고 다른 session/device 입력을 받지 않는다. exact same-origin BFF Origin·Referer/CSRF를 요구하며 Runtime은 운영 Cookie를 `Secure; HttpOnly; SameSite=Lax; Max-Age=0`으로 제거한다. 다른 session/device revoke는 기존 Step-up 계약을 유지한다.
- session/refresh revoke와 immutable `SELF_LOGOUT` audit intent는 동일 SQLite transaction에 commit한다. 중앙 Audit은 commit 후 deterministic event ID로 idempotent projection한다. outbox insert/pre-commit 실패는 전부 write0, 중앙 실패는 revoke1+pending1, restart replay는 central1+delivered1·duplicate0이다. 중앙 Audit과 물리적으로 동일 transaction이라고 과장하지 않는다.
- actual Browser에서 최초 logout→Back은 BFCache 인증 DOM 재표시 RED였다. Home·3열이 `pageshow.persisted`/`popstate`마다 보호 UI를 loading으로 숨기고 실제 server session을 재검증하도록 교정했다. 이후 logout→Back·Forward·direct Notebook URL은 모두 `/` 로그인으로 replace되고 보호 DOM0, console warning/error0이었다.
- Evidence는 Home·3열·session-expired·logout login 원본 JPEG 4장(각 exact1920×1080, 후처리0), product Network 56행/8 paths와 logout Network 43행/8 paths를 결속한다. Browser 경로는 전부 `127.0.0.1:4220` same-origin이고 내부 API destination·credential/cookie/token 원문은 0이다. local bounded session fixture는 운영 배포·운영 자격 검증 증거로 과장하지 않는다.
- fresh 검증은 API `454 PASS·38 SKIP·137 subtests`, self-logout focused `4/4`, Identity+Runtime `39 PASS·2 subtests`, Node focused+Evidence `47/47`, OpenAPI `75 paths/94 operations/120 schemas/31 errors`, Web production build·TypeScript·boundary347/0, Desktop Vite build, diff-check·staged0을 통과했다. external deploy/commit/push는 이번 Phase E 범위에서 0이다.

### Phase E Review1 보완

- Windows Native 7 operation에 canonical Notebook scope를 query/header/body exact로 결속하고 Citation을 8필드+locator projection으로 고정했다. Rust actual wire/contract full은 `118/118 PASS`다.
- SELF_LOGOUT outbox는 startup bounded recovery(최대32건/0.25초), poison pending 유지, concurrent central duplicate0, intent UPDATE·DELETE 금지와 delivered NULL→timestamp 단방향 trigger를 갖춘다.
- Source list와 processing status SQL은 selected `notebook_bindings`를 선행 JOIN한다. Web Context/logout은 exact safe projection만 반환한다.
- actual Browser에서 Session 응답 2초 지연 중 history Back의 protected text·answer·interactive는 0이었다. expired reload/direct URL도 1.2초 지연 구간 protected0 뒤 `/` login으로 replace됐다.
- disposable actual PostgreSQL fresh `0001→0020`에서 exact 18 test ID를 non-skip 실행해 `18/18 PASS`, 종료 `db=0 role=0`을 확인했다. Evidence transcript는 DSN/password/raw SQLSTATE 원문 0이다.
- fresh API는 `456 PASS·38 SKIP·137 subtests`, Review focused Node `46/46`, OpenAPI/BFF `56/56`, Web build·TypeScript·boundary348/0, Desktop Vite build, Evidence hash test `1/1`, diff-check·staged0을 통과했다. Evidence manifest SHA-256은 `C9269188E2FD2971204F39B882608B72AF46E125A3D5A3D7981EA72D3771E504`다.
- 별도 legacy Desktop monolith는 `22/29`이며, 5건은 현재 필수 Notebook fixture·textarea·Safe error UI와 과거 React 기대 차이, 2건은 본 Review 범위 이전 Tauri config/PostCSS protected baseline 기대 차이다. Review1 Native wire·Rust full·Web focused·build 결과와 분리했으며 해당 legacy 기대를 맞추려고 안전 UI/보호 기준선을 완화하지 않았다.

### Phase E Review2 Critical 재개

- 판정: `REWORK_IN_PROGRESS`.
- production `main.jsx`는 `<DesktopShell />`에 Notebook selection을 제공하지 않고 DesktopShell의 기본 `notebookId=null`은 Adapter를 항상 unavailable로 만든다. 따라서 Review1의 legacy React 5건은 stale가 아니라 Windows 제품 조립 누락을 드러낸 실제 회귀로 정정한다.
- 완료 판정은 서버 권위 Native Notebook Home→명시적 선택→3열, create/existing/back/refresh/direct deep-link/history/workspace switch/expiry/logout actual Gate와 관련 legacy UI GREEN 뒤에 다시 수행한다.

#### Review2 구현 결과

- 판정: `CODE_AND_CONTRACT_PASS / ACTUAL_WINDOWS_WEBVIEW_PENDING`.
- production Desktop은 로그인 후 Notebook Home을 표시하고 사용자 명시 선택/생성 결과만 Native get/context 재검증 뒤 3열에 전달한다. default·첫 Notebook·fixture prop 선택은 0이다.
- Native Home4+Workspace7 실제 wire, selected Context projection, Workspace 전환 fail-close, BFCache conceal을 회귀로 고정했다. Review2 관련 legacy React 5건은 모두 GREEN이다.
- 실제 Windows/Tauri WebView의 신규 로그인→Home→선택→3열→logout/history 캡처 전까지 전체 판정은 `INCOMPLETE`다.

### Phase E Review3 비동기 경합 보완

- 판정: `CODE_REVIEW_PENDING / WINDOWS_ACTUAL_BLOCKED`.
- Desktop Notebook 요청에 monotonic epoch를 결속했다. Session identity·Workspace·logout·hash/popstate·pagehide·unmount가 이전 요청을 즉시 무효화하고, list/get/context/create 각 await 뒤 exact Session·Workspace·target hash가 일치할 때만 Home/3열/hash를 갱신한다.
- 최초 actual React RED는 이전 Notebook context 지연 중 Workspace 전환 뒤 남아 있던 old hash가 새 Session에서 다시 열려 `OLD SELECTED`가 노출된 것이었다. Session identity 변경 시 실제 hash도 Home으로 replace해 GREEN했다.
- stale list/context/create/popstate 응답은 이전 title/source/answer/citation/card/hash를 렌더하지 않는다. 3열 component key는 Session+Notebook으로 결속하고, BFCache 보호막의 이전 finally도 별도 protection epoch가 최신 검증을 덮지 못한다.
- actual React A-D 경합 `1/1`, 정상 E와 로그인·권한·logout 경쟁 `1/1`, runner behavior `4/4`, Native/Adapter/Phase E focused `24/24`, Desktop build·lint·boundary36/0은 PASS했다. full Desktop은 `28/30`이며 기존 Tauri config/PostCSS 보호 baseline 2건만 분리된다.
- Windows UI/Tauri actual은 지시대로 재시도하지 않았으므로 screenshot/wire/credential 신규 증거0이다. 코드·계약 재검토 전이며 Windows actual blocker를 완료로 과장하지 않는다.

### Phase E Review4 Session 재검증 보완

- 판정: `CODE_REVIEW_PENDING / WINDOWS_ACTUAL_BLOCKED`.
- protection epoch가 reveal에만 적용되던 결함을 actual React deferred status로 재현했다. 늦은 unauthenticated/rejection/old Workspace status가 최신 authenticated Session과 Home을 덮는 RED였다.
- revalidation은 epoch·Session·Workspace·hash snapshot을 캡처하고 status 성공 직후, Session 적용 전, Notebook open/load 전에 latest compatibility를 검증한다. stale success와 catch는 상태·hash·보호막을 변경하지 않는다.
- A-F 개별 GREEN은 old authenticated/unauthenticated/rejection, rich answer+exact Citation context, stale Question/Studio interaction command0, 정상 Home→선택→3열을 포함해 7/7이다.
- focused actual React9/9, Phase E·Native·Adapter·runner24/24, Desktop lint/build·boundary36/0 PASS다. full Desktop35/37 중 기존 Tauri config/PostCSS baseline2는 이번 변경과 무관하게 유지된다.
- Windows actual을 재실행하지 않았으므로 전체 판정과 blocker는 변경하지 않는다.
