# R1-M2-07-C01 수정 작업지시서 — 재처리·복구 권한·증거 경계 보정

## 1. 판정과 작업 계약

| 항목 | 내용 |
| --- | --- |
| 원 Work Order | `R1-M2-07` |
| 동일 issue_id | `R1-M2-07-I001` |
| 현재 분류 | `INCOMPLETE 1/3`, 정식 `FAILURE_REPORT` 0회 |
| 수정 작업 | 불변 Run·Mode/상태 Fail-close·Recovery 권한/Step-up/G9·직전 Run 계보·입력 안전·화면 증거 보정 |
| 개발자 | 동일 어울2 · Project Custom Agent `daon-developer` |
| Branch/Worktree | `codex/r1-m2-07` · `C:\tmp\Daon_User-r1-m2-07` |
| 기준 HEAD | `ab2a3b055581fcaea75cceafc3bb8bedb2a80066` + Attempt 1 미Commit 변경 |
| 진행 기록 | 기존 `docs/04_test_reports/release_1/R1-M2-07_progress.md`에 `C01-*` 단계 추가 |
| 결과보고 | `docs/02_work_orders/reports/R1-M2-07_correction-1.md` |

착수 전 아래 파일을 EOF까지 읽고 Hash를 대조한다. 원 작업지시서의 나머지 계약은 그대로 유지한다.

| 정본 | SHA-256 |
| --- | --- |
| `docs/02_work_orders/release_1/R1-M2-07_work_order.md` | `5D080EADAC96994A7F786B70738F35ABE02D494326035CB71A0FA7A1FB439B4A` |
| `docs/02_work_orders/reports/R1-M2-07_attempt-1.md` | `939BCBE298CF20FD8591F06C8834EC3926BE64F3BF5FDDE3E5336922CC1AFDF7` |
| `docs/03_evidence/release_1/R1-M2-07/evidence-manifest.json` | `0DF2514E68D5BEB5A602BAC4F72E2EE1D93A26C70E836BD6C4E69793792BF5B5` |
| `docs/01_architecture/operations_recovery_prototype_adapter_contract.md` | `AB53C7E7DF6E8E3AF39D4C4FD0F3AD04180703A00DFC9BCC55D853EF8D4DBF5E` |

## 2. 독립 검토 판정

Attempt 1의 자동 153/153·Build·Gate·Manifest는 유효하지만 아래 C2/C3를 가리지 못하므로 `COMPLETED`를 수락하지 않는다.

### C2-1 불변 이전 Run 수정

`complete-retry-preview`에 최초 `immutable:true` 실패 Run ID를 주면 그 Run이 `completed/READY_GATE_PASSED`로 바뀌고 Source도 `ready`가 된다. 이전 ProcessingRun은 어떤 Action으로도 수정할 수 없어야 한다.

### C2-2 자동 재처리 Fail-open과 Event 중복

- `selectionMode=attacker_mode` 등 Allowlist 밖 값도 healthy Event에서 새 Run을 Queue한다.
- Source가 이미 `ready`여도 새 Readiness Event가 다시 Run을 Queue한다.
- 동일 Event ID는 새 Run은 막지만 `readinessEvents`에 중복 항목을 추가한다.

자동 허용은 `waiting_model` 상태의 `auto` 또는 정책이 명시 허용한 `local_only`뿐이다. Mode·상태·Event 중복은 기본 거부하며 Event/Run/Queue 수를 변경하지 않는다.

### C2-3 Recovery 권한·Step-up·G9 위조

현재 구현은 Action Payload의 `stepUpStatus=valid`와 임의 `approvalId`를 신뢰한다. Viewer도 `forged-g9`로 성공 Preview/Audit를 만들 수 있다. 현재 Membership·Tenant·Workspace·Capability, StepUpAuthorization Actor/Action/Target/PolicyVersion/상태/만료/사용 여부와 G9 승인 정본을 검증해야 한다.

### C2-4 직전 실패 Run 계보

