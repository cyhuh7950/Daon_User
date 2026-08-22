# 실제 데이터 기반 대화·Studio 검증 Transcript

- Issue: `R1-M8-10-REAL-DATA-CONVERSATION-E2E-I001`
- Date: `2026-08-20`
- Fixture inventory: product Source `0`, fixture Source `0`, delete `0` (`already absent`)

## 실제 PostgreSQL

- 환경: WSL `local-postgres`, 고유 disposable DB/Role
- Migration: fresh `0001 → 0020`
- Tests: latest full Gate `22 passed`, skipped `0`
- 일반 대화: Source/Object storage read `0`, Citation `0`, selected Provider attempt `1`, Run·Conversation·Notebook binding `1`
- grounded 흐름: selected Notebook Source binding, Citation `1`, Studio 저장·동일 key replay, 다른 Notebook write `0`
- HTTP authoritative replay(2026-08-21 current 계약): 동일 replay도 current Notebook Binding을 재검증한다. external 저장 결과는 current `EXTERNAL_LLM`과 exact effective Policy를 추가 확인하며 provider/prepare/ask/domain write `0`; local/server 저장 결과는 VIEW+Binding만 확인한다. fingerprint mismatch write `0`, cross-notebook 결과 반환 `0`이다.
- External preflight actual PostgreSQL: Provider kind·목적지·payload bytes·`internal` 분류·masking·redaction 불일치에서 Provider/Profile/Model/Run/Egress/Audit 9개 table write `0`.
- External full service actual PostgreSQL: authorizer가 완료 저장과 동일 canonical helper로 Conversation FK·request fingerprint·Provider kind·egress scope를 가진 immutable Run을 최초 생성한다. 최초 HTTP `200`, stub transport `1`, 동일 replay `200`, Provider 재호출 `0`; 불완전 선삽입 회귀 `0`.
- Cleanup: disposable DB `0`, Role `0`

## 대표 Provider transport compatibility

- 기존 서버 설정 boolean: `UPSTAGE`, `GROQ`, `MISTRAL` configured
- 선택: `UPSTAGE`
- bounded call: `1`
- 결과: HTTP `200`, response schema `valid`, Citation `0`, secret echo `0`
- Key·응답 원문·Credential: 출력/복사/Artifact `0`
- 운영 DB write/deploy root change: `0`
- 원격 고유 temp cleanup: remaining `0`

이 검증은 기존 ysna-server Provider transport compatibility만 증명한다. 새 제품 코드는 배포하지 않았고 Credential을 로컬로 반입하지 않았으므로, 새 제품 경로의 실제 Source 업로드→처리→Provider 대화→Citation→Studio 전체 호출은 `NOT_RUN`이다.

## 계약·자동 회귀

- Latest focused API: `52 passed`, 전체 API `488 passed / 42 skipped / 137 subtests`, 실제 PostgreSQL tests `22 passed` 별도
- Latest affected Node: `25/25 passed` (기존 broader related `87/87` 근거 유지)
- Rust Native wire: `3/3 passed`
- Web production build: PASS; product UI boundary `349 files`, violations `0`
- Desktop production build: PASS
- OpenAPI: `75 paths`, `94 operations`, `120 schemas`, `31 errors`; SHA-256 `594AED28565CCDBA60F3A12565071F7EAE5239544D5632508BD612EA8D180E0A`
- Fixture production import graph: violations `0`
- same-origin BFF 일반대화 exact body: PASS
- UI intent provenance: general label PASS, grounded no-citation label `0`, stale response answer/label `0`
- Unicode intent exact vector: Python/JS/Rust 모두 fullwidth letter·`！`·`？`·U+3000 space fail-close, 정상 ASCII `!`·`?` 허용
- 최종 Minor focused: Python `1/1`, Node `1/1`, Rust `1/1` PASS. actual Provider/PostgreSQL/Windows 재실행 `0`

## 실제 화면 판정

- React actual behavior test: 빈 Notebook에서 일반대화 실행·`일반 대화 · 근거 미사용`, grounded 입력 차단 PASS
- Browser 1920×1080 새 제품 실제 Provider 수직 흐름: `NOT_RUN`
- Windows actual 새 제품 실제 Provider 수직 흐름: `NOT_RUN`
- 따라서 overall은 `PARTIAL`; 자동 계약 PASS를 actual 제품 E2E PASS로 과장하지 않는다.

## 2026-08-21 실제 제품 Gate 재개 · Policy 준비

