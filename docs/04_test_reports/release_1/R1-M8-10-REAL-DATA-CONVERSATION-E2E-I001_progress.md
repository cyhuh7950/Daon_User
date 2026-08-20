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
