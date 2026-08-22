# R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001 Progress

## 2026-08-20 어울1 착수

- 정본 Root: `C:\Users\cyhuh\Desktop\D Driver\Project\Daon_User`
- Branch: `codex/user-auth-screen-split`
- HEAD: `2d4c59e1c761ec12848dcfac8c2f04078dcbb47b`
- staged: 0
- 보호 dirty/untracked: 기존 Phase C/D/E·Windows recovery·Mobile 삭제 등 전체 미접촉
- WSL `daon_user-api-1` Cloud DB: `local-postgres/postgres`
- read-only inventory: `sources=0`, `fixture` Source=0
- 판단: 화면의 `daon-cp3-e2e-fixture.pdf` 5건은 종료된 E2E test runtime fixture이며 제품 DB 잔류가 아니다. 삭제할 제품 행이 없어 destructive DB 작업은 수행하지 않았다.
- 다음: 어울2 단일 Writer TDD 전달.

## 2026-08-20 어울2 정본·보호 경계

- Git root: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`
- Branch: `codex/user-auth-screen-split`
- origin: `git@github-cyhuh7950:cyhuh7950/Daon_User.git`
- HEAD: `2d4c59e1c761ec12848dcfac8c2f04078dcbb47b`
- staged: 0
- SHA-256: AGENTS `aabb11177ea7541b62c0ad6e6ab2fd745fcd4aded72a25df98522fc8e41b47ea`; 설계 `28b8694bd4cd88b62a0c157e83c5b676909c0d07c12a05772a143b8c432b12c8`; 계획 `0e25248b094c603e3142a4640dfdeeaaba1841fc8a4b057126bbe1a04e35bac7`; Work Order `432610b5f1fc209b8524209475788664bc9f02f12cc95920a94665a3d374aae4`; Prompt `18dea75e88aa9d4d84e9e69d71c893b90bb2964318e44c0afdbfe4be730dfbc3`.
- 기존 Phase C/D/E·Windows recovery 제품/테스트/Evidence, Mobile/Web 삭제와 전체 dirty/untracked는 보호 자산이다. restore/delete/stage/commit/push/deploy0을 유지한다.
- 어울1 read-only inventory `sources=0`, fixture Source=0을 정본으로 인수했다. 삭제 대상이 already absent이므로 제품 DB/Object Storage 삭제0, 테스트·Harness·Evidence 삭제0이다.
- 정본 계획은 좁은 일반 대화 응답에 `근거 미사용` 상태가 필요하나 현재 Question DTO는 grounded citation 전용으로 보인다. 공개 DTO 변경 여부는 기존 계약을 먼저 대조한 뒤 쓰기 전에 판단 경계를 확인한다.
## 2026-08-20 어울1 기술 판단 — 기존 Question DTO 유지

- 새 request field/route/response field를 추가하지 않는다.
- 서버는 기존 `question` text를 좁은 일반대화 allowlist(인사·감사·제품 도움말)로만 분류하며, 이 경우에만 Source/Knowledge Context 부재를 허용하고 선택 Provider를 호출한다.
- Web/Native/UI도 동일 allowlist일 때만 context 없는 submit을 허용한다. 그 밖의 질의는 기존 grounded context/evidence 요구를 fail-close로 유지한다.
- 화면의 `일반 대화 · 근거 미사용`은 기존 response shape에서 `insufficient=false` 및 `citations=[]`인 승인된 일반대화 요청에만 UI가 요청 분류와 결합해 표시한다. 응답 DTO 변경은 0이다.
- Provider selection, egress policy/Step-up, tenant/workspace/notebook scope는 변경하지 않는다.

## 2026-08-20 어울2 TDD · 실제 경계 진행

- 좁은 일반대화 Domain/Runtime/PostgreSQL/Web/Native/UI를 기존 DTO 안에서 구현했다. 자동 fallback·새 route·새 field는 0이며, 일반대화 외 no-context 질의는 계속 fail-close한다.
- RED→GREEN: 일반대화 분류·선택 Provider 호출 `7/7`, Runtime no-context 분기 포함 `14/14`, Web/Native exact body `18/18`, Rust wire `3/3`, 독립 Source/Conversation/Studio 오류 UI actual React `2/2`, PostgreSQL repository fake `1/1`, OpenAPI exact verifier PASS, production fixture import graph `26 modules / violations0`.
- actual PostgreSQL: WSL `local-postgres`의 고유 disposable DB/role에서 fresh `0001→0020`, 일반대화 Source read0/Citation0/Provider attempt1/Run·Conversation·Notebook binding을 검증했다. exact test `1/1 PASS`, 종료 후 DB0/role0이며 공용 제품 DB와 fixture는 변경0이다.
- 대표 Provider: ysna-server 기존 API 설정은 값 미노출 boolean 기준 `UPSTAGE,GROQ,MISTRAL` configured였다. UPSTAGE를 서버 내부에서 bounded 1회 호출해 HTTP 200/schema valid/citations0/secret_echo0을 확인했다. Key·응답 원문은 출력·복사하지 않았고 운영 DB·배포 root 변경0, 고유 `/tmp/daon-real-conversation-*` cleanup remaining0이다. 이는 transport compatibility PASS이며 새 로컬 제품 flow 전체 actual은 credential 미반입·코드 미배포로 NOT_RUN이다.
- 추가 RED: 일반대화 `ask()`가 authorization 준비와 달리 egress payload 변환 전 bytes를 승인·전송해 마스킹 정책과 불일치했다. 실제 test에서 transport payload 원문이 `[MASKED]` 기대와 달라 실패함을 고정했다.
- 최소 GREEN: grounded 경계와 동일하게 `prepare_payload` 결과를 authorization과 실제 Provider transport 모두에 exact 재사용한다. general focused `3/3 PASS`; 승인 bytes와 transport canonical bytes 동일을 검증했다.
- 다음: same-origin BFF exact body, grounded Source→Question→Citation→Studio actual PG, 전체 회귀·Evidence 마감.

## 2026-08-20 어울2 actual grounded·회귀·종료 전 판정

- same-origin BFF는 일반대화 요청을 exact `{notebook_id, question}` body로 내부 API에 전달하고 Browser 절대주소를 생성하지 않음을 focused `1/1 PASS`로 확인했다.
- actual PostgreSQL Gate를 새 suffix로 다시 실행했다. fresh `0001→0020`, 일반대화 lineage와 grounded selected Notebook Source→Citation→Studio 원자 저장·동일 key replay·cross-notebook write0가 `2/2 PASS (6.55s)`, skipped0이었다. trap 종료 후 disposable DB0/role0이다.
- API 영향 범위: `30 passed, 7 skipped`; 7 skip은 DSN 없는 로컬 실행의 actual PG 항목이며 위 고유 DB Gate에서 요구 2건을 별도 non-skip 실행했다.
- Node 영향 범위: API/BFF/OpenAPI/Product Workspace/Web/Native/fixture boundary `87/87 PASS`.
- Rust direct 실행은 Tauri sidecar 기본 설정 때문에 build-script가 `resource path ... sidecar ... doesn't exist`로 실패했다. 제품 결함이 아니라 검증 구성 차이로 분리했고, `TAURI_CONFIG bundle.externalBin=[]` 정본 contract 설정으로 Native wire test를 재실행해 `3/3 PASS (34.36s)`했다. pre-existing `src-tauri/gen`은 보호하여 isolated wrapper cleanup을 실행하지 않았다.
- Web production build PASS, scanned `349 files / violations0`; Desktop Vite build PASS; workspace lint 5 files PASS; OpenAPI verifier PASS (`75 paths / 94 operations / 120 schemas / 31 errors`).
- 실제 Provider는 Upstage transport compatibility만 PASS다. 새 제품 코드를 외부 배포하지 않고 Credential을 로컬로 반입하지 않았으므로 실제 제품 Source→Provider→Citation→Studio Browser/Windows Gate는 NOT_RUN이다.
- 종료 판정은 `PARTIAL / CODE_VERIFIED / ACTUAL_PROVIDER_TRANSPORT_PASS / PRODUCT_E2E_NOT_RUN`. 미실행 Gate를 PASS로 주장하지 않는다.

