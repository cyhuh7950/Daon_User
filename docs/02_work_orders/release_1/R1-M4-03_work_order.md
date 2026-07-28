# R1-M4-03 사용자·조직·Session 인증 Core 작업지시서

## 승인 기준과 Writer

- Branch `codex/r1-m4-03`, 기준 HEAD `1fba9576df85a6108dcf1c5d9a790afb3775d607`.
- 상세 설계 v0.7, 구현계획 v0.9 M4-03, 테스트계획 v0.7, TS-SEC-001~006, M4-01 OpenAPI, M4-02 Audit Core를 적용한다.
- 어울2가 이 Worktree의 유일한 Writer다.

## 단일 목표

OIDC Authorization Code+PKCE, Web Session·Native opaque access/refresh, Device 등록·신뢰·철회, StepUpAuthorization, 재시작 가능한 최소 IAM Repository와 모든 인증 성공·거부·만료·재사용 Audit 연결을 Python 3.14 표준 라이브러리 Core로 구현한다.

## 허용·제외 범위

- 허용: `identity*.py`와 Package Export, `test_identity*.py`, API README, Root Identity Verifier와 API Quality Capability, OpenAPI Identity Schema·Verifier/Test/Evidence, Identity Architecture, 본 작업·진행·보고 문서.
- Audit Core는 Import/Integration에 필요한 최소만 사용한다.
- 제외: 실제 HTTP/FastAPI/BFF Route, UI, PostgreSQL/Migration/RLS, Workspace 권한, Local Service, Workflow, Lockfile, 외부 의존성.

## OIDC 계약

- issuer/client/audience/redirect exact allowlist와 cryptographic state·nonce·PKCE S256를 사용한다.
- Transaction은 짧은 TTL·single-use이며 state/nonce/verifier 평문을 저장하지 않는다.
- Callback은 state·미사용·미만료·exact client/redirect, PKCE, Provider가 검증 완료한 signature/issuer/audience/subject/nonce/exp를 다시 fail-close 확인한다.
- Provider Protocol은 검증 완료 Claims만 반환하며 Fake Adapter 증거를 외부 Provider 성공으로 주장하지 않는다.

## Credential·Session 계약

- Web/Native Credential은 고엔트로피 opaque 값이며 Repository에는 SHA-256 digest만 저장한다.
- Web Cookie HttpOnly/Secure/SameSite/CSRF 적용은 M4-05 소유다.
- Native refresh는 회전·만료·철회하며 Replay 시 Family와 Session을 원자 철회한다.
- 위조·만료·철회는 안정 Code와 401 의미를 반환하며 Raw Token·Verifier·Provider 오류를 반사하지 않는다.

## IAM Repository·Device 계약

- SQLite Adapter는 주입 경로, parameterized SQL, explicit transaction, unique/foreign key, WAL, schema version을 사용한다.
- User·Tenant·Membership·Session·Refresh family/token digest·Device·OIDC transaction·StepUpAuthorization을 재시작 후 복구한다.
- Device는 opaque ID, actor/tenant/platform/trust/registered/last_seen/revoked/session binding을 가진다.
- Device revoke는 관련 Session·Refresh Family를 원자 철회하고 Sync Key 실제 폐기 대신 후속 M5/M6용 revoke event를 반환한다.

## Step-up 계약

- §14.4 최소 7개 Action Group은 제거 불가이며 조직은 추가만 가능하다.
- 현재 actor/session/device/tenant/action/target/policy_version을 확인하고 짧은 opaque one-time Authorization을 발급한다.
- 소비는 동일 binding·미사용·미만료·미철회를 원자 검사한다. 누락은 `STEP_UP_REQUIRED`, 만료·재사용·binding 불일치는 변경 전 거부한다.
- TTL은 정책 주입 가능하며 안전 기본 300초·상한 600초로 제한한다.

## Audit·원자성·안전 계약

- login started/succeeded/denied, refresh rotated/replay denied, session/device revoked, step-up issued/used/expired/reuse/binding denied를 Actor·Trace·Policy·안전 Projection으로 기록한다.
- 보안 Write와 Audit append를 같은 Repository Transaction 경계에서 처리해 Audit 실패 시 DB Write를 Rollback한다.
- 오류·Audit·DB에 Raw Token/Verifier/Secret/Provider 원문/DB 경로/Internal Host를 남기지 않는다.
- 입력은 whitelist·길이·UTC·opaque ID 검증, SQL은 정적 parameterized Query만 사용한다.

## OpenAPI 계약

- 기존 `/api/v1/session`, `/api/v1/session/step-up`과 필요한 최소 OIDC·Session·Device Request/Response를 generic Envelope에서 구체화한다.
- Web same-origin Cookie 전달과 Native HTTPS Bearer 전달 의미를 구분하되 실제 Runtime 성공은 주장하지 않는다.
- M4-01 공통·M4-02 Audit 계약을 보존한다.

## TDD·검증

- RED: OIDC PKCE/state/nonce, Restart, expiry/rotation/replay/revoke, Device revoke, Step-up 7종/binding/TTL/one-time/concurrency, Audit safety/lineage/failure rollback, parameterized persistence failure.
- GREEN: 표준 라이브러리만 사용하며 신규 Dependency/Lockfile 필요 시 중단하고 `INCOMPLETE`로 보고한다.
- Root `verify:api-identity` write/no-write, Identity 전체, Audit/OpenAPI no-write, Compile/Export, Workspace, Independence, Toolchain, Node/JSON, Quality Gate, 전체 Node를 검증한다.
- 실패는 exact base 격리 비교한다. Secret 검색은 값이 아닌 presence/count만 보고한다.

## 진행·보고

`docs/04_test_reports/release_1/R1-M4-03_progress.md`에 착수·각 RED/GREEN·오류/복구·검증·종료 직전 상태를 기록한다. 결과보고 후 단일 목적 Commit을 Push하고 exact Local/Remote SHA·Clean을 보고한다. PR·CI·Merge는 어울1 소유다.
