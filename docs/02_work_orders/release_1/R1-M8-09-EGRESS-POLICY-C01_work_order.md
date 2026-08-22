# R1-M8-09-EGRESS-POLICY-C01 작업지시서

## 작업 계약

| 항목 | 값 |
| --- | --- |
| Work Order | `R1-M8-09-EGRESS-POLICY-C01` |
| issue_id | `R1-M8-09-EGRESS-POLICY-C01-I001` |
| 목표 | Versioned Organization/Workspace Egress 정책 정본, 정책 관리 API/UI, Run별 EgressDecision 결속을 실제 PostgreSQL과 Browser까지 구현한다. |
| 승인 기록 | `APR-R1-M8-09-EGRESS-POLICY-C01-20260813-01` |
| 설계 | `docs/superpowers/specs/2026-08-13-egress-policy-version-binding-design.md` 1.0 |
| 계획 | `docs/superpowers/plans/2026-08-13-egress-policy-version-binding.md` |
| 진행 기록 | `docs/04_test_reports/release_1/R1-M8-09-EGRESS-POLICY-C01_progress.md` |
| 결과보고 | `docs/04_test_reports/release_1/R1-M8-09-EGRESS-POLICY-C01_completion_report.md` |
| Writer | 어울2 `daon-developer` 단일 Writer |

## 착수·보존

`AGENTS.md`, 승인 상위 설계·Release 계획, 결정 기록, 수정 설계·계획, 제안, 본 지시, Prompt, 기존 R1-M8-09 Progress/Completion을 EOF까지 읽고 Hash를 Progress에 기록한다. 공식 root/branch/origin/HEAD/dirty/staged0를 확인한다. 현재 branch와 보호 dirty를 그대로 보존하고 checkout/stash/reset/stage하지 않는다. `D:\Project\Daon_User`는 읽거나 수정하지 않는다.

## 범위

- 승인된 Migration `0012`, Canon policy version/binding, deny backfill, RLS/FK/immutable/audit/idempotency/rollback.
- effective policy resolver와 Question/Studio Run의 Frozen RoutingContext/EgressDecision/RoutingDecision 결속.
- 외부 Question은 approver Role 임계와 exact Run/effective-policy 결속 `external_transfer` Step-up을 전송 전에 consume하고 Canon provider kind 기반 `route_single_model()` 결과를 고정한다. no-evidence Run도 외부 transport 없이 policy/decision lineage를 보존한다.
- same-origin Question authorization preflight는 동일 Idempotency-Key, prepared wire payload, current policy/provider, deterministic Run을 결속하고 local password를 저장·로그·응답하지 않는다. Question POST는 opaque one-time authorization만 받아 exact match 후 consume한다.
- 정책 조회·변경 Runtime/OpenAPI/BFF와 조직 설정 Product UI.
- 기존 `organization_security_or_connector_policy_change` Step-up, current-password reauth, ETag/If-Match, CSRF, 현재 권한.
- Progress/Completion/Evidence와 관련 테스트.

## 불변·제외

- `egress_decisions`를 정책 template으로 재사용하지 않는다. Organization deny를 Workspace가 완화하지 않는다.
- 기존 로그인 분리, Source 처리, 질문/Citation, 5종 Studio, 승인/전달/등록, Provider 설정, same-origin 의미를 보존한다.
- 외부 API URL·localhost·Docker 주소를 Browser 코드에 넣지 않는다.
- 테스트 fixture를 제품 성공으로 표시하거나 Secret/Credential/원문을 기록하지 않는다.
- 관련 없는 리팩터링, 기존 보호 변경 복원·삭제, commit/push/PR/deploy 금지.

## 허용 모듈

- `services/api/migrations/versions/0012_egress_policy_version_binding.py`
- 신규/기존 Egress policy Domain·PostgreSQL repository·Question/Studio/Runtime wiring 및 해당 테스트
- `packages/contracts/openapi/v1/openapi.json`, verifier/summary/test
- `apps/web` 조직 설정 route/component/server-only adapter/BFF 및 테스트
- `packages/ui` 조직 설정 Product model/pane/style/export 및 테스트
- 본 Work Order 전용 docs/evidence

정확한 기존 파일명은 코드 구조를 확인해 최소 범위로 선택한다. 이 범위 밖 파일이 필수이면 쓰지 말고 이유·영향을 어울1에게 보고한다.

## TDD·검증

승인 계획 Task 1~6 순서로 각 기능 RED를 먼저 실행하고 예상 실패 원인을 Progress에 기록한 뒤 최소 GREEN한다. 전체 API, Web/BFF/OpenAPI, Build/TypeScript/Product Boundary, diff-check/staged0를 수행한다. disposable PostgreSQL 15에서 migration 0001→0012, backfill, RLS/FK, deny precedence, rollback/reapply와 cleanup remaining0를 검증한다. 운영 유사 Browser에서는 same-origin Network, 정책 조회·변경 Step-up, deny 시 외부 transport 0건과 Audit을 검증한다.

Browser/실제 DB Gate가 환경 때문에 미실행이면 코드·자동 계약과 분리해 `INCOMPLETE`로 보고하며 완료로 승격하지 않는다.

## 결과 계약

`status | issue_id | 수행한 작업 | 생성·변경한 결과 | 테스트 결과 | 미해결 사항 | 다음으로 필요한 판단`

Progress는 착수, 각 RED/GREEN, 오류·원인·복구, 회귀, 실제 Gate, cleanup, 종료 직전에 시각·변경 파일·명령/결과·다음 작업을 기록한다.
