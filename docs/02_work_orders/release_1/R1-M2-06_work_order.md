# R1-M2-06 작업지시서 — 계정·조직·정책·장치

## 1. 작업 계약

| 항목 | 내용 |
| --- | --- |
| Work Order | `R1-M2-06` |
| issue_id | `R1-M2-06-I001` |
| 작업 | 역할·세부 권한·조직 정책·장치·Step-up·현재 권한 재검증 Production-bound Prototype |
| 개발자 | 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch | `codex/r1-m2-06` |
| 기준 SHA | `cfb45a5bad2429d7fc0303ee7489f1b1789f27ad` |
| 선행 작업 | `R1-M2-01`, `R1-M2-02` 완료·Merge; M2-03~05의 Source·Run·Studio 권한 투영 계약 재사용 |
| 결과 상태 | `COMPLETED`, `FAILURE_REPORT`, `INCOMPLETE` 중 하나 |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M2-06_progress.md` |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-06_attempt-1.md` |

어울2는 착수 전에 아래 정본을 EOF까지 읽고 SHA-256을 대조한다. 요약본으로 대체하지 않는다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` | `6539F274890F3FBE7C7286853A790B6C724D9525FB1F404ED853350470206C7A` |
| `docs/02_work_orders/daon_user_program_release_1_implementation_plan.md` | `E4C4D8151A24C207BBE2C97759FCC2975B0E35E2679DF1D4AF185B4CBD0D0162` |
| `docs/04_test_reports/release_1_test_plan.md` | `C45DAE31FD408AF0D8885E006E570CC3BE36852A9F925811F8BC329C85ED9D13` |
| `docs/04_test_reports/release_1/scenarios/05_account_security.md` | `8283356104039659386C6DE4F28EF6758C4BBB7593E77FFD5C279113875DE9EF` |
| `docs/01_architecture/workspace_layout_state_adapter_contract.md` | `3E3A95C5299A2B68519A631DEC75CA03F71712B32DA1F43553CFCA434C2731C8` |
| `docs/01_architecture/studio_workflow_prototype_adapter_contract.md` | `F40C78C751E5CBF629533158A74A6231DE357A0A96BD33DA66AB40B84AF15292` |

## 2. 목적과 사용자 완료 여정

개인 사용자는 계정과 신뢰 장치를 확인하고, 조직 관리자는 멤버 역할·세부 권한·Provider/Model·RuleSet·가중치·보안 정책의 잠금과 영향을 화면에서 이해한다. 민감 작업은 작업 시작 전에 단기 추가 인증을 요구하고, 권한 축소 뒤 과거 결과는 원본을 바꾸지 않은 채 현재 권한으로 마스킹 또는 차단되는 흐름을 클릭 가능한 Prototype으로 완성한다.

최소 완료 여정은 다음과 같다.

1. `/settings/account`에서 현재 사용자·조직 Membership과 등록 장치·신뢰 상태를 확인한다.
2. `/settings/organization`에서 일곱 설계 역할과 여덟 세부 권한의 허용·거부 Matrix를 확인한다.
3. 조직 정책이 Provider·Model·RuleSet·권위 Boost/가중치·데이터 영역·외부 전송·보존·검토 조건을 잠그고 사용자가 완화할 수 없는 이유를 확인한다.
4. 권한이 없는 역할로 정책 변경을 시도해 정보 비노출 `403` Prototype 안전 판정을 확인한다.
5. 민감 작업을 추가 인증 없이 시도해 변경 0건과 `STEP_UP_REQUIRED`를 확인한다.
6. `actor + action + target + policy_version`이 일치하는 단기 `StepUpAuthorization`만 한 번 사용하고, 만료·다른 대상·재사용은 거부한다.
7. 분실 장치의 Session·Sync Key 철회를 확인하되 실제 장치·Key 조작 성공으로 위장하지 않는다.
8. 사용자 권한 축소 후 과거 OutputVersion Read·Citation·Export·Delivery·KnowledgeRegistration·Rerun을 현재 권한으로 다시 판정하여 `partially_redacted`와 `access_blocked`를 확인한다.
9. 과거 OutputVersion·RunSnapshot·EvidenceReference는 불변이고, 재실행은 현재 정책 Snapshot의 새 Run Preview만 만드는지 확인한다.
10. Local-private→Cloud-sync 이동은 대상·범위→권한/민감정보→명시 승인→전송→버전/Audit의 다섯 단계 Preview를 따르며 실제 전송은 0건임을 확인한다.

## 3. 구현 범위

### 3.1 화면·상태·Navigation 정본 재사용

- M2-01의 `account_settings`와 `organization_settings` Route·Screen ID·역할·상태 계약을 수정 재정의하지 않고 실제 Next Prototype Route `/settings/account`, `/settings/organization`에서 직접 소비한다.
- `AccountSecurityViewState` 또는 동등한 단일 정본 상태에 선택 Persona, Membership, 역할·세부 권한, Policy Version, 장치, Step-up, Audit Preview, 과거 결과 AccessDecision을 보존한다.
- Web 폭 변화와 Account↔Organization 이동 뒤 선택 역할·정책·대상 OutputVersion·Step-up 상태를 초기화하지 않는다.
- Organization 화면은 설계상 Web·Windows용이다. 1920·1200·800·500px Browser는 폭과 무관하게 모두 `client_type=web`이므로 같은 Organization 기능을 반응형으로 유지한다. Android·iOS 미지원은 화면 폭이 아니라 명시적 `client_type=android | ios` Fixture 또는 후속 Native Adapter에서 `unavailable`과 Web·Windows 이어서 작업 안내로 검증한다.
- Prototype Adapter와 후속 M3/M4 실제 Adapter 교체 경계를 문서화한다.

### 3.2 역할·세부 권한·조직 격리

- 설계서 §14.1의 `personal_owner`, `organization_admin`, `workspace_admin`, `editor`, `reviewer`, `approver`, `viewer` 일곱 `MembershipRole`을 정확히 모델링한다.
- M2-01 `NavigationPersona`는 Route 노출 가능성을 판정하는 축이고 `MembershipRole`은 Tenant·Workspace의 실제 작업 권한을 판정하는 별도 축이다. Persona만으로 MembershipRole이나 Write 권한을 추론하지 않는다.

| NavigationPersona | MembershipRole Adapter 계약 |
| --- | --- |
| `personal_user` | `tenant_kind=personal`이고 대상 개인 영역의 Owner인 때만 `personal_owner`; 조직 영역에서는 명시 Membership 없이는 역할 없음 |
| `organization_member` | 명시 Membership Grant의 `editor` 또는 `viewer`만 사용하며 기본 편집 권한 없음 |
| `workspace_admin` | 대상 Workspace의 활성 Membership이 `workspace_admin`인 경우만 동일 역할 |
| `reviewer` | 대상 Workspace의 활성 Membership이 `reviewer`인 경우만 동일 역할 |
| `approver` | 대상 Workspace의 활성 Membership이 `approver`인 경우만 동일 역할 |
| `organization_admin` | 대상 Organization의 활성 Membership이 `organization_admin`인 경우만 동일 역할 |
| `operator` | Operations Route Persona일 뿐 기본 MembershipRole은 없음. 별도 Membership Grant 없이는 조직 콘텐츠 Read/Write와 정책 Write 권한 0건 |

- 하나의 사용자가 NavigationPersona와 MembershipRole을 각각 가질 수 있지만 판정 입력·표시·Audit에는 두 값을 분리해 기록한다. Persona가 Route에 들어갈 수 있어도 현재 Capability가 없으면 Read-only·`forbidden` 상태를 표시하며 Write를 허용하지 않는다.
- `organization_member`의 Grant 누락, `operator`의 콘텐츠/정책 Write, 개인 영역 밖 `personal_user`, 대상 Tenant·Workspace가 다른 관리자 Persona를 각각 부정 Test로 고정한다.
- 여덟 독립 권한은 외부 LLM 전송, 인터넷 검색, 로컬·사내 LLM, Daon 승인 지식, 파일 다운로드·공유, 생산 지식 등록, 영역 이동, 최종 승인·외부 전달이다.
- 역할 기본값과 Membership별 Grant/Revoke를 분리한다. 하나의 권한 회수는 다른 권한을 임의로 바꾸지 않는다.
- 다른 Tenant·Workspace ID 직접 지정, 조회자→편집자, 편집자→승인자, 일반 사용자→조직 관리자 상승 시도는 `AUTHORIZATION_DENIED` 또는 `CURRENT_ACCESS_DENIED`의 안정적 Prototype 판정과 변경 0건을 만든다.
- UI 숨김은 권한 강제로 계산하지 않는다. 동일 순수 Authorization 판정기를 모든 Control·Fixture Action이 사용한다.

### 3.3 조직 정책·잠금·Provider 안전 표시

- 조직 정책은 강제 RuleSet/Version, Daon 권위 Boost 최소값, 허용 Model·Provider·Runtime Node, 외부 전송·Masking·Region, local-private 강제, 저장·Token·비용·보존 한도, 산출물 검토·승인, 공유·다운로드·전달 대상을 표시한다.
- 사용자·Workspace 관리자는 조직 정책보다 완화된 설정을 저장할 수 없다. 요청값·유효값·잠금 이유·Policy Version을 함께 표시한다.
- Provider 화면에는 불투명 Profile/Deployment ID, 허용 상태, 데이터 영역과 정책만 표시한다. Secret·Credential 값, Raw Provider 오류, 내부 Host/Port, Daon 내부 식별자는 표시·Fixture·로그·증거에 넣지 않는다.
- 강제 RuleSet Binding 변경은 조직 관리자만, 선택형 Binding은 Workspace 관리자만 허용하는 Prototype 판정을 제공한다.
- 정책 변경 Preview는 ETag/Policy Version·Actor·변경 전후·Audit Event를 만들되 실제 API·Connector·Credential Write는 0건이다.

### 3.4 장치·Session·Sync Key

- 장치는 `registered`, `trusted`, `attention_required`, `revoked`를 포함하고 장치 ID, 유형, 마지막 확인, 신뢰 사유, Session 수, Sync Key 상태의 안전한 Metadata만 표시한다.
- 분실 장치 철회는 권한 판정→Step-up→확인→Session/Sync Key 철회 Preview→Audit 순서다.
- 철회된 장치는 기존 화면 상태를 되살리거나 다시 trusted로 자동 전환하지 않는다. 재등록은 새 Device Registration Preview로 분리한다.
- 실제 Token·Session·Sync Key 값은 생성·저장·표시하지 않는다. 실제 인증·Secure Store·기기 철회는 M3/M4/M5 후속이다.

### 3.5 Step-up 추가 인증

- §14.4 최소 민감 작업 7종을 제거 불가능한 정본 목록으로 구현하고 조직 추가 항목만 허용한다.
- `StepUpAuthorization`은 불투명 ID, Actor, Action, Target, Policy Version, 발급·만료·사용 시각, `issued | used | expired | failed` 상태와 Audit를 가진다.
- 유효한 ID가 없으면 민감 Write를 시작하기 전에 `STEP_UP_REQUIRED`로 거부하고 Domain 변경·Audit 성공 Event·외부 호출을 0건으로 유지한다.
- 다른 Actor·Action·Target·Policy Version, 만료 ID, 이미 사용한 ID는 재사용하지 못한다. 장기 ApprovalRequest와 Step-up을 같은 상태로 취급하지 않는다.
- 실제 MFA/OIDC Token 발급 성공을 주장하지 않는다. 이번 범위는 순수 상태 전이와 화면 Preview이며 M4-03 실제 인증 Adapter가 교체한다.

### 3.6 과거 결과 현재 권한 재검증

- M2-05의 특정 불변 OutputVersion과 ApprovalRequest를 Fixture로 재사용하며 승인 Badge 옆에 대상 `output_version_id`를 표시해 현재 Draft의 재승인 필요 상태와 구분한다. 이는 R1-M2-05 독립 검토 S3 보완을 흡수한 것이다.
- 과거 Read·Citation·원문 열기·Export·Delivery·KnowledgeRegistration·Rerun마다 현재 Membership·Workspace ACL·SourceVersion 권한·조직 Policy Version으로 새 `AccessDecision`을 만든다.
- `available`, `partially_redacted`, `access_blocked`를 구분하고 마스킹 Reference·사유·판정 시각을 표시한다. 안전하게 분리할 수 없거나 비인가 근거에 결정적으로 의존하면 전체 내용을 차단한다.
- 원본 OutputVersion·RunSnapshot·EvidenceReference를 수정하지 않는다. 과거 Snapshot 권한은 재현 Evidence일 뿐 현재 권한으로 사용하지 않는다.
- Rerun은 과거 결과를 되살리지 않고 현재 ACL·영역·정책·비용 한도를 고정한 새 Run Preview와 `previous_run_id` 계보를 만든다.
- 이미 외부 Export된 사본은 회수 성공을 주장하지 않고 Export 시점·대상·당시 권한·후속 권한 변경을 운영 경고/Audit Preview로 표시한다.

### 3.7 데이터 영역 이동·Audit

- Local-private→Cloud-sync 이동은 다섯 단계를 건너뛸 수 없는 순수 State Machine으로 표현한다.
- 승인 전에는 `EgressDecision`, 전송, 대상 SourceVersion을 성공 상태로 만들지 않는다. 정책·민감정보 판정과 Step-up이 모두 유효해야 명시 승인 Preview로 진행한다.
- 이번 범위의 실제 Network·파일 복사·재색인·DB Write는 0건이다.
- Audit Preview는 Append-only Event 배열이며 Actor·Action·Target·Policy Version·Trace ID·판정·안전 Code·시각을 가진다. 기존 Event 수정·삭제 Action은 거부한다.

### 3.8 Prototype 정직성·보안·접근성

- 실제 OIDC/PKCE, Cookie, CSRF, RLS, DB, API 401/403, Session/Key 철회, MFA, Egress, Connector 호출을 완료로 표시하지 않는다. `prototype_fixture`와 `deferred_actual`을 분리하고 후자는 PASS 수에 포함하지 않는다.
- 실제 Backend가 없으므로 화면의 `403`은 `HTTP 403 계약 Preview · 실제 API 미실행`으로 표시한다.
- Token·Password·API Key·Credential·Cookie·개인정보 원문을 Source, Fixture, DOM, Console, Screenshot, 결과보고에 넣지 않는다. 오류에는 Stack Trace·DB/내부 Host·Provider 원문을 노출하지 않는다.
- Browser 코드는 same-origin 상대 경계만 유지하고 API 절대주소·`localhost`·Docker Host/Port·`NEXT_PUBLIC_API_BASE_URL` 직접 호출을 금지한다.
- 사용자 입력 Fixture는 허용 Enum·길이·ID 형식을 화이트리스트로 검증하고 HTML을 직접 삽입하지 않는다.
- 1920×1080·본문 12px 표준, Tooltip/Popover 설명, Keyboard·Focus·ARIA·비색상 상태를 유지한다.

## 4. Production-bound 재사용·교체 계약

- M2-01 Navigation/Screen/Token, M2-02 Layout/State, M2-03 Source 권한, M2-04 RunSnapshot, M2-05 Studio/AccessDecision 계약을 수정 재정의하지 않는다.
- 순수 역할·권한·정책·장치·Step-up·AccessDecision Model과 UI Projection을 실제 Adapter가 재사용할 수 있게 분리한다.
- `docs/01_architecture/account_security_prototype_adapter_contract.md`에 재사용 파일, Prototype Fixture, 실제 교체 Owner(M3/M4/M5/M6/M8), 금지된 임시 경로를 표로 남긴다.
- 공개 API·데이터·보안 경계를 새로 확정하지 않는다. 실제 코드와 승인 설계가 충돌하면 증거와 선택지를 어울1에게 보고하고 쓰기를 중지한다.

## 5. 허용 변경 범위

- `apps/web/app/settings/account/`, `apps/web/app/settings/organization/`의 Prototype Route
- `packages/ui/src/`의 계정·조직·보안 전용 Model·Pane과 기존 Export/Studio 연결 최소 수정
- `scripts/tests/`의 전용 Test와 기존 회귀 Test 최소 수정
- `docs/01_architecture/account_security_prototype_adapter_contract.md`
- `docs/03_evidence/release_1/R1-M2-06/`
- 지정 진행 기록과 결과보고

금지:

- 실제 API·Auth·Cookie·Token·DB·Migration·RLS·Session/Key·Connector·Provider 구현
- Dependency·Lockfile·Toolchain·CI 설정 변경
- M2-01~05 정본 계약 재작성, 무관 Refactor, 기존 Fixture 삭제
- Secret·Credential·개인정보 원문 또는 성공으로 보이는 임시 Backend Mock
- 보호 Dirty `docs/03_evidence/release_1/R1-M1-04/dependency-graph.json`, `violations.json` 수정·복원·Stage

## 6. TDD와 구현 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| S0 | 정본·Hash·선행 계약·Branch·단일 Writer·보호 Dirty 확인 | 진행 기록 |
| S1 | 역할·권한·정책·장치·Step-up·AccessDecision·영역 이동 부정 경로 Test 선작성 | 계약별 유효 RED |
| S2 | 순수 AccountSecurity Domain Model·Reducer 최소 구현 | 전용 Model Test PASS |
| S3 | Account/Organization Route·Pane·상태 연결 | Route·상태 보존 회귀 PASS |
| S4 | 역할 7·권한 8·정책 잠금·Provider 안전 표시 | Matrix·잠금 Test PASS |
| S5 | 장치 철회·Step-up 최소 7종·Audit | 종료·재사용·불변 Test PASS |
| S6 | 과거 결과 AccessDecision·새 Rerun·영역 이동 5단계 | 마스킹·차단·변경 0 Test PASS |
| S7 | 접근성·네 폭·Production Build·실제 Browser 클릭 | Console·Network·시각 증거 |
| S8 | Architecture·Manifest·Diff·결과보고 | HANDOFF_READY |
| S9 | 읽기 전용 독립 검토 | ACCEPT 또는 REWORK |
| S10 | 어울1 Commit·Push 후 GitHub·ysna-server 불변 SHA 검증 | Required Check·Artifact·ARM64 PASS |
| S11 | 어울1 최종 대조와 Merge | Merge SHA |

각 기능은 실패 Test→최소 구현→회귀 순서로 진행한다. 환경·Loader 오류는 유효 RED가 아니다.

## 7. 자동·Browser 검증

### 7.1 자동 검증

- 설계 역할 7개, Navigation Persona Adapter, 세부 권한 8개와 독립 회수
- Tenant/역할 상승·무권한 정책 변경의 변경 0과 안전 Code
- 조직 정책 요청값·유효값·잠금 이유·Policy Version, Secret/내부 주소 노출 0건
- 민감 작업 최소 7종, Step-up Actor/Action/Target/Policy 결합, 만료·오대상·재사용 거부
- 장치 철회 종료 상태와 실제 Token/Key 값 0건
- 과거 결과 `available | partially_redacted | access_blocked`, 원본 불변, 새 Rerun 현재 Snapshot
- Local-private→Cloud-sync 다섯 단계와 무승인 Egress/전송/재색인 0건
- Append-only Audit와 Trace ID, 안전 오류 필드, Chain-of-Thought·Secret·Raw 오류 노출 0건
- M2-05 승인 Badge의 대상 OutputVersion ID 명시
- 기존 Foundation·Workspace·Source·Run·Studio 전수 회귀, Lint, Build, 공통 Gate

### 7.2 실제 Production Browser

새 Production Build·새 Browser 세션에서 다음을 실제 클릭하고 1920×1080, 1200×900, 800×900, 500×900 증거를 남긴다.

- Account·Organization Route와 역할/권한 Matrix
- 조직 정책 잠금 사유와 무권한 `403` 계약 Preview
- Step-up 미충족→발급 Preview→정상 1회 사용→재사용/만료 거부
- 장치 신뢰→분실→철회 Preview와 Audit
- 권한 축소 전/후 과거 결과 available→partial→blocked, 현재 권한 새 Run Preview
- 영역 이동 5단계와 승인 전 실제 전송 0건
- ApprovalRequest Badge 대상 OutputVersion ID
- Keyboard·Focus·Tooltip/Popover·네 폭 상태 보존
- Console Warning/Error, Resource Timing 가용 여부, API-like·비동일 Origin·금지 주소를 구분해 기록

Resource Timing API가 없으면 0으로 쓰지 말고 `unavailable`과 사유를 기록한다. 실제 API가 없는데 401/403·MFA·철회 성공을 주장하지 않는다.

### 7.3 M2 Fixture와 후속 실제 검증

| 시나리오 | 이번 M2-06 | 실제 완료 책임 |
| --- | --- | --- |
| TS-SEC-005~006 | 장치 상태·철회 순수 전이와 UI Preview | M3/M4/M5 실제 등록·Session·Sync Key·Secure Store |
| TS-SEC-010~016 | 역할/권한/Tenant 부정 Fixture, 현재 AccessDecision·새 Run Preview | M4 API 403·RLS·실제 계정, M8 전체 흐름 |
| TS-SEC-020~023 | 영역 이동 5단계·승인 전 변경 0 | M4/M5/M6 실제 Egress·Copy·Version·Index |
| TS-SEC-084~084A | Step-up 최소 목록·결합·만료·재사용 순수 전이 | M4-03 실제 OIDC/MFA/API 강제 |
| TS-SEC-001~004·040~083·085 | 화면 계약·안전 오류·정적 비노출 교차 증거만 제공 | M3~M6·M8·M9 실제 Auth/API/DB/Network/보안 검증 |

## 8. 완료 조건

- §2의 열 가지 사용자·운영 여정이 클릭 가능한 Production-bound Prototype으로 연결됨
- 역할 7·권한 8·정책 잠금·장치·민감 작업 7·Step-up·AccessDecision 3상태·영역 이동 5단계 누락 0건
- 부정 경로에서 Domain 변경·외부 호출·성공 Audit 0건
- 원본 OutputVersion·RunSnapshot·EvidenceReference·종료 Step-up/장치 상태 불변
- 실제와 Prototype 주장 분리, Secret·Credential·개인정보·내부 주소 노출 0건
- 기존 M2-01~05 회귀 0, 전용·전체 Test, Lint, Production Build, 공통 Gate PASS
- 네 폭 Browser에서 Console 오류·잘림·겹침·상태 초기화 0건
- Architecture 계약, Browser JSON, Screenshot, Evidence Manifest, 진행 기록, 결과보고 완비

## 9. ysna-server 경계

S10 서버 검증은 `/home/ubuntu/deploy/daon-user/R1-M2-06/<exact-push-sha>`에 한정한다. 기존 `shared-db`, `common`, `netdata`, `proxy`를 사용하거나 변경하지 않는다. 기존 Container·Network·Volume 사전/사후 Hash 일치, detached exact SHA·Clean, ARM64 Build·Test·Gate, 임시 자원 0을 증거로 남긴다. Schema·Migration 신호가 없으면 `NOT_APPLICABLE_NO_SCHEMA`, DB 명령 0건을 기록한다.

## 10. 진행 기록·결과보고

어울2는 착수, 각 세부 단계 완료, 오류·복구, Test·Build·Browser 완료, 종료 직전에 진행 파일에 시각·단계·상태·변경 파일·명령/Exit·검사 결과·오류/원인·복구·증거·남은 위험·다음 작업을 기록한다.

결과보고 첫 줄은 다음 형식으로 고정한다.

```text
COMPLETED | R1-M2-06-I001 | 요약 | 변경 파일 | 테스트 근거 | 남은 위험 | 다음 조치
```

완료 조건이 하나라도 없으면 `COMPLETED`를 사용하지 않는다. 첫 오류만으로 `FAILURE_REPORT`를 제출하지 말고 원인·대안·현재 변경·검증 근거를 먼저 정리한다. 승인 경계 변경이 필요하면 쓰기를 중지하고 어울1에게 보고한다.

## 11. 판정 기준

- `COMPLETED`: 필수 산출물·테스트·Browser 증거·보고가 모두 있고 범위 위반이 없다.
- `FAILURE_REPORT`: 원인과 대안을 조사했지만 승인 경계 안에서 완료할 수 없음을 정식 보고한다.
- `INCOMPLETE`: 응답 종료·시간 제한 등으로 필수 산출물 또는 증거가 빠졌다.

중대 미진은 별도 수정 작업지시서로 재작업한다. 합격 가능한 경미 보완은 다음 작업지시서에 흡수하며 사소한 사유로 전체 합격 작업을 다시 열지 않는다. 검토 출력은 `판정 → 판단 이유 → 조치` 순서로 한다.
