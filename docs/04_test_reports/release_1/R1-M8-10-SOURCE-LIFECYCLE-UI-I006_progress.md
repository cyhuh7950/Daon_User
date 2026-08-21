# R1-M8-10-SOURCE-LIFECYCLE-UI-I006 진행 기록

## 2026-08-21 착수·기준선

- 공식 root `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, branch `codex/user-auth-screen-split`, origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`, HEAD `c68ace2a7cd6857bfd94d5e90bd990be65498ea4`, staged0을 확인했다.
- 로컬 cached `origin/master`는 `1b652ec`이며 비교 결과 0 behind/37 ahead다. 최신 fetch는 sandbox의 `.git/FETCH_HEAD` write 거부로 갱신하지 못했다.
- AGENTS의 master-only 원칙과 기존 운영 기준 branch가 충돌했으나, 어울1이 I006에 한해 현재 branch를 임시 통합 기준선으로 승인했다. 완료 후 master 통합 판단이 필요하다.
- 보호 범위는 Mobile·model-connections 삭제, 기존 M5/Windows 문서·Evidence·untracked 전부다. 이전 승인 Source/Cloud 상태 분리 변경인 `packages/ui/src/product-workspace-shell.jsx`, `scripts/tests/product-workspace.test.mjs`만 I006과 겹치는 현재 Writer 변경으로 보존한다.
- 정본 SHA-256: AGENTS `AABB11177EA7541B62C0AD6E6AB2FD745FCD4ADED72A25DF98522FC8E41B47EA`, R1-M5-06 WO `52C24D398B16D317DFBEB2FD27B0C3F665FD16DAE3286B434A3EFCC4B22F006D`, 구현계획 `E04D1A0CE8DB8C0FD03637707D4627273FD679317B412319FBC03645CA3B5654`, 상세설계 `FFB21D198C6A99677097B7CF6949F71C821F91C4162D6F846DA86C5C56D57028`, 테스트계획 `CF607EE9CF25552F051BBC382EB269E5AE11C7C6D2223FE6DE7560F2`.
- 실제 코드 대조: `Promise.allSettled` 이후 한 projection 블록의 예외가 trailing catch에서 Source 오류로 합쳐진다. `0020`의 `notebook_bindings`는 immutable insert-only라 종료 사실을 표현할 정본이 없고, R1-M5-06 request/get/cancel Runtime route는 존재한다.

다음: Source 상태 소유권 deferred/reverse RED를 고정한다.

## 2026-08-21 RED→GREEN 구현

- Source 상태 소유권 RED: Source 200과 동시에 Knowledge/Conversation/Studio projection이 실패하면 공통 trailing catch가 Source 오류를 만들 수 있었고, 이전 Notebook 요청이 늦게 끝나 현재 Source를 덮을 수 있었다. `ProductWorkspaceShell`에 load epoch와 pane별 projection/error 소유권을 적용해 Source 성공은 다른 pane 실패와 독립적으로 유지하고 stale 결과는 0으로 만들었다.
- Notebook 연결 해제 RED: immutable `notebook_bindings`에는 종료 정본이 없었다. 신규 `0021`에 append-only unbinding ledger, idempotency, Audit, FORCE RLS를 추가하고 current selected Context·Source list·질문·Studio가 종료된 binding을 즉시 제외하도록 결속했다. 물리 Source와 Source Version은 보존된다.
- 삭제 요청 RED: Browser가 derivative inventory를 제출하던 기존 입력 계약을 승인 결정에 따라 빈 exact body로 바꿨다. 서버는 original/index/preview/cache/known_local_copy/sync_reference 여섯 class를 실제 정본으로 산출한다. 실제 object만 opaque ID로 기록하고, 결정론적 미생성은 `not_present/not_applicable`, 검증되지 않은 Local Copy는 `verification_pending`으로 유지한다.
- durable Runtime RED: 삭제 request/get/cancel이 메모리 Reference 경계에 머물러 재시작 지속성이 없었다. 신규 `0022`와 `PostgresRetentionRequestService`를 추가해 request/get/cancel, idempotency, locator, exact6 cleanup inventory와 중앙 Audit을 PostgreSQL transaction으로 결속했다. purge/hold 공개 route는 기존 서버 계약을 유지하며 Product UI는 purge를 호출하지 않는다.
- 동시 활성 요청 RED: 동일 Source에 다른 idempotency key를 사용하면 DB unique violation이 `RETENTION_UNAVAILABLE`로 투영됐다. Source-scope advisory lock과 replay 후 authoritative current request 조회를 추가해 `DELETION_REQUEST_ACTIVE` safe conflict/write0로 교정했다.
- API full RED: legacy `FakeNotebook`이 신규 binding ETag를 제공하지 않아 1건 실패했다. 제품 계약을 완화하지 않고 fixture를 exact `\"notebook-binding:1\"`으로 교정했다.
- OpenAPI RED: 승인된 source-unbindings path가 exact verifier allowlist에 누락됐다. 새 path의 POST만 허용하도록 verifier를 동기화했다.
- Browser lint RED: 기존 safe projection의 내부 URL 탐지 regex literal 자체가 browser boundary scanner에 걸렸다. 탐지 기능은 유지하면서 금지 URL literal이 bundle source에 직접 존재하지 않도록 안전하게 구성했다.

