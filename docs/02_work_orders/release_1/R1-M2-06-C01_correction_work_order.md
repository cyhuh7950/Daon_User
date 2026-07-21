# R1-M2-06-C01 수정 작업지시서 — 권한 우회·Route 정본·Browser 증거

## 1. 수정 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M2-06` |
| issue_id | `R1-M2-06-I001` |
| Attempt 판정 | `INCOMPLETE 1/3`, 정식 `FAILURE_REPORT 0` |
| 수정 범위 | C2 세 건의 최소 보정과 회귀 증거 재수집 |
| 개발자 | 동일 어울2 · Project Custom Agent `daon-developer` |
| 기준 Branch | `codex/r1-m2-06` |
| 기준 HEAD | `12afc61b3f8411ef0adfc05c9ac66010f9f07bcd` 위 Attempt 1 미Commit Worktree |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-06_progress.md`에 C01 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-06_attempt-2.md` |

원 작업지시서·실행 프롬프트·승인 정본·Attempt 1 보고서·독립 검토 지적을 EOF까지 다시 읽는다. 원 범위와 금지사항은 그대로 유지한다.

## 2. 결함과 필수 보정

### C2-1 Step-up이 현재 권한을 대체하는 권한 우회

독립 재현 결과, `viewer`가 `change_organization_policy` 또는 `move_data_realm` Step-up을 발급·사용해 Domain 변경과 영역 이동 완료 Preview를 만들 수 있다. 현재 구현은 Step-up Scope만 검사하고 발급 전·소비 직전의 현재 Membership·Capability·Tenant/Workspace 권한을 검사하지 않는다.

필수 보정:

1. 민감 Action Registry를 단일 정본으로 만든다. 각 Action은 최소 민감 작업 7종 또는 조직이 명시 추가한 승인 목록에만 속하고, 필요한 세부 권한·MembershipRole·Target 종류·Tenant/Workspace Scope를 선언한다.
2. Registry에 없는 Action, 임의 문자열, 제거된 최소 Action은 `STEP_UP_ACTION_NOT_ALLOWED`로 발급 전에 거부한다.
3. `issue-step-up`은 발급 전에 현재 Actor·MembershipRole·세부 권한·Tenant·Workspace·Policy Version을 순수 Authorization 판정기로 검사한다. Step-up은 권한을 부여하지 않고 이미 있는 권한의 추가 인증만 증명한다.
4. `perform-sensitive-action`은 Step-up Scope 검증 전에 또는 함께 소비 직전 현재 권한을 다시 검사한다. 발급 뒤 권한 회수·Membership 변경·Scope 변경이 있으면 `CURRENT_ACCESS_DENIED`로 거부하고 Domain 변경·성공 Audit·외부 호출 0건을 유지한다.
5. `advance-realm-move`의 명시 승인과 전송 Preview 진입 전에도 `move_data_realm` 현재 권한·Tenant/Workspace·Step-up을 재검사한다. 한 단계라도 실패하면 다음 단계·Approval Preview·실제 Count를 바꾸지 않는다.
6. 정책 변경은 대상 조직의 활성 `organization_admin`만 허용한다. `workspace_admin`의 선택형 RuleSet Binding 같은 원 계약 예외를 조직 정책 변경 권한으로 확대하지 않는다.
7. `viewer`, Grant 없는 `organization_member`, 기본 `operator`, 개인 영역 밖 `personal_user`, 다른 Tenant/Workspace 관리자에 대해 Step-up 발급·사용·영역 이동 전체 경로를 부정 Test로 고정한다.
8. 정상 조직 관리자 경로, 만료·다른 Target·재사용·종료 상태 불변, Audit Preview는 계속 통과해야 한다.

부정 경로에서 안전 오류를 기록하기 위한 Append-only Denied Audit는 허용하지만, 성공 Event·Domain Mutation·Approval/Egress/Run/Session/Key Count는 0이어야 한다. 실제 Auth/API/MFA를 구현하거나 성공으로 표시하지 않는다.

### C2-2 Browser JSON과 명명 PNG의 직접 증거 불일치

- `realm-move-state-500x900.png`에는 영역 이동 5단계/완료/실제 Count가 보이지 않는다.
- `organization-policy-403-1200x900.png`에는 `AUTHORIZATION_DENIED`·HTTP 403 계약 Preview·정책 불변 결과가 직접 보이지 않는다.
- `access-partial-rerun-800x900.png`에는 여섯 작업 판정 요약과 새 Rerun Preview가 직접 보이지 않는다.

필수 보정:

