# Phase E Product Assembly actual Gate

- 시각: 2026-08-20 13:59 KST
- 실행 경계: local test-only Vite middleware + 실제 production `AuthLanding`, `NotebookHomeWorkspace`, `NotebookProductWorkspace`, `ActualWorkspace`, `ProductWorkspaceShell`
- 인증 경계: test-only ephemeral identity를 로그인 UI로 입력했고, server middleware가 `HttpOnly; SameSite=Lax; Path=/` cookie를 발급했다. Browser code·DOM·Network log에 password/cookie 원문을 기록하지 않았다.
- Harness 경계: `scripts/test-harness/phase-e-product`이며 production route/build import는 0이다. 운영 인증·ACL을 통과했다는 증거가 아니라, 승인된 bounded local session fixture를 정상 UI/BFF 경로로 소비한 제품 조립 증거다.

## 실제 Browser 결과

1. 비인증 `/`는 로그인 3화면 중 로그인 화면을 표시했다.
2. 로그인 UI 성공 후 `/notebooks`로 이동했으며 Workspace만으로 Notebook을 자동 선택하지 않았다.
3. 기존 `notebook-existing` 선택 후 `/notebooks/notebook-existing` 3열 화면에서 bound Source 1, 보존 Conversation answer, bound Studio Output 1을 표시했다.
4. Browser back은 Notebook Home으로 복귀했다.
5. 직접 URL과 reload 후에도 동일 selected Context를 다시 읽어 Source·Conversation·Library를 보존했다.
6. test-only session expiry 후 reload는 `location.replace("/")`로 로그인 화면에 복귀했고, 만료 상태의 직접 Notebook URL도 로그인으로 차단했다.
7. 승인된 current-session self logout을 3열 설정 메뉴에서 실행했고 `/` 로그인 화면으로 `replace`됐다. test-only cookie는 만료됐으며 cookie/token/password 원문은 기록하지 않았다.
8. 최초 실제 Back은 BFCache에 남은 인증 DOM을 재표시해 RED였다. Home·3열이 `pageshow.persisted`/`popstate`마다 보호 UI를 즉시 loading으로 숨기고 서버 `/session`을 재검증하도록 교정했다.
9. 교정 후 logout→Back·Forward와 logout 후 직접 `/notebooks/notebook-existing` URL은 모두 실제 session 401 뒤 `/` 로그인으로 replace됐고 Workspace·Notebook 보호 DOM은 0이었다. client-only auth flag로 통과시키지 않았다.

## Review1 BFCache 재검증

- `/session` 응답을 test-only middleware에서 2,000ms 지연한 뒤 history Back을 시작했다. 재검증 완료 전 protected text 0, answer 0, button/input/textarea/link 0을 실제 DOM·접근성 projection에서 확인했다. 성공 응답 뒤에만 Home root가 `data-session-validated=true`로 복구됐다.
- session expiry 뒤 1,200ms 지연 상태에서 현재 Notebook reload와 직접 `/notebooks/notebook-existing` 진입을 각각 확인했다. 초기 100ms 구간의 Source·Conversation protected text와 interactive control은 0이었고, 최종 URL은 `/` 로그인으로 replace됐다.
- 제품 root는 `pagehide`에서 동기적으로 `hidden`·`inert`·`aria-hidden`을 적용하고 `pageshow.persisted`/`popstate`의 서버 재검증 전에는 해제하지 않는다. React 비동기 state만으로 BFCache를 신뢰하지 않는다.

## 로그아웃 감사 원자성

- current session/refresh revoke와 immutable `SELF_LOGOUT` audit intent는 동일 SQLite connection/transaction에서 commit한다.
- 중앙 Audit store는 commit 뒤 deterministic event ID로 projection한다. 중앙 장애 시 logout과 pending intent가 함께 남고, restart/replay에서 중앙 event 1건·delivered 1건으로 수렴한다.
- outbox insert 실패와 pre-commit 실패는 session/refresh/outbox/central 모두 write0이다. raw cookie/token/digest/credential은 outbox column·Evidence에 0이다.
- Review1에서 startup bounded dispatcher를 추가했다. 최대 32건/0.25초의 pending intent만 처리하며 poison row는 pending으로 유지한다. concurrent dispatcher는 deterministic event ID로 central duplicate 0이다.
- SQLite trigger가 audit intent field UPDATE·DELETE를 차단하고, `delivered_at`은 NULL→timestamp 1회만 허용한다. actual restart test는 사용자 logout 재호출 없이 pending1→central1/delivered1 수렴을 확인했다.

## Review1 Native·Projection·SQL 경계