## 2026-08-20 독립검토 재작업 · authoritative replay·provenance·Unicode

- I1 RED: 동일 `/questions` 재요청이 저장 결과 확인 전에 `_question_inputs`와 selected Notebook binding을 다시 호출했다. Runtime actual test에서 expected binding call0/actual1로 재현했다.
- I1 GREEN: tenant/workspace/notebook/actor/run/idempotency와 question+context selection만 canonical 결속한 `sha256` digest를 run canonical record에 저장했다. Step-up ID·Provider payload·Credential은 digest 대상0이다. PostgreSQL authoritative helper는 current Notebook의 conversation binding과 completed run을 함께 확인하며 exact replay만 반환한다. mismatch는 `IDEMPOTENCY_KEY_REUSED`, digest 없는 legacy row는 `QUESTION_REPLAY_UNAVAILABLE`로 fail-close한다.
- Runtime은 `_question_inputs`·binding·Provider·egress·Step-up 전에 authoritative replay를 1회 호출한다. miss 이후 Service에는 `replay_checked=True`와 같은 digest를 전달해 이중 조회0, persist도 동일 digest를 사용한다.
- actual PG HTTP 첫 시도: 동일 replay/current side effect0는 통과했으나 mismatch 409가 safe allowlist 누락으로 `QUESTION_FAILED`로 축약되어 exact code RED였다. DB/role cleanup0 후 QuestionRepository safe allowlist에 기존 공통 `IDEMPOTENCY_KEY_REUSED` 1항목만 추가했다.
- actual PG HTTP 최종: 새 disposable DB/role fresh `0001→0020`, general lineage·grounded Citation/Studio·실제 FastAPI replay `3/3 PASS (8.82s)`. 동일 replay는 binding/provider/policy/ask side effect0, mismatch write0, cross-notebook replay0. cleanup DB0/role0.

### 2026-08-21 I004 REWORK1 current replay 보안 계약으로 교정

- 위 2026-08-20 `binding/provider/policy 이전 replay`와 `current binding side effect0` 문구는 당시 승인 계약의 역사 기록이며 현재 판정으로 사용하지 않는다. 독립 검토에서 권한·Policy 철회 후 저장 결과가 노출될 수 있음을 확인해 current 계약을 교정했다.
- current Runtime은 replay 전에 요청 Source/Knowledge Binding을, repository는 conversation Binding을 재검증한다. 저장 Run의 canonical `provider_kind`/egress scope가 external이면 current `EXTERNAL_LLM` 및 Provider·목적지·payload bytes·`internal` 분류·masking·redaction exact effective Policy를 모두 재검증한다. local/server는 VIEW+Binding만 확인한다.
- external 신규 요청도 동일 exact 정책 검증을 PostgreSQL authorizer transaction 전에 수행한다. actual PostgreSQL fresh `0001→0020`, `21/21 PASS`, external mismatch domain 9-table write0, external replay metadata와 local HTTP replay Binding 재검증을 확인했고 DB0/role0으로 정리했다.
- UI RED: grounded 요청의 `{insufficient:false,citations:[]}` 응답도 answer 모양만으로 `일반 대화 · 근거 미사용`을 표시했다.
- UI GREEN: 요청 시점의 local `answerIntent`와 monotonic question epoch를 결속했다. 최신 general 요청만 label을 표시하며 grounded no-citation label0, workspace/source/knowledge/unmount 후 stale response의 answer/label0이다. reload된 서버 answer는 공개 provenance가 없으므로 shape로 label을 추론하지 않는다. 공개 DTO 변경0.
- Unicode RED→GREEN: Python/JS NFKC가 fullwidth `Ｄａｏｎ`을 일반대화로 승인했지만 dependency 없는 Rust는 거부했다. 승인안대로 NFKC 변환이 원문과 다른 입력을 Python/JS도 exact fail-close하여 3언어 동일 negative로 고정했다.
- fresh 회귀: API `36 passed`(DSN 없는 local actual 8 skip; 별도 actual 3 non-skip PASS), Node `87/87`, Rust `3/3`, Web/desktop build, lint, OpenAPI, fixture/product boundary PASS.
- 대표 Provider actual 재호출0, 운영/Windows 접근0, commit/push/deploy0.

## 2026-08-20 최종 Minor cleanup · Unicode 3언어 exact 정합

- RED: Rust classifier가 fullwidth `！`·`？`를 suffix로 trim하고 U+3000 space도 `trim()`으로 제거하여 일반대화로 승인했다. focused Rust contract는 negative vector에서 `1 failed`로 재현됐다.
- GREEN: Rust는 trim 전에 U+3000과 U+FF01..U+FF5E를 fail-close한다. fullwidth letter·punctuation·space는 Python/JS와 동일하게 거부하고 정상 ASCII `안녕하세요!`·`안녕하세요?`만 허용한다.
- 공통 벡터 focused 결과: Python `1/1 PASS`, Node `1/1 PASS`, Rust `1/1 PASS`.
- 이 Minor에서는 actual Provider/PostgreSQL/Windows를 재실행하지 않았으며 제품 actual 판정은 `PARTIAL / PRODUCT_E2E_NOT_RUN`으로 유지한다.

## 2026-08-20 23:42 어울1 직접 결과관리 · 설정 연결과 사용자 설명서 배포

- Subagent 자동 테스트 결과를 최종 제품 완료로 대신하지 않고 어울1이 실제 서버 화면까지 직접 대조했다.
- 대화 실패가 Source 전체 오류로 투영되던 상태를 분리하고, Notebook 홈의 설정 메뉴를 화면 설정·라이선스·사용자 설명서 실제 Route에 연결했다. 관련 커밋은 `c036b46`이다.
- Web runtime image에 `public` 자산이 빠져 `/manual/manifest.json`이 404였던 실제 배포 결함을 Dockerfile RED→GREEN으로 교정했다(`615dad0`). 실제 Reverse Proxy가 JSON에 `charset=UTF-8`을 붙이는 경계도 exact allowlist로 교정했다(`27ff06d`).
- 사용자 설명서 3종에서 Phase D/E 준비 중 문구와 종료된 fixture/오류 화면 6개를 제거하고, 현재 로그인→Notebook 홈→명시 선택→3열 작업, 좁은 일반대화, Provider 연결 시험과 실제 생성의 차이, Egress 정책을 반영했다.
- 최종 매뉴얼은 Markdown/DOCX/PDF 3종×3형식으로 재생성했다. DOCX/PDF 렌더 17페이지를 어울1이 전수 시각 검수했고 겹침·잘림·깨진 글자0, DOCX 접근성 high/medium/low 모두0, PDF page count `5/6/6`이다.
- fresh Gate: Manual `7/7 PASS`, Web production build·TypeScript·12 route PASS, product boundary `391 files / violations0`, manifest bytes/SHA exact PASS.
- 커밋 `689be84aeeda9655968badecc1ff2dd48ea50a95`를 원격 브랜치에 Push하고 ysna-server Daon 전용 Web만 재빌드·재기동했다. 서버 HEAD=동일 SHA, Web health=`healthy`이다.
- 실제 `https://daon-user.sinsan.kr/settings/manual`에서 Release `1.0.0`, 업데이트 `2026-08-20`, 세 문서 목록, 최신 사용자 설명서 본문과 console warn/error0을 확인했다. PDF는 HTTP 200, `application/pdf`, `177756 bytes`였다.
- 보호 dirty/untracked는 stage/restore/delete0, API·DB·공용 서비스 변경0이다. 제품 전체 Source→외부 Provider→Citation→Studio E2E는 외부전송 정책의 명시 승인 전까지 여전히 NOT_RUN이며 완료로 주장하지 않는다.

