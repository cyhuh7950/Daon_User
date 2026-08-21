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

## 2026-08-21 승인 SHA ysna-server 격리 배포 착수

- 승인 SHA `edb401e`는 이미 push된 기준선이다. 배포 대상은 `/home/ubuntu/deploy/daon-user` 하위의 Daon 전용 경로이며, ARM64 `aarch64`를 확인했다.
- 사전 read-only inventory: 기존 `daon_user-api`, `daon_user-web`, `daon_user-document-worker`, Daon object storage는 healthy/running; `shared-db`, `proxy-network`, `nginx-proxy-manager`, `daon2-*`는 공용/타 서비스로 변경 금지. 원격 기준 checkout은 `6b5d6fa`이며 보호 `backups/`, `secrets/` untracked만 존재했다.
- 다음 순서: exact SHA checkout 및 대상 tree hash 확인 → Daon 전용 DB 논리 backup/사전 migration 확인 → 필요한 Daon API/Web/Worker만 build/recreate → migration apply/health/server smoke → 실패 시 기존 image/tag rollback. Oracle 운영 배포와 Provider/사용자 Source write는 0이다.

- 배포 실행: 원격 build SSH 세션은 로컬 중단 시점에 출력이 끊겼으나 원격 build 프로세스는 완료되어 새 Daon 전용 이미지가 생성됐다(API `sha256:9717c44a56f7…`, Worker `sha256:192c3c1398c3…`, Web `sha256:59ad72235bb6…`). 중복 build는 실행하지 않았다.
- migration/재생성: backup `791408 bytes`, SHA-256 `a89f9ca312208ceb6aec64b8e4a1687f1692a1458976662147a99e7ba8e0a946`; exact SHA worktree의 0021→0022를 적용해 DB `0022 (head)` 확인. `api`, `document-worker`, `web`만 `--no-deps --force-recreate`했고 object-storage/shared-db/proxy/타 서비스는 변경하지 않았다.
- 서버 smoke: 새 API 내부 `/health/live=200`, `/health/ready=200`, Web HTTPS `/=200`(7349 bytes), 최근 3분 Daon API/Web/Worker error/traceback/fatal/exception 로그 0. 공개 `/health/ready=404`는 reverse proxy 비공개 경계로 내부 health와 분리 기록한다. Oracle 운영 배포·Provider/사용자 Source write는 0이다.
- SSH 경계 복구: 기본 SSH 호출과 달리 승인된 `ssh -F C:\\Users\\cyhuh\\.ssh\\config ysna-server`로 `hostname=ysna-server`, `whoami=ubuntu`, exact SHA 및 API/Web healthy를 재확인했다. 이후 ysna-server 명령은 이 명시 config를 사용한다.

## 2026-08-21 운영 Notebook Context 오류 read-only 1차 조사

- 현상: 로그인 후 `/notebooks` 목록은 정상이나 Notebook 선택 시 Browser alert `NOTEBOOK_CONTEXT_INVALID`; 사용자 운영 데이터 변경·Provider 호출은 0이다.
- 서버 증거: 승인 SSH config로 API/Web/Worker 최근 30분 logs에서 `NOTEBOOK_CONTEXT_INVALID`, `/context`, 4xx/5xx correlation row를 찾지 못했다. 새 API 내부 `/health/live=200`, `/health/ready=200`; Web HTTPS `/=200`이다.
- DB read-only 집계: `notebooks=1`, `notebook_bindings=1`, migration `0022`; 원문 ID/사용자 정보는 출력하지 않았다. 현재 exact 사용자 요청의 HTTP status/response body는 Chrome Network correlation이 없어 미확정이다.
- 코드 경계: `NotebookContext` 생성 실패는 tenant/workspace/actor/trace/policy 식별자 SAFE_ID 위반 시 서버 `NOTEBOOK_CONTEXT_INVALID`가 되고, BFF client는 Context response shape/ETag 불일치도 동일 safe code로 fail-close한다. 따라서 현재는 서버 4xx와 client projection invalid를 구분할 증거가 부족하며 수정/재배포하지 않는다. 다음은 동일 Chrome 요청의 Network status/response 또는 서버 correlation 확보이다.
- 추가 Browser 증거: 사용자가 `/bff/api/.../context`를 직접 탐색할 때 Chrome이 `ERR_BLOCKED_BY_CLIENT`로 차단했다. 이는 확장/브라우저 차단과 Context fetch 실패의 연관 가능성은 있으나 제품 서버 원인으로 확정할 수 없다. 이 단계에서 앱 수정·재배포는 수행하지 않는다.