- Windows Native Source list/processing/Citation/Studio list, Upload, Question, Studio write의 7 transport는 canonical `notebook_id`를 exact query/header/body로 보낸다. missing/duplicate/invalid는 network 전 fail-close한다.
- Native Citation은 승인 8필드와 exact locator만 decode하며 extra/internal field를 거부한다. actual Rust wire test 7 operation과 rich Citation decode가 GREEN이다.
- Web Notebook Context와 UI Adapter는 answer 및 Citation 8필드/locator를 새 safe object로 projection한다. logout도 exact envelope/data/meta를 검증한 뒤 `{status,replayed}`만 반환한다.
- Source list와 processing status repository SQL은 `notebook_bindings`를 tenant/workspace/notebook/target version exact로 선행 JOIN한다. Runtime 후처리만으로 선택 범위를 판정하지 않는다.
- actual PostgreSQL 상세 결과는 `phase-e-review1-postgres-transcript.md`에 test ID 18개와 cleanup `db=0 role=0`로 기록했다.

## 화면·Network

- 실제 viewport: 1920×1080, DPR 1.
- 원본 screenshot은 브라우저 backend가 JPEG/JFIF로 반환했으며 확장자를 `.jpg`로 정직하게 기록했다. crop/padding/resize/format conversion 0.
- `phase-e-notebook-home-1920x1080.jpg`: bytes 38143, SHA-256 `941E31C56D490E5AF36B1D3A3E473DF06180D66E0FA274C88A146DD9A5B0D4B7`
- `phase-e-notebook-product-1920x1080.jpg`: bytes 74161, SHA-256 `E071EE2F5F586360FB50807D3699517B6606FED04EA920E897320916B1F2DCF5`
- `phase-e-session-expired-1920x1080.jpg`: bytes 22601, SHA-256 `87DF6552FEF6395CB350623059C02C2C75E3F8C39A82463FA325C1F0F6015378`
- `phase-e-logout-login-1920x1080.jpg`: bytes 22601, SHA-256 `87DF6552FEF6395CB350623059C02C2C75E3F8C39A82463FA325C1F0F6015378`
- `phase-e-product-network.jsonl`: 56 captured request rows, 8 unique paths, host는 전부 `127.0.0.1:4220`, absolute/internal destination 0. React StrictMode와 초기 교정 run을 포함하므로 동일 GET이 반복된다.
- 제품 요청은 `/bff/api/session`, `/bff/api/auth/login`, `/bff/api/workspaces/.../notebooks`, `/context`, Notebook-scoped `/sources?notebook_id=...`, `/bff/api/studio-outputs?...&notebook_id=...`의 same-origin 경로다.
- `/__phase_e/expire` 1건은 session-expiry를 발생시키는 test-only local control이며 production 경로가 아니다.
- `phase-e-logout-network.jsonl`: 43 captured request rows, 8 unique paths, 전부 `127.0.0.1:4220` same-origin이고 `/bff/api/session/logout`을 포함한다. absolute/internal destination 0.
- Browser 실제 error message 0. 도구가 Vite 연결·React DevTools 안내 info를 error-level query 결과에 포함했으나 메시지 분류상 application/console error는 0이었다.
- 실제 local fixture cookie 제거 후 Back/direct URL의 `/bff/api/session`이 인증 실패했음을 UI 행동으로 확인했다. 운영 `Secure; HttpOnly; SameSite=Lax; Max-Age=0` 속성은 Runtime focused test의 응답 Header 계약이며 local HTTP Harness 성공으로 과장하지 않는다.

## 정리

- Browser tab closed, viewport override reset.
- owned Vite PID 종료, listener 4220=0.
- credential/cookie/password 원문 Evidence 0.

## Review2 Windows production 조립 재검증

- production `main.jsx → DesktopShell`의 `notebookId=null`로 3열 Adapter가 항상 unavailable이 되는 Critical을 재현하고 고정 prop 수용을 폐기했다.
- 인증 후 Native `notebook_list` Home을 표시하며 첫 항목·고정 ID·Workspace ID로 자동 선택하지 않는다. 사용자 카드/생성 결과만 `notebook_get`+`notebook_context` 서버 재검증 후 selected Context Adapter와 3열을 구성한다.
- actual React에서 로그인→Home→새 Notebook 생성→empty Context 3열→Home 복귀→기존 Notebook 명시 선택→bound Source→Question command를 확인했다. Workspace 전환은 이전 Source를 제거하고 Home으로 fail-close한 뒤 새 Workspace에서 다시 선택한 Context만 표시했다.
- 공통 3열 `knowledgeContext`와 Windows legacy flat source 입력 불일치를 RED로 고정하고, 선택 Notebook에 결속된 Source resource/version만 Question DTO로 projection했다. Native Citation은 Web BFF를 만들지 않고 안전한 hash와 `workspace_citation_content`를 사용한다.
- Native actual wire는 Notebook Home 4개와 Source·Question·Studio 7개, 합계 11 operation의 고정 HTTPS path/header/body를 검증했다.
- `pagehide` 동기 conceal+inert, `pageshow.persisted`·history event의 Native session/Notebook 서버 재검증 전 reveal 금지를 적용했다.
- focused: Native production 4/4, Windows Adapter/Modal/Visual 10/10, Review2 관련 legacy React 5/5, Rust lib 33/33, Native 11-wire 1/1, Desktop Vite build PASS. monolith 27/29의 나머지 2건은 기존 generated Tauri config/PostCSS baseline이다.
- Rust full은 제품/계약 compile과 lib를 통과한 뒤 Windows Credential Manager 환경 2건이 `*_WRITE_FAILED`였다. 실제 Windows/Tauri WebView 신규 캡처는 실행하지 않아 overall blocker는 유지한다.

