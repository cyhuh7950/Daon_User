# R1-M4-04 Tenant·Workspace Authorization Core 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-04-I001`.
- Branch `codex/r1-m4-04`, 기준 HEAD `6f03712ded1cef7ac5156f0d399eb423979cdbcf`, 시작 Clean.
- 상세 설계 v0.7 §14.1~14.5·§16·§17.2·§18.4·§20.1, 구현계획 v0.9 M4-04, 테스트계획 v0.7, TS-SEC-010~016·084, M4-01~03/C01 계약을 적용한다.
- 어울2가 이 Worktree의 유일한 Writer다. PR·CI·Merge는 어울1 소유다.

## 단일 목표

현재 `IdentityPrincipal`을 기준으로 Tenant·Workspace Membership/ACL, 역할·세부 권한·정책 상속을 평가하고 과거 결과 접근마다 불변 `AccessDecision`을 생성하는 Python 3.14 표준 라이브러리 Authorization Core를 구현한다.

## 허용·제외 범위

- 허용: `authorization.py`, Package export, Identity integration 최소, Authorization tests/verifier/evidence, OpenAPI·verifier/evidence, Authorization Architecture·API README, 본 작업 문서.
- 제외: App/UI, 실제 HTTP/BFF, Local Service, PostgreSQL RLS/Migration, Source·Output·Run Service 본체, Workflow, Lockfile, 외부 의존성, Audit Core 변경.
- M4-01 OpenAPI, M4-02 Audit, M4-03/C01 Identity·Session·Step-up 계약과 기존 테스트를 보존한다.

## 역할·세부 권한 계약

1. 역할은 `personal_owner`, `organization_admin`, `workspace_admin`, `editor`, `reviewer`, `approver`, `viewer` 7종으로 고정한다.
2. 역할 Matrix는 query·analyze·generate·edit·review·revision request·approve·deliver·knowledge register·policy·member·view 의미를 명시하고 미정의 Action은 deny-by-default다.
3. 역할 상승·정책 변경은 호출자 주장 역할이 아니라 현재 Repository Membership에서 판정한다.
4. 세부 권한은 `external_llm`, `internet_search`, `local_internal_llm`, `daon_knowledge`, `file_download_share`, `production_knowledge_registration`, `data_area_move`, `final_approval_external_delivery` 8종이다.
5. 역할 기본값과 Tenant·Workspace grant/deny override를 결합한다. 조직 deny·lock은 Workspace가 완화하지 못하며 effective 결과에 requested/effective·locked_by·reason·policy_version을 남긴다.

## Tenant·Write·Step-up 계약

- Resource는 Tenant·Workspace에 귀속하고 모든 SQL은 parameterized tenant/workspace predicate를 사용한다.
- foreign tenant와 missing ID는 동일한 안전 404·고정 Message/Audit target으로 처리한다. 같은 Tenant 무권한은 403 또는 `CURRENT_ACCESS_DENIED`다.
- Membership·ACL·Policy는 version·updated_at과 expected_version optimistic concurrency를 가진다.
- Role·Permission·Policy 변경은 allow/deny Audit와 safe before/after를 남긴다. Audit append 실패는 DB Write를 rollback한다.
- 중요한 조직 정책 Write는 `organization_security_or_connector_policy_change` Step-up을 actor·현재 session/device·tenant·action·target·policy_version에 결합해 소비한 경우만 적용한다.
- 후속 Domain Write는 `authorize_action`이 요구 Step-up action을 반환하고 유효 Authorization 없이는 fail-close하는 Core 계약까지만 구현한다.

## 과거 결과·AccessDecision 계약

- 과거 결과 Descriptor는 output/run·tenant/workspace·source/evidence reference·segment dependency·decisive dependency·safe separation·원래 policy/membership snapshot을 불변 저장한다.
- read·citation·open_source·export·delivery·knowledge_registration·rerun마다 현재 Membership·ACL·SourceVersion access·조직 정책을 새로 조회하며 과거 Snapshot으로 권한을 부여하지 않는다.
- `AccessDecision`은 opaque ID, actor/action/resource, tenant/workspace, 현재 membership/ACL/policy version, evaluated_at, state, reason, allowed/masked reference·segment ID를 가진다.
- 비인가 Evidence가 없으면 `available`; 안전 분리 가능한 비인가 근거와 의존 Segment는 마스킹해 `partially_redacted`; decisive dependency 또는 안전 분리 불가는 `access_blocked/CURRENT_ACCESS_DENIED`다.
- 원본 Descriptor·Output·Evidence는 수정하지 않는다. 오류에 존재 여부·내용·다른 Tenant ID를 반사하지 않는다.
- rerun은 현재 ACL·data area·policy·cost limit Snapshot과 새 opaque run request ID만 반환하며 실제 Run은 만들지 않는다.
- Read decision Audit 실패도 성공 응답을 반환하지 않는다.

## 저장·OpenAPI 계약

- 주입 SQLite 경로의 격리 Authorization Adapter를 사용한다. FK·WAL·transaction·unique·parameterized SQL·restart/concurrency를 증명하고 평문 민감값을 저장하지 않는다.
- 실제 PostgreSQL RLS는 M5 소유이며 이번 결과로 완료를 주장하지 않는다.
- OpenAPI에 Role·Permission·EffectivePolicy·AccessDecision·access_state·`CURRENT_ACCESS_DENIED` 구체 Schema와 최소 Authorization evaluation/policy 계약을 추가한다. Runtime Route 성공은 주장하지 않는다.

## TDD·검증

- RED: 7 role matrix, 8 permission 개별 revoke, 조직 lock 완화 금지, expected_version 충돌, cross/missing 비노출, 권한 상승 거부, Step-up 정책 변경, 현재 ACL 재검사, available/partial/block, Descriptor 불변, 접근 Action별 재검사, rerun Snapshot, restart/concurrency, Audit rollback·민감값 비노출.
- 외부 Dependency·Lockfile을 변경하지 않는다.
- `verify:api-authorization` write/no-write, Identity 18/18, Audit 13/13, OpenAPI no-write, compile/export, Workspace, Independence, Toolchain, JSON/Node, 관련 Quality capability, 전체 Node와 exact-base 분리를 수행한다.
- 장시간 Gate는 긴 제한으로 추적하되 같은 실패를 근거 없이 반복하지 않는다. Secret은 값 없이 존재 Count만 보고한다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-04_progress.md`에 착수·RED·GREEN·오류/복구·각 검증·종료 직전 상태를 기록한다. 완료 후 단일 Commit을 같은 Branch에 Push하고 Local/Remote exact SHA·Clean을 보고한다.