1. 최종 보정 코드로 Production Build를 새로 만들고 새 Browser Session에서 재검증한다.
2. 1200×900은 무권한 정책 변경 결과의 안전 Code, `HTTP 403 계약 Preview · 실제 API 미실행`, 정책 요청값/유효값/Version 불변이 한 화면에 직접 보이게 촬영한다.
3. 800×900은 현재 권한 여섯 작업의 AccessDecision History/Count, `partially_redacted`의 Masking Reference, `access_blocked/CURRENT_ACCESS_DENIED`, 새 Rerun Preview ID·현재 Snapshot·실제 Run 0건이 직접 보이게 촬영한다. 한 화면에 불가능하면 상태별 추가 PNG를 만들고 Browser JSON과 Manifest에 각각 연결한다.
4. 500×900은 영역 이동 다섯 단계 완료, Scope가 일치한 Step-up, Approval Preview, 실제 전송·SourceVersion·재색인 0건이 직접 보이게 Scroll한 화면을 촬영한다. 상단 Matrix만 보이는 화면은 이 상태 증거로 사용하지 않는다.
5. 각 PNG의 실제 Pixel Dimension, 표시 문자열, 클릭 순서, Console warning/error, Resource Timing 가용 여부를 Browser JSON에 기록한다.
6. Browser JSON의 모든 주장은 연결된 PNG 또는 명시된 자동 Test 근거와 일치해야 한다. PNG 이름·상태·화면 내용 불일치 0건이어야 한다.

### C2-3 URL과 Route/Screen 정본 불일치

내부 Account↔Organization 전환은 URL과 내부 상태만 바꾸고 시작 Route의 `routeId/screenId` Props를 유지할 수 있다.

필수 보정:

1. 현재 Active Route 또는 URL에 따라 M2-01 정본의 `account_settings/account_settings`와 `organization_settings/organization_settings`를 동적으로 투영한다.
2. 내부 전환 직후 URL, `data-route-id`, `data-screen-id`, 제목·Client 지원 상태가 모두 현재 Route와 일치해야 한다.
3. Account→Organization→Account, Organization→Account→Organization, Browser Back/Forward 또는 지원하는 History 복원 경로를 Test한다. 지원하지 않는 Browser Navigation을 성공으로 주장하지 않는다.
4. 1920·1200·800·500 Browser는 모두 `client_type=web`이고 동일 기능을 반응형으로 유지한다. Width로 Android/iOS를 추론하지 않는다.
5. Route 전환 중 AccountSecurity Domain 상태는 보존하되 현재 Route Metadata만 정확히 바뀐다.

## 3. TDD·검증 순서

| 단계 | 작업 | 증거 |
| --- | --- | --- |
| C01-S0 | 원 계약·Attempt 1·검토 지적·보호 Dirty 재확인 | Progress |
| C01-S1 | 세 결함별 회귀 Test를 먼저 추가하고 현재 코드에서 유효 RED 확인 | 결함별 RED 수량·원인 |
| C01-S2 | Action Registry·발급 전/소비 전 권한 재검사·영역 이동 Guard 최소 보정 | 권한 우회 Green |
| C01-S3 | 동적 Route/Screen 정본·History 전환 최소 보정 | URL/DOM Green |
| C01-S4 | 전용·전체 회귀·Lint·Build·공통 Gate | 전부 PASS |
| C01-S5 | 새 Production Browser 클릭·PNG/JSON 재수집 | 명명 상태 직접 노출 |
| C01-S6 | Architecture·Manifest·Progress·Attempt 2·Diff 최종 대조 | HANDOFF_READY |

유효 RED는 기존 테스트 성공 뒤 새 회귀 테스트만 의도한 보안·Route·증거 계약으로 실패해야 한다. Loader·환경·선택자 오류는 RED가 아니다.

## 4. 완료 조건

- 권한 없는 Actor의 Step-up 발급·사용·정책 변경·영역 이동 성공 0건
- 발급 전과 소비 직전 현재 권한·Tenant·Workspace·Policy 재검사
- 최소 7종/조직 승인 추가 외 Action 발급 0건
- viewer·operator·Grant 누락·개인/타 Tenant 부정 경로 Test PASS
- URL·Route ID·Screen ID가 두 방향 전환과 History 경로에서 일치
- 최종 전용·전체 회귀·Lint·Production Build·공통 Gate PASS
- 1200 403, 800 AccessDecision+Rerun, 500 영역 이동 최종 상태가 PNG에 직접 보임
- Browser JSON·PNG·Manifest Hash/Dimension·상태 주장 일치
- Prototype 정직성·same-origin·Secret 비노출·기존 M2-01~05 회귀 유지
- 보호 Dirty 2개와 범위 밖 파일 무변경

## 5. 결과보고

첫 줄:

```text
COMPLETED | R1-M2-06-I001 | C01 권한 우회·Route 정본·Browser 증거 보정 | 변경 파일 | 테스트 근거 | 남은 위험 | 다음 조치
```

Commit·Push·ysna-server·PR·Merge는 수행하지 않는다. 완료 조건 하나라도 빠지면 `COMPLETED`를 쓰지 않는다.