## 2026-08-21 어울2 실제 제품 E2E 재개 · 착수/preflight

- 신산님이 고지된 제한적 외부전송 범위의 실제 제품 Gate 진행을 승인했다. 대표 Provider는 `UPSTAGE` 하나로 고정하고 자동 fallback은 0으로 유지한다.
- 정본 Root=`C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, Branch=`codex/user-auth-screen-split`, origin=`git@github-cyhuh7950:cyhuh7950/Daon_User.git`, HEAD=`7ed4132522277e5332a01eb95ee375bb34f1c1eb`, staged=0이다.
- 정본 SHA-256: AGENTS `aabb11177ea7541b62c0ad6e6ab2fd745fcd4aded72a25df98522fc8e41b47ea`; 설계 `28b8694bd4cd88b62a0c157e83c5b676909c0d07c12a05772a143b8c432b12c8`; 계획 `0e25248b094c603e3142a4640dfdeeaaba1841fc8a4b057126bbe1a04e35bac7`; Work Order `432610b5f1fc209b8524209475788664bc9f02f12cc95920a94665a3d374aae4`; Prompt `18dea75e88aa9d4d84e9e69d71c893b90bb2964318e44c0afdbfe4be730dfbc3`.
- 로컬 dirty는 승인된 제품 commit 밖의 Mobile/model-connections 삭제, R1-M5·Windows recovery·기타 untracked 보호 자산뿐이다. restore/delete/stage/commit/push0을 유지한다.
- ysna-server 배포 Root는 detached HEAD `689be84aeeda9655968badecc1ff2dd48ea50a95`, origin HTTPS, tracked dirty0이고 `backups/`, `secrets/` untracked 보호 자산이 있다. API/Web은 healthy, document-worker running, object-storage healthy다.
- 다음: API image가 제품 commit과 일치하는지, Provider 설정 boolean·effective egress policy·제품 DB 상태를 secret-free read-only로 확인한다. 차이가 있을 때만 Daon 전용 서비스 갱신 또는 공식 policy version 적용을 수행한다.

## 2026-08-21 어울2 실제 제품 E2E · 서버/정책 preflight

- ysna-server API의 `runtime.py`, Question service/repository bytes는 배포 commit `689be84aeeda9655968badecc1ff2dd48ea50a95`와 일치했다. API `/health/live`, `/health/ready`, Web health는 정상이다. 최신 제품 API/Web을 다시 배포할 필요가 없어 배포 변경은 0이다.
- 운영 DB read-only inventory는 대상 Workspace 1, Notebook 1, Source 0이다. 기존 사용자/운영 Source를 읽거나 삭제하지 않았다.
- Provider projection은 `UPSTAGE / external_api / active`, text role=`solar-pro4`이다. GROQ/MISTRAL을 선택하거나 fallback하지 않았고, 이 단계의 Provider 호출은 0이다.
- effective policy read-only 결과는 Workspace version 1 `deny_external / restricted / max_bytes=0 / masking=true / redaction=true / provider kinds=[] / destinations=[]`이다. 이는 이번에 승인된 `allow_approved_external / internal / max_bytes=1048576 / masking+redaction / api.upstage.ai exact` 범위와 다르므로 fail-close 상태가 정상 유지되고 있다.
- 첫 정책 조회는 추정 테이블명 불일치, 두 번째는 PostgreSQL 문자열 결합 연산자 우선순위 때문에 실패했다. migration/repository 정본으로 실제 table/column을 확인한 뒤 JSON 필드를 subquery로 투영하여 read-only 조회를 성공시켰다. 각 실패의 DB write·외부 호출은 0이다.
- 공식 Chrome 인증 세션에서 `/notebooks`와 선택 Notebook 3열을 실제 확인했다. Source 0, Library 0이며 설정의 LLM 화면은 UPSTAGE 활성·credential configured·connection unverified를 표시했다. Browser 요청은 `/bff/api/...` same-origin이었다.
- 정식 `/settings/organization` 화면에는 정책 필드와 현재 비밀번호 Step-up 입력이 있으나 organization version 저장만 지원한다. 정책 합성 정본상 organization 또는 workspace 중 하나라도 deny이면 effective deny이며, 운영 DB의 workspace deny도 별도 활성 상태다. 기존 workspace policy API는 있으나 Web save adapter/UI는 없다.
- 현재 blocker: 승인 policy를 실제 적용하려면 사용자 현재 비밀번호 Step-up과 organization/workspace 두 scope의 정식 저장 경계가 모두 필요하다. Credential을 읽거나 추측하지 않았고 임의 DB update·우회 endpoint·정책 write는 0이다. 기존 API/security 계약을 재사용하는 최소 Workspace policy UI 제공 여부를 어울1 판단 경계로 보고했다.

## 2026-08-21 어울1 기술 판단 · 2단계 Policy UI 승인

- 기존 공개 API·데이터·보안 계약 변경0인 내부 UI 조립으로 진행한다. `/settings/organization`에서 organization과 workspace policy를 별도 단계·별도 명시 저장하며 각 단계는 기존 Step-up을 사용한다.
- 한 화면에 두 비밀번호를 보관하지 않고 활성 단계의 단일 uncontrolled ref만 사용한다. 요청 성공·실패 후 비밀번호와 Step-up authorization은 즉시 비우며, organization 성공을 workspace 성공으로 표시하지 않는다.
- 양쪽 적용 후 effective projection이 승인 범위와 exact 일치하기 전에는 Provider 호출0을 유지한다. Browser는 same-origin BFF만 사용한다.

## 2026-08-21 어울2 TDD · 2단계 Policy UI/Runtime

- RED 1: Web adapter에 `saveWorkspaceEgressPolicy` export가 없어 focused Node가 module import 단계에서 실패했다.
- RED 2: 기존 `POST /api/v1/workspaces/{id}/egress-policy-versions`가 DTO에 존재하지 않는 `body.workspace_id`를 접근해 AttributeError 500을 반환했다. 공개 DTO는 이미 extra-forbid이며 path `id`가 canonical scope이므로, 존재하지 않는 접근만 제거했다.
- GREEN: Web은 기존 Step-up helper를 organization/workspace scope에 공용화하고 각각 `/bff/api/organizations/{id}/egress-policy-versions`, `/bff/api/workspaces/{id}/egress-policy-versions` 상대 경로를 사용한다. 비밀번호·Step-up은 `finally`에서 지운다.
- GREEN: 설정 Pane은 `1. 조직 정책`과 `2. Workspace 정책`을 단일 활성 단계로 제공한다. DOM에는 현재 단계 비밀번호 입력 하나만 존재하고 단계 전환 시 즉시 비운다. 각 단계는 자신의 ETag와 저장 함수를 사용하며 최종 effective 상태를 별도로 표시한다.
- focused 결과: Egress Web/BFF/React `6/6 PASS`, Runtime HTTP `1/1 PASS`. Runtime test는 Workspace endpoint의 201/scope_type과 기존 organization 권한 거부·write count를 함께 검증했다.
- 변경 파일: 승인 설계/계획/Work Order/Progress, `services/api/src/daon_user_api/runtime.py`, Runtime test, `apps/web/lib/egress-policy-api.js`, 설정 page, `packages/ui/src/egress-policy-pane.jsx`, Web tests. 외부 정책 write·Provider 호출은 여전히 0이다.

## 2026-08-21 어울2 배포 전 마감

- fresh Egress 전체 API `10/10 PASS`; Web/BFF/React focused `6/6 PASS`; Web production build·TypeScript·12 routes·product boundary `391 files / violations0`; lint 3 files; OpenAPI `75 paths / 94 operations / 120 schemas / 31 errors` exact PASS다.
- 확대 Node glob의 1 RED는 별도 과거 Issue `R1-M8-09-EGRESS-POLICY-C01` manifest가 현재 HEAD의 `question_egress.py` hash를 반영하지 못한 pre-existing Evidence 정합성이다. 현재 정책 UI/Runtime 기능 테스트 6건은 모두 GREEN이며, 과거 Evidence와 현재 변경 범위 밖 파일을 수정하지 않았다.
- 보안 scan에서 Browser 절대주소·localhost0을 확인했다. `password`와 Step-up 표기는 정식 same-origin 요청 body 생성, 단일 password ref 및 `finally` clear 위치에만 존재하며 값·Credential·내부 URL 원문은 문서/Evidence/log에 0이다.
- 어울1 지시에 따라 uncommitted source를 서버로 복사하거나 배포하지 않았다. 실제 policy write·Provider call·Source upload는 0이며, 현재 상태는 `PARTIAL / POLICY_UI_CODE_VERIFIED / POLICY_DEPLOY_AND_STEP_UP_PENDING / PRODUCT_E2E_NOT_RUN`이다.
- 다음: 어울1이 diff와 테스트를 검토해 exact stage·commit·push 후 Daon 전용 API/Web만 배포한다. 배포 후에는 정식 UI의 현재 비밀번호 입력 직전 멈춰 사용자 Step-up을 요청하고, 양 scope effective 승인값을 확인한 뒤에만 실제 Provider/Source Gate를 재개한다.

## 2026-08-21 독립 리뷰 재작업 1/2 · async context/Step-up exact

- React RED 1: old Workspace load가 지연된 상태에서 props를 새 Workspace로 바꾸면 두 번째 load도 old Workspace ID를 사용했다. reverse resolve에서 이전 정책이 최신 DOM을 덮을 수 있었다.
- React RED 2: save pending 중 조직·Workspace navigation control이 활성 상태였다. scope·draft·ETag가 분리될 경쟁 조건을 고정했다.
- GREEN: organizationId/workspaceId/activeScope를 monotonic epoch와 exact snapshot에 결속했다. props·scope·unmount 변화는 epoch increment와 AbortSignal로 이전 load/save를 무효화하며, 각 await 뒤 최신 snapshot만 reducer/DOM을 갱신한다.
- GREEN: 저장 중 두 scope navigation은 disabled다. context 변경으로 abort된 이전 save는 test adapter write0, 이전 `finally`는 keyed form의 새 password DOM을 지우지 않으며 stale success/catch/error/status도 최신 scope에 0이다. 조직↔Workspace 양방향 전환을 확인했다.
- Runtime 보안 계약: organization/workspace 각각 기존 action group, exact target_id, operation, idempotency를 검증했다. ACL deny는 consume_step_up 0이며 wrong-target Step-up 실패는 safe `INVALID_REQUEST`, policy write0이다.
- fresh 결과: Egress React/API/BFF `9/9 PASS`, Egress API `10/10 PASS`, lint 3 files, OpenAPI exact PASS, Web production build·TypeScript·12 routes·boundary `391/0` PASS. 외부 write·Provider call·deploy0이다.

## 2026-08-21 독립 리뷰 재작업 2/2 · context safe reset

- RED: context identity가 바뀌어도 reducer가 이전 `effective/draft/canSave`를 유지해 status=`ready`였다. 지연된 새 load 동안 이전 form/nav/password/정책 DOM이 상호작용 가능한 위험을 고정했다.
- GREEN: prop context 변경 즉시 `context_loading`으로 effective/draft/canSave/error를 초기화하고 loading placeholder만 렌더한다. 새 load 성공 전 form/nav/password/submit/old policy text/interaction은 0이다. 같은 context의 scope 전환은 각 organization/workspace policy에서 draft를 새로 투영해 이전 draft를 폐기한다.
- production adapter: Step-up response가 지연된 상태에서 context AbortSignal을 받으면 policy endpoint call0이며 sensitive password/token은 `finally` clear된다. 정책 POST가 이미 서버로 송신된 뒤에는 exact old snapshot write가 완료될 수 있고, client가 보증하는 것은 stale UI projection0이다. 서버의 ACL/ETag/Step-up/idempotency 검증은 그대로 유지한다.
- focused Egress Node는 `11/11 PASS`다. 이전 epoch/Step-up exact·ACL-before-consume·wrong-target write0 테스트를 유지한다.

## 2026-08-21 독립 리뷰 재작업 3 · first-commit identity

- RED: 기존 Pane은 hook을 소유한 stateful component 자체여서 prop organization/workspace identity가 React key와 동기 결속되지 않았다. passive effect 전 첫 commit에서 이전 reducer/form이 재사용될 수 있었다. 별도 RED에서 settings context GET의 AbortSignal도 `undefined`였다.
- GREEN: exported wrapper가 non-empty `[organizationId, workspaceId]`를 JSON tuple로 직렬화한 injective key로 stateful inner를 동기 remount한다. `("a:b", "c")`와 `("a", "b:c")`는 서로 다른 key임을 단위 테스트로 고정했다. prop 변경 render commit 순간 old reducer/password/form/nav/text가 재사용되지 않는다. empty props는 `session-resolved` key로 기존 session context resolution을 유지한다.
- GREEN: `getOrganizationSettingsContext({signal})`은 same-origin `/bff/api/session` GET에 exact AbortSignal을 전달해 projection 차단뿐 아니라 read 자체도 취소한다.
- 기존 monotonic epoch·AbortSignal·context_loading·Step-up exact 테스트는 유지했고 focused Egress Node는 `12/12 PASS`다.
- fresh 종료 Gate: Egress API `10/10 PASS`, lint `4 files`, OpenAPI `75 paths / 94 operations / 120 schemas / 31 errors`, Web production build·TypeScript·12 routes·boundary `391/0` PASS. Evidence manifest `15 artifacts / mismatch0`, secret·internal URL scan0, `git diff --check` PASS, staged0이다.
## 2026-08-21 I004 REWORK2 — external immutable Run 재검증

- actual PostgreSQL RED에서 authorizer의 불완전 Run 선삽입으로 최초 외부 요청 성공 후 동일 HTTP replay가 404임을 확인했다.
- authorizer와 완료 저장을 repository의 단일 canonical Run payload helper에 결속하고, authorizer가 Provider 호출 전에 결정론적 Conversation과 완전한 replay metadata를 최초 생성하도록 교정했다.
- fresh PostgreSQL `22/22`(transport1, replay200/provider0, db0/role0), focused API `52/52`, 전체 API `488 passed/42 skipped/137 subtests`, Node `25/25`, OpenAPI exact, Ruff PASS다. 운영 Provider·정책 write·배포는 0이다.

## 2026-08-21 I004 REWORK3 — concurrent Provider owner

- 동일 idempotency key 동시 최초 요청 RED에서 transport2와 중복 transition 오류를 재현했다. durable egress decision creator 하나만 internal Provider owner가 되고 follower는 bounded completed replay만 수행하도록 교정했다.
- owner 미완료 timeout은 same-key 소유권 탈취 없이 retryable internal 409/provider0이며 새 idempotency key만 새 Run으로 복구한다. 자동 TTL takeover는 구현하지 않았다.
- fresh actual PostgreSQL `4/4`: transport1, Run1, Result1, Egress1, Audit `canon.transition5 + egress1 + answer1`, cleanup db0/role0. 운영 Provider·정책 write·배포0이다.
- fresh 회귀는 focused API `38/38`, 전체 API `489 passed/42 skipped/137 subtests`, Node `27/27`, OpenAPI exact, Ruff PASS다.
- 첫 focused/전체 회귀는 각각 과거 파일 selector와 isolated `cryptography` 누락으로 collection 전에 종료됐고, 현재 파일/dependency를 명시한 fresh 실행으로 복구했다. 제품 write0이다.

## 2026-08-21 I004 REWORK4 — in-flight fingerprint exact

- actual RED에서 same run/wire/frozen + 다른 request fingerprint follower가 owner 결과를 받은 결함을 재현했다.
- stored complete canonical exact 비교와 완료 후 fingerprint-aware replay로 교정했다. mismatch follower는 409, Provider 추가 호출0, 결과0, 추가 write0이다.
- fresh actual PostgreSQL 4/4, focused API39/39, full API490 passed/42 skipped/137 subtests, Node27/27, OpenAPI·Ruff PASS, cleanup db0/role0이다.

## 2026-08-21 09:29 KST — 운영 제품 E2E 읽기 전용 사전점검

- 실제 로그인된 `https://daon-user.sinsan.kr` 제품 화면에서 선택 Notebook `notebook-1c67a1adb2bd6a132f57ca429ceef091`을 읽기 전용으로 확인했다. 현재 Daon 승인 지식 0, Raw Source 0, 저장된 Studio 산출물 0이며 질문 실행은 비활성 상태다.
- 조직 및 Workspace effective 정책은 모두 `승인된 외부 전송 허용`이다. exact 값은 mode `allow_approved_external`, provider kind `external_api`, destinations `api.upstage.ai,api.groq.com,api.mistral.ai`, classification `internal`, max bytes `1048576`, masking/redaction enabled, required approver `organization_admin`이다.
- LLM 설정은 UPSTAGE가 활성·Credential 설정됨·연결 미확인이고, GROQ/MISTRAL을 포함한 나머지는 비활성이다. Endpoint/Credential 원문은 읽거나 기록하지 않았다.
- Source upload, 정책/DB write, 연결 시험, 질문, Studio 생성, 외부 Provider 호출은 모두 0이다. Browser console warn/error도 0이었다.
- 실제 E2E의 다음 경계는 non-sensitive PDF Source 등록이다. 후보는 공개 제품 문서 `docs/manual/dist/daon-knowledge-llm-guide.pdf`(172473 bytes, SHA256 `BC3560C70E225FF7BA1F01AF4DDE72A4975EE1DDE6487605EE1CCCD2D9528259`)다. 업로드 및 UPSTAGE 전송 직전 사용자에게 대상 도메인, 파일, 일반대화/근거질문 문구와 bounded Source excerpt 전송 범위를 명시해 승인을 받아야 한다.