두 번째 재처리가 첫 재처리 실패 Run이 아니라 항상 `processingRuns[0]`을 `retry_of_processing_run_id`로 사용한다. 동일 SourceVersion·필수 역할의 가장 최근 재시도 가능한 실패 Run을 직전 부모로 선택한다.

### C3-1 잘못된 Outcome 중단

Allowlist 밖 `outcome`이 `TypeError`로 Reducer를 중단한다. 안전 Code로 거부하고 Source·Run·Queue를 변경하지 않는다.

### C3-2 Browser JSON–PNG 연결 부족

기존 PNG 4개는 상단 Dashboard·Queue 중심이라 JSON이 주장하는 계보·장애 6종·Step-up/G9·알림/Deep Link를 직접 확인할 수 없다. 상태별 직접 화면 증거를 다시 수집한다.

## 3. 수정 계약

### 3.1 ProcessingRun 불변성과 계보

- `complete-retry-preview`는 `actualRunCreated=false`인 현재 Prototype 재처리 Run이며 허용 진행 상태인 대상만 처리한다.
- `immutable:true`, 최초/과거 Run, 이미 종료된 Run, 다른 SourceVersion·역할 Run은 각각 안정 Code로 거부하고 원본 객체·Source 상태·Queue·Audit 성공을 바꾸지 않는다.
- 재처리 부모는 동일 SourceVersion·필수 역할에서 가장 최근의 재시도 가능한 종료 실패 Run이다. 첫 재시도 실패 뒤 두 번째 재시도는 첫 재시도 Run을 직접 가리켜야 한다.
- 부모 Run의 전체 객체는 새 Run 생성·완료 전후 깊은 불변이어야 한다.

### 3.2 Mode·Source 상태·Event·Outcome Fail-close

- 허용 Mode Enum은 `auto | local_only | pinned | direct`로 고정한다. `set-selection-mode`와 Readiness 경로 모두 Allowlist 밖 값을 `INVALID_SELECTION_MODE`로 거부한다.
- 자동 Readiness Event는 Source가 정확히 `waiting_model`일 때만 평가한다. `ready`, `needs_review`, `disabled`, 그 밖의 상태는 `SOURCE_NOT_WAITING_MODEL`로 새 Run 0건이다.
- 자동 실행은 `auto`와 정책 허용 `local_only`만, `pinned/direct`은 `MANUAL_RETRY_REQUIRED`만 반환한다.
- 동일 Event ID는 `readinessEvents`, `processedEventIds`, Run, Queue에 중복 항목을 추가하지 않는다. 억제 Audit은 허용하되 업무 상태 변화 0건을 보장한다.
- Outcome Allowlist는 `ready | policy_blocked | runtime_exhausted`다. 그 밖의 값은 `INVALID_RETRY_OUTCOME`으로 거부하며 예외를 던지지 않는다.

### 3.3 Recovery AuthorizationContext

- Action Payload의 `role`, `grants`, `stepUpStatus`, 임의 승인 문자열을 권한 정본으로 사용하지 않는다.
- ViewState의 현재 활성 Membership 정본에서 Tenant·Workspace·Capability를 판정한다. Restore/Rollback/Update Preview Capability를 명시하고 Viewer, 권한 회수, 다른 Tenant/Workspace를 거부한다.
- M2-06 계약과 같은 구조의 `StepUpAuthorization` Fixture 정본을 ViewState에 둔다. Action은 ID만 전달하며 Actor·Action·Target·PolicyVersion, `issued`, 미만료, 미사용을 모두 검사한다.
- G9 Fixture 정본은 `approval_id`, `kind=drill|deploy`, Target/Scope, PolicyVersion, `approved`, 승인자·승인 시각을 가진다. Restore/Rollback은 DRILL, Update는 DEPLOY 정본과 정확히 일치해야 한다.
- 유효 Step-up과 G9가 있어도 이번 M2의 결과는 `RECOVERY_PREVIEW_ONLY`, 실제 효과 0건이다. 성공 Preview 시 사용된 Step-up은 1회 사용 처리하고 Append-only Audit에 정본 ID와 판정을 남긴다.
- 위조·Scope 불일치·만료·사용됨·권한 회수는 안전 Code와 Preview/성공 Audit/외부 효과 0건으로 거부한다.