## 2026-08-21 검증·종료 전 상태

- actual PostgreSQL disposable Gate: fresh `0001→0022`, empty rollback `0022→0019`, reapply, actual 10/10, live downgrade fail-close, `db=0 role=0`.
- actual PostgreSQL 범위: unbind append-only/concurrent stale ETag/replay/cross-scope0, selected Context filtering, authoritative inventory present/absent/unverified, durable FastAPI request/get/cancel/restart, active duplicate safe conflict, cleanup6, Audit2.
- API full: `492 passed, 42 skipped, 137 subtests passed`; skip은 명시적 외부 DSN/환경 Gate이며 I006 actual PostgreSQL은 별도 disposable Gate에서 실행했다.
- Node focused: `28/28`; OpenAPI `paths=75 operations=94 schemas=120 errors=31`, SHA-256 `594AED28565CCDBA60F3A12565071F7EAE5239544D5632508BD612EA8D180E0A`.
- Web lint 6 files PASS; Next production build/TypeScript PASS; Product UI boundary 392 files, violation0/error0.
- 운영 실제 사용자 Source에 대한 delete/unbind/purge/write와 Provider 호출은 0이다. 1920×1080 actual Browser destructive dialog 검증은 수행하지 않았으며 React actual behavior 검증과 분리한다.
- commit/push/deploy/stage는 0이고, 기존 Mobile/model-connections/Windows/M5 dirty·untracked는 보존했다.
- Evidence manifest는 6개 current artifact의 bytes/SHA-256을 재계산해 일치했으며 manifest 자체는 `1793 bytes`, SHA-256 `EA86A856C74B858B8B5C23E382FEB9EBB655CE446D3D870BDC14BA935AE0E781`이다. 대상 파일 secret/private-key/DSN 원문 scan은 match0, `git diff --check` 오류0, staged0이다.

## 2026-08-21 독립 검토 REWORK1 착수

- 판정 `REWORK_REQUIRED(Critical0/Important5/Minor1)`을 코드와 대조했다. 지적은 모두 현재 구현에서 재현 가능하다.
- I1: durable deletion request는 Source를 `source_active=false`로 만들지만 Notebook Context·Source/Question/Citation/Studio SQL은 unbinding만 검사한다. 서버 소비 predicate 누락이다.
- I2: cloud Runtime create/get/cancel은 PostgreSQL이나 hold/release/purge는 ReferenceRetentionService를 사용해 물리적으로 분리되어 있다. 재시작 뒤 Hold/Purge 정합성을 보증하지 못한다.
- I3: Product UI는 요청 직후 local state만 보존하며 refresh 뒤 request ID/state/version/grace/hold를 복원할 정본 projection이 없다.
- I4: unbind는 idempotency-key lock과 binding-specific lock을 사용해 동일 Notebook의 서로 다른 Source가 같은 stale ETag로 동시에 성공할 수 있다.
- I5: authoritative inventory가 create transaction 전에 별도 transaction으로 산출되어 Source Version/index/sync 변경과 request commit 사이 snapshot race가 있다.
- Minor: retention Browser DTO validator가 outer/data identity와 allowed state/date/ETag를 충분히 exact 검증하지 않는다.
- 구현 순서는 I1 predicate→I2 단일 durable service→I3 lifecycle projection→I4 notebook lock→I5 same-transaction inventory→DTO hardening이다. 공개 route 추가 없이 기존 Notebook Context의 별도 safe lifecycle projection을 우선 검토하며, inactive Source 본문/Version은 Context Source 목록에는 포함하지 않는다.