## 2026-08-21 운영 Source 목록 `SOURCE_LIST_INPUT_INVALID` TDD 복구

- 운영상태 Provider/API/Storage/Queue는 모두 정상인 반면, 선택 Notebook의 Source GET만 `SOURCE_LIST_INPUT_INVALID`로 실패했다. 원인은 `ProductWorkspaceShell`이 `adapter.listSources({signal})`을 호출할 때 선택 Notebook ID를 넘기지 않고, Web 기본 Adapter도 Workspace ID만 보유해 `listWorkspaceSources`의 notebook scope 입력 검증에서 차단된 것이다.
- RED: 실제 Web Adapter focused React test에서 선택 Notebook으로 factory를 구성하고 `listSources()`를 호출하면 `SOURCE_LIST_INPUT_INVALID`가 발생했다(`0/1`).
- GREEN: `createWebProductWorkspaceAdapter(workspaceId, notebookId)`가 선택 Notebook ID를 immutable closure로 보유하고 Source list/upload/processing/question/Citation/Studio read-write 옵션에 canonical scope를 결속한다. `NotebookProductWorkspace`는 검증된 Context의 `notebook_id`만 factory와 `ActualWorkspace`에 전달하며, 공개 route/DTO 변경은 0이다.
- 검증: Web Adapter focused React `1/1 PASS`; Source·Context·Question·Studio·BFF 회귀 `27/27 PASS`; 제품 파일 lint `2 files PASS`; Web production build·TypeScript·12 static pages·boundary `391 files / violations0 PASS`다. 전체 test file lint는 과거 contract fixture의 내부 URL 문자열 4건 때문에 실패했으며 이번 변경과 무관해 제품 파일 lint로 분리했다.
- 실제 PDF upload, DB write, Provider call, connection test는 계속 0이다. 배포 전 상태이며 운영 화면은 현재 배포본이므로 수정 적용 전까지 Source GET 실패가 유지된다.

