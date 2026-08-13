# R1-M8-09-EGRESS-POLICY-C01 완료보고

## 2026-08-13 actual Gate 재개 결과

- PostgreSQL 15.18 disposable DB에서 fresh `0001→0012`, fixture 포함 `0012→0011→0012`, deterministic deny backfill, canonical SHA-256, immutable/digest/scope/current unique 거부, FORCE RLS·cross-tenant 0을 실제 검증했다.
- 실제 repository/runtime에서 Organization deny 완화 write 0과 denial Audit, ETag, 동일 Question key의 두 PostgreSQL connection advisory-lock 직렬화를 확인했다.
- current working tree를 production build하여 공용 3330과 분리된 API 18480/Web 14180에서 same-origin login, 정책 GET, 8필드 edit, current-password Step-up, Organization policy 저장·refresh를 실제 Browser로 확인했다.
- 첫 actual 저장은 Step-up Runtime 필수 `Idempotency-Key`가 Web adapter에 없어 `INVALID_REQUEST`였다. RED로 누락을 재현하고 기존 save key를 Step-up header에도 전달하는 최소 교정 후 focused 4 PASS, production rebuild, actual save PASS를 확인했다.
- 저장 후 Organization policy version 2, succeeded Audit 1, Run/RunResult/ModelAttempt 0이었다. ready Source/provider fixture가 없어 deny 외부 Question actual click은 미실행이다.
- 격리 process/test SQLite/log와 `daon_r1_m8_09_*_it_*` DB를 exact 정리했다. remaining 0, 공용 `local-postgres` running=true다.

## 최신 판정

`COMPLETED / ACTUAL_EXTERNAL_QUESTION_DENY_CLICK_PASS`

actual PostgreSQL Gate와 정책 관리 Browser Gate에 이어 외부 Question 제출의 provider transport 0 actual Chrome 클릭까지 통과했다.

## 최종 Idempotency·동시 authorization 재작업

- Question authorization preflight와 Question POST는 동일한 16..128자 safe Idempotency-Key 계약을 사용한다. 15자, 129자, unsafe separator 입력은 400이며 Question service/provider transport/result write가 0임을 Runtime 행동 테스트로 확인했다.
- Egress authorization의 deterministic Run 쓰기는 transaction 첫 단계에서 tenant/workspace/run advisory lock을 얻고 기존 Run과 Decision을 재조회한다. exact frozen context는 replay하고, 다른 payload/policy는 sentinel로 write transaction을 완전히 종료한 다음 별도 fresh transaction에서 denial Audit을 남기고 `QUESTION_NEW_RUN_REQUIRED`를 반환한다. fake transaction 테스트가 `enter→lock→reread→exit→enter→audit→exit` 순서를 고정한다.
- 최종 회귀: focused 13 PASS, API full 350 PASS·26 SKIP·134 subtests, Question/BFF/OpenAPI 52 PASS, Python compile PASS, Product Boundary 281/0, diff-check PASS, staged0.
- 이 단계 당시 actual PostgreSQL concurrency와 authenticated Browser는 미실행이었다. 이후 actual Gate 재개로 PostgreSQL concurrency와 정책 관리 Browser Gate를 완료했다.

## 종료 전 wire masking·Studio originating Run 재작업

