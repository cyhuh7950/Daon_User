# Versioned Egress Policy Binding 구현 계획

> **For 어울2:** 승인 설계와 작업지시서를 EOF까지 읽고 TDD RED→GREEN으로 순서대로 수행한다.

**Goal:** Organization/Workspace Egress 정책 정본과 Run별 EgressDecision을 분리하고 정책 관리 UI부터 실제 Provider 호출 전 검증까지 연결한다.

**Architecture:** PostgreSQL Canon에 immutable policy version과 current binding을 추가한다. Repository가 effective policy를 fail-close로 계산하고 Question/Studio Run이 Frozen Context와 Decision을 transaction으로 고정한다. Web은 same-origin BFF를 통해 조직 설정 화면에서 정책을 조회·변경한다.

**Tech Stack:** Python/FastAPI/PostgreSQL, Next/React, OpenAPI JSON, Node test, pytest.

---

### Task 1: Migration·Backfill 계약

- RED: migration shape, immutable/RLS/FK, current uniqueness, Organization deny precedence, 기존 Workspace deny backfill·idempotency·rollback 테스트.
- GREEN: `0012_egress_policy_version_binding.py` 최소 구현.
- Verify: isolated migration test와 disposable PostgreSQL 15 실제 적용/rollback/재적용.

### Task 2: Domain·Repository·Service

- RED: missing/inactive/stale/wrong tenant/workspace/version, 상위 deny 완화, idempotency fingerprint, Audit 부정 테스트.
- GREEN: policy create/activate/effective projection service와 PostgreSQL repository.
- Verify: Unit + actual PostgreSQL RLS/transaction.

### Task 3: Run/Egress 결속

- RED: 외부 transport보다 Decision commit이 선행, Frozen Context와 RoutingDecision/ModelAttempt/RunResult exact 결속, 정책 변경 시 새 Run, deny면 transport/write 0건.
- GREEN: Question과 Studio generation 경계에 공통 policy resolver/decision writer 연결.
- GREEN 보충: Question 외부 전송 전 `required_approver` Role과 Run/effective-policy 결속 `external_transfer` Step-up을 consume하고, Canon provider kind로 `route_single_model()` 결과를 Decision에 고정한다. no-evidence Run도 transport 0인 frozen policy/decision을 남긴다.
- GREEN 보충: same-origin Question authorization preflight가 동일 Idempotency-Key의 deterministic Run과 prepared wire payload/effective policy를 결속해 opaque one-time authorization을 발급하며 Question POST가 exact 재검증·consume한다.
- Verify: provider fake transport 순서 테스트와 실제 PostgreSQL rollback.

### Task 4: Runtime·OpenAPI·same-origin BFF

- RED: GET effective, Organization/Workspace POST, ETag/If-Match, idempotency, Step-up, 권한, CSRF, exact method allowlist와 Safe Error.
- GREEN: Runtime DTO/route, OpenAPI schema, BFF allowlist/headers.
- Verify: Runtime HTTP, OpenAPI verifier, BFF tests.

### Task 5: 조직 설정 Product UI

- RED: 실제 React click으로 조회, deny lock, 변경 preview, current-password Step-up 즉시 소거, 성공 refresh, stale/forbidden/error 상태 보존.
- GREEN: Product model/pane와 server-only adapter 연결. 1920×1080/12px/Tooltip 기준 적용.
- Verify: React DOM event test, Product boundary, same-origin forbidden token scan.

### Task 6: 회귀·실제 Gate·문서

- 전체 API/Web/OpenAPI 테스트, production build/typecheck, boundary, diff-check, staged0.
- disposable PostgreSQL migration/backfill/RLS/FK/transaction/cleanup.
- 운영 유사 Docker의 실제 Browser에서 조직 정책 변경과 deny 외부 호출 0건을 Network/Audit로 검증. 외부 모델 성능은 정책 계약 검증과 분리한다.
- Progress·Completion·Evidence Manifest에 정적/자동/DB/Browser 증거를 구분한다.