## 2026-08-21 독립 검토 REWORK1 RED→GREEN·종료 전

- I1 RED→GREEN: `source_active=false`를 Notebook selected Context/Conversation, processing read, Question preflight·replay·Citation, Studio report 및 Studio Workspace create/list/version/action/export의 authoritative SQL predicate로 결속했다. create 직후 Source와 이를 근거로 한 결과는 서버에서 제외되고 cancel 뒤 Source selected Context가 복구됨을 actual PG에서 확인했다.
- I2 RED→GREEN: cloud Runtime의 retention service를 `PostgresRetentionRequestService` 하나로 결속하고 hold/release/purge locator·idempotency·Audit·RLS를 `0022`에 추가했다. 실제 PG에서 active hold→blocked request, restart get, release→grace, cancel→restore와 fixture-only expired request cleanup6→purged를 검증했다. Browser purge call은 0이다.
- I3 RED→GREEN: inactive Source를 usable `sources`에 섞지 않고 Notebook Context에 exact safe `source_deletion_requests` projection(request/source/state/version/grace/hold)만 추가했다. Adapter와 Product UI는 refresh 후 삭제 상태와 exact ETag cancel을 복원하며 epoch가 지난 Context 결과는 기존 load 경계에서 반영0이다.
- I4 RED→GREEN: unbind lock을 Notebook scope로 올리고 replay→Notebook ETag→binding→append 순서를 고정했다. actual PG의 서로 다른 두 Source/same stale ETag 경쟁은 success1/412 one, append/activity/idempotency1이다.
- I5 RED→GREEN: inventory는 Source advisory lock을 획득한 deletion transaction 내부에서 산출한다. `0022` trigger가 source_versions/index_versions/sync_preview_items mutation에도 같은 lock을 적용해 request snapshot과 mutation을 직렬화한다.
- Minor RED→GREEN: Browser retention response는 outer/data exact keys, request/source identity, allowed state, ISO timestamps, cleanup6, exact ETag를 검증하며 extra/mismatch는 projection 전 fail-close한다. Node focused `23/23` GREEN.
- actual PG 최종: fresh `0001→0022`, rollback `0022→0019`, reapply, actual `10/10`, live downgrade fail-close, cleanup `db=0 role=0`. purge fixture의 잘못된 test column과 DB-clock grace fixture를 각각 RED로 확인해 테스트 데이터만 최소 교정했다.
- API full 최종: `493 passed, 42 skipped, 137 subtests passed`. 최초 full에서 신규 Context safe key를 누락한 legacy expected fixture 1건만 RED였고 exact 계약으로 교정 후 focused1/full 모두 GREEN이다.
- Node `23/23`, OpenAPI `75/94/120/31` SHA `594AED28565CCDBA60F3A12565071F7EAE5239544D5632508BD612EA8D180E0A`, lint4, Next build/TypeScript, boundary392 violation0/error0가 통과했다.
- 실제 운영 사용자 Source write/delete/unbind/purge와 Provider 호출, commit/push/deploy/stage는 모두 0이다. 1920x1080 Browser destructive interaction은 계속 NOT_RUN이다.

## 2026-08-21 REWORK2 R2-M1 actual barrier 재작업