- Upstage/Ollama의 실제 준비 payload는 question/evidence가 top-level이 아니라 `messages`의 user `content` JSON 문자열에 있다. 실제 `QuestionAdapterRegistry.prepare` 결과를 사용하는 RED에서 두 provider 모두 원문이 남는 실패와 malformed wire 3종 통과를 재현했다.
- GREEN은 user content를 parse한 뒤 question과 각 evidence text만 deterministic `[MASKED]`로 바꾸고 canonical JSON으로 재직렬화한다. model/system prompt/response schema/options 등 비민감 wire 필드는 보존하며, 인식 불가 구조는 `EGRESS_TRANSFORMATION_FAILED`로 provider transport 전에 fail-close한다. 따라서 authorization byte 수와 fingerprint는 변환된 실제 전송 wire와 exact 일치한다.
- Studio generation 자체는 외부 provider transport가 없으므로 새 EgressDecision/RoutingDecision을 만들지 않는다. 대신 originating Run의 기존 frozen RoutingContext와 Egress/Routing decision을 tenant/workspace/run으로 exact 조회하고, current effective policy Version/Binding IDs·values·fingerprint 및 decision 상호 결속을 검증해 `GenerationSettingsSnapshot.server_policy_projection.authoritative_values.originating_run`에 고정한다. missing/mismatch/stale reference는 generation write 전에 거부한다.
- 최종 자동 검증은 focused 14 PASS·1 SKIP, API full 348 PASS·26 SKIP·134 subtests, Web/BFF/OpenAPI/Product 71 PASS, OpenAPI 75 paths/94 operations/120 schemas, Production Build·TypeScript·Boundary 269/0 및 root Boundary 281/0 PASS다.
- 이 단계 당시 actual PostgreSQL 15과 authenticated Browser Gate는 미실행이었다. 이후 actual Gate 재개로 두 Gate를 완료했다.

## 독립 리뷰 Critical/Important 재작업

- Question은 실제 Adapter wire payload의 question·evidence text/page를 포함한 canonical bytes/fingerprint로 authorization하고, max-bytes를 그 bytes에 적용한다. masking/redaction 정책은 question과 evidence text를 deterministic `[MASKED]`로 변환해 원문 전송을 0으로 만든다.
- Provider kind는 `external_api|server_internal|local_runtime` Canon을 그대로 사용하고 `route_single_model()` 결과를 RoutingDecision에 고정한다. no-evidence도 transport 0의 frozen policy/decision lineage를 남긴다.
- external allow는 same-origin Question authorization preflight에서 current password 재인증, approver Role, deterministic Run, actual prepared payload/provider/deployment/effective policy fingerprint를 결속한 one-time authorization을 발급하고 Question POST가 exact 재계산 후 consume한다.
- GET은 effective와 current Organization/Workspace payload, effective/organization/workspace ETag를 분리한다. Organization 편집은 자기 deny를 parent lock으로 오인하지 않고 organization ETag/current payload를 사용하며, Workspace admin의 tenant-wide Organization write는 403/write0이다.
- Migration 0012는 canonical JSON/text/sha256 digest 일치를 insert trigger로 강제한다. OpenAPI는 route별 exact request와 `{data,meta}`, 신규 Question authorization, provider kind enum을 반영한다.
- 자동 검증: API 342 PASS·26 SKIP·134 subtests, Web/BFF/OpenAPI/Product 60 PASS, React policy click 1 PASS, OpenAPI 75/94/120, Production Build·TypeScript·Boundary 269/0 PASS.
- 이 단계 당시 actual PostgreSQL 15과 authenticated Browser Gate는 미실행이었다. 이후 actual Gate 재개로 두 Gate를 완료했다.

## 첫 INCOMPLETE 독립 리뷰 재작업

독립 리뷰에서 Product Adapter가 `/session/step-up`에 `current_password`를 전송하지만 Runtime `StepUpBody`는 `extra="forbid"`이며 `password`를 필수로 요구해 실제 정책 저장이 422가 되는 Critical을 확인했다. Fake fetch가 Step-up body의 exact key를 검사하도록 먼저 RED를 추가해 `actual=[action_group,current_password,target_id]`, `expected=[action_group,password,target_id]` 실패를 재현한 뒤 Adapter의 필드명만 `password`로 최소 교정했다.

- password 외 추가 필드가 없고 `current_password`가 존재하지 않음을 검사한다.
- 입력 password와 반환 Step-up authorization은 기존 `finally`에서 즉시 소거한다.
- UI/BFF focused 28 PASS, Runtime/Identity/Question 관련 API 34 PASS·2 subtests PASS, Production Build·TypeScript·Boundary 269/0 PASS.
- 이 단계 당시 actual PostgreSQL/Browser Gate는 미검증이었다. 이후 actual Gate 재개로 PostgreSQL과 정책 관리 Browser 검증을 완료했다.

## 판정

`COMPLETED / ACTUAL_EXTERNAL_QUESTION_DENY_CLICK_PASS`