### 3.4 화면 증거

기존 네 폭 반응형 증거를 유지하되 다음 상태가 실제 보이는 PNG를 추가 또는 교체한다.

- 재처리 상세: 직전 `retry_of_processing_run_id`, `trigger_type`, Snapshot, 중복 Event 억제 Code
- 장애·보안 상세: 6종 축소 Code, `STEP_UP_REQUIRED`, `G9_DRILL_APPROVAL_REQUIRED`, `G9_DEPLOY_APPROVAL_REQUIRED`
- 알림 상세: 읽음 상태, Count 2 중복 집계, Incident Deep Link와 `recovered`
- 500px compact 상태: 직접 문자열과 문서 Overflow 0

`browser-validation.json`은 각 주장마다 대응 Screenshot 파일과 직접 표시 문자열을 연결한다. PNG가 증명하지 않는 항목은 PNG 증거라고 주장하지 않고 Browser DOM/Console/Resource Timing 증거로 분리한다.

## 4. TDD·검증 단계

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| C01-S0 | 정본 Hash·현재 Diff·단일 Writer 확인 | Progress |
| C01-S1 | 위 C2/C3 공격 재현 Test 선작성 | 신규 Test별 유효 RED |
| C01-S2 | Run 불변·직전 계보·Mode/상태/Event/Outcome Fail-close 최소 보정 | 전용 Test Green |
| C01-S3 | Membership·Step-up·G9 정본 재검증 최소 보정 | 위조/회수/Scope 부정 Test Green |
| C01-S4 | 전용·전체 회귀·Lint·Build·공통 Gate | 전부 PASS |
| C01-S5 | 실제 Browser 상태별 PNG·JSON 재수집 | 직접 증거 연결 PASS |
| C01-S6 | Adapter 계약·Manifest·보고·Diff 최종 정합 | `COMPLETED` 또는 정식 보고 |

필수 신규 회귀 Test:

1. immutable 최초 Run 완료 시도 → 변경 0건
2. 두 번 연속 Runtime 실패 재처리 → 두 번째 부모가 직전 실패 Run
3. unknown Mode·Source ready/needs_review → 자동 Run 0건
4. 동일 Readiness Event → Event/Run/Queue 중복 0건
5. unknown Outcome → 예외 0, 안전 Code, 변경 0건
6. Viewer/권한 회수/다른 영역/Payload Role·Grant 주입 → Recovery Preview 0건
7. 위조·만료·사용됨·Scope 불일치 Step-up/G9 → Preview/성공 Audit/외부 효과 0건
8. 유효 현재 Membership+Step-up+정확 G9 → Preview only, 실제 효과 0건, Step-up 1회 사용

## 5. 변경·금지 범위와 결과보고

수정은 Attempt 1의 Operations/Recovery Model·Pane·전용 Test·Adapter 계약·R1-M2-07 Evidence·Progress·Correction 보고로 제한한다. Route/CSS는 상태별 증거에 필요한 최소 변경만 허용한다.

Dependency·Lockfile·Toolchain·CI, 기존 M2-01~06 구현, 실제 API/Queue/DB/Backup/Restore/Update/배포는 변경하거나 실행하지 않는다. 기존 Green을 삭제·완화·Skip하지 않는다. Browser same-origin·Secret 금지와 R1-D022 운영 Release 금지를 유지한다.

각 단계와 오류·복구·테스트를 기존 Progress에 즉시 기록한다. 결과보고는 `판정 → 판단 이유 → 조치` 순서와 `COMPLETED | FAILURE_REPORT | INCOMPLETE` 중 하나를 사용한다. Commit·Push·PR·Merge·외부 배포는 수행하지 않는다.