- ysna-server 제품: API/Web healthy, API 관련 bytes는 배포 commit `689be84aeeda9655968badecc1ff2dd48ea50a95`와 일치, 재배포 `0`.
- 운영 DB read-only inventory: 대상 Workspace `1`, Notebook `1`, Source `0`; 삭제 `0`.
- Provider projection: `UPSTAGE / external_api / active / text=solar-pro4`; 이 재개 단계의 Provider call `0`, fallback `0`.
- 현재 effective Workspace policy: `deny_external / restricted / max_bytes=0 / masking=true / redaction=true / provider kinds=[] / destinations=[]`.
- 승인 목표: organization·workspace 각 `allow_approved_external / internal / max_bytes=1048576 / masking=true / redaction=true / external_api / api.upstage.ai`.
- 실제 코드 충돌: Workspace policy POST route가 DTO에 없는 필드를 읽어 500을 반환했고, Web 조직 설정은 organization save만 제공했다.
- TDD: Runtime route RED→GREEN, organization/workspace same-origin adapter와 별도 Step-up UI RED→GREEN. Egress Node/BFF/React `6/6`, API Egress `10/10`, Web build·TS·12 routes·boundary `391/0`, lint `3 files`, OpenAPI exact PASS.
- Security: 브라우저 API 절대주소·localhost `0`; password는 활성 단계의 단일 uncontrolled ref와 함수 지역에만 존재하고 단계 전환 및 요청 `finally`에서 clear; 로그/Artifact 원문 `0`.
- 정책 write·Provider call·Source upload·배포 `0`. Git 기준선 없는 직접 서버 복사는 하지 않았으며, 어울1의 exact stage·commit·push·배포 후 사용자가 정식 UI에서 현재 비밀번호를 입력하기 전까지 actual Gate는 `BLOCKED / POLICY_DEPLOY_AND_STEP_UP_PENDING`이다.
- 과거 `R1-M8-09-EGRESS-POLICY-C01` Evidence hash test 1건은 현재 HEAD의 `question_egress.py`와 과거 별도 Issue manifest가 불일치하는 기존 정합성 문제로 분리했다. 이번 변경 파일이 아니며 과거 Evidence를 수정하지 않았다.

## 독립 리뷰 재검증

- Context/scope epoch actual React: reverse delayed load, saving navigation lock, aborted old save write0, 새 password 보존, 조직↔Workspace 전환 `4/4 PASS`.
- Egress React/API/BFF 전체 `9/9 PASS`; Egress API service/runtime/PostgreSQL contract/migration `10/10 PASS`.
- Step-up: organization/workspace exact action_group·target_id·operation·idempotency PASS; ACL deny consume0; wrong-target failure policy write0.
- Web build·TypeScript·12 routes·boundary `391 files / violations0`; lint 3 files; OpenAPI exact PASS.
- 실제 policy/Provider/DB write·서버 배포는 `0`; actual 판정은 계속 `POLICY_DEPLOY_AND_STEP_UP_PENDING`이다.

### 재작업 2/2 안전 경계

- Context 변경 직후 old effective/draft/form/nav/password/submit/policy text `0`; 새 load 성공 후에만 복구.
- Step-up deferred abort: policy endpoint call `0`, sensitive password/token clear. 이미 송신된 policy POST는 old snapshot write가 완료될 수 있으며 이 경우 보증은 stale UI projection `0`이다.
- Egress Node focused `11/11 PASS`; 공개 API/data/security 변경 `0`.

### 재작업 3 first-commit 경계

- Prop organization/workspace identity를 keyed wrapper→stateful inner에 동기 결속해 passive effect 전 old reducer/form/password/nav/text 재사용 `0`.
- Empty props의 session-resolved context 경로 유지. Session GET AbortSignal 전달 PASS.
- Egress Node focused `12/12 PASS`; 공개 계약·외부 write `0`.
- Fresh 종료 회귀: API `10/10`, lint `4 files`, OpenAPI exact, Web build·TypeScript·12 routes·boundary `391/0`, manifest `15/mismatch0`, secret·internal URL0, staged0.

### I004 REWORK2 immutable Run Gate

- RED: 외부 full service 최초 성공 뒤 replay `404`; authorizer 선삽입 Run의 Conversation/replay canonical metadata 유실을 actual PostgreSQL에서 재현했다.
- GREEN: fresh PostgreSQL `22/22`, focused API `52/52`, full API `488 passed / 42 skipped / 137 subtests`, Node `25/25`, OpenAPI exact, Ruff PASS.
- 운영 Provider 호출·정책 write·배포는 `0`; 이 Gate의 transport는 test-only stub이며 기존 제품 E2E `NOT_RUN` 판정은 유지한다.

### I004 REWORK3 concurrent owner Gate

- RED: same-key 동시 최초 요청이 Provider transport `2`와 중복 transition 실패를 만들었다.
- GREEN actual PostgreSQL 2-connection: Provider transport `1`, Run `1`, Result `1`, Egress decision `1`, Audit `canon.transition=5`, `question.egress.authorize=1`, `question.answer=1`.
- follower는 owner 완료를 bounded poll하여 같은 결과를 반환한다. owner 미완료 timeout은 same-key Provider 재호출 `0`의 retryable internal 409이며, 새 idempotency key만 새 Run으로 복구한다.
- Fresh selected Gate `4/4`, cleanup database `0`, role `0`. 실제 운영 Provider 호출·정책 write·배포 `0`.
- Fresh regression: focused API `38/38`, full API `489 passed / 42 skipped / 137 subtests`, Node `27/27`, OpenAPI exact, Ruff PASS.

### I004 REWORK4 in-flight fingerprint Gate

- RED: same run/wire/frozen의 다른 request fingerprint follower가 owner 결과를 반환했다.
- GREEN actual PostgreSQL: owner Provider transport `1`; mismatch follower `IDEMPOTENCY_KEY_REUSED` 409, result `0`, additional domain/audit write `0`; final Run/Result/Egress `1/1/1`, Audit `7`.
- 완료 follower는 completed 확인 뒤 fingerprint-aware authoritative replay를 통과해야만 반환한다. owner timeout same-key 409/provider0과 새 key recovery 계약은 유지한다.
- Fresh selected PG `4/4`, focused API `39/39`, full API `490 passed / 42 skipped / 137 subtests`, Node `27/27`, OpenAPI/Ruff PASS, cleanup db0/role0. 운영 Provider·배포0.