## 2026-08-21 운영 Source 초기 로드 간헐 실패 TDD

- 운영 exact Notebook URL에서 최초 Source load가 오류가 된 뒤 동일 화면의 수동 `다시 시도`는 약 1.2초 내 정상 empty로 회복됐다. Daon Web/API container는 healthy이고 운영 배포 HEAD는 notebook scope fix `287d95536c7286d391f0d44bfb4a6ace7c9ec9c5`와 일치했다. 최근 Web/API container log에는 Source 오류 상세이 남지 않아 최초 upstream safe code 자체는 로그로 재구성하지 못했다.
- 코드 대조에서 Context 준비 전 Shell mount 또는 stale adapter 결과가 최신 상태를 덮는 경로는 확인되지 않았다. `NotebookProductWorkspace`는 session→Notebook→Context 검증 뒤 Shell을 mount하고, Shell effect는 adapter/workspace 변경 시 이전 AbortController를 취소한다. 관련 stale response 회귀도 GREEN이다.
- 실제 제품 결함은 Source API error envelope의 `retryable` 판정을 Web client가 버리고, Shell이 retryable 최초 GET도 즉시 영구 오류 UI로 확정한 것이다. RED는 safe retryable 보존 실패와 최초 retryable 실패 후 호출 수 1을 각각 고정했다(`0/2`).
- GREEN: Source list client는 safe `code`와 boolean `retryable`만 non-enumerable Error metadata로 보존한다. Shell은 `retryable=true`인 Source GET만 250ms 뒤 정확히 1회 자동 재시도한다. non-retryable/입력/권한 오류는 자동 재시도하지 않고 기존 수동 `다시 시도`를 유지하며, AbortSignal이 취소되면 재요청0이다.
- 검증: deferred actual React Adapter가 최초 pending→retryable reject→자동 retry empty로 복구하고 오류 DOM 0임을 확인했다. focused `2/2 PASS`, Source·Context·Question·Studio·BFF React 회귀 `28/28 PASS`, API Source/Studio HTTP `3/3 PASS`; UI 제품 lint `3 files PASS`; Web production build·TypeScript·12 pages·boundary `391/0 PASS`. `product-workspace-api.js` 단독 workspace lint는 이 변경과 무관한 기존 `INTERNAL_VALUE` 차단 정규식의 URL 패턴을 검출해 분리 기록했다.
- 운영 upload/DB write/Provider call은 0이다. 운영 적용 전이므로 actual reload 재검증은 commit/push/Daon Web 배포 뒤 수행해야 한다.
## 2026-08-21 운영 빈 Context의 이전 Source 오류 잔류 TDD 복구

