# R1-M4-04-C01 완료보고

## 판정

`COMPLETED` — Tenant 역할과 Workspace Membership 역할의 저장·평가·변경 범위를 분리했고 승인된 C01 검증을 모두 통과했다.

## 판단 이유

- `personal_owner`·`organization_admin`은 별도 Tenant 역할 Binding과 optimistic version을 사용하며 Workspace Membership으로 부여할 수 없다.
- `workspace_admin`·`editor`·`reviewer`·`approver`·`viewer`만 현재 Workspace Membership에 저장한다.
- 개인/조직 공간 kind와 Tenant 역할이 맞지 않으면 bootstrap·변경을 fail-close한다.
- 조직 관리자는 별도 Membership 없이 같은 조직 Tenant의 두 Workspace에서 동일한 Tenant 범위 권한을 가진다.
- Workspace 관리자는 자신의 Workspace만 관리하며 Tenant 정책과 다른 Workspace 변경은 거부된다.
- Tenant 역할 철회 뒤 과거 결과는 현재 권한으로 다시 평가되어 차단되며, AccessDecision과 rerun Snapshot은 실제 역할 Binding의 `role_scope`와 Version을 기록한다.
- 기존 Cross-tenant 비노출, Step-up Binding, 감사 원자성과 기존 Authorization 15개·Identity 18개·Audit 13개 테스트를 유지했다.

## 조치

- Authorization Schema를 v2로 올리고 `auth_tenant_roles`와 `RoleScope` 계약을 추가했다.
- Authorization 공개 Export, OpenAPI 정본·검증기·결정적 Evidence, Architecture와 API README를 같은 계약으로 정합화했다.
- C01 회귀 테스트 7개를 추가하고 기존 테스트의 Tenant 역할 설정만 분리된 Repository 계약으로 교정했다.
- HTTP Runtime·UI·PostgreSQL/RLS·M5 Migration·외부 배포는 작업지시 제외 범위이므로 구현 또는 완료로 주장하지 않는다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| Authorization write→no-write | 22/22 PASS, 역할 7·권한 8, SHA `047E0B4F...FEE` |
| Identity no-write | 18/18 PASS, SHA `C588F9DE...CCE6` |
| Audit no-write | 13/13 PASS, SHA `F859FE66...E041` |
| OpenAPI write→no-write | 44 Path·67 Operation·53 Schema, SHA `FA26093B...E932` |
| OpenAPI Node | 8/8 PASS |
| Python Compile·Public Export | PASS; Scope 2·Tenant 역할 2·Workspace 역할 5 |
| Workspace | 34/34 PASS |
| Independence | 8 Component·10 Edge·148 File·0 Violation |
| Toolchain | 7 npm Manifest·Exact Pin·Lockfile PASS |
| 관련 Quality capability | `api-authorization` 직접 PASS |
| JSON·Node Syntax·Diff Check | PASS |

## 제한과 인계

- 직전 R1-M4-04 전체 Quality Gate는 PASS 기준선이며 C01에서는 관련 API Authorization capability를 직접 재검증했다.
- PR·CI·Merge와 외부 독립 검증은 어울1 소유다.
