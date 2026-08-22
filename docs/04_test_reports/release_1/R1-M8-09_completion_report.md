# R1-M8-09 완료보고

## R1-WEB-02 로컬 Gate 종료 판정 — DATA_CONTRACT_BLOCKED

현재 판정은 `BLOCKED / DATA_CONTRACT_BLOCKED`다. 실제 Browser에서 로그인·ready Source 선택과 오류 시 Source 보존은 확인했으나, live OLLAMA 질문은 공유 환경의 90초 성능 상한 또는 grounding validator를 통과하지 못했다. 별도 same-origin HTTP lifecycle을 위해 production Question Run을 생성하려 했으나, 외부 호출 전에 EgressDecision을 평가할 authoritative Workspace/Organization Egress policy Version/Binding이 schema·migration·production setting에 존재하지 않아 실제 PostgreSQL에서 `QUESTION_POLICY_UNAVAILABLE`로 fail-close했다.

- 유지한 발견 수정: Source GET BFF, 질문 실패 시 Sources/locks/outputs 보존, actual OLLAMA server-only adapter, Question route 계층별 bounded timeout, non-JSON upstream error 안전 투영.
- 실제 증거: HTTP local-test same-origin 로그인 성공, ready PDF Source 활성, Browser Source 선택, 질문 오류 뒤 Source 보존, 격리 API/Web/PostgreSQL/Object health, actual PostgreSQL Egress 평가 실패 시 신규 Run·Result·Citation·EgressDecision mutation 0.
- 제거한 초안: WorkspacePolicy에 존재하지 않는 `egress_policy`를 가정한 코드·테스트와 authoritative source 없이 EgressDecision을 저장하려던 Run completion 변경. 신규 Migration은 만들지 않았다.
- correction proposal: `docs/02_work_orders/release_1/R1-M8-09-EGRESS-POLICY-C01_proposal.md`. 권장안은 immutable Workspace/Organization Egress policy Version/Binding을 RunSnapshot/FrozenContext에 고정하고 평가 결과만 EgressDecision으로 append하는 구조다.
- 로컬 Gate 자원은 containers 5·volumes 3·network 1·images 4·tmp 10·compose 파일·root curl 부산물 2개를 exact 제거했다. 마지막 `daon-r1web02-api-red:local` image와 이를 참조한 것으로 보고된 created container `49183fe598f6`, 공용 서비스 running 상태는 WSL Docker가 반복 `Wsl/Service/0x8007274c`로 응답하지 않아 최종 존재 0/무영향 확인이 남았다. WSL 재시도와 공용 서비스 조작은 중단했다.
- 실제 Browser 전체 Studio lifecycle, live answer+Citation, Office Open은 완료되지 않았다. 이전 완료 또는 “남은 것은 Browser/Office뿐”이라는 표현은 현재 유효하지 않다.

결과 계약: `BLOCKED / DATA_CONTRACT_BLOCKED | R1-M8-09-I001 | R1-WEB-02 실제 Browser·PG Gate 수행 중 Egress 정책 정본 부재 확인, speculative 초안 제거 및 correction proposal 작성 | valid UI/Ollama/timeout/BFF 수정 보존, Progress·Completion·proposal 갱신, local Gate exact cleanup | focused/full/build/diff-check 결과는 종료 검증 절에 기록 | authoritative Egress policy 데이터 계약 미승인, Browser live answer·Studio lifecycle·Office 미검증 | 신산님이 correction proposal의 권장 대안 A·Migration/API/backfill/deny precedence를 승인할지 판단`

## Important A-D 추가 재작업 결과 — INCOMPLETE / JOURNEY_UNVERIFIED

최종 독립 재리뷰의 신규 Critical은 0건이었으나 Important A-D를 동일 `R1-M8-09-I001`에서 다시 열어 TDD로 보강했다. 이 섹션이 현재 판정이며 아래 과거 판정은 이력으로만 보존한다.

- A: AI 재생성은 새 GenerationRequest와 full GenerationSettingsSnapshot을 만들고 실제 `build_structured_output` 결과와 새 EvidenceReference를 불변 OutputVersion에 기록한다. 설정 변경은 8개 full settings를 받아 source/workspace/run lineage, Citation 전체 coverage, 서버 6종 정책을 다시 검증한다.
- B: 새 Version 응답에 content를 포함하고 UI merge는 이전 `review_request_id`, `approval_request_id`, `approval_id`, delivery·registration link를 제거한다.
- C: generation/version/action mutation이 실제 Version 또는 action record 기반 ETag를 발급한다. OpenAPI request/response schema와 Runtime `{data,meta}`·ETag conformance를 동기화했다.
- D: WorkspacePolicy, RuleSetBinding, WeightProfile, KnowledgeScope, EgressDecision에서 투영하는 6개 lock을 각 row의 존재·active/current·workspace·version과 Egress run 결속 및 여섯 필수값까지 fail-close 검증하고 authoritative full values를 Snapshot에 저장한다.