## 2026-08-21 Context ETag 계약 교정 착수

- 확정 원인: `runtime.py:get_notebook_selected_context`가 `_json_with_etag(content, json.dumps(content["data"], sort_keys=True))`로 projection hash ETag를 반환했지만 Web `notebook-api.js`는 Context에 exact `"notebook-binding:<version>"`만 허용한다. 따라서 HTTP 성공 응답도 client에서 `NOTEBOOK_CONTEXT_INVALID`로 fail-close했다.
- RED: 실제 Context route 응답 ETag가 selected binding ETag가 아닌 projection hash인 기존 경계를 테스트로 고정한다. 최소 수정은 해당 route에서 `selected.etag`를 직접 response ETag로 설정하는 것이며 다른 `_json_with_etag` route는 변경하지 않는다.

- RED 실행: `services/api/tests/test_runtime_http.py::RuntimeHttpTests::test_notebook_create_list_get_update_title_contract`에서 실제 응답 ETag가 `"projection-862ed9ad704a4f84317306cb"`로 반환되어 exact `"notebook-binding:1"` assertion이 실패했다.
- GREEN 수정: `services/api/src/daon_user_api/runtime.py`의 Context route만 `JSONResponse(content)` 후 `response.headers["ETag"] = selected.etag`로 교정했다. 다른 `_json_with_etag` 경로는 변경하지 않았다.
- GREEN 검증: focused runtime 1 passed; runtime HTTP 전체 `28 passed, 19 warnings, 2 subtests`; Node notebook/context/product tests `21 passed`; Web production build·TypeScript·boundary `392 files, violations=0, boundaryErrors=0`.

## 2026-08-21 Context ETag fix commit/deploy

- Stage는 수정 파일 3개(`runtime.py`, `test_runtime_http.py`, I006 Progress)만 포함했고 보호 dirty/untracked는 제외했다. `git diff --cached --check=0` 후 commit `ba52cca1e9213debee4bf09cd8908db513a7e7d6`, push `origin/codex/user-auth-screen-split` 성공.
- ysna-server exact SHA worktree `/home/ubuntu/deploy/daon-user/deploy/r1-m8-10-source-lifecycle-ui-i006/ba52cca1e9213debee4bf09cd8908db513a7e7d6`를 생성하고 API image를 ARM64 build/recreate했다. API image digest `sha256:9580c67c84a5c803373b158c4a0ddf52dbeaf7c2d443f4161d94321e01186d43`; API healthy, internal live/ready `200`, DB migration `0022` 유지.
- Web/Worker/object-storage/shared-db/proxy는 이번 ETag fix에서 변경하지 않았다. Chrome 동일 사용자 Notebook URL의 Context ETag/alert 소거는 현재 직접 Browser connector가 없어 미검증이며, 운영 Source 변경·Provider 호출은 0이다.

## 2026-08-21 Source 초기 일시 실패 retry 보완 착수

- 승인 범위: transient fetch/network `TypeError`만 기존 250ms bounded 1회 재시도에 추가한다. `AbortError`, 4xx/auth, response contract 오류는 재시도하지 않는다. 기존 epoch/Abort 경계와 Source/Knowledge/Conversation/Studio 상태 분리는 유지한다.
- RED 계획: ProductWorkspaceShell 실제 React deferred adapter로 transient retry 성공, abort 중 retry 0, non-retryable 오류 호출 1회를 고정한다. 기존 보호 파일은 수정하지 않는다.