## Review3 async stale response 및 runner 계약

- actual React Tree에서 네 경합을 fresh 실행했다. A) 이전 Workspace list 지연 뒤 Session/Workspace 전환, B) 이전 Notebook get 성공 뒤 context 지연 중 전환, C) create 지연 중 logout, D) 두 popstate 응답 역순이다. 최초 RED는 B에서 이전 hash가 새 Session effect에 재소비되어 `OLD SELECTED`가 다시 렌더된 1건이었다.
- DesktopShell은 request epoch와 desired hash를 Session identity·Workspace·logout·hash/popstate·pagehide·unmount에서 무효화한다. 각 list/get/context/create await 뒤 epoch+Session+Workspace+target hash를 확인하며, identity 변경은 실제 hash도 Home으로 replace한다. stale 결과는 title/source/answer/citation/card/hash를 갱신하지 않는다.
- `ProductWorkspaceShell` key는 Session+Notebook으로 결속되어 같은 Session에서 Notebook을 바꿔도 이전 질문·Citation state를 재사용하지 않는다. 보호막 revalidation은 별도 monotonic protection epoch로 이전 finally의 조기 reveal을 차단한다.
- focused actual React 결과는 async A-D `1/1 PASS`, 기존 정상 E(login→Home→create empty→Home→existing select→3열) 및 Login/권한/logout 경쟁 `1/1 PASS`다. Desktop 관련 focused 24/24, Native production 5/5, runner behavior 4/4, Desktop build와 boundary36/0도 PASS했다.
- full Desktop monolith는 fresh `28/30`; 실패 2건은 Review2부터 분리된 기존 Tauri generated config fail-closed 기대와 PostCSS successor blob baseline이며 Review3 제품 경로 실패가 아니다.
- Windows evidence runner는 `waitForTargetableWindow(120_000)`, target/form 확보 전 입력0, finally owned child/API/config/unique test Credential cleanup, standalone seeder·obsolete `targetable_windows` marker0을 4/4로 검증했다.
- 이번 Review3에서는 Windows UI/Tauri launch·credential write·screenshot·wire actual을 재시도하지 않았다. 판정은 `CODE_REVIEW_PENDING / WINDOWS_ACTUAL_BLOCKED`이며 이전 Browser JPEG/Network를 새 Windows actual로 과장하지 않는다.

## Review4 session revalidation stale protection

- 실제 React deferred status를 역순으로 완료하는 A-F를 개별 subtest로 실행했다. 최초 RED는 B/C의 늦은 unauthenticated/rejection이 최신 authenticated Home을 Login으로 덮었고, D/E의 이전 Workspace status/context가 최신 Home loading을 교란한 것으로 `2 PASS / 5 FAIL`이었다.
- 원인은 protection epoch가 이전 `finally`의 reveal만 막고 `sessionBridge.status()` 성공·실패 뒤 `applyNativeSession`, Notebook open/load 전에는 최신 여부를 검사하지 않은 것이다.
- 각 revalidation은 protection epoch, Session ID, Workspace ID, target hash snapshot을 캡처한다. status await 직후, Session 적용 전, open/load 전에 effect mounted+epoch+Session+Workspace+hash를 exact 대조한다. stale success/rejection은 모두 no-op이다.
- Session identity·Workspace·logout·pagehide·hash navigation·effect cleanup·unmount는 request/protection epoch를 함께 무효화한다. latest revalidation만 보호막을 reveal하며 이전 finally는 새 hidden/inert/loading/error 상태를 변경하지 않는다.
- GREEN은 A old authenticated, B old unauthenticated, C old rejection, D rich answer+8필드 Citation context, E old Question/Studio interaction·Native command0, F 정상 Home→선택→3열을 모두 포함해 `7/7 PASS`다. 단순 Source 문자열만이 아니라 old title·answer·citation ID·hash·textarea·Native command0을 검증했다.
- 관련 actual React/epoch focused `9/9`, Phase E·Native·Adapter·runner `24/24`, Desktop lint/build, boundary36/0이 PASS했다. full Desktop은 subtest 포함 `35/37`이며 기존 Tauri config/PostCSS baseline 2건만 동일하게 분리된다.
- Windows UI/Tauri launch·credential·screenshot·wire는 재시도0이고 actual status는 계속 `BLOCKED`다.
