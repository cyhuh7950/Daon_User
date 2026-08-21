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