검증 결과는 focused Python 11 PASS·1 SKIP, Web/OpenAPI 52 PASS, API 전체 327 PASS·26 SKIP·134 subtests, OpenAPI 18 PASS 및 verifier 71/90/112(SHA `D628E1AE87A45254950883C2A86FA10F642C5871298429A8B0D16CDC84C33418`), Next Production Build·TypeScript, Product Boundary web 259/all 271 files, `git diff --check` PASS다. 전용 disposable PostgreSQL `daon_studio_r1m809_ad_20260813_01`에서 migration 0001→0011과 실제 RLS/FK/rollback/Canon/KnowledgeRegistration test를 포함한 test file 9 PASS 후 해당 DB만 exact 삭제해 `remaining=0`을 확인했다.

현재는 A-D의 구현·자동·actual PostgreSQL 증거를 제출한 상태다. 실제 Browser 전체 lifecycle 및 same-origin Network, 실제 Office Open, 배포는 수행하지 않았고 어울1의 독립 수락도 아직이므로 `CONTRACT_COMPLETE` 또는 `COMPLETED`로 판정하지 않는다.

결과 계약: `INCOMPLETE / JOURNEY_UNVERIFIED | R1-M8-09-I001 | 독립 재리뷰 Important A-D를 실제 generation/lineage·lifecycle reset·ETag conformance·6종 policy fail-close로 RED→GREEN 보강 | Studio PostgreSQL/Runtime, Product Studio model/pane/API, OpenAPI/verifier summary, 테스트·Progress·Completion 갱신; disposable DB exact 삭제 | focused Python 11 PASS·1 SKIP, Web/OpenAPI 52 PASS, API 327 PASS·26 SKIP·134 subtests, actual PG test file 9 PASS, OpenAPI 18 PASS, Build/Boundary/diff-check PASS, staged0 | 실제 Browser 전체 lifecycle/Network·Office Open·배포 및 어울1 독립 수락 미실행 | 어울1이 A-D 근거를 독립 검토하고 후속 Gate 진입 여부 판단`

> 2026-08-13 독립 Review 재작업: 아래 `CONTRACT_COMPLETE / JOURNEY_UNVERIFIED` 판정은 취소되었다. 저장 산출물 재진입 호환, 유형별 실제 구조, 전체 Step-up/Delivery/재제출/등록 수명주기, 서버 정책 Projection, SQL coverage, BFF 다운로드 헤더, Runtime/OpenAPI exact 계약 및 실제 PostgreSQL·파일 Open 검증이 부족하므로 동일 `R1-M8-09-I001`에서 재작업한다. 과거 판정과 당시 증거는 이력 보존을 위해 삭제하지 않는다.

## 독립 재리뷰 재작업 결과 — INCOMPLETE / JOURNEY_UNVERIFIED

이전 `코드 계약 닫힘` 주장도 취소한다. 코드 결함과 실제 PostgreSQL 계약은 보강했지만 실제 Browser 전체 lifecycle click과 Office Open을 실행하지 않았으므로 `CONTRACT_COMPLETE` 또는 `COMPLETED`로 재승격하지 않는다.

- 구조화 content 선택 React crash, KnowledgeRegistration SourceVersion 전 FK INSERT, access-token-only Step-up을 각각 재현하고 수정했다. Step-up은 현재 local password hash 재검증 후에만 발급하며 OIDC 등 verifier 없는 session은 fail-close한다.
- AI 재생성·설정 변경은 새 GenerationSettingsSnapshot·GenerationRequest·불변 OutputVersion과 반려 재제출 관계를 남긴다. 서버 RuleSet·Review·Authority·Weight·Data Area·Egress 6종 Projection은 UI lock과 Snapshot에 저장된다.
- SVG 800×600과 PNG 640×480은 실제 Node/Edge를 생성한다. OpenAPI는 Runtime exact `{data,meta}`, 200 replay/201, Step-up writeOnly password, 7 media type을 기술하며 mismatch verifier가 이를 검사한다.
- 실제 PostgreSQL exact disposable DB에서 migration 0011, forced RLS, FK 위반, rollback, Canon transition, KnowledgeRegistration/searchable을 통과했다. `...0300`과 `...0415`를 exact 삭제하고 remaining 0을 확인했다.