- 착수: Source lifecycle UI stale 및 retention concurrency 재검증. `ProductWorkspaceShell` keyed remount와 adapter/notebook identity 변경 시 기존 dialog/pending mutation 폐기를 유지했다. UI focused `12/12` GREEN.
- R2-I2 actual PG: `release/create`, `hold/cancel`, `hold/purge` 2-connection race를 disposable PostgreSQL에서 검증했다. terminal history 허용을 위해 migration `0022`에서 legacy source-global UNIQUE를 current-state partial UNIQUE로 교체했고, fresh upgrade/rollback/reapply와 live downgrade block을 통과했다. actual notebook PG `10/10`, cleanup `db=0 role=0`.
- R2-M1 최초 barrier 실패 원인: raw test connection에서 `app.capability`를 누락해 INSERT가 trigger 이전 RLS 경계에서 중단됐다. capability를 추가한 뒤 `source_versions` fixture가 immutable `canonical_json/canonical_text/digest_sha256` 정합성을 지키지 않아 `CANON_DIGEST_MISMATCH`가 발생했다. 이는 제품/runtime 오류가 아니라 test fixture 오류로 확인했다.
- 복구: 테스트 fixture에 승인된 retention capability와 canonical JSON/text SHA를 추가하고, 실제 trigger 연결 및 request-side Source lock barrier를 재정렬했다. `r2i006l`은 placeholder 오류로 RED였고 query를 정정했다. 최종 `r2i006m`에서 source_versions/index_versions/sync_preview_items 각각의 실제 INSERT transaction이 Source trigger lock을 보유하고, request-side 역방향 probe가 차단되며 rollback 후 partial row가 남지 않음을 검증했다. fresh upgrade/rollback/reapply, actual PG `10/10`, live downgrade block, cleanup `db=0 role=0`.
- 추가 복구: `ThreadPoolExecutor` block의 잘못된 들여쓰기를 dedent하고, 의도적인 rollback RuntimeError는 테스트에서 명시적으로 수용했다. 제품 runtime/schema 변경은 없었다.
- 추가 review gap: 기존 request-side 역방향 검증은 수동 `pg_advisory_xact_lock` probe였고 실제 `PostgresRetentionRequestService.create_request()`가 lock을 보유한 상태의 INSERT 경합을 포함하지 않았다. 이를 실제 service transaction pause/provider barrier로 교체하는 작업을 시작했다.
- R2-M1 service barrier 완료: disposable PG에서 실제 `create_request()`가 inventory 단계에서 Source lock을 보유하도록 정지시킨 뒤, source_versions/index_versions/sync_preview_items 각각의 실제 INSERT가 `55P03`으로 차단됨을 확인했다. service transaction release 후 동일 INSERT는 성공했고, block 시 partial row는 0이었다. 이후 request cancel 및 전체 lifecycle assertion도 통과했다.
- 최종 wrapper `r2i006q`: fresh `0001→0022`, rollback `0022→0019`, reapply, actual `10/10`, live downgrade block, cleanup `db=0 role=0`. `py_compile` GREEN. 이 기록의 초기 RED들은 모두 테스트 fixture/들여쓰기/assertion 오류로 분리·교정했으며 제품 runtime/schema 변경은 없다.

## 2026-08-21 독립 검토 REWORK3 manifest 정합성

- 판정: `REWORK_REQUIRED(Important1)`. 최신 변경 3개(`0022_retention_request_runtime.py`, `retention_request_postgres.py`, `product-workspace-shell.jsx`)가 기존 Evidence manifest의 bytes/SHA 목록과 불일치했다.
- 원인: R2 수정·실제 barrier 테스트와 UI keyed remount를 추가한 뒤 기존 manifest를 재생성하지 않은 기록 정합성 누락이다. 제품 동작 오류가 아니며 최신 파일을 기준으로 manifest를 재생성한다.
- 조치: 최신 bytes/SHA manifest 재생성 후 actual PG barrier, focused UI/HTTP, secret scan, diff-check 및 필수 hash 검증을 다시 실행한다. stage/commit/push/deploy는 0으로 유지한다.

- REWORK3 actual 재검증 1차: fresh upgrade/rollback/reapply는 통과했으나 wrapper label `manifest-r3-final`이 disposable PostgreSQL role identifier에 그대로 결합되어 syntax error가 발생했다. 이는 제품/마이그레이션 오류가 아닌 runner 인자 오류이며 `NOTEBOOK_GATE_CLEANUP db=0 role=0`을 확인했다. 영숫자-only label로 1회 교정 실행한다.

