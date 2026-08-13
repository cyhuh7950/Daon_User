# R1-M8-09-EGRESS-POLICY-C01 수정 제안

## 상태

- 제안 버전: 1.0
- 상태: 승인
- 관련 Issue: `R1-M8-09-I001`
- 승인 기록: `APR-R1-M8-09-EGRESS-POLICY-C01-20260813-01` · 신산님 · 2026-08-13
- 실행 작업지시·Prompt: `R1-M8-09-EGRESS-POLICY-C01_work_order.md` · `R1-M8-09-EGRESS-POLICY-C01_prompt.md`

## 판정

`DATA_CONTRACT_BLOCKED`

현재 Canon에는 Run별 결과인 `EgressDecision`은 있으나, 이를 생성하기 전에 참조할 Workspace·Organization 범위의 불변 Egress 정책 정본과 Version Binding이 없다. 따라서 Question Run 완료 경계가 `routing.route_single_model()`의 `external_egress_allowed`를 서버 정본으로 구성하고 재검증할 수 없다.

## 판단 이유

- 상세설계 §20.2와 R1-M6-02는 외부 Provider 호출 전에 목적지·전송 범위·분류·Byte·Masking·정책·승인 주체를 포함한 `EgressDecision` 생성을 요구한다.
- `routing.py`는 이미 결정된 `RoutingContext.external_egress_allowed`를 소비하지만 그 값을 제공하는 production persistence/service가 없다.
- `workspace_policies`에는 현재 Data Area·Authority가 있으나 Egress 정책 Binding이 없고, Provider 설정은 Provider Kind·Model·Role만 보유한다.
- `egress_decisions`는 특정 Run의 평가 결과이므로 최신 행을 다음 Run의 정책 정본으로 재사용하면 결과와 정책의 역할이 뒤섞이고 재현성·감사성이 깨진다.
- 실제 격리 PostgreSQL 검증에서 이 누락을 fail-close하면 `QUESTION_POLICY_UNAVAILABLE`이 발생했고 Run·Result·Citation·EgressDecision은 생성되지 않았다.

## 대안 비교

| 대안 | 내용 | 장점 | 위험·한계 | 판정 |
|---|---|---|---|---|
| A. Workspace/Organization 정책 Version에 불변 Egress Policy Binding | Organization 기본 정책과 Workspace override를 Versioned Canon으로 저장하고, Run 시작 시 effective binding을 `RunSnapshot/FrozenContext`에 고정한 뒤 평가 결과를 `EgressDecision`으로 append | 정책과 결과 분리, 재현·감사·deny precedence·TOCTOU 방지 가능 | Migration, 정책 관리 API/UI, backfill·rollback 설계 필요 | 권장 |
| B. WorkspacePolicy JSON에 선택 필드 추가 | 기존 WorkspacePolicy canonical JSON에 Egress 필드를 직접 추가 | 구현량이 작음 | Organization 상속·override·정책 독립 Version·참조 무결성이 약하고 기존 행 backfill 의미가 불명확 | 비권장 |
| C. 최신 EgressDecision을 다음 Run 정책으로 사용 | 최근 결과를 template처럼 조회 | Migration 없음 | 결과를 정책으로 승격, Run 결속 위반, 순환 의존·감사 왜곡 | 금지 |
| D. Provider Kind 또는 Data Area만으로 추론 | Internal 허용, External 차단 등을 코드에 고정 | 단순 | 승인 주체·Masking·Workspace/Organization 정책을 잃고 공개 요구사항을 축소 | 금지 |

## 권장 조치

1. `EgressPolicyVersion`과 Workspace/Organization Binding의 Canon 계약을 승인한다.
2. Effective policy 계산은 Organization deny가 Workspace allow보다 우선하도록 하며, 존재·active/current·scope·version을 모두 fail-close 검증한다.
3. Run 시작 transaction에서 Policy Version/Binding, Data Realm, Provider/Deployment, SourceVersion/Chunk/Field, 분류, 예상 Byte, Masking/Redaction, 승인 주체를 Frozen RoutingContext와 RunSnapshot에 기록한다.
4. 외부 호출 전에 `route_single_model()` 평가 결과를 append-only `EgressDecision`으로 저장하고 `RoutingDecision`과 결속한다. Provider 호출은 해당 transaction commit 뒤에만 허용한다.
5. 완료 시 같은 Frozen Context와 Decision ID를 `ModelAttempt/RunResult`에 연결한다. 재시도는 동일 Idempotency Key·Fingerprint만 replay하고 변경된 정책은 새 Run으로 처리한다.
6. RLS는 Tenant·Workspace scope를 강제하고, 정책 생성·변경·평가·deny·사용을 AuditEvent로 남긴다.

## 예상 영향

- Migration: 새 Versioned policy/binding table 또는 승인된 기존 정책 Canon 확장, FK·RLS·immutable trigger·index 필요.
- API: 정책 관리·조회 Runtime/OpenAPI 계약과 Step-up/권한 경계 결정 필요. Browser는 same-origin BFF만 사용한다.
- Backfill: 기존 Workspace는 보수적 `deny_external` 기본 Version을 명시적으로 생성해야 하며 임의 allow 추론은 금지한다.
- Rollback: 새 Binding 활성화를 이전 Version으로 되돌리는 append-only rollback을 사용한다. 이미 생성된 RunSnapshot/EgressDecision은 변경하지 않는다.
- Test: deny precedence, Organization/Workspace association, inactive/non-current/missing binding, RLS, audit, idempotency, transaction rollback, actual PostgreSQL FK, external network 선행 Decision을 검증한다.

## 승인 결과

신산님은 2026-08-13 다음을 승인했다.

1. 권장 대안 A와 신규 Migration `0012`.
2. Organization deny 우선과 Workspace가 상위 deny를 완화하지 못하는 규칙.
3. 기존 Workspace의 명시적 `deny_external` Backfill.
4. Release 1 정책 조회·변경 API/UI와 기존 `organization_security_or_connector_policy_change` Step-up Action Group 적용.
5. 별도 수정 작업지시서·실행 Prompt 발행과 중단 없는 구현 진행.