- 실제 운영에서 Notebook Context GET은 `200`, Source binding은 `0`인데 이전 Source 오류 화면이 남고 수동 재시도 뒤 정상 empty로 복구되는 현상을 재현했다. Source HTTP 실패가 아니라 `ProductWorkspaceShell`이 새 empty Context를 확인한 뒤에도 다른 초기 비동기 결과를 기다리는 동안 이전 `error` state를 유지하는 UI 상태 결함으로 분리했다.
- RED: 기존 `SOURCE_LIST_FAILED` state + `notebookContext.sources=[]` + Knowledge/Studio 응답 지연 조건에서 `Source를 불러오지 못했습니다`가 계속 노출됐다(`0/1`).
- GREEN: 로드 effect 시작 시 authoritative Notebook Context의 Source가 empty이면 canonical `empty` state로 즉시 reset한다. API/BFF/데이터 계약과 자동 retry 횟수는 변경하지 않았다.
- fresh 검증: focused React `1/1`, Notebook Context/Product Workspace `11/11`, Web production build·TypeScript·boundary PASS. 별도 Desktop 모놀리스의 기존 보호 기준선 3건과 Web package의 미정의 `lint` script는 본 변경과 분리했다.
- 배포 후 재현에서 empty reset 뒤에도 Shell이 `listSources()`를 1회 호출해 stale reject가 다시 error를 만들 수 있는 경로를 확인했다. 추가 RED는 authoritative `sources=[]`에서 호출 기대0/실제1을 고정했다. GREEN은 empty Context이면 Source 호출 자체를 생략하고 canonical empty를 유지한다. focused `1/1`, 관련 `11/11`, Web build·TypeScript·boundary를 다시 통과했다.

## 2026-08-21 질문 답변 범위·프롬프트 확장 TDD

- 신산님 확정 요구사항을 추가 대조했다. 기존 grounded prompt는 supplied evidence만 답하고 `insufficient=true`를 반환하도록 되어 있었으며, general prompt는 greeting/product-help allowlist만 답하도록 제한되어 있었다. Egress masking도 plain general question을 같은 allowlist로 재검증했다.
- RED: `test_question_answering.py::test_grounded_prompt_explains_source_scope_and_handles_out_of_scope_without_refusal`는 Source scope 설명·범위 밖 불일치 안내·웹 검색 승인 대기 지시가 없어 실패했다. `test_question_egress_transform.py::test_required_masking_accepts_any_general_question_wire`는 사실형 plain 질문을 `EGRESS_TRANSFORMATION_FAILED`로 차단했다. Web/HTTP arbitrary no-context RED는 앞 단계에서 이미 고정했다.
- GREEN: 기존 DTO/route를 유지하면서 Web은 Source 선택과 질문 실행을 분리하고, API `QuestionBody`/input validation은 빈 Context를 allowlist 없이 허용한다. evidence가 없으면 선택 Source 여부와 무관하게 기존 Provider general payload 경로로 답변하며 `insufficient=false,citations=[]`를 저장한다. Egress는 JSON evidence payload와 구분되는 일반 plain 질문을 동일 masking 경계에서 처리한다.
- Prompt GREEN: grounded Upstage/OpenAI-compatible prompt에 Source scope 우선 설명, 범위 내 evidence-only 답변, 범위 밖 불일치 안내(일반 거절문 금지·insufficient=true·Citation 없음), 최신 정보는 웹 검색 제안 후 명시 승인 대기, Studio Source evidence-only 지시를 추가했다. general prompt도 임의 질문 답변과 웹 검색 승인 대기를 명시했다.
- focused GREEN: prompt `1/1`, egress `1/1`, service no-evidence `2/2`, runtime arbitrary/no-context `2/2`, Web arbitrary/Studio gate `2/2`가 통과했다. 다음은 전체 관련 회귀와 TypeScript/build/boundary이며 Provider·운영 DB·실제 Source write는 0이다.
- 최종 관련 회귀: API question/egress `34 passed, 14 warnings, 3 subtests`; Node ProductWorkspace/Notebook/BFF/retention `28/28`; Python `compileall`·Ruff focused PASS; Web Next production build·TypeScript·12 static pages·boundary `392 files / violations0` PASS. 경고는 기존 httpx per-request cookie deprecation뿐이며 기능 실패가 아니다.
- 변경 파일(이번 질문 범위): `packages/ui/src/product-workspace-shell.jsx`, `scripts/tests/product-workspace.test.mjs`, `services/api/src/daon_user_api/runtime.py`, `services/api/src/daon_user_api/question_answering.py`, `services/api/src/daon_user_api/question_answering_service.py`, `services/api/src/daon_user_api/question_egress.py`, 관련 API/Node 테스트 4개와 본 Progress. 기존 보호 dirty/untracked는 수정·stage하지 않았다.
- 외부 Provider 호출, 운영 Source/PDF, DB write, 정책 변경, commit/push/deploy는 0이다. 실제 Provider/Browser E2E는 정책·사용자 데이터 경계상 별도 승인 없이는 실행하지 않았다.

## 2026-08-21 질문 무근거 일반답변 요구사항 TDD RED

- 신산님 확정 요구사항을 반영해 질문 라우팅을 대조했다. 현재 Web `askQuestion` guard와 `buildQuestionKnowledgeContext`는 Source/Knowledge가 없을 때 `isGeneralConversationIntent` allowlist만 허용하고, 서버 `QuestionBody`, `_question_inputs`, `QuestionAnsweringService`도 동일하게 사실형 빈 Context를 `QUESTION_CONTEXT_INVALID`로 차단한다.
- 응답 DTO는 기존 `answer`, `insufficient`, `citations` shape로 일반 답변(`insufficient=false`, `citations=[]`)을 표현할 수 있고, Studio는 별도 `canCreateGroundedReport`의 Citation·insufficient gate를 유지하므로 새 공개 route/field는 필요하지 않다는 영향 판정을 기록했다.
- RED Web: `scripts/tests/product-workspace.test.mjs`에 빈 Notebook 사실형 질문이 Provider adapter를 호출하고 근거 부족 문구를 표시하지 않는 테스트를 추가했다. `node --test ... --test-name-pattern="임의 질문은 일반 답변"` 결과 `15 passed, 1 failed`; 실제 `askQuestion` 호출이 0건으로 실패했다.
- RED HTTP: `services/api/tests/test_question_answering_runtime_http.py`에 동일 계약의 HTTP 테스트를 추가했다. 기본 Python은 `psycopg_pool` 미설치로 collection 단계에서 중단되어 제품 RED까지 도달하지 못했으며, API 테스트용 승인 런타임/의존성 경계를 확인한 후 재실행한다. 제품 코드 변경은 아직 없다.

## 2026-08-21 Task 3 공개 질문 응답 DTO 확장 TDD