- GREEN 구현: `packages/ui/src/product-workspace-shell.jsx`에 browser 표준 transient fetch 메시지(`Failed to fetch`, `NetworkError when attempting to fetch resource`, `Load failed`)를 판별하는 내부 predicate를 추가하고 기존 250ms 1회 retry 조건에만 결합했다. AbortError, 4xx/auth 및 contract error는 기존대로 즉시 fail-close한다.
- UI focused RED→GREEN: `scripts/tests/product-workspace.test.mjs`에 transient retry 성공, abort retry 0, non-retryable contract 1회 호출 3건을 추가했다. 전체 ProductWorkspace/Notebook/Source retention Node focused `27 passed`이며 React act warning 0이다.
- Web 검증: Next production build·TypeScript PASS, boundary `392 files`, violations `0`, boundaryErrors `0`. 아직 commit/push/deploy는 하지 않았으며 다음은 diff/staged/보호 dirty 확인 후 승인된 좁은 배포 판단이다.

## 2026-08-21 작업 재개

- 신산님 지시에 따라 중단 상태에서 재개했다. 기존 운영 Source 변경·커밋·푸시·배포는 계속 0으로 유지한다.
- R2-M1 actual barrier 보완 결과는 어울2 보고와 progress 기록을 회수했으나, 독립 최종 검토 결과 수집은 재개 후 다시 확인한다.
- 다음: 최신 diff와 R2-M1 evidence를 독립 검토하고, 정적/자동/실제 검증을 구분해 최종 판정한다.

## 2026-08-21 실제 Chrome 재검증

- Context ETag 원인을 수정한 배포 SHA `ba52cca1e9213debee4bf09cd8908db513a7e7d6`에서 로그인 후 동일 Notebook을 다시 열었다.
- `NOTEBOOK_CONTEXT_INVALID`가 사라지고 Workspace가 정상 렌더링되었으며 Raw Source 1건과 Source 작업 메뉴가 표시되었다.
- 작업 메뉴에서 `Notebook에서 제거`, `Source 삭제 요청`, `취소`가 모두 노출됨을 확인했고, 실제 제거·삭제 요청·파일 업로드는 실행하지 않았다.
- Chrome viewport는 `1700x1002`로 확인되어 지정 기준 `1920x1080`과 일치하지 않는다. viewport capability가 제공되지 않아 1920x1080 강제 검증은 미실행으로 남긴다.

## 2026-08-21 Source 목록 오류 재현·복구 확인

- 사용자 화면의 `Source를 불러오지 못했습니다` 상태에서 실제 Chrome의 `다시 시도`를 실행하자 동일 세션·동일 Notebook에서 Raw Source 1건(`daon-knowledge-llm-guide.pdf`)이 즉시 표시되고 `Source 준비` 상태로 회복됐다.
- 동일 URL을 재진입하는 5회 반복에서 모두 `Raw Source 목록` 1건과 `Source 준비`가 확인됐다. Source unbind/delete request/upload 및 Provider 호출은 실행하지 않았다.
- ysna-server read-only 조사에서 API/Web 최근 로그에 Source endpoint 4xx/5xx, `SOURCE_LIST_INPUT_INVALID`, `SOURCE_LIST_RESPONSE_INVALID`, 예외 row가 없었고, route 응답 shape와 client exact 검증도 일치했다.
- 현재 증거상 영구적인 Source API/DB 오류는 확인되지 않았으며, 최초 로드 시의 일시적 브라우저/BFF 요청 실패 가능성이 남아 있다. 최초 실패 요청의 HTTP status/response body를 직접 수집할 Network capability는 제공되지 않아 원인을 확정하지 않는다.

## 2026-08-21 transient Source retry 배포·재검증

- `fa86729`를 `origin/codex/user-auth-screen-split`에 push했다. 변경은 브라우저 표준 transient fetch `TypeError` 1회 재시도와 3개 focused UI 테스트이며, 보호 dirty/untracked 파일은 stage하지 않았다.
- ysna-server 격리 worktree에서 Web 이미지를 재빌드·재생성했다. Web production build/TypeScript/boundary가 통과했고 boundary는 `414 files`, violations `0`, boundaryErrors `0`이었다. API/DB/object-storage/shared-db/proxy는 재생성하지 않았다.
- 배포 후 Web health `healthy`, HTTPS `/` `200`, 최근 Web error/exception/fatal 로그 0. 로그인된 동일 Chrome Notebook을 재진입해 `Raw Source 목록` 1건과 Source 정상 상태를 확인했으며 Source 변경·삭제·업로드·Provider 호출은 0이다.
