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