- 승인된 범위: 기존 `run_id`, `run_result_id`, `answer`, `insufficient`, `citations`를 유지하면서 `mode`, `grounding`, `source_scope_summary`, `mismatch`, `next_actions`를 추가했다. 허용 mode는 `work_support`, `explicit_source_lookup`, `source_backed_action`, `approved_web_research`로 고정했고, 외부 웹 호출·Provider 자동 fallback·Studio evidence gate는 변경하지 않았다.
- RED: HTTP 질문 테스트에서 enriched metadata가 없어 일반/grounded/mismatch 응답 4건이 `KeyError: mode`로 실패했다. Web projection은 legacy 5-field 응답만 허용하는 상태였다.
- GREEN: `runtime.py`에 질문 intent 기반 safe metadata projection을 추가했다. Source citation이 있으면 `source_backed`, 빈 Context는 `ungrounded`, Source 응답이 insufficient이면 `source_evidence_unavailable`와 `SOURCE_SCOPE_MISMATCH` 및 3개 다음 행동을 반환한다. Web `projectSafeQuestionAnswer`는 legacy 응답 또는 exact enriched DTO만 허용하고 mode/grounding/mismatch/next_actions allowlist를 검증한다.
- 변경 파일: `services/api/src/daon_user_api/runtime.py`, `services/api/tests/test_question_answering_runtime_http.py`, `packages/ui/src/product-workspace-shell.jsx`, `scripts/tests/product-workspace.test.mjs`, 기존 Task 3의 `packages/ui/src/conversation-intent.js`, `services/api/src/daon_user_api/question_answering.py`, `services/api/tests/test_question_answering.py` 및 본 Progress. 보호 dirty/untracked는 수정·stage하지 않았다.
- GREEN 검증: Runtime HTTP `12 passed`(19 warnings: 기존 httpx cookie deprecation), Python question/service `19 passed, 3 subtests`, Node ProductWorkspace `18 passed`. 추가 Web/Notebook/Source focused, production build, TypeScript, boundary, diff-check는 다음 단계다.
- 실제 Provider/browser/운영 DB·Source write/Studio 생성 및 commit/push/deploy는 0이다.

- 최종 관련 검증: Python question/egress/runtime HTTP `38 passed, 19 warnings, 3 subtests`, Python `compileall` PASS; Node ProductWorkspace/Notebook/Source upload `36/36 PASS`; Web production build·TypeScript·12 pages PASS; product boundary `392 files / violations0`, root boundary `415 files / violations0`; `git diff --check`는 CRLF 경고만 반환하고 오류0이다.
- 실제 Provider/browser/운영 DB·Source upload/Studio 생성은 실행하지 않았고, 공개 응답 DTO 변경은 승인된 기존 `/questions` 응답의 additive fields 범위에 한정했다. staged/commit/push/deploy는 0이다.

## 2026-08-21 Question client enriched DTO compatibility repair

- 운영 브라우저의 `대화를 불러오지 못했습니다`는 서버가 추가한 `mode`, `grounding`, `source_scope_summary`, `mismatch`, `next_actions`를 Web `question-answering-api.js`의 구 exact 5-field validator가 거부한 DTO 불일치였다.
- RED: `scripts/tests/question-answering-api.test.mjs`에 승인된 enriched response를 추가했고 기존 client가 `QUESTION_RESPONSE_INVALID`를 반환하는 것을 확인했다(`13 pass, 1 fail`).
- GREEN: 기존 필수 답변/Citation 검증과 legacy response 호환은 유지하면서, 승인된 enriched 5-field shape만 allowlist·길이·mismatch/next_actions 조건으로 검증해 수용했다. unknown mode, empty mismatch actions, unknown fields는 계속 fail-close한다.
- 검증: Question API/UI/real-data/Product/Notebook Node `47 passed`; Web production build·TypeScript·12 pages PASS; Web boundary `392/0`, root boundary `415/0`; `git diff --check` 오류0(CRLF 경고만). 실제 Provider/browser/운영 DB write/commit/push/deploy 0.

## 2026-08-23T02:25:00+09:00 실제 Source 질문·Studio 저장 Gate

- 실제 Source: YSNA 운영 유사 환경의 로그인 Notebook에서 `daon-knowledge-llm-guide.pdf`를 선택하고 `문서 제목은 무엇인가요?`를 실행했다. 답변과 Citation 2·3·4쪽 링크가 표시됐으며, Notebook-scoped same-origin Citation URL을 확인했다.
- 실제 Studio: 같은 Source와 질문 Run으로 근거 기반 보고서(PDF, 표준, 요약·본문·결론, 검토 필수)를 생성했다. 최초 시도는 routing decision의 configured deployment ID와 내부 model record ID 불일치로 `ORIGINATING_RUN_MODEL_UNAVAILABLE`이었고, `c65070b`에서 두 식별자를 모두 조회하도록 수정했다.
- 배포/재검증: YSNA API와 studio-worker를 `c65070b` 기준으로 재빌드·재기동했다. 재시도 Job `studio-job-7d4560a1f8ad8cd7184d9869aabfb8e0`은 `completed`, DB에 `output-5c517909e8f548add11eec71924821ee`가 생성됐으며 새로고침 후 Library가 3개 산출물을 표시했다.
- 회귀: `services/api/tests/test_studio_workspace_postgres.py` `21 passed, 1 skipped`. 변경은 `studio_workspace_postgres.py`와 회귀 fixture에 한정했고 커밋·푸시 `c65070b`, 진행기록 커밋 `cfcdba6`이다.
- 미검증: 다른 Studio 유형(슬라이드·오디오 등), 원격 Oracle 운영 배포, Upstage 간헐 응답 변동의 장기 안정성은 아직 확인하지 않았다. 이 범위는 PASS로 선언하지 않는다.
- 후속 회귀: Studio·질문·egress·runtime HTTP 묶음 `50 passed, 1 skipped, 19 warnings`로 재실행했다. 경고는 기존 httpx per-request cookie deprecation이며 기능 실패가 아니다.

## 2026-08-23 목표 실행 검증 및 독립 검토

- 제품 Node 회귀 `58 passed, 0 failed`, `npm run verify:product-ui-boundary`는 `417 files / violations 0`, OpenAPI 계약 검증 명령은 exit 0으로 완료했다. Web production build는 TypeScript·12 static pages 생성까지 도달했다.
- 독립 검토자는 `c65070b`의 configured deployment ID→실제 `model_deployments.record_id` lineage 보정, Source 질문·Citation·Studio PDF 저장·Library 표시 증거를 계획 Task 5와 대조해 조건부 승인했다. 검토 결과 `REVIEW_COMPLETE`이며 API Studio 회귀는 `21 passed, 1 skipped`다.
- Desktop Rust 계약 테스트는 `npm run verify:desktop-rust-unit`을 실행했으나 저장소의 기존 `apps/desktop/src-tauri/gen` 경로가 존재해 안전 래퍼가 실행을 거부했다(`DESKTOP_CARGO_CHILD_ERROR refusing to run while the desktop Tauri gen path already exists`). 기존 경로는削除하지 않았으며 Rust 결과는 미검증으로 분리한다.
- 전체 작업트리에는 기존 보호 dirty/untracked 변경이 126건 존재한다. 다른 작업의 파일은 수정·stage·복구하지 않았다. Oracle 운영 배포, 기타 Studio 유형, 장기 Provider 안정성, 전체 Source/MCP 브라우저 통합은 여전히 미검증이다.

## 2026-08-23 후속 잔여 범위 검증

- 기타 Studio 유형의 로컬 계약·UI·Library 회귀를 `product-studio.test.mjs`, `product-studio-click.test.mjs`, `offline-studio-ui.test.mjs`, `real-data-conversation-contract.test.mjs`로 실행해 `27 passed, 0 failed`를 확인했다. 이는 실제 Provider 산출물 생성이 아닌 계약·렌더링 검증이다.
- YSNA 운영 유사 환경의 API/Web/document-worker/studio-worker/object-storage 컨테이너는 모두 실행 중이며 API 환경은 `DAON_RUNTIME_PROFILE=production`, 개발 인증 우회 변수 없음이다.
- 비밀값을 출력하지 않는 `run-remote-provider-compatibility.py`를 YSNA에서 3회 실행했다. 매회 설정된 Provider는 UPSTAGE/GROQ/MISTRAL로 확인됐고, 선택된 UPSTAGE probe가 `HTTP 200`, `schema=valid`, `secret_echo=0`, `citations=0`을 3회 연속 반환했다. 장기 안정성 전체를 보증하는 부하·장시간 시험은 아니며 단기 연속 호환성 증거로만 기록한다.
- Oracle 운영 배포는 외부 운영 변경 권한이 필요한 미실행 항목으로 남겼다. 운영 배포 승인 전에는 Oracle 상태를 변경하지 않는다.

