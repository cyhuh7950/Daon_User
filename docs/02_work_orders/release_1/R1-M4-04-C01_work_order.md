# R1-M4-04-C01 Tenant·Workspace 역할 범위 중대 보완 작업지시서

## 승인 기준과 Writer

- Issue ID: `R1-M4-04-C01`.
- Branch `codex/r1-m4-04`, 기준 HEAD `dad05d061e8b69f2426c8f1777341ef702863210`, 시작 Clean.
- 상세 설계 v0.7 §14.1, 구현계획 v0.9 M4-04, 테스트계획 v0.7, R1-M4-04 정본과 어울1 독립 보안검토 결론을 적용한다.
- 어울2가 이 Worktree와 작업 범위의 유일한 Writer다. PR·CI·Merge는 어울1 소유다.

## 판정과 단일 목표

- 판정: `MAJOR_GAP / CORRECTION_REQUIRED`.
- 이유: 현재 `personal_owner`와 `organization_admin`가 `auth_memberships(tenant_id, workspace_id, ...)`에 저장되어 테넌트 역할이 특정 Workspace에 종속된다. 그 결과 Workspace 한 곳의 역할 배정으로 Tenant 정책 변경 권한을 얻거나, 같은 Tenant의 다른 Workspace에서는 조직 관리자 권한이 사라진다.
- 목표: 개인 소유자·조직 관리자의 Tenant/개인 공간 범위와 Workspace 역할 범위를 저장·평가·변경 단계에서 분리하고, 테넌트 정책 및 모든 Workspace 권한이 역할 책임 범위와 일치함을 증명한다.

## 허용·제외 범위

- 허용: `authorization.py`, Authorization tests/export, Authorization OpenAPI/verifier/evidence, Architecture·API README, R1-M4-04/C01 작업·진행·완료보고.
- 제외: Identity·Audit Core 동작 변경, UI, 실제 HTTP Runtime, PostgreSQL/RLS, M5 Migration, 외부 의존성·Lockfile, 역할 7종·세부 권한 8종의 공개 명칭 변경.
- M4-01~03 계약, Tenant 정보 비노출, optimistic concurrency, Step-up binding, 과거 결과 현재 권한 재평가를 보존한다.

## 역할 범위 계약

1. `personal_owner`는 개인 공간 전체를 관리하는 Tenant/개인 공간 역할이며 `workspace_kind=personal`에만 유효하다.
2. `organization_admin`은 조직 Tenant 역할이며 특정 Workspace 멤버십에 종속되지 않고 같은 Tenant의 모든 조직 Workspace에서 조직 관리자 권한을 가진다.
3. `workspace_admin`, `editor`, `reviewer`, `approver`, `viewer`만 Workspace Membership 역할로 배정한다.
4. Workspace Membership 변경 API로 `personal_owner` 또는 `organization_admin`를 부여할 수 없다. Tenant 역할 변경이 필요하면 Workspace Membership과 분리된 명시적 Repository 계약과 optimistic concurrency를 사용한다.
5. 개인 공간과 조직 공간의 kind/owner-role 불일치는 bootstrap 및 변경 시 fail-close한다. 임의 문자열 kind나 역할 조합을 허용하지 않는다.
6. 권한 평가 시 Tenant 역할이 있으면 해당 범위의 우선 역할로 해석하고, 없으면 현재 Workspace Membership을 사용한다. 다른 Tenant 역할·멤버십은 절대 참조하지 않는다.

## 정책·관리 권한 계약

- Tenant 정책 변경은 현재 Repository의 Tenant 역할(`personal_owner` 또는 `organization_admin`)로만 허용한다. 특정 Workspace의 `workspace_admin` 또는 조작된 Workspace role claim으로는 허용하지 않는다.
- Workspace 정책·멤버·Source ACL 변경은 해당 Tenant 역할 또는 현재 Workspace의 `workspace_admin`만 허용한다.
- 조직 관리자는 같은 Tenant의 두 개 이상 Workspace에서 별도 Workspace Membership 없이도 조직 관리자 책임 범위로 동작한다.
- Workspace 관리자는 자신의 Workspace만 관리하고 Tenant 정책 및 다른 Workspace를 변경하지 못한다.
- 모든 allow/deny와 역할·정책 변경은 기존 Audit 원자성·안전 before/after 계약을 유지한다.

## 과거 결과·Snapshot 계약

- `AccessDecision.membership_version` 및 rerun Snapshot은 실제 권한 근거가 Tenant 역할이면 그 Tenant 역할 binding version, Workspace 역할이면 Workspace Membership version을 사용한다.
- 역할 근거가 바뀌거나 철회된 뒤 과거 결과를 조회·내보내기·전달·지식 등록·재실행하면 현재 역할·정책·Source ACL을 다시 평가한다.
- 원래 Descriptor와 과거 Snapshot으로 권한을 복원하지 않는다.

## TDD·검증

- RED 신규 테스트: 개인/조직 kind와 owner-role 조합 검증, Workspace Membership을 통한 Tenant 역할 부여 거부, 조직 관리자 2개 Workspace 일관 권한, Workspace 관리자 Tenant 정책·다른 Workspace 거부, Tenant 역할 철회 후 과거 결과 차단, Tenant/Workspace role version Snapshot, cross-tenant 비노출.
- 기존 Authorization 15개, Identity 18개, Audit 13개 테스트 기대를 약화하거나 삭제하지 않는다.
- Authorization/Identity/Audit/OpenAPI verifier를 no-write로 실행하고 Python compile/export, Workspace, Independence, Toolchain, 관련 Quality capability를 실행한다.
- Windows CRLF로 기존 Identity evidence가 달라지지 않도록 기존 no-write verifier 정규화 계약을 보존한다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-04-C01_progress.md`에 착수, RED, GREEN, 오류·복구, 검증, 종료 직전 상태를 기록한다. 완료보고 후 단일 보완 Commit을 같은 Branch에 Push하고 Local/Remote SHA·Clean을 보고한다.