검증은 focused Web/OpenAPI 51 PASS, focused API 24 PASS·1 SKIP, API 전체 326 PASS·26 SKIP·134 subtests, OpenAPI 18 PASS 및 verifier 71/90/112(SHA `70DF7D0A4A13808DB283F51C28C6D31037D3C34749A9B46234BB0123201F5C9C`), Next Build·TypeScript·Boundary 271 files·diff-check PASS다. 실제 React event dispatch는 구조 산출물 선택·검토·승인 요청까지 통과했으나 controlled input을 포함한 승인·전달·등록 전체 click은 최소 DOM runner 한계로 미검증이다.

Git은 공식 root, 승인 Branch 예외, HEAD=`origin/master`=`1b652ec0858021bb2c78e408cc50c32150a88450`, staged 0을 유지했다. 보호 dirty를 보존했고 Commit·Push·PR·Deploy는 수행하지 않았다.

남은 Gate는 인증된 실제 Browser에서 생성→편집/AI 재생성/설정 변경→검토→반려→재제출→재승인→내보내기→전달→등록 전체 click과 same-origin Network를 확인하는 것, 그리고 이번 지시로 금지된 실제 Word·Excel·PDF·SVG·PNG Open이다.

결과 계약: `INCOMPLETE / JOURNEY_UNVERIFIED | R1-M8-09-I001 | 재리뷰 Critical/Important 코드·자동 계약과 실제 PostgreSQL RLS/FK/transaction을 RED→GREEN 보강 | Product Studio/Identity/Runtime/PostgreSQL/Export/OpenAPI/테스트/진행·완료보고 갱신, disposable DB exact 정리 | Web/OpenAPI 51 PASS, API 326 PASS·26 SKIP·134 subtests, actual PG 1 PASS, OpenAPI 18 PASS, Build/Boundary/diff-check PASS, staged0 | 실제 Browser 전체 lifecycle click·Office Open 미실행 | 어울1이 Browser R1-WEB-02와 Office Gate를 수행·판정`

## 독립 Review 재작업 결과 — FAILURE_REPORT

**현재 판정은 `FAILURE_REPORT`다. 이전 완료 판정은 계속 취소 상태이며 모든 Critical/Important를 실제 증거로 닫기 전 재승격하지 않는다.**

- 닫은 코드·자동 계약: 저장 공통 DTO 재진입 crash, 유형별 승인 Domain 구조, 실제 다중 행/Cell·Node/Edge·Template/Section·보고서 Export, Step-up 발급·소비 연결, 편집/AI 재생성/설정 변경·검토/승인/반려/전달/등록 UI 경로, 서버 정책 Projection, SourceVersion/Citation 전체 coverage, ApprovalRequest/ReviewRequest 결속, BFF `nosniff`, format allowlist·한국어·부록·bounded bytes·Object checksum 저장, Runtime exact BFF method, OpenAPI exact request/7 media type mismatch verifier.
- 자동 증거: 집중 Node/OpenAPI 48 PASS, Studio Python 14 PASS, 전체 Web/OpenAPI 65 PASS, 전체 API 320 PASS·25 SKIP·134 subtests, Web Production Build·TypeScript·Product Boundary·`git diff --check` PASS, staged 0.
- 미종결 Critical/Important: 실제 client React click/reentry 전체 흐름, 실제 PostgreSQL의 RLS/FK/Canon transition/transaction 통합, 실제 Word·Excel·PDF·SVG/PNG Open.
- 확인된 원인: 로컬 DB DSN 4종이 모두 없고 Windows Docker가 없으며 WSL은 `E_ACCESSDENIED`; 추가 승인 요청은 도구 사용량 한도로 거절됐다. 새 API/DB가 기동된 인증 Browser 환경과 Office Open 환경도 없다.
- 현재 변경은 허용 파일에만 있으며 보호 dirty를 복원·삭제·Stage하지 않았다. Commit·Push·Deploy·Credential 입력은 0건이다.
- 필요한 판단: 어울1이 실제 전용 PostgreSQL test DSN/격리 실행환경, 인증 Browser R1-WEB-02 환경, Office Open 검증 환경을 제공 또는 별도 Gate로 승인한 후 동일 issue를 재개해야 한다.

## 판정

**CONTRACT_COMPLETE / JOURNEY_UNVERIFIED — NotebookLM 참고 3면 Workspace의 오른쪽 업무 Studio를 다섯 실제 산출물 공통 Web/API/DB/파일 수명주기 계약으로 연결했다. 실제 Browser·Office 파일 Open·ysna-server 배포는 실행하지 않았으므로 `COMPLETED` 또는 TP-4 PASS로 승격하지 않는다.**

