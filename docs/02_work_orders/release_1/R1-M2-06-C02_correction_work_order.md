# R1-M2-06-C02 수정 작업지시서 — 호출자 역할 주입 권한 우회

## 1. 수정 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M2-06` |
| issue_id | `R1-M2-06-I001` |
| 누적 판정 | `INCOMPLETE 2/3`, 정식 `FAILURE_REPORT 0` |
| 수정 범위 | 정책 변경 Preview의 호출자 역할 주입 C2 한 건 최소 보정 |
| 개발자 | 동일 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch/HEAD | `codex/r1-m2-06` · `d5e0a09` 위 현재 미Commit Worktree |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-06_progress.md`에 C02 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-06_attempt-3.md` |

원 작업지시서, C01 수정 작업지시서, Attempt 2 보고서와 C01 독립 검토 결과를 EOF까지 다시 읽는다. 원 범위·금지사항·보호 Dirty 계약은 그대로 유지한다. C01에서 수락된 Step-up, Route/Screen 정본과 Browser 증거를 재작성하지 않는다.

## 2. 판정과 원인

### 판정

`REWORK` · C2 보안 결함 1건.

### 판단 이유

활성 `viewer` Membership에서 `preview-policy-change` Action에 호출자 입력 `role: "organization_admin"`을 주입하면 현재 Membership 대신 위조 Role과 그 Role의 Grant를 신뢰하여 조직 정책 Preview가 생성된다. 현재 테스트는 `role: "viewer"`만 전달해 이 위조를 놓쳤다.

### 조치

1. 호출자가 전달한 `action.role`, Persona, Capability 또는 Grant 배열을 권한 정본으로 사용하지 않는다.
2. 정책 변경 Preview는 현재 상태의 활성 Membership, 대상 Tenant/Organization/Workspace, 현재 Policy Version과 서버·Adapter가 제공할 권한 정본만으로 판정한다.
3. 현재 Membership과 요청 Role이 다르면 요청 Role을 무시해 현재 Membership으로 판정하거나 안전 Code로 거부한다. 어떤 경우에도 요청 Role로 권한이 상승하면 안 된다.
4. 정책 변경은 대상 조직의 활성 `organization_admin` Membership과 필요한 정책 변경 권한을 모두 만족할 때만 Preview를 만든다.
5. 부정 경로는 `AUTHORIZATION_DENIED` 또는 `CURRENT_ACCESS_DENIED`로 끝나고 `policyPreview`, 요청값·유효값 노출, 성공 Audit, Domain Mutation, 외부 호출이 모두 0건이어야 한다. Denied Audit Preview만 허용한다.
6. 동일 정본 판정 규칙을 다른 민감 Action에 적용하되 무관 구조 변경이나 전면 리팩터링은 금지한다.

## 3. TDD·검증 순서

| 단계 | 작업 | 증거 |
| --- | --- | --- |
| C02-S0 | 정본·현재 Diff·단일 Writer·보호 Dirty·누적 판정 재확인 | Progress |
| C02-S1 | 활성 Viewer + `role=organization_admin` 주입 회귀 Test를 먼저 추가하고 현재 코드에서 유효 RED 확인 | 안전 오류 없음·Preview 생성 재현 |
| C02-S2 | 현재 Membership 정본 판정 최소 보정 | 신규 Test Green |
| C02-S3 | Viewer·operator·Grant 누락·개인/타 Tenant·정상 조직 관리자와 기존 Step-up·Route 회귀 | 전용·선택 회귀 PASS |
| C02-S4 | Lint·Production Build·공통 Gate 실행 | 범주별 결과 |
| C02-S5 | Manifest·Progress·Attempt 3·Diff 최종 대조 | 상태 계약 보고 |

유효 RED는 기존 전용 20개가 통과한 뒤 신규 역할 주입 테스트만 의도한 보안 계약으로 실패해야 한다. Loader·환경·선택자 오류는 RED가 아니다.

`npm audit`에서 확인된 `next@16.2.10`의 Sharp/PostCSS 기준선 취약점은 C02 기능 결함과 분리한다. Dependency·Lockfile·Toolchain·CI는 수정하지 않는다. 공통 Gate를 끝까지 실행해 같은 감사 실패만 남는다면 기능 보정 결과와 의존성 `BLOCKED`를 분리해 정직하게 보고한다.

## 4. 완료 조건

- Viewer Membership + 위조 `organization_admin` Role로 정책 Preview 생성 0건
- 호출자 제공 Role·Persona·Grant로 권한 상승 0건
- 부정 경로의 요청값·유효값 노출, 성공 Audit, Domain Mutation, 외부 호출 0건
- 정상 대상 조직 관리자 Preview와 기존 C01 Step-up·Route·Browser 계약 회귀 0건
- 전용·전체 선택 회귀·Lint·Production Build PASS
- 공통 Gate 범주별 결과와 Dependency Audit 차단을 분리 기록
- Manifest·Progress·Attempt 3·Diff 정합, 보호 Dirty 2개와 범위 밖 파일 무변경

## 5. 결과보고

첫 줄은 다음 계약을 사용한다.

```text
status | R1-M2-06-I001 | C02 호출자 역할 주입 권한 우회 보정 | 변경 파일 | 테스트 결과 | 미해결 사항 | 다음 판단
```

기능 완료 조건을 충족하고 의존성 감사만 남으면 `BLOCKED`로 보고하되, C02 기능 보정 완료와 기준선 Dependency 차단을 명확히 분리한다. Commit·Push·ysna-server·PR·Merge는 수행하지 않는다.