## 2026-08-23 Oracle 운영 배포 및 잔여 범위 검증

- 신산님이 운영 배포를 승인했다. 운영 대상은 DNS `daon-user.sinsan.kr`이 연결된 `ysna-server`의 `daon_user` Compose 스택으로 확정했다. 별도 `daon-server` 배포 루트는 존재하지 않았다.
- 배포 전 원격 체크아웃은 `c65070bca4508e132afa45741c9b62386af94c2b`였고, 해당 커밋은 현재 검증된 Studio model-deployment lineage 보정 커밋이다. 원격 `git fetch origin master` 후 원격 저장소의 `origin/master`가 로컬에서 확인한 SHA와 다른 `1b652ec08`을 가리켰으며, 그 커밋의 Compose에는 현재 운영에 필요한 `studio-worker` 서비스가 없어 배포를 중단하고 기존 `c65070b`로 즉시 되돌렸다. 이 과정에서 컨테이너 재기동은 발생하지 않았다. 원격 저장소 SHA 불일치는 예외로 남긴다.
- `c65070b`에서 `api`, `document-worker`, `studio-worker`, `web` 이미지를 재빌드하고 해당 4개 서비스를 재기동했다. `object-storage`는 기존 healthy 컨테이너를 유지했다. 빌드 중 Web Next production build·TypeScript·12 static pages·product boundary `416 files / violations 0`이 통과했다.
- 배포 후 `api`, `web`, `object-storage`는 healthy, `document-worker`·`studio-worker`는 running 상태이며 API 환경은 `DAON_RUNTIME_PROFILE=production`, `DAON_DEV_AUTH_BYPASS` 미설정이다. 공개 `https://daon-user.sinsan.kr/`와 `/notebooks`는 각각 HTTP 200을 반환했다. 원격 HEAD는 `c65070b`이며 기존 untracked `backups/`, 배포자료, 문서, secrets는 보존했다.
- 기타 Studio 유형은 `27 passed, 0 failed` 로컬 계약·UI·Library 검증을 유지한다. 이는 실제 Provider 산출물 생성 검증이 아니다. Provider 안정성은 YSNA 단기 호환성 3회 연속 `HTTP 200/schema valid/secret_echo 0` 증거까지만이며 장기 부하·soak 시험은 미검증이다.
- 현재 잔여 미검증: 로그인 세션을 이용한 Oracle 브라우저 실제 Source 업로드→처리→질문→Citation→Studio 흐름의 재실행, 기타 Studio 유형의 실제 Provider 산출물, 장기 Provider 부하·soak, 원격 저장소 SHA 불일치 원인 조사. 운영 서비스 헬스와 공개 URL만 확인했으며 이 항목들을 완료로 과장하지 않는다.
- 배포 후 기존 로그인 Notebook 브라우저를 same-origin 운영 URL에서 재확인했다. `Daon 실제 기능 검증` Notebook, Raw Source 1건(`daon-knowledge-llm-guide.pdf`, 사용 가능), 질문 답변과 Citation 2·3쪽, Library 산출물 3건이 DOM에 표시됐다. 연결형 Source 2건은 현재 `사용 불가`·재연결 UI로 표시되며, 이는 데이터 부재 상태를 숨기지 않는 현재 계약과 일치한다. 브라우저에서 새 파일 업로드나 삭제는 운영 데이터 변경이므로 이번 Gate에서는 수행하지 않았다.
- Provider 호환성 probe를 YSNA에서 10회 연속 실행했다. 10/10 모두 `provider=UPSTAGE`, `http=200`, `schema=valid`, `citations=0`, `secret_echo=0`이었다. 이는 10회 단기 연속 안정성 증거이며, 장시간·부하 soak 시험의 대체가 아니다.

## 2026-08-23 Oracle 실제 PDF·Studio·Provider·SHA 잔여 범위 재검증

- 최신 Oracle 대상 `daon-user.sinsan.kr`의 로그인 Notebook `새로운 테스트`에서 실제 PDF `daon-user-manual.pdf`(Source version `sv-8e7dad93db27d5f4d8af25f4c986860f`)를 업로드했다. Source 목록은 `사용 가능`으로 표시됐고, 문서 제목 질문 답변과 Citation `1쪽`, `2쪽`이 브라우저 DOM에 표시됐다.
- 같은 Source로 `제약·준수 점검표`를 실제 생성했다. Library에 `Daon 사용자 설명서 운영 절차 준수 점검`이 저장되고 output version `output-version-63909d45745d513cf523e48e0cd6f107`과 Source 1 lineage가 표시됐다.
- 추가 Provider Studio 실제 생성 요청을 수행했다. `비교·데이터 표` Job `studio-job-b685b23cd1b85e26e027a6d28d6e491a`와 `근거 기반 보고서` Job `studio-job-c8b10bef0700d5ae4b46db7c05edf7fc`가 운영 DB의 `studio_generation_jobs`에서 각각 `completed`, `safe_error_code=NULL`로 확인됐다. 각 요청은 동일 PDF Source version과 실제 Run을 사용했다. 브라우저는 비동기 오류 알림을 잠시 유지해 Library 즉시 갱신은 확인하지 못했으므로 다운로드/렌더링은 미검증으로 분리한다.
- Provider 단기 soak을 YSNA에서 비밀값 비노출 helper로 30회 연속 실행했다. `30/30` 모두 `provider=UPSTAGE`, `http=200`, `schema=valid`, `citations=0`, `secret_echo=0`이며 총 소요 약 28초였다. 이는 bounded soak 증거이며 장시간·고부하 시험을 대체하지 않는다.
- 원격 SHA 불일치 원인을 read-only로 확정했다. ysna checkout의 로컬 `refs/heads/master`와 `refs/remotes/origin/master`는 stale `1b652ec08`을 가리켰지만, `git ls-remote origin refs/heads/master`와 새로 fetch한 로컬 `origin/master`는 `632463f812ee17071a6bbbe6528a5dca2b24191a`였다. 운영 HEAD `c65070bca4508e132afa45741c9b62386af94c2b`는 canonical master의 조상이며 `c65070b...origin/master`는 `0 9`로 원격이 9개 커밋 앞섰다. 즉 배포 checkout의 원격 추적 ref가 갱신되지 않은 것이 원인이다. stale 커밋으로의 checkout 시도는 `studio-worker`가 없는 Compose를 발견해 컨테이너 재기동 없이 원복했다.
- 이번 단계에서 소스 코드·운영 DB·Object Storage를 변경하지 않았고, 원격 checkout ref 갱신·운영 재배포도 수행하지 않았다. 미검증은 Studio 다운로드/파일 렌더링, 전체 Studio 유형 행렬, 장시간 Provider 부하·soak, MCP/연결형 Source 실제 연결이다.
- 후속 bounded soak 집계는 YSNA에서 추가 `60/60` 호출 모두 `UPSTAGE HTTP 200/schema=valid/citations=0/secret_echo=0`으로 완료됐다. 총 90회 이상 단기 연속 호출의 성공 증거가 되었지만, 시간 기반 장시간·동시성 고부하 시험은 여전히 미실행이다.