## 판단 이유

- Product DOM은 다섯 Tile, 생성 전 설정 확인, 서버 잠금, 저장 목록·상세, 새 불변 Version과 검토·승인·내보내기·생산 지식 등록 Control을 제공한다.
- Browser Adapter는 `/bff/api/...` same-origin 경로만 사용하고 BFF는 승인된 Studio route·method·query만 전달한다.
- PostgreSQL Repository는 기존 Canon table·FK·RLS transaction 경계에서 Snapshot, GenerationRequest, StudioOutput, OutputVersion, EvidenceReference, AuditEvent, Idempotency를 결속한다.
- 승인·전달·생산 지식 등록은 현재 권한과 exact Step-up을 재검증하며, 승인되지 않은 Version의 Export·Delivery는 거부한다.
- DOCX·PDF·XLSX·CSV·JSON·SVG·PNG는 메타 이름이 아니라 signature·구조·Version·생성시각·지식범위·근거 부록을 가진 bounded bytes로 생성한다.
- 새 dependency와 migration은 추가하지 않았다.

## 주요 변경

- Product UI: `product-studio-model.js`, `product-studio-pane.jsx`, Product Workspace Shell·CSS·public export.
- Web: 실제 Workspace Adapter, 공통 Studio generation/version/action/export client, same-origin BFF allowlist.
- API: Studio DTO·Runtime route, Domain service, PostgreSQL repository, Canon 상태 전이, 실제 파일 exporter.
- 계약: OpenAPI Export path·binary response와 결정론적 OpenAPI evidence.
- 테스트: Product React/Adapter/BFF, Domain/PostgreSQL/Runtime HTTP, 7형식 Export 테스트.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| TDD RED | 신규 Product/API 모듈 부재로 예상 실패 확인 |
| Studio focused Node | 26 PASS |
| Studio focused Python | 11 PASS |
| 지정 Web 4-suite | 46 PASS |
| API 전체 | 317 PASS · 25 SKIP · 134 subtests PASS |
| OpenAPI | 17 PASS · verifier 71 Path/97 Operation/104 Schema · SHA-256 `C9FAC070C0FA885C6865E3307624E8437D8926DD1886461668BBEABAB41AECD4` |
| Web Production Build | PASS · TypeScript 및 `/workspaces/[workspace_id]` 포함 |
| Product UI Boundary | 271 files · 위반 0 |
| Git 안전 | `git diff --check` PASS · staged 0 · HEAD exact `origin/master` |
| Web workspace lint | NOT RUN · baseline `@daon-user/web`에 `lint` script 없음 |

## 보호 경계

- 공식 root `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, Branch 예외 `codex/user-auth-screen-split`, HEAD/origin master `1b652ec0858021bb2c78e408cc50c32150a88450`를 유지했다.
- local `master` ref 불일치와 보호 dirty 때문에 Checkout·ref 이동·Stash를 하지 않았다.
- `auth-pane.jsx`, `desktop-tauri-shell.test.mjs`, Cargo, 모바일 삭제, Native Evidence 및 타 작업 미추적 문서를 수정·복원·삭제·Stage하지 않았다.
- Commit·Push·PR·Deploy·실제 Credential 입력은 수행하지 않았다.

## 남은 검증

- Production Chrome R1-WEB-02에서 실제 로그인→ready Source→질문/Citation→다섯 산출물 생성→Version→검토/승인→Export/Delivery/등록 Network와 화면을 확인해야 한다.
- 실제 Word·Excel·PDF·SVG/PNG Open 및 ysna-server 격리 배포·전용 DB 통합 검증이 남아 있다.
- 작업계획의 Web lint 명령은 `apps/web/package.json`에 script가 없는 baseline 계약 불일치다. 허용 범위 밖 package 변경 없이 어울1이 후속 Gate에서 판단해야 한다.

## 결과 계약

`CONTRACT_COMPLETE / JOURNEY_UNVERIFIED | R1-M8-09-I001 | 다섯 실제 산출물의 Product UI·same-origin Web/BFF·API/Canon/Step-up·불변 Version·7형식 파일 수명주기 구현 | 허용 파일 23개 변경·생성 및 OpenAPI 결정론적 증거·Progress·Completion 생성 | 집중 Node 26 PASS, 집중 Python 11 PASS, Web 46 PASS, API 317 PASS·25 SKIP·134 subtests, OpenAPI 17 PASS, Build/Boundary/diff-check PASS, staged0 | Browser·Office Open·ysna 배포 미실행, Web lint script baseline 부재 | 어울1의 코드 검토 후 R1-WEB-02·Office·ysna Gate 진행 판단`