- REWORK3 actual 재검증 완료: 영숫자 label `manifestr3final`로 fresh `0001→0022`, empty rollback `0022→0019`, reapply, actual PostgreSQL `10 passed in 5.41s`, live downgrade fail-close를 통과했고 `NOTEBOOK_GATE_CLEANUP db=0 role=0`이다. manifest current entries는 `mismatches=0`으로 일치한다.
- REWORK3 최종 정적 확인: UI focused `12/12`, retention HTTP focused `4/4`, Python `py_compile` 통과, `git diff --check` 오류0, staged0 유지. 다음은 최신 manifest 자체 hash와 제한적 secret scan 확인이다.

- REWORK3 종료: manifest `docs/03_evidence/release_1/R1-M8-10-SOURCE-LIFECYCLE-UI-I006/manifest.json`은 `1794 bytes`, SHA-256 `2CBE7F5971F57C34A70DF0193D153FF11C35840552F1978211C697B2BAC51AE4`, entries `6`, mismatches `0`이다. 대상 3개 최신 bytes/SHA도 일치한다. 제한적 private-key/DSN/API-key scan `matches=0`, `git diff --check=0`, `staged0=true`. I006의 1920x1080 destructive Browser actual은 기존 판정대로 NOT_RUN이며 이번 manifest 재검증에서 실행하지 않았다. commit/push/deploy 및 운영 write/provider 호출은 0이다.

## 2026-08-21 I006 통합 대상 분류(승인 후, stage 전)

- I006에 포함할 exact closure는 다음으로 고정한다: `packages/contracts/openapi/v1/openapi.json`; `apps/web/lib/source-retention-api.js`, `apps/web/lib/notebook-api.js`, `apps/web/lib/product-workspace-api.js`; `packages/ui/src/product-workspace-shell.jsx`, `packages/ui/src/notebook-context-adapter.js`; `services/api/migrations/versions/0021_notebook_source_unbinding.py`, `services/api/migrations/versions/0022_retention_request_runtime.py`; `services/api/src/daon_user_api/retention_inventory_postgres.py`, `retention_request_postgres.py`, `retention.py`, `runtime.py`, `notebook.py`, `notebook_postgres.py`, `document_processing_postgres.py`, `question_answering_postgres.py`, `studio_report_postgres.py`, `studio_workspace_postgres.py`, `cloud_storage.py`; `apps/web/components/actual-workspace.jsx`; `scripts/tests/source-retention-api.test.mjs`, `product-workspace.test.mjs`, `notebook-context-adapter.test.mjs`; `services/api/tests/test_cloud_storage.py`, `test_notebook.py`, `test_notebook_postgres.py`, `test_retention_runtime_http.py`, `test_runtime_http.py`, `test_studio_report_runtime_http.py`; I006 Work Order/Progress/Completion, I006 Evidence manifest 및 `docs/superpowers/specs/2026-08-21-source-lifecycle-ui-design.md`, `docs/superpowers/plans/2026-08-21-source-lifecycle-ui.md`.
- 보호 또는 다른 작업 범위로 명시 제외한다: `apps/mobile/**` 삭제 전체, `.stage-a-operations-react-*`, `.codex-temp`, Windows WebView recovery 파일·Evidence·Progress, M5/M5-07 문서·Evidence, R1-USER-PRODUCT-SEPARATION 문서·Evidence, model-connections 삭제/설정 파일, Native/Tauri 관련 테스트, 이전 Phase D/E 전용 문서·Evidence, 그리고 위 closure에 없는 모든 dirty/untracked. `services/api/migrations/versions/0005_retention_legal_hold.py`도 published baseline 보호를 위해 제외한다.
- 현재 stage는 0이며 위 분류 기록만 추가했다. commit/push/deploy는 이 기록 시점에 수행하지 않았다. 실제 stage 직전 `git diff --name-only --cached`, 보호 제외 목록, manifest SHA를 다시 대조한다.

## 2026-08-21 작업 재개

- 신산님 지시에 따라 중단 상태에서 재개했다. 기존 운영 Source 변경·커밋·푸시·배포는 계속 0으로 유지한다.
- R2-M1 actual barrier 보완 결과는 어울2 보고와 progress 기록을 회수했으나, 독립 최종 검토 결과 수집은 재개 후 다시 확인한다.
- 다음: 최신 diff와 R2-M1 evidence를 독립 검토하고, 정적/자동/실제 검증을 구분해 최종 판정한다.