승인된 코드·자동 계약과 Product UI, actual PostgreSQL 15 migration/rollback/RLS/FK/backfill/concurrency, current-source 정책 관리 Browser Gate를 구현·검증했다. 새 일회성 자격의 실제 Chrome Question 클릭에서 Workspace deny, durable Audit/Decision과 provider transport 0을 확인했으므로 `COMPLETED`로 판정한다.

## 구현 결과

- Migration `0012`: immutable Policy Version, scope current Binding, FK·RLS·unique·guard trigger, deterministic Organization/Workspace `deny_external` backfill과 rollback.
- Domain/PostgreSQL: 누락·inactive·stale·scope mismatch fail-close, Organization deny 우선, Workspace 완화 거부, full values/fingerprint/ETag, idempotency와 Audit.
- Run: Frozen RoutingContext, EgressDecision/RoutingDecision pre-transport commit, deny transport/result 0, exact completion 결속. Studio는 `egress_decisions`를 정책 template으로 읽지 않고 Version/Binding을 사용한다.
- Runtime/Web: GET effective와 Organization/Workspace POST, existing Step-up group, If-Match/Idempotency, same-origin BFF exact method, OpenAPI exact request/response.
- Product UI: 조직 설정에서 effective mode·parent lock·bytes·masking/approver projection, current-password Step-up memory-only 처리와 즉시 소거, refresh/error state 보존, 12px/Tooltip 기준.

## 검증

- API 전체: 340 PASS, 26 SKIP, 134 subtests PASS.
- C01 focused: 14 PASS; Studio projection 8 PASS·1 SKIP.
- Web/BFF/OpenAPI: 46 PASS.
- OpenAPI verifier: 74 paths, 93 operations, 115 schemas, SHA-256 `CF3E0AD3A4902C2B598978645DBCD3B5574B058D6C306002CD4025326CE70C21`.
- Next Production Build·TypeScript PASS, Product Boundary 269 files·위반 0.
- `git diff --check` PASS, staged 0. Commit·Push·PR·Deploy 없음.

## 최종 Actual Gate

- actual PostgreSQL과 정책 관리 Browser의 Organization 변경·same-origin·Step-up·Audit 증거는 확보했다.
- production Source upload→processing→index helper로 ready Source와 external_api Provider/Deployment/text binding을 구성했고, 정식 same-origin login 및 Source GET 200을 확인했다.
- 병렬 Studio outputs 503 하나가 정상 Source까지 지우는 UI 결합 결함을 `Promise.allSettled` 독립 결과로 수정했다. ready Source/selected/question 가능 상태를 보존하고 `studioStatus=unavailable`과 safe `studioSafeError`를 ProductStudioPane에 표시하며 내부 URL/stack은 노출하지 않는다. 정상 empty/list·locks·saved outputs 의미를 유지했고 Product Workspace/Studio 14 PASS, Next Production Build·TypeScript, Boundary 269/0을 통과했다.
- production helper를 document job `enqueue→claim→complete`까지 확장해 API/Web 기동 전 `ready|completed|completed`를 확인했다.
- matching Next static chunk 200과 정식 same-origin login을 확인하고 ready Source에서 Question을 실제 1회 클릭했다.
- Question POST는 403 `EGRESS_POLICY_DENIED`; Workspace deny1, Run1, denied EgressDecision1, RoutingDecision1, deny Audit1, ModelAttempt0, RunResult0, provider transport spy0이었다.

## 결과 계약

`COMPLETED | R1-M8-09-EGRESS-POLICY-C01-I001 | 승인 구현, actual PostgreSQL Gate, 정책 관리 Browser Gate와 production ready Source/external Question deny actual Chrome click 수행 | production document job helper와 secret-free external deny evidence/Manifest/Progress/Completion 갱신 | 기존 회귀 및 actual PostgreSQL PASS; Build/TypeScript/Boundary PASS; same-origin login·Source GET 200·Question POST 403; durable deny decision/audit1; model attempt/result/provider transport0; cleanup remaining0·ports0·public running·staged0 | 없음 | 어울1 최종 검토`
