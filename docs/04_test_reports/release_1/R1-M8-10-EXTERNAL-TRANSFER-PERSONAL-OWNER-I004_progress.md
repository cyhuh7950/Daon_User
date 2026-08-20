# R1-M8-10-EXTERNAL-TRANSFER-PERSONAL-OWNER-I004 진행 기록

## 2026-08-21 착수

- 정본: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, Branch `codex/user-auth-screen-split`, origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`, HEAD `7e9378c59b9bd714916126ee42a758a109b037e0`, staged0.
- 최초 RED: 기존 per-question Step-up 계약을 기준으로 personal owner의 `/questions/authorization` 201을 기대했으나 403을 확인했다(1 failed, 3 passed). 이후 신산님 승인으로 요구가 변경되어 이 RED와 personal-owner approver 확대 방안은 폐기했으며 제품 코드에는 적용하지 않았다.
- 확정 계약: Step-up은 organization·workspace 정책 변경과 License 등 관리자 설정에만 유지한다. 일반·근거 질문과 Studio 사용은 유효 로그인 Session, `EXTERNAL_LLM` 권한, effective Egress Policy와 Provider/목적지/분류/마스킹/redaction 검사를 매 요청 수행하며 비밀번호 재입력은 0이다.
- 변경 목표: ProductWorkspaceShell의 질문 비밀번호/추가 인증 UI와 authorize 호출을 제거한다. `/questions`는 외부 Provider 선택 시 Session 권한과 effective Policy를 검증해 안전한 내부 authorization projection을 구성하되 Step-up issue/consume은 하지 않는다. 호환 `/questions/authorization` API는 유지한다.
- 보호: 공개 API/data/security 변경0, 외부 Provider/production write0, commit/push/deploy0, 다른 dirty/untracked 미접촉.
- 새 RED: Runtime 6개 중 4개 실패(allow 경로가 `STEP_UP_REQUIRED`, deny policy가 service 진입, missing permission이 prepare까지 진행), UI 8개 중 비밀번호 UI 노출 1개 실패. 승인된 변경 필요성을 고정했다.
- 첫 GREEN: ask route는 외부 Provider 선택에서 effective allow일 때 Session에 결속된 fingerprint projection을 만들고 Step-up issue/consume을 0으로 유지한다. deny policy는 `EGRESS_POLICY_DENIED`, 비인증·cross-tenant는 provider/write0이다. ProductWorkspaceShell은 질문 비밀번호 DOM과 authorize 호출을 제거하고 ask body에서 `step_up_authorization_id`를 보내지 않는다.
- 오류 복구: 최초 구현은 Provider 선택 전에 모든 질문에 `EXTERNAL_LLM` permission을 요구해 local/server Provider까지 차단할 위험이 있었다. `local_runtime` + workspace_admin 기대를 RED로 추가해 실패를 고정한 뒤, 초기 VIEW 검증은 유지하고 prepared selection이 `external_api`일 때만 `EXTERNAL_LLM`을 재검증하도록 교정했다. local runtime 허용과 external missing-permission 차단이 함께 GREEN이다.
- 기존 테스트 변경 사유/전후: `test_security_audit_persistence_contract.py`는 question external transfer가 민감 관리자 mutation처럼 Step-up consume한다고 정적으로 요구했다. 변경 전에는 `operation="question.external_transfer"`를 필수로 보았고, 변경 후에는 ordinary question route에 consume/operation이 없고 external selection에 `EXTERNAL_LLM` permission 검사가 존재함을 요구한다. Policy/License/Sync/Recovery 등 관리자 Step-up 목록은 그대로다.
- GREEN: focused API 21/21 및 최종 session/provider scope 10/10, Web/Common UI 42/42, OpenAPI paths75/operations94/schemas120, Web production build+TypeScript+product boundary PASS, changed product lint 4 files PASS, product boundary 414 files PASS.
- 전체 API: `PYTHONPATH=src;.`로 476 passed, 40 skipped, 137 subtests passed. 첫 실행의 유일 RED는 위 stale security contract였으며 승인 계약에 맞춰 교정 후 전체 GREEN이다.
- 분리 회귀: `npm run verify:workspace`는 37개 중 33 PASS, 4 RED이며 AuthLanding 전환, 확장된 safe state, legacy workspace redirect 등 기존 Phase E 이후 fixture drift로 이번 변경 파일과 무관하다. ProductWorkspace/Question/Policy/Studio focused 42개와 Web build는 GREEN이다.
- 외부/운영 상태: Provider 실제 호출0, production policy/data write0, commit/push/deploy0.
- 종료 Gate: `git diff --check` 오류0, staged0. Browser 변경 파일 absolute/internal URL·`NEXT_PUBLIC_API_BASE_URL`·Secret marker0이며 검색된 loopback 3건은 server-only Runtime bind 기본값, `step_up_authorization_id` 1건은 호환 authorization 응답 validator다. Product UI/adapter의 `authorizeQuestion`, `questionPasswordRef`, question step-up body는 0이다.
- 보호 상태: 기존 Mobile/model-connections 삭제, R1-M5 문서, Windows WebView recovery 및 기타 dirty/untracked는 restore/delete/stage하지 않았다.
- 종료 판단: 코드·계약·focused/full 회귀는 완료. 실제 Provider/운영 write는 작업지시상 금지되어 실행하지 않았다.

## 2026-08-21 독립 검토 REWORK1 — authoritative replay 및 external exact preflight

- 검토 finding 재현 RED: focused Runtime에서 기존 7 PASS/신규 11 FAIL을 확인했다. 완료 replay가 current Notebook Binding·`EXTERNAL_LLM`·effective Policy보다 먼저 반환되었고, 저장 `Run`에는 replay 판정용 `provider_kind`/egress scope가 없었다. 신규 external 질문은 effective `mode`만 선검증하여 Provider·목적지·payload 크기·분류·마스킹·redaction 불일치가 뒤쪽 PostgreSQL authorizer에서 도메인 행 생성 후 거부될 수 있었다.
- 최소 교정: `Run.canonical_json`에 선택 `provider_kind`와 외부전송의 안전한 `egress_scope`(`destination`, payload byte 수, classification, masking/redaction 요구)를 함께 저장하고 internal `StoredQuestionAnswer`에만 투영한다. 공개 Question DTO/route에는 필드를 추가하지 않았다. legacy completed Run에 canonical replay metadata가 없으면 `QUESTION_REPLAY_UNAVAILABLE`로 fail-close한다.
- replay 경계: request fingerprint 확인 전에 현재 Notebook의 요청 Source/Knowledge Binding을 재검증하고, repository의 conversation Binding도 현재 선택 Notebook에 존재해야 한다. external replay는 current `EXTERNAL_LLM`과 current effective Policy exact 조건을 모두 다시 확인한 뒤 저장 결과를 반환한다. permission revoke 403, policy deny 403, binding 제거 404이며 provider/prepare/ask write0이다. `local_runtime|server_internal` replay는 VIEW와 Binding만 요구하고 external Policy는 읽지 않는다.
- external 신규 경계: `external_question_policy_matches` 단일 pure helper를 Runtime 선검증과 PostgreSQL authorizer가 함께 사용한다. `external_api`, exact allowlisted hostname, transformed payload bytes≤max, `internal` classification, masking=true, redaction=true가 모두 맞지 않으면 authorizer transaction 전에 `EGRESS_POLICY_DENIED`로 종료해 Provider/Profile/Model/Run/Egress/Audit write0을 유지한다.
- RED→GREEN: Runtime+Repository+Egress focused `47 passed`; authoritative Runtime 두 파일 `26 passed`; repository/service `22 passed`. policy mismatch vector는 provider kind, destination, classification, max bytes, masking, redaction 6종이다.
- actual PostgreSQL: 고유 disposable DB/role에서 fresh `0001→0020`, `21 passed, skipped0`. external mismatch 전후 `provider_profiles`, `model_artifacts`, `model_deployments`, `routing_policy_versions`, `routing_decisions`, `egress_decisions`, `runs`, `model_attempts`, `audit_events`가 모두 0임을 확인했다. external general Run의 provider/scope 저장·authoritative replay와 local HTTP replay current Binding 재검증도 actual PostgreSQL에서 통과했다. 최초 suffix의 `-` SQL identifier 오류와 두 번째 WSL path quoting 오류는 각각 제품 밖 runner 입력 오류였으며 매번 trap `db=0 role=0`; underscore/path 교정 후 성공했고 최종 cleanup도 `db=0 role=0`이다.
- 전체 회귀: API `488 passed, 41 skipped, 137 subtests passed`; Web/Common Node `25/25`; OpenAPI `paths=75 operations=94 schemas=120 errors=31`; Web production build+TypeScript와 product boundary `391 files, violations0, boundaryErrors0`; changed Web/Common lint `4 files` PASS.
- 외부/보호: Provider actual 호출0, 운영 Policy/DB write0, commit/push/deploy0. 기존 Mobile/model-connections 삭제, R1-M5/Windows Recovery/기타 dirty·untracked는 restore/delete/stage하지 않았다.

## 2026-08-21 독립 검토 REWORK2 — immutable Run 최초 소유 경계

- RED: actual PostgreSQL full `QuestionAnsweringService → PostgresQuestionEgressAuthorizer → stub transport → persist_completed → HTTP replay`에서 최초 요청 200 뒤 동일 replay가 404였다. authorizer가 `runs`를 먼저 만들었으나 `conversation_id`, `request_fingerprint`, `provider_kind`, `egress_scope`가 없고, 완료 저장의 `ON CONFLICT DO NOTHING`이 이를 보완하지 못한 것이 원인이다. disposable DB/role cleanup은 `0/0`이다.
- 선택 대안: FK 때문에 egress/routing decision 이전 Run 자체는 필요하다. 따라서 authorizer가 Provider 호출 전에 결정론적 Conversation을 먼저 만들고, repository의 단일 `_run_canonical_payload` helper로 완전한 immutable Run을 최초 생성한다. `persist_completed`도 동일 helper와 payload를 재사용하므로 canonical 충돌을 우회하거나 행을 update하지 않는다.
- GREEN: actual PostgreSQL fresh migration `0001→0020`, `22/22`, skipped0. 실제 외부 full path 최초 HTTP 200, transport1, 동일 replay200, Provider 재호출0이며 저장 Run의 conversation FK·request fingerprint·external provider kind·exact egress scope를 확인했다. external policy mismatch 9-table write0과 local replay binding 재검증도 유지했고 cleanup db0/role0이다.
- 회귀: focused API `52/52`, 전체 API `488 passed, 42 skipped, 137 subtests`, Node `25/25`, OpenAPI `75/94/120/31`, Ruff 변경 4파일 PASS. 공개 DTO/route·질문별 Step-up0 계약은 변하지 않았다.

## 2026-08-21 독립 검토 REWORK3 — 동시 최초 요청 Provider 단일 소유

- RED: 동일 idempotency key의 외부 질문을 2개 connection/thread로 동시에 시작하면 advisory lock 뒤의 기존 allowed decision도 Provider 실행 경로로 진행하여 transport2가 되고, 중복 transition 중 하나가 `QUESTION_DATABASE_UNAVAILABLE`로 실패했다.
- 선택 설계: 기존 PostgreSQL advisory transaction lock과 durable `egress_decisions` row를 소유권 정본으로 사용한다. 신규 decision creator만 internal `provider_owner=true`, 기존 decision 조회자는 `false`이며 공개 DTO에는 노출하지 않는다. follower는 bounded `load_completed` replay만 수행한다.
- 장애 계약: owner가 완료하지 못하면 같은 idempotency key의 follower는 Provider 소유권을 탈취하지 않고 기존 retryable internal 409로 fail-close한다. 새 idempotency key/new Run만 재시도할 수 있다. TTL 자동 탈취나 same-key 자동 recovery는 구현·주장하지 않는다.
- 첫 GREEN 실행은 transport1, Run1, Result1, Egress1을 만족했으나 Audit 기대를 2로 잘못 둬 실제 canonical transition 포함7과 불일치했다. action별 정본으로 `canon.transition=5`, `question.egress.authorize=1`, `question.answer=1`을 exact 고정했다.
- actual PostgreSQL fresh `0001→0020`, 4/4 non-skip PASS: 동시 transport1, Run1, Result1, Egress1, exact Audit7, 기존 external HTTP replay/provider0, general/grounded/local replay 회귀를 함께 확인했다. cleanup `db=0 role=0`이다.
- runner 첫 실행은 과거 이름의 테스트 selector가 collection 실패했으며 제품 실행0, trap cleanup `db=0 role=0`이었다. 현재 정본 테스트명으로 selector를 교정한 다음 실행은 PASS다. 외부 Provider·운영 write·배포는 0이다.
- fresh 회귀: focused Question API `38/38`, 전체 API `489 passed/42 skipped/137 subtests`, Node `27/27`, OpenAPI `75/94/120/31`, Ruff 변경 4파일 PASS다.
- 회귀 오류 복구: 첫 focused 명령은 삭제된 과거 테스트 파일명, 첫 local 전체 명령은 isolated 환경의 `cryptography` 누락으로 collection 전에 종료됐다. 현재 존재 파일과 명시 dependency로 교정한 fresh 실행이 위 GREEN이며 제품 결함이나 write는 없었다.

## 2026-08-21 독립 검토 REWORK4 — in-flight fingerprint exact

- RED actual PostgreSQL: 같은 run ID·question·Provider wire·frozen scope에 request fingerprint만 다른 두 요청을 동시에 실행하면 follower가 owner의 저장 결과를 그대로 반환했다. Provider는1이었지만 mismatch가 409가 되지 않았다. cleanup db0/role0이다.
- GREEN: authorizer advisory lock의 existing branch는 저장된 complete Run canonical 전체와 현재 payload를 비교한다. question/context mode/Source IDs/request fingerprint/frozen 중 하나라도 다르면 `IDEMPOTENCY_KEY_REUSED` 409로 transaction write0 차단한다.
- follower wait는 먼저 completed 존재만 확인해 미완료 binding의 조기 404를 피하고, 완료 감지 뒤에는 `load_completed_for_replay(context,run_id,request_fingerprint)` authoritative 검증 결과만 반환한다.
- actual PostgreSQL fresh `0001→0020`, 4/4: mixed fingerprint owner transport1, mismatch follower 409/result0/additional write0; Run1/Result1/Egress1/Audit7. same fingerprint replay, owner timeout409/new key recovery, 기존 external/local 회귀도 유지했고 cleanup db0/role0이다.
- fresh 회귀: focused API `39/39`, 전체 API `490 passed/42 skipped/137 subtests`, Node `27/27`, OpenAPI `75/94/120/31`, Ruff PASS. 외부 Provider·운영 write·배포0이다.
