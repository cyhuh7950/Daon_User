# R1-M4-04 완료 보고서

## 판정

`COMPLETED` — Tenant·Workspace Authorization Core와 현재 권한 기반 과거 결과 재검증 계약을 승인 범위 안에서 구현했고, 관련 검증과 전체 품질 게이트가 통과했다.

## 판단 이유

- 7개 역할과 8개 독립 권한, 역할 Action Matrix, 조직/Workspace 정책 상속과 조직 Deny·Lock 우선을 구현했다.
- 호출자 주장 역할이 아닌 현재 Repository Membership을 사용하며 Tenant 밖과 Missing Resource는 같은 안전 결과로 처리한다.
- Membership·ACL·정책 Version과 `expected_version` 동시성 제어, 안전 Before/After 감사, Audit 실패 Transaction Rollback을 구현했다.
- 중요한 조직 정책 Write는 M4-03의 실제 `organization_security_or_connector_policy_change` Step-up Authorization을 소비한다.
- 과거 결과 Descriptor는 불변 보존하고 read·citation·open source·export·delivery·knowledge registration·rerun마다 현재 Membership·ACL·정책·Source 접근을 재평가해 새 불변 `AccessDecision`을 만든다.
- 비인가 근거는 안전 분리 가능성에 따라 `partially_redacted` 또는 `access_blocked`로 판정하며 원본 결과·근거를 수정하지 않는다.
- 재실행은 과거 Snapshot이 아니라 현재 ACL·정책·데이터 영역·비용 한도·허용 Source Version의 새 Request Snapshot만 반환한다.

## 변경 범위

- Authorization Core·공개 Export·Test 15개
- Authorization 결정적 Verifier·Evidence와 기존 API Quality Capability 연결
- 권한 평가·AccessDecision OpenAPI Schema/Path와 Contract Verifier·Evidence
- Authorization Architecture·API README
- Windows 새 Checkout에서 Identity Evidence의 CRLF만 정규화하는 검증기 최소 수정
- Work Order·Prompt·Progress·본 완료 보고서

App/UI, HTTP Runtime/BFF, Local Service, PostgreSQL Migration·RLS, 실제 Workflow/Run, 외부 의존성·Lockfile, Audit Core는 변경하지 않았다.

## 검증 결과

| 검증 | 결과 |
| --- | --- |
| Authorization TDD·Verifier write/no-write | 15/15 PASS, 7 roles·8 permissions, source `CFC1CCB9...E797F` |
| Identity 회귀·Evidence no-write | 18/18 PASS, source `C588F9DE...7CCE6` |
| Audit 회귀 | 13/13 PASS |
| OpenAPI | no-write PASS, Node 8/8, 44 paths·67 operations·50 schemas, SHA `E1730F21...E6B1B` |
| Python compile·Public export | PASS |
| Workspace | 34/34 PASS |
| Independence | 8 components·10 edges·147 scanned files·0 violations |
| Toolchain | 7 npm manifests, exact pins, lockfiles PASS |
| Quality Gate | lint 8·type 5·unit 9·contract 3·build 8·security 3·independence 1, 전체 PASS·실패 0 |
| JSON·Node syntax·Diff check | PASS |

전체 Node는 변경 Worktree와 정확한 Base HEAD `6f03712` 모두 341개 중 331개 통과·동일 iOS Permission CI Script 10개 실패였다. 따라서 M4-04 회귀는 0건이며 기준선 공통 제한으로 분리했다. 해당 iOS 범위는 이번 Work Order에서 수정하지 않았다.

## 미해결·후속 경계

- 실제 HTTP Route·Cookie/CSRF·운영 Browser Network는 M4-05 이후 소유다.
- PostgreSQL Migration·RLS·durable Audit outbox는 M5 소유다.
- 실제 Source/Output/Run Service 연결과 UI는 후속 Work Order 소유다.
- PR·CI·Merge는 어울1 소유다.
