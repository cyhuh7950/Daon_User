# Versioned Egress Policy Binding 상세 설계

## 문서 정보

| 항목 | 값 |
| --- | --- |
| 설계 ID | `R1-M8-09-EGRESS-POLICY-C01` |
| 버전 | 1.0 |
| 상태 | 승인 |
| 승인 | 신산님 · 2026-08-13 · `APR-R1-M8-09-EGRESS-POLICY-C01-20260813-01` |
| 상위 정본 | `docs/superpowers/specs/2026-07-20-daon-user-program-design.md` 1.0 |

## 목표와 불변 조건

외부 Provider 호출 전에 서버가 참조할 Organization/Workspace Egress 정책 정본을 만들고, 정책과 Run별 평가 결과를 분리한다. 기존 same-origin, 인증, RLS, Source/Run/Citation, Studio Version, 승인과 Audit 의미는 유지한다.

## 데이터 계약

Migration `0012_egress_policy_version_binding.py`는 기존 migration 관례를 따라 다음 Canon을 추가한다.

- `egress_policy_versions`: Tenant, 소유 scope(organization/workspace), 단조 증가 version, 상태, canonical JSON/digest, 생성 actor/time. canonical payload는 mode(`deny_external` 또는 `allow_approved_external`), 허용 provider kind/destination, classification, max bytes, masking/redaction, required approver를 포함한다. 생성 후 immutable이다.
- `egress_policy_bindings`: Tenant, Organization, 선택 Workspace, policy version FK, active/current, binding version, actor/time. 동일 scope의 current Binding은 하나만 허용한다.
- FK·unique·index·RLS·immutable trigger를 기존 Canon 방식으로 적용한다. 잘못된 Tenant/scope 조합, 비현재/비활성 Version, 교차 Workspace는 거부한다.
- 기존 Workspace마다 deterministic하고 멱등인 `deny_external` policy/version/binding을 생성한다. 기존 RunSnapshot·EgressDecision은 변경하지 않는다.

Effective policy는 Organization과 Workspace Binding을 모두 조회한다. 하나라도 deny면 deny이며 Workspace는 Organization deny를 완화하지 못한다. 필요한 Binding이 없거나 stale하면 fail-close한다.

## 실행 계약

1. 현재 actor·membership·Workspace·SourceVersion·provider/deployment와 effective policy를 조회한다.
2. Frozen RoutingContext/RunSnapshot에 policy/binding Version과 전송 fingerprint를 기록한다.
3. 외부 호출 전에 `route_single_model()`을 평가하고 append-only EgressDecision/RoutingDecision을 transaction으로 저장한다.
4. transaction commit 후에만 Provider를 호출한다.
5. 완료·재시도는 같은 policy/binding/fingerprint를 재검증하고 ModelAttempt/RunResult를 같은 Decision에 결속한다. 다르면 새 Run이다.

## 공개 API와 UI

- `GET /api/v1/workspaces/{workspace_id}/egress-policy`: effective projection과 잠금 사유, ETag.
- `POST /api/v1/organizations/{organization_id}/egress-policy-versions`: Organization 새 불변 Version 생성·활성화.
- `POST /api/v1/workspaces/{workspace_id}/egress-policy-versions`: Workspace 새 불변 Version 생성·활성화. Organization deny 완화 요청은 거부한다.
- 쓰기는 `If-Match`, Idempotency-Key, 현재 관리자 권한, CSRF, `organization_security_or_connector_policy_change` Step-up, Audit를 요구한다.
- Web 조직 설정은 same-origin BFF로만 위 API를 사용한다. effective mode, 상위 잠금, provider/destination, byte/masking/approver를 화면으로 관리하며 상시 설명 박스 대신 Tooltip을 쓴다.

## 오류·보안

`allow_approved_external` Question은 optional `step_up_authorization_id`를 입력받되 외부 전송에서만 필수다. 서버는 현재 Membership Role이 effective `required_approver` 임계 이상인지 확인하고, exact actor·`external_transfer`·`run_id`·effective policy fingerprint에 결속된 단기 Step-upAuthorization을 전송 직전에 1회 consume한다. internal/deny 요청은 기존 DTO 의미를 유지한다. Routing은 ProviderProfile Canon의 `external_api|server_internal|local_runtime` 값을 그대로 사용해 `route_single_model()`을 실제 호출하고 Decision에 결과를 고정한다.

Question authorization preflight는 `POST /api/v1/workspaces/{workspace_id}/questions/authorization`이다. source/version/question/current password와 실제 Question POST가 재사용할 Idempotency-Key로 서버가 principal, current Provider/Policy, deterministic Run과 prepared wire payload fingerprint를 계산한다. local credential 재인증 뒤 opaque authorization ID·expiry·run/request fingerprint만 응답하며 password·Cookie·원문은 저장·Audit·응답하지 않는다. Question POST는 같은 key/payload/authorization을 재계산해 exact match 후 1회 consume한다. mismatch·expiry·reuse·wrong actor/workspace는 transport와 Result 0으로 닫는다.

- 누락/비활성/stale/scope mismatch: `EGRESS_POLICY_UNAVAILABLE` 또는 `EGRESS_POLICY_STALE`, 외부 호출·Run 결과 쓰기 0건.
- 상위 deny 완화: `EGRESS_POLICY_DENIED`.
- Step-up·권한·ETag 실패: 기존 Safe Error 계약을 유지하며 정책 쓰기 0건.
- Secret, 내부 URL, 원문 민감 payload를 API·UI·로그·증거에 남기지 않는다.

## 검증

Unit/contract뿐 아니라 실제 PostgreSQL migration/backfill/RLS/FK/rollback, 외부 transport 호출 전 Decision commit, same-origin Browser Network, 조직 설정 실제 클릭, deny 정책에서 외부 호출 0건을 검증한다. 배포·commit·push는 이번 작업 범위가 아니다.
