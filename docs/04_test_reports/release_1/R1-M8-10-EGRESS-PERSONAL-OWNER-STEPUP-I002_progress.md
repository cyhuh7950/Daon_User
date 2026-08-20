# R1-M8-10-EGRESS-PERSONAL-OWNER-STEPUP-I002 진행 기록

## 2026-08-21T01:32:15+09:00 착수

- 정본: `C:/Users/cyhuh/Desktop/D Driver/Project/Daon_User`, Branch `codex/user-auth-screen-split`, origin `git@github-cyhuh7950:cyhuh7950/Daon_User.git`, HEAD `c2e2b6f0d39ef05ec562ee86940791df8a32c571`, staged0.
- 승인 근거: `R1-M4-04-C01`은 Tenant 정책 변경을 Tenant 역할 `personal_owner` 또는 `organization_admin`에 허용한다. Workspace Membership 역할은 허용하지 않는다.
- 실제 충돌: Runtime organization egress route는 Step-up consume 전에 `organization_admin_workspace()`를 호출하며, 이 메서드는 `Role.ORGANIZATION_ADMIN`과 organization workspace만 허용한다. 운영 personal tenant의 valid `personal_owner` Step-up이 unused 상태로 `FORBIDDEN`된다.
- 변경 전: organization admin만 server-selected organization workspace를 얻는다. personal owner는 승인된 Tenant 정책 권한이 있어도 거부된다.
- 변경 후 목표: personal owner는 server-selected personal workspace, organization admin은 organization workspace를 얻는다. workspace_admin/member는 기존대로 ACL 단계에서 거부되어 Step-up consume0/write0이며 Audit은 판정 역할을 safe metadata로 기록한다.
- 보호: 기존 Mobile/model-connections 삭제와 다른 dirty/untracked 미접촉. 수동 DB/role 변경, 외부 policy/provider write, commit/push/deploy0.
- 다음: Authorization·Runtime 실제 실패 테스트 RED 고정.

## 2026-08-21T01:36:27+09:00 RED → GREEN · 종료 검증

- RED: Tenant 역할 두 종류의 server-selected workspace를 요구한 direct Authorization test에서 `personal_owner`가 `ACTION_DENIED`로 실패했다. 원인은 `organization_admin_workspace()`가 `Role.ORGANIZATION_ADMIN`과 `workspace_kind='organization'`만 허용한 단일 조건이다.
- GREEN: Tenant 역할과 Workspace kind를 exact mapping(`personal_owner→personal`, `organization_admin→organization`)하고 parameterized query로 해당 Tenant의 workspace를 서버 선택한다. 허용/거부 Audit metadata에는 판정한 Tenant 역할 또는 `none`을 기록한다.
- Runtime GREEN: personal owner의 organization egress version POST는 201이며 Step-up의 action group·Tenant target·operation·idempotency가 exact다. organization admin 기존 경로도 유지한다. workspace_admin과 viewer는 ACL에서 403이며 Step-up consume0, policy write0이다.
- 테스트: 신규 direct RED 1건 확인 후 focused direct `1/1 PASS`; Authorization+Egress focused `10/10 PASS`; 관련 Authorization·Egress 전체 `35/35 PASS`; OpenAPI `75 paths / 94 operations / 120 schemas / 31 errors` exact PASS; Python compile PASS.
- 오류·복구: JS/TS 전용 `lint-workspace.mjs`에 Python 파일을 전달해 unsupported extension 및 test fixture `base_url=http://test` false-positive가 발생했다. 제품 결함이 아니며 Python `compileall`과 관련 pytest로 올바른 언어 경계를 검증했다. JS/TS 제품 변경은 0이다.
- 변경 파일: `authorization.py`, C01 Authorization test, Egress Runtime HTTP test, 본 Progress. 공개 API/data/security 계약, DB role/data, 외부 policy/provider 상태 변경0.
- 종료 상태: diff-check와 staged0를 확인한 뒤 어울1에게 인계. commit/push/deploy0.

## 2026-08-21 REWORK1 · ambiguous server-selected workspace

- 독립 검토 finding: 현재 query는 matching Workspace가 여러 개여도 `ORDER BY ... LIMIT 1`로 임의 첫 항목을 선택한다. Tenant-scoped write의 server-selected context가 모호해지는 보안 결함이다.
- RED 목표: missing·multiple·cross-tenant는 동일 safe denial이며 direct Audit metadata는 비식별 role/reason/count만 포함한다. Runtime personal owner와 organization admin의 multiple 후보는 Step-up consume0, policy write0이다.
- 변경 전/후: `첫 후보 허용` → `최대 2개 조회 후 정확히 1개만 허용`. 정상 personal owner·organization admin과 workspace_admin/viewer denial은 유지한다.
- 첫 RED fixture는 missing workspace를 `DELETE`하려다 FK 보호로 `PERSISTENCE_CONFLICT`가 발생했고, Runtime은 seed 부재로 auth 통과 뒤 503이어서 제품 결함과 fixture 오류가 섞였다. 반복하지 않고 own workspace kind 불일치로 후보0을 만들고 정상 Egress seed를 추가해 테스트 경계를 교정했다.
- 정식 RED: multiple 후보에서 direct authorization이 성공했고 Runtime은 Step-up을 1회 consume했다. 이는 임의 첫 Workspace 선택 결함을 직접 증명한다.
- GREEN: tenant_id+expected workspace_kind parameter query가 `LIMIT 2`로 후보를 bounded 조회하고 `candidate_count == 1`일 때만 허용한다. 0/2(capped)는 동일 `ACTION_DENIED`; Audit metadata는 `role`, `reason_code`, `candidate_count`만 포함하고 Workspace ID·secret0이다.
- 결과: direct missing/multiple/cross-tenant 및 Runtime personal owner/organization admin multiple `2/2 PASS`. 정상 personal owner 201, organization admin 기존 경로, workspace_admin/viewer consume0·write0를 포함한 Authorization+Egress 관련 전체 `37/37 PASS`; Python compile PASS; OpenAPI exact PASS.
- 외부 policy/provider write·수동 DB/role 변경·commit/push/deploy0. 보호 dirty 미접촉. diff-check/staged0 후 인계한다.
